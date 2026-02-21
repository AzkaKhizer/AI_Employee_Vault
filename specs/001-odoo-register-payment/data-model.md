# Data Model: Odoo Register Payment MCP Tool

**Feature**: `001-odoo-register-payment`
**Date**: 2026-02-21

---

## Entities (Odoo-side — read-only from MCP perspective, written via JSON-RPC)

### 1. Invoice (`account.move`)

| Field | Type | Usage |
|-------|------|-------|
| `id` | integer | Primary key; passed to wizard as `active_ids` |
| `name` | string | Invoice number (e.g., `INV/2026/00001`); used for lookup |
| `move_type` | string | Must be `"out_invoice"` (customer invoice) |
| `state` | string | Must be `"posted"` for payment eligibility |
| `payment_state` | string | Guard: `"paid"` → return `already_paid`; `"not_paid"` or `"partial"` → proceed |
| `amount_residual` | float | Outstanding balance; used for overpayment guard and returned as `remaining_balance` |
| `partner_id` | [id, name] | Customer reference; carried forward for logging |
| `currency_id` | [id, name] | Used by wizard for currency context (implicit) |

**Lookup query**:
```
model: account.move
domain: [['move_type','=','out_invoice'], ['state','=','posted'], ['name','=',invoice_number]]
fields: ['id','name','partner_id','amount_residual','payment_state','currency_id']
limit: 1
```

**State transition guard**:
```
payment_state == "paid"      → return already_paid (no write)
payment_state == "not_paid"  → proceed
payment_state == "partial"   → proceed (further partial payment allowed)
```

---

### 2. Journal (`account.journal`)

| Field | Type | Usage |
|-------|------|-------|
| `id` | integer | Passed to wizard as `journal_id` |
| `name` | string | Resolved from user input `journal_name` |
| `type` | string | Must be `"bank"` or `"cash"` (safety filter) |

**Lookup query**:
```
model: account.journal
domain: [['name','ilike',journal_name], ['type','in',['cash','bank']]]
fields: ['id','name','type']
limit: 1
```

---

### 3. Payment Register Wizard (`account.payment.register`)

Transient model; created, used, and discarded in a single operation.

| Field | Type | Usage |
|-------|------|-------|
| `amount` | float | Payment amount (rounded to 2dp) |
| `journal_id` | integer | Resolved journal ID |
| `payment_date` | string | ISO date (today, server-computed) |

**Context required**:
```json
{ "active_model": "account.move", "active_ids": [invoice_id] }
```

**Operations**:
1. `create` → returns `wizard_id`
2. `action_create_payments([[wizard_id]])` → creates, posts, and reconciles payment

---

### 4. MCP Tool Input Schema

```json
{
  "type": "object",
  "properties": {
    "invoice_number": {
      "type": "string",
      "description": "Invoice number as displayed in Odoo (e.g. INV/2026/00001)"
    },
    "amount": {
      "type": "number",
      "description": "Payment amount (positive, must not exceed outstanding balance)"
    },
    "journal_name": {
      "type": "string",
      "description": "Accounting journal to use (default: Cash)"
    }
  },
  "required": ["invoice_number", "amount"]
}
```

---

### 5. MCP Tool Output Schema

**Success**:
```json
{
  "status": "payment_registered",
  "invoice": "INV/2026/00001",
  "amount_paid": 5000,
  "remaining_balance": 0
}
```

**Already paid**:
```json
{
  "status": "already_paid",
  "invoice": "INV/2026/00001",
  "remaining_balance": 0
}
```

**Error (structured)**:
```json
{
  "error": "invoice_not_found | invoice_not_posted | invalid_amount | overpayment_not_allowed | journal_not_found",
  "detail": "Human-readable explanation",
  "invoice_number": "INV/2026/00001"
}
```

---

## Validation Rules

| Rule | Check | Guard Type |
|------|-------|------------|
| Amount > 0 | `amount <= 0` | Local pre-flight (no Odoo call) |
| Invoice exists | `searchRead` returns 0 records | After Odoo read |
| Invoice posted | `state == 'posted'` | After Odoo read (redundant with domain filter, kept as explicit guard) |
| Not already paid | `payment_state != 'paid'` | After Odoo read |
| No overpayment | `round(amount,2) <= round(amount_residual,2)` | After Odoo read |
| Journal found | `searchRead` returns ≥1 record | After Odoo read |

**Order matters**: Local guards run first (no Odoo I/O). Odoo reads run second. Writes only happen after all guards pass.
