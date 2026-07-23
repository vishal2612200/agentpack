from __future__ import annotations

import json
import hashlib
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.session.identity import remember_external_thread_ids, resolve_identity


DEFAULT_EVENTS_PATH = ".agentpack/session-events.jsonl"
EVENT_SCHEMA_VERSION = 1
_IDENTITY_FIELDS = (
    "project_id",
    "workspace_id",
    "task_id",
    "logical_task_id",
    "session_id",
    "external_thread_ids",
    "agent",
)

_CANONICAL_EVENT_TYPES = {
    "pack": "context_prepared",
    "task_memory": "memory_recorded",
    "task_memory_event": "memory_recorded",
    "task_start_snapshot": "task_started",
}


def configured_events_output(root: Path) -> str:
    """Return the configured event path without making configuration mandatory."""
    try:
        from agentpack.core.config import load_config

        return load_config(root).runtime.session_events_output or DEFAULT_EVENTS_PATH
    except Exception:
        return DEFAULT_EVENTS_PATH


def record_event(
    root: Path,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    output_path: str | None = None,
    source: str = "agentpack",
) -> dict[str, Any]:
    path = root / (output_path or configured_events_output(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload or {})
    identity = resolve_identity(
        root,
        task=str(data.get("task") or ""),
        thread_id=str(data.get("thread_id") or data.get("thread") or ""),
        agent=str(data.get("agent") or ""),
        explicit_task_id=str(data.get("task_id") or ""),
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": "event-" + uuid.uuid4().hex[:20],
        "event_type": _CANONICAL_EVENT_TYPES.get(event_type, event_type),
        "occurred_at": timestamp,
        "type": event_type,
        "timestamp": timestamp,
        "source": source,
        "evidence": data.pop("evidence", []) if isinstance(data.get("evidence", []), list) else [],
        "payload": data,
        **data,
    }
    # Canonical identity wins over legacy payload fields such as timestamp-based
    # task IDs, while the original fields remain available in payload.
    event.update(identity)
    event["event_type"] = _CANONICAL_EVENT_TYPES.get(event_type, event_type)
    remember_external_thread_ids(root, identity["external_thread_ids"])
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    return event


def read_events(root: Path, *, output_path: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, int(limit))
    resolved_output = output_path or configured_events_output(root)
    paths = [root / resolved_output]
    legacy_path = root / DEFAULT_EVENTS_PATH
    if legacy_path not in paths:
        paths.append(legacy_path)
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    base_identity: dict[str, Any] | None = None
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if not all(key in rec for key in _IDENTITY_FIELDS) and base_identity is None:
                base_identity = resolve_identity(root)
            normalized = normalize_event(root, rec, base_identity=base_identity)
            event_id = str(normalized.get("event_id") or "")
            if event_id and event_id in seen_ids:
                continue
            if event_id:
                seen_ids.add(event_id)
            rows.append(normalized)
    rows.sort(key=lambda item: (str(item.get("occurred_at") or item.get("timestamp") or ""), str(item.get("event_id") or "")))
    return rows[-limit:]


def normalize_event(root: Path, event: dict[str, Any], *, base_identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a compatibility-preserving canonical view without rewriting history."""
    result = dict(event)
    legacy_type = str(result.get("type") or result.get("event_type") or "unknown")
    timestamp = str(result.get("timestamp") or result.get("occurred_at") or "")
    result.setdefault("schema_version", EVENT_SCHEMA_VERSION)
    result.setdefault("event_type", _CANONICAL_EVENT_TYPES.get(legacy_type, legacy_type))
    result.setdefault("type", legacy_type)
    result.setdefault("timestamp", timestamp)
    result.setdefault("occurred_at", timestamp)
    result.setdefault("source", "legacy")
    result.setdefault("evidence", [])
    if not isinstance(result.get("evidence"), list):
        result["evidence"] = []
    if not isinstance(result.get("payload"), dict):
        result["payload"] = {
            key: value
            for key, value in result.items()
            if key not in {"schema_version", "event_id", "event_type", "occurred_at", "type", "timestamp", "source", "evidence", "payload"}
        }
    if not all(key in result for key in _IDENTITY_FIELDS):
        identity = resolve_identity(
            root,
            task=str(result.get("task") or result.get("payload", {}).get("task") or ""),
            thread_id=str(result.get("thread_id") or result.get("thread") or ""),
            agent=str(result.get("agent") or ""),
            explicit_task_id=str(result.get("task_id") or ""),
            base=base_identity,
        )
        for key, value in identity.items():
            if key == "external_thread_ids":
                existing = result.get(key) if isinstance(result.get(key), list) else []
                result[key] = list(dict.fromkeys([*existing, *value]))[:20]
            else:
                result[key] = value or result.get(key, "")
    if not result.get("event_id"):
        stable = json.dumps(result, sort_keys=True, separators=(",", ":"))
        result["event_id"] = "event-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]
    return result


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(event.get("type") or "unknown") for event in events)
    packed_tokens = sum(int(event.get("packed_tokens") or 0) for event in events if event.get("type") == "pack")
    raw_tokens = sum(int(event.get("raw_tokens") or 0) for event in events if event.get("type") == "pack")
    retrievals = counts.get("retrieve", 0)
    output_compressions = counts.get("compress_output", 0)
    return {
        "events": len(events),
        "counts": dict(counts),
        "packed_tokens": packed_tokens,
        "raw_tokens": raw_tokens,
        "estimated_saved_tokens": max(0, raw_tokens - packed_tokens),
        "retrievals": retrievals,
        "output_compressions": output_compressions,
    }
