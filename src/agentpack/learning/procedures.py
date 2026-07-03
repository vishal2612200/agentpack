from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core.node_identity import hash_text

PROCEDURES_PATH = ".agentpack/procedures.jsonl"
MEMORY_EDGES_PATH = ".agentpack/memory-edges.jsonl"
PROCEDURE_SCHEMA_VERSION = 1


def record_procedure(
    root: Path,
    *,
    procedure_id: str,
    title: str,
    triggers: list[str],
    steps: list[str],
    validation: list[str] | None = None,
    version: int = 1,
    output_path: str = PROCEDURES_PATH,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "schema_version": PROCEDURE_SCHEMA_VERSION,
        "procedure_id": _clean_id(procedure_id) or "procedure:" + hash_text(title)[:12],
        "version": max(1, int(version)),
        "title": _clip(title, 160),
        "triggers": _clean_list(triggers, 40, 80),
        "steps": _clean_list(steps, 20, 240),
        "validation": _clean_list(validation or [], 20, 200),
        "last_validated_at": "",
        "created_at": now,
        "updated_at": now,
    }
    record["version_key"] = f"{record['procedure_id']}@v{record['version']}"
    record["procedure_hash"] = hash_text(json.dumps(record, sort_keys=True, separators=(",", ":")))[:16]
    _append_jsonl(root / output_path, record)
    return record


def record_memory_edge(
    root: Path,
    *,
    from_id: str,
    to_id: str,
    edge_type: str,
    confidence: float,
    reason: str,
    provenance: dict[str, Any] | None = None,
    source_hash: str = "",
    output_path: str = MEMORY_EDGES_PATH,
) -> None:
    if not from_id or not to_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    clean_provenance = provenance or {}
    clean_source_hash = _clip(source_hash, 120)
    relationship_hash = hash_text(
        json.dumps(
            {
                "edge_type": edge_type,
                "from_id": from_id,
                "provenance": clean_provenance,
                "source_hash": clean_source_hash,
                "to_id": to_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )[:16]
    clean_reason = _clip(reason, 240)
    edge = {
        "schema_version": PROCEDURE_SCHEMA_VERSION,
        "from_id": _clip(from_id, 160),
        "to_id": _clip(to_id, 160),
        "edge_type": _clip(edge_type, 80),
        "confidence": _confidence(confidence),
        "reason": clean_reason,
        "visible_reason": clean_reason,
        "provenance": clean_provenance,
        "source_hash": clean_source_hash,
        "relationship_hash": relationship_hash,
        "created_at": now,
        "observed_at": now,
        "edge_version": "edge:" + hash_text("|".join([from_id, to_id, edge_type, now]))[:16],
    }
    _append_jsonl(root / output_path, edge)


def load_procedures(root: Path, *, output_path: str = PROCEDURES_PATH, limit: int = 500) -> list[dict[str, Any]]:
    return _read_jsonl(root / output_path, limit=limit)


def matching_procedures(
    root: Path,
    task: str,
    episode: dict[str, Any],
    *,
    output_path: str = PROCEDURES_PATH,
) -> list[dict[str, Any]]:
    task_terms = _terms(task)
    episode_terms = _terms(str(episode.get("task") or ""))
    for concept in episode.get("concepts") or []:
        if isinstance(concept, str):
            episode_terms |= _terms(concept)
    explicit_ids = {str(value) for value in episode.get("procedure_ids") or [] if isinstance(value, str)}
    matches: list[dict[str, Any]] = []
    for procedure in load_procedures(root, output_path=output_path):
        procedure_id = str(procedure.get("procedure_id") or "")
        trigger_terms = _terms(" ".join(str(item) for item in procedure.get("triggers") or []))
        explicit = bool(procedure_id and procedure_id in explicit_ids)
        overlap = task_terms & (trigger_terms | episode_terms)
        if not explicit and not overlap:
            continue
        confidence = 0.75 if explicit else min(0.7, 0.45 + len(overlap) * 0.05)
        matches.append(
            {
                "procedure_id": procedure_id,
                "title": str(procedure.get("title") or procedure_id),
                "confidence": confidence,
                "visible_reason": (
                    "procedure explicitly linked by prior episode"
                    if explicit
                    else f"procedure trigger overlap: {', '.join(sorted(overlap)[:5])}"
                ),
            }
        )
    return matches[:5]


def _terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", value.lower())
        if term not in {"the", "and", "for", "with", "this", "that", "from", "into", "agentpack"}
    }


def _clean_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "-", value.strip())[:120]


def _clean_list(values: list[str], limit: int, item_limit: int) -> list[str]:
    out: list[str] = []
    for value in values:
        clean = _clip(str(value), item_limit)
        if clean:
            out.append(clean)
        if len(out) >= limit:
            break
    return out


def _clip(value: str, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _confidence(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.5
    return max(0.0, min(1.0, parsed))


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
