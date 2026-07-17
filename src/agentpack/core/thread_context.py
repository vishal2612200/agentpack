from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agentpack.core.task_freshness import task_hash

THREAD_INDEX = ".agentpack/thread_index.jsonl"
_THREAD_ENV_VARS = (
    "AGENTPACK_THREAD_ID",
    "CODEX_THREAD_ID",
    "CLAUDE_SESSION_ID",
    "CURSOR_SESSION_ID",
    "WINDSURF_SESSION_ID",
    "ANTIGRAVITY_SESSION_ID",
    "GEMINI_SESSION_ID",
)
_GLOBAL_THREAD_VALUES = {"", "global", "legacy", "none", "off"}
_SAFE_THREAD_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ThreadPaths:
    thread_id: str
    base: Path
    task: Path
    task_state: Path
    context: Path
    context_claude: Path
    metadata: Path

    def as_relative_dict(self, root: Path) -> dict[str, str]:
        return {
            "base": _rel(self.base, root),
            "task": _rel(self.task, root),
            "task_state": _rel(self.task_state, root),
            "context": _rel(self.context, root),
            "context_claude": _rel(self.context_claude, root),
            "metadata": _rel(self.metadata, root),
        }


def resolve_thread_id(explicit: str | None = None, env: dict[str, str] | None = None) -> str | None:
    raw = (explicit or "").strip()
    source = env if env is not None else os.environ
    if not raw:
        for key in _THREAD_ENV_VARS:
            raw = (source.get(key) or "").strip()
            if raw:
                break
    return sanitize_thread_id(raw) if raw else None


def resolve_thread_option(value: str | None, env: dict[str, str] | None = None) -> str | None:
    raw = (value or "").strip()
    if raw.lower() in _GLOBAL_THREAD_VALUES:
        return None
    if raw.lower() == "auto":
        return resolve_thread_id(None, env=env)
    return resolve_thread_id(raw, env={})


def resolve_session_thread_option(value: str | None = None, env: dict[str, str] | None = None) -> str | None:
    raw = (value or "").strip()
    if raw.lower() in {"global", "legacy", "none", "off"}:
        return None
    if raw and raw.lower() != "auto":
        return resolve_thread_id(raw, env={})
    return resolve_thread_id(None, env=env)


def sanitize_thread_id(value: str) -> str:
    cleaned = _SAFE_THREAD_RE.sub("-", value.strip())
    cleaned = cleaned.strip(".-")
    return (cleaned or "thread")[:80]


def thread_paths(root: Path, thread_id: str | None) -> ThreadPaths | None:
    if not thread_id:
        return None
    resolved = sanitize_thread_id(thread_id)
    base = root / ".agentpack" / "threads" / resolved
    return ThreadPaths(
        thread_id=resolved,
        base=base,
        task=base / "task.md",
        task_state=base / "task_state.md",
        context=base / "context.md",
        context_claude=base / "context.claude.md",
        metadata=base / "pack_metadata.json",
    )


def append_thread_index(root: Path, row: dict[str, Any]) -> None:
    path = root / THREAD_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def build_thread_index_row(
    *,
    root: Path,
    thread_id: str,
    task: str,
    branch: str | None,
    selected_files: list[str],
    dirty_files: list[str],
    status: str,
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "task": task,
        "task_hash": task_hash(task),
        "branch": branch,
        "worktree": str(root.resolve()),
        "selected_files": selected_files,
        "dirty_files": dirty_files,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def task_state_path(root: Path, thread_id: str | None) -> Path:
    scoped = thread_paths(root, thread_id)
    return scoped.task_state if scoped else root / ".agentpack" / "task_state.md"


def read_task_status(root: Path, thread_id: str | None = None) -> str:
    path = task_state_path(root, thread_id)
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.lower().startswith("status:"):
            return line.split(":", 1)[1].strip().lower()
    return ""


def task_is_done(root: Path, thread_id: str | None = None) -> bool:
    return read_task_status(root, thread_id) == "done"


def task_is_terminal(root: Path, thread_id: str | None = None) -> bool:
    return read_task_status(root, thread_id) in {"done", "handed_off"}


def detect_conflicts(root: Path, current: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    active = _active_rows(root, now=now)
    current_thread = current.get("thread_id")
    current_branch = current.get("branch")
    current_worktree = current.get("worktree")
    current_files = set(current.get("selected_files") or []) | set(current.get("dirty_files") or [])
    conflicts: list[dict[str, Any]] = []

    for row in active:
        if row.get("thread_id") == current_thread:
            continue
        if row.get("worktree") != current_worktree or row.get("branch") != current_branch:
            continue
        overlap = sorted(current_files & (set(row.get("selected_files") or []) | set(row.get("dirty_files") or [])))
        if not overlap:
            continue
        conflicts.append(
            {
                "thread_id": row.get("thread_id"),
                "task": row.get("task"),
                "status": row.get("status"),
                "updated_at": row.get("updated_at"),
                "overlap": overlap[:12],
                "overlap_count": len(overlap),
            }
        )

    return {
        "thread_id": current_thread,
        "active_threads": len(active),
        "conflicts": conflicts,
        "warning": bool(conflicts),
    }


def list_thread_rows(root: Path, *, active_only: bool = False, now: datetime | None = None) -> list[dict[str, Any]]:
    if active_only:
        return _active_rows(root, now=now)
    path = root / THREAD_INDEX
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("thread_id"):
            rows.append(row)
    return rows


def _active_rows(root: Path, now: datetime | None = None) -> list[dict[str, Any]]:
    path = root / THREAD_INDEX
    if not path.exists():
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=24)
    latest: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") in {"done", "handed_off"}:
            continue
        updated_at = _parse_datetime(row.get("updated_at"))
        if updated_at is None or updated_at < cutoff:
            continue
        thread_id = str(row.get("thread_id") or "")
        if not thread_id:
            continue
        previous = latest.get(thread_id)
        if previous is None or (updated_at > (_parse_datetime(previous.get("updated_at")) or cutoff)):
            latest[thread_id] = row
    return list(latest.values())


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)
