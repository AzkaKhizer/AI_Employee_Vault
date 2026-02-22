---
id: "010"
title: Revenue Protection Engine
stage: green
date: 2026-02-22
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 002-close-invoice-batch-tools
user: HP
command: implement Revenue Protection Engine (RPE) — 8 steps
labels: ["revenue_protection","aging_buckets","risk_scoring","reminder_drafts","hitl","ceo-briefing","autonomy_metrics","dry_run"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - finance/__init__.py
  - finance/revenue_protection.py
  - finance/test_revenue_protection.py
  - execution_metrics.py
  - .claude/skills/ceo-briefing/SKILL.md
tests:
  - "python finance/test_revenue_protection.py — 67/67 PASS"
  - "execution_metrics RPE defaults: OK (6 new keys, all zero)"
  - "RPE pipeline dry_run=True — no files created, revenue_at_risk correct"
  - "extract_metrics_extension — all 6 RPE keys present"
  - "format_ceo_briefing_section — Revenue Protection Overview section correct"
  - "ALL SMOKE TESTS PASS — no breaking changes"
---

## Prompt

You are extending a production-ready autonomous AI operations engine.
[Full 8-step Revenue Protection Engine spec as provided by user]

Steps:
1. Aging buckets: categorize_aging_bucket(), build_aging_summary()
2. Revenue at risk: calculate_revenue_at_risk()
3. Risk scoring: calculate_risk_score() with aging + amount points
4. Reminder draft generator: generate_reminder_draft() with 4 tones
5. Draft file creation: create_reminder_approval_file() to /Pending_Approval/ — HITL only, DRY_RUN safe
6. CEO briefing extension: ## Revenue Protection Overview section
7. Autonomy metrics extension: 6 additive RPE fields in metrics JSON
8. Unit tests: aging, risk scoring, revenue at risk, tone selection, DRY_RUN gate

Constraints: no auto-email, no auto-payment, HITL required, DRY_RUN fully supported, no refactors, no breaking changes.

## Response snapshot

**New package `finance/`**:
- `__init__.py` — empty package marker
- `revenue_protection.py` (~280 lines):
  - `categorize_aging_bucket(days_overdue)` — 0-30/31-60/61-90/91+
  - `build_aging_summary(invoices)` — {bucket: {count, amount}} for all 4 buckets
  - `calculate_revenue_at_risk(invoices)` — sum of amount for days_overdue >= 31
  - `calculate_risk_score(invoice)` — aging_pts (0-4) + amount_pts (0-3) → {score, risk_level}
  - `generate_reminder_draft(invoice, risk_level)` — 4 distinct tones per level
  - `create_reminder_approval_file(invoice, risk_score, dry_run)` — HITL gate: only high/critical, 7-day cooldown, DRY_RUN aware
  - `run_revenue_protection(invoices, dry_run)` — full pipeline, structured report dict
  - `format_ceo_briefing_section(report)` — ## Revenue Protection Overview markdown
  - `extract_metrics_extension(report)` — 6 RPE keys for autonomy metrics merge
  - `_days_overdue()`, `_amount()`, `_rpe_log()` — internal helpers

- `test_revenue_protection.py` — 6 test groups, 67 assertions, all PASS

**Modified `execution_metrics.py`**:
- Added 6 RPE keys to `compute_metrics()` return dict with zero defaults:
  `revenue_at_risk`, `high_risk_invoice_count`, `critical_invoice_count`,
  `aging_warning_amount`, `aging_high_amount`, `aging_critical_amount`

**Modified `.claude/skills/ceo-briefing/SKILL.md`**:
- Step 6a added: Revenue Protection Analysis — call `run_revenue_protection()` or read latest RPE JSON; read-only in briefing context
- Briefing template: `## Revenue Protection Overview` section with aging breakdown, risk counts, reminder drafts pending

## Outcome

- 67/67 unit tests PASS
- DRY_RUN confirmed: no files created in dry_run=True mode, correct logging
- No breaking changes: all existing metrics keys intact, existing orchestrator/CB untouched
- HITL enforced: only high/critical invoices get reminder files; all go to /Pending_Approval/ for human review
- No auto-email, no auto-payment, no financial writes

## Evaluation notes (flywheel)

- Failure modes observed: Windows cp1252 encoding — replaced Unicode arrow in test labels with ASCII "->"
- Graders: 67/67 PASS, smoke tests PASS
- Next: run `python finance/test_revenue_protection.py` after merge to confirm clean on main
