from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core.node_identity import hash_text, normalize_repo_path
from agentpack.core.scanner import file_hash
from agentpack.learning.procedures import matching_procedures, record_memory_edge

EPISODIC_CASES_PATH = ".agentpack/episodic-cases.jsonl"
EPISODE_SCHEMA_VERSION = 2


def record_episode(
    root: Path,
    *,
    task: str,
    selected_files: list[str],
    changed_files: list[str],
    checks: list[dict[str, Any]] | None = None,
    passed: bool | None = None,
    failure_class: str = "",
    failure_source: str = "",
    context_hash: str = "",
    task_id: str = "",
    touched_nodes: list[dict[str, Any]] | None = None,
    procedure_ids: list[str] | None = None,
    final_git_sha: str = "",
    diff_hash: str = "",
    output_path: str = EPISODIC_CASES_PATH,
) -> None:
    if not task and not changed_files:
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    clean_changed = _bounded_paths(changed_files)
    clean_selected = _bounded_paths(selected_files)
    episode_id = "episode:" + hash_text("|".join([task, ",".join(clean_changed), timestamp]))[:16]
    nodes = _bounded_nodes(touched_nodes or _nodes_from_latest_task_start(root, [*clean_selected, *clean_changed]))
    record = {
        "schema_version": EPISODE_SCHEMA_VERSION,
        "episode_id": episode_id,
        "timestamp": timestamp,
        "completed_at": timestamp,
        "recorded_at": timestamp,
        "episode_version": f"{episode_id}@{timestamp}",
        "task_id": task_id,
        "task": task,
        "concepts": sorted(_terms(task)),
        "selected_files": clean_selected,
        "changed_files": clean_changed,
        "path_hashes": _path_hashes(root, [*selected_files, *changed_files]),
        "touched_nodes": nodes,
        "procedure_ids": _bounded_strings(procedure_ids or [], limit=20),
        "checks": checks or [],
        "passed": passed,
        "failure_class": failure_class,
        "failure_source": failure_source,
        "context_hash": context_hash,
        "final_git_sha": final_git_sha,
        "diff_hash": diff_hash,
        "confidence": 1.0 if passed else 0.4 if passed is False else 0.6,
        "visible_reason": "completed task episode with source hashes and touched nodes",
    }
    path = root / output_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    _record_episode_edges(root, record)


def episodic_memory_boosts(
    root: Path,
    task: str,
    *,
    output_path: str = EPISODIC_CASES_PATH,
    procedures_path: str = ".agentpack/procedures.jsonl",
    max_boost: float = 12.0,
    limit: int = 500,
) -> dict[str, float]:
    boosts: dict[str, float] = {}
    for match in episodic_memory_matches(
        root,
        task,
        output_path=output_path,
        procedures_path=procedures_path,
        max_boost=max_boost,
        limit=limit,
    ):
        path = str(match.get("path") or "")
        boost = float(match.get("boost") or 0)
        if path and boost > 0:
            boosts[path] = min(max_boost, boosts.get(path, 0.0) + boost)
    return boosts


