"""
finance/test_revenue_protection.py — Unit tests for Revenue Protection Engine.

Tests (no integration / no Odoo / no file I/O unless DRY_RUN patched):
  1. Aging categorization — all bucket boundaries
  2. Revenue at risk — sum of invoices with days_overdue >= 31
  3. Risk scoring — all aging + amount combinations
  4. Draft tone selection — correct opener per risk level
  5. build_aging_summary — aggregation correctness
  6. DRY_RUN gate — no files created in dry_run mode

Usage:
  python -m pytest finance/test_revenue_protection.py -v
  # or without pytest:
  python finance/test_revenue_protection.py
"""

import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

VAULT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(VAULT_ROOT))

from finance.revenue_protection import (
    categorize_aging_bucket,
    build_aging_summary,
    calculate_revenue_at_risk,
    calculate_risk_score,
    generate_reminder_draft,
    create_reminder_approval_file,
    _days_overdue,
    _amount,
    _score_to_level,
)

SEP = "=" * 60
PASS_COUNT = 0
FAIL_COUNT = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail and not condition else ""
    print(f"  [{status}] {label}{suffix}")
    if condition:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _inv(days_overdue: int, amount: float, number: str = "INV/001") -> dict:
    """Build a minimal invoice dict with a computed due date."""
    due = (datetime.now(timezone.utc) - timedelta(days=days_overdue)).strftime("%Y-%m-%d")
    return {
        "invoice_number":   number,
        "partner_name":     "Test Customer",
        "amount_residual":  amount,
        "invoice_date_due": due,
    }


# ── Test 1: Aging categorization ──────────────────────────────────────────────

def test_aging_categorization() -> None:
    print(f"\n{SEP}\n  1. Aging Categorization\n{SEP}")

    cases = [
        (0,   "current"),
        (1,   "current"),
        (30,  "current"),
        (31,  "warning"),
        (60,  "warning"),
        (61,  "high"),
        (90,  "high"),
        (91,  "critical"),
        (120, "critical"),
        (365, "critical"),
    ]
    for days, expected in cases:
        got = categorize_aging_bucket(days)
        _check(
            f"days={days:>3} -> {expected}",
            got == expected,
            f"got '{got}'"
        )


# ── Test 2: Revenue at risk ────────────────────────────────────────────────────

def test_revenue_at_risk() -> None:
    print(f"\n{SEP}\n  2. Revenue At Risk\n{SEP}")

    invoices = [
        _inv(0,   500.00, "INV/001"),   # current — excluded
        _inv(15,  300.00, "INV/002"),   # current — excluded
        _inv(31,  1000.00, "INV/003"),  # warning — included
        _inv(65,  2500.00, "INV/004"),  # high    — included
        _inv(95,  7500.00, "INV/005"),  # critical — included
    ]

    at_risk = calculate_revenue_at_risk(invoices)
    expected = 1000.00 + 2500.00 + 7500.00
    _check(f"revenue_at_risk = {expected}", abs(at_risk - expected) < 0.01, f"got {at_risk}")

    # All current → zero at risk
    current_only = [_inv(10, 500.0), _inv(20, 300.0)]
    _check("all current → 0 at risk", calculate_revenue_at_risk(current_only) == 0.0)

    # Empty list
    _check("empty list → 0 at risk", calculate_revenue_at_risk([]) == 0.0)


# ── Test 3: Risk scoring ──────────────────────────────────────────────────────

def test_risk_scoring() -> None:
    print(f"\n{SEP}\n  3. Risk Scoring\n{SEP}")

    # Aging-only checks
    aging_cases = [
        (0,    0, "low"),
        (1,    1, "low"),
        (30,   1, "low"),
        (31,   2, "low"),
        (60,   2, "low"),
        (61,   3, "medium"),
        (90,   3, "medium"),
        (91,   4, "medium"),
    ]
    for days, expected_base_score, _ in aging_cases:
        inv = _inv(days, 0.0)  # amount=0 → 0 amount points
        rs = calculate_risk_score(inv)
        _check(
            f"aging only, days={days:>3} → score={expected_base_score}",
            rs["score"] == expected_base_score,
            f"got score={rs['score']}"
        )

    # Amount-only checks (days=0 → 0 aging points)
    amount_cases = [
        (500.0,    0, "low"),
        (1001.0,   1, "low"),
        (5001.0,   2, "low"),
        (10001.0,  3, "medium"),
    ]
    for amount, expected_pts, _ in amount_cases:
        inv = _inv(0, amount)
        rs = calculate_risk_score(inv)
        _check(
            f"amount only, {amount:>8,.0f} → score={expected_pts}",
            rs["score"] == expected_pts,
            f"got score={rs['score']}"
        )

    # Combined: critical = 91+ days + >10k
    inv = _inv(91, 10001.0)
    rs = calculate_risk_score(inv)
    _check(
        "combined 91d+10001 → score=7, critical",
        rs["score"] == 7 and rs["risk_level"] == "critical",
        f"got score={rs['score']} level={rs['risk_level']}"
    )

    # Score-to-level mapping
    level_cases = [(0, "low"), (2, "low"), (3, "medium"), (4, "medium"),
                   (5, "high"), (6, "high"), (7, "critical"), (10, "critical")]
    for score, expected in level_cases:
        got = _score_to_level(score)
        _check(f"score {score} → {expected}", got == expected, f"got {got}")


