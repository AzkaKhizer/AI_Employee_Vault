"""
execution_metrics.py — Autonomy Intelligence Layer for AI Employee Vault.

Parses /Logs/YYYY-MM-DD.json files, computes execution metrics and an
investor-grade Autonomy Score, then optionally updates Dashboard.md.

Usage:
  python execution_metrics.py [--days N] [--update-dashboard] [--save-report]

Flags:
  --days N             Number of past days to analyse (default: 7)
  --update-dashboard   Inject results into Dashboard.md Autonomy Metrics section
  --save-report        Write full JSON report to /Logs/metrics-report-YYYYMMDD.json
  --json               Print raw JSON output (default: human-readable summary)

Autonomy Score formula (0–100):
  score = (auto_completed / total) * 100
          - (failure_rate * FAILURE_PENALTY)
          - MANUAL_INTERVENTION_WEIGHT
  Clamped to [0, 100].

Weights are read from environment or use defaults defined in AUTONOMY_WEIGHTS below.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Graceful import — recovery_engine.py lives in the same directory
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from recovery_engine import classify_error as _classify_error, FAILURE_TYPES
    _RECOVERY_ENGINE_AVAILABLE = True
except ImportError:
    _RECOVERY_ENGINE_AVAILABLE = False
    FAILURE_TYPES = frozenset({"guard_rejection", "tool_validation_error", "network_error", "timeout", "unknown"})
    def _classify_error(error_msg: str, event: str = "") -> str:  # noqa: E302
        return "unknown"

# Force UTF-8 output on Windows (emoji badges in markdown-destined strings)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Python 3.7+
    except AttributeError:
        pass


# ── Vault paths ───────────────────────────────────────────────────────────────

VAULT_ROOT = Path(os.getenv("VAULT_ROOT", Path(__file__).parent))
LOG_DIR           = VAULT_ROOT / "Logs"
DASH_FILE         = VAULT_ROOT / "Dashboard.md"
HISTORY_FILE      = LOG_DIR / "metrics-history.json"
MAX_HISTORY_WEEKS = 12


# ── Autonomy Score weights (override via env or SECURITY.md config section) ───

AUTONOMY_WEIGHTS = {
    # Penalty per percentage point of failure rate (0–1 scale)
    "failure_penalty":           float(os.getenv("AUTONOMY_FAILURE_PENALTY", "20")),
    # Fixed deduction for each manual intervention (approval-required event)
    "manual_intervention_weight": float(os.getenv("AUTONOMY_MANUAL_WEIGHT", "5")),
    # Estimated hours saved per auto-completed task
    "time_saved_multiplier":      float(os.getenv("AUTONOMY_TIME_SAVED_MULTIPLIER", "0.25")),
}


# ── Log entry classifiers ─────────────────────────────────────────────────────

# MCP tool logs: {source, tool, status: "invoked"|"success"|"error", result_summary}
# Watcher/orchestrator logs: {watcher|agent, event, outcome, dry_run}

_WRITE_TOOLS = {
    "register_payment", "close_invoice", "auto_close_all_unpaid",
    "create_draft_invoice", "action_post",
}

_READ_TOOLS = {
    "list_unpaid_invoices", "get_revenue_summary", "list_expenses",
    "list_expenses",
}


def _date_range(days: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def _load_logs(days: int) -> list[dict]:
    """Load and merge all log entries from the last N days."""
    entries = []
    for date_str in _date_range(days):
        log_file = LOG_DIR / f"{date_str}.json"
        if not log_file.exists():
            continue
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                entries.extend(data)
        except (json.JSONDecodeError, OSError):
            pass
    # Sort chronologically
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return entries


# ── History & delta functions ─────────────────────────────────────────────────

def append_metrics_history(metrics: dict, revenue_collected: float = 0.0):
    """
    Append a slim snapshot of current metrics to HISTORY_FILE.
    Keeps at most MAX_HISTORY_WEEKS entries (rolling window).
    Returns the previous snapshot dict, or None on first run.
    """
    history = []
    if HISTORY_FILE.exists():
        try:
            raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                history = raw
        except (json.JSONDecodeError, OSError):
            pass

    previous = history[-1] if history else None

    snapshot = {
        "generated_at":               metrics["generated_at"],
        "autonomy_score":             metrics["autonomy_score"],
        "failure_rate":               metrics["failure_rate"],
        "approval_rate":              metrics["approval_rate"],
        "tasks_auto_completed":       metrics["tasks_auto_completed"],
        "total_tasks_processed":      metrics["total_tasks_processed"],
        "estimated_time_saved_hours": metrics["estimated_time_saved_hours"],
        "revenue_collected":          round(revenue_collected, 2),
    }

    history.append(snapshot)
    history = history[-MAX_HISTORY_WEEKS:]

    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return previous


def compute_deltas(current: dict, previous: dict) -> dict:
    """
    Compute week-over-week signed deltas for key metrics.
    Pass None for previous to receive a no-data dict (all values None).
    """
    if previous is None:
        return {
            "autonomy_score_delta":     None,
            "failure_rate_delta":       None,
            "approval_rate_delta":      None,
            "revenue_collection_delta": None,
        }
    return {
        "autonomy_score_delta":     round(current["autonomy_score"]  - previous["autonomy_score"], 1),
        "failure_rate_delta":       round(current["failure_rate"]    - previous["failure_rate"], 1),
        "approval_rate_delta":      round(current["approval_rate"]   - previous["approval_rate"], 1),
        "revenue_collection_delta": round(
            current.get("revenue_collected", 0.0) - previous.get("revenue_collected", 0.0), 2
        ),
    }


# ── Metric computation ────────────────────────────────────────────────────────

def compute_metrics(entries: list[dict], days: int) -> dict:
    """
    Parse log entries and return a structured metrics dict.

    Task definition:
      - MCP tool: one (invoked → success/error) cycle = 1 task
      - Orchestrator: one (task_claimed → task_complete/failed) cycle = 1 task
      - Watcher: one (poll → process) per signal = 1 task
    """
    # ── MCP tool metrics ──────────────────────────────────────────────────
    mcp_invocations: dict[str, list[dict]] = {}   # tool_call_key → [invoked, result]
    mcp_errors = 0
    mcp_auto_ok = 0
    mcp_error_response = 0

    # ── Orchestrator metrics ──────────────────────────────────────────────
    orch_tasks_complete = 0
    orch_tasks_failed = 0
    orch_max_iter = 0
    orch_iterations: list[int] = []

    # ── Approval / HITL metrics ───────────────────────────────────────────
    approval_requests = 0
    approval_executed = 0

    # ── Watcher metrics ───────────────────────────────────────────────────
    watcher_tasks = 0
    watcher_errors = 0

    # ── Dry-run calls ─────────────────────────────────────────────────────
    dry_run_calls = 0

    # ── Auto-recovery tracking ────────────────────────────────────────────
    auto_recovered = 0

    # ── Circuit Breaker tracking ───────────────────────────────────────────
    circuit_opened_count  = 0
    circuit_blocked_count = 0

    # ── Social media tracking ─────────────────────────────────────────────
    social_drafts_created  = 0
    social_posts_approved  = 0
    social_posts_rejected  = 0

    # ── Failure classification ────────────────────────────────────────────
    failure_breakdown: dict[str, int] = {ft: 0 for ft in FAILURE_TYPES}

    # ── Iteration tracking (orchestrator) ────────────────────────────────
    _active_task_iterations: dict[str, int] = {}

    for e in entries:
        if e.get("dry_run"):
            dry_run_calls += 1

        # ── MCP tool entries ─────────────────────────────────────────────
        if "tool" in e and "source" in e:
            tool = e["tool"]
            status = e.get("status", "")

            if status == "invoked":
                pass  # tracking start
            elif status == "success":
                rs = e.get("result_summary", "ok")
                if rs == "ok":
                    mcp_auto_ok += 1
                else:
                    mcp_error_response += 1
            elif status == "error":
                mcp_errors += 1
                err_msg = e.get("result_summary", "") or e.get("error", "")
                ft = _classify_error(err_msg, "")
                failure_breakdown[ft] = failure_breakdown.get(ft, 0) + 1

        # ── Orchestrator entries ─────────────────────────────────────────
        elif "agent" in e or e.get("watcher") == "orchestrator":
            event = e.get("event", "")

            if event == "task_complete":
                orch_tasks_complete += 1
                task = e.get("task", "unknown")
                iters = e.get("iterations", 1)
                orch_iterations.append(iters)
                _active_task_iterations.pop(task, None)

            elif event == "claude_invoke":
                task = e.get("task", "")
                _active_task_iterations[task] = _active_task_iterations.get(task, 0) + 1

            elif event == "retry_success":
                # A recoverable failure was retried and ultimately succeeded
                auto_recovered += 1

            elif event == "claude_error":
                orch_tasks_failed += 1
                err_msg = e.get("stderr", "") or e.get("error", "")
                ft = _classify_error(err_msg, event)
                failure_breakdown[ft] = failure_breakdown.get(ft, 0) + 1

            elif event == "claude_timeout":
                orch_tasks_failed += 1
                ft = _classify_error("", event)
                failure_breakdown[ft] = failure_breakdown.get(ft, 0) + 1

            elif event == "release_failed":
                orch_tasks_failed += 1
                ft = _classify_error("", event)
                failure_breakdown[ft] = failure_breakdown.get(ft, 0) + 1

            elif event == "max_iter_reached":
                orch_max_iter += 1
                task = e.get("task", "")
                iters = _active_task_iterations.pop(task, e.get("max_iter", 10))
                orch_iterations.append(iters)
                ft = _classify_error("", event)
                failure_breakdown[ft] = failure_breakdown.get(ft, 0) + 1

            elif event == "circuit_opened":
                circuit_opened_count += 1

            elif event == "circuit_blocked":
                circuit_blocked_count += 1

        # ── Watcher / filesystem entries ─────────────────────────────────
        elif "watcher" in e:
            event = e.get("event", "")
            outcome = e.get("outcome", "ok")

            if event in ("file_routed", "email_processed", "message_processed", "snapshot_saved"):
                if outcome == "ok":
                    watcher_tasks += 1
                else:
                    watcher_errors += 1
                    ft = _classify_error("", event)
                    failure_breakdown[ft] = failure_breakdown.get(ft, 0) + 1

            elif event in ("poll_error", "process_error", "watcher_died"):
                watcher_errors += 1
                ft = _classify_error("", event)
                failure_breakdown[ft] = failure_breakdown.get(ft, 0) + 1

        # ── Social media entries ──────────────────────────────────────────
        if e.get("source") == "social_mcp":
            ev = e.get("event", "")
            if ev == "social_draft_created":
                social_drafts_created += 1
            elif ev == "social_post_approved":
                social_posts_approved += 1
            elif ev == "social_post_rejected":
                social_posts_rejected += 1

        # ── Approval pipeline entries ────────────────────────────────────
        if e.get("event") == "write_needs_action" or e.get("tool") in (
            "approval-detector", "email-executor"
        ):
            pass  # Tracked separately below

    # Count approval files written (by scanning Pending_Approval dir)
    pending_approval_dir = VAULT_ROOT / "Pending_Approval"
    approved_dir = VAULT_ROOT / "Approved"
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for f in pending_approval_dir.glob("*.md") if pending_approval_dir.exists() else []:
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime >= cutoff:
            approval_requests += 1

    for f in approved_dir.glob("*.md") if approved_dir.exists() else []:
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime >= cutoff:
            approval_executed += 1

    # ── Aggregate ─────────────────────────────────────────────────────────
    # "Tasks" = MCP tool outcomes + orchestrator task completions + watcher signals
    mcp_total    = mcp_auto_ok + mcp_error_response + mcp_errors
    total_tasks  = mcp_total + orch_tasks_complete + orch_tasks_failed + orch_max_iter + watcher_tasks

    auto_completed     = mcp_auto_ok + orch_tasks_complete + watcher_tasks
    tasks_req_approval = approval_requests
    execution_failures = mcp_errors + orch_tasks_failed + orch_max_iter + watcher_errors

    # Auto-recovered failures count as successes for scoring purposes
    net_failures       = max(0, execution_failures - auto_recovered)
    net_auto_completed = auto_completed + auto_recovered
    auto_recovery_rate = round(
        (auto_recovered / execution_failures * 100) if execution_failures > 0 else 100.0, 1
    )

    failure_rate  = net_failures / max(total_tasks, 1)
    approval_rate = tasks_req_approval / max(total_tasks, 1)
    avg_iterations = (
        sum(orch_iterations) / len(orch_iterations) if orch_iterations else 1.0
    )
    time_saved = net_auto_completed * AUTONOMY_WEIGHTS["time_saved_multiplier"]

    # ── Autonomy Score (uses net figures — auto-recovered = success) ──────
    manual_deduction = min(tasks_req_approval * AUTONOMY_WEIGHTS["manual_intervention_weight"], 30)

    raw_score = (
        (net_auto_completed / max(total_tasks, 1)) * 100
        - (failure_rate * AUTONOMY_WEIGHTS["failure_penalty"])
        - manual_deduction
    )
    autonomy_score = round(max(0.0, min(100.0, raw_score)), 1)

    return {
        "period_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tasks_processed":       total_tasks,
        "tasks_auto_completed":        net_auto_completed,
        "tasks_requiring_approval":    tasks_req_approval,
        "approval_rate":               round(approval_rate * 100, 1),
        "execution_failures":          execution_failures,
        "net_failures":                net_failures,
        "failure_rate":                round(failure_rate * 100, 1),
        "auto_recovered":              auto_recovered,
        "auto_recovery_rate":          auto_recovery_rate,
        "failure_breakdown":           failure_breakdown,
        "average_iterations_per_task": round(avg_iterations, 2),
        "estimated_time_saved_hours":  round(time_saved, 2),
        "dry_run_calls":               dry_run_calls,
        "circuit_opened_count":        circuit_opened_count,
        "circuit_blocked_count":       circuit_blocked_count,
        # ── RPE fields (populated externally via merge) ────────────────────
        "revenue_at_risk":             0.0,
        "high_risk_invoice_count":     0,
        "critical_invoice_count":      0,
        "aging_warning_amount":        0.0,
        "aging_high_amount":           0.0,
        "aging_critical_amount":       0.0,
        # ── Social media fields (populated from log events) ────────────────
        "social_drafts_created":       social_drafts_created,
        "social_posts_approved":       social_posts_approved,
        "social_posts_rejected":       social_posts_rejected,
        # ── EPE fields (populated externally via merge from run_priority_cycle) ──
        "critical_priority_tasks":     0,
        "high_priority_tasks":         0,
        "autonomy_score":              autonomy_score,
        "score_breakdown": {
            "base_auto_rate":          round((net_auto_completed / max(total_tasks, 1)) * 100, 1),
            "failure_deduction":       round(failure_rate * AUTONOMY_WEIGHTS["failure_penalty"], 1),
            "manual_deduction":        round(manual_deduction, 1),
            "auto_recovery_boost":     round(auto_recovered / max(total_tasks, 1) * 100, 1),
        },
        "weights_used":                AUTONOMY_WEIGHTS,
        "sub_totals": {
            "mcp_auto_ok":             mcp_auto_ok,
            "mcp_error_response":      mcp_error_response,
            "mcp_hard_errors":         mcp_errors,
            "orchestrator_complete":   orch_tasks_complete,
            "orchestrator_failed":     orch_tasks_failed,
            "orchestrator_max_iter":   orch_max_iter,
            "watcher_signals":         watcher_tasks,
            "watcher_errors":          watcher_errors,
            "approval_requests":       approval_requests,
            "approval_executed":       approval_executed,
        },
    }


# ── Dashboard integration ─────────────────────────────────────────────────────

_DASHBOARD_SECTION_START = "## Autonomy Metrics"
_DASHBOARD_SECTION_END   = "---"

def _trend_arrow(delta, threshold: float = 0.5) -> str:
    """Return directional arrow: ▲ (up), ▼ (down), or → (stable)."""
    if delta is None:
        return "→"
    if delta > threshold:
        return "▲"
    if delta < -threshold:
        return "▼"
    return "→"


def _delta_str(delta, higher_is_better: bool = True) -> str:
    """Format a delta with its trend arrow for inline display. Empty string if None."""
    if delta is None:
        return ""
    arrow = _trend_arrow(delta)
    sign  = "+" if delta >= 0 else ""
    return f" {arrow}({sign}{delta:.1f})"


def _momentum_label(delta, higher_is_better: bool = True) -> str:
    """Return 🟢 Improving / 🟡 Stable / 🔴 Declining given a signed delta."""
    if delta is None:
        return "🟡 No prior data"
    improving = delta > 0.5 if higher_is_better else delta < -0.5
    declining  = delta < -0.5 if higher_is_better else delta > 0.5
    if improving:
        return "🟢 Improving"
    if declining:
        return "🔴 Declining"
    return "🟡 Stable"


def _score_badge(score: float) -> str:
    if score >= 85:
        return "🟢 STRONG"
    if score >= 65:
        return "🟡 MODERATE"
    if score >= 40:
        return "🟠 DEVELOPING"
    return "🔴 WEAK"


def _failure_breakdown_md(fb: dict) -> str:
    """Return a compact markdown failure breakdown sub-table, or empty string if no failures."""
    rows = [(ft, cnt) for ft, cnt in fb.items() if cnt > 0]
    if not rows:
        return ""
    rows.sort(key=lambda x: -x[1])
    lines = ["\n**Failure Breakdown:**\n", "| Type | Count |", "|------|-------|"]
    for ft, cnt in rows:
        lines.append(f"| {ft} | {cnt} |")
    return "\n".join(lines) + "\n"


def update_dashboard(metrics: dict, deltas: dict = None) -> None:
    """Inject or replace the Autonomy Metrics section in Dashboard.md."""
    if not DASH_FILE.exists():
        print(f"[metrics] Dashboard.md not found at {DASH_FILE}", file=sys.stderr)
        return

    content = DASH_FILE.read_text(encoding="utf-8")
    score   = metrics["autonomy_score"]
    days    = metrics["period_days"]
    d       = deltas or {}

    score_trend = _delta_str(d.get("autonomy_score_delta"), higher_is_better=True)
    fail_trend  = _delta_str(d.get("failure_rate_delta"),   higher_is_better=False)
    hitl_trend  = _delta_str(d.get("approval_rate_delta"),  higher_is_better=False)

    section = f"""\
