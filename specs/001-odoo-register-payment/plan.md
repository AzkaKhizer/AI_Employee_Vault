# Implementation Plan: Odoo Register Payment MCP Tool

**Branch**: `001-odoo-register-payment` | **Date**: 2026-02-21 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-odoo-register-payment/spec.md`

---

## Summary

Add a `register_payment` tool to the existing Odoo MCP server (`index.js`) that accepts an invoice number, amount, and optional journal name, validates all safety guards locally and against Odoo, then creates and reconciles a payment via Odoo's `account.payment.register` wizard — returning a structured JSON response with the remaining balance. All changes are confined to a single handler function and tool definition entry in `index.js`.

---

## Technical Context

**Language/Version**: Node.js (ESM, existing project — no version change)
**Primary Dependencies**: `@modelcontextprotocol/sdk`, `node-fetch` (both already installed)
**Storage**: N/A (stateless handler; Odoo is the system of record)
**Testing**: Manual integration against live Odoo 19+ instance; unit tests via mocked `OdooClient`
**Target Platform**: Windows 10 / Node.js process (same as existing MCP server)
**Project Type**: Single — additive change to existing server file
**Performance Goals**: Response in under 5 seconds (SC-001); wizard approach adds ~2 JSON-RPC round trips beyond existing tool baseline
**Constraints**: No overpayment; no writes before all guards pass; all errors return structured JSON (no throws to MCP layer)
**Scale/Scope**: Single-user vault; no concurrency requirements

---

## Constitution Check

The project constitution is a placeholder template (no live principles). Applying baseline SDD standards:

| Gate | Status | Notes |
|------|--------|-------|
| Smallest viable diff | PASS | Single file modified (`index.js`); no new dependencies |
| No hardcoded secrets | PASS | Credentials from env vars (existing pattern) |
| Structured error responses | PASS | All error paths return `{ error, detail }` |
| Logging on every invocation | PASS | `logToVault` called at start and on completion |
| No unrelated refactoring | PASS | Only `register_payment` handler and tool definition added |

---

## Project Structure

### Documentation (this feature)

```text
specs/001-odoo-register-payment/
├── plan.md              ← This file
├── research.md          ← Phase 0: reconciliation strategy decisions
├── data-model.md        ← Phase 1: entity fields, validation rules, schemas
├── quickstart.md        ← Phase 1: usage examples, test sequence
├── contracts/
│   └── register_payment.json  ← Tool I/O contract
├── checklists/
│   └── requirements.md  ← Spec quality checklist (all pass)
└── tasks.md             ← Phase 2 output (created by /sp.tasks — not yet)
```

### Source Code (single file change)

```text
.claude/skills/odoo-mcp-server/scripts/
├── index.js             ← MODIFIED: add tool definition + handler + dispatch entry
├── odoo-client.js       ← UNCHANGED
└── logger.js            ← UNCHANGED
```

---

## Data Flow

```
Claude (MCP client)
    │
    │  register_payment({ invoice_number, amount, journal_name? })
    ▼
