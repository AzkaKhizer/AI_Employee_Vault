---
id: 003
title: Register Payment Task Breakdown
stage: tasks
date: 2026-02-21
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 001-odoo-register-payment
user: HP
command: /sp.tasks
labels: [odoo, mcp, payment, tasks, implementation, testing]
links:
  spec: specs/001-odoo-register-payment/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-odoo-register-payment/tasks.md
tests:
  - none
---

## Prompt

Based on the approved implementation plan for the "register_payment" MCP tool, generate a structured task breakdown.

Requirements:

1. Tasks must be grouped into: Core Implementation, Validation & Safety, Error Handling, Logging, Testing, Documentation
2. Each task must be atomic, executable, and include acceptance criteria
3. Include: JSON-RPC request builder changes, account.move lookup logic, account.journal lookup logic, account.payment creation logic, payment posting, reconciliation logic, residual recalculation, structured response formatter, structured error formatter, Vault logging hook
4. Include edge case tasks: Invoice not found, Invoice already paid, Journal not found, Overpayment attempt, Network failure, Odoo restart during call
5. Include testing - Invalid invoice number, Concurrency test (two payments at once)
6. Output tasks in numbered checklist format ready for execution.

## Response snapshot

Generated `specs/001-odoo-register-payment/tasks.md` with 36 tasks across 7 phases:

- **Phase 1** (Setup): T001–T002 — environment verification
- **Phase 2** (Foundational): T003–T004 — tool definition + dispatch wiring (stub)
- **Phase 3** (US1 - Core + Logging): T005–T011 — invoice lookup, journal lookup, wizard create, action_create_payments, re-read residual, response builder, logging verification
- **Phase 4** (US2 - Partial Payment): T012–T014 — rounding helper, partial amount handling, integration test
- **Phase 5** (US3 - Validation + Error Handling): T015–T023 — 5 safety guards + network failure, Odoo restart, duplicate invoice, empty journal name
- **Phase 6** (Testing): T024–T033 — 6 integration tests + 4 edge case tests (network, invalid amount, concurrency, log completeness)
- **Phase 7** (Documentation): T034–T036 — quickstart update, SKILL.md update, FR checklist completion

MVP scope: T001–T011 + T015–T019 (17 tasks — safe full payment with all guards).
8 parallel opportunities identified across phases.

## Outcome

- ✅ Impact: 36 atomic, acceptance-criteria-bearing tasks ready for execution via /sp.implement
- 🧪 Tests: 10 test tasks in Phase 6 covering integration, edge cases, and concurrency simulation
- 📁 Files: specs/001-odoo-register-payment/tasks.md
- 🔁 Next prompts: `/sp.implement` to execute tasks; `/sp.adr odoo-payment-reconciliation-strategy` to document architecture decision
- 🧠 Reflection: US3 guards placed in Phase 5 with note to implement before US1 integration tests for safety; concurrency test is a sequential approximation since the vault is single-user (no true parallelism).

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): format validation — all 36 tasks follow checklist format with ID, optional [P], optional [Story], description with file path
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): run /sp.implement starting from T001
