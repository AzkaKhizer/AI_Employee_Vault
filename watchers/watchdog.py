"""
watchdog.py — Supervisor process that starts and restarts failed watchers.

Usage:
  python watchdog.py [--dry-run] [--dev]

All watcher processes are started as subprocesses. If one crashes,
it is restarted after a backoff period (max 5 restarts before giving up
and alerting via a Needs_Action file).

Structured events are logged to /Logs/YYYY-MM-DD.json.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

VAULT_ROOT = Path(os.getenv("VAULT_ROOT", Path(__file__).parent.parent))
LOG_DIR    = VAULT_ROOT / "Logs"
LOG_DIR.mkdir(exist_ok=True)

WATCHERS_DIR = Path(__file__).parent

WATCHER_SCRIPTS = [
    "filesystem_watcher.py",
    "gmail_watcher.py",
    "finance_watcher.py",
    # "whatsapp_watcher.py",  # Uncomment when Playwright session is configured
]

MAX_RESTARTS  = 5
RESTART_DELAY = 10  # seconds, doubles each restart


# ── Structured log ────────────────────────────────────────────────────────────

def log_event(event: str, payload: dict, outcome: str = "ok") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "watcher": "watchdog",
        "event": event,
        "outcome": outcome,
        **payload,
    }
    log_file = LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    existing = []
    if log_file.exists():
        try:
            existing = json.loads(log_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    existing.append(entry)
    log_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[watchdog] {event}: {payload}", flush=True)


def write_alert(script: str, restarts: int) -> None:
    """Write a Needs_Action alert when a watcher exceeds max restarts."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    alert_file = VAULT_ROOT / "Needs_Action" / f"{timestamp}-watcher-failure-{script}.md"
    content = f"""# Watcher Failure Alert: {script}

**Timestamp**: {datetime.now(timezone.utc).isoformat()}
**Watcher**: {script}
**Restarts attempted**: {restarts}
**Status**: STOPPED — max restarts exceeded

## Required Action

- [ ] Check watcher logs in /Logs/
- [ ] Investigate root cause of crash
- [ ] Fix and manually restart: `python watchers/{script}`
- [ ] Or re-enable in watchdog.py after fix
"""
    try:
        alert_file.write_text(content, encoding="utf-8")
        log_event("alert_written", {"file": alert_file.name, "watcher": script})
    except Exception as exc:  # noqa: BLE001
        log_event("alert_failed", {"error": str(exc)}, outcome="error")


# ── Process manager ───────────────────────────────────────────────────────────

class WatcherProcess:
    def __init__(self, script: str, env: dict):
        self.script  = script
        self.env     = env
        self.process: subprocess.Popen | None = None
        self.restarts = 0
        self.last_restart: float = 0.0
        self.active = True

    def start(self) -> None:
        script_path = WATCHERS_DIR / self.script
        self.process = subprocess.Popen(
            [sys.executable, str(script_path)],
            env=self.env,
            cwd=str(WATCHERS_DIR),
        )
        self.last_restart = time.monotonic()
        log_event("watcher_started", {"script": self.script, "pid": self.process.pid})

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def check_and_restart(self) -> None:
        if not self.active or self.is_alive():
            return
        returncode = self.process.returncode if self.process else -1
        log_event("watcher_died", {"script": self.script, "returncode": returncode}, outcome="error")

        if self.restarts >= MAX_RESTARTS:
            log_event("watcher_giving_up", {"script": self.script, "restarts": self.restarts}, outcome="error")
            write_alert(self.script, self.restarts)
            self.active = False
            return

        delay = RESTART_DELAY * (2 ** self.restarts)
        log_event("watcher_restarting", {"script": self.script, "delay_s": delay, "attempt": self.restarts + 1})
        time.sleep(delay)
        self.restarts += 1
        self.start()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AI Employee Vault — Watcher Supervisor")
    parser.add_argument("--dry-run", action="store_true", help="Enable DRY_RUN for all watchers")
    parser.add_argument("--dev",     action="store_true", help="Enable DEV_MODE for all watchers")
    args = parser.parse_args()

    env = os.environ.copy()
    env["VAULT_ROOT"] = str(VAULT_ROOT)
    if args.dry_run:
        env["DRY_RUN"] = "true"
    if args.dev:
        env["DEV_MODE"] = "true"

    log_event("watchdog_started", {
        "watchers": WATCHER_SCRIPTS,
        "dry_run": args.dry_run,
        "dev_mode": args.dev,
    })

    processes = [WatcherProcess(script, env) for script in WATCHER_SCRIPTS]
    for wp in processes:
        wp.start()

    try:
        while True:
            all_stopped = True
            for wp in processes:
                wp.check_and_restart()
                if wp.active:
                    all_stopped = False

            if all_stopped:
                log_event("all_watchers_stopped", {}, outcome="error")
                print("[watchdog] All watchers stopped. Check /Needs_Action/ for alerts.", flush=True)
                break

            time.sleep(15)

    except KeyboardInterrupt:
        log_event("watchdog_interrupted", {})
        print("\n[watchdog] Shutting down...", flush=True)
        for wp in processes:
            if wp.is_alive():
                wp.process.terminate()
                wp.process.wait(timeout=5)
        log_event("watchdog_stopped", {})


if __name__ == "__main__":
    main()
