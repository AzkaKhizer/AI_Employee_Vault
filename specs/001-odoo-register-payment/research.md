# Research: Odoo Register Payment MCP Tool

**Feature**: `001-odoo-register-payment`
**Date**: 2026-02-21
**Phase**: 0 — Research & Unknowns Resolution

---

## Decision 1: Payment + Reconciliation Strategy

**Decision**: Use the `account.payment.register` wizard via JSON-RPC as the primary reconciliation path.

**Rationale**:
Odoo 17+ fundamentally changed how payments reconcile against invoices. The old `js_assign_outstanding_credit` is a client-side JavaScript method not reliably callable server-side. The `account.payment.register` wizard (`account.payment.register`) is the officially supported server-side path — it handles payment creation, posting, and reconciliation atomically in one action.

The call sequence via JSON-RPC:
1. `odoo.call('account.payment.register', 'create', [{ amount, journal_id, payment_date }], { context: { active_model: 'account.move', active_ids: [invoice_id] } })`
2. `odoo.call('account.payment.register', 'action_create_payments', [[wizard_id]])`
3. Re-read `account.move` to get the updated `amount_residual`

**Alternatives considered**:
- Manual `account.payment` create + `action_post` + `account.move.line.reconcile()`: More error-prone; requires finding the correct matching move lines (receivable debit vs. credit) after posting, with risk of reconciling wrong lines. Rejected as primary path.
- `js_assign_outstanding_credit`: Client-side JS action; not suitable for server-side JSON-RPC. Rejected.
- Direct ORM write: Not accessible via public JSON-RPC endpoint. Rejected.

---

## Decision 2: OdooClient Extension

**Decision**: No new `OdooClient` class methods are needed. The existing `call()` method covers all required JSON-RPC operations.

**Rationale**:
- `searchRead()` handles invoice and journal lookups.
- `call(model, method, args, kwargs)` covers wizard creation, `action_create_payments`, and post-payment re-reads.
- `create()` can be used as an alternative to `call()` for wizard creation, but `call()` with kwargs context is preferred for passing `active_model` and `active_ids`.

**Impact**: Zero changes to `odoo-client.js`. All new logic lives in the handler function in `index.js`.

---

## Decision 3: Journal Lookup

**Decision**: Case-insensitive `ilike` search on `account.journal` by `name`, filtered to `type = 'bank' OR type = 'cash'` to prevent selection of non-payment journals.

**Rationale**: The `name` field in Odoo journal records uses human-readable names like "Cash" or "Bank". Case-insensitive search prevents failures from minor capitalisation differences. Restricting to `bank`/`cash` journal types prevents accidentally using a general journal (which would corrupt accounting).

**Alternatives considered**:
- Exact match: Fragile. Rejected.
- No type filter: Allows selecting non-payment journals. Rejected for safety.

---

## Decision 4: Partial Reconciliation Handling

**Decision**: The wizard approach handles partial amounts natively — pass `amount` to the wizard; it reconciles exactly that amount and leaves the invoice partially unpaid.

**Rationale**: Odoo's `account.payment.register` wizard accepts an `amount` field. If the amount is less than the invoice total, it creates a partial payment and the invoice's `payment_state` becomes `"partial"` rather than `"paid"`. No special code needed.

---

## Decision 5: Post-Payment Balance Read

**Decision**: After `action_create_payments`, re-read the invoice with `searchRead` on `account.move` to get the final `amount_residual`.

**Rationale**: The wizard call does not return the updated invoice state directly. A follow-up read ensures the returned `remaining_balance` reflects the true post-payment state. This also serves as an implicit verification that reconciliation succeeded.

---

## Decision 6: Amount Precision

**Decision**: Round all floating-point amounts to 2 decimal places before sending to Odoo and in responses.

**Rationale**: Floating-point arithmetic in JavaScript can introduce precision errors (e.g., `5000.0000000001`). Rounding to 2dp before comparison and before sending to Odoo prevents false overpayment rejections and incorrect balances. Consistent with existing handler pattern in `handleGetRevenueSummary`.

---

## Resolved Unknowns Summary

| Unknown | Resolution |
|---------|------------|
| Reconciliation API in Odoo 19+ | `account.payment.register` wizard via `call()` |
| New OdooClient methods needed? | None — existing `call()` sufficient |
| Journal resolution strategy | `ilike` name search, filtered to cash/bank types |
| Partial payment support | Native via wizard `amount` field |
| Post-payment balance source | Re-read `account.move.amount_residual` after wizard |
| Float precision | Round to 2dp throughout |
