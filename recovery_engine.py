"""
recovery_engine.py — Failure Intelligence & Auto-Recovery Layer.

Provides:
  classify_error(error_msg, event)        — map log entry to a canonical failure type
  is_recoverable(error_type, error_msg)   — whether this failure can be auto-retried
  suggest_journal(attempted_name)         — closest Odoo cash/bank journal names
  suggest_invoice(attempted_number)       — closest Odoo invoice numbers
  get_correction_suggestions(type, msg)   — dispatch by error type, return suggestions dict

Consumed by:
  orchestrator.py       — runtime retry decisions
  execution_metrics.py  — log classification and breakdown reporting

No extra dependencies: stdlib only (difflib, json, os, re, urllib).
Credentials are read from environment variables — never hardcoded.
"""

import difflib
import json
import os
import re
import urllib.request
from pathlib import Path


# ── Failure taxonomy ───────────────────────────────────────────────────────────

FAILURE_TYPES = frozenset({
    "guard_rejection",       # Action blocked by DRY_RUN / HITL / threshold guard
    "tool_validation_error", # Invalid args: bad journal, partner, or invoice not found
    "network_error",         # Connection refused, DNS failure, SSL error
    "timeout",               # Claude timeout, MCP call timeout, max_iter guard
    "unknown",               # Catch-all when no pattern matches
})

# These types can be auto-retried without human input
RECOVERABLE_TYPES = frozenset({"network_error", "timeout", "tool_validation_error"})


# ── Pattern matchers ───────────────────────────────────────────────────────────

_GUARD_RE = re.compile(
    r"approval.required|blocked|dry.run|not.allowed|threshold.exceeded|\bguard\b|\bhitl\b",
    re.I,
)
_NETWORK_RE = re.compile(
    r"connection.refused|could not connect|econnrefused|\bnetwork\b|unreachable|\bsocket\b|\bssl\b",
    re.I,
)
_TIMEOUT_RE = re.compile(
    r"\btimeout\b|timed.out|max.iter|max_iter",
    re.I,
)
_VALIDATION_RE = re.compile(
    r"invalid.journal|no journal|journal.not.found"
    r"|invoice.not.found|no invoice|no partner"
    r"|\bnot.found\b|\binvalid\b|validation.error",
    re.I,
)
_JOURNAL_RE = re.compile(r"\bjournal\b", re.I)
_INVOICE_RE = re.compile(r"invoice.not.found|no invoice|inv.*not.found", re.I)


# ── Classifier ────────────────────────────────────────────────────────────────

def classify_error(error_msg: str, event: str = "") -> str:
    """
    Map a failure log entry to one of the canonical FAILURE_TYPES.
    Checks orchestrator event name first, then falls back to message patterns.
    """
    event_lower = (event or "").lower()

    # Event-based classification (highest priority)
    if event_lower in ("claude_timeout", "max_iter_reached"):
        return "timeout"
    if event_lower == "release_failed":
        return "network_error"
    if event_lower in ("watcher_died", "poll_error", "process_error"):
        return "network_error"

    msg = (error_msg or "").lower()

    if _GUARD_RE.search(msg):
        return "guard_rejection"
    if _NETWORK_RE.search(msg):
        return "network_error"
    if _TIMEOUT_RE.search(msg):
        return "timeout"
    if _VALIDATION_RE.search(msg):
        return "tool_validation_error"

    return "unknown"


# ── Recoverability gate ────────────────────────────────────────────────────────

def is_recoverable(error_type: str, error_msg: str = "") -> bool:
    """
    Return True if this failure can be auto-retried without human input.
    guard_rejection and unknown always require human review.
    """
    return error_type in RECOVERABLE_TYPES


# ── Odoo JSON-RPC helpers ──────────────────────────────────────────────────────

def _odoo_creds():
    """Read Odoo credentials from environment — never returns raw values to callers."""
    return (
        os.getenv("ODOO_URL", "").rstrip("/"),
        os.getenv("ODOO_DB", ""),
        os.getenv("ODOO_USERNAME", ""),
        os.getenv("ODOO_PASSWORD", ""),
    )