def episodic_memory_matches(
    root: Path,
    task: str,
    *,
    output_path: str = EPISODIC_CASES_PATH,
    procedures_path: str = ".agentpack/procedures.jsonl",
    max_boost: float = 12.0,
    limit: int = 500,
    eligible_paths: set[str] | None = None,
    eligible_node_keys: set[str] | None = None,
    eligible_episode_ids: set[str] | None = None,
    explicit_procedures_only: bool = False,
    current_source_hashes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    task_terms = _terms(task)
    node_keys = {str(key) for key in eligible_node_keys or set() if key}
    episode_ids = {str(value) for value in eligible_episode_ids or set() if value}
    if not task_terms and not node_keys:
        return []
    matches: list[dict[str, Any]] = []
    eligible = {normalize_repo_path(path) for path in eligible_paths or [] if path}
    for record in _read_jsonl(root / output_path, limit=limit):
        episode_id = str(record.get("episode_id") or "")
        if episode_ids and episode_id not in episode_ids:
            continue
        matched_nodes = _matching_current_nodes(root, record, node_keys, current_source_hashes)
        if node_keys and not matched_nodes:
            continue
        episode_terms = _terms(str(record.get("task") or ""))
        for concept in record.get("concepts") or []:
            if isinstance(concept, str):
                episode_terms |= _terms(concept)
        overlap = task_terms & episode_terms
        if not overlap and not matched_nodes:
            continue
        failed = record.get("passed") is False
        location_match = bool(matched_nodes)
        weight = 0.0 if failed else min(max_boost, (6.0 if location_match else 4.0) + len(overlap) * 2.0)
        confidence = min(0.98, (0.8 if location_match else 0.55) + len(overlap) * 0.06)
        procedures = matching_procedures(
            root,
            task,
            record,
            output_path=procedures_path,
            explicit_only=explicit_procedures_only,
        )
        if procedures and not failed:
            weight = min(max_boost, weight + 2.0)
            confidence = min(0.98, confidence + 0.1)
        path_hashes = record.get("path_hashes") if isinstance(record.get("path_hashes"), dict) else {}
        for path in record.get("changed_files") or []:
            normalized_path = normalize_repo_path(path) if isinstance(path, str) else ""
            if node_keys and normalized_path not in {node["path"] for node in matched_nodes}:
                continue
            if isinstance(path, str) and (not eligible or normalized_path in eligible) and _path_is_current(root, path, path_hashes, current_source_hashes):
                matches.append(
                    {
                        "path": normalized_path,
                        "boost": weight,
                        "episode_id": episode_id,
                        "task_id": str(record.get("task_id") or ""),
                        "confidence": confidence,
                        "negative_guidance": failed,
                        "matched_node_keys": [node["node_key"] for node in matched_nodes],
                        "retrieval_source": "node_identity" if location_match else "task_terms",
                        "visible_reason": (
                            (
                                f"failed episode: avoid repeating the prior approach; {_memory_match_reason(overlap, matched_nodes)}; "
                                "source hash still current"
                                if failed
                                else f"episodic memory match; {_memory_match_reason(overlap, matched_nodes)}; source hash still current"
                            )
                        ),
                        "node_ids": _node_ids_for_path(record, path),
                        "procedures": procedures,
                    }
                )
    return matches


def _matching_current_nodes(
    root: Path,
    record: dict[str, Any],
    eligible_node_keys: set[str],
    current_source_hashes: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    if not eligible_node_keys:
        return []
    matches: list[dict[str, str]] = []
    for node in record.get("touched_nodes") or []:
        if not isinstance(node, dict):
            continue
        node_key = str(node.get("node_key") or node.get("node_id") or "")
        path = normalize_repo_path(str(node.get("path") or ""))
        if node_key not in eligible_node_keys or not path or not _node_is_current(root, path, str(node.get("source_hash") or ""), current_source_hashes):
            continue
        matches.append({"node_key": node_key, "path": path})
    return matches


def _node_is_current(root: Path, path: str, expected_hash: str, current_source_hashes: dict[str, str] | None = None) -> bool:
    if not expected_hash:
        return True
    if current_source_hashes is not None:
        return current_source_hashes.get(normalize_repo_path(path)) == expected_hash
    abs_path = root / path
    if not abs_path.exists() or not abs_path.is_file():
        return False
    try:
        return file_hash(abs_path) == expected_hash
    except OSError:
        return False


def _memory_match_reason(overlap: set[str], matched_nodes: list[dict[str, str]]) -> str:
    if matched_nodes:
        return "node=" + ", ".join(node["node_key"] for node in matched_nodes[:3])
    return "overlap=" + ", ".join(sorted(overlap)[:5])


def _read_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", value.lower())
        if term not in {"the", "and", "for", "with", "this", "that", "from", "into", "agentpack"}
    }


def _bounded_paths(paths: list[str], limit: int = 80) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        value = str(path).strip().replace("\\", "/")
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _bounded_strings(values: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean[:160])
        if len(result) >= limit:
            break
    return result


def _bounded_nodes(nodes: list[dict[str, Any]], limit: int = 80) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "").strip()
        node_key = str(node.get("node_key") or node_id).strip()
        path = normalize_repo_path(str(node.get("path") or ""))
        if not node_key or node_key in seen:
            continue
        seen.add(node_key)
        source_hash = str(node.get("source_hash") or "")[:120]
        result.append(
            {
                "node_id": node_key[:160],
                "node_key": node_key[:160],
                "revision_id": str(node.get("revision_id") or "node-revision:" + hash_text(f"{node_key}|{source_hash}")[:20])[:160],
                "path": path,
                "symbol": str(node.get("symbol") or "")[:160],
                "kind": str(node.get("kind") or "")[:40],
                "source_hash": source_hash,
                "confidence": _confidence(node.get("confidence"), default=1.0),
                "observed_at": str(node.get("observed_at") or "")[:80],
                "visible_reason": str(node.get("visible_reason") or "node touched by task")[:240],
            }
        )
        if len(result) >= limit:
            break
    return result


