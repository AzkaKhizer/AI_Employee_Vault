---
id: "012"
title: Executive Priority Engine
stage: green
date: 2026-02-22
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 002-close-invoice-batch-tools
user: HP
command: implement Executive Priority Engine (EPE) -- 7 steps
labels: ["priority_engine","scoring","ranking","executive_alerts","ceo-briefing","execution_metrics","deterministic","additive"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - executive/__init__.py
  - executive/priority_engine.py
  - executive/test_priority_engine.py
  - execution_metrics.py
  - .claude/skills/ceo-briefing/SKILL.md
tests:
  - "python executive/test_priority_engine.py -- 91/91 PASS"
  - "EPE fields in execution_metrics: OK (2 new keys, zero defaults)"
  - "Prior schema intact: all existing keys present"
  - "EPE pipeline smoke test: total=3 critical=1 high=0"
  - "CEO section renders correctly"
  - "ALL SMOKE TESTS PASS -- no breaking changes"
---

## Prompt

You are extending a production-ready AI Executive Assistant system.
[Full 7-step Executive Priority Engine spec as provided by user]

Scoring model v1 (deterministic):
- Finance: risk_level (0-4pt) + amount tiers (0-3pt) + days_overdue (0-2pt)
- Marketing: x+revenue/launch (+2), drafts=0 (+1), pending>3d (+1)
- Approval: pending>3d (+3), pending>1d (+2)
- Global: ceo_attention_required (+2), failure_count>0 (+1)
- Level: 0-2=low, 3-5=medium, 6-8=high, >=9=critical

Steps 1-4: executive/priority_engine.py (TaskPriority, calculate_priority, rank_tasks, extract_executive_alerts, run_priority_cycle, format_ceo_priority_section)
Step 5: execution_metrics.py -- 2 additive EPE fields
Step 6: ceo-briefing/SKILL.md -- step 6c + ## Executive Priority Overview template
Step 7: executive/test_priority_engine.py -- 91 assertions across 9 test groups

## Response snapshot

**New package `executive/`**:
- `__init__.py` -- empty package marker
- `priority_engine.py` (~230 lines):
  - `TaskPriority` dataclass: task_id, domain, metadata, priority_score, priority_level, created_at
  - `_score_to_level(score)` -- 0-2/3-5/6-8/>=9 boundary mapping
  - `calculate_priority(task)` -- dispatcher to domain scorers + global rules; mutates task in-place, returns same object
  - `_score_finance(meta)` -- risk (4pt max) + amount steps + overdue steps
  - `_score_marketing(meta)` -- x+revenue/launch, drafts gap, staleness
  - `_score_approval(meta)` -- age-based mutually exclusive tiers
  - `_score_global(meta)` -- ceo flag + failure history
  - `rank_tasks(tasks)` -- sorted() by (score DESC, created_at DESC); returns NEW list
  - `extract_executive_alerts(tasks)` -- critical_count, high_count, top_tasks (max 5)
  - `run_priority_cycle(raw_tasks)` -- full pipeline: build TaskPriority objects (never mutates input), score, rank, extract, return summary dict
  - `format_ceo_priority_section(summary)` -- ## Executive Priority Overview markdown

**Modified `execution_metrics.py`**:
- Added 2 EPE keys: `critical_priority_tasks`, `high_priority_tasks` (zero defaults, additive)

**Modified `.claude/skills/ceo-briefing/SKILL.md`**:
- Step 6c added: run_priority_cycle from finance/marketing/approval task lists; merge EPE keys into metrics
- Briefing template: `## Executive Priority Overview` section with critical/high counts, top 5 list, no-task fallback

## Outcome

- 91/91 unit tests PASS (9 test groups incl. bonus CEO section + global rules)
- All scoring rules verified with boundary cases
- rank_tasks confirmed non-mutating (returns new list)
- run_priority_cycle confirmed non-mutating (deep-copies metadata)
- Zero breaking changes: all prior schema keys intact
- Pure computation: no file writes, no network calls, no randomness

## Evaluation notes (flywheel)

- Failure modes: None -- all 91 assertions green on first pass
- Tie-breaker design: ISO8601 lexicographic sort is correct for UTC timestamps
- amount_residual fallback key tested (Odoo uses this field name)
- Next: wire run_priority_cycle into weekly_scheduler.py to auto-merge EPE fields into metrics JSON
