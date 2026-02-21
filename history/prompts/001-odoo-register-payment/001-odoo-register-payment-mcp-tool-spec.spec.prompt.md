---
id: 001
title: Odoo Register Payment MCP Tool Spec
stage: spec
date: 2026-02-21
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 001-odoo-register-payment
user: HP
command: /sp.specify
labels: [odoo, mcp, payment, spec, financial]
links:
  spec: specs/001-odoo-register-payment/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-odoo-register-payment/spec.md
  - specs/001-odoo-register-payment/checklists/requirements.md
tests:
  - none
---

## Prompt

Add a new MCP tool named "register_payment" to the Odoo MCP server.

Requirements:

1. Tool name: register_payment
2. Inputs:
   - invoice_number (string, required)
   - amount (float, required)
   - journal_name (string, optional, default: "Cash")
3. Behavior:
   - Authenticate with Odoo via JSON-RPC
   - Search account.move where:
       move_type = 'out_invoice'
       state = 'posted'
       name = invoice_number
   - If invoice not found → return structured error
   - If invoice already paid → return structured response
   - Create account.payment record
   - Post payment
   - Reconcile with invoice
   - Return:
       {
         status: "payment_registered",
         invoice: invoice_number,
         amount_paid: amount,
         remaining_balance: residual
       }

4. Safety:
   - Do not allow overpayment
   - Do not allow negative values
   - Require invoice to be posted
   - Log action to Vault logs

5. Must follow existing MCP tool pattern and error handling format.

## Response snapshot

Created feature branch `001-odoo-register-payment` and wrote complete specification at `specs/001-odoo-register-payment/spec.md`. The spec covers:

- 3 prioritized user stories (full payment P1, partial payment P2, input rejection P3)
- 13 functional requirements (FR-001 through FR-013)
- 3 key entities (Invoice, Payment, Journal)
- 5 measurable success criteria
- Edge cases and explicit assumptions about Odoo version, OdooClient reuse, currency, and scope

Spec quality checklist created at `specs/001-odoo-register-payment/checklists/requirements.md` — all items pass.

## Outcome

- ✅ Impact: Feature spec is complete and ready for `/sp.plan`
- 🧪 Tests: No tests in spec phase; acceptance scenarios defined in user stories
- 📁 Files: specs/001-odoo-register-payment/spec.md, specs/001-odoo-register-payment/checklists/requirements.md
- 🔁 Next prompts: `/sp.clarify` (optional) or `/sp.plan 001-odoo-register-payment`
- 🧠 Reflection: Existing `index.js` pattern (TOOLS array + HANDLERS map + logToVault) was reviewed to ensure spec aligns with implementation constraints without leaking tech details into requirements.

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): spec quality checklist — all PASS
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): run /sp.plan to generate architecture decisions