## Autonomy Metrics (Last {days} Days)

| Metric | Value |
|--------|-------|
| **Autonomy Score** | {score}/100 — {_score_badge(score)}{score_trend} |
| Tasks Completed | {metrics['tasks_auto_completed']} / {metrics['total_tasks_processed']} |
| HITL Rate | {metrics['approval_rate']}% ({metrics['tasks_requiring_approval']} approvals){hitl_trend} |
| Failures | {metrics['execution_failures']} ({metrics['failure_rate']}%){fail_trend} |
| Auto-Recovered | {metrics.get('auto_recovered', 0)} (Rate: {metrics.get('auto_recovery_rate', 100.0):.1f}%) |
| Avg Iterations/Task | {metrics['average_iterations_per_task']} |
| Estimated Hours Saved | {metrics['estimated_time_saved_hours']}h |
| Dry-Run Calls | {metrics['dry_run_calls']} |
| Circuit Opened | {metrics.get('circuit_opened_count', 0)} |
| Circuit Blocked | {metrics.get('circuit_blocked_count', 0)} |
{_failure_breakdown_md(metrics.get('failure_breakdown', {}))}
*Score = auto_rate − failure_deduction({metrics['score_breakdown']['failure_deduction']}) − manual_deduction({metrics['score_breakdown']['manual_deduction']})*
*Generated: {metrics['generated_at'][:19]}Z*

