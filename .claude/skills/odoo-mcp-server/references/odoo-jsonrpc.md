# Odoo JSON-RPC Reference

## Authentication

```
POST /web/session/authenticate
{
  "jsonrpc": "2.0",
  "params": { "db": "<db>", "login": "<user>", "password": "<pass>" }
}
```

Returns session with `uid`. All subsequent calls use the authenticated session cookie.

## Common Endpoints

### search_read

```
POST /web/dataset/call_kw
{
  "params": {
    "model": "account.move",
    "method": "search_read",
    "args": [[["state", "=", "posted"]]],
    "kwargs": { "fields": ["name", "amount_total"], "limit": 100 }
  }
}
```

### create

```
POST /web/dataset/call_kw
{
  "params": {
    "model": "account.move",
    "method": "create",
    "args": [{ "move_type": "out_invoice", "partner_id": 1, ... }],
    "kwargs": {}
  }
}
```

## Key Models

| Model | Purpose |
|---|---|
| `account.move` | Invoices, bills, journal entries |
| `res.partner` | Customers and vendors |
| `account.move.line` | Invoice/bill line items |

## Invoice move_type Values

| Value | Meaning |
|---|---|
| `out_invoice` | Customer invoice |
| `in_invoice` | Vendor bill |
| `out_refund` | Customer credit note |
| `in_refund` | Vendor credit note |

## Invoice States

| State | Meaning |
|---|---|
| `draft` | Not yet posted — safe to edit |
| `posted` | Finalized — appears in accounting |
| `cancel` | Cancelled |

## Payment States

| State | Meaning |
|---|---|
| `not_paid` | No payment received |
| `partial` | Partially paid |
| `paid` | Fully paid |
| `in_payment` | Payment initiated |
