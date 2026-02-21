"""
orchestrator.py — AI Employee Vault orchestrator.

Responsibilities:
  1. Monitor /Needs_Action/ for new task files
  2. Claim tasks via atomic move to /In_Progress/<agent-id>-<file>
  3. Invoke Claude CLI to process the task
  4. Move completed tasks to /Done/
  5. Enforce max iterations (Ralph Wiggum loop guard)
  6. Write structured audit logs

Usage:
  python orchestrator.py [--dry-run] [--dev] [--once]

Flags:
  --dry-run   Log what would happen but do not claim, invoke, or move files
  --dev       DEV_MODE: use mock Claude response instead of real CLI
  --once      Process one cycle then exit (useful for cron/testing)
  --max-iter  Max Claude invocations per task (default: 10)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ── Config ────────────────────────────────────────────────────────────────────

VAULT_ROOT       = Path(os.getenv("VAULT_ROOT", Path(__file__).parent))
NEEDS_ACTION_DIR = VAULT_ROOT / "Needs_Action"
IN_PROGRESS_DIR  = VAULT_ROOT / "In_Progress"
DONE_DIR         = VAULT_ROOT / "Done"
LOG_DIR          = VAULT_ROOT / "Logs"

for d in [NEEDS_ACTION_DIR, IN_PROGRESS_DIR, DONE_DIR, LOG_DIR]:
    d.mkdir(exist_ok=True)

AGENT_ID     = os.getenv("AGENT_ID", f"orchestrator-{uuid.uuid4().hex[:8]}")
POLL_SECONDS = int(os.getenv("ORCH_POLL_INTERVAL", "30"))
MAX_CLAIM_AGE_MINUTES = int(os.getenv("ORCH_MAX_CLAIM_AGE", "30"))

CLAUDE_CLI_CMD = os.getenv("CLAUDE_CLI", "claude")  # Path to Claude Code binary


# ── Structured logger ─────────────────────────────────────────────────────────

def log_event(event: str, payload: dict, outcome: str = "ok", dry_run: bool = False) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": AGENT_ID,
        "event": event,
        "outcome": outcome,
        "dry_run": dry_run,
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
    print(f"[{AGENT_ID}] {event}: {json.dumps(payload)}", flush=True)


# ── Claim-by-move ─────────────────────────────────────────────────────────────

def claim_task(task_file: Path, dry_run: bool) -> Path | None:
    """
    Atomically move task from /Needs_Action/ to /In_Progress/<agent-id>-<file>.
    Returns the claimed path, or None if another agent claimed it first.
    """
    claimed_name = f"{AGENT_ID}-{task_file.name}"
    claimed_path = IN_PROGRESS_DIR / claimed_name

    if dry_run:
        log_event("claim_dry_run", {"file": task_file.name, "would_claim": claimed_name}, dry_run=True)
        return None  # Do not actually move in dry_run

    try:
        task_file.rename(claimed_path)
        log_event("claimed", {"file": task_file.name, "claimed_as": claimed_name})
        return claimed_path
    except (FileNotFoundError, OSError):
        # Another agent claimed it — not an error
        log_event("claim_missed", {"file": task_file.name}, outcome="skipped")
        return None


def release_task(claimed_path: Path, success: bool, dry_run: bool) -> None:
    """Move claimed task to /Done/ or back to /Needs_Action/ on failure."""
    if dry_run:
        return
    dest_dir = DONE_DIR if success else NEEDS_ACTION_DIR
    dest = dest_dir / claimed_path.name.replace(f"{AGENT_ID}-", "", 1)
    try:
        claimed_path.rename(dest)
        log_event("released", {"file": claimed_path.name, "dest": str(dest_dir.name), "success": success})
    except OSError as exc:
        log_event("release_failed", {"file": claimed_path.name, "error": str(exc)}, outcome="error")


# ── Stale claim recovery ──────────────────────────────────────────────────────

def reclaim_stale(dry_run: bool) -> None:
    """Move files stuck in /In_Progress/ longer than MAX_CLAIM_AGE_MINUTES back to /Needs_Action/."""
    now = datetime.now(timezone.utc).timestamp()
    for f in IN_PROGRESS_DIR.glob("*.md"):
        age_minutes = (now - f.stat().st_mtime) / 60
        if age_minutes > MAX_CLAIM_AGE_MINUTES:
            log_event("stale_reclaim", {"file": f.name, "age_minutes": round(age_minutes, 1)})
            if not dry_run:
                dest_name = f.name
                # Strip any agent prefix
                for part in dest_name.split("-"):
                    if "orchestrator" in part:
                        dest_name = dest_name[len(part) + 1:]
                        break
                dest = NEEDS_ACTION_DIR / dest_name
                try:
                    f.rename(dest)
                except OSError as exc:
                    log_event("stale_reclaim_failed", {"file": f.name, "error": str(exc)}, outcome="error")


# ── Claude invocation ─────────────────────────────────────────────────────────

def invoke_claude(task_content: str, task_name: str, max_iter: int, dev_mode: bool, dry_run: bool) -> tuple[bool, str]:
    """
    Invoke Claude CLI with the task content.
    Returns (success, output_text).

    Ralph Wiggum loop: each invocation may produce sub-tasks; Claude
    is invoked iteratively until the task resolves or max_iter is reached.
    """
    if dry_run:
        log_event("invoke_dry_run", {"task": task_name}, dry_run=True)
        return True, "DRY_RUN: no Claude invocation"

    if dev_mode:
        log_event("invoke_dev_mock", {"task": task_name})
        return True, f"[DEV_MODE] Mock completion of: {task_name}"

    prompt = f"""You are the AI Employee processing a vault task.

