# Quickstart: register_payment MCP Tool

**Feature**: `001-odoo-register-payment`
**Date**: 2026-02-21

---

## What This Does

Adds a `register_payment` tool to the existing Odoo MCP server (`index.js`). When called, it:

1. Validates inputs locally (amount > 0)
2. Looks up the invoice in Odoo
3. Validates invoice state and overpayment guard
4. Resolves the journal by name
5. Creates and posts a payment via Odoo's `account.payment.register` wizard
6. Returns the remaining balance after reconciliation

**No changes to `odoo-client.js` or `logger.js`.**

---

## File to Modify

```
.claude/skills/odoo-mcp-server/scripts/index.js
```

Only `index.js` changes. Add:
- 1 entry to `TOOLS` array
- 1 handler function `handleRegisterPayment`
- 1 entry to `HANDLERS` dispatch map

---

## Tool Call Examples

### Full payment
```json
{
  "tool": "register_payment",
  "arguments": {
    "invoice_number": "INV/2026/00001",
    "amount": 5000,
    "journal_name": "Cash"
  }
}
```

Expected response:
```json
{
  "status": "payment_registered",
  "invoice": "INV/2026/00001",
  "amount_paid": 5000,
  "remaining_balance": 0
}
```

### Partial payment
```json
{
  "tool": "register_payment",
  "arguments": {
    "invoice_number": "INV/2026/00001",
    "amount": 2000
  }
}
```

Expected response:
```json
{
  "status": "payment_registered",
  "invoice": "INV/2026/00001",
  "amount_paid": 2000,
  "remaining_balance": 3000
}
```

### Error — overpayment
```json
{
  "tool": "register_payment",
  "arguments": {
    "invoice_number": "INV/2026/00001",
    "amount": 9999
  }
}
```

Expected response:
```json
{
  "error": "overpayment_not_allowed",
  "detail": "Amount 9999 exceeds outstanding balance 5000 for INV/2026/00001",
  "invoice_number": "INV/2026/00001"
}
```

---

## Recommended Test Sequence

1. Call `list_unpaid_invoices` → confirm a posted invoice exists
2. Call `register_payment` with `amount` equal to outstanding balance → verify `remaining_balance: 0`
3. Call `list_unpaid_invoices` again → invoice should no longer appear
4. Call `register_payment` again on the same invoice → verify `status: "already_paid"` response

---

## Restart MCP Server After Changes

Claude Code automatically restarts MCP servers when source files change. If testing manually, restart via:

```bash
node .claude/skills/odoo-mcp-server/scripts/index.js
```

Ensure `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`, `VAULT_ROOT` env vars are set (see `.mcp.json`).
