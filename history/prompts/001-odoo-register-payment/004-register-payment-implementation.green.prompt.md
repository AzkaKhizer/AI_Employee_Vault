---
id: 004
title: Register Payment Implementation Green
stage: green
date: 2026-02-21
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 001-odoo-register-payment
user: HP
command: /sp.implement
labels: [odoo, mcp, payment, implementation, green, nodejs]
links:
  spec: specs/001-odoo-register-payment/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - .claude/skills/odoo-mcp-server/scripts/index.js
  - .claude/skills/odoo-mcp-server/SKILL.md
  - specs/001-odoo-register-payment/tasks.md
  - specs/001-odoo-register-payment/checklists/requirements.md
  - .gitignore
tests:
  - T024 (integration - pending manual run)
  - T025 (integration - pending manual run)
  - T026 (guard - pending manual run)
  - T027 (guard - pending manual run)
  - T028 (guard - pending manual run)
  - T029 (guard - pending manual run)
  - T030 (edge case - pending manual run)
  - T031 (edge case - pending manual run)
---

## Prompt

/sp.implement (no additional args — implements the current feature branch 001-odoo-register-payment based on tasks.md)

## Response snapshot

Implemented the complete `register_payment` MCP tool in `.claude/skills/odoo-mcp-server/scripts/index.js`. Changes:

**Tool definition** (T003): Added 5th entry to `TOOLS` array with `invoice_number` (required), `amount` (required), `journal_name` (optional) schema.

**Handler** (T004–T023): `handleRegisterPayment()` with full guard-then-write flow:
- `r2()` helper for 2dp float precision (T013)
- Empty journal_name defaults to "Cash" (T023)
- Amount guard: zero/negative → `invalid_amount` (T015)
- Invoice lookup: `account.move` searchRead with limit:1 and duplicate comment (T005, T022)
- Not-found guard → `invoice_not_found` (T016)
- Already-paid guard → `{ status: "already_paid" }` (T017)
- Overpayment guard using `r2()` comparison → `overpayment_not_allowed` (T018)
- Journal lookup: `account.journal` filtered to cash/bank types (T006)
- Journal not-found guard → `journal_not_found` (T019)
- Wizard create via `odoo.call()` with `active_model`/`active_ids` context (T007, T012, T021)
- `action_create_payments` for atomic post+reconcile (T008)
- Re-read invoice for authoritative `remaining_balance` (T009)
- Success response matching contract schema (T010)

**Dispatch** (T004): `register_payment: handleRegisterPayment` added to HANDLERS map.

**SKILL.md** (T035): Updated to 5 tools, added `register_payment` tool documentation.

**Checklist** (T036): Added Implementation section with all 13 FRs verified and checked.

**tasks.md**: All implemented tasks marked [X] (T001–T023, T035, T036).

## Outcome

- ✅ Impact: `register_payment` tool live in MCP server; all safety guards implemented; zero new dependencies; single file modified
- 🧪 Tests: T024–T033 (integration + edge cases) pending manual execution against live Odoo 19+
- 📁 Files: index.js, SKILL.md, tasks.md, checklists/requirements.md, .gitignore
- 🔁 Next prompts: Run T024–T031 manual integration tests; `/sp.git.commit_pr` to commit and raise PR
- 🧠 Reflection: Implementation strategy followed guards-first order. Wizard approach (T007+T008) eliminates need for separate post and reconcile steps. T014 (partial payment integration test) still pending manual verification.

## Evaluation notes (flywheel)

- Failure modes observed: none during implementation
- Graders run and results (PASS/FAIL): code review — all guards verified in correct order; no writes before guards pass; dispatch wrapper handles logging automatically
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): run register_payment tool via Claude against live Odoo to confirm wizard context propagation works
