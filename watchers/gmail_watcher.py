"""
gmail_watcher.py — Watch Gmail inbox for actionable emails.

Requires:
  GMAIL_CREDENTIALS  Path to OAuth2 credentials JSON (NOT stored in vault)
  GMAIL_TOKEN_PATH   Path to token.json (NOT stored in vault)

DEV_MODE: Uses fixture emails — no Gmail API calls.
DRY_RUN:  Reads Gmail but does not write to vault.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from base_watcher import BaseWatcher, DEV_MODE, DRY_RUN, log_event, retry

# ── Gmail API imports (optional — graceful degradation) ───────────────────────
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GMAIL_AVAILABLE = True
except ImportError:
    GMAIL_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# ── Fixtures for DEV_MODE ─────────────────────────────────────────────────────
_DEV_FIXTURES = [
    {
        "id": "dev-001",
        "from": "client@example.com",
        "subject": "Invoice follow-up",
        "snippet": "Can you send me the invoice for the last milestone?",
        "date": "2026-02-21T10:00:00Z",
    },
    {
        "id": "dev-002",
        "from": "vendor@supplier.com",
        "subject": "New proposal attached",
        "snippet": "Please review the attached proposal for Q2 services.",
        "date": "2026-02-21T11:00:00Z",
    },
]

# ── Keyword classification ────────────────────────────────────────────────────
_ACTION_KEYWORDS = {
    "invoice":     "accounting",
    "payment":     "accounting",
    "proposal":    "review",
    "urgent":      "urgent",
    "contract":    "legal",
    "meeting":     "calendar",
    "follow.up":   "followup",
    "review":      "review",
}

def _classify(subject: str, snippet: str) -> str:
    text = (subject + " " + snippet).lower()
    for pattern, category in _ACTION_KEYWORDS.items():
        if re.search(pattern, text):
            return category
    return "general"


class GmailWatcher(BaseWatcher):
    def __init__(self):
        super().__init__("gmail_watcher", poll_interval=int(os.getenv("GMAIL_POLL_INTERVAL", "120")))
        self._seen_ids: set[str] = set()
        self._service = None

    def _get_service(self):
        if not GMAIL_AVAILABLE:
            raise RuntimeError("google-api-python-client not installed. Run: pip install -r watchers/requirements.txt")
        creds_path = os.getenv("GMAIL_CREDENTIALS")
        token_path = os.getenv("GMAIL_TOKEN_PATH", "/tmp/gmail_token.json")
        if not creds_path:
            raise RuntimeError("GMAIL_CREDENTIALS env var not set")
        creds = None
        if Path(token_path).exists():
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                creds = flow.run_local_server(port=0)
            Path(token_path).write_text(creds.to_json())
        return build("gmail", "v1", credentials=creds)

    @retry(max_attempts=3, backoff=2.0)
    def _poll(self) -> list[dict]:
        if DEV_MODE:
            return [f for f in _DEV_FIXTURES if f["id"] not in self._seen_ids]

        if self._service is None:
            self._service = self._get_service()

        result = self._service.users().messages().list(
            userId="me", labelIds=["INBOX", "UNREAD"], maxResults=20
        ).execute()

        messages = result.get("messages", [])
        signals = []
        for msg in messages:
            msg_id = msg["id"]
            if msg_id in self._seen_ids:
                continue
            detail = self._service.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            signals.append({
                "id": msg_id,
                "from": headers.get("From", "unknown"),
                "subject": headers.get("Subject", "(no subject)"),
                "snippet": detail.get("snippet", ""),
                "date": headers.get("Date", ""),
            })
        return signals

    def _process(self, signal: dict) -> None:
        msg_id = signal["id"]
        self._seen_ids.add(msg_id)

        category = _classify(signal["subject"], signal["snippet"])
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", signal["subject"].lower())[:40]
        filename = f"{timestamp}-gmail-{slug}.md"

        content = f"""# Email Signal: {signal['subject']}

**Source**: Gmail
**From**: {signal['from']}
**Date**: {signal['date']}
**Category**: {category}
**Signal ID**: {msg_id}

## Snippet

{signal['snippet']}

## Required Action

- [ ] Review email content
- [ ] Determine response required
- [ ] Draft reply if needed (route through approval if external)
"""
        self.write_needs_action(filename, content)
        self.write_signal(f"{timestamp}-gmail-{msg_id}.json", signal)
        log_event(self.name, "email_processed", {"id": msg_id, "category": category, "subject": signal["subject"]})


if __name__ == "__main__":
    GmailWatcher().run()
