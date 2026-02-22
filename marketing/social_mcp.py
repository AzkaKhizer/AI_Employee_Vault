"""
marketing/social_mcp.py — Social Media MCP Abstraction Layer

Design constraints:
  - NO auto-publishing. All drafts route to /Pending_Approval/ for HITL review.
  - NO real API keys or external network calls — stub-ready structure.
  - Fully DRY_RUN aware.
  - Respects circuit breaker before any simulated MCP call.
  - All writes are logged via structured _social_log().
"""

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

VAULT_ROOT       = Path(os.getenv("VAULT_ROOT", Path(__file__).parent.parent))
PENDING_APPROVAL = VAULT_ROOT / "Pending_Approval"
LOG_DIR          = VAULT_ROOT / "Logs"
DRY_RUN          = os.getenv("DRY_RUN", "false").lower() == "true"

# Bootstrap path for shared modules
sys.path.insert(0, str(VAULT_ROOT))


# ── Platform enum ─────────────────────────────────────────────────────────────

class SocialPlatform(Enum):
    FACEBOOK  = "facebook"
    INSTAGRAM = "instagram"
    X         = "x"


# ── Circuit breaker guard ──────────────────────────────────────────────────────

def _circuit_is_open() -> bool:
    """
    Check the orchestrator circuit breaker state without importing the full
    module (avoids side-effects on import). Returns True if circuit is open.
    """
    try:
        import orchestrator as _orch
        state = _orch._cb_check(dry_run=True)  # read-only check, no state change
        return state == "open"
    except Exception:
        return False  # If orchestrator unavailable, allow through (graceful)


# ── MCP stub layer ─────────────────────────────────────────────────────────────

def create_social_draft(
    platform: SocialPlatform,
    content: str,
    dry_run: bool = False,
) -> dict:
    """
    Simulate an MCP call to create a social media draft on the given platform.

    Returns a structured result dict:
        {
            "platform":   str,
            "draft_id":   str,
            "status":     "draft_created" | "dry_run_skipped" | "circuit_blocked",
            "timestamp":  ISO8601 str,
            "content_length": int,
        }

    Guards:
      - circuit_blocked → returns status "circuit_blocked", no log write
      - dry_run → returns status "dry_run_skipped", logs intent
      - live → simulates MCP call, logs "social_draft_created"
    """
    ts = datetime.now(timezone.utc).isoformat()

    # Circuit breaker guard
    if _circuit_is_open():
        _social_log("social_mcp_circuit_blocked", {
            "platform": platform.value,
            "reason":   "circuit_open",
        })
        return {
            "platform":       platform.value,
            "draft_id":       "",
            "status":         "circuit_blocked",
            "timestamp":      ts,
            "content_length": len(content),
        }

    draft_id = f"draft-{platform.value}-{uuid.uuid4().hex[:12]}"

    if dry_run:
        _social_log("social_draft_dry_run", {
            "platform":       platform.value,
            "draft_id":       draft_id,
            "content_length": len(content),
        })
        return {
            "platform":       platform.value,
            "draft_id":       draft_id,
            "status":         "dry_run_skipped",
            "timestamp":      ts,
            "content_length": len(content),
        }

    # ── Simulated MCP call (stub — replace with real SDK call when ready) ────
    # In production: call platform API via MCP tool (e.g., facebook_mcp.create_draft)
    # For now: structured simulation with deterministic draft_id
    _social_log("social_draft_created", {
        "platform":       platform.value,
        "draft_id":       draft_id,
        "content_length": len(content),
        "status":         "draft_created",
    })

    return {
        "platform":       platform.value,
        "draft_id":       draft_id,
        "status":         "draft_created",
        "timestamp":      ts,
        "content_length": len(content),
    }


# ── Pending Approval file writer ──────────────────────────────────────────────

