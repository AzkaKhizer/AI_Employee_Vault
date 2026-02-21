# AI Employee Vault — Dashboard

**Last Updated**: 2026-02-21 | **Mode**: LIVE | **DRY_RUN**: false

---

## System Status

| Component | Status | Notes |
|-----------|--------|-------|
| filesystem_watcher | ⚪ Not started | `python watchers/watchdog.py --dev` |
| gmail_watcher | ⚪ Not started | Needs GMAIL_CREDENTIALS configured |
| finance_watcher | ⚪ Not started | Starts with watchdog |
| whatsapp_watcher | ⚪ Disabled | Enable in watchdog.py after Playwright setup |
| orchestrator | ⚪ Not started | `python orchestrator.py --dev` |
| odoo-mcp-server | 🟢 Active | 7 tools: invoices, payments, batch close |

---

## Queue Snapshot

| Folder | Count |
|--------|-------|
| Inbox | 0 |
| Needs_Action | 0 |
| In_Progress | 0 |
| Projects | 3 |
| Pending_Approval | 1 |
| Approved | 0 |
| Done | 4 |

---

## Active Projects

| File | Priority | Status | Processed |
|------|----------|--------|-----------|
| new.md | Medium | Pending | 2026-02-17 |
| send-client-update.md | High | Pending_Approval | 2026-02-17 |
| weekly-linkedin-content-strategy.md | Medium | Pending_Approval | 2026-02-17 |

## Pending Approval

| File | Priority |
|------|----------|
| approval-weekly-linkedin-content-strategy.md | Medium |

## Done

| File | Completed |
|------|-----------|
| approval-send-client-update.md | 2026-02-17 |
| new.md | 2026-02-17 |
| send-client-update.md | 2026-02-17 |
| test-task.md | 2026-02-17 |

---

## Financial Summary (Odoo)

| Metric | Value |
|--------|-------|
| Paid this session | PKR 4,501 |
| Outstanding (2 draft invoices) | PKR 4,200 |
| Overdue invoices | 0 |
| Last batch close | 2026-02-21 (4 invoices) |

---

## Recent Activity

- `2026-02-21` — `dry_run` mode added to `auto_close_all_unpaid` ✅
- `2026-02-21` — Batch close 4 invoices (PKR 4,501) ✅
- `2026-02-21` — `close_invoice` + `auto_close_all_unpaid` shipped ✅
- `2026-02-21` — `register_payment` full SDD cycle completed ✅

---

## Quick Commands

```bash
# Start all watchers (safe dev mode)
python watchers/watchdog.py --dev --dry-run

# Run orchestrator once (dev mode)
python orchestrator.py --once --dev --dry-run

# Dry-run preview of all unpaid invoices
# auto_close_all_unpaid(dry_run=true) via MCP
```


## Autonomy Metrics (Last 7 Days)

| Metric | Value |
|--------|-------|
| **Autonomy Score** | 54.4/100 — 🟠 DEVELOPING →(+0.0) |
| Tasks Completed | 22 / 35 |
| HITL Rate | 2.9% (1 approvals) →(+0.0) |
| Failures | 6 (17.1%) →(+0.0) |
| Avg Iterations/Task | 1.0 |
| Estimated Hours Saved | 5.5h |
| Dry-Run Calls | 0 |

*Score = auto_rate − failure_deduction(3.4) − manual_deduction(5.0)*
*Generated: 2026-02-21T15:36:33Z*


---

*See: [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) · [Business_Goals.md](Business_Goals.md)*
