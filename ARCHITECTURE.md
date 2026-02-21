# Architecture — AI Employee Vault

**Version**: 1.0
**Last Updated**: 2026-02-21

---

## System Overview

A production-grade Digital FTE built on three layers:

```
┌─────────────────────────────────────────────────────┐
│                   SIGNAL LAYER                      │
│  gmail_watcher  whatsapp_watcher  finance_watcher   │
│              filesystem_watcher                     │
│              (Python BaseWatcher)                   │
└────────────────────┬────────────────────────────────┘
                     │ writes to /Needs_Action/
┌────────────────────▼────────────────────────────────┐
│               ORCHESTRATION LAYER                   │
│                 orchestrator.py                     │
│   Monitor → Claim → Claude CLI → Move to /Done      │
│          (Ralph Wiggum loop, max 10 iter)           │
└────────────────────┬────────────────────────────────┘
                     │ calls via MCP
┌────────────────────▼────────────────────────────────┐
│                 ACTION LAYER                        │
│   odoo-mcp  email-mcp  browser-mcp  calendar-mcp   │
│         All gated by DRY_RUN + HITL approval        │
└─────────────────────────────────────────────────────┘
```

---

## Vault Folder Contracts

| Folder | Owner | Contents | SLA |
|--------|-------|----------|-----|
| `/Inbox/` | Watchers | Raw inbound signals | Processed within 15 min |
| `/Needs_Action/` | Orchestrator | Structured task files | Claimed within 5 min |
| `/In_Progress/` | Orchestrator | Claimed tasks (lock files) | Max 30 min |
| `/Plans/` | Claude Skills | Execution plans | Referenced by orchestrator |
| `/Pending_Approval/` | Claude Skills | HITL approval requests | Human reviews within 1h |
| `/Approved/` | Human | Approved actions | Executed within 5 min |
| `/Rejected/` | Human | Rejected actions | Logged, no action |
| `/Done/` | Orchestrator | Completed tasks | Retained 30 days |
| `/Logs/` | All | Structured JSON audit logs | Never deleted |
| `/Briefings/` | Sunday audit | CEO-level summaries | Generated weekly |
| `/Accounting/` | Finance watcher | CSV/JSON financial data | Updated daily |
| `/Signals/` | Watchers | Parsed signal metadata | Indexed by watcher |
| `/Updates/` | Orchestrator | Status updates to humans | Read-only |

---

## Agent Skills (Claude Code)

All skills live in `.claude/skills/<name>/SKILL.md`.

| Skill | Trigger | Output |
|-------|---------|--------|
| vault-processor | manual / orchestrator | Structured plans from /Needs_Action |
| silver-orchestrator | scheduled / manual | Full vault cycle |
| approval-detector | post-planning | Approval files in /Pending_Approval |
| email-executor | post-approval | Email sent via MCP |
| ceo-briefing | Sunday audit | /Briefings/YYYY-MM-DD.md |
| performance-analyst | on-demand | Metrics report |
| risk-policy-enforcer | pre-orchestrator | Policy gate decision |
| execution-manager | wraps other skills | Execution context + audit log |
| plan-creator | from needs-action | Phased plans |
| needs-action-processor | orchestrator | Converts inbox items to plans |

---

## MCP Servers

| Server | Transport | Purpose |
|--------|-----------|---------|
| odoo-mcp-server | stdio/node | Odoo 19+ JSON-RPC (invoices, payments) |
| email-mcp | stdio | Send/read email via Gmail API |
| browser-mcp | stdio | Playwright browser automation |
| calendar-mcp | stdio (stub) | Calendar read/write |

---

## Python Watchers

All watchers in `/watchers/`, managed by `watchdog.py`.

| Watcher | Source | Output |
|---------|--------|--------|
| filesystem_watcher | /Inbox filesystem | /Needs_Action task files |
| gmail_watcher | Gmail API | /Signals/ + /Needs_Action/ |
| whatsapp_watcher | Playwright/web | /Signals/ + /Needs_Action/ |
| finance_watcher | CSV / Odoo API | /Accounting/ updates |

---

## Claim-by-Move Rule (Platinum)

To prevent race conditions in cloud/multi-agent deployments:
1. Orchestrator atomically moves file from `/Needs_Action/X.md` → `/In_Progress/<agent-id>-X.md`
2. Only the process that completes the move owns the task
3. On completion: move to `/Done/`
4. On timeout (30 min): watchdog reclaims to `/Needs_Action/`

---

## DRY_RUN Propagation

```
ENV: DRY_RUN=true
  → orchestrator.py reads it
  → passes --dry-run flag to Claude CLI
  → MCP tools check DRY_RUN before writes
  → All logs include "dry_run": true
```

---

## Key Design Decisions

1. **No secrets in vault** — `.mcp.json` and `.env` are gitignored
2. **Structured logs only** — all agents write JSON, never plain text
3. **Idempotent actions** — every MCP write is safe to retry
4. **HITL on all financial/comms actions** — no autonomous money movement
5. **Claim-by-move** — file rename is atomic on POSIX and Windows NTFS
