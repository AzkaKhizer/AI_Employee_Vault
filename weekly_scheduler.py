"""
weekly_scheduler.py — Sunday Audit Scheduler for AI Employee Vault.

Runs a full autonomy + financial + risk audit and writes:
  /Briefings/Week-YYYY-MM-DD.md

Can be triggered:
  1. Manually:   python weekly_scheduler.py [--dry-run] [--force]
  2. Via cron:   0 9 * * 0 python /path/to/vault/weekly_scheduler.py
  3. Via Task Scheduler (Windows): Run weekly_scheduler.py every Sunday

Flags:
  --dry-run   Compute everything but do not write /Briefings/ file
  --force     Run even if today is not Sunday
  --days N    Days of history to analyse (default: 7)
  --json      Also emit a JSON metrics file alongside the briefing

DRY_RUN environment variable is also honoured.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Bootstrap path so execution_metrics can be imported ──────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from execution_metrics import (  # noqa: E402
    _load_logs, compute_metrics, update_dashboard,
    save_report, _score_badge, VAULT_ROOT, LOG_DIR,
)

VAULT_ROOT = Path(os.getenv("VAULT_ROOT", VAULT_ROOT))
BRIEFINGS_DIR = VAULT_ROOT / "Briefings"
BRIEFINGS_DIR.mkdir(exist_ok=True)

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"


# ── Financial snapshot ────────────────────────────────────────────────────────

def _latest_accounting_snapshot() -> dict:
    """Read the most recent Accounting/*.json snapshot."""
    accounting_dir = VAULT_ROOT / "Accounting"
    if not accounting_dir.exists():
        return {}
    snapshots = sorted(accounting_dir.glob("*-snapshot.json"))
    if not snapshots:
        return {}
    try:
        return json.loads(snapshots[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _financial_summary(snap: dict) -> dict:
    invoices = snap.get("invoices", [])
    total_outstanding = sum(i.get("amount_residual", 0) for i in invoices)
    overdue = [i for i in invoices if i.get("invoice_date_due", "9999") < datetime.now(timezone.utc).date().isoformat()]
    return {
        "overdue_count":     len(overdue),
        "overdue_total":     sum(i.get("amount_residual", 0) for i in overdue),
        "outstanding_count": len(invoices),
        "outstanding_total": total_outstanding,
        "overdue_invoices":  overdue,
    }


# ── Subscription waste detector ───────────────────────────────────────────────

def _detect_subscription_waste() -> list[str]:
    """Scan Business_Goals.md for services with no documented ROI."""
    goals_file = VAULT_ROOT / "Business_Goals.md"
    if not goals_file.exists():
        return []
    content = goals_file.read_text(encoding="utf-8")
    waste = []
    in_table = False
    for line in content.splitlines():
        if "| Service |" in line:
            in_table = True
            continue
        if in_table and line.startswith("|") and "---" not in line:
            cols = [c.strip() for c in line.strip("|").split("|")]
            if len(cols) >= 4:
                service, cost, value, action = cols[0], cols[1], cols[2], cols[3] if len(cols) > 3 else ""
                if service and not service.startswith("*"):
                    if not value or value.lower() in ("", "unknown", "tbd", "?"):
                        waste.append(f"{service} ({cost}/mo) — no ROI documented")
        elif in_table and not line.startswith("|"):
            in_table = False
    return waste


# ── Risk summary ──────────────────────────────────────────────────────────────

def _risk_signals(metrics: dict, fin: dict) -> list[str]:
    signals = []
    if metrics["autonomy_score"] < 40:
        signals.append(f"CRITICAL: Autonomy Score {metrics['autonomy_score']}/100 — manual overhead unsustainable")
    if metrics["failure_rate"] > 20:
        signals.append(f"HIGH: Failure rate {metrics['failure_rate']}% exceeds 20% threshold")
    if metrics["approval_rate"] > 30:
        signals.append(f"MEDIUM: HITL rate {metrics['approval_rate']}% — too many human interventions required")
    if fin.get("overdue_count", 0) > 0:
        signals.append(f"MEDIUM: {fin['overdue_count']} overdue invoice(s), PKR {fin['overdue_total']:,.0f} at risk")
    if not signals:
        signals.append("No critical risk signals detected.")
    return signals


# ── Briefing writer ───────────────────────────────────────────────────────────

def _strategic_actions(metrics: dict, fin: dict, waste: list[str]) -> list[str]:
    actions = []
    if metrics["autonomy_score"] < 65:
        actions.append(
            f"Raise Autonomy Score from {metrics['autonomy_score']} → 80+ by reducing manual approvals "
            f"({metrics['tasks_requiring_approval']} this week). Owner: AI. Add auto-approval rules for low-risk tasks."
        )
    else:
        actions.append(
            f"Maintain Autonomy Score ({metrics['autonomy_score']}/100). Schedule next audit in 7 days. Owner: Scheduler."
        )
    if fin.get("overdue_count", 0) > 0:
        actions.append(
            f"Collect {fin['overdue_count']} overdue invoice(s) (PKR {fin['overdue_total']:,.0f}). "
            f"Run auto_close_all_unpaid or contact clients directly. Owner: AI + Human approval."
        )
    elif fin.get("outstanding_count", 0) > 0:
        actions.append(
            f"Schedule auto_close_all_unpaid for {fin['outstanding_count']} outstanding invoice(s) "
            f"(PKR {fin['outstanding_total']:,.0f}). Owner: AI."
        )
    else:
        actions.append("No outstanding invoices. Create next month's invoices proactively. Owner: AI.")
    if waste:
        actions.append(
            f"Review subscription waste: {', '.join(waste[:2])}. Cancel or document ROI. Owner: Human decision."
        )
    else:
        actions.append(
            "Expand automation coverage: configure gmail_watcher and schedule daily finance_watcher. Owner: AI + Human setup."
        )
    return actions[:3]


def generate_briefing(metrics: dict, fin: dict, waste: list[str], dry_run: bool, week_start: str) -> str:
    score = metrics["autonomy_score"]
    badge = _score_badge(score)
    risks = _risk_signals(metrics, fin)
    actions = _strategic_actions(metrics, fin, waste)
    now = datetime.now(timezone.utc)
    eff = round(
        (1 - (fin.get("outstanding_total", 0) / max(fin.get("outstanding_total", 1) + 1, 1))) * 100, 1
    ) if fin else 0.0

    return f"""# Weekly AI Employee Briefing

**Generated:** {now.strftime('%Y-%m-%d %H:%M')} UTC
**Week of:** {week_start}
**Mode:** {"DRY_RUN — no data written" if dry_run else "LIVE"}

---

## Portfolio Snapshot

| Metric | Value |
|--------|-------|
| Active Projects | {len(list((VAULT_ROOT / 'Projects').glob('*.md')))} |
| Pending Approval | {len(list((VAULT_ROOT / 'Pending_Approval').glob('*.md')))} |
| Done (this week) | {metrics.get('tasks_auto_completed', 0)} |

---

## Autonomy Intelligence

| Metric | Value | Signal |
|--------|-------|--------|
| **Autonomy Score** | **{score}/100** | {badge} |
| Tasks Auto-Completed | {metrics['tasks_auto_completed']} / {metrics['total_tasks_processed']} | — |
| HITL Rate | {metrics['approval_rate']}% | {"🟡 Acceptable" if metrics['approval_rate'] < 20 else "🔴 High"} |
| Failures | {metrics['execution_failures']} ({metrics['failure_rate']}%) | {"🟢 Low" if metrics['failure_rate'] < 10 else "🔴 High"} |
| Avg Iterations/Task | {metrics['average_iterations_per_task']} | — |
| Est. Hours Saved | {metrics['estimated_time_saved_hours']}h | 🟢 |

**Score breakdown:** base {metrics['score_breakdown']['base_auto_rate']} − failure {metrics['score_breakdown']['failure_deduction']} − manual {metrics['score_breakdown']['manual_deduction']} = **{score}**

---

## Financial Efficiency

| Metric | Value |
|--------|-------|
| Overdue Invoices | {fin.get('overdue_count', 0)} (PKR {fin.get('overdue_total', 0):,.0f}) |
| Outstanding Total | {fin.get('outstanding_count', 0)} invoices (PKR {fin.get('outstanding_total', 0):,.0f}) |
| Subscription Waste | {len(waste)} item(s) flagged |
{chr(10).join(f"| — {w} | Review |" for w in waste) if waste else "| — None detected | ✅ |"}

---

## Risk Signals

{chr(10).join(f"- {r}" for r in risks)}

---

## Bottlenecks

- **Autonomy Score:** {"Below target — manual intervention rate too high" if score < 65 else "On track"}
- **Approval queue:** {len(list((VAULT_ROOT / 'Pending_Approval').glob('*.md')))} item(s) pending human review

---

## Subscription Waste

{chr(10).join(f"- {w}" for w in waste) if waste else "- None detected. All subscriptions have documented ROI."}

---

## Strategic Actions (Next 7 Days)

{chr(10).join(f"{i+1}. **{a.split('.')[0].strip()}** — {'.'.join(a.split('.')[1:]).strip()}" for i, a in enumerate(actions))}

---

*Auto-generated by weekly_scheduler.py | Vault: {VAULT_ROOT.name} | DRY_RUN: {dry_run}*
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AI Employee Vault — Weekly Sunday Audit")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not write briefing file")
    parser.add_argument("--force",   action="store_true", help="Run even if today is not Sunday")
    parser.add_argument("--days",    type=int, default=7, help="Days of history (default: 7)")
    parser.add_argument("--json",    action="store_true", help="Also save JSON metrics report")
    args = parser.parse_args()

    dry_run = args.dry_run or DRY_RUN
    now = datetime.now(timezone.utc)

    # Sunday check (weekday 6 = Sunday)
    if now.weekday() != 6 and not args.force:
        print(f"[scheduler] Today is {now.strftime('%A')} — not Sunday. Use --force to run anyway.")
        sys.exit(0)

    week_start = (now - timedelta(days=args.days)).strftime("%Y-%m-%d")
    print(f"[scheduler] Running weekly audit for week of {week_start} (dry_run={dry_run})")

    # ── Step 1: Autonomy metrics ──────────────────────────────────────────
    print("[scheduler] Computing autonomy metrics...")
    entries = _load_logs(args.days)
    metrics = compute_metrics(entries, args.days)

    # ── Step 2: Financial snapshot ────────────────────────────────────────
    print("[scheduler] Loading financial snapshot...")
    snap = _latest_accounting_snapshot()
    fin  = _financial_summary(snap)

    # ── Step 3: Subscription waste ────────────────────────────────────────
    print("[scheduler] Checking subscription waste...")
    waste = _detect_subscription_waste()

    # ── Step 4: Generate briefing ─────────────────────────────────────────
    briefing = generate_briefing(metrics, fin, waste, dry_run, week_start)

    print("\n" + briefing)

    # ── Step 5: Write outputs ─────────────────────────────────────────────
    if not dry_run:
        date_str = now.strftime("%Y-%m-%d")
        brief_file = BRIEFINGS_DIR / f"Week-{date_str}.md"
        brief_file.write_text(briefing, encoding="utf-8")
        print(f"[scheduler] Briefing saved → {brief_file}")

        update_dashboard(metrics)

        if args.json:
            save_report(metrics)
    else:
        print("[scheduler] DRY_RUN — briefing not written to disk.")
        if args.json:
            print("[scheduler] Metrics JSON:")
            print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
