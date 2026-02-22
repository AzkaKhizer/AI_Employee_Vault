---
id: "008"
title: Failure Intelligence and Auto-Recovery Layer
stage: green
date: 2026-02-22
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 002-close-invoice-batch-tools
user: HP
command: implement Failure Intelligence & Auto-Recovery Layer (5 phases)
labels: ["recovery_engine","failure_classification","auto_retry","orchestrator","execution_metrics","ceo-briefing"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - recovery_engine.py
  - execution_metrics.py
  - orchestrator.py
  - .claude/skills/ceo-briefing/SKILL.md
  - simulate_recovery_test.py
tests:
  - "python simulate_recovery_test.py — all 7 validation checks PASS"
  - "python execution_metrics.py --days 7 --save-failure-analysis — live vault PASS"
  - "python -c 'import recovery_engine' — import PASS"
  - "python -c 'import orchestrator; print(orchestrator._RECOVERY_AVAILABLE)' — True"
---

## Prompt

You are extending AI_Employee_Vault.

Goal: Reduce execution failure rate and convert recoverable failures into auto-retries or guided corrections.

PHASE 1 — FAILURE CLASSIFICATION
  Extend execution_metrics.py to classify failures by type: guard_rejection, tool_validation_error, network_error, timeout, unknown. Add failure_breakdown = {type: count}. Log to /Logs/failure-analysis-YYYY-MM-DD.json. Show in CLI, Dashboard, CEO briefing.

PHASE 2 — RECOVERABLE FAILURE DETECTION
  Define recoverable failures (invalid journal, invoice not found, network, timeout, rate limit). Add is_recoverable(error) -> bool.

PHASE 3 — AUTO-RETRY ENGINE
  In orchestrator.py: retry up to 2 times, exponential backoff, log retry_attempts. On exhaustion: create RECOVERY_REQUIRED_<task>.md in Needs_Action/.

PHASE 4 — SMART CORRECTION SUGGESTIONS
  If invalid journal: query Odoo for valid journals, suggest closest via string similarity. If invoice_not_found: suggest top 3 closest invoice numbers. Add to recovery file and CEO briefing risk section.

PHASE 5 — AUTONOMY SCORE BOOST
  Auto-recovered failures count as SUCCESS. Add auto_recovery_rate.

VALIDATION: simulate 3 recoverable failures, show retry log, updated score, breakdown. Confirm DRY_RUN and no secret leakage.

## Response snapshot

**New file — `recovery_engine.py`** (220 lines):
- `classify_error(msg, event)` — regex-based classifier: guard_rejection / tool_validation_error / network_error / timeout / unknown
- `is_recoverable(error_type, msg)` — RECOVERABLE_TYPES = {network_error, timeout, tool_validation_error}
- `suggest_journal(attempted_name)` — Odoo JSON-RPC query account.journal, difflib.get_close_matches
- `suggest_invoice(attempted_number)` — Odoo JSON-RPC query account.move, difflib.get_close_matches
- `get_correction_suggestions(error_type, msg)` — dispatch by type, return {suggestions, action}
- `_odoo_creds()` reads from os.getenv only — no hardcoding, no logging of values

**Modified `execution_metrics.py`**:
- Import classify_error from recovery_engine (graceful fallback)
- `failure_breakdown` dict initialized in compute_metrics, populated per failure event
- Orchestrator error condition split from combined into individual branches for per-type classification
- `auto_recovered` counter — incremented on `retry_success` events
- Scoring adjusted: net_failures = execution_failures − auto_recovered; net_auto_completed includes recovered
- `auto_recovery_rate` added to metrics dict
- `save_failure_analysis(metrics)` → /Logs/failure-analysis-YYYY-MM-DD.json
- `_failure_breakdown_md(fb)` → inline markdown sub-table for Dashboard
- `print_summary()` shows Failure Breakdown block when failures > 0
- `update_dashboard()` adds Auto-Recovered row + failure breakdown sub-table
- `--save-failure-analysis` CLI flag

**Modified `orchestrator.py`**:
- MAX_RETRIES=2, RETRY_BACKOFF=30 (env-overridable)
- `_create_recovery_file()` → RECOVERY_REQUIRED_<task>.md in Needs_Action/
- `run_task_with_retry()` — wraps invoke_claude with retry loop, exponential backoff, correction suggestions
- `process_cycle()` uses run_task_with_retry instead of invoke_claude
- New log events: retry_attempt, retry_success, recovery_exhausted, failure_not_recoverable, recovery_file_created, task_retry_summary

**Modified `ceo-briefing/SKILL.md`**:
- Step 5b: load failure-analysis JSON, extract breakdown
- Autonomy Intelligence table: added Auto-Recovered Failures row
- Risk Signals template: added Failure Type Breakdown sub-table with Recoverable? column

**New file — `simulate_recovery_test.py`** (validation):
- 17 simulated log entries covering 3 recoverable failures + 2 auto-recoveries + 1 exhausted
- 7 validation checks: classify+recover, retry log, autonomy score, DRY_RUN, secret leakage, failure-analysis file

## Outcome

- ✅ Impact: 6/6 failure types classified correctly in simulation. Auto-recovery detection working. Score boost active when retries succeed. Recovery file writer writes RECOVERY_REQUIRED_*.md to Needs_Action/. No secrets exposed.
- 🧪 Tests: simulate_recovery_test.py — ALL 7 CHECKS PASS. Live vault: failure_breakdown populated (2 network_error + 4 unknown in existing logs).
- 📁 Files: 2 new files, 3 modified files
- 🔁 Next prompts: commit and push; run orchestrator --dev --once to see retry engine in action
- 🧠 Reflection: auto_recovery_rate will be 0% until orchestrator.py is actually run against live tasks that fail and recover — the scoring infrastructure is in place.

## Evaluation notes (flywheel)

- Failure modes observed: Combined elif branch in compute_metrics prevented per-type classification — split into individual branches. Windows console UTF-8 already handled.
- Graders run and results (PASS/FAIL): simulate_recovery_test.py PASS (all 7 checks), live vault PASS
- Prompt variant (if applicable): none
- Next experiment: run orchestrator --dev --once with a task that triggers a recoverable failure to see retry_success events in real logs
