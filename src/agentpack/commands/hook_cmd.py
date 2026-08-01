from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from agentpack.commands._shared import _root
from agentpack.core import git as _git
from agentpack.core.command_surface import refresh_commands
from agentpack.core.config import load_config
from agentpack.core.task_freshness import read_task_md, write_task_md
from agentpack.core.thread_context import resolve_session_thread_option, thread_paths
from agentpack.integrations.platform import cli_module_argv, detached_popen

_CODING_PROMPT_RE = re.compile(
    r"(?:fix|add|refactor|impl|implement|update|write|debug|test|build|migrate|remove|delete|rename|optimize|deploy|release|rollback|ship)\b",
    re.IGNORECASE,
)
_REVIEW_PROMPT_RE = re.compile(
    r"(?:\b(?:review|findings?|comments?)\b.*\b(?:pr|pull request|diff|code|branch|change|changes|github|gh)\b"
    r"|\b(?:pr|pull request|diff|github|gh)\b.*\b(?:review|findings?|comments?)\b"
    r"|@agentpack-review|/agentpack-review|gh\s+pr)",
    re.IGNORECASE,
)
_TASK_STOPWORDS = {
    "add",
    "all",
    "and",
    "bug",
    "build",
    "can",
    "change",
    "changes",
    "code",
    "delete",
    "fix",
    "for",
    "implement",
    "improve",
    "make",
    "please",
    "refactor",
    "remove",
    "task",
    "test",
    "that",
    "the",
    "these",
    "this",
    "update",
    "with",
    "work",
    "write",
    "you",
}
_VAGUE_TASK_REFERENCES = {
    "all",
    "everything",
    "gap",
    "gaps",
    "issue",
    "issues",
    "it",
    "that",
    "these",
    "this",
    "those",
}
_RUNTIME_INFRA_TERMS = {
    "alb",
    "aws",
    "cfn",
    "cloud",
    "cloudformation",
    "cloudwatch",
    "copilot",
    "deploy",
    "deployment",
    "ecs",
    "github",
    "iam",
    "infra",
    "lambda",
    "log",
    "logs",
    "otp",
    "package",
    "rendered",
    "runbook",
    "runtime",
    "secret",
    "service",
    "security",
    "ssm",
    "waf",
    "workflow",
}
_DEPLOY_TASK_TERMS = {
    "aws",
    "cfn",
    "cloudformation",
    "cloudwatch",
    "copilot",
    "deploy",
    "deployment",
    "ecs",
    "iam",
    "lambda",
    "prod",
    "production",
    "release",
    "rollback",
    "serverless",
    "ship",
}
_RUNTIME_INFRA_PATH_TERMS = {
    ".github/workflows",
    "aws",
    "cfn",
    "cloudformation",
    "copilot",
    "deploy",
    "ecs",
    "iam",
    "infra",
    "lambda",
    "manifest",
    "override",
    "security",
    "serverless",
    "waf",
}
_DEPLOY_PATH_TERMS = {
    ".github/workflows",
    "buildspec",
    "cfn.patches",
    "cloudformation",
    "copilot",
    "deploy",
    "deployment",
    "dockerfile",
    "ecs",
    "iam",
    "manifest",
    "package",
    "pipeline",
    "rendered",
    "serverless",
}


def register(app: typer.Typer) -> None:
    @app.command(name="hook")
    def hook(
        event: str = typer.Option("UserPromptSubmit", "--event", help="Hook event name."),
        agent: str = typer.Option("auto", "--agent", help="Agent name for git auto-repack hooks."),
    ) -> None:
        """Run as a Claude Code hook. Reads stdin (JSON), emits additionalContext."""
        root = _root()
        if event == "UserPromptSubmit":
            _run_user_prompt_submit(root)
        elif event == "SessionStart":
            _run_session_start(root)
        elif event == "GitAutoRepack":
            _run_git_auto_repack(root, agent)
        else:
            sys.exit(0)


# ---------------------------------------------------------------------------
# Public helpers (tested directly)
# ---------------------------------------------------------------------------