def _nodes_from_latest_task_start(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    start_path = root / ".agentpack" / "task-starts.jsonl"
    records = _read_jsonl(start_path, limit=1)
    if not records:
        return []
    path_set = set(_bounded_paths(paths))
    nodes: list[dict[str, Any]] = []
    for node in records[-1].get("node_refs") or []:
        if isinstance(node, dict) and (not path_set or normalize_repo_path(str(node.get("path") or "")) in path_set):
            nodes.append(node)
    return nodes


def _node_ids_for_path(record: dict[str, Any], path: str) -> list[str]:
    rel = normalize_repo_path(path)
    ids: list[str] = []
    for node in record.get("touched_nodes") or []:
        if isinstance(node, dict) and normalize_repo_path(str(node.get("path") or "")) == rel:
            node_key = str(node.get("node_key") or node.get("node_id") or "")
            if node_key:
                ids.append(node_key)
    return ids[:10]


def _record_episode_edges(root: Path, record: dict[str, Any]) -> None:
    episode_id = str(record.get("episode_id") or "")
    provenance = {
        "task_id": str(record.get("task_id") or ""),
        "episode_id": episode_id,
        "timestamp": str(record.get("timestamp") or ""),
    }
    for node in record.get("touched_nodes") or []:
        if not isinstance(node, dict):
            continue
        record_memory_edge(
            root,
            from_id=str(node.get("node_key") or node.get("node_id") or ""),
            to_id=episode_id,
            edge_type="node_episode",
            confidence=_confidence(node.get("confidence"), default=_confidence(record.get("confidence"), default=0.6)),
            reason=str(node.get("visible_reason") or "node touched by episode"),
            provenance=provenance,
            source_hash=str(node.get("source_hash") or ""),
        )
    for procedure_id in record.get("procedure_ids") or []:
        record_memory_edge(
            root,
            from_id=episode_id,
            to_id=str(procedure_id),
            edge_type="episode_procedure",
            confidence=_confidence(record.get("confidence"), default=0.6),
            reason="procedure linked by completed episode",
            provenance=provenance,
        )


def _path_hashes(root: Path, paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in _bounded_paths(paths):
        abs_path = root / path
        if not abs_path.exists() or not abs_path.is_file():
            continue
        try:
            hashes[path] = file_hash(abs_path)
        except OSError:
            continue
    return hashes


def _path_is_current(
    root: Path,
    path: str,
    path_hashes: object,
    current_source_hashes: dict[str, str] | None = None,
) -> bool:
    if not path:
        return False
    if current_source_hashes is None:
        abs_path = root / path
        if not abs_path.exists() or not abs_path.is_file():
            return False
    if not isinstance(path_hashes, dict):
        return True
    normalized_path = normalize_repo_path(path)
    expected = path_hashes.get(path) or path_hashes.get(normalized_path)
    if not isinstance(expected, str) or not expected:
        return True
    if current_source_hashes is not None:
        return current_source_hashes.get(normalized_path) == expected
    try:
        return file_hash(abs_path) == expected
    except OSError:
        return False


def _confidence(value: object, *, default: float) -> float:
    try:
        parsed = float(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))
