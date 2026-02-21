---
id: 006
title: Close Invoice Batch Tools Bug Fix
stage: green
date: 2026-02-21
surface: agent
model: claude-sonnet-4-6
feature: 001-odoo-register-payment
branch: 002-close-invoice-batch-tools
user: HP
command: auto_close_all_unpaid (integration test + bug fix)
labels: [odoo, mcp, close_invoice, auto_close_all_unpaid, bugfix, batch, nodejs]
links:
  spec: specs/001-odoo-register-payment/spec.md
  ticket: null
  adr: null
  pr: https://github.com/AzkaKhizer/AI_Employee_Vault/pull/new/002-close-invoice-batch-tools
files:
  - .claude/skills/odoo-mcp-server/scripts/index.js
  - .claude/skills/odoo-mcp-server/SKILL.md
tests:
  - auto_close_all_unpaid with 4 draft invoices — batch_complete, 4 closed, 4501 PKR reconciled, 0 failures
---

## Prompt

(Continued from previous session) Create 3 draft invoices for batch testing `auto_close_all_unpaid`, then test the batch tool end-to-end.

## Response

### Actions taken

1. **Batch invoice creation (fixed)**: Previous session failed with "No partner found" for Batch Test A/B/C. Retried using existing partner "Test Customer" — all 3 draft invoices created successfully (IDs 6, 7, 8; 1500 + 2200 + 800 PKR).

2. **Bug 1 — `auto_close_all_unpaid` blind to drafts**: The original implementation called `handleListUnpaidInvoices()` which only queries `state = "posted"`. Draft invoices (IDs 6-8) were invisible. Fixed by replacing with a direct Odoo query: `state in [draft, posted]` + `payment_state != paid`. Made `journal_name` optional (default Cash).

3. **Bug 2 — draft invoice name stays `/` after posting**: When `handleAutoCloseAllUnpaid` passed `inv.name = "/"` to `handleCloseInvoice`, it auto-posted the invoice (changing name from `"/"` to real `INV/2026/XXXXX`), but then called `handleRegisterPayment({ invoice_number: "/" })` — which failed to find the now-renamed invoice. Fixed by adding `"name"` to the post-refresh `searchRead` fields in `handleCloseInvoice` and reassigning `invoice_number = invoice.name` after posting.

4. **Integration test — final run**: `auto_close_all_unpaid("Cash")` → `{ status: "batch_complete", total_processed: 4, total_closed: 4, total_amount_reconciled: 4501, failures: [] }` ✅

5. **SKILL.md updated**: Description changed from "five tools" to "seven tools"; added `close_invoice` and `auto_close_all_unpaid` documentation sections.

6. **Committed and pushed**: Branch `002-close-invoice-batch-tools`, commit `7af4c02`.

### Key architectural note

Draft invoices in Odoo have `name = "/"` until `action_post` is called, at which point Odoo assigns the real sequential name. Any code that stores the name before posting and reuses it after must re-read the name field post-post.
