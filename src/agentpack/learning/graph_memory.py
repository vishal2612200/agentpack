"""Bounded, auditable memory retrieval rooted in live architecture evidence."""
from __future__ import annotations

from typing import Any, Iterable

from agentpack.core.node_identity import normalize_repo_path
from agentpack.learning.episodes import episodic_memory_matches

MEMORY_RETRIEVAL_SCHEMA_VERSION = 1


def retrieve_memory_chain(
    root,
    *,
    task: str,
    live_paths: Iterable[str],
    live_entity_keys: Iterable[str] = (),
    architecture_edges: Iterable[Any] = (),
    max_boost: float = 12.0,
) -> dict[str, Any]:
    """Retrieve memory in fixed order without adding candidates outside live scope."""
    paths = sorted({normalize_repo_path(path) for path in live_paths if path})
    entity_keys = sorted({str(key) for key in live_entity_keys if key})
    one_hop = _one_hop_edges(architecture_edges, set(entity_keys))
    episodes = episodic_memory_matches(
        root,
        task,
        max_boost=max_boost,
        eligible_paths=set(paths),
    )
    procedures = _procedures(episodes)
    boosts = {
        str(item["path"]): min(max_boost, float(item.get("boost") or 0.0))
        for item in episodes
        if not item.get("negative_guidance") and float(item.get("boost") or 0.0) > 0
    }
    return {
        "schema_version": MEMORY_RETRIEVAL_SCHEMA_VERSION,
        "order": ["live_pr_entities", "architecture_one_hop", "compatible_episodes", "validated_procedures"],
        "live_pr_entities": {"entity_keys": entity_keys[:100], "paths": paths[:100]},
        "architecture_one_hop": one_hop,
        "compatible_episodes": episodes[:50],
        "validated_procedures": procedures[:25],
        "candidate_boosts": boosts,
        "constraints": {
            "candidate_scope": "live paths only",
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


def _procedures(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    procedures: list[dict[str, Any]] = []
    seen: set[str] = set()
    for episode in episodes:
        for procedure in episode.get("procedures") or []:
            if not isinstance(procedure, dict):
                continue
            procedure_id = str(procedure.get("procedure_id") or "")
            if not procedure_id or procedure_id in seen:
                continue
            seen.add(procedure_id)
            procedures.append(procedure)
    return procedures


def _field(value: Any, name: str) -> str:
    if isinstance(value, dict):
        return str(value.get(name) or "")
    return str(getattr(value, name, "") or "")
