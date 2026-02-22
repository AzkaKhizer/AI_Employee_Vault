"""
finance/revenue_protection.py — Revenue Protection Engine (RPE)

Analyzes unpaid invoices, categorizes by aging buckets, scores risk,
generates reminder drafts, and writes approval-required files for human review.

Design constraints:
  - No auto-email sending
  - No auto-payment registration
  - All outbound actions require human approval (HITL)
  - Fully DRY_RUN aware (log-only when DRY_RUN=True)
  - Respects circuit breaker state before any MCP calls

Invoice schema expected from Odoo MCP list_unpaid_invoices:
  {
    "invoice_number": str,
    "partner_name":   str,
    "amount_residual": float,  # outstanding balance
    "invoice_date":    str,    # YYYY-MM-DD
    "invoice_date_due": str,   # YYYY-MM-DD  (may be absent — falls back to invoice_date)
  }
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, date as _date_type
from pathlib import Path
from typing import Any

VAULT_ROOT        = Path(os.getenv("VAULT_ROOT", Path(__file__).parent.parent))
PENDING_APPROVAL  = VAULT_ROOT / "Pending_Approval"
LOG_DIR           = VAULT_ROOT / "Logs"
DRY_RUN           = os.getenv("DRY_RUN", "false").lower() == "true"

# Reminder cooldown — do not re-create a reminder file within this many days
REMINDER_COOLDOWN_DAYS = int(os.getenv("RPE_REMINDER_COOLDOWN_DAYS", "7"))

# ── Aging bucket thresholds ───────────────────────────────────────────────────

_BUCKET_ORDER = ("current", "warning", "high", "critical")


def categorize_aging_bucket(days_overdue: int) -> str:
    """
    Map days_overdue to an aging bucket label.

    0–30   → "current"
    31–60  → "warning"
    61–90  → "high"
    91+    → "critical"
    """
    if days_overdue <= 30:
        return "current"
    if days_overdue <= 60:
        return "warning"
    if days_overdue <= 90:
        return "high"
    return "critical"


def build_aging_summary(invoices: list) -> dict:
    """
    Aggregate invoices into aging bucket summary.

    Returns:
        {
          "current":  {"count": int, "amount": float},
          "warning":  {"count": int, "amount": float},
          "high":     {"count": int, "amount": float},
          "critical": {"count": int, "amount": float},
        }
    """
    summary: dict[str, dict] = {b: {"count": 0, "amount": 0.0} for b in _BUCKET_ORDER}
    for inv in invoices:
        days = _days_overdue(inv)
        bucket = categorize_aging_bucket(days)
        summary[bucket]["count"] += 1
        summary[bucket]["amount"] = round(
            summary[bucket]["amount"] + _amount(inv), 2
        )
    return summary


# ── Revenue at risk ───────────────────────────────────────────────────────────

def calculate_revenue_at_risk(invoices: list) -> float:
    """
    Sum of outstanding amounts for invoices with days_overdue >= 31.
    These are beyond the 'current' bucket — actual revenue at risk.
    """
    return round(
        sum(_amount(inv) for inv in invoices if _days_overdue(inv) >= 31), 2
    )


# ── Risk scoring ──────────────────────────────────────────────────────────────

def calculate_risk_score(invoice: dict) -> dict:
    """
    Compute a composite risk score from aging + amount.

    Aging contribution:
      1–30 days  → +1
      31–60      → +2
      61–90      → +3
      91+        → +4

    Amount contribution:
      > 1,000    → +1
      > 5,000    → +2
      > 10,000   → +3

    Score mapping:
      0–2 → "low"
      3–4 → "medium"
      5–6 → "high"
      7+  → "critical"

    Returns:
        {"score": int, "risk_level": str}
    """
    days   = _days_overdue(invoice)
    amount = _amount(invoice)

    # Aging points
    if days >= 91:
        aging_pts = 4
    elif days >= 61:
        aging_pts = 3
    elif days >= 31:
        aging_pts = 2
    elif days >= 1:
        aging_pts = 1
    else:
        aging_pts = 0

    # Amount points
    if amount > 10_000:
        amount_pts = 3
    elif amount > 5_000:
        amount_pts = 2
    elif amount > 1_000:
        amount_pts = 1
    else:
        amount_pts = 0

    score = aging_pts + amount_pts
    risk_level = _score_to_level(score)
    return {"score": score, "risk_level": risk_level}


def _score_to_level(score: int) -> str:
    if score <= 2:
        return "low"
    if score <= 4:
        return "medium"
    if score <= 6:
        return "high"
    return "critical"


# ── Reminder draft generator ──────────────────────────────────────────────────

_TONE_OPENERS = {
    "low":      "friendly",
    "medium":   "firm",
    "high":     "urgent",
    "critical": "escalation",
}


def generate_reminder_draft(invoice: dict, risk_level: str) -> str:
    """
    Generate a markdown reminder draft appropriate to the risk_level.

    Tones:
      low      → friendly reminder
      medium   → firm reminder
      high     → urgent notice
      critical → escalation warning

    Returns a markdown string (not sent — for human review only).
    """
    inv_num  = invoice.get("invoice_number", "UNKNOWN")
    partner  = invoice.get("partner_name", "Valued Client")
    amount   = _amount(invoice)
    days     = _days_overdue(invoice)
    due_date = invoice.get("invoice_date_due", invoice.get("invoice_date", "N/A"))

    tone = _TONE_OPENERS.get(risk_level, "friendly")

    if risk_level == "low":
        subject = f"Friendly Reminder — Invoice {inv_num}"
        body = (
            f"Dear {partner},\n\n"
            f"We hope this message finds you well. We wanted to send a friendly "
            f"reminder that invoice **{inv_num}** for **PKR {amount:,.2f}** "
            f"was due on {due_date}.\n\n"
            f"If payment has already been sent, please disregard this notice. "
            f"Otherwise, we would appreciate settlement at your earliest convenience.\n\n"
            f"Thank you for your continued partnership."
        )
    elif risk_level == "medium":
        subject = f"Payment Reminder — Invoice {inv_num} ({days} days overdue)"
        body = (
            f"Dear {partner},\n\n"
            f"This is a reminder that invoice **{inv_num}** for **PKR {amount:,.2f}** "
            f"remains unpaid, now **{days} days past the due date** of {due_date}.\n\n"
            f"Please arrange payment promptly to avoid any service disruptions. "
            f"If you have any questions regarding this invoice, contact us immediately.\n\n"
            f"We appreciate your prompt attention to this matter."
        )
    elif risk_level == "high":
        subject = f"URGENT: Invoice {inv_num} Overdue — Immediate Action Required"
        body = (
            f"Dear {partner},\n\n"
            f"**URGENT NOTICE:** Invoice **{inv_num}** for **PKR {amount:,.2f}** "
            f"is **{days} days overdue** (due date: {due_date}).\n\n"
            f"Failure to settle this balance within 7 days may result in service "
            f"suspension and referral to our collections process.\n\n"
            f"Please contact us immediately to arrange payment or discuss a payment plan."
        )
    else:  # critical
        subject = f"ESCALATION WARNING — Invoice {inv_num} ({days} days overdue)"
        body = (
            f"Dear {partner},\n\n"
            f"**ESCALATION NOTICE:** Despite previous communications, invoice "
            f"**{inv_num}** for **PKR {amount:,.2f}** remains unpaid and is now "
            f"**{days} days overdue** (original due date: {due_date}).\n\n"
            f"This matter has been escalated to senior management. "
            f"Immediate payment is required to avoid formal collections proceedings "
            f"and potential legal action.\n\n"
            f"Contact our accounts team within 48 hours."
        )

    return (
        f"**Subject:** {subject}\n\n"
        f"**Tone:** {tone} reminder\n\n"
        f"---\n\n"
        f"{body}\n\n"
        f"---\n\n"
        f"*Best regards,*  \n"
        f"*Accounts Receivable Team*"
    )


# ── Draft file creation (Step 5) ──────────────────────────────────────────────

def create_reminder_approval_file(
    invoice: dict,
    risk_score: dict,
    dry_run: bool = False,
) -> Path | None:
    """
    Write REMINDER_{invoice_number}.md to /Pending_Approval/ for human review.

    Guards:
      - Only for risk_level in ["high", "critical"]
      - Skips if a reminder file was written within REMINDER_COOLDOWN_DAYS
      - In dry_run mode: logs intent but creates no file

    Returns the path written, or None (dry_run / skipped / low-risk).
    """
    risk_level = risk_score["risk_level"]
    if risk_level not in ("high", "critical"):
        return None

    inv_num = invoice.get("invoice_number", "UNKNOWN")
    safe_num = re.sub(r"[^\w.-]", "_", inv_num)
    filename = f"REMINDER_{safe_num}.md"
    dest = PENDING_APPROVAL / filename

    # Cooldown guard — skip if file was recently created
    if dest.exists():
        age_days = (
            datetime.now(timezone.utc).timestamp() - dest.stat().st_mtime
        ) / 86400
        if age_days < REMINDER_COOLDOWN_DAYS:
            _rpe_log("reminder_cooldown", {
                "invoice": inv_num,
                "existing_file": filename,
                "age_days": round(age_days, 1),
                "cooldown_days": REMINDER_COOLDOWN_DAYS,
            })
            return None

    days    = _days_overdue(invoice)
    amount  = _amount(invoice)
    draft   = generate_reminder_draft(invoice, risk_level)

    content = f"""# Payment Reminder Approval Required

