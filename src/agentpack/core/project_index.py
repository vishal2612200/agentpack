from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core import git


PROJECT_INDEX_SCHEMA_VERSION = 1
MAX_PROJECT_INDEX_ROWS = 200


def project_index_path() -> Path:
    base = os.environ.get("AGENTPACK_HOME")
    home = Path(base).expanduser() if base else Path.home() / ".agentpack"
    return home / "projects.json"


def load_project_index(path: Path | None = None) -> list[dict[str, Any]]:
    index_path = path or project_index_path()
    if not index_path.exists():
        return []
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(raw, dict):
        projects = raw.get("projects")
    else:
        projects = raw
    if not isinstance(projects, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        path_value = str(item.get("path") or "").strip()
        if not path_value:
            continue
        rows.append(dict(item, path=path_value))
    return rows


def register_project(root: Path, path: Path | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    index_path = path or project_index_path()
    project_root = root.expanduser().resolve()
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    existing_rows = load_project_index(index_path)
    existing_by_path = {str(row.get("path")): row for row in existing_rows if row.get("path")}
    previous = existing_by_path.get(str(project_root), {})
    row = _project_index_row(project_root, timestamp, previous)
    existing_by_path[str(project_root)] = row

    rows = sorted(
        existing_by_path.values(),
        key=lambda item: str(item.get("last_seen_at") or item.get("first_seen_at") or ""),
        reverse=True,
    )[:MAX_PROJECT_INDEX_ROWS]
    _write_project_index(index_path, rows, timestamp)
    return row


def _project_index_row(root: Path, timestamp: str, previous: dict[str, Any]) -> dict[str, Any]:
    is_git = git.is_git_repo(root)
    return {
        "path": str(root),
        "name": root.name or str(root),
        "first_seen_at": str(previous.get("first_seen_at") or timestamp),
        "last_seen_at": timestamp,
        "branch": git.current_branch(root) or "" if is_git else "",
        "git_sha": (git.current_sha(root) or "")[:12] if is_git else "",
        "agentpack_config": str(root / ".agentpack" / "config.toml"),
    }


def _write_project_index(path: Path, rows: list[dict[str, Any]], timestamp: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": PROJECT_INDEX_SCHEMA_VERSION,
        "updated_at": timestamp,
        "projects": rows,
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