Task file: {task_name}

Task content:
---
{task_content}
---

Process this task using available skills and MCP tools.
Produce a structured completion report.
If you need human approval, create an approval file in /Pending_Approval/.
If the task is complete, output: TASK_COMPLETE
If more iterations are needed, output: NEEDS_ITERATION
"""

    for iteration in range(1, max_iter + 1):
        log_event("claude_invoke", {"task": task_name, "iteration": iteration})
        try:
            result = subprocess.run(
                [CLAUDE_CLI_CMD, "--print", "--no-conversation", prompt],
                capture_output=True, text=True, timeout=300,
                env={**os.environ, "VAULT_ROOT": str(VAULT_ROOT)},
            )
            output = result.stdout.strip()
            log_event("claude_response", {
                "task": task_name, "iteration": iteration,
                "returncode": result.returncode, "output_length": len(output),
            })

            if result.returncode != 0:
                log_event("claude_error", {"task": task_name, "stderr": result.stderr[:500]}, outcome="error")
                return False, result.stderr

            if "TASK_COMPLETE" in output:
                log_event("task_complete", {"task": task_name, "iterations": iteration})
                return True, output

            if "NEEDS_ITERATION" not in output:
                # Treat non-NEEDS_ITERATION response as complete
                return True, output

        except subprocess.TimeoutExpired:
            log_event("claude_timeout", {"task": task_name, "iteration": iteration}, outcome="error")
            return False, "Claude invocation timed out"
        except FileNotFoundError:
            log_event("claude_not_found", {"cmd": CLAUDE_CLI_CMD}, outcome="error")
            return False, f"Claude CLI not found at: {CLAUDE_CLI_CMD}"

    log_event("max_iter_reached", {"task": task_name, "max_iter": max_iter}, outcome="error")
    return False, f"Max iterations ({max_iter}) reached without TASK_COMPLETE"


# ── Main loop ─────────────────────────────────────────────────────────────────

def process_cycle(max_iter: int, dev_mode: bool, dry_run: bool) -> int:
    """Process one cycle. Returns number of tasks processed."""
    reclaim_stale(dry_run)

    task_files = sorted(NEEDS_ACTION_DIR.glob("*.md"))
    if not task_files:
        return 0

    processed = 0
    for task_file in task_files:
        claimed = claim_task(task_file, dry_run)
        if claimed is None and not dry_run:
            continue

        if dry_run:
            # In dry_run, just report
            content = task_file.read_text(encoding="utf-8")
            log_event("would_process", {"task": task_file.name, "size": len(content)}, dry_run=True)
            processed += 1
            continue

        content = claimed.read_text(encoding="utf-8")
        success, output = invoke_claude(content, claimed.name, max_iter, dev_mode, dry_run)
        release_task(claimed, success, dry_run)
        processed += 1

    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Employee Vault Orchestrator")
    parser.add_argument("--dry-run",  action="store_true", help="Preview only — no file moves or Claude invocations")
    parser.add_argument("--dev",      action="store_true", help="DEV_MODE: mock Claude responses")
    parser.add_argument("--once",     action="store_true", help="Run one cycle and exit")
    parser.add_argument("--max-iter", type=int, default=10, help="Max Claude iterations per task (default: 10)")
    args = parser.parse_args()

    dry_run  = args.dry_run or os.getenv("DRY_RUN", "false").lower() == "true"
    dev_mode = args.dev or os.getenv("DEV_MODE", "false").lower() == "true"

    log_event("orchestrator_started", {
        "agent_id": AGENT_ID,
        "dry_run": dry_run,
        "dev_mode": dev_mode,
        "max_iter": args.max_iter,
        "poll_interval": POLL_SECONDS,
    })

    try:
        while True:
            n = process_cycle(args.max_iter, dev_mode, dry_run)
            if n > 0:
                log_event("cycle_complete", {"tasks_processed": n})
            if args.once:
                break
            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        log_event("orchestrator_stopped", {"reason": "keyboard_interrupt"})
        print(f"\n[{AGENT_ID}] Stopped.", flush=True)


if __name__ == "__main__":
    main()
