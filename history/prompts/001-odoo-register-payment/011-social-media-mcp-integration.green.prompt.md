---
id: "011"
title: Social Media MCP Integration
stage: green
date: 2026-02-22
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 002-close-invoice-batch-tools
user: HP
command: implement Social Media MCP Integration for Gold Tier compliance — 7 steps
labels: ["social_mcp","facebook","instagram","x","content_generator","hitl","dry_run","circuit_breaker","execution_metrics","ceo-briefing"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - marketing/__init__.py
  - marketing/social_mcp.py
  - marketing/social_content.py
  - marketing/test_social_mcp.py
  - execution_metrics.py
  - .claude/skills/ceo-briefing/SKILL.md
tests:
  - "python marketing/test_social_mcp.py — 56/56 PASS"
  - "Draft structure: all required keys, correct types"
  - "Platform tone: Facebook (informative, no emoji), Instagram (concise+emoji), X (<=280 chars)"
  - "X long-topic truncation: stays <= 280 chars with '...' marker"
  - "DRY_RUN: no files created, returns None"
  - "Metrics: 3 social keys, zero defaults, correct counter increment from log events"
  - "CEO section: renders with activity and no-activity cases"
  - "Circuit breaker: circuit_blocked status when open, draft_created when closed"
---

## Prompt

You are extending a production-ready autonomous AI employee system.
[Full 7-step Social Media MCP Integration spec as provided by user]

Steps:
1. Social MCP abstraction (SocialPlatform enum, create_social_draft)
2. Social content generator (generate_social_post, 3 platform tones)
3. Draft file creation to /Pending_Approval/ (HITL, DRY_RUN safe)
4. Execution metrics extension (3 additive social fields)
5. CEO briefing extension (## Marketing Activity section + step 6b)
6. run_social_cycle() integration hook
7. Unit tests (56 assertions across 7 test groups)

## Response snapshot

**New package `marketing/`**:
- `__init__.py` — empty package marker
- `social_mcp.py` (~200 lines):
  - `SocialPlatform(Enum)` — FACEBOOK / INSTAGRAM / X
  - `_circuit_is_open()` — read-only CB check via orchestrator._cb_check(dry_run=True)
  - `create_social_draft(platform, content, dry_run)` — stub MCP call, returns {platform, draft_id, status, timestamp, content_length}; guards: circuit_blocked, dry_run_skipped, draft_created
  - `create_post_approval_file(platform, content, topic, draft_result, dry_run)` — writes POST_{PLATFORM}_{timestamp}.md to /Pending_Approval/; dry_run safe
  - `run_social_cycle(topic, platforms, dry_run)` — full pipeline per platform; returns summary dict
  - `_social_log(event, payload)` — appends to today's vault log (source=social_mcp)

- `social_content.py` (~75 lines):
  - `generate_social_post(topic, platform)` — dispatcher
  - `_generate_facebook(topic)` — informative 2-3 sentences, no emoji
  - `_generate_instagram(topic)` — concise, single tasteful emoji
  - `_generate_x(topic)` — short, hard-capped at 280 chars with truncation + "..."

**Modified `execution_metrics.py`**:
- Added 3 counters: `social_drafts_created`, `social_posts_approved`, `social_posts_rejected`
- Track `source == "social_mcp"` events: `social_draft_created`, `social_post_approved`, `social_post_rejected`
- All 3 added to metrics return dict (zero defaults, additive)

**Modified `.claude/skills/ceo-briefing/SKILL.md`**:
- Step 6b added: load social metrics from execution_metrics JSON, detect active platforms from Pending_Approval/ filenames
- Briefing template: `## Marketing Activity` section with drafts/approved/rejected counts, platforms, momentum arrow
- No-activity case: "No social activity this period."

## Outcome

- 56/56 unit tests PASS
- DRY_RUN confirmed: no files created in dry_run=True, returns None
- Circuit breaker: mocked open → circuit_blocked; closed → draft_created
- No schema breaks: all existing metrics keys intact, CB/RPE/orchestrator untouched
- NO auto-posting, NO API keys, NO external network calls

## Evaluation notes (flywheel)

- Failure modes observed: None
- Graders: 56/56 PASS
- Next: add real social platform SDK calls to create_social_draft() stubs when API keys available; the structure is already production-ready
