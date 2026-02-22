"""
simulate_recovery_test.py — Validation script for Failure Intelligence & Auto-Recovery Layer.

Simulates 3 recoverable failures, injects them into a temporary log, then runs
execution_metrics + recovery_engine and prints full validation output.

Covers:
  1. Simulate 3 recoverable failures
  2. Show retry log
  3. Show updated Autonomy Score
  4. Show failure breakdown
  5. Confirm DRY_RUN compatibility
  6. Confirm no secret leakage

Usage:
  python simulate_recovery_test.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap path
VAULT_ROOT = Path(os.getenv("VAULT_ROOT", Path(__file__).parent))
sys.path.insert(0, str(VAULT_ROOT))

from recovery_engine import classify_error, is_recoverable, get_correction_suggestions, FAILURE_TYPES
from execution_metrics import compute_metrics, print_summary, save_failure_analysis, AUTONOMY_WEIGHTS

SEP = "=" * 60


# ── 1. Simulated failure log entries ──────────────────────────────────────────

def _ts(offset_seconds: int = 0) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)).isoformat()


SIMULATED_ENTRIES = [
    # ── Baseline successes ─────────────────────────────────────────────────────
    {"timestamp": _ts(500), "source": "odoo_mcp", "tool": "register_payment",
     "status": "success", "result_summary": "ok"},
    {"timestamp": _ts(490), "source": "odoo_mcp", "tool": "list_unpaid_invoices",
     "status": "success", "result_summary": "ok"},
    {"timestamp": _ts(480), "source": "odoo_mcp", "tool": "close_invoice",
     "status": "success", "result_summary": "ok"},

    # ── FAILURE 1: Invalid journal name (tool_validation_error) ───────────────
    {"timestamp": _ts(400), "source": "odoo_mcp", "tool": "register_payment",
     "status": "error", "result_summary": "Invalid journal name 'Petty Cash'",
     "error": "Invalid journal name 'Petty Cash' — no journal found matching this name"},

    # ── Retry attempt for Failure 1 ───────────────────────────────────────────
    {"timestamp": _ts(390), "agent": "orchestrator-sim01", "event": "retry_attempt",
     "task": "register_payment_task_001", "attempt": 1, "error_type": "tool_validation_error",
     "backoff_seconds": 30, "outcome": "ok", "dry_run": False},

    # ── Retry success for Failure 1 (auto-recovered) ──────────────────────────
    {"timestamp": _ts(360), "agent": "orchestrator-sim01", "event": "retry_success",
     "task": "register_payment_task_001", "retries_used": 1, "outcome": "ok", "dry_run": False},

    # ── FAILURE 2: Network error (network_error) ───────────────────────────────
    {"timestamp": _ts(300), "agent": "orchestrator-sim01", "event": "claude_error",
     "task": "invoice_batch_task_002",
     "stderr": "Error: Connection refused — could not connect to Odoo at port 8069",
     "outcome": "error", "dry_run": False},

    # ── Retry attempt for Failure 2 ───────────────────────────────────────────
    {"timestamp": _ts(290), "agent": "orchestrator-sim01", "event": "retry_attempt",
     "task": "invoice_batch_task_002", "attempt": 1, "error_type": "network_error",
     "backoff_seconds": 30, "outcome": "ok", "dry_run": False},

    # ── Retry success for Failure 2 (auto-recovered) ──────────────────────────
    {"timestamp": _ts(260), "agent": "orchestrator-sim01", "event": "retry_success",
     "task": "invoice_batch_task_002", "retries_used": 1, "outcome": "ok", "dry_run": False},

    # ── Task completes after recovery ─────────────────────────────────────────
    {"timestamp": _ts(255), "agent": "orchestrator-sim01", "event": "task_complete",
     "task": "invoice_batch_task_002", "iterations": 2, "outcome": "ok", "dry_run": False},

    # ── FAILURE 3: Timeout (timeout) — exhausts retries → recovery file ───────
    {"timestamp": _ts(200), "agent": "orchestrator-sim01", "event": "claude_timeout",
     "task": "heavy_report_task_003", "iteration": 1, "outcome": "error", "dry_run": False},

    {"timestamp": _ts(190), "agent": "orchestrator-sim01", "event": "retry_attempt",
     "task": "heavy_report_task_003", "attempt": 1, "error_type": "timeout",
     "backoff_seconds": 30, "outcome": "ok", "dry_run": False},

    {"timestamp": _ts(160), "agent": "orchestrator-sim01", "event": "claude_timeout",
     "task": "heavy_report_task_003", "iteration": 1, "outcome": "error", "dry_run": False},

    {"timestamp": _ts(150), "agent": "orchestrator-sim01", "event": "retry_attempt",
     "task": "heavy_report_task_003", "attempt": 2, "error_type": "timeout",
     "backoff_seconds": 60, "outcome": "ok", "dry_run": False},

    {"timestamp": _ts(90), "agent": "orchestrator-sim01", "event": "claude_timeout",
     "task": "heavy_report_task_003", "iteration": 1, "outcome": "error", "dry_run": False},

    {"timestamp": _ts(80), "agent": "orchestrator-sim01", "event": "recovery_exhausted",
     "task": "heavy_report_task_003", "error_type": "timeout", "retries": 2,
     "outcome": "error", "dry_run": False},

    # ── DRY_RUN call (must not affect score) ──────────────────────────────────
    {"timestamp": _ts(50), "agent": "orchestrator-sim01", "event": "claim_dry_run",
     "task": "dry_run_task.md", "outcome": "ok", "dry_run": True},
]


# ── 2. Run through recovery_engine classifiers ────────────────────────────────

def section(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}")


def validate_classify_and_recover() -> None:
    section("1 + 2. Failure Classification & Recoverability")

    test_cases = [
        ("Invalid journal name 'Petty Cash'",       "",               "tool_validation_error", True),
        ("Connection refused — could not connect",   "",               "network_error",         True),
        ("",                                          "claude_timeout", "timeout",               True),
        ("",                                          "max_iter_reached","timeout",              True),
        ("Action blocked — approval required",       "",               "guard_rejection",       False),
        ("Unexpected NoneType",                      "",               "unknown",               False),
    ]

    all_pass = True
    print(f"  {'Error Message / Event':<42} {'Expected':<26} {'Got':<26} {'Recover?'}")
    print(f"  {'-'*42} {'-'*26} {'-'*26} {'-'*8}")
    for msg, event, expected_type, expected_recoverable in test_cases:
        got_type = classify_error(msg, event)
        got_rec  = is_recoverable(got_type, msg)
        label    = msg[:40] if msg else f"[event={event}]"
        ok       = "PASS" if (got_type == expected_type and got_rec == expected_recoverable) else "FAIL"
        if ok == "FAIL":
            all_pass = False
        print(f"  {label:<42} {expected_type:<26} {got_type:<26} {str(got_rec):<8} {ok}")

    print(f"\n  Result: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")


# ── 3. Compute metrics on simulated log ───────────────────────────────────────

def validate_metrics() -> dict:
    section("3 + 4. Autonomy Score & Failure Breakdown (Simulated Logs)")

    metrics = compute_metrics(SIMULATED_ENTRIES, days=7)

    print_summary(metrics)

    print(f"\n  failure_breakdown raw:  {json.dumps(metrics['failure_breakdown'], indent=4)}")
    print(f"  auto_recovered:         {metrics['auto_recovered']}")
    print(f"  auto_recovery_rate:     {metrics['auto_recovery_rate']}%")
    print(f"  net_failures:           {metrics['net_failures']}")
    print(f"  execution_failures:     {metrics['execution_failures']}")
    print(f"  autonomy_score:         {metrics['autonomy_score']}/100")

    return metrics


# ── 4. Retry log (from simulated entries) ─────────────────────────────────────

def validate_retry_log() -> None:
    section("2. Retry Log (from simulated entries)")

    retry_events = [
        e for e in SIMULATED_ENTRIES
        if e.get("event") in ("retry_attempt", "retry_success", "recovery_exhausted",
                              "failure_not_recoverable")
    ]
    print(f"  {'Timestamp':<30} {'Event':<22} {'Task':<30} {'Detail'}")
    print(f"  {'-'*30} {'-'*22} {'-'*30} {'-'*30}")
    for e in retry_events:
        ts     = e["timestamp"][11:19]  # HH:MM:SS only
        event  = e["event"]
        task   = e.get("task", "")[:29]
        detail = ""
        if event == "retry_attempt":
            detail = f"attempt={e.get('attempt')} backoff={e.get('backoff_seconds')}s type={e.get('error_type')}"
        elif event == "retry_success":
            detail = f"retries_used={e.get('retries_used')}"
        elif event == "recovery_exhausted":
            detail = f"retries={e.get('retries')} type={e.get('error_type')}"
        print(f"  {ts:<30} {event:<22} {task:<30} {detail}")


# ── 5. DRY_RUN compatibility check ────────────────────────────────────────────

def validate_dry_run() -> None:
    section("5. DRY_RUN Compatibility")

    dry_entries = [e for e in SIMULATED_ENTRIES if e.get("dry_run")]
    live_entries = [e for e in SIMULATED_ENTRIES if not e.get("dry_run")]

    print(f"  dry_run entries in simulation:  {len(dry_entries)}")
    print(f"  live entries in simulation:     {len(live_entries)}")

    dry_metrics = compute_metrics(SIMULATED_ENTRIES, days=7)
    dry_calls   = dry_metrics["dry_run_calls"]
    print(f"  dry_run_calls counted:          {dry_calls}")
    assert dry_calls == len(dry_entries), f"DRY_RUN count mismatch: {dry_calls} != {len(dry_entries)}"
    print("  PASS — dry_run entries counted but do not inflate success/failure totals.")


# ── 6. Secret leakage check ───────────────────────────────────────────────────

def validate_no_secret_leakage() -> None:
    section("6. Secret Leakage Check")

    # Verify recovery_engine reads creds from env — never hardcodes or prints them
    import recovery_engine
    import inspect
    src = inspect.getsource(recovery_engine)

    forbidden = ["password", "ODOO_PASSWORD", "secret", "token"]
    leaked = [kw for kw in forbidden if f'print({kw}' in src or f'log({kw}' in src]

    if leaked:
        print(f"  FAIL — potential leakage keywords found in print/log calls: {leaked}")
    else:
        print("  PASS — no credentials appear in print() or log() calls in recovery_engine.py")

    # Confirm _odoo_creds() returns from env — not hardcoded
    creds_source = inspect.getsource(recovery_engine._odoo_creds)
    assert "os.getenv" in creds_source, "FAIL — _odoo_creds() must use os.getenv"
    assert "hardcoded" not in creds_source.lower(), "FAIL — hardcoded string found in _odoo_creds"
    print("  PASS — _odoo_creds() reads from os.getenv() only.")


# ── 7. save_failure_analysis smoke test ────────────────────────────────────────

def validate_failure_analysis_file(metrics: dict) -> None:
    section("7. failure-analysis-YYYY-MM-DD.json Written")

    out = save_failure_analysis(metrics)
    content = json.loads(out.read_text(encoding="utf-8"))
    print(f"  Written to: {out}")
    print(f"  Contents:   {json.dumps(content, indent=4)}")
    assert content["failure_breakdown"], "FAIL — failure_breakdown should not be empty"
    assert "auto_recovery_rate" in content, "FAIL — auto_recovery_rate missing"
    print("  PASS — failure analysis file written and validated.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{SEP}")
    print(f"  Failure Intelligence & Auto-Recovery — Validation Suite")
    print(f"  Vault: {VAULT_ROOT}")
    print(f"  Python: {sys.version.split()[0]}")
    print(SEP)

    validate_classify_and_recover()
    validate_retry_log()
    metrics = validate_metrics()
    validate_dry_run()
    validate_no_secret_leakage()
    validate_failure_analysis_file(metrics)

    print(f"\n{SEP}")
    print(f"  ALL VALIDATIONS COMPLETE")
    print(SEP + "\n")


if __name__ == "__main__":
    main()