┌─────────────────────────────────────────────────────────────┐
│  handleRegisterPayment()                                    │
│                                                             │
│  [1] LOCAL GUARD: amount > 0                               │
│       ✗ → { error: "invalid_amount" }                      │
│                                                             │
│  [2] ODOO READ: searchRead account.move                    │
│       domain: [move_type=out_invoice, state=posted,        │
│                name=invoice_number]                         │
│       ✗ (0 results) → { error: "invoice_not_found" }      │
│                                                             │
│  [3] GUARD: payment_state == "paid"                        │
│       ✗ → { status: "already_paid", remaining_balance: 0 } │
│                                                             │
│  [4] GUARD: round(amount,2) <= round(amount_residual,2)    │
│       ✗ → { error: "overpayment_not_allowed" }            │
│                                                             │
│  [5] ODOO READ: searchRead account.journal                 │
│       domain: [name ilike journal_name, type in cash/bank] │
│       ✗ (0 results) → { error: "journal_not_found" }      │
│                                                             │
│  ── ALL GUARDS PASSED — WRITE PHASE ──                     │
│                                                             │
│  [6] ODOO WRITE: call account.payment.register / create    │
│       context: { active_model: account.move,               │
│                  active_ids: [invoice_id] }                 │
│       values:  { amount, journal_id, payment_date: today } │
│       → wizard_id                                          │
│                                                             │
│  [7] ODOO WRITE: call account.payment.register /           │
│       action_create_payments([[wizard_id]])                 │
│       → payment created, posted, reconciled                │
│                                                             │
│  [8] ODOO READ: searchRead account.move (re-read invoice)  │
│       → updated amount_residual                            │
│                                                             │
│  [9] LOG: logToVault({ tool, invoice, amount, status })    │
│                                                             │
│  [10] RETURN: { status: "payment_registered",              │
│                 invoice, amount_paid, remaining_balance }   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
Claude (MCP client) displays result
```

---

## Failure Handling Plan

| Failure Point | Odoo Writes Made? | Response | Action |
|---------------|:-----------------:|----------|--------|
| amount ≤ 0 | None | `{ error: "invalid_amount" }` | Return immediately |
| Invoice not found | None | `{ error: "invoice_not_found" }` | Return immediately |
| Invoice already paid | None | `{ status: "already_paid", remaining_balance: 0 }` | Return immediately |
| Overpayment | None | `{ error: "overpayment_not_allowed" }` | Return immediately |
| Journal not found | None | `{ error: "journal_not_found" }` | Return immediately |
| Wizard create fails | None | Caught by try/catch in dispatch → `Error: <msg>` | Log + return MCP error |
| `action_create_payments` fails | Wizard created (transient, auto-cleaned) | Caught by try/catch → `Error: <msg>` | Log + return MCP error |
| Re-read fails | Payment posted and reconciled | Catch → return partial success with `remaining_balance: null` | Log warning |
| Unhandled exception | Unknown | MCP `isError: true` response | Logged by existing dispatch wrapper |

**Key invariant**: No write to `account.payment` or `account.move` occurs until steps [1]–[5] all pass. The write phase is Steps [6]–[7] only.

---

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| Credential exposure | Credentials are in env vars (`ODOO_PASSWORD`); never logged or returned |
| Amount injection | `amount` is validated as a JS `number` by MCP SDK schema; rounded to 2dp before use |
| Invoice number injection | Passed as exact string to Odoo domain filter; Odoo JSON-RPC handles escaping internally |
| Overpayment | Explicit guard at step [4]: `round(amount,2) <= round(amount_residual,2)` |
| Journal type confusion | Journal lookup is filtered to `type IN ('cash','bank')` — prevents use of misc journals |
| Replay/double payment | Not idempotent by design; caller must verify invoice state via `list_unpaid_invoices` before retrying |
| Privilege escalation | Uses same Odoo session as all other tools; no elevated permissions granted |

---

## Logging Strategy

Every invocation logs two entries to the Vault daily log (`Logs/YYYY-MM-DD.json`) using the existing `logToVault()`:

**Entry 1 — Invocation** (before any Odoo call):
```json
{
  "timestamp": "...",
  "source": "odoo-mcp-server",
  "tool": "register_payment",
  "args": { "invoice_number": "...", "amount": 5000, "journal_name": "Cash" },
  "status": "invoked"
}
```

**Entry 2 — Outcome** (after handler completes):
```json
{
  "timestamp": "...",
  "source": "odoo-mcp-server",
  "tool": "register_payment",
  "status": "success | error_response | error",
  "result_summary": "payment_registered | already_paid | invalid_amount | ..."
}
```

This matches the existing pattern in the dispatch wrapper (`HANDLERS` map call in `server.setRequestHandler`). The handler itself does not call `logToVault` — the existing wrapper in `index.js` handles both log entries already. No changes to logging infrastructure needed.

---

## Testing Strategy

### Unit Tests (mock OdooClient)

| Test Case | Guard Tested | Expected |
|-----------|-------------|----------|
| amount = 0 | Local guard | `{ error: "invalid_amount" }` |
| amount = -100 | Local guard | `{ error: "invalid_amount" }` |
| Invoice not in Odoo | searchRead returns [] | `{ error: "invoice_not_found" }` |
| Invoice payment_state = "paid" | already_paid guard | `{ status: "already_paid" }` |
| amount > amount_residual | Overpayment guard | `{ error: "overpayment_not_allowed" }` |
| Journal not found | Journal lookup | `{ error: "journal_not_found" }` |
| Valid full payment (mocked wizard) | Full happy path | `{ status: "payment_registered", remaining_balance: 0 }` |
| Valid partial payment (mocked wizard) | Partial path | `{ status: "payment_registered", remaining_balance: N }` |

### Integration Tests (live Odoo 19+)

| Sequence | Steps | Pass Criteria |
|----------|-------|---------------|
| Full payment workflow | 1. Create+post invoice, 2. `register_payment` full amount, 3. `list_unpaid_invoices` | Invoice absent from unpaid list; `remaining_balance: 0` |
| Partial payment | 1. Create+post invoice, 2. `register_payment` half amount | `remaining_balance` = half; invoice still in unpaid list |
| Double payment guard | 1. Full payment, 2. `register_payment` again | `{ status: "already_paid" }` |
| Overpayment guard | `register_payment` with amount > outstanding | `{ error: "overpayment_not_allowed" }`; no Odoo record created |
| Invalid journal | `register_payment` with `journal_name: "Nonexistent"` | `{ error: "journal_not_found" }` |

---

## Implementation Steps (ordered)

### Step 1 — Tool Definition
Add to `TOOLS` array in `index.js`:
```js
{
  name: "register_payment",
  description: "Register a payment against a posted customer invoice in Odoo. Creates, posts, and reconciles the payment. Prevents overpayment and negative amounts.",
  inputSchema: {
    type: "object",
    properties: {
      invoice_number: { type: "string", description: "Invoice number (e.g. INV/2026/00001)" },
      amount: { type: "number", description: "Payment amount (positive, must not exceed outstanding balance)" },
      journal_name: { type: "string", description: "Journal name (default: Cash)" },
    },
    required: ["invoice_number", "amount"],
  },
}
```

### Step 2 — Handler Function
Add `handleRegisterPayment()` function following the guard-then-write pattern documented in the data flow above. Key points:
- Default `journal_name` to `"Cash"` if not provided
- Round `amount` to 2dp before all comparisons and Odoo calls
- Use `odoo.call()` with context for wizard creation and `action_create_payments`
- Re-read invoice after wizard to get final `amount_residual`
- Return structured objects (never `throw` — existing dispatch wrapper catches and wraps)

### Step 3 — Dispatch Registration
Add to `HANDLERS` map:
```js
register_payment: handleRegisterPayment,
```

### Step 4 — Manual Verification
Run the test sequence from `quickstart.md` against the live Odoo 19+ instance.

---

## Non-Goals (explicit)

- No auto-posting of draft invoices
- No multi-currency conversion
- No vendor bill payment support (`in_invoice`)
- No payment reversal or cancellation
- No batch payment registration
- No changes to `odoo-client.js`, `logger.js`, or `.mcp.json`

---

## Risks

1. **`action_create_payments` wizard context passing**: Odoo transient models expect `active_model`/`active_ids` in the execution context. If the OdooClient's `call()` method does not correctly propagate these context keys, the wizard will not link to the invoice and reconciliation will not occur. **Mitigation**: Verify context propagation in integration test before marking complete; fall back to manual create+post+reconcile via move lines if needed.

2. **Float precision edge case in overpayment guard**: JavaScript float arithmetic may cause `amount > amount_residual` to be false when it should be true (or vice versa). **Mitigation**: Round both sides to 2dp using `Math.round(x * 100) / 100` before comparison.

3. **Odoo version drift**: The `account.payment.register` wizard API changed between Odoo 16 and 17+. If the Odoo instance is downgraded, the wizard call may fail. **Mitigation**: Instance is confirmed Odoo 19+ per spec assumptions; out of scope to support older versions.
