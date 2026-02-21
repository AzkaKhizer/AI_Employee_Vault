"""
base_watcher.py — Abstract base class for all AI Employee Vault watchers.

All watchers inherit from BaseWatcher and implement:
  - _poll() → list[dict]   : fetch new signals from source
  - _process(signal)       : write to vault (Needs_Action / Signals)

Features:
  - Structured JSON logging to /Logs/YYYY-MM-DD.json
  - DEV_MODE (no external calls, uses fixtures)
  - DRY_RUN (no vault writes)
  - Retry with exponential backoff
  - Never crashes silently
"""

import abc
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path


# ── Environment ───────────────────────────────────────────────────────────────

VAULT_ROOT = Path(os.getenv("VAULT_ROOT", Path(__file__).parent.parent))
DRY_RUN    = os.getenv("DRY_RUN", "false").lower() == "true"
DEV_MODE   = os.getenv("DEV_MODE", "false").lower() == "true"

LOG_DIR    = VAULT_ROOT / "Logs"
LOG_DIR.mkdir(exist_ok=True)

# ── Structured logger ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)


def _log_path() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"{date_str}.json"


def log_event(watcher_name: str, event_type: str, payload: dict, outcome: str = "ok") -> None:
    """Append a structured JSON log entry."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "watcher": watcher_name,
        "event": event_type,
        "outcome": outcome,
        "dry_run": DRY_RUN,
        "dev_mode": DEV_MODE,
        **payload,
    }
    try:
        log_file = _log_path()
        existing = []
        if log_file.exists():
            try:
                existing = json.loads(log_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = []
        existing.append(entry)
        log_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(watcher_name).error("Failed to write log: %s", exc)


# ── Retry decorator ───────────────────────────────────────────────────────────

def retry(max_attempts: int = 3, backoff: float = 2.0, exceptions: tuple = (Exception,)):
    """Decorator: retry with exponential backoff, log each failure."""
    def decorator(fn):
        def wrapper(self, *args, **kwargs):
            delay = 1.0
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(self, *args, **kwargs)
                except exceptions as exc:
                    log_event(self.name, "retry", {"attempt": attempt, "error": str(exc)}, outcome="retrying")
                    if attempt == max_attempts:
                        log_event(self.name, "retry_exhausted", {"error": str(exc)}, outcome="error")
                        raise
                    time.sleep(delay)
                    delay *= backoff
        return wrapper
    return decorator


# ── BaseWatcher ───────────────────────────────────────────────────────────────

class BaseWatcher(abc.ABC):
    """
    Abstract base for all vault watchers.

    Subclasses must implement:
      _poll()          → list[dict]  raw signals from source
      _process(signal) → None        write structured task to vault
    """

    def __init__(self, name: str, poll_interval: int = 60):
        self.name = name
        self.poll_interval = poll_interval
        self.logger = logging.getLogger(name)
        self._running = False

    # ── Abstract interface ─────────────────────────────────────────────────

    @abc.abstractmethod
    def _poll(self) -> list[dict]:
        """Fetch new signals. Must be idempotent."""

    @abc.abstractmethod
    def _process(self, signal: dict) -> None:
        """Write signal to vault (Needs_Action / Signals). Respects DRY_RUN."""

    # ── Vault helpers ──────────────────────────────────────────────────────

    def write_needs_action(self, filename: str, content: str) -> None:
        """Write a task file to /Needs_Action/. No-op in DRY_RUN."""
        target = VAULT_ROOT / "Needs_Action" / filename
        if DRY_RUN:
            log_event(self.name, "write_needs_action", {"file": filename, "skipped": True}, outcome="dry_run")
            return
        target.write_text(content, encoding="utf-8")
        log_event(self.name, "write_needs_action", {"file": filename}, outcome="ok")

    def write_signal(self, filename: str, payload: dict) -> None:
        """Write a signal file to /Signals/. No-op in DRY_RUN."""
        signals_dir = VAULT_ROOT / "Signals"
        signals_dir.mkdir(exist_ok=True)
        target = signals_dir / filename
        if DRY_RUN:
            log_event(self.name, "write_signal", {"file": filename, "skipped": True}, outcome="dry_run")
            return
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        log_event(self.name, "write_signal", {"file": filename}, outcome="ok")

    # ── Main loop ──────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the polling loop. Runs until stop() is called or process exits."""
        self._running = True
        self.logger.info("Started (DEV_MODE=%s DRY_RUN=%s interval=%ds)", DEV_MODE, DRY_RUN, self.poll_interval)
        log_event(self.name, "started", {"poll_interval": self.poll_interval})

        while self._running:
            try:
                signals = self._poll()
                log_event(self.name, "poll", {"count": len(signals)})
                for signal in signals:
                    try:
                        self._process(signal)
                    except Exception as exc:  # noqa: BLE001
                        log_event(self.name, "process_error", {"signal": signal, "error": str(exc)}, outcome="error")
                        self.logger.exception("Error processing signal: %s", signal)
            except Exception as exc:  # noqa: BLE001
                log_event(self.name, "poll_error", {"error": str(exc)}, outcome="error")
                self.logger.exception("Poll error — will retry after backoff")
                time.sleep(min(self.poll_interval * 2, 300))
                continue

            time.sleep(self.poll_interval)

        log_event(self.name, "stopped", {})
        self.logger.info("Stopped.")

    def stop(self) -> None:
        self._running = False