"""

    # Replace existing section or append
    pattern = re.compile(
        r"## Autonomy Metrics.*?(?=\n## |\Z)", re.DOTALL
    )
    if pattern.search(content):
        new_content = pattern.sub(section.rstrip() + "\n", content)
    else:
        # Append before the last --- or at end
        if "\n---\n" in content:
            new_content = content.rsplit("\n---\n", 1)[0] + "\n\n" + section + "\n---\n" + content.rsplit("\n---\n", 1)[1]
        else:
            new_content = content.rstrip() + "\n\n" + section

    DASH_FILE.write_text(new_content, encoding="utf-8")
    print(f"[metrics] Dashboard.md updated — Autonomy Score: {score}/100")


# ── Report saver ──────────────────────────────────────────────────────────────

def save_report(metrics: dict) -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = LOG_DIR / f"metrics-report-{date_str}.json"
    out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[metrics] Report saved → {out}")
    return out


# ── Failure analysis saver ────────────────────────────────────────────────────

def save_failure_analysis(metrics: dict) -> Path:
    """Write a focused failure breakdown to /Logs/failure-analysis-YYYY-MM-DD.json."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = LOG_DIR / f"failure-analysis-{date_str}.json"
    report = {
        "date":               date_str,
        "generated_at":       metrics["generated_at"],
        "period_days":        metrics["period_days"],
        "total_failures":     metrics["execution_failures"],
        "net_failures":       metrics.get("net_failures", metrics["execution_failures"]),
        "auto_recovered":     metrics.get("auto_recovered", 0),
        "auto_recovery_rate": metrics.get("auto_recovery_rate", 100.0),
        "failure_breakdown":  metrics.get("failure_breakdown", {}),
    }
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[metrics] Failure analysis saved → {out}")
    return out


