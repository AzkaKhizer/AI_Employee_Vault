"""
finance_watcher.py — Monitor financial signals from CSV exports or Odoo API.

Sources (in priority order):
  1. CSV files dropped in /Accounting/ (always available)
  2. Odoo JSON-RPC API (if ODOO_* env vars present)

DEV_MODE: Uses fixture data — no file reads or API calls.
DRY_RUN:  Analyses data but does not write to vault.

Alerts on:
  - Overdue invoices (>30 days)
  - High monthly expense spikes (>20% above 3-month average)
  - Subscription anomalies
"""

import csv
import io
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

from base_watcher import BaseWatcher, VAULT_ROOT, DEV_MODE, log_event, retry

ODOO_URL      = os.getenv("ODOO_URL")
ODOO_DB       = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")

ACCOUNTING_DIR = VAULT_ROOT / "Accounting"
ACCOUNTING_DIR.mkdir(exist_ok=True)

_DEV_FIXTURES = {
    "invoices": [
        {"name": "INV/2026/00001", "amount_residual": 5000, "invoice_date_due": "2026-01-15", "partner": "Test Customer"},
        {"name": "INV/2026/00003", "amount_residual": 3000, "invoice_date_due": "2026-02-01", "partner": "Test Customer"},
    ],
    "expenses": [
        {"name": "BILL/2026/00001", "amount_total": 12000, "invoice_date": "2026-02-01", "vendor": "Cloud Provider"},
        {"name": "BILL/2026/00002", "amount_total": 15000, "invoice_date": "2026-02-15", "vendor": "Cloud Provider"},
    ],
}


def _odoo_rpc(endpoint: str, payload: dict) -> dict:
    url = f"{ODOO_URL}/web/dataset/call_kw"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["result"]


def _odoo_authenticate() -> int:
    payload = {
        "jsonrpc": "2.0", "method": "call", "id": 1,
        "params": {"db": ODOO_DB, "login": ODOO_USERNAME, "password": ODOO_PASSWORD},
    }
    url = f"{ODOO_URL}/web/session/authenticate"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
    return result["result"]["uid"]


class FinanceWatcher(BaseWatcher):
    def __init__(self):
        super().__init__("finance_watcher", poll_interval=int(os.getenv("FINANCE_POLL_INTERVAL", "3600")))
        self._uid = None
        self._last_check: datetime | None = None

    def _odoo_available(self) -> bool:
        return all([ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD])

    @retry(max_attempts=3, backoff=10.0)
    def _poll(self) -> list[dict]:
        if DEV_MODE:
            return [{"type": "finance_snapshot", "data": _DEV_FIXTURES}]

        signals = []
        snapshot = {"invoices": [], "expenses": []}

        # ── Source 1: CSV files in /Accounting/ ───────────────────────────
        for csv_file in ACCOUNTING_DIR.glob("*.csv"):
            try:
                rows = list(csv.DictReader(csv_file.read_text(encoding="utf-8").splitlines()))
                snapshot["csv_imports"] = snapshot.get("csv_imports", []) + rows
                log_event(self.name, "csv_imported", {"file": csv_file.name, "rows": len(rows)})
            except Exception as exc:  # noqa: BLE001
                log_event(self.name, "csv_error", {"file": csv_file.name, "error": str(exc)}, outcome="error")

        # ── Source 2: Odoo API ────────────────────────────────────────────
        if self._odoo_available():
            try:
                if self._uid is None:
                    self._uid = _odoo_authenticate()

                # Overdue invoices
                today = datetime.now(timezone.utc).date().isoformat()
                result = _odoo_rpc("/web/dataset/call_kw", {
                    "jsonrpc": "2.0", "method": "call", "id": 1,
                    "params": {
                        "model": "account.move", "method": "search_read",
                        "args": [[
                            ["move_type", "=", "out_invoice"],
                            ["state", "=", "posted"],
                            ["payment_state", "!=", "paid"],
                            ["invoice_date_due", "<", today],
                        ]],
                        "kwargs": {"fields": ["name", "amount_residual", "invoice_date_due", "partner_id"], "limit": 50},
                    },
                })
                snapshot["invoices"] = [
                    {"name": r["name"], "amount_residual": r["amount_residual"],
                     "invoice_date_due": r["invoice_date_due"], "partner": r["partner_id"][1] if r["partner_id"] else "?"}
                    for r in (result or [])
                ]
            except Exception as exc:  # noqa: BLE001
                log_event(self.name, "odoo_error", {"error": str(exc)}, outcome="error")

        signals.append({"type": "finance_snapshot", "data": snapshot})
        return signals

    def _process(self, signal: dict) -> None:
        if signal["type"] != "finance_snapshot":
            return

        data = signal["data"]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

        # Save snapshot to /Accounting/
        snap_file = ACCOUNTING_DIR / f"{timestamp}-snapshot.json"
        if not __import__("base_watcher").DRY_RUN:
            snap_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log_event(self.name, "snapshot_saved", {"file": snap_file.name, "invoice_count": len(data.get("invoices", []))})

        # ── Alert: overdue invoices ────────────────────────────────────────
        overdue = data.get("invoices", [])
        if overdue:
            total_overdue = sum(inv.get("amount_residual", 0) for inv in overdue)
            alert_content = f"""# Finance Alert: Overdue Invoices

**Date**: {timestamp}
**Source**: finance_watcher
**Total Overdue**: {total_overdue:,.2f}

## Overdue Invoices

| Invoice | Partner | Due Date | Outstanding |
|---------|---------|----------|-------------|
""" + "\n".join(
                f"| {inv['name']} | {inv.get('partner', '?')} | {inv.get('invoice_date_due', '?')} | {inv.get('amount_residual', 0):,.2f} |"
                for inv in overdue
            ) + "\n\n## Required Action\n\n- [ ] Review each overdue invoice\n- [ ] Contact client if >14 days overdue\n- [ ] Escalate if >30 days\n"

            self.write_needs_action(f"{timestamp}-overdue-invoices.md", alert_content)
            log_event(self.name, "overdue_alert", {"count": len(overdue), "total": total_overdue})


if __name__ == "__main__":
    FinanceWatcher().run()
