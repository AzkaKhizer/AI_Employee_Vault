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
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Recovery engine — graceful fallback if not yet installed
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from recovery_engine import classify_error, is_recoverable, get_correction_suggestions
    _RECOVERY_AVAILABLE = True
except ImportError:
    _RECOVERY_AVAILABLE = False
    def classify_error(m, e=""): return "unknown"                        # noqa: E302
    def is_recoverable(t, m=""): return t in ("network_error", "timeout", "tool_validation_error")  # noqa: E302
    def get_correction_suggestions(t, m): return {"error_type": t, "suggestions": [], "action": ""}  # noqa: E302


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

# ── Retry / recovery config ────────────────────────────────────────────────────
MAX_RETRIES   = int(os.getenv("ORCH_MAX_RETRIES", "2"))
RETRY_BACKOFF = int(os.getenv("ORCH_RETRY_BACKOFF", "30"))  # base seconds (doubles each attempt)

# ── Circuit Breaker config ─────────────────────────────────────────────────────
CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
CIRCUIT_WINDOW_SECONDS    = int(os.getenv("CIRCUIT_WINDOW_SECONDS",    "600"))
CIRCUIT_COOLDOWN_SECONDS  = int(os.getenv("CIRCUIT_COOLDOWN_SECONDS",  "300"))

# ── Circuit Breaker state (module-level) ──────────────────────────────────────
_cb_state:         str   = "closed"   # "closed" | "open" | "half_open"
_cb_opened_at:     float = 0.0        # time.monotonic() when circuit opened
_cb_failure_times: list  = []         # monotonic timestamps of recoverable failures
_cb_probe_allowed: bool  = False      # True when exactly one probe task is permitted


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


# ── Recovery file writer ──────────────────────────────────────────────────────

def _create_recovery_file(
    task_name: str, error_type: str, error_msg: str,
    suggestions: dict, dry_run: bool,
) -> Path | None:
    """
    Create RECOVERY_REQUIRED_<task>.md in /Needs_Action/ for human intervention.
    Returns the path written, or None in dry_run mode.
    """
    if dry_run:
        log_event("recovery_file_dry_run", {
            "task": task_name, "error_type": error_type,
        }, dry_run=True)
        return None

    safe_name  = re.sub(r"[^\w.-]", "_", task_name)
    filename   = f"RECOVERY_REQUIRED_{safe_name}.md"
    dest       = NEEDS_ACTION_DIR / filename
    suggestion_lines = "\n".join(f"- {s}" for s in suggestions.get("suggestions", []))

    content = f"""# Recovery Required: {task_name}

**Generated:** {datetime.now(timezone.utc).isoformat()}
**Error Type:** `{error_type}`
**Error Message:** {error_msg[:500]}

---

## Correction Suggestions

{suggestions.get('action', 'Review error and retry manually.')}

### Closest Matches

{suggestion_lines or '- No automatic matches found — check Odoo manually.'}

---

## Action Required

1. Review the error type and suggestions above.
2. Correct the task parameters in the original task file.
3. Re-submit the corrected task to `Needs_Action/`.

*Auto-generated by orchestrator.py recovery engine.*
"""
    dest.write_text(content, encoding="utf-8")
    log_event("recovery_file_created", {
        "task":          task_name,
        "error_type":    error_type,
        "recovery_file": filename,
        "suggestions":   suggestions.get("suggestions", []),
    })
    return dest


# ── Auto-retry wrapper ────────────────────────────────────────────────────────