def _mcp_installed(root: Path) -> bool:
    local_mcp = root / ".mcp.json"
    if local_mcp.exists():
        try:
            cfg = json.loads(local_mcp.read_text())
            if "agentpack" in cfg.get("mcpServers", {}):
                return True
        except Exception:
            pass
    global_settings = Path.home() / ".claude" / "settings.json"
    if global_settings.exists():
        try:
            cfg = json.loads(global_settings.read_text())
            if "agentpack" in cfg.get("mcpServers", {}):
                return True
        except Exception:
            pass
    return False


def _load_task_md(root: Path, thread_id: str | None = None) -> str:
    """Return task.md content if user has written a real task (not the default placeholder)."""
    scoped = thread_paths(root, thread_id)
    if scoped:
        try:
            return scoped.task.read_text(encoding="utf-8").strip()[:200] if scoped.task.exists() else ""
        except OSError:
            return ""
    return (read_task_md(root) or "")[:200]


def _emit_additional_context(message: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": message,
        }
    }))


def _looks_like_coding_prompt(prompt: str) -> bool:
    """Return True if prompt looks like a coding task (not a slash command or chat)."""
    stripped = prompt.strip()
    if stripped.startswith("/"):
        return False
    return bool(_CODING_PROMPT_RE.search(stripped) or _looks_like_review_prompt(stripped))

def _looks_like_review_prompt(prompt: str) -> bool:
    stripped = prompt.strip()
    if not stripped:
        return False
    return bool(_REVIEW_PROMPT_RE.search(stripped))

def _review_preflight_note(
    *,
    review_intent: bool,
    context_stale: bool,
    has_mcp: bool,
    task: str,
    thread_id: str | None = None,
) -> str:
    if not review_intent:
        return ""
    if has_mcp:
        refresh = (
            f'If the AgentPack MCP tool is visible, call agentpack_get_pr_context(focus="{task}") before PR diff/code review; '
            f"otherwise run `{_refresh_command(thread_id)}` and use direct repo evidence."
        )
    else:
        refresh = f"Run `{_refresh_command(thread_id)}` before PR diff/code review."
    lines = [
        "REVIEW DETECTED: refresh AgentPack context before PR diff/code review.",
        refresh,
    ]
    if context_stale:
        lines.append("BYPASS REQUIRED: if reviewing without refreshed AgentPack context, state why.")
    return "\n".join(lines) + "\n"


def _review_reminder_key(task: str, packed_root_hash: str | None, current_root_hash: str | None) -> str:
    return json.dumps(
        {
            "task": task,
            "packed_root_hash": packed_root_hash or "",
            "current_root_hash": current_root_hash or "",
        },
        sort_keys=True,
    )


def _should_emit_review_preflight(
    root: Path,
    *,
    review_intent: bool,
    task: str,
    packed_root_hash: str | None,
    current_root_hash: str | None,
    thread_id: str | None = None,
) -> bool:
    if not review_intent:
        return False
    reminder_path = _session_state_path(root, ".review_preflight_reminded", thread_id)
    key = _review_reminder_key(task, packed_root_hash, current_root_hash)
    try:
        if reminder_path.exists() and reminder_path.read_text(encoding="utf-8") == key:
            return False
        reminder_path.parent.mkdir(parents=True, exist_ok=True)
        reminder_path.write_text(key, encoding="utf-8")
    except OSError:
        return True
    return True


def _source_of_truth_note(task: str) -> str:
    if _looks_like_deploy_task(task):
        return (
            "AgentPack: guardrail + context frame only.\n"
            "Source of truth: GitHub PR/head, clean worktree, rendered deploy config, live AWS/ECS/CloudFormation status, and CloudWatch logs.\n"
        )
    if not _looks_like_runtime_infra_task(task):
        return ""
    return (
        "SOURCE OF TRUTH: treat AgentPack as guardrail/orientation only. "
        "Use direct repo search, rendered config, cloud/provider validation, and focused tests for final evidence.\n"
    )


def _looks_like_runtime_infra_task(task: str) -> bool:
    terms = _task_terms(task)
    return bool(terms & _RUNTIME_INFRA_TERMS)


def _looks_like_deploy_task(task: str) -> bool:
    terms = _task_terms(task)
    return bool(terms & _DEPLOY_TASK_TERMS)