def _jsonrpc(base_url: str, service: str, method: str, args: list):
    """Low-level Odoo JSON-RPC call. Returns result or None on any error."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method":  "call",
        "id":      1,
        "params":  {"service": service, "method": method, "args": args},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/jsonrpc",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            return data.get("result")
    except Exception:
        return None


def _odoo_uid(base_url: str, db: str, username: str, password: str):
    return _jsonrpc(base_url, "common", "authenticate", [db, username, password, {}])


def _odoo_search_read(base_url, db, uid, password, model, domain, fields, limit=50):
    result = _jsonrpc(base_url, "object", "execute_kw", [
        db, uid, password, model, "search_read",
        [domain], {"fields": fields, "limit": limit},
    ])
    return result if isinstance(result, list) else []


# ── Smart correction suggestions ───────────────────────────────────────────────

def suggest_journal(attempted_name: str) -> list:
    """
    Query Odoo for cash/bank journal names and return the closest matches
    to attempted_name using string similarity (difflib).
    Returns empty list if Odoo is unreachable or credentials are absent.
    """
    base_url, db, username, password = _odoo_creds()
    if not all([base_url, db, username, password]):
        return []
    uid = _odoo_uid(base_url, db, username, password)
    if not uid:
        return []
    journals = _odoo_search_read(
        base_url, db, uid, password,
        "account.journal",
        [["type", "in", ["cash", "bank"]]],
        ["name", "type"],
    )
    names = [j["name"] for j in journals if isinstance(j, dict) and j.get("name")]
    if not attempted_name:
        return names[:3]
    return difflib.get_close_matches(attempted_name, names, n=3, cutoff=0.3) or names[:3]


def suggest_invoice(attempted_number: str) -> list:
    """
    Query Odoo for recent posted/draft invoices and return closest matches
    to attempted_number using string similarity (difflib).
    Returns empty list if Odoo is unreachable or credentials are absent.
    """
    base_url, db, username, password = _odoo_creds()
    if not all([base_url, db, username, password]):
        return []
    uid = _odoo_uid(base_url, db, username, password)
    if not uid:
        return []
    invoices = _odoo_search_read(
        base_url, db, uid, password,
        "account.move",
        [["move_type", "=", "out_invoice"], ["state", "in", ["draft", "posted"]]],
        ["name", "state", "amount_residual"],
        limit=50,
    )
    names = [
        inv["name"] for inv in invoices
        if isinstance(inv, dict) and inv.get("name") and inv["name"] != "/"
    ]
    if not attempted_number:
        return names[:3]
    return difflib.get_close_matches(attempted_number, names, n=3, cutoff=0.2) or names[:3]


def get_correction_suggestions(error_type: str, error_msg: str) -> dict:
    """
    Return a structured suggestions dict for a given failure.
    Only queries Odoo when the error message contains journal/invoice patterns.

    Returns:
      {
        "error_type":   str,
        "suggestions":  list[str],   # closest matches
        "action":       str,         # human-readable correction hint
      }
    """
    result = {"error_type": error_type, "suggestions": [], "action": ""}

    if error_type != "tool_validation_error":
        result["action"] = "Retry automatically — no correction needed."
        return result

    msg = error_msg or ""

    if _JOURNAL_RE.search(msg):
        # Extract attempted journal name from quoted string in error message
        m = re.search(r"['\"]([^'\"]+)['\"]", msg)
        attempted = m.group(1) if m else ""
        closest = suggest_journal(attempted)
        if closest:
            result["suggestions"] = closest
            result["action"] = (
                f"Invalid journal '{attempted}'. "
                f"Closest valid journals: {closest}. "
                "Update the task to use one of these journal names."
            )
        else:
            result["action"] = (
                "Invalid journal name. Could not reach Odoo to suggest alternatives. "
                "Check ODOO_URL and journal name manually."
            )

    elif _INVOICE_RE.search(msg):
        m = re.search(r"['\"]([^'\"]+)['\"]", msg)
        attempted = m.group(1) if m else ""
        closest = suggest_invoice(attempted)
        if closest:
            result["suggestions"] = closest
            result["action"] = (
                f"Invoice '{attempted}' not found. "
                f"Closest matches: {closest}. "
                "Verify the invoice number in Odoo and retry."
            )
        else:
            result["action"] = (
                "Invoice not found. Could not reach Odoo to suggest alternatives. "
                "Check the invoice number in Odoo manually."
            )

    else:
        result["action"] = "Validation error — review task arguments and retry."

    return result
