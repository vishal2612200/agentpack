from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core import git
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.node_identity import hash_text, normalize_repo_path
from agentpack.core.pack_registry import load_pack_registry
from agentpack.core.redactor import redact_secrets
from agentpack.core.scanner import file_hash
from agentpack.core.thread_context import thread_paths
from agentpack.learning.collector import LearningInputs
from agentpack.observer.events import record_task_observation
from agentpack.observer.brief import write_observer_brief
from agentpack.session.events import read_events, record_event

TASK_STARTS_PATH = ".agentpack/task-starts.jsonl"
TASK_MEMORY_SCHEMA_VERSION = 1
MAX_TASK_CHARS = 500
MAX_SUMMARY_CHARS = 800
MAX_PATHS = 30
MAX_SELECTED = 20
MAX_NODE_REFS = 80

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
    try:
        record_task_observation(root, payload)
        write_observer_brief(root, task=str(payload.get("task") or ""))
    except Exception:
        pass


def record_task_start_snapshot(
    root: Path,
    *,
    task: str,
    thread: str = "",
    agent: str = "",
    context_path: Path | None = None,
    dirty_files_before: list[str] | None = None,
    output_path: str = TASK_STARTS_PATH,
) -> dict[str, Any]:
    snapshot = build_task_start_snapshot(
        root,
        task=task,
        thread=thread,
        agent=agent,
        context_path=context_path,
        dirty_files_before=dirty_files_before,
    )
    _append_jsonl(root / output_path, snapshot)
    record_event(root, "task_start_snapshot", snapshot)
    return snapshot


def record_task_event(
    root: Path,
    *,
    task_id: str,
    event_type: str,
    summary: str = "",
    path: str = "",
    node_id: str = "",
    confidence: float = 0.5,
    provenance: dict[str, Any] | None = None,
) -> None:
    observed_at = datetime.now(timezone.utc).isoformat()
    redacted_summary, warnings = redact_secrets(_clip(summary, MAX_SUMMARY_CHARS), path or "task_event")
    payload = {
        "schema_version": TASK_MEMORY_SCHEMA_VERSION,
        "task_id": _clip(task_id, 120),
        "event_id": "event:" + hash_text("|".join([task_id, event_type, path, node_id, observed_at]))[:16],
        "event_type": _clip(event_type, 80),
        "observed_at": observed_at,
        "summary": redacted_summary,
        "path": normalize_repo_path(path) if path else "",
        "node_id": _clip(node_id, 120),
        "confidence": max(0.0, min(1.0, float(confidence))),
        "redaction_warnings": warnings,
        "provenance": provenance or {},
    }
    record_event(root, "task_memory_event", payload)


def build_task_start_snapshot(
    root: Path,
    *,
    task: str,
    thread: str = "",
    agent: str = "",
    context_path: Path | None = None,
    dirty_files_before: list[str] | None = None,
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()
    redacted_task, warnings = redact_secrets(_clip(task, MAX_TASK_CHARS), "task")
    selected = _selected_files(root, thread=thread)[:MAX_SELECTED]
    branch = git.current_branch(root) if git.is_git_repo(root) else None
    sha = git.current_sha(root) if git.is_git_repo(root) else None
    dirty_source = sorted(git.dirty_files(root)) if dirty_files_before is None else dirty_files_before
    dirty_before = _bounded_paths(dirty_source)[:MAX_PATHS]
    resolved_context = context_path or _context_path_from_metadata(root, thread)
    context_rel = _rel_to_root(resolved_context, root) if resolved_context else ""
    context_hash = _file_hash_or_empty(resolved_context) if resolved_context else ""
    task_id = "task:" + hash_text("|".join([redacted_task, thread, agent, branch or "", sha or "", timestamp]))[:16]
    node_refs = _node_refs_from_registry(root, selected)
    return {
        "schema_version": TASK_MEMORY_SCHEMA_VERSION,
        "task_id": task_id,
        "timestamp": timestamp,
        "started_at": timestamp,
        "recorded_at": timestamp,
        "snapshot_version": f"{task_id}@{timestamp}",
        "task": redacted_task,
        "thread": _clip(thread, 120),
        "agent": _clip(agent, 80),
        "branch": branch or "",
        "git_sha": sha or "",
        "dirty_files_before": dirty_before,
        "selected_files": selected,
        "node_refs": node_refs,
        "context_path": context_rel,
        "context_pack_hash": context_hash,
        "context_snapshot_root_hash": _pack_metadata(root, thread).get("snapshot_root_hash", ""),
        "confidence": 1.0,
        "visible_reason": "task-start map captured before agent edits; live source remains authority",
        "redaction_warnings": warnings,
        "provenance": {
            "cwd": str(root),
            "branch": branch or "",
            "git_sha": sha or "",
            "context_path": context_rel,
        },
    }


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
    selected = _selected_files(root, thread=thread)[:MAX_SELECTED]
    branch = git.current_branch(root) if git.is_git_repo(root) else None
    sha = git.current_sha(root) if git.is_git_repo(root) else None
    concepts = _infer_concepts(task, changed, selected)
    tests = [path for path in changed if path.startswith("tests/") or "/test" in path][:10]
    payload: dict[str, Any] = {
        "schema_version": TASK_MEMORY_SCHEMA_VERSION,
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


def recent_task_start_snapshots(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    return _read_jsonl(root / TASK_STARTS_PATH, limit=limit)


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


def _selected_files(root: Path, thread: str = "") -> list[str]:
    metadata = _pack_metadata(root, thread)
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


def _bounded_paths(paths: list[str], limit: int = MAX_PATHS) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        value = normalize_repo_path(str(path).strip())
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _pack_metadata(root: Path, thread: str = "") -> dict[str, Any]:
    scoped = thread_paths(root, thread or None)
    return load_pack_metadata(root, scoped.metadata if scoped else None) or {}


def _context_path_from_metadata(root: Path, thread: str = "") -> Path | None:
    value = _pack_metadata(root, thread).get("context_path")
    if isinstance(value, str) and value:
        return root / value
    return None


def _node_refs_from_registry(root: Path, selected: list[str]) -> list[dict[str, Any]]:
    registry = load_pack_registry(root)
    if registry is None:
        return []
    observed_at = str(registry.generated_at or "")
    selected_set = set(selected)
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in registry.records:
        if record.include_mode != "symbol" or not record.symbol:
            continue
        if selected_set and record.path not in selected_set:
            continue
        node_id = record.node_id or ""
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        refs.append(
            {
                "node_id": node_id,
                "path": record.path,
                "symbol": record.symbol,
                "source_hash": record.file_hash or "",
                "content_hash": record.content_hash,
                "confidence": 1.0,
                "observed_at": observed_at,
                "visible_reason": "selected symbol from latest AgentPack pack registry",
            }
        )
        if len(refs) >= MAX_NODE_REFS:
            break
    return refs


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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _read_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _file_hash_or_empty(path: Path) -> str:
    try:
        return file_hash(path) if path.exists() and path.is_file() else ""
    except OSError:
        return ""


def _rel_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)