def _filter_runtime_infra_hints(task: str, hints: list[dict]) -> list[dict]:
    if not _looks_like_runtime_infra_task(task):
        return hints
    task_terms = _task_terms(task)
    if _looks_like_deploy_task(task):
        return [hint for hint in hints if _deploy_hint_relevant(hint, task_terms)]
    filtered = [hint for hint in hints if _runtime_infra_hint_relevant(hint, task_terms)]
    return filtered


def _deploy_hint_relevant(hint: dict, task_terms: set[str]) -> bool:
    haystack = f"{hint.get('path', '')} {hint.get('why', '')}".lower()
    if any(term in haystack for term in _DEPLOY_PATH_TERMS):
        return True
    return any(term in haystack for term in task_terms if len(term) >= 4)


def _runtime_infra_hint_relevant(hint: dict, task_terms: set[str]) -> bool:
    haystack = f"{hint.get('path', '')} {hint.get('why', '')}".lower()
    if any(term in haystack for term in task_terms if len(term) >= 3):
        return True
    return any(term in haystack for term in _RUNTIME_INFRA_PATH_TERMS)


def _review_stage_gate_note(root: Path, *, review_intent: bool) -> str:
    if not review_intent:
        return ""
    stale_reason = _active_review_stale_reason(root)
    if stale_reason:
        return (
            "REVIEW PREFLIGHT STALE: "
            f"{stale_reason}. Ignoring stale active review state; run `agentpack review --pr <number>` "
            "or `agentpack review --allow-local-fallback` for this checkout before using AgentPack review gates.\n"
        )
    state_path = root / ".agentpack" / "review-state.json"
    if not state_path.exists():
        return ""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return "REVIEW STAGE BLOCK: active review state is unreadable. Run `agentpack review --check`.\n"
    status = str(state.get("status") or "")
    if status in {"ready_to_publish", "complete"}:
        return ""
    if status in {"awaiting_anchor", "awaiting_understanding"}:
        return "REVIEW STAGE BLOCK: Anchor understanding artifact missing. Write it, then run `agentpack review --check`.\n"
    if status in {"awaiting_judge", "awaiting_findings"}:
        return "REVIEW STAGE BLOCK: Judge findings artifact missing. Write it, then run `agentpack review --check` before final summary.\n"
    if status == "awaiting_critic":
        return "REVIEW STAGE BLOCK: Critic decisions artifact missing. Write one decision per Judge finding, then run `agentpack review --check` before final summary.\n"
    if status == "blocked_invalid_artifact":
        return "REVIEW STAGE BLOCK: active review artifact invalid. Run `agentpack review --check` for exact error.\n"
    return f"REVIEW STAGE BLOCK: active review status `{status}`. Run `agentpack review --check`.\n"


def _active_review_stale_reason(root: Path) -> str:
    preflight_path = root / ".agentpack" / "review-preflight.json"
    if not preflight_path.exists():
        return ""
    try:
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    except Exception:
        return "active review preflight is unreadable"
    preflight_git = preflight.get("git") if isinstance(preflight.get("git"), dict) else {}
    expected_branch = str(preflight_git.get("branch") or "")
    current_branch = _git.current_branch(root) or ""
    if expected_branch and current_branch and expected_branch != current_branch:
        return f"active review was prepared for branch {expected_branch}, but current branch is {current_branch}"
    paths = preflight.get("paths") if isinstance(preflight.get("paths"), dict) else {}
    run_dir = str(paths.get("run_dir") or "")
    if run_dir and not (root / run_dir).exists():
        return f"active review run directory is missing: {run_dir}"
    return ""


def _prompt_task(prompt: str) -> str:
    if not prompt or not _looks_like_coding_prompt(prompt):
        return ""
    task = " ".join(prompt.strip().split())[:200]
    if not _task_terms(task) and not _has_vague_task_reference(task):
        return ""
    return task


def _task_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text.lower()):
        for part in re.split(r"[-_]", raw):
            if len(part) >= 3 and part not in _TASK_STOPWORDS:
                terms.add(part)
    return terms