def run_task_with_retry(
    task_content: str, task_name: str, max_iter: int,
    dev_mode: bool, dry_run: bool,
) -> tuple[bool, str, bool, int]:
    """
    Invoke Claude with automatic retry for recoverable failures.

    Returns:
      (success, output, auto_recovered, retry_count)

    Retry policy:
      - Attempt up to MAX_RETRIES additional tries (total = 1 + MAX_RETRIES)
      - Only for recoverable error types (network_error, timeout, tool_validation_error)
      - Exponential backoff: RETRY_BACKOFF * 2^attempt seconds between attempts
      - If all retries exhausted → write RECOVERY_REQUIRED_<task>.md to Needs_Action/
    """
    # ── Circuit Breaker gate ──────────────────────────────────────────────────
    cb_state = _cb_check(dry_run)
    if cb_state == "open":
        cooldown_remaining = max(0.0, CIRCUIT_COOLDOWN_SECONDS - (time.monotonic() - _cb_opened_at))
        log_event("circuit_blocked", {
            "task":                   task_name,
            "cooldown_remaining_s":   round(cooldown_remaining, 1),
        }, outcome="error")
        return False, "Circuit open — task blocked during cooldown", False, 0

    is_probe = False
    if cb_state == "half_open":
        is_probe = _cb_consume_probe(task_name, dry_run)
        if not is_probe:
            # Probe slot already consumed this cycle — block additional tasks
            log_event("circuit_blocked", {
                "task":   task_name,
                "reason": "half_open_probe_slot_occupied",
            }, outcome="error")
            return False, "Circuit half-open — probe slot already in use", False, 0

    auto_recovered  = False
    retry_count     = 0
    last_error      = ""
    last_error_type = "unknown"

    for attempt in range(MAX_RETRIES + 1):
        success, output = invoke_claude(task_content, task_name, max_iter, dev_mode, dry_run)

        if success:
            if retry_count > 0:
                auto_recovered = True
                log_event("retry_success", {
                    "task": task_name, "retries_used": retry_count,
                })
            _cb_record_success(dry_run)
            return success, output, auto_recovered, retry_count

        last_error      = output
        last_error_type = classify_error(output, "")

        if not is_recoverable(last_error_type, last_error):
            log_event("failure_not_recoverable", {
                "task":       task_name,
                "error_type": last_error_type,
                "error":      last_error[:300],
            }, outcome="error")
            break  # No retry — surface to human immediately

        # Record recoverable failure; may open/reopen circuit
        _cb_record_failure(last_error_type, dry_run)

        # If circuit just opened mid-retry, stop retrying
        if _cb_check(dry_run) == "open":
            log_event("retry_aborted_circuit_opened", {
                "task":       task_name,
                "attempt":    attempt + 1,
                "error_type": last_error_type,
            }, outcome="error")
            break

        if attempt < MAX_RETRIES:
            retry_count += 1
            backoff = RETRY_BACKOFF * (2 ** (attempt))
            log_event("retry_attempt", {
                "task":            task_name,
                "attempt":         retry_count,
                "error_type":      last_error_type,
                "backoff_seconds": backoff,
            })
            if not dry_run:
                time.sleep(backoff)
        else:
            retry_count += 1  # count the last failed attempt

    # All retries exhausted (or aborted) — create a recovery task for human review
    suggestions = get_correction_suggestions(last_error_type, last_error)
    _create_recovery_file(task_name, last_error_type, last_error, suggestions, dry_run)
    log_event("recovery_exhausted", {
        "task":       task_name,
        "error_type": last_error_type,
        "retries":    retry_count,
    }, outcome="error")

    return False, last_error, False, retry_count


# ── Circuit Breaker helpers ───────────────────────────────────────────────────

def _cb_prune_window() -> None:
    """Remove failure timestamps outside the rolling window."""
    global _cb_failure_times
    cutoff = time.monotonic() - CIRCUIT_WINDOW_SECONDS
    _cb_failure_times = [t for t in _cb_failure_times if t > cutoff]