# ── Test 4: Draft tone selection ──────────────────────────────────────────────

def test_draft_tone() -> None:
    print(f"\n{SEP}\n  4. Draft Tone Selection\n{SEP}")

    tone_map = {
        "low":      "friendly",
        "medium":   "firm",
        "high":     "urgent",
        "critical": "escalation",
    }
    for risk_level, expected_tone in tone_map.items():
        inv = _inv(45, 2000.0, f"INV/TONE_{risk_level.upper()}")
        draft = generate_reminder_draft(inv, risk_level)
        _check(
            f"risk_level={risk_level} → tone contains '{expected_tone}'",
            expected_tone.lower() in draft.lower(),
            f"draft snippet: {draft[:80]!r}"
        )

    # Drafts must include invoice number and amount
    inv = _inv(40, 3500.0, "INV/2026/00042")
    for rl in ("low", "medium", "high", "critical"):
        draft = generate_reminder_draft(inv, rl)
        _check(
            f"draft({rl}) contains invoice number",
            "INV/2026/00042" in draft
        )
        _check(
            f"draft({rl}) contains amount",
            "3,500.00" in draft
        )


# ── Test 5: build_aging_summary ───────────────────────────────────────────────

def test_aging_summary() -> None:
    print(f"\n{SEP}\n  5. Aging Summary Aggregation\n{SEP}")

    invoices = [
        _inv(10,  100.0, "INV/A1"),
        _inv(25,  200.0, "INV/A2"),  # both current
        _inv(35,  300.0, "INV/B1"),  # warning
        _inv(50,  400.0, "INV/B2"),  # warning
        _inv(70,  500.0, "INV/C1"),  # high
        _inv(95, 1000.0, "INV/D1"),  # critical
    ]

    summary = build_aging_summary(invoices)

    _check("current count=2",   summary["current"]["count"]  == 2)
    _check("current amount=300", abs(summary["current"]["amount"]  - 300.0) < 0.01)
    _check("warning count=2",   summary["warning"]["count"]  == 2)
    _check("warning amount=700", abs(summary["warning"]["amount"]  - 700.0) < 0.01)
    _check("high count=1",      summary["high"]["count"]     == 1)
    _check("high amount=500",   abs(summary["high"]["amount"]     - 500.0) < 0.01)
    _check("critical count=1",  summary["critical"]["count"] == 1)
    _check("critical amount=1000", abs(summary["critical"]["amount"] - 1000.0) < 0.01)

    # Empty invoices
    empty = build_aging_summary([])
    for bucket in ("current", "warning", "high", "critical"):
        _check(f"empty → {bucket}.count=0", empty[bucket]["count"] == 0)
        _check(f"empty → {bucket}.amount=0", empty[bucket]["amount"] == 0.0)


# ── Test 6: DRY_RUN gate ──────────────────────────────────────────────────────

def test_dry_run_gate() -> None:
    print(f"\n{SEP}\n  6. DRY_RUN Gate — No Files Created\n{SEP}")

    import tempfile, os
    from unittest.mock import patch

    inv = _inv(95, 15000.0, "INV/DRY/001")
    rs  = calculate_risk_score(inv)
    _check("setup: risk_level is critical", rs["risk_level"] == "critical")

    # Patch PENDING_APPROVAL to a temp dir, then call with dry_run=True
    with tempfile.TemporaryDirectory() as tmpdir:
        import finance.revenue_protection as rpe
        orig = rpe.PENDING_APPROVAL
        rpe.PENDING_APPROVAL = Path(tmpdir)
        try:
            result = create_reminder_approval_file(inv, rs, dry_run=True)
            files_written = list(Path(tmpdir).glob("*.md"))
            _check("dry_run=True → returns None",   result is None)
            _check("dry_run=True → no files written", len(files_written) == 0)
        finally:
            rpe.PENDING_APPROVAL = orig

    # Confirm live mode would write (with patched PENDING_APPROVAL)
    with tempfile.TemporaryDirectory() as tmpdir:
        import finance.revenue_protection as rpe
        orig = rpe.PENDING_APPROVAL
        rpe.PENDING_APPROVAL = Path(tmpdir)
        try:
            result = create_reminder_approval_file(inv, rs, dry_run=False)
            files_written = list(Path(tmpdir).glob("*.md"))
            _check("dry_run=False → returns Path",  result is not None)
            _check("dry_run=False → 1 file written", len(files_written) == 1)
        finally:
            rpe.PENDING_APPROVAL = orig


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{SEP}")
    print(f"  Revenue Protection Engine — Unit Test Suite")
    print(f"  Vault: {VAULT_ROOT}")
    print(SEP)

    test_aging_categorization()
    test_revenue_at_risk()
    test_risk_scoring()
    test_draft_tone()
    test_aging_summary()
    test_dry_run_gate()

    print(f"\n{SEP}")
    total = PASS_COUNT + FAIL_COUNT
    print(f"  Results: {PASS_COUNT}/{total} PASS  |  {FAIL_COUNT} FAIL")
    if FAIL_COUNT == 0:
        print(f"  ALL TESTS PASS")
    else:
        print(f"  FAILURES DETECTED — review above")
    print(SEP + "\n")

    sys.exit(0 if FAIL_COUNT == 0 else 1)


if __name__ == "__main__":
    main()