def _looks_like_task_switch(current_task: str, prompt: str, min_terms: int = 1) -> bool:
    """Heuristic: a coding prompt with disjoint concrete terms likely starts a new task."""
    prompt_task = _prompt_task(prompt)
    if not current_task or not prompt_task:
        return False
    if current_task.strip().lower() == prompt_task.lower():
        return False
    current_terms = _task_terms(current_task)
    prompt_terms = _task_terms(prompt_task)
    if current_terms and not prompt_terms and _has_vague_task_reference(prompt_task):
        return True
    required_terms = max(1, min_terms)
    if len(current_terms) < required_terms or len(prompt_terms) < required_terms:
        return False
    return bool(current_terms and prompt_terms and current_terms.isdisjoint(prompt_terms))


def _has_vague_task_reference(prompt: str) -> bool:
    prompt_words = {
        raw.lower()
        for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", prompt)
    }
    return bool(prompt_words & _VAGUE_TASK_REFERENCES)


def _write_task_md(root: Path, task: str, thread_id: str | None = None) -> None:
    scoped = thread_paths(root, thread_id)
    if scoped:
        scoped.task.parent.mkdir(parents=True, exist_ok=True)
        scoped.task.write_text(task.strip() + "\n", encoding="utf-8")
        return
    write_task_md(root, task)


def _resolve_task(
    root: Path,
    prompt: str,
    *,
    task_switch_detection: bool = True,
    task_switch_min_terms: int = 1,
    thread_id: str | None = None,
) -> str:
    """Merge task.md + prompt into best task description for repack."""
    task_md = _load_task_md(root, thread_id)
    prompt_task = _prompt_task(prompt)
    if (
        task_switch_detection
        and task_md
        and prompt_task
        and _looks_like_task_switch(task_md, prompt_task, min_terms=task_switch_min_terms)
    ):
        return prompt_task
    if task_md:
        return task_md
    if prompt_task:
        return prompt_task
    return "auto"


def _load_hints(root: Path, n: int = 5) -> list[dict]:
    """Return top-n selected_hints (path + why) from last metrics record."""
    metrics_path = root / ".agentpack" / "metrics.jsonl"
    if not metrics_path.exists():
        return []
    try:
        lines = metrics_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            hints = rec.get("selected_hints", [])
            if hints:
                return hints[:n]
            # Fallback: old metrics without hints
            paths = rec.get("selected_paths", [])
            if paths:
                return [{"path": p, "why": ""} for p in paths[:n]]
    except Exception:
        pass
    return []


def _load_top_files(root: Path, n: int = 5) -> list[dict]:
    """Alias kept for backward compat with tests."""
    return _load_hints(root, n)


def _load_pack_metadata(root: Path, thread_id: str | None = None) -> dict:
    scoped = thread_paths(root, thread_id)
    meta_path = scoped.metadata if scoped else root / ".agentpack" / "pack_metadata.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_pack_task(root: Path, thread_id: str | None = None) -> str:
    return str(_load_pack_metadata(root, thread_id).get("task", "") or "")


def _load_delta_summary(root: Path, thread_id: str | None = None) -> str:
    meta = _load_pack_metadata(root, thread_id)
    freshness = meta.get("freshness") or {}
    delta = freshness.get("delta_summary", "")
    return str(delta).splitlines()[0][:240] if delta else ""


def _packed_root_hash(root: Path, thread_id: str | None = None) -> str | None:
    value = _load_pack_metadata(root, thread_id).get("snapshot_root_hash")
    return str(value) if value else None


def _infer_live_task(root: Path) -> str:
    """Live task: git priority chain (no stale metadata). Falls back to 'unknown'."""
    try:
        task, _ = _git.infer_task_with_source(root)
        return task
    except Exception:
        return "unknown"


def _current_root_hash(root: Path) -> str | None:
    snap = root / ".agentpack" / "snapshots" / "latest.json"
    if not snap.exists():
        return None
    try:
        return json.loads(snap.read_text()).get("root_hash")
    except Exception:
        return None


def _stale_reasons(
    *,
    pack_missing: bool,
    repo_changed: bool,
    task_switched: bool,
    pack_task_changed: bool,
) -> list[str]:
    reasons: list[str] = []
    if pack_missing:
        reasons.append("pack missing metadata")
    if repo_changed:
        reasons.append("repo snapshot changed")
    if task_switched:
        reasons.append("prompt switched task")
    if pack_task_changed:
        reasons.append("task differs from packed task")
    return reasons


