"""
executive/test_priority_engine.py — Unit tests for Executive Priority Engine.

Test groups:
  1. Finance scoring rules (multiple invoice scenarios)
  2. Marketing scoring rules
  3. Approval aging logic
  4. Priority level mapping boundaries
  5. Ranking order correctness
  6. Extraction of top-5 limit
  7. No mutation of original input list

No integration tests. No external calls. Pure computation.

Usage:
  python executive/test_priority_engine.py
  python -m pytest executive/test_priority_engine.py -v
"""

import sys
import copy
from datetime import datetime, timezone
from pathlib import Path

VAULT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(VAULT_ROOT))

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from executive.priority_engine import (
    TaskPriority,
    calculate_priority,
    rank_tasks,
    extract_executive_alerts,
    run_priority_cycle,
    format_ceo_priority_section,
    _score_to_level,
    _score_finance,
    _score_marketing,
    _score_approval,
    _score_global,
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


def _task(task_id: str, domain: str, **meta) -> TaskPriority:
    """Build a TaskPriority with given metadata kwargs."""
    return TaskPriority(task_id=task_id, domain=domain, metadata=meta)


# ── Test 1: Finance scoring rules ─────────────────────────────────────────────

def test_finance_scoring() -> None:
    print(f"\n{SEP}\n  1. Finance Scoring Rules\n{SEP}")

    # Risk level points (amount=0, days=0 so only risk contributes)
    risk_cases = [
        ("critical", 4),
        ("high",     3),
        ("medium",   2),
        ("low",      1),
        ("unknown",  0),
        ("",         0),
    ]
    for level, expected in risk_cases:
        pts = _score_finance({"risk_level": level, "amount": 0, "days_overdue": 0})
        _check(f"finance risk_level={level!r} -> +{expected}", pts == expected,
               f"got {pts}")

    # Amount points (risk=none, days=0)
    amount_cases = [
        (500,    0),
        (1001,   1),
        (5001,   2),
        (10001,  3),
    ]
    for amount, expected in amount_cases:
        pts = _score_finance({"amount": amount, "days_overdue": 0})
        _check(f"finance amount={amount} -> +{expected}", pts == expected,
               f"got {pts}")

    # Days overdue points (risk=none, amount=0)
    overdue_cases = [
        (0,  0),
        (29, 0),
        (30, 1),
        (59, 1),
        (60, 2),
        (91, 2),
    ]
    for days, expected in overdue_cases:
        pts = _score_finance({"days_overdue": days, "amount": 0})
        _check(f"finance days_overdue={days} -> +{expected}", pts == expected,
               f"got {pts}")

    # Combined: critical + >10k + 60 days = 4+3+2 = 9
    combined = _score_finance({"risk_level": "critical", "amount": 15000, "days_overdue": 65})
    _check("finance combined critical+15k+65d -> 9", combined == 9, f"got {combined}")

    # amount_residual fallback key
    pts = _score_finance({"amount_residual": 6000, "days_overdue": 0})
    _check("finance amount_residual fallback -> 2", pts == 2, f"got {pts}")

    # Full calculate_priority via TaskPriority
    t = _task("INV-001", "finance", risk_level="critical", amount=15000, days_overdue=65)
    calculate_priority(t)
    _check("finance full task: score=9", t.priority_score == 9, f"got {t.priority_score}")
    _check("finance full task: level=critical", t.priority_level == "critical",
           f"got {t.priority_level}")


# ── Test 2: Marketing scoring rules ───────────────────────────────────────────

def test_marketing_scoring() -> None:
    print(f"\n{SEP}\n  2. Marketing Scoring Rules\n{SEP}")

    # platform=x + revenue topic -> +2
    pts = _score_marketing({"platform": "x", "topic": "Q3 revenue results"})
    _check("marketing x+revenue topic -> +2", pts == 2, f"got {pts}")

    # platform=x + launch topic -> +2
    pts = _score_marketing({"platform": "x", "topic": "product launch announcement"})
    _check("marketing x+launch topic -> +2", pts == 2, f"got {pts}")

    # platform=x but unrelated topic -> 0
    pts = _score_marketing({"platform": "x", "topic": "team building activities"})
    _check("marketing x+unrelated topic -> 0", pts == 0, f"got {pts}")

    # platform=facebook + revenue -> 0 (only X gets the bonus)
    pts = _score_marketing({"platform": "facebook", "topic": "revenue growth record"})
    _check("marketing facebook+revenue -> 0", pts == 0, f"got {pts}")

    # social_drafts_created == 0 -> +1
    pts = _score_marketing({"social_drafts_created": 0})
    _check("marketing drafts=0 -> +1", pts == 1, f"got {pts}")

    # social_drafts_created > 0 -> 0
    pts = _score_marketing({"social_drafts_created": 3})
    _check("marketing drafts=3 -> +0", pts == 0, f"got {pts}")

    # days_pending > 3 -> +1
    pts = _score_marketing({"days_pending": 4})
    _check("marketing days_pending=4 -> +1", pts == 1, f"got {pts}")

    # days_pending == 3 -> 0 (strictly greater than)
    pts = _score_marketing({"days_pending": 3})
    _check("marketing days_pending=3 -> +0", pts == 0, f"got {pts}")

    # Combined: x + revenue + drafts=0 + pending=5 -> 2+1+1 = 4
    pts = _score_marketing({
        "platform": "x", "topic": "revenue launch",
        "social_drafts_created": 0, "days_pending": 5,
    })
    _check("marketing combined x+rev+drafts0+pending5 -> 4", pts == 4, f"got {pts}")


# ── Test 3: Approval aging logic ──────────────────────────────────────────────

def test_approval_scoring() -> None:
    print(f"\n{SEP}\n  3. Approval Aging Logic\n{SEP}")

    cases = [
        (0, 0),
        (1, 0),
        (2, 2),   # > 1 day
        (3, 2),   # > 1 but not > 3
        (4, 3),   # > 3 days
        (10, 3),  # > 3 days
    ]
    for days, expected in cases:
        pts = _score_approval({"days_pending": days})
        _check(f"approval days_pending={days} -> +{expected}", pts == expected,
               f"got {pts}")

    # Full task
    t = _task("APPROVAL-007", "approval", days_pending=5)
    calculate_priority(t)
    _check("approval full task: score=3", t.priority_score == 3,
           f"got {t.priority_score}")
    _check("approval full task: level=medium", t.priority_level == "medium",
           f"got {t.priority_level}")


# ── Test 4: Priority level mapping boundaries ──────────────────────────────────

def test_level_mapping() -> None:
    print(f"\n{SEP}\n  4. Priority Level Mapping Boundaries\n{SEP}")

    cases = [
        (0,  "low"),
        (1,  "low"),
        (2,  "low"),
        (3,  "medium"),
        (4,  "medium"),
        (5,  "medium"),
        (6,  "high"),
        (7,  "high"),
        (8,  "high"),
        (9,  "critical"),
        (10, "critical"),
        (15, "critical"),
    ]
    for score, expected in cases:
        got = _score_to_level(score)
        _check(f"score={score:>2} -> {expected}", got == expected, f"got {got}")


# ── Test 5: Ranking order correctness ─────────────────────────────────────────

def test_ranking() -> None:
    print(f"\n{SEP}\n  5. Ranking Order Correctness\n{SEP}")

    tasks = [
        _task("LOW-1",      "approval", days_pending=0),
        _task("CRITICAL-1", "finance",  risk_level="critical", amount=15000, days_overdue=65),
        _task("MEDIUM-1",   "finance",  risk_level="medium",   amount=500,   days_overdue=0),
        _task("HIGH-1",     "finance",  risk_level="high",     amount=6000,  days_overdue=31),
    ]

    for t in tasks:
        calculate_priority(t)

    ranked = rank_tasks(tasks)

    # Should NOT mutate original list order
    _check("original list not mutated", tasks[0].task_id == "LOW-1")

    # Check ranked order by task_id
    ranked_ids = [t.task_id for t in ranked]
    _check("first is CRITICAL-1", ranked_ids[0] == "CRITICAL-1",
           f"got {ranked_ids[0]}")
    _check("CRITICAL before HIGH",
           ranked_ids.index("CRITICAL-1") < ranked_ids.index("HIGH-1"))
    _check("HIGH before MEDIUM",
           ranked_ids.index("HIGH-1") < ranked_ids.index("MEDIUM-1"))
    _check("MEDIUM before LOW",
           ranked_ids.index("MEDIUM-1") < ranked_ids.index("LOW-1"))

    # Scores are non-increasing
    scores = [t.priority_score for t in ranked]
    _check("scores non-increasing", all(scores[i] >= scores[i+1] for i in range(len(scores)-1)),
           f"scores={scores}")

    # Tie-breaker: same score, newer task wins
    t1 = TaskPriority("OLD", "approval", {"days_pending": 2},
                      created_at="2026-01-01T00:00:00+00:00")
    t2 = TaskPriority("NEW", "approval", {"days_pending": 2},
                      created_at="2026-02-01T00:00:00+00:00")
    calculate_priority(t1)
    calculate_priority(t2)
    _check("tie-breaker: same score, same pts",
           t1.priority_score == t2.priority_score,
           f"t1={t1.priority_score} t2={t2.priority_score}")
    tie_ranked = rank_tasks([t1, t2])
    _check("tie-breaker: newer task first",
           tie_ranked[0].task_id == "NEW",
           f"first was {tie_ranked[0].task_id}")


# ── Test 6: Top-5 extraction limit ────────────────────────────────────────────

def test_top5_extraction() -> None:
    print(f"\n{SEP}\n  6. Top-5 Extraction Limit\n{SEP}")

    # Build 8 tasks with varying scores
    raw = [
        {"task_id": f"T-{i:02d}", "domain": "finance",
         "metadata": {"risk_level": "critical", "amount": 15000, "days_overdue": 65}}
        for i in range(8)
    ]
    summary = run_priority_cycle(raw)

    _check("total_tasks == 8", summary["total_tasks"] == 8,
           f"got {summary['total_tasks']}")
    _check("top_tasks capped at 5", len(summary["top_tasks"]) == 5,
           f"got {len(summary['top_tasks'])}")
    _check("all 8 in ranked_task_ids", len(summary["ranked_task_ids"]) == 8,
           f"got {len(summary['ranked_task_ids'])}")

    # All critical -> critical_count == 8
    _check("critical_count == 8", summary["critical_count"] == 8,
           f"got {summary['critical_count']}")
    _check("high_count == 0",    summary["high_count"] == 0,
           f"got {summary['high_count']}")

    # Empty list
    empty = run_priority_cycle([])
    _check("empty input: total_tasks=0", empty["total_tasks"] == 0)
    _check("empty input: top_tasks=[]",  empty["top_tasks"] == [])

    # top_tasks dict keys
    if summary["top_tasks"]:
        top = summary["top_tasks"][0]
        for key in ("task_id", "domain", "priority_level", "score"):
            _check(f"top_task has key '{key}'", key in top, f"missing from {list(top.keys())}")


# ── Test 7: No mutation of original input ────────────────────────────────────

def test_no_mutation() -> None:
    print(f"\n{SEP}\n  7. No Mutation of Original Input\n{SEP}")

    original_raw = [
        {"task_id": "INV-MUT", "domain": "finance",
         "metadata": {"risk_level": "high", "amount": 6000, "days_overdue": 45}},
        {"task_id": "POST-MUT", "domain": "marketing",
         "metadata": {"platform": "x", "topic": "revenue launch"}},
    ]
    # Deep copy to compare later
    snapshot = copy.deepcopy(original_raw)

    _ = run_priority_cycle(original_raw)

    _check("raw_tasks list length unchanged", len(original_raw) == len(snapshot))
    for i, (orig, snap) in enumerate(zip(original_raw, snapshot)):
        _check(f"raw_tasks[{i}] task_id unchanged",
               orig["task_id"] == snap["task_id"])
        _check(f"raw_tasks[{i}] metadata unchanged",
               orig["metadata"] == snap["metadata"])

    # rank_tasks returns new list
    t1 = _task("R1", "finance", risk_level="critical", amount=20000, days_overdue=90)
    t2 = _task("R2", "finance", risk_level="low",      amount=100,   days_overdue=0)
    calculate_priority(t1)
    calculate_priority(t2)
    original_order = [t1, t2]
    ranked = rank_tasks(original_order)
    _check("rank_tasks returns new list", ranked is not original_order)
    _check("original order preserved",    original_order[0].task_id == "R1")

    # calculate_priority does not mutate metadata
    t = _task("META-SAFE", "finance", risk_level="medium", amount=3000, days_overdue=10)
    meta_before = dict(t.metadata)
    calculate_priority(t)
    _check("calculate_priority: metadata unchanged", t.metadata == meta_before)


# ── Bonus: CEO section rendering ──────────────────────────────────────────────

def test_ceo_section_renders() -> None:
    print(f"\n{SEP}\n  Bonus: CEO Priority Section Rendering\n{SEP}")

    # With critical + high tasks
    summary = run_priority_cycle([
        {"task_id": "INV-CEO-1", "domain": "finance",
         "metadata": {"risk_level": "critical", "amount": 15000, "days_overdue": 65}},
        {"task_id": "APPR-CEO-1", "domain": "approval",
         "metadata": {"days_pending": 5, "ceo_attention_required": True}},
        {"task_id": "POST-CEO-1", "domain": "marketing",
         "metadata": {"platform": "x", "topic": "revenue launch", "days_pending": 4}},
    ])
    section = format_ceo_priority_section(summary)
    _check("section contains header",    "## Executive Priority Overview" in section)
    _check("section contains critical count", str(summary["critical_count"]) in section)
    _check("section contains task list", "INV-CEO-1" in section or "Top Executive" in section)
    _check("section renders without crash", True)

    # No critical/high
    low_summary = run_priority_cycle([
        {"task_id": "LOW-1", "domain": "finance",
         "metadata": {"risk_level": "low", "amount": 100, "days_overdue": 0}},
    ])
    low_section = format_ceo_priority_section(low_summary)
    _check("no critical: fallback message shown",
           "No critical tasks at this time." in low_section)

    # Empty
    empty_section = format_ceo_priority_section(run_priority_cycle([]))
    _check("empty: fallback message shown",
           "No critical tasks at this time." in empty_section)


# ── Global rules ──────────────────────────────────────────────────────────────

def test_global_rules() -> None:
    print(f"\n{SEP}\n  Global Scoring Rules\n{SEP}")

    # ceo_attention_required -> +2
    pts = _score_global({"ceo_attention_required": True})
    _check("ceo_attention_required=True -> +2", pts == 2, f"got {pts}")

    pts = _score_global({"ceo_attention_required": False})
    _check("ceo_attention_required=False -> 0", pts == 0, f"got {pts}")

    pts = _score_global({})
    _check("ceo_attention_required absent -> 0", pts == 0, f"got {pts}")

    # failure_count > 0 -> +1
    pts = _score_global({"failure_count": 1})
    _check("failure_count=1 -> +1", pts == 1, f"got {pts}")

    pts = _score_global({"failure_count": 5})
    _check("failure_count=5 -> +1 (not cumulative)", pts == 1, f"got {pts}")

    pts = _score_global({"failure_count": 0})
    _check("failure_count=0 -> 0", pts == 0, f"got {pts}")

    # Combined: ceo + failure -> +3
    pts = _score_global({"ceo_attention_required": True, "failure_count": 3})
    _check("ceo+failure combined -> +3", pts == 3, f"got {pts}")

    # Global rules stack on top of finance rules
    t = _task("CEO-FIN", "finance",
              risk_level="high", amount=6000, days_overdue=31,
              ceo_attention_required=True, failure_count=2)
    calculate_priority(t)
    # high=3, amount>5k=2, days>=30=1, ceo=2, failure=1 -> 9
    _check("finance+global combined -> score=9",
           t.priority_score == 9, f"got {t.priority_score}")
    _check("finance+global combined -> critical",
           t.priority_level == "critical", f"got {t.priority_level}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{SEP}")
    print(f"  Executive Priority Engine — Unit Test Suite")
    print(f"  Vault: {VAULT_ROOT}")
    print(SEP)

    test_finance_scoring()
    test_marketing_scoring()
    test_approval_scoring()
    test_level_mapping()
    test_ranking()
    test_top5_extraction()
    test_no_mutation()
    test_global_rules()
    test_ceo_section_renders()

    print(f"\n{SEP}")
    total = PASS_COUNT + FAIL_COUNT
    print(f"  Results: {PASS_COUNT}/{total} PASS  |  {FAIL_COUNT} FAIL")
    if FAIL_COUNT == 0:
        print(f"  ALL TESTS PASS")
    else:
        print(f"  FAILURES DETECTED -- review above")
    print(SEP + "\n")

    sys.exit(0 if FAIL_COUNT == 0 else 1)


if __name__ == "__main__":
    main()
