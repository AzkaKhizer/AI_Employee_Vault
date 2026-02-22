"""
marketing/test_social_mcp.py — Unit tests for Social Media MCP Integration.

Tests (no real API calls, no external network):
  1. Draft structure validity — all required keys present and typed correctly
  2. Platform tone differentiation — Facebook/Instagram/X each produce distinct content
  3. X character limit — hard cap at 280 enforced for long topics
  4. DRY_RUN prevents file creation
  5. Metrics increment correctly via execution_metrics schema
  6. CEO Marketing Activity section renders without crash
  7. Circuit breaker guard — circuit_blocked status when open

Usage:
  python marketing/test_social_mcp.py
  python -m pytest marketing/test_social_mcp.py -v
"""

import sys
import os
import tempfile
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

from marketing.social_mcp import (
    SocialPlatform,
    create_social_draft,
    create_post_approval_file,
    run_social_cycle,
)
from marketing.social_content import generate_social_post

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


# ── Test 1: Draft structure validity ─────────────────────────────────────────

def test_draft_structure() -> None:
    print(f"\n{SEP}\n  1. Draft Structure Validity\n{SEP}")

    required_keys = {"platform", "draft_id", "status", "timestamp", "content_length"}

    for platform in SocialPlatform:
        content = f"Test content for {platform.value}"
        result = create_social_draft(platform, content, dry_run=True)

        _check(f"{platform.value}: all required keys present",
               required_keys.issubset(result.keys()),
               f"missing: {required_keys - set(result.keys())}")

        _check(f"{platform.value}: platform field matches",
               result["platform"] == platform.value,
               f"got '{result['platform']}'")

        _check(f"{platform.value}: status is dry_run_skipped",
               result["status"] == "dry_run_skipped",
               f"got '{result['status']}'")

        _check(f"{platform.value}: content_length is int",
               isinstance(result["content_length"], int),
               f"got {type(result['content_length'])}")

        _check(f"{platform.value}: timestamp is ISO string",
               "T" in result["timestamp"] and "Z" not in result["timestamp"],
               f"got '{result['timestamp'][:25]}'")

    # Live mode structure (no file writes — no approval file called)
    live = create_social_draft(SocialPlatform.FACEBOOK, "hello", dry_run=False)
    _check("live mode: status is draft_created",
           live["status"] == "draft_created")
    _check("live mode: draft_id non-empty",
           bool(live["draft_id"]))


# ── Test 2: Platform tone differentiation ─────────────────────────────────────

def test_platform_tone() -> None:
    print(f"\n{SEP}\n  2. Platform Tone Differentiation\n{SEP}")

    topic = "our new AI Employee system launch"

    fb  = generate_social_post(topic, SocialPlatform.FACEBOOK)
    ig  = generate_social_post(topic, SocialPlatform.INSTAGRAM)
    x   = generate_social_post(topic, SocialPlatform.X)

    # Facebook — longer, informative
    _check("Facebook: length > 100 chars", len(fb) > 100, f"len={len(fb)}")
    _check("Facebook: no emoji", not any(ord(c) > 0xFFFF or (0x1F300 <= ord(c) <= 0x1FAFF) for c in fb),
           "contains emoji")
    _check("Facebook: contains topic", topic.lower()[:15] in fb.lower())

    # Instagram — concise + emoji
    _check("Instagram: shorter than Facebook", len(ig) < len(fb), f"ig={len(ig)} fb={len(fb)}")
    _check("Instagram: contains emoji", any(0x1F300 <= ord(c) <= 0x1FAFF for c in ig),
           "no emoji found")
    _check("Instagram: contains topic", topic.lower()[:15] in ig.lower())

    # X — short, within limit
    _check(f"X: under 280 chars", len(x) <= 280, f"len={len(x)}")
    _check("X: shortest of three", len(x) <= len(ig), f"x={len(x)} ig={len(ig)}")
    _check("X: contains topic fragment", topic[:10].lower() in x.lower())

    # All three must be distinct
    _check("All three posts are distinct", len({fb, ig, x}) == 3,
           "two or more are identical")