def _stale_note(reasons: list[str]) -> str:
    return f"stale reason: {', '.join(reasons)}\n" if reasons else ""


def _mcp_status_note(root: Path, *, has_mcp: bool, task: str, thread_id: str | None = None) -> str:
    reminder_path = _session_state_path(root, ".mcp_reminded", thread_id)
    key = json.dumps({"task": task, "has_mcp": has_mcp}, sort_keys=True)
    try:
        if reminder_path.exists() and reminder_path.read_text(encoding="utf-8") == key:
            return ""
        reminder_path.parent.mkdir(parents=True, exist_ok=True)
        reminder_path.write_text(key, encoding="utf-8")
    except OSError:
        pass
    if has_mcp:
        return (
            "MCP registration found. Live exposure must be proven by readiness(). "
            "If readiness is absent, run one bounded `agentpack mcp` diagnostic, then use CLI/direct search fallback. "
            "Do not keep `agentpack mcp` running manually.\n"
        )
    return (
        "MCP unavailable: run `agentpack repair --agent auto`. Using CLI/direct search fallback. "
        "A bounded `agentpack mcp` diagnostic can distinguish setup failure from host exposure failure.\n"
    )


def _git_sync_decision_note(root: Path) -> str:
    """Cheap prompt-time git reminder. No fetch, pull, pack, or repair work."""
    if not _looks_like_git_checkout(root):
        return ""
    status = _fast_git_status(root)
    if not status:
        return ""
    summary = _parse_git_status_summary(status)
    branch = summary["branch"] or "(none)"
    upstream_value = summary["upstream"]
    upstream = upstream_value or "(none)"
    tracked_dirty = summary["staged"] + summary["unstaged"]
    untracked = summary["untracked"]
    ahead = summary["ahead"]
    behind = summary["behind"]
    dirty_sample = summary["dirty_sample"][:3]
    if tracked_dirty:
        action = "decide whether tracked local changes are the task target before editing or repacking"
    elif not upstream_value:
        action = "no upstream configured; verify the intended base before editing"
    elif behind and ahead:
        action = "branch diverged from upstream; choose rebase/merge before editing"
    elif behind:
        action = "branch appears behind upstream from local git status; sync before editing when safe"
    else:
        action = "local status is clean enough to proceed"
    sample = f"; dirty sample: {', '.join(dirty_sample)}" if dirty_sample else ""
    return (
        "GIT SYNC DECISION: "
        f"branch={branch}, upstream={upstream}, tracked_dirty={tracked_dirty}, "
        f"untracked={untracked}, ahead/behind={ahead}/{behind}. "
        f"Action: {action}{sample}.\n"
    )


def _fast_git_status(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--branch"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _parse_git_status_summary(status: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "branch": "",
        "upstream": "",
        "ahead": 0,
        "behind": 0,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "dirty_sample": [],
    }
    dirty_sample: list[str] = []
    for line in status.splitlines():
        if line.startswith("## "):
            branch, upstream, ahead, behind = _parse_git_branch_line(line[3:].strip())
            summary["branch"] = branch or ""
            summary["upstream"] = upstream or ""
            summary["ahead"] = ahead
            summary["behind"] = behind
            continue
        if not line:
            continue
        status_code = line[:2]
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if status_code == "??":
            summary["untracked"] += 1
        else:
            if status_code[0] != " ":
                summary["staged"] += 1
            if len(status_code) > 1 and status_code[1] != " ":
                summary["unstaged"] += 1
        if path:
            dirty_sample.append(path)
    summary["dirty_sample"] = dirty_sample
    return summary