# ── Human-readable summary ────────────────────────────────────────────────────

def print_summary(m: dict, deltas: dict = None) -> None:
    d   = deltas or {}
    sep = "-" * 52
    print(f"\n{sep}")
    print(f"  AI Employee — Autonomy Intelligence Report")
    print(f"  Period: Last {m['period_days']} days  |  {m['generated_at'][:10]}")
    print(sep)
    print(f"  Autonomy Score:         {m['autonomy_score']:>6.1f} / 100   {_score_badge(m['autonomy_score'])}{_delta_str(d.get('autonomy_score_delta'), higher_is_better=True)}")
    print(f"  Total Tasks:            {m['total_tasks_processed']:>6}")
    print(f"  Auto-Completed:         {m['tasks_auto_completed']:>6}  ({100 - m['failure_rate'] - m['approval_rate']:.1f}% fully autonomous)")
    print(f"  Requiring Approval:     {m['tasks_requiring_approval']:>6}  (HITL rate: {m['approval_rate']}%){_delta_str(d.get('approval_rate_delta'), higher_is_better=False)}")
    print(f"  Failures:               {m['execution_failures']:>6}  ({m['failure_rate']}%){_delta_str(d.get('failure_rate_delta'), higher_is_better=False)}")
    print(f"  Avg Iterations/Task:    {m['average_iterations_per_task']:>6.2f}")
    print(f"  Est. Hours Saved:       {m['estimated_time_saved_hours']:>6.2f}h")
    print(f"  Dry-Run Calls:          {m['dry_run_calls']:>6}")
    if m.get("circuit_opened_count", 0) > 0 or m.get("circuit_blocked_count", 0) > 0:
        print(f"  Circuit Opened:         {m.get('circuit_opened_count', 0):>6}")
        print(f"  Circuit Blocked Tasks:  {m.get('circuit_blocked_count', 0):>6}")
    if any(v is not None for v in d.values()):
        print(sep)
        print(f"  Week-over-Week Deltas:")
        print(f"    Autonomy Score:{_delta_str(d.get('autonomy_score_delta'), higher_is_better=True) or '  → (no prior data)'}")
        print(f"    Failure Rate:  {_delta_str(d.get('failure_rate_delta'),   higher_is_better=False) or '  → (no prior data)'}")
        print(f"    Approval Rate: {_delta_str(d.get('approval_rate_delta'),  higher_is_better=False) or '  → (no prior data)'}")
    fb = m.get("failure_breakdown", {})
    if m.get("execution_failures", 0) > 0:
        print(sep)
        print(f"  Failure Breakdown:  (total={m['execution_failures']}, net={m.get('net_failures', m['execution_failures'])}, recovered={m.get('auto_recovered', 0)})")
        for ft in ("tool_validation_error", "network_error", "timeout", "guard_rejection", "unknown"):
            cnt = fb.get(ft, 0)
            if cnt > 0:
                print(f"    {ft:<26} {cnt:>3}")
        print(f"  Auto-Recovery Rate:     {m.get('auto_recovery_rate', 100.0):>5.1f}%")
    print(sep)
    print(f"  Score Breakdown:")
    b = m["score_breakdown"]
    print(f"    Base auto-rate:       {b['base_auto_rate']:>6.1f}")
    print(f"    Failure deduction:  − {b['failure_deduction']:>5.1f}")
    print(f"    Manual deduction:   − {b['manual_deduction']:>5.1f}")
    print(f"    -------------------------")
    print(f"    Final score:          {m['autonomy_score']:>6.1f}")
    print(sep + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AI Employee Vault — Autonomy Metrics Engine")
    parser.add_argument("--days",             type=int, default=7, help="Days to analyse (default: 7)")
    parser.add_argument("--update-dashboard", action="store_true",  help="Write metrics to Dashboard.md")
    parser.add_argument("--save-report",           action="store_true",  help="Save JSON report to /Logs/")
    parser.add_argument("--save-failure-analysis", action="store_true",  help="Save failure breakdown to /Logs/failure-analysis-YYYYMMDD.json")
    parser.add_argument("--json",                  action="store_true",  help="Print raw JSON")
    args = parser.parse_args()

    entries  = _load_logs(args.days)
    metrics  = compute_metrics(entries, args.days)
    previous = append_metrics_history(metrics)
    deltas   = compute_deltas(metrics, previous)
    metrics["deltas"] = deltas

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print_summary(metrics, deltas)

    if args.update_dashboard:
        update_dashboard(metrics, deltas)

    if args.save_report:
        save_report(metrics)

    if args.save_failure_analysis:
        save_failure_analysis(metrics)


if __name__ == "__main__":
    main()
