"""Stable local identities shared by sessions, tasks, and event records."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


SESSION_FILE = ".agentpack/session.json"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def repository_path(root: Path) -> str:
    result = _run_git(root, ["git", "rev-parse", "--show-toplevel"])
    return str(Path(result).resolve()) if result else str(root.resolve())


def project_id(root: Path) -> str:
    common_dir = _run_git(root, ["git", "rev-parse", "--git-common-dir"])
    if common_dir:
        path = Path(common_dir)
        identity = str((root / path).resolve() if not path.is_absolute() else path.resolve())
    else:
        identity = repository_path(root)
    return "project-" + digest(identity)


def workspace_id(root: Path, *, current_project_id: str | None = None) -> str:
    project = current_project_id or project_id(root)
    return "workspace-" + digest(f"{project}:{root.resolve()}")


def task_id(root: Path, task: str, *, thread_id: str = "", explicit: str = "") -> str:
    if explicit.startswith("task-"):
        return explicit
    title = " ".join(task.strip().split())
    if not title:
        return ""
    return "task-" + digest(f"{project_id(root)}:{workspace_id(root)}:{thread_id}:{title}")


def session_id(root: Path) -> str:
    data = _load_session(root)
    if not data:
        return ""
    existing = str(data.get("session_id") or "").strip()
    if existing:
        return existing
    started_at = str(data.get("started_at") or "")
    agent = str(data.get("agent") or "generic")
    if not started_at:
        return ""
    return "session-" + digest(f"{project_id(root)}:{workspace_id(root)}:{agent}:{started_at}")


def external_thread_ids(root: Path, *values: object) -> list[str]:
    data = _load_session(root) or {}
    candidates: list[object] = [*(data.get("external_thread_ids") or []), *values]
    result: list[str] = []
    for value in candidates:
        if isinstance(value, list):
            candidates.extend(value)
            continue
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text[:120])
    return result[:20]


def remember_external_thread_ids(root: Path, values: list[str]) -> None:
    if not values:
        return
    path = root / SESSION_FILE
    data = _load_session(root)
    if data is None:
        return
    existing = data.get("external_thread_ids") if isinstance(data.get("external_thread_ids"), list) else []
    merged = list(dict.fromkeys([str(item) for item in [*existing, *values] if item]))[:20]
    if merged == existing:
        return
    data["external_thread_ids"] = merged
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def resolve_identity(
    root: Path,
    *,
    task: str = "",
    thread_id: str = "",
    agent: str = "",
    explicit_task_id: str = "",
) -> dict[str, Any]:
    data = _load_session(root) or {}
    resolved_agent = agent.strip() or str(data.get("agent") or "generic")
    threads = external_thread_ids(root, thread_id)
    return {
        "project_id": project_id(root),
        "workspace_id": workspace_id(root),
        "task_id": task_id(root, task, thread_id=thread_id, explicit=explicit_task_id),
        "session_id": session_id(root),
        "external_thread_ids": threads,
        "agent": resolved_agent,
    }


def _load_session(root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((root / SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _run_git(root: Path, command: list[str]) -> str:
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