def _parse_git_branch_line(value: str) -> tuple[str | None, str | None, int, int]:
    if "..." not in value:
        return (None if value == "HEAD (no branch)" else value), None, 0, 0
    branch_part, rest = value.split("...", 1)
    branch = branch_part.strip() or None
    upstream = rest.split(" ", 1)[0].strip() or None
    ahead = behind = 0
    if "[" in rest and "]" in rest:
        meta = rest.split("[", 1)[1].split("]", 1)[0]
        for part in meta.split(","):
            item = part.strip()
            if item.startswith("ahead "):
                ahead = _safe_int(item.split(" ", 1)[1])
            elif item.startswith("behind "):
                behind = _safe_int(item.split(" ", 1)[1])
    return branch, upstream, ahead, behind


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _looks_like_git_checkout(root: Path) -> bool:
    try:
        for candidate in (root, *root.parents):
            if (candidate / ".git").exists():
                return True
    except OSError:
        return False
    return False


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def _run_session_start(root: Path) -> None:
    """Clear sentinels so first prompt gets fresh context."""
    thread_id = resolve_session_thread_option("")
    for sentinel in [
        _session_state_path(root, ".mcp_reminded", thread_id),
        _session_state_path(root, ".context_injected", thread_id),
        _session_state_path(root, ".no_task_reminded", thread_id),
        _session_state_path(root, ".review_preflight_reminded", thread_id),
    ]:
        try:
            sentinel.unlink(missing_ok=True)
        except Exception:
            pass
    # No output needed — SessionStart hooks don't inject additionalContext


def _run_git_auto_repack(root: Path, agent: str) -> None:
    config_path = root / ".agentpack" / "config.toml"
    if not config_path.exists():
        return
    args = cli_module_argv("pack", "--agent", agent, "--task", "auto", "--mode", "balanced")
    thread_id = resolve_session_thread_option("")
    if thread_id:
        args.extend(["--thread", thread_id])
    detached_popen(
        args,
        cwd=root,
    )


def _run_blocking_pack(root: Path, thread_id: str | None = None) -> tuple[bool, str]:
    args = cli_module_argv("pack", "--task", "auto", "--mode", "balanced")
    if thread_id:
        args.extend(["--thread", thread_id])
    try:
        result = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        return False, str(exc)
    output = (result.stderr or result.stdout or "").strip().splitlines()
    detail = output[-1][:180] if output else ""
    return result.returncode == 0, detail


