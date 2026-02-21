---
name: ceo-briefing
description: Generate an executive-level briefing from the current state of the AI Employee vault. Scans Projects/, Pending_Approval/, and Done/ to produce a portfolio snapshot, risk signals, bottlenecks, momentum analysis, and priority focus items. Read-only — does not modify any files. Use when the user says "CEO update", "project status", "execution health", "where are we blocked", "weekly executive report", "briefing", or asks for an overall summary of what is happening across the vault.
---

# CEO Briefing

## Constraints

- **Read-only.** Do not create, edit, move, or delete any files.
- **No fluff.** No motivational language, no filler sentences.
- **Executive tone.** Short sentences. Direct observations. Data-backed.

## Workflow

1. **Parse time window** — Default: last 7 days. If user specifies a window (e.g., "last month", "past 2 weeks"), use that instead.
2. **Scan folders** — Read all `.md` files in `Projects/`, `Pending_Approval/`, and `Done/`.
3. **Extract metadata** from each file:
   - `**Priority:**` — High, Medium, or Low
   - `**Status:**` — Pending, In Progress, Done, or Unknown
   - `**Processed:**` — timestamp in `YYYY-MM-DD HH:MM` format
4. **Compute metrics:**
   - Active projects count (files in `Projects/`)
   - Pending approval count (files in `Pending_Approval/`)
   - Completed count (files in `Done/` with Processed date within time window)
   - Priority distribution (count of High / Medium / Low across all active + pending)
5. **Load autonomy metrics** — Run `python execution_metrics.py --days N --json` (or read the latest `Logs/metrics-report-*.json` if the script is unavailable). Extract: `autonomy_score`, `tasks_auto_completed`, `execution_failures`, `approval_rate`, `estimated_time_saved_hours`. If unavailable, note "Autonomy data not available."
6. **Scan financial signals** — Read the latest file in `Accounting/` (JSON snapshots or CSV). Extract: overdue invoices count, total outstanding, paid this period. If unavailable, use Odoo MCP `list_unpaid_invoices` and `get_revenue_summary`.
7. **Detect subscription waste** — Scan `Business_Goals.md` subscription table. Flag any service where Value Assessment is blank, "Unknown", or cost > PKR 5,000/month with no documented ROI.
8. **Detect risk signals** — Apply the Risk Signal Rules below.
9. **Detect bottlenecks** — Apply the Bottleneck Rules below.
10. **Assess momentum** — Apply the Momentum Heuristics below.
11. **Identify priority focus** — Select top 3 items requiring leadership attention.
12. **Generate 3 strategic actions** — Based on all signals, propose 3 concrete next actions.
13. **Generate briefing** — Output using the Briefing Template below.
14. **Delivery** — Print briefing to chat. If user requests saving, write to `Briefings/<date>-ceo-briefing.md`.

## Risk Signal Rules

Flag each condition that is true:

| Signal | Condition |
|--------|-----------|
| High-priority overload | More than 50% of active items are High priority |
| Approval bottleneck | 3+ items in Pending_Approval, or any item there for 7+ days |
| Stalled execution | No items completed within the time window |
| Unbalanced workload | All active items are in a single priority tier |
| Growing backlog | Items in Projects/ exceed items in Done/ by 3x or more |

If no signals detected, state: "No risk signals detected."

## Bottleneck Rules

Flag each condition that is true:

| Bottleneck | Condition |
|------------|-----------|
| Approval queue | Any item in Pending_Approval/ with Processed date older than 7 days |
| Stale projects | Any item in Projects/ with Status "Pending" and Processed date older than 14 days |
| Missing metadata | Any file lacking Priority or Status fields (flag as ungoverned) |

For each bottleneck, name the specific file(s) causing it.

If no bottlenecks detected, state: "No bottlenecks detected."

## Momentum Heuristics

Assess execution velocity using these indicators:

- **Strong:** Multiple completions within time window, approval queue is clear, active projects show "In Progress" status.
- **Moderate:** Some completions, some items aging, approval queue has 1-2 items.
- **Weak:** No recent completions, growing approval queue, most items stuck in "Pending" status.
- **Stalled:** No completions in 2x the time window, approval queue growing, no status changes.

Write 1-2 sentences summarizing the momentum assessment. Reference specific data points.

## Priority Focus Selection

Select the top 3 items requiring leadership attention. Prioritize in this order:

1. High-priority items stuck in Pending_Approval (blocked, needs decision)
2. High-priority items with stale status (no progress signal)
3. Items with missing metadata (ungoverned work)
4. Oldest unresolved items by Processed date

For each item, state: file name, why it needs attention, and recommended action.

## Briefing Template

Output in this exact structure:

```markdown
# CEO Briefing

**Generated:** YYYY-MM-DD HH:MM
**Window:** Last N days

---

## Portfolio Snapshot

- **Active Projects:** N
- **Pending Approval:** N
- **Completed (last N days):** N
- **Priority Mix:** N High / N Medium / N Low

## Autonomy Intelligence

| Metric | Value | Signal |
|--------|-------|--------|
| Autonomy Score | N/100 | 🟢/🟡/🟠/🔴 |
| Tasks Auto-Completed | N | — |
| HITL Rate | N% | [low/acceptable/high] |
| Execution Failures | N (N%) | [stable/rising] |
| Est. Hours Saved | Nh | — |

**Autonomy Trend:** [Improving / Stable / Declining] — [1 sentence with data point]

## Financial Efficiency

- **Revenue collected (period):** PKR N
- **Outstanding invoices:** N invoices, PKR N total
- **Overdue (>30 days):** N invoices — [action required / none]
- **Subscription waste detected:** [Yes — [service names] / None]
- **Financial efficiency score:** [Collected / (Collected + Outstanding)] × 100 = N%

## Risk Signals

- [Signal and brief explanation]
- [Signal and brief explanation]

## Bottlenecks

- [Bottleneck: specific file(s) and age]
- [Bottleneck: specific file(s) and age]

## Momentum

[1-2 sentence assessment with data points]

## Priority Focus

1. **[File name]** — [Why it needs attention]. Recommended action: [action].
2. **[File name]** — [Why it needs attention]. Recommended action: [action].
3. **[File name]** — [Why it needs attention]. Recommended action: [action].

## Strategic Actions (Next 7 Days)

1. **[Action]** — [Why, measurable outcome, owner: Human/AI]
2. **[Action]** — [Why, measurable outcome, owner: Human/AI]
3. **[Action]** — [Why, measurable outcome, owner: Human/AI]
```

## Save Behavior

- Default: print to chat only.
- If user says "save this" or "save the briefing": create `Briefings/` directory if needed, write to `Briefings/YYYY-MM-DD-ceo-briefing.md`.
- Never save unless explicitly requested.
