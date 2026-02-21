# Security Policy

**Owner**: Vault Architect
**Last Reviewed**: 2026-02-21

---

## Secrets Management

- **NEVER** commit secrets to this vault or any git repository
- All credentials live in `.env` (gitignored) or system environment variables
- `.mcp.json` is gitignored — contains Odoo credentials
- Rotate credentials on any suspected exposure

### Required Environment Variables

```
ODOO_URL            Odoo instance URL
ODOO_DB             Odoo database name
ODOO_USERNAME       Odoo login
ODOO_PASSWORD       Odoo password
VAULT_ROOT          Absolute path to this vault
GMAIL_CREDENTIALS   Path to Gmail OAuth JSON (never vault itself)
DRY_RUN             Set to "true" to enable global dry-run mode
```

---

## Human-in-the-Loop (HITL) Gates

All actions in these categories require an approval file in `/Pending_Approval/` before execution:

| Category | Examples | Approval File Required |
|----------|---------|----------------------|
| Financial | Payments, invoice posting, transfers | Yes |
| Communications | Emails sent externally | Yes |
| Publishing | LinkedIn posts, public content | Yes |
| Data deletion | Removing vault files, Odoo records | Yes |

**Approval flow**: `Pending_Approval/` → human review → move to `Approved/` or `Rejected/`

---

## DRY_RUN Global Safety

All watchers, orchestrators, and MCP tools must check `DRY_RUN` before executing write actions.

```python
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
if DRY_RUN:
    log_structured("dry_run", {"action": action, "skipped": True})
    return
```

---

## Action Thresholds (hard limits)

| Action | Max per day | Behaviour if exceeded |
|--------|------------|----------------------|
| Emails sent | 50 | Block + alert |
| Invoices created | 20 | Block + alert |
| Payments registered | 10 | Block + HITL escalation |
| Vault files deleted | 5 | Block always |

---

## Audit Requirements

Every agent action must produce a structured log entry in `/Logs/YYYY-MM-DD.json`:

```json
{
  "timestamp": "ISO-8601",
  "tool": "tool_name",
  "action": "action_type",
  "args": {},
  "outcome": "success|error|dry_run|blocked",
  "dry_run": false
}
```

---

## Autonomy Score Weights

These values control `execution_metrics.py`. Override via environment variables.

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUTONOMY_FAILURE_PENALTY` | `20` | Points deducted per 1% failure rate |
| `AUTONOMY_MANUAL_WEIGHT` | `5` | Points deducted per approval-required event (max 30) |
| `AUTONOMY_TIME_SAVED_MULTIPLIER` | `0.25` | Hours saved per auto-completed task |

**Score formula:**
```
autonomy_score = (auto_completed / total) × 100
               − (failure_rate × FAILURE_PENALTY)
               − min(approval_events × MANUAL_WEIGHT, 30)
Clamped to [0, 100].
```

**Score bands:**
- 85–100 = 🟢 STRONG — investor-grade automation
- 65–84  = 🟡 MODERATE — solid, optimisation opportunities
- 40–64  = 🟠 DEVELOPING — significant manual overhead remains
- 0–39   = 🔴 WEAK — system requires immediate intervention

---

## Incident Response

1. Suspected credential exposure → revoke immediately, update `.env`, restart all watchers
2. Unintended financial action → check Odoo, create reversal approval file
3. Runaway orchestrator → kill process, check `/Logs/`, review `/In_Progress/`