def _run_user_prompt_submit(root: Path) -> None:
    try:
        hook_data = json.loads(sys.stdin.read())
        prompt = hook_data.get("prompt", "")
    except Exception:
        prompt = ""

    cfg = load_config(root)
    thread_id = resolve_session_thread_option("")
    task_md = _load_task_md(root, thread_id)
    if not task_md:
        if _looks_like_coding_prompt(prompt):
            git_note = _git_sync_decision_note(root)
            reminder = _session_state_path(root, ".no_task_reminded", thread_id)
            if not reminder.exists():
                try:
                    reminder.parent.mkdir(parents=True, exist_ok=True)
                    reminder.write_text("1", encoding="utf-8")
                except Exception:
                    pass
                start_cmd = 'agentpack start "describe the task"'
                if thread_id:
                    start_cmd += f" --thread {thread_id}"
                _emit_additional_context(
                    f"AgentPack idle. No active task in `{_task_label(thread_id)}`.\n"
                    + git_note
                    + f"Run `{start_cmd}` to enable prompt-time hints."
                )
        return

    prompt_has_agentpack_work = _looks_like_coding_prompt(prompt)
    if not prompt_has_agentpack_work:
        return

    task_switched = bool(
        cfg.hooks.task_switch_detection
        and _looks_like_task_switch(
            task_md,
            prompt,
            min_terms=cfg.hooks.task_switch_min_terms,
        )
    )
    task = _resolve_task(
        root,
        prompt,
        task_switch_detection=cfg.hooks.task_switch_detection,
        task_switch_min_terms=cfg.hooks.task_switch_min_terms,
        thread_id=thread_id,
    )
    if task_switched and task != "auto":
        try:
            _write_task_md(root, task, thread_id)
        except Exception:
            pass

    current_hash = _current_root_hash(root)
    packed_task = _load_pack_task(root, thread_id)
    packed_root_hash = _packed_root_hash(root, thread_id)
    repo_changed = bool(current_hash and packed_root_hash and current_hash != packed_root_hash)
    pack_missing = not packed_task or not packed_root_hash
    pack_task_changed = bool(task != "auto" and packed_task and packed_task != task)

    context_stale = pack_missing or repo_changed or task_switched or pack_task_changed
    stale_reasons = _stale_reasons(
        pack_missing=pack_missing,
        repo_changed=repo_changed,
        task_switched=task_switched,
        pack_task_changed=pack_task_changed,
    )
    blocking_refresh = bool(cfg.hooks.blocking_task_refresh and context_stale)
    refresh_state = "fresh"
    refresh_error = ""

    if context_stale:
        refresh_state = "refresh pending"
        if blocking_refresh:
            ok, detail = _run_blocking_pack(root, thread_id)
            refresh_state = "refreshed" if ok else "refresh failed"
            refresh_error = detail
            if ok:
                pack_missing = False
                pack_task_changed = False
                repo_changed = False
                task_switched = False

    has_mcp = _mcp_installed(root)
    current_task = _load_task_md(root, thread_id) or _infer_live_task(root)
    git_note = _git_sync_decision_note(root)
    review_intent = _looks_like_review_prompt(prompt)
    delta = _load_delta_summary(root, thread_id)
    safe_hints = not pack_missing and not pack_task_changed and not task_switched and not repo_changed
    raw_hints = _load_hints(root, n=5 if has_mcp else 8) if safe_hints else []
    hints = _filter_runtime_infra_hints(current_task, raw_hints)
    hints_suppressed = safe_hints and bool(raw_hints) and not hints
    emit_review_preflight = _should_emit_review_preflight(
        root,
        review_intent=review_intent,
        task=current_task,
        packed_root_hash=packed_root_hash,
        current_root_hash=current_hash,
        thread_id=thread_id,
    )
    review_note = _review_preflight_note(
        review_intent=review_intent,
        context_stale=context_stale,
        has_mcp=has_mcp,
        task=current_task,
        thread_id=thread_id,
    ) if emit_review_preflight else ""
    review_stage_gate = _review_stage_gate_note(root, review_intent=review_intent)
    source_note = _source_of_truth_note(current_task)
    stale_detail = _stale_note(stale_reasons)
    mcp_detail = _mcp_status_note(root, has_mcp=has_mcp, task=current_task, thread_id=thread_id)

    if has_mcp:
        if hints:
            files_lines = "\n".join(
                f"  - {h['path']}" + (f" — {h['why']}" if h.get("why") else "")
                for h in hints
            )
            if refresh_state == "refreshed":
                status_note = "(refreshed for current task)"
            elif refresh_state == "refresh failed":
                status_note = "(refresh failed — call pack_context to retry)"
            elif refresh_state == "refresh pending":
                status_note = "(refresh pending — call get_context for fresh results)"
            else:
                status_note = "(index fresh)"
            msg = (
                f"AgentPack {status_note}\n"
                f"task: {current_task}\n"
                + review_note
                + review_stage_gate
                + source_note
                + git_note
                + stale_detail
                + mcp_detail
                + (f"refresh error: {refresh_error}\n" if refresh_error else "")
                + (f"delta: {delta}\n" if delta else "")
                +
                f"top files:\n{files_lines}\n"
                "If the AgentPack MCP tools are visible, call agentpack_get_delta_context() for delta or "
                'agentpack_pack_context(task="...") for full ranked context; otherwise use direct repo search.'
            )
        elif refresh_state == "refresh pending":
            msg = (
                "AgentPack STALE (refresh pending)\n"
                f"task: {current_task}\n"
                + (f"packed task: {packed_task}\n" if packed_task and packed_task != current_task else "")
                + review_note
                + review_stage_gate
                + source_note
                + git_note
                + stale_detail
                + mcp_detail
                + (
                    "If the AgentPack MCP tool is visible, call agentpack_get_context(); "
                    f"otherwise run `{_refresh_command(thread_id)}` and use direct repo search."
                )
            )
        elif refresh_state == "refresh failed":
            msg = (
                "AgentPack STALE (refresh failed)\n"
                f"task: {current_task}\n"
                + (f"packed task: {packed_task}\n" if packed_task and packed_task != current_task else "")
                + review_note
                + review_stage_gate
                + source_note
                + git_note
                + stale_detail
                + mcp_detail
                + (f"refresh error: {refresh_error}\n" if refresh_error else "")
                + (
                    'If the AgentPack MCP tool is visible, call agentpack_pack_context(task="..."); '
                    f"otherwise run `{_refresh_command(thread_id)}` and use direct repo search."
                )
            )
        elif hints_suppressed:
            msg = (
                "AgentPack guardrail active. Selected-file hints suppressed because they did not match this runtime/infra task.\n"
                f"task: {current_task}\n"
                + review_note
                + review_stage_gate
                + source_note
                + git_note
                + mcp_detail
                + (f"delta: {delta}\n" if delta else "")
                + (
                    "If the AgentPack MCP tools are visible, call agentpack_pack_context(task=\"...\") for a fresh pack; "
                    f"otherwise run `{_refresh_command(thread_id)}` or use direct repo search."
                )
            )
        else:
            msg = (
                "AgentPack active. No pack yet.\n"
                + source_note
                + git_note
                + mcp_detail
                + (
                    'If the AgentPack MCP tool is visible, call agentpack_pack_context(task="..."); '
                    f"otherwise run `{_refresh_command(thread_id)}`."
                )
            )
    else:
        if hints:
            files_lines = "\n".join(
                f"  - {h['path']}" + (f" — {h['why']}" if h.get("why") else "")
                for h in hints
            )
            if refresh_state == "refreshed":
                changed_note = " (refreshed)"
            elif refresh_state == "refresh failed":
                changed_note = " (refresh failed)"
            elif refresh_state == "refresh pending":
                changed_note = " (refresh pending)"
            else:
                changed_note = ""
            msg = (
                f"AgentPack context{changed_note}\n"
                f"task: {current_task}\n"
                + review_note
                + review_stage_gate
                + source_note
                + git_note
                + stale_detail
                + mcp_detail
                + (f"refresh error: {refresh_error}\n" if refresh_error else "")
                + (f"delta: {delta}\n" if delta else "")
                +
                f"top files:\n{files_lines}\n\n"
                f"For richer context, install MCP: agentpack install --agent claude"
            )
        elif refresh_state == "refresh pending":
            msg = (
                "AgentPack STALE (refresh pending)\n"
                f"task: {current_task}\n"
                + (f"packed task: {packed_task}\n" if packed_task and packed_task != current_task else "")
                + review_note
                + review_stage_gate
                + source_note
                + git_note
                + stale_detail
                + mcp_detail
                + f"Run `{_refresh_command(thread_id)}`. If tools stay unavailable, use direct repo search."
            )
        elif refresh_state == "refresh failed":
            msg = (
                "AgentPack STALE (refresh failed)\n"
                f"task: {current_task}\n"
                + (f"packed task: {packed_task}\n" if packed_task and packed_task != current_task else "")
                + review_note
                + review_stage_gate
                + source_note
                + git_note
                + stale_detail
                + mcp_detail
                + (f"refresh error: {refresh_error}\n" if refresh_error else "")
                + f"Run `{_refresh_command(thread_id)}` to rebuild the current task pack."
            )
        elif hints_suppressed:
            msg = (
                "AgentPack guardrail active. Selected-file hints suppressed because they did not match this runtime/infra task.\n"
                f"task: {current_task}\n"
                + review_note
                + review_stage_gate
                + source_note
                + git_note
                + mcp_detail
                + (f"delta: {delta}\n" if delta else "")
                + f"Run `{_refresh_command(thread_id)}` for a fresh pack, or use direct repo search."
            )
        else:
            pack_cmd = "agentpack pack --task auto"
            if thread_id:
                pack_cmd += f" --thread {thread_id}"
            msg = (
                f"AgentPack active. Write `{_task_label(thread_id)}`, then run `{pack_cmd}` to build context.\n"
                + git_note
                + mcp_detail
                + "For auto context, install MCP: agentpack install --agent claude"
            )

        if len(msg) > 3000:
            msg = msg[:2970] + "\n... [truncated]"

    _emit_additional_context(msg)


def _refresh_command(thread_id: str | None = None) -> str:
    command = refresh_commands("auto").primary
    return f"{command} --thread {thread_id}" if thread_id else command


def _session_state_path(root: Path, filename: str, thread_id: str | None = None) -> Path:
    scoped = thread_paths(root, thread_id)
    return (scoped.base / filename) if scoped else (root / ".agentpack" / filename)


def _task_label(thread_id: str | None = None) -> str:
    scoped = thread_paths(Path("."), thread_id)
    return str(scoped.task) if scoped else ".agentpack/task.md"
