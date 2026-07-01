from __future__ import annotations

from pathlib import Path
from typing import Any

from agentpack.core import git
from agentpack.core.context_pack import load_pack_metadata
from agentpack.learning.collector import LearningInputs
from agentpack.session.events import read_events, record_event

MAX_TASK_CHARS = 500
MAX_SUMMARY_CHARS = 800
MAX_PATHS = 30
MAX_SELECTED = 20

_CONCEPT_TERMS: list[tuple[str, tuple[str, ...]]] = [
    ("mcp", ("mcp", "model context protocol", "retrieve_context")),
    ("authentication", ("auth", "token", "login", "permission", "jwt", "expired")),
    ("retry logic", ("retry", "retries", "attempt", "backoff", "timeout")),
    ("caching", ("cache", "redis", "memo", "ttl")),
    ("rate limiting", ("rate limit", "rate_limit", "ratelimit", "limiter", "throttle", "quota", "429")),
    ("configuration", ("config", "toml", "env", "setting", "yaml", "yml", "json")),
    ("testing", ("test_", "pytest", "assert ", "fixture", "/test", "tests/")),
    ("CLI design", ("typer", "@app.command", "option", "argument", "command", "cli")),
    ("context packing", ("pack", "context", "selected_files", "tokens", "ranking")),
    ("serialization", ("json", "model_dump", "pydantic", "schema", "toon")),
]


def record_task_memory(
    root: Path,
    *,
    task: str,
    stage: str,
    status: str,
    thread: str = "",
    summary: str = "",
    loop_summary: dict[str, Any] | None = None,
) -> None:
    """Append bounded task facts. No lesson generation or dashboard rendering."""
    payload = build_task_memory_payload(
        root,
        task=task,
        stage=stage,
        status=status,
        thread=thread,
        summary=summary,
        loop_summary=loop_summary,
    )
    record_event(root, "task_memory", payload)


def build_task_memory_payload(
    root: Path,
    *,
    task: str,
    stage: str,
    status: str,
    thread: str = "",
    summary: str = "",
    loop_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    changed = sorted(git.dirty_files(root))[:MAX_PATHS] if git.is_git_repo(root) else []
    selected = _selected_files(root)[:MAX_SELECTED]
    branch = git.current_branch(root) if git.is_git_repo(root) else None
    sha = git.current_sha(root) if git.is_git_repo(root) else None
    concepts = _infer_concepts(task, changed, selected)
    tests = [path for path in changed if path.startswith("tests/") or "/test" in path][:10]
    payload: dict[str, Any] = {
        "task": _clip(task, MAX_TASK_CHARS),
        "stage": stage,
        "status": status,
        "thread": _clip(thread, 120),
        "summary": _clip(summary, MAX_SUMMARY_CHARS),
        "branch": branch or "",
        "git_sha": sha or "",
        "changed_files": changed,
        "selected_files": selected,
        "concepts": concepts,
        "tests": tests,
        "provenance": {
            "cwd": str(root),
            "branch": branch or "",
            "git_sha": sha or "",
        },
    }
    if loop_summary:
        payload["loop"] = {
            "status": _clip(str(loop_summary.get("status") or ""), 80),
            "reason": _clip(str(loop_summary.get("reason") or ""), 300),
            "iterations": int(loop_summary.get("iterations") or 0),
        }
    return payload


def recent_task_memories(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = [event for event in read_events(root, limit=max(limit * 5, 50)) if event.get("type") == "task_memory"]
    return rows[-limit:]


def latest_task_memory(root: Path) -> dict[str, Any] | None:
    rows = recent_task_memories(root, limit=1)
    return rows[-1] if rows else None


def learning_inputs_from_memory(memory: dict[str, Any]) -> LearningInputs:
    task = str(memory.get("task") or "Recent task")
    changed_files = {path: "modified" for path in _str_list(memory.get("changed_files"))}
    concept_hint = " ".join(_str_list(memory.get("concepts")))
    return LearningInputs(
        task=task,
        since=None,
        changed_files=changed_files,
        diffs={path: concept_hint for path in changed_files},
        selected_files=_str_list(memory.get("selected_files")),
    )


def _selected_files(root: Path) -> list[str]:
    metadata = load_pack_metadata(root) or {}
    raw = metadata.get("selected_files_meta") or metadata.get("selected_files") or []
    selected: list[str] = []
    if not isinstance(raw, list):
        return selected
    for item in raw:
        if isinstance(item, str):
            selected.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            selected.append(str(item["path"]))
    return _unique(selected)


def _infer_concepts(task: str, changed: list[str], selected: list[str]) -> list[str]:
    haystack = "\n".join([task, *changed, *selected]).lower()
    return [concept for concept, terms in _CONCEPT_TERMS if any(term in haystack for term in terms)]


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _clip(value: str, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"
