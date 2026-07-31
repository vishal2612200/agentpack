"""Bounded, auditable memory retrieval rooted in live architecture evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from agentpack.core.node_identity import normalize_repo_path
from agentpack.learning.episodes import episodic_memory_matches
from agentpack.learning.procedures import MEMORY_EDGES_PATH, load_procedures

MEMORY_RETRIEVAL_SCHEMA_VERSION = 2


def retrieve_memory_chain(
    root: Path,
    *,
    task: str,
    live_paths: Iterable[str],
    live_entity_keys: Iterable[str] = (),
    architecture_edges: Iterable[Any] = (),
    entity_node_keys: dict[str, str] | None = None,
    max_boost: float = 12.0,
) -> dict[str, Any]:
    """Retrieve location-matched history before task-text/path fallback."""
    paths = sorted({normalize_repo_path(path) for path in live_paths if path})
    entity_keys = sorted({str(key) for key in live_entity_keys if key})
    one_hop = _one_hop_edges(architecture_edges, set(entity_keys))
    node_keys_by_entity = entity_node_keys or {}
    related_entity_keys = _related_entity_keys(entity_keys, one_hop)
    node_keys = sorted({node_keys_by_entity[key] for key in related_entity_keys if node_keys_by_entity.get(key)})
    memory_edges = _load_memory_edges(root)
    edge_episode_ids = _episode_ids_for_nodes(memory_edges, set(node_keys))
    episodes = episodic_memory_matches(
        root,
        task,
        max_boost=max_boost,
        eligible_paths=set(paths) if not node_keys else None,
        eligible_node_keys=set(node_keys) or None,
        eligible_episode_ids=edge_episode_ids or None,
        explicit_procedures_only=bool(node_keys),
    )
    procedures = _procedures(root, episodes, memory_edges)
    boosts = {
        str(item["path"]): min(max_boost, float(item.get("boost") or 0.0))
        for item in episodes
        if item.get("path") in paths and not item.get("negative_guidance") and float(item.get("boost") or 0.0) > 0
    }
    return {
        "schema_version": MEMORY_RETRIEVAL_SCHEMA_VERSION,
        "order": ["live_pr_entities", "architecture_one_hop", "compatible_episodes", "validated_procedures"],
        "live_pr_entities": {
            "entity_keys": entity_keys[:100],
            "paths": paths[:100],
            "node_keys": node_keys[:100],
        },
        "architecture_one_hop": one_hop,
        "compatible_episodes": episodes[:50],
        "validated_procedures": procedures[:25],
        "candidate_boosts": boosts,
        "constraints": {
            "candidate_scope": "live paths only",
            "episode_gate": "current node identity" if node_keys else "task/path fallback; no live node identity",
            "max_boost": max_boost,
            "blocking_effect": "none",
            "failed_episodes": "negative guidance only",
        },
    }


def _one_hop_edges(edges: Iterable[Any], live_entity_keys: set[str]) -> list[dict[str, str]]:
    if not live_entity_keys:
        return []
    result: list[dict[str, str]] = []
    for edge in edges:
        source = _field(edge, "source_entity_key")
        target = _field(edge, "target_entity_key")
        if source not in live_entity_keys and target not in live_entity_keys:
            continue
        result.append(
            {
                "edge_key": _field(edge, "edge_key"),
                "edge_type": _field(edge, "edge_type"),
                "source_entity_key": source,
                "target_entity_key": target,
            }
        )
        if len(result) >= 100:
            break
    return result


def _related_entity_keys(entity_keys: list[str], edges: list[dict[str, str]]) -> set[str]:
    related = set(entity_keys)
    for edge in edges:
        related.add(edge["source_entity_key"])
        related.add(edge["target_entity_key"])
    return related


def _load_memory_edges(root: Path, *, limit: int = 2_000) -> list[dict[str, Any]]:
    path = root / MEMORY_EDGES_PATH
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _episode_ids_for_nodes(edges: list[dict[str, Any]], node_keys: set[str]) -> set[str]:
    return {
        str(edge.get("to_id") or "")
        for edge in edges
        if edge.get("edge_type") == "node_episode"
        and str(edge.get("from_id") or "") in node_keys
        and str(edge.get("to_id") or "")
    }


def _procedures(root: Path, episodes: list[dict[str, Any]], memory_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passed_episode_ids = {
        str(episode.get("episode_id") or "")
        for episode in episodes
        if not episode.get("negative_guidance") and str(episode.get("episode_id") or "")
    }
    linked_ids = {
        str(edge.get("to_id") or "")
        for edge in memory_edges
        if edge.get("edge_type") == "episode_procedure"
        and str(edge.get("from_id") or "") in passed_episode_ids
        and str(edge.get("to_id") or "")
    }
    if not linked_ids:
        for episode in episodes:
            if episode.get("negative_guidance"):
                continue
            linked_ids.update(
                str(item.get("procedure_id") or "")
                for item in episode.get("procedures") or []
                if isinstance(item, dict) and item.get("procedure_id")
            )
    procedures: list[dict[str, Any]] = []
    for procedure in load_procedures(root):
        procedure_id = str(procedure.get("procedure_id") or "")
        if procedure_id not in linked_ids:
            continue
        procedures.append(
            {
                "procedure_id": procedure_id,
                "title": str(procedure.get("title") or procedure_id),
                "confidence": 0.75,
                "visible_reason": "procedure linked by current-node matched passed episode",
            }
        )
        if len(procedures) >= 25:
            break
    return procedures


def _field(value: Any, name: str) -> str:
    if isinstance(value, dict):
        return str(value.get(name) or "")
    return str(getattr(value, name, "") or "")
