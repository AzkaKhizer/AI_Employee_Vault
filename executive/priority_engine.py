"""
executive/priority_engine.py — Executive Priority Engine (EPE)

Scores, ranks, and surfaces high-priority tasks across all domains
(finance, marketing, approval, future). Pure computation — no file
writes, no network calls, no external dependencies.

Design constraints:
  - Deterministic scoring (no randomness — same input always produces same score)
  - Fully DRY_RUN safe (read-only; no side effects)
  - Additive — does not modify any existing module
  - No external network calls
  - No schema removals

Usage:
    from executive.priority_engine import run_priority_cycle, TaskPriority

    summary = run_priority_cycle([
        {"task_id": "INV-101", "domain": "finance", "metadata": {...}},
        {"task_id": "POST_IG_001", "domain": "marketing", "metadata": {...}},
    ])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TaskPriority:
    """
    Represents a scoreable task from any domain.

    Attributes:
        task_id        : Unique task identifier (e.g. "INV-101", "POST_FB_001")
        domain         : Domain category: "finance" | "marketing" | "approval" | other
        metadata       : Arbitrary dict containing domain-specific scoring signals
        priority_score : Computed score (populated by calculate_priority)
        priority_level : "low" | "medium" | "high" | "critical" (derived from score)
        created_at     : ISO8601 timestamp used as tie-breaker in ranking
    """
    task_id:        str
    domain:         str
    metadata:       dict = field(default_factory=dict)
    priority_score: int  = 0
    priority_level: str  = "normal"
    created_at:     str  = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Priority level thresholds ─────────────────────────────────────────────────

def _score_to_level(score: int) -> str:
    """
    Map a raw score to a named priority level.

      score >= 9 → "critical"
      score 6–8  → "high"
      score 3–5  → "medium"
      score 0–2  → "low"
    """
    if score >= 9:
        return "critical"
    if score >= 6:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


# ── Scoring model ─────────────────────────────────────────────────────────────

def calculate_priority(task: TaskPriority) -> TaskPriority:
    """
    Apply the deterministic v1 scoring model to a TaskPriority object.

    Modifies task.priority_score and task.priority_level in-place and
    returns the same object for convenience.

    Domain-specific rules are applied first, then global rules.
    """
    score = 0
    meta  = task.metadata

    if task.domain == "finance":
        score += _score_finance(meta)
    elif task.domain == "marketing":
        score += _score_marketing(meta)
    elif task.domain == "approval":
        score += _score_approval(meta)
    # Unknown domains get no domain-specific points — only global rules apply.

    score += _score_global(meta)

    task.priority_score = max(0, score)
    task.priority_level = _score_to_level(task.priority_score)
    return task


def _score_finance(meta: dict) -> int:
    """
    Finance domain scoring rules.

    Risk level (mutually exclusive — highest band wins):
      +4  risk_level == "critical"
      +3  risk_level == "high"
      +2  risk_level == "medium"
      +1  risk_level == "low"

    Amount (cumulative):
      +3  amount > 10,000
      +2  amount > 5,000   (added above the >10k threshold)
      +1  amount > 1,000   (added above the >5k threshold)

    Days overdue (cumulative):
      +2  days_overdue >= 60
      +1  days_overdue >= 30
    """
    pts = 0

    # Risk level
    risk_map = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    pts += risk_map.get(str(meta.get("risk_level", "")).lower(), 0)

    # Amount (cumulative steps)
    amount = float(meta.get("amount", meta.get("amount_residual", 0)))
    if amount > 10_000:
        pts += 3
    elif amount > 5_000:
        pts += 2
    elif amount > 1_000:
        pts += 1

    # Days overdue (cumulative steps)
    days = int(meta.get("days_overdue", 0))
    if days >= 60:
        pts += 2
    elif days >= 30:
        pts += 1

    return pts


def _score_marketing(meta: dict) -> int:
    """
    Marketing domain scoring rules.

    Platform + topic relevance:
      +2  platform == "x" AND topic relates to "revenue" or "launch"

    Content gap:
      +1  social_drafts_created == 0 this period

    Approval staleness:
      +1  post awaiting approval > 3 days (days_pending > 3)
    """
    pts = 0

    platform = str(meta.get("platform", "")).lower()
    topic    = str(meta.get("topic", "")).lower()
    revenue_keywords = {"revenue", "launch", "sales", "growth", "profit"}

    if platform == "x" and any(kw in topic for kw in revenue_keywords):
        pts += 2

    if int(meta.get("social_drafts_created", -1)) == 0:
        pts += 1

    days_pending = int(meta.get("days_pending", 0))
    if days_pending > 3:
        pts += 1

    return pts


def _score_approval(meta: dict) -> int:
    """
    Approval domain scoring rules.

    Pending age (mutually exclusive — highest band wins):
      +3  days_pending > 3
      +2  days_pending > 1
    """
    days_pending = int(meta.get("days_pending", 0))
    if days_pending > 3:
        return 3
    if days_pending > 1:
        return 2
    return 0


def _score_global(meta: dict) -> int:
    """
    Global scoring rules applied to every domain.

      +2  flagged as "ceo_attention_required"
      +1  has repeated failure history (failure_count > 0)
    """
    pts = 0

    if meta.get("ceo_attention_required"):
        pts += 2

    if int(meta.get("failure_count", 0)) > 0:
        pts += 1

    return pts


# ── Task ranking ──────────────────────────────────────────────────────────────

def rank_tasks(tasks: list[TaskPriority]) -> list[TaskPriority]:
    """
    Sort tasks by priority_score descending.
    Tie-breaker: newest created_at first (lexicographic ISO8601 descending).

    Returns a NEW list — does not mutate the input.
    """
    return sorted(
        tasks,
        key=lambda t: (t.priority_score, t.created_at),
        reverse=True,
    )


# ── High-priority extraction ──────────────────────────────────────────────────

def extract_executive_alerts(tasks: list[TaskPriority]) -> dict:
    """
    Extract summary of critical/high tasks, capped at the top 5.

    Returns:
        {
          "critical_count": int,
          "high_count":     int,
          "top_tasks": [
              {"task_id": str, "domain": str, "priority_level": str, "score": int},
              ...  (max 5 entries)
          ]
        }
    """
    critical_count = sum(1 for t in tasks if t.priority_level == "critical")
    high_count     = sum(1 for t in tasks if t.priority_level == "high")

    top_tasks = [
        {
            "task_id":        t.task_id,
            "domain":         t.domain,
            "priority_level": t.priority_level,
            "score":          t.priority_score,
        }
        for t in tasks[:5]  # tasks already ranked descending
    ]

    return {
        "critical_count": critical_count,
        "high_count":     high_count,
        "top_tasks":      top_tasks,
    }


# ── Integration hook (Step 4) ─────────────────────────────────────────────────

def run_priority_cycle(raw_tasks: list[dict]) -> dict:
    """
    Full EPE pipeline from raw task dicts to a scored, ranked, alerting summary.

    raw_tasks schema:
        [
          {
            "task_id": str,       # required
            "domain":  str,       # required: "finance"|"marketing"|"approval"|other
            "metadata": dict,     # optional: domain scoring signals
          },
          ...
        ]

    Returns:
        {
          "total_tasks":      int,
          "critical_count":   int,
          "high_count":       int,
          "medium_count":     int,
          "low_count":        int,
          "top_tasks":        list[dict],   # top 5
          "ranked_task_ids":  list[str],    # full ranked order
          "generated_at":     str,          # ISO8601
        }
    """
    # Build TaskPriority objects — never mutate caller's dicts
    tasks = [
        TaskPriority(
            task_id  = str(raw.get("task_id", f"task-{i}")),
            domain   = str(raw.get("domain", "unknown")),
            metadata = dict(raw.get("metadata", {})),  # shallow copy
        )
        for i, raw in enumerate(raw_tasks)
    ]

    # Score
    for task in tasks:
        calculate_priority(task)

    # Rank (returns new list)
    ranked = rank_tasks(tasks)

    # Extract alerts from ranked list
    alerts = extract_executive_alerts(ranked)

    medium_count = sum(1 for t in ranked if t.priority_level == "medium")
    low_count    = sum(1 for t in ranked if t.priority_level == "low")

    return {
        "total_tasks":     len(ranked),
        "critical_count":  alerts["critical_count"],
        "high_count":      alerts["high_count"],
        "medium_count":    medium_count,
        "low_count":       low_count,
        "top_tasks":       alerts["top_tasks"],
        "ranked_task_ids": [t.task_id for t in ranked],
        "generated_at":    datetime.now(timezone.utc).isoformat(),
    }


# ── CEO briefing section renderer (Step 6 helper) ─────────────────────────────

def format_ceo_priority_section(summary: dict) -> str:
    """
    Render the ## Executive Priority Overview markdown section.
    Suitable for injection into CEO briefing output.
    """
    critical = summary.get("critical_count", 0)
    high     = summary.get("high_count", 0)
    top      = summary.get("top_tasks", [])

    if critical == 0 and high == 0:
        return (
            "## Executive Priority Overview\n\n"
            "No critical tasks at this time."
        )

    lines = [
        "## Executive Priority Overview",
        "",
        f"- **Critical Tasks:** {critical}",
        f"- **High Priority Tasks:** {high}",
        "",
        "### Top Executive Attention Items:",
    ]

    for i, t in enumerate(top, 1):
        lines.append(
            f"{i}. **{t['task_id']}** — {t['domain'].capitalize()} "
            f"— Score {t['score']} ({t['priority_level'].upper()})"
        )

    return "\n".join(lines)
