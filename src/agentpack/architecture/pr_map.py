"""Small, source-free PR architecture map projection for local dashboard use."""
from __future__ import annotations

from typing import Any

from agentpack.architecture.service import build_diff, build_snapshot_for_ref, run_check


def build_pr_map(
    root,
    *,
    base_ref: str,
    head_ref: str,
    entity_type: str = "",
    confidence: str = "",
    status: str = "",
    limit: int = 500,
) -> dict[str, Any]:
    diff = build_diff(root, base_ref, head_ref)
    check = run_check(root, base_ref, head_ref)
    base = build_snapshot_for_ref(root, base_ref)
    head = build_snapshot_for_ref(root, head_ref)
    base_entities = {entity.entity_key: entity for entity in base.entities}
    head_entities = {entity.entity_key: entity for entity in head.entities}
    nodes: dict[str, dict[str, Any]] = {}

    def add_entity(entity, node_status: str) -> None:
        if entity_type and entity.entity_type != entity_type:
            return
        if confidence and entity.confidence_tier != confidence:
            return
        if status and node_status != status:
            return
        nodes.setdefault(
            entity.entity_key,
            {
                "id": entity.entity_key,
                "label": entity.display_name,
                "qualified_name": entity.qualified_name,
                "entity_type": entity.entity_type,
                "path": entity.locator.path,
                "domain": str(entity.metadata.get("domain") or "root"),
                "confidence_tier": entity.confidence_tier,
                "status": node_status,
                "source_hash": entity.source_hash,
                "evidence": [item.model_dump(mode="json") for item in entity.evidence[:5]],
            },
        )

    for entity in diff.added_entities:
        add_entity(entity, "added")
    for entity in diff.removed_entities:
        add_entity(entity, "removed")
    for change in diff.changed_entities:
        before = base_entities.get(change.entity_key)
        after = head_entities.get(change.entity_key)
        if after is not None:
            add_entity(after, "changed")
        elif before is not None:
            add_entity(before, "changed")
    for alias in diff.aliased_entities:
        before = base_entities.get(alias.before_entity_key)
        after = head_entities.get(alias.after_entity_key)
        if before is not None:
            add_entity(before, "alias" if alias.status == "confirmed" else "ambiguous")
        if after is not None:
            add_entity(after, "alias" if alias.status == "confirmed" else "ambiguous")

    edges: list[dict[str, Any]] = []
    for edge in [*diff.added_edges, *diff.removed_edges]:
        edge_status = "added" if edge in diff.added_edges else "removed"
        if edge.source_entity_key not in nodes or edge.target_entity_key not in nodes:
            continue
        if confidence and edge.confidence_tier != confidence:
            continue
        edges.append(
            {
                "id": edge.edge_key,
                "source": edge.source_entity_key,
                "target": edge.target_entity_key,
                "type": edge.edge_type,
                "status": edge_status,
                "confidence_tier": edge.confidence_tier,
                "evidence": [item.model_dump(mode="json") for item in edge.evidence[:5]],
            }
        )
    for alias in diff.aliased_entities:
        if alias.before_entity_key in nodes and alias.after_entity_key in nodes:
            edges.append(
                {
                    "id": f"alias:{alias.before_entity_key}:{alias.after_entity_key}",
                    "source": alias.before_entity_key,
                    "target": alias.after_entity_key,
                    "type": "alias",
                    "status": alias.status,
                    "confidence": alias.confidence,
                    "confidence_tier": "structured" if alias.status == "confirmed" else "best_effort",
                    "evidence": [item.model_dump(mode="json") for item in alias.evidence[:5]],
                }
            )
    ordered_nodes = sorted(nodes.values(), key=lambda item: (item["domain"], item["path"], item["id"]))[:max(1, limit)]
    node_ids = {item["id"] for item in ordered_nodes}
    ordered_edges = [edge for edge in edges if edge["source"] in node_ids and edge["target"] in node_ids][: max(1, limit * 2)]
    return {
        "schema_version": 1,
        "base_sha": base.commit_sha,
        "head_sha": head.commit_sha,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "nodes": ordered_nodes,
        "edges": ordered_edges,
        "districts": sorted({item["domain"] for item in ordered_nodes}),
        "policies": check.model_dump(mode="json"),
        "summary": {
            "nodes": len(ordered_nodes),
            "edges": len(ordered_edges),
            "added": sum(item["status"] == "added" for item in ordered_nodes),
            "removed": sum(item["status"] == "removed" for item in ordered_nodes),
            "changed": sum(item["status"] == "changed" for item in ordered_nodes),
            "ambiguous_aliases": sum(item["status"] == "ambiguous" for item in ordered_nodes),
        },
    }