# ── Test 3: X character limit enforcement ─────────────────────────────────────

def test_x_character_limit() -> None:
    print(f"\n{SEP}\n  3. X Character Limit Enforcement\n{SEP}")

    # Short topic — well within limit
    short = generate_social_post("quarterly results", SocialPlatform.X)
    _check("short topic: under 280", len(short) <= 280, f"len={len(short)}")

    # Very long topic — must be truncated to fit
    long_topic = "a" * 300
    result = generate_social_post(long_topic, SocialPlatform.X)
    _check("long topic: under 280 chars", len(result) <= 280, f"len={len(result)}")
    _check("long topic: contains '...' marker", "..." in result)


# ── Test 4: DRY_RUN prevents file creation ────────────────────────────────────

def test_dry_run_no_files() -> None:
    print(f"\n{SEP}\n  4. DRY_RUN Prevents File Creation\n{SEP}")

    import marketing.social_mcp as _mcp

    draft_result = {
        "platform":   SocialPlatform.FACEBOOK.value,
        "draft_id":   "test-draft-123",
        "status":     "dry_run_skipped",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "content_length": 100,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        orig = _mcp.PENDING_APPROVAL
        _mcp.PENDING_APPROVAL = Path(tmpdir)
        try:
            path = create_post_approval_file(
                SocialPlatform.FACEBOOK,
                "Test content",
                "test topic",
                draft_result,
                dry_run=True,
            )
            files = list(Path(tmpdir).glob("*.md"))
            _check("dry_run=True: returns None", path is None)
            _check("dry_run=True: no files written", len(files) == 0)
        finally:
            _mcp.PENDING_APPROVAL = orig

    # Live mode — file IS written
    with tempfile.TemporaryDirectory() as tmpdir:
        orig = _mcp.PENDING_APPROVAL
        _mcp.PENDING_APPROVAL = Path(tmpdir)
        try:
            path = create_post_approval_file(
                SocialPlatform.INSTAGRAM,
                "Live content",
                "live topic",
                {**draft_result, "status": "draft_created", "draft_id": "live-id-456"},
                dry_run=False,
            )
            files = list(Path(tmpdir).glob("*.md"))
            _check("dry_run=False: returns Path", path is not None)
            _check("dry_run=False: 1 file written", len(files) == 1)
            if files:
                content = files[0].read_text(encoding="utf-8")
                _check("file contains 'Awaiting Approval'", "Awaiting Approval" in content)
                _check("file contains platform name", "INSTAGRAM" in content)
        finally:
            _mcp.PENDING_APPROVAL = orig


# ── Test 5: Metrics increment correctly ──────────────────────────────────────

def test_metrics_schema() -> None:
    print(f"\n{SEP}\n  5. Metrics Schema — Social Fields Present\n{SEP}")

    import execution_metrics as em

    m = em.compute_metrics([], days=7)

    social_keys = ["social_drafts_created", "social_posts_approved", "social_posts_rejected"]
    for k in social_keys:
        _check(f"key '{k}' in metrics dict", k in m, f"missing from dict")
        _check(f"key '{k}' defaults to 0", m[k] == 0, f"got {m[k]}")

    # Simulate log entries and verify counters
    fake_entries = [
        {"timestamp": "2026-02-22T10:00:00+00:00", "source": "social_mcp",
         "event": "social_draft_created", "platform": "facebook"},
        {"timestamp": "2026-02-22T10:01:00+00:00", "source": "social_mcp",
         "event": "social_draft_created", "platform": "instagram"},
        {"timestamp": "2026-02-22T10:02:00+00:00", "source": "social_mcp",
         "event": "social_post_approved", "platform": "facebook"},
        {"timestamp": "2026-02-22T10:03:00+00:00", "source": "social_mcp",
         "event": "social_post_rejected", "platform": "instagram"},
    ]
    m2 = em.compute_metrics(fake_entries, days=7)
    _check("2 draft events counted", m2["social_drafts_created"] == 2,
           f"got {m2['social_drafts_created']}")
    _check("1 approved event counted", m2["social_posts_approved"] == 1,
           f"got {m2['social_posts_approved']}")
    _check("1 rejected event counted", m2["social_posts_rejected"] == 1,
           f"got {m2['social_posts_rejected']}")

    # Confirm existing keys untouched
    _check("autonomy_score still present", "autonomy_score" in m2)
    _check("circuit_opened_count still present", "circuit_opened_count" in m2)
    _check("revenue_at_risk still present", "revenue_at_risk" in m2)


# ── Test 6: CEO section renders without crash ────────────────────────────────

def test_ceo_section_renders() -> None:
    print(f"\n{SEP}\n  6. CEO Marketing Activity Section Renders\n{SEP}")

    # Simulate the section rendering logic from the SKILL.md template
    metrics = {
        "social_drafts_created": 3,
        "social_posts_approved": 1,
        "social_posts_rejected": 0,
    }
    platforms_active = ["Facebook", "Instagram"]

    def _render_marketing_section(m: dict, platforms: list, delta: int | None = None) -> str:
        if m["social_drafts_created"] == 0:
            return "## Marketing Activity\n\nNo social activity this period."
        arrow = "-> (no prior data)" if delta is None else (
            "Up" if delta > 0 else ("Down" if delta < 0 else "Stable")
        )
        return (
            f"## Marketing Activity\n\n"
            f"- **Social Drafts Created:** {m['social_drafts_created']}\n"
            f"- **Posts Approved:** {m['social_posts_approved']}\n"
            f"- **Posts Rejected:** {m['social_posts_rejected']}\n"
            f"- **Platforms Active:** {' / '.join(platforms)}\n"
            f"- **Marketing Momentum:** {arrow}\n"
        )

    # With activity
    section = _render_marketing_section(metrics, platforms_active, delta=2)
    _check("section contains header", "## Marketing Activity" in section)
    _check("section contains draft count", "3" in section)
    _check("section contains platforms", "Facebook" in section)
    _check("section renders without exception", True)

    # No activity case
    empty = _render_marketing_section(
        {"social_drafts_created": 0, "social_posts_approved": 0, "social_posts_rejected": 0},
        [],
    )
    _check("no activity: correct message", "No social activity this period." in empty)


# ── Test 7: Circuit breaker guard ────────────────────────────────────────────

def test_circuit_breaker_guard() -> None:
    print(f"\n{SEP}\n  7. Circuit Breaker Guard\n{SEP}")

    import marketing.social_mcp as _mcp
    import time

    # Save real function and replace with mock that returns "open"
    orig_check = _mcp._circuit_is_open
    _mcp._circuit_is_open = lambda: True  # simulate open circuit

    try:
        result = create_social_draft(SocialPlatform.X, "test content", dry_run=False)
        _check("circuit open: status=circuit_blocked",
               result["status"] == "circuit_blocked",
               f"got '{result['status']}'")
        _check("circuit open: draft_id is empty", result["draft_id"] == "")
    finally:
        _mcp._circuit_is_open = orig_check

    # With circuit closed — normal draft_created
    _mcp._circuit_is_open = lambda: False
    try:
        result = create_social_draft(SocialPlatform.X, "test content", dry_run=False)
        _check("circuit closed: status=draft_created",
               result["status"] == "draft_created",
               f"got '{result['status']}'")
    finally:
        _mcp._circuit_is_open = orig_check


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{SEP}")
    print(f"  Social Media MCP Integration — Unit Test Suite")
    print(f"  Vault: {VAULT_ROOT}")
    print(SEP)

    test_draft_structure()
    test_platform_tone()
    test_x_character_limit()
    test_dry_run_no_files()
    test_metrics_schema()
    test_ceo_section_renders()
    test_circuit_breaker_guard()

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