**Invoice:**      {inv_num}
**Customer:**     {invoice.get("partner_name", "Unknown")}
**Amount Due:**   PKR {amount:,.2f}
**Days Overdue:** {days}
**Risk Level:**   {risk_level.upper()}
**Risk Score:**   {risk_score["score"]}

---

## Draft Reminder Message

{draft}

---

## Action Required

Review the draft above. To send:
1. Confirm the message content and recipient email address.
2. Approve by moving this file to `/Approved/`.
3. The email-executor skill will dispatch it on next cycle.

> **DRY_RUN:** {'YES — this file was created in dry_run mode for preview only.' if dry_run else 'NO — live approval file.'}

*Generated by Revenue Protection Engine — {datetime.now(timezone.utc).isoformat()[:19]}Z*
"""

    if dry_run:
        _rpe_log("reminder_draft_dry_run", {
            "invoice":    inv_num,
            "risk_level": risk_level,
            "would_write": str(dest),
        })
        return None

    PENDING_APPROVAL.mkdir(exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    _rpe_log("reminder_draft_created", {
        "invoice":    inv_num,
        "risk_level": risk_level,
        "file":       filename,
        "amount":     amount,
        "days":       days,
    })
    return dest


# ── Full analysis pipeline ────────────────────────────────────────────────────

def run_revenue_protection(
    invoices: list,
    dry_run: bool = False,
) -> dict:
    """
    Run the full RPE pipeline over a list of unpaid invoice dicts.

    Returns a structured report dict suitable for CEO briefing and
    autonomy metrics integration.
    """
    aging      = build_aging_summary(invoices)
    at_risk    = calculate_revenue_at_risk(invoices)

    scored     = []
    high_count = 0
    critical_count = 0
    files_created  = []
    files_skipped  = []

    for inv in invoices:
        rs = calculate_risk_score(inv)
        scored.append({**inv, "days_overdue": _days_overdue(inv), **rs})
        if rs["risk_level"] == "high":
            high_count += 1
        elif rs["risk_level"] == "critical":
            critical_count += 1

        path = create_reminder_approval_file(inv, rs, dry_run=dry_run)
        if path:
            files_created.append(str(path))
        elif rs["risk_level"] in ("high", "critical"):
            files_skipped.append(inv.get("invoice_number", "?"))

    report = {
        "generated_at":          datetime.now(timezone.utc).isoformat(),
        "dry_run":                dry_run,
        "total_invoices":         len(invoices),
        "revenue_at_risk":        at_risk,
        "aging_summary":          aging,
        "high_risk_invoice_count":    high_count,
        "critical_invoice_count":     critical_count,
        "reminder_files_created": files_created,
        "reminder_files_skipped": files_skipped,
        "scored_invoices":        scored,
    }

    _rpe_log("rpe_analysis_complete", {
        "invoices":          len(invoices),
        "revenue_at_risk":   at_risk,
        "high":              high_count,
        "critical":          critical_count,
        "reminders_created": len(files_created),
        "dry_run":           dry_run,
    })

    return report


# ── CEO Briefing section generator (Step 6) ───────────────────────────────────

def format_ceo_briefing_section(report: dict) -> str:
    """
    Render the ## Revenue Protection Overview markdown section
    for injection into CEO briefing output.
    """
    a = report.get("aging_summary", {})

    def _row(bucket: str) -> str:
        b = a.get(bucket, {"count": 0, "amount": 0.0})
        return f"  - {bucket.capitalize()}: {b['count']} invoices / PKR {b['amount']:,.2f}"

    dry_tag = " *(DRY_RUN)*" if report.get("dry_run") else ""

    lines = [
        f"## Revenue Protection Overview{dry_tag}",
        "",
        f"- **Revenue at Risk:** PKR {report['revenue_at_risk']:,.2f}",
        "- **Aging Breakdown:**",
        _row("current"),
        _row("warning"),
        _row("high"),
        _row("critical"),
        f"- **High Risk Invoices:** {report['high_risk_invoice_count']}",
        f"- **Critical Risk Invoices:** {report['critical_invoice_count']}",
    ]

    if report.get("reminder_files_created"):
        lines.append(
            f"- **Reminder Drafts Created:** {len(report['reminder_files_created'])} "
            f"(pending human approval in `/Pending_Approval/`)"
        )

    return "\n".join(lines)


# ── Autonomy metrics extension (Step 7) ───────────────────────────────────────

def extract_metrics_extension(report: dict) -> dict:
    """
    Return the RPE fields to merge into the autonomy metrics JSON.
    Keys are additive — they do not overwrite existing schema fields.
    """
    a = report.get("aging_summary", {})
    return {
        "revenue_at_risk":         report.get("revenue_at_risk", 0.0),
        "high_risk_invoice_count": report.get("high_risk_invoice_count", 0),
        "critical_invoice_count":  report.get("critical_invoice_count", 0),
        "aging_warning_amount":    a.get("warning",  {}).get("amount", 0.0),
        "aging_high_amount":       a.get("high",     {}).get("amount", 0.0),
        "aging_critical_amount":   a.get("critical", {}).get("amount", 0.0),
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _days_overdue(invoice: dict) -> int:
    """
    Compute calendar days between today and the invoice due date.
    Uses invoice_date_due if present, else invoice_date.
    Returns 0 if date cannot be parsed (treat as current).
    """
    raw = invoice.get("invoice_date_due") or invoice.get("invoice_date", "")
    if not raw:
        return 0
    try:
        due = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        return max(0, (today - due).days)
    except (ValueError, AttributeError):
        return 0


def _amount(invoice: dict) -> float:
    """Return the outstanding balance as a float."""
    return float(invoice.get("amount_residual", invoice.get("amount", 0.0)))


def _rpe_log(event: str, payload: dict) -> None:
    """Append a structured log entry to today's vault log file."""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        log_file = LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        existing = []
        if log_file.exists():
            try:
                existing = json.loads(log_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = []
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source":    "revenue_protection_engine",
            "event":     event,
            **payload,
        }
        existing.append(entry)
        log_file.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # Never crash the caller due to logging failure