def create_post_approval_file(
    platform: SocialPlatform,
    content: str,
    topic: str,
    draft_result: dict,
    dry_run: bool = False,
) -> Path | None:
    """
    Write POST_{platform}_{timestamp}.md to /Pending_Approval/ for human review.

    In dry_run mode: logs intent only, returns None.
    Returns the Path written, or None (dry_run / circuit blocked).
    """
    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"POST_{platform.value.upper()}_{ts_slug}.md"
    dest = PENDING_APPROVAL / filename

    if dry_run:
        _social_log("post_approval_file_dry_run", {
            "platform":    platform.value,
            "topic":       topic,
            "would_write": str(dest),
        })
        return None

    char_count = len(content)
    draft_id   = draft_result.get("draft_id", "N/A")
    generated  = draft_result.get("timestamp", datetime.now(timezone.utc).isoformat())

    content_md = f"""# Social Post Approval Required

**Platform:**   {platform.value.upper()}
**Topic:**      {topic}
**Draft ID:**   {draft_id}
**Characters:** {char_count}
**Generated:**  {generated[:19]}Z
**Status:**     Awaiting Approval

---

## Draft Content

{content}

---

## Action Required

Review the draft content above. To approve:
1. Confirm the content is accurate and on-brand.
2. Move this file to `/Approved/` to queue for posting.
3. The social executor will handle publishing on next cycle.

> **Important:** This system does NOT auto-post. Human approval is mandatory.

*Generated by Social Media MCP Integration — {datetime.now(timezone.utc).isoformat()[:19]}Z*
"""

    PENDING_APPROVAL.mkdir(exist_ok=True)
    dest.write_text(content_md, encoding="utf-8")
    _social_log("post_approval_file_created", {
        "platform": platform.value,
        "topic":    topic,
        "file":     filename,
        "draft_id": draft_id,
    })
    return dest


# ── Full social cycle orchestration (Step 6) ──────────────────────────────────

def run_social_cycle(
    topic: str,
    platforms: list,
    dry_run: bool = False,
) -> dict:
    """
    Run a full social draft cycle for the given topic across all platforms.

    For each platform:
      1. Generate content (via social_content.generate_social_post)
      2. Create MCP draft stub
      3. Write /Pending_Approval/POST_*.md

    Returns a summary dict:
        {
            "topic":          str,
            "dry_run":        bool,
            "platforms_run":  list[str],
            "drafts_created": int,
            "files_created":  list[str],
            "circuit_blocked": list[str],
            "results":        list[dict],
        }
    """
    from marketing.social_content import generate_social_post

    results        = []
    files_created  = []
    circuit_blocked = []
    drafts_created = 0

    for platform in platforms:
        content = generate_social_post(topic, platform)
        draft   = create_social_draft(platform, content, dry_run=dry_run)

        if draft["status"] == "circuit_blocked":
            circuit_blocked.append(platform.value)
            results.append({**draft, "topic": topic, "file": None})
            continue

        path = create_post_approval_file(platform, content, topic, draft, dry_run=dry_run)

        if draft["status"] == "draft_created":
            drafts_created += 1

        if path:
            files_created.append(str(path))

        results.append({
            **draft,
            "topic":   topic,
            "file":    str(path) if path else None,
            "content": content,
        })

    summary = {
        "topic":            topic,
        "dry_run":          dry_run,
        "platforms_run":    [p.value for p in platforms],
        "drafts_created":   drafts_created,
        "files_created":    files_created,
        "circuit_blocked":  circuit_blocked,
        "results":          results,
    }

    _social_log("social_cycle_complete", {
        "topic":           topic,
        "platforms":       [p.value for p in platforms],
        "drafts_created":  drafts_created,
        "files_created":   len(files_created),
        "circuit_blocked": len(circuit_blocked),
        "dry_run":         dry_run,
    })

    return summary


# ── Internal helpers ──────────────────────────────────────────────────────────

def _social_log(event: str, payload: dict) -> None:
    """Append a structured log entry to today's vault log file."""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        log_file = LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        existing = []
        if log_file.exists():
            try:
                existing = json.loads(log_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = []
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source":    "social_mcp",
            "event":     event,
            **payload,
        }
        existing.append(entry)
        log_file.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # Never crash the caller on logging failure