def _cb_record_failure(error_type: str, dry_run: bool) -> None:
    """
    Record a recoverable failure and open the circuit if threshold is reached.
    No-op for non-recoverable failures or in dry_run mode.
    """
    global _cb_state, _cb_opened_at, _cb_failure_times, _cb_probe_allowed

    if dry_run or not is_recoverable(error_type):
        return

    _cb_failure_times.append(time.monotonic())
    _cb_prune_window()

    if _cb_state == "closed" and len(_cb_failure_times) >= CIRCUIT_FAILURE_THRESHOLD:
        _cb_state       = "open"
        _cb_opened_at   = time.monotonic()
        _cb_probe_allowed = False
        log_event("circuit_opened", {
            "failure_count":       len(_cb_failure_times),
            "window_seconds":      CIRCUIT_WINDOW_SECONDS,
            "cooldown_seconds":    CIRCUIT_COOLDOWN_SECONDS,
            "failure_threshold":   CIRCUIT_FAILURE_THRESHOLD,
        }, outcome="error")

    elif _cb_state == "half_open":
        # Probe failed — reopen the circuit
        _cb_state       = "open"
        _cb_opened_at   = time.monotonic()
        _cb_probe_allowed = False
        log_event("circuit_reopened", {
            "reason":           "probe_failure",
            "error_type":       error_type,
            "cooldown_seconds": CIRCUIT_COOLDOWN_SECONDS,
        }, outcome="error")


def _cb_record_success(dry_run: bool) -> None:
    """Close the circuit after a successful probe (half_open → closed)."""
    global _cb_state, _cb_failure_times, _cb_probe_allowed

    if dry_run:
        return

    if _cb_state == "half_open":
        _cb_state         = "closed"
        _cb_failure_times = []
        _cb_probe_allowed = False
        log_event("circuit_closed", {"reason": "probe_success"})
    elif _cb_state == "closed":
        # On healthy success in closed state, clear any stale window entries
        _cb_prune_window()


def _cb_check(dry_run: bool) -> str:
    """
    Check circuit state; transition open→half_open if cooldown elapsed.
    Returns current state: "closed" | "open" | "half_open".
    """
    global _cb_state, _cb_opened_at, _cb_probe_allowed

    if _cb_state == "open":
        elapsed = time.monotonic() - _cb_opened_at
        if elapsed >= CIRCUIT_COOLDOWN_SECONDS:
            _cb_state         = "half_open"
            _cb_probe_allowed = True
            log_event("circuit_half_open", {
                "elapsed_seconds":  round(elapsed, 1),
                "cooldown_seconds": CIRCUIT_COOLDOWN_SECONDS,
            })
        # else: still cooling down

    return _cb_state


def _cb_consume_probe(task_name: str, dry_run: bool) -> bool:
    """
    Consume the probe slot in half_open state.
    Returns True if task is allowed to proceed as a probe; False if slot already used.
    """
    global _cb_probe_allowed

    if _cb_probe_allowed and not dry_run:
        _cb_probe_allowed = False
        log_event("circuit_probe_start", {"task": task_name})
        return True
    return False


# ── Main loop ─────────────────────────────────────────────────────────────────

def process_cycle(max_iter: int, dev_mode: bool, dry_run: bool) -> int:
    """Process one cycle. Returns number of tasks processed."""
    reclaim_stale(dry_run)

    # ── Circuit Breaker: skip entire cycle if circuit is open ────────────────
    cb_state = _cb_check(dry_run)
    if cb_state == "open":
        task_files_waiting = list(NEEDS_ACTION_DIR.glob("*.md"))
        cooldown_remaining = max(0.0, CIRCUIT_COOLDOWN_SECONDS - (time.monotonic() - _cb_opened_at))
        log_event("circuit_blocked", {
            "tasks_waiting":        len(task_files_waiting),
            "cooldown_remaining_s": round(cooldown_remaining, 1),
            "reason":               "cycle_skipped",
        }, outcome="error")
        return 0

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
        success, output, auto_recovered, retries = run_task_with_retry(
            content, claimed.name, max_iter, dev_mode, dry_run,
        )
        if retries > 0:
            log_event("task_retry_summary", {
                "task": claimed.name, "retries": retries, "auto_recovered": auto_recovered,
            })
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
