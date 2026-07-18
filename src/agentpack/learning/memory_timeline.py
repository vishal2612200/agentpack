from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentpack.core.config import load_config
from agentpack.core.scanner import file_hash
from agentpack.learning.procedures import MEMORY_EDGES_PATH, PROCEDURES_PATH
from agentpack.learning.task_memory import TASK_STARTS_PATH


def build_memory_timeline(root: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    cfg = load_config(root)
    rows: list[dict[str, Any]] = []
    rows.extend(
        _timeline_rows(
            _read_jsonl(root / getattr(cfg.learning, "task_starts_output", TASK_STARTS_PATH), limit=limit),
            root=root,
            kind="task_start",
            id_field="task_id",
        )
    )
    rows.extend(
        _timeline_rows(
            _read_jsonl(root / cfg.learning.episodic_cases_output, limit=limit),
            root=root,
            kind="episode",
            id_field="episode_id",
        )
    )
    rows.extend(
        _timeline_rows(
            _read_jsonl(root / getattr(cfg.learning, "procedures_output", PROCEDURES_PATH), limit=limit),
            root=root,
            kind="procedure",
            id_field="procedure_id",
        )
    )
    rows.extend(
        _timeline_rows(
            _read_jsonl(root / getattr(cfg.learning, "memory_edges_output", MEMORY_EDGES_PATH), limit=limit),
            root=root,
            kind="memory_edge",
            id_field="edge_version",
        )
    )
    rows.sort(key=lambda item: (item.get("timestamp") or "", item.get("kind") or ""))
    return rows[-limit:]


def _timeline_rows(records: list[dict[str, Any]], *, root: Path, kind: str, id_field: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        timestamp = _record_timestamp(record)
        stale_paths = _stale_paths(root, record)
        rows.append(
            {
                "kind": kind,
                "id": str(record.get(id_field) or record.get("id") or ""),
                "timestamp": timestamp,
                "version": _record_version(record),
                "schema_version": record.get("schema_version"),
                "record_hash": _record_hash(record),
                "task_id": record.get("task_id", ""),
                "from_id": record.get("from_id", ""),
                "to_id": record.get("to_id", ""),
                "edge_type": record.get("edge_type", ""),
                "confidence": record.get("confidence", ""),
                "visible_reason": record.get("visible_reason") or record.get("reason") or "",
                "source_hash": record.get("source_hash", ""),
                "stale_paths": stale_paths,
                "is_stale": bool(stale_paths),
            }
        )
    return rows


def _record_timestamp(record: dict[str, Any]) -> str:
    for field in ("observed_at", "completed_at", "started_at", "recorded_at", "updated_at", "created_at", "timestamp"):
        value = record.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _record_version(record: dict[str, Any]) -> str:
    for field in ("snapshot_version", "episode_version", "version_key", "edge_version"):
        value = record.get(field)
        if isinstance(value, str) and value:
            return value
    version = record.get("version")
    return str(version) if version not in (None, "") else ""


def _record_hash(record: dict[str, Any]) -> str:
    for field in ("relationship_hash", "procedure_hash", "context_pack_hash", "context_snapshot_root_hash", "diff_hash", "source_hash"):
        value = record.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _stale_paths(root: Path, record: dict[str, Any]) -> list[str]:
    path_hashes = record.get("path_hashes")
    if not isinstance(path_hashes, dict):
        return []
    stale: list[str] = []
    for path, expected in path_hashes.items():
        if not isinstance(path, str) or not isinstance(expected, str) or not expected:
            continue
        current = root / path
        try:
            if not current.exists() or not current.is_file() or file_hash(current) != expected:
                stale.append(path)
        except OSError:
            stale.append(path)
        if len(stale) >= 20:
            break
    return stale


def _read_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
