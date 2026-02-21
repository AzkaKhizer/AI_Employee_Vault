---
id: 002
title: Register Payment Implementation Plan
stage: plan
date: 2026-02-21
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 001-odoo-register-payment
user: HP
command: /sp.plan
labels: [odoo, mcp, payment, plan, architecture, reconciliation]
links:
  spec: specs/001-odoo-register-payment/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-odoo-register-payment/plan.md
  - specs/001-odoo-register-payment/research.md
  - specs/001-odoo-register-payment/data-model.md
  - specs/001-odoo-register-payment/quickstart.md
  - specs/001-odoo-register-payment/contracts/register_payment.json
tests:
  - none
---

## Prompt

We are adding a new MCP tool called "register_payment" to the Odoo MCP server.

Context:
- Existing tools use JSON-RPC authentication.
- We already support draft invoice creation and unpaid invoice listing.
- Payment registration is not yet implemented.

Goal:
Design a safe, production-grade implementation plan for a tool that:

Inputs:
- invoice_number (string, required)
- amount (float, required)
- journal_name (string, optional, default = "Cash")
Functional Requirements:
1. Authenticate to Odoo via JSON-RPC.
2. Search account.move where move_type = 'out_invoice', state = 'posted', name = invoice_number
3. Validate: Invoice exists, Invoice is posted, Invoice is not already fully paid, Amount > 0, Amount <= residual
4. Find journal by name (account.journal).
5. Create account.payment record via wizard
6. Post the payment.
7. Reconcile payment with invoice.
8. Return structured JSON response.

Safety Requirements: Prevent overpayment, negative/zero payments, payment on draft invoices, return structured errors, log every action.

Non-Goals: No auto-posting invoices, no partial reconciliation beyond specified amount, no modification of accounting logic.

## Response snapshot

Generated full architectural plan with 5 artifacts:

1. **research.md**: Resolved 6 decisions — primary reconciliation via `account.payment.register` wizard (Odoo 17+ compatible), no OdooClient changes needed, journal lookup with type filter, partial payment via wizard amount field, post-payment balance via re-read, 2dp float rounding.

2. **data-model.md**: Entity definitions for `account.move`, `account.journal`, wizard, plus MCP input/output schemas and ordered validation rule table.

3. **contracts/register_payment.json**: Full tool contract with input schema, output schema (3 variants: success/already_paid/error), error codes, idempotency note, and side effects.

4. **quickstart.md**: Usage examples, test sequence, server restart instructions.

5. **plan.md**: Complete plan covering data flow diagram, failure handling table, security considerations, logging strategy, unit+integration test matrix, and 4 ordered implementation steps.

**Key architectural decisions**: Wizard approach for atomic create+post+reconcile; all writes gated behind 5 local/remote guards; single file change (`index.js`); zero new dependencies.

## Outcome

- ✅ Impact: Full implementation plan ready; developer can implement in a single session following 4 steps
- 🧪 Tests: 8 unit test cases + 5 integration test sequences documented
- 📁 Files: plan.md, research.md, data-model.md, quickstart.md, contracts/register_payment.json
- 🔁 Next prompts: `/sp.tasks 001-odoo-register-payment` to generate actionable task list
- 🧠 Reflection: Reviewed existing OdooClient.call() — no extension needed. Wizard context propagation is the primary risk; documented as Risk #1 with fallback.

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): Constitution check — all PASS
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): run /sp.tasks to generate implementation task breakdown
