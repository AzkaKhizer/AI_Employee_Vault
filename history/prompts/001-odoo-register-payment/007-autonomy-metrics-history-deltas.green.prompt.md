---
id: "007"
title: Autonomy Metrics History and WoW Deltas
stage: green
date: 2026-02-21
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 002-close-invoice-batch-tools
user: HP
command: extend execution_metrics.py with history, deltas, trend arrows
labels: ["execution_metrics","autonomy","dashboard","history","deltas","ceo-briefing"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - execution_metrics.py
  - .claude/skills/ceo-briefing/SKILL.md
tests:
  - "python execution_metrics.py --days 7  (first run — no deltas shown)"
  - "python execution_metrics.py --days 7  (second run — WoW Deltas section rendered)"
  - "python execution_metrics.py --days 7 --update-dashboard  (trend arrows injected into Dashboard.md)"
  - "Logs/metrics-history.json written with 2 entries, correct schema"
---

## Prompt

Extend execution_metrics.py:

1. Store weekly metrics snapshots to:
   /Logs/metrics-history.json

2. On each run:
   - Append current summary
   - Keep last 12 weeks

3. Compute:
   - week_over_week_autonomy_delta
   - failure_rate_delta
   - approval_rate_delta
   - revenue_collection_delta

4. Update Dashboard.md with:
   ▲ improving
   ▼ declining
   → stable

5. In CEO briefing:
   Add "Momentum Signal" section:
     - Operational momentum
     - Financial momentum
     - Risk momentum

## Response snapshot

Added to `execution_metrics.py`:
- `HISTORY_FILE = LOG_DIR / "metrics-history.json"` and `MAX_HISTORY_WEEKS = 12` constants
- `append_metrics_history(metrics, revenue_collected=0.0)` — loads history, appends slim snapshot (8 fields), trims to 12, saves, returns previous entry
- `compute_deltas(current, previous)` — returns signed deltas for autonomy_score, failure_rate, approval_rate, revenue_collection (all None if no prior data)
- `_trend_arrow(delta, threshold=0.5)` — returns ▲/▼/→
- `_delta_str(delta, higher_is_better)` — inline formatted delta string e.g. `→(+0.0)`
- `_momentum_label(delta, higher_is_better)` — returns 🟢 Improving / 🟡 Stable / 🔴 Declining
- Updated `update_dashboard(metrics, deltas=None)` — trend arrows in Autonomy Score, HITL Rate, Failures rows
- Updated `print_summary(m, deltas=None)` — inline deltas on key lines + "Week-over-Week Deltas" block when data available
- Updated `main()` — calls `append_metrics_history`, `compute_deltas`, passes deltas to `print_summary` and `update_dashboard`

Updated `.claude/skills/ceo-briefing/SKILL.md`:
- Added step 5a: load metrics history, compute WoW deltas with threshold-based arrow logic
- Added "Momentum Signal" table section in briefing template (Operational / Risk / Approval Load / Financial rows with WoW Delta column)

## Outcome

- ✅ Impact: execution_metrics.py now maintains a rolling 12-week history and shows week-over-week trend arrows on every run. Dashboard.md Autonomy Metrics section includes inline ▲▼→ indicators. CEO briefing has structured Momentum Signal table.
- 🧪 Tests: All three CLI invocations passed. History file written correctly with 2 entries. Dashboard.md updated with trend arrows (verified via regex extraction).
- 📁 Files: execution_metrics.py (+95 lines), SKILL.md (Momentum Signal section added)
- 🔁 Next prompts: commit and push to branch 002-close-invoice-batch-tools
- 🧠 Reflection: revenue_collection_delta defaults to 0.0 from execution_metrics.py alone; weekly_scheduler.py can pass the real value via append_metrics_history(metrics, revenue_collected=fin["outstanding_total"]) for richer financial momentum tracking.

## Evaluation notes (flywheel)

- Failure modes observed: UnicodeEncodeError on Windows console when printing emoji — already handled by sys.stdout.reconfigure at module top. Delta inline formatting initially misaligned due to `−` vs `-` character in existing source.
- Graders run and results (PASS/FAIL): smoke test PASS (3 CLI invocations), Dashboard.md injection PASS
- Prompt variant (if applicable): none
- Next experiment: wire revenue_collected from weekly_scheduler.py financial snapshot to get non-zero revenue_collection_delta
