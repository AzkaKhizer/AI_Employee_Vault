"""
whatsapp_watcher.py — Watch WhatsApp Web for new messages using Playwright.

Requires:
  WHATSAPP_SESSION_DIR  Directory to store Playwright browser session (NOT in vault)

DEV_MODE: Uses fixture messages — no browser launched.
DRY_RUN:  Reads messages but does not write to vault.

Install: pip install playwright && playwright install chromium
"""

import os
import re
from datetime import datetime, timezone

from base_watcher import BaseWatcher, DEV_MODE, log_event, retry

# ── Playwright import (graceful degradation) ──────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

SESSION_DIR = os.getenv("WHATSAPP_SESSION_DIR", "/tmp/whatsapp_session")

_DEV_FIXTURES = [
    {"sender": "Ahmad Khan", "text": "Hey, did you send the invoice?", "time": "10:32"},
    {"sender": "Sara Bilal", "text": "Project update needed ASAP", "time": "11:05"},
]


class WhatsAppWatcher(BaseWatcher):
    def __init__(self):
        super().__init__("whatsapp_watcher", poll_interval=int(os.getenv("WA_POLL_INTERVAL", "60")))
        self._seen: set[str] = set()
        self._browser = None
        self._page = None

    def _start_browser(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")
        pw = sync_playwright().start()
        self._browser = pw.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,  # WhatsApp Web requires visible browser for QR scan initially
            args=["--no-sandbox"],
        )
        self._page = self._browser.pages[0] if self._browser.pages else self._browser.new_page()
        self._page.goto("https://web.whatsapp.com", timeout=30_000)
        # Wait for chat list to appear (user scans QR on first run)
        self._page.wait_for_selector('[data-testid="chat-list"]', timeout=60_000)
        log_event(self.name, "browser_ready", {"session_dir": SESSION_DIR})

    @retry(max_attempts=3, backoff=5.0, exceptions=(Exception,))
    def _poll(self) -> list[dict]:
        if DEV_MODE:
            return [m for m in _DEV_FIXTURES if m["sender"] not in self._seen]

        if self._page is None:
            self._start_browser()

        messages = []
        try:
            # Find all unread chat badges
            unread_chats = self._page.query_selector_all('[data-testid="icon-unread-count"]')
            for badge in unread_chats:
                # Click parent chat row
                chat_row = badge.evaluate_handle("el => el.closest('[data-testid=\"cell-frame-container\"]')")
                if not chat_row:
                    continue
                chat_row.click()
                self._page.wait_for_timeout(800)

                # Extract sender name
                sender_el = self._page.query_selector('[data-testid="conversation-info-header-chat-title"]')
                sender = sender_el.inner_text() if sender_el else "Unknown"

                # Extract last few messages
                msg_els = self._page.query_selector_all('[data-testid="msg-container"]')
                for msg_el in msg_els[-5:]:
                    text_el = msg_el.query_selector('[data-testid="conversation-compose-box-input"], .copyable-text')
                    text = text_el.inner_text() if text_el else ""
                    key = f"{sender}:{text[:30]}"
                    if text and key not in self._seen:
                        messages.append({"sender": sender, "text": text, "time": datetime.now(timezone.utc).isoformat()})
                        self._seen.add(key)
        except PlaywrightTimeout as exc:
            log_event(self.name, "poll_timeout", {"error": str(exc)}, outcome="error")

        return messages

    def _process(self, signal: dict) -> None:
        sender = signal["sender"]
        self._seen.add(sender)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", sender.lower())[:30]
        filename = f"{timestamp}-whatsapp-{slug}.md"

        content = f"""# WhatsApp Signal: {sender}

**Source**: WhatsApp Web
**Sender**: {sender}
**Time**: {signal.get('time', timestamp)}

## Message

{signal['text']}

## Required Action

- [ ] Review message
- [ ] Determine if response needed
- [ ] Draft reply (route through approval if client-facing)
"""
        self.write_needs_action(filename, content)
        self.write_signal(f"{timestamp}-whatsapp-{slug}.json", signal)
        log_event(self.name, "message_processed", {"sender": sender, "length": len(signal["text"])})

    def stop(self):
        super().stop()
        if self._browser:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    WhatsAppWatcher().run()
