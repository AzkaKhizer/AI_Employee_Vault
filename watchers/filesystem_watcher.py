"""
filesystem_watcher.py — Watch /Inbox/ for new markdown files and route to /Needs_Action/.

This is the primary intake watcher. Any markdown file dropped in /Inbox/ is:
  1. Read and classified
  2. Moved to /Needs_Action/ with a structured header injected
  3. Logged

DEV_MODE: Creates a fixture file in /Inbox/ then processes it.
DRY_RUN:  Reads and classifies but does not move files.
"""

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from base_watcher import BaseWatcher, VAULT_ROOT, DEV_MODE, DRY_RUN, log_event

INBOX_DIR       = VAULT_ROOT / "Inbox"
NEEDS_ACTION_DIR = VAULT_ROOT / "Needs_Action"
DONE_DIR        = VAULT_ROOT / "Done"

INBOX_DIR.mkdir(exist_ok=True)
NEEDS_ACTION_DIR.mkdir(exist_ok=True)
DONE_DIR.mkdir(exist_ok=True)

# Keywords → priority classification
_PRIORITY_RULES = {
    r"\burgent\b":    "P1",
    r"\bblocking\b":  "P1",
    r"\bcritical\b":  "P1",
    r"\bASAP\b":      "P1",
    r"\bclient\b":    "P2",
    r"\binvoice\b":   "P2",
    r"\bmeeting\b":   "P2",
    r"\bdeadline\b":  "P2",
}

_CATEGORY_RULES = {
    r"\binvoice\b|\bpayment\b|\baccounting\b": "accounting",
    r"\bemail\b|\breply\b|\brespond\b":         "communication",
    r"\bmeeting\b|\bcalendar\b|\bschedule\b":   "calendar",
    r"\bproposal\b|\bcontract\b|\blegal\b":     "legal",
    r"\breport\b|\banalysis\b|\bsummary\b":     "reporting",
}


def _classify(text: str) -> tuple[str, str]:
    lower = text.lower()
    priority = "P3"
    for pattern, p in _PRIORITY_RULES.items():
        if re.search(pattern, lower, re.IGNORECASE):
            priority = p
            break
    category = "general"
    for pattern, c in _CATEGORY_RULES.items():
        if re.search(pattern, lower, re.IGNORECASE):
            category = c
            break
    return priority, category


class FilesystemWatcher(BaseWatcher):
    def __init__(self):
        super().__init__("filesystem_watcher", poll_interval=int(os.getenv("FS_POLL_INTERVAL", "30")))
        self._processed: set[str] = set()

        if DEV_MODE:
            _fixture = INBOX_DIR / "dev-fixture-task.md"
            if not _fixture.exists():
                _fixture.write_text(
                    "# Client invoice follow-up\n\nClient has not paid the invoice from last month. Urgent: follow up immediately.\n",
                    encoding="utf-8",
                )

    def _poll(self) -> list[dict]:
        files = [
            f for f in INBOX_DIR.glob("*.md")
            if f.name not in self._processed
        ]
        return [{"path": str(f), "name": f.name} for f in files]

    def _process(self, signal: dict) -> None:
        src = Path(signal["path"])
        if not src.exists():
            return

        content = src.read_text(encoding="utf-8")
        priority, category = _classify(content)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", src.stem.lower())[:40]
        dest_name = f"{timestamp}-inbox-{slug}.md"

        # Inject structured header
        header = f"""---
source: inbox
priority: {priority}
category: {category}
ingested_at: {datetime.now(timezone.utc).isoformat()}
original_file: {src.name}
---

"""
        enriched = header + content

        if DRY_RUN:
            log_event(self.name, "file_classified", {
                "file": src.name, "priority": priority, "category": category, "skipped": True,
            }, outcome="dry_run")
        else:
            dest = NEEDS_ACTION_DIR / dest_name
            dest.write_text(enriched, encoding="utf-8")
            src.unlink()  # Remove from Inbox after routing
            log_event(self.name, "file_routed", {
                "source": src.name, "dest": dest_name, "priority": priority, "category": category,
            })

        self._processed.add(signal["name"])


if __name__ == "__main__":
    FilesystemWatcher().run()
