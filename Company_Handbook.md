# Company Handbook — AI Employee Vault

## 1. Company Mission

Demonstrate that an AI agent can function as an autonomous employee — receiving tasks, processing them into structured plans, completing work, and maintaining a live dashboard — using only an Obsidian vault and coordinated skills.

## 2. Operating Principles

- **Autonomy first.** The AI Employee processes and routes work without manual intervention.
- **Transparency.** Every action produces an audit trail with timestamps embedded in files.
- **Determinism.** File routing and priority classification use rule-based logic. No guesswork.
- **No fluff.** All outputs are concise and decision-oriented.

## 3. AI Employee Responsibilities

| Responsibility | Skill | How It Works |
|---|---|---|
| Monitor Inbox for new files | vault-watcher | Continuously polls `Inbox/`, moves new `.md` files to `Needs_Action/` |
| Process tasks into structured plans | vault-processor | Reads files in `Needs_Action/`, adds priority + checklist, routes to `Done/` |
| Maintain live dashboard | vault-processor | Auto-regenerates `Dashboard.md` after every change |

## 4. Folder Definitions

| Folder | Purpose | Items Enter Via | Items Leave Via |
|---|---|---|---|
| **Inbox/** | Raw intake. User drops ideas, notes, or requests here. | User | vault-watcher moves to Needs_Action/ |
| **Needs_Action/** | Processing queue. Files awaiting AI structuring. | vault-watcher or direct user input | vault-processor routes to Done/ |
| **Done/** | Completed work. Archived with processing timestamps. | vault-processor | Terminal state |

## 5. How It Works

### Automated Intake

```
User drops file in Inbox/
         │
         ▼
   vault-watcher (runs continuously)
   ├── Detects new .md file
   ├── Moves to Needs_Action/
   └── Logs the action with timestamp
```

### Task Processing

```
User says "Process my Needs_Action folder"
         │
         ▼
   vault-processor
   ├── Reads each .md file
   ├── Assigns priority (keyword-based: High / Medium / Low)
   ├── Converts to structured format:
   │     ├── Title
   │     ├── Priority tag
   │     ├── Processing timestamp
   │     ├── Checklist of action items
   │     └── Original notes preserved
   ├── Routes to Done/
   └── Rebuilds Dashboard.md
```

### Priority Classification

| Priority | Keywords |
|----------|----------|
| High | `urgent`, `asap`, `critical`, `immediately`, `deadline` |
| Low | `low priority`, `when possible`, `no rush`, `backlog` |
| Medium | Default — no keyword match |

### Dashboard

`Dashboard.md` is auto-generated after every processing run. It shows:

- File counts per folder (Inbox, Needs_Action, Done)
- Tables of all tracked items with priority and timestamps
- Single source of truth for vault state

## 6. Running the AI Employee

**Start the watcher:**
```bash
PYTHONUTF8=1 python .claude/skills/vault-watcher/scripts/watch_inbox.py .
```

**Process tasks:**
Say "Process my Needs_Action folder" to trigger vault-processor.

**Check status:**
Open `Dashboard.md` in Obsidian for a live summary.
