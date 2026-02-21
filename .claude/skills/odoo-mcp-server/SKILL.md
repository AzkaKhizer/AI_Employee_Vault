---
name: odoo-mcp-server
description: Node.js MCP server connecting to Odoo 19+ via JSON-RPC. Exposes seven tools — create_draft_invoice, list_unpaid_invoices, get_revenue_summary, list_expenses, register_payment, close_invoice, auto_close_all_unpaid. All monetary writes create DRAFT only (never auto-post) except register_payment/close_invoice/auto_close_all_unpaid which create, post, and reconcile payments. Logs all calls to Vault/Logs/YYYY-MM-DD.json. Use when the user says "create invoice", "list unpaid invoices", "revenue summary", "list expenses", "register payment", "pay invoice", "close invoice", "close all invoices", "batch pay", "connect to Odoo", "Odoo invoices", "check outstanding invoices", or needs financial data from an Odoo 19+ instance. Requires ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD environment variables.
---

# Odoo MCP Server

## Setup

### 1. Install dependencies

```bash
cd <vault-root>/.claude/skills/odoo-mcp-server/scripts
npm install
```

### 2. Configure environment

Set these environment variables before starting the server:

```
ODOO_URL=https://your-odoo-instance.com
ODOO_DB=your-database-name
ODOO_USERNAME=your-username
ODOO_PASSWORD=your-password
```

Optionally set `VAULT_ROOT` to the vault directory. If unset, the logger walks up from cwd looking for `Dashboard.md`.

### 3. Register as MCP server

Add to Claude Desktop `claude_desktop_config.json` or Claude Code MCP settings:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "node",
      "args": ["<vault-root>/.claude/skills/odoo-mcp-server/scripts/index.js"],
      "env": {
        "ODOO_URL": "https://your-odoo-instance.com",
        "ODOO_DB": "your-db",
        "ODOO_USERNAME": "user",
        "ODOO_PASSWORD": "pass"
      }
    }
  }
}
```

## Tools

### create_draft_invoice(customer_name, amount, description)

Create a **DRAFT** customer invoice. Looks up partner by name, creates `account.move` with `move_type: "out_invoice"`. Invoice stays in `draft` state — **never auto-posts**.

- `customer_name`: must match an existing `res.partner` name
- `amount`: positive number (single invoice line)
- `description`: line item label

**HITL required**: posting the invoice is a separate manual action in Odoo.

### list_unpaid_invoices()

Query all posted customer invoices with `payment_state` in `[not_paid, partial]`. Returns id, number, customer, total, outstanding, date, due_date. Read-only.

### get_revenue_summary(start_date, end_date)

Aggregate posted customer invoices in the date range. Returns invoice_count, total_revenue, total_collected, total_outstanding. Read-only.

### list_expenses(start_date, end_date)

Query vendor bills (`in_invoice`) in the date range. Returns count, total, and per-expense details. Read-only.

### register_payment(invoice_number, amount, journal_name?)

Register a payment against a posted customer invoice (creates, posts, and reconciles). Locates the invoice by number, validates all safety guards (positive amount, no overpayment, invoice posted, not already paid, journal exists), then creates and reconciles payment via the `account.payment.register` wizard in a single atomic action.

- `invoice_number`: must match a posted `out_invoice` (e.g. `INV/2026/00001`)
- `amount`: positive number, must not exceed outstanding balance
- `journal_name`: optional, defaults to `"Cash"` — must be a Cash or Bank type journal

Returns `{ status: "payment_registered", invoice, amount_paid, remaining_balance }` on success. Returns structured errors for invalid inputs without creating any Odoo records.

### close_invoice(invoice_number, journal_name?)

Close a customer invoice end-to-end. Auto-posts draft invoices before payment. Idempotent — returns `already_closed` if the residual is already zero. Reuses all `register_payment` safety guards (journal validation, overpayment prevention, structured errors). Verifies reconciliation by re-reading residual after payment.

- `invoice_number`: invoice name as displayed in Odoo (e.g. `INV/2026/00001`)
- `journal_name`: optional, defaults to `"Cash"`

Returns `{ status: "invoice_closed", invoice, paid_amount, remaining_balance: 0 }` on success.

### auto_close_all_unpaid(journal_name?, dry_run?)

Batch-close all open customer invoices — both draft and posted. Queries all invoices with `state in [draft, posted]` and `payment_state != paid`, then calls `close_invoice` for each sequentially. Never throws — always returns a structured batch summary.

- `journal_name`: optional, defaults to `"Cash"`
- `dry_run`: optional boolean (default `false`). If `true`, performs a read-only preview — no payments are created and no Odoo data is modified.

**Normal mode** (`dry_run: false` or omitted):
Returns `{ status: "batch_complete", total_processed, total_closed, total_amount_reconciled, failures[] }`.

**Dry-run mode** (`dry_run: true`):
Returns `{ status: "dry_run", total_would_process, total_amount, invoices: [{ name, state, amount_residual }] }` — safe to call before committing to a batch payment.

## Security Constraints

- **Draft only**: `create_draft_invoice` creates invoices in `draft` state. The server never calls `action_post`.
- **No auto-execution**: no tool triggers posting, payment registration, or any state-changing accounting action beyond draft creation.
- **HITL approval**: all monetary write actions must go through the vault's approval-detector pipeline before reaching this server.
- **Logging**: every tool invocation is appended to `Vault/Logs/YYYY-MM-DD.json` with timestamp, tool name, args, and status.

## Architecture

```
scripts/
├── index.js         — MCP server entry point (stdio transport)
├── odoo-client.js   — Odoo JSON-RPC client (auth + call wrappers)
├── logger.js        — Vault-compatible append-safe JSON logger
└── package.json     — Node.js dependencies
```

For Odoo JSON-RPC API details, see [references/odoo-jsonrpc.md](references/odoo-jsonrpc.md).
