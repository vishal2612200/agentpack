from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentpack.core import git
from agentpack.core.thread_context import ThreadPaths

_VALID_STATUSES = {"planned", "in_progress", "blocked", "handed_off", "done"}
_CHECKED_RE = re.compile(r"^\s*-\s*\[[xX]\]")
_OPEN_RE = re.compile(r"^\s*-\s*\[\s\]")
_BLOCKED_RE = re.compile(r"^\s*-\s*\[!\]")


def build_execution_state(root: Path, paths: ThreadPaths | None = None) -> dict[str, Any]:
    git_state = git.working_tree_summary(root)
    task_state = read_task_state(root, paths)
    if not task_state.get("status"):
        task_state["status"] = _derive_status(git_state)
        task_state["source"] = "derived"
    runtime_state = _runtime_state(root)
    return {
        "task": task_state,
        "git": git_state,
        "runtime": runtime_state,
    }


def read_task_state(root: Path, paths: ThreadPaths | None = None) -> dict[str, Any]:
    candidates: list[Path] = []
    if paths is not None:
        candidates.append(paths.task_state)
    candidates.append(root / ".agentpack" / "task_state.md")

    for path in candidates:
        if not path.exists():
            continue
        parsed = parse_task_state(path.read_text(encoding="utf-8", errors="replace"))
        parsed["state_file"] = _rel(path, root)
        parsed["source"] = "file"
        return parsed

    return {
        "status": "",
        "summary": "",
        "state_file": "",
        "source": "derived",
        "checklist": {"done": 0, "open": 0, "blocked": 0},
    }


def parse_task_state(text: str) -> dict[str, Any]:
    status = ""
    summary = ""
    done = open_items = blocked = 0
    for line in text.splitlines():
        lower = line.lower()
        if lower.startswith("status:"):
            candidate = line.split(":", 1)[1].strip().lower()
            status = candidate if candidate in _VALID_STATUSES else ""
        elif lower.startswith("summary:"):
            summary = line.split(":", 1)[1].strip()
        if _CHECKED_RE.match(line):
            done += 1
        elif _BLOCKED_RE.match(line):
            blocked += 1
        elif _OPEN_RE.match(line):
            open_items += 1
    return {
        "status": status,
        "summary": summary,
        "checklist": {"done": done, "open": open_items, "blocked": blocked},
    }


def compact_execution_state(state: dict[str, Any]) -> dict[str, Any]:
    compact = dict(state)
    runtime = dict(compact.get("runtime") or {})
    runtime.pop("detail", None)
    compact["runtime"] = runtime
    return compact


def _derive_status(git_state: dict[str, Any]) -> str:
    dirty = (
        int(git_state.get("staged_count") or 0)
        + int(git_state.get("unstaged_count") or 0)
        + int(git_state.get("untracked_count") or 0)
    )
    if dirty:
        return "in_progress"
    if int(git_state.get("ahead") or 0) > 0:
        return "committed_not_pushed"
    if git_state.get("sha"):
        return "committed"
    return "unknown"


def _runtime_state(root: Path) -> dict[str, Any]:
    compose_file = next(
        (name for name in ("docker-compose.yml", "compose.yaml", "compose.yml") if (root / name).exists()),
        "",
    )
    docker_bin = shutil.which("docker")
    if not docker_bin:
        docker = "missing"
        detail = "docker CLI not found"
    else:
        try:
            result = subprocess.run(
                [docker_bin, "info", "--format", "{{.ServerVersion}}"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                docker = "running"
                detail = result.stdout.strip() or "docker daemon running"
            else:
                docker = "daemon_unavailable"
                detail = (result.stderr or result.stdout).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            docker = "daemon_unavailable"
            detail = str(exc)
    return {"docker": docker, "compose_file": compose_file, "detail": detail[:160]}


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)
