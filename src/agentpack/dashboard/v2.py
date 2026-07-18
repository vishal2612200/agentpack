"""Versioned dashboard contracts built from the existing local evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentpack.core.handoff import HandoffError, HandoffStore
from agentpack.dashboard.actions import build_dashboard_action_command
from agentpack.dashboard.collectors import (
    build_project_dashboard_snapshot,
    semantic_graph_summary,
)
from agentpack.dashboard.graph import build_dashboard_graph
from agentpack.dashboard.map import build_dashboard_map
from agentpack.dashboard.project_state import build_project_home_snapshot
from agentpack.dashboard.terminal import inspect_command
from agentpack.dashboard.contracts import (
    DashboardV2ActionInspection,
    DashboardV2Actions,
    DashboardV2AgentSession,
    DashboardV2AgentsResponse,
    DashboardV2Agents,
    DashboardV2EvidenceItem,
    DashboardV2Evidence,
    DashboardV2Handoff,
    DashboardV2Impact,
    DashboardV2ImpactEntity,
    DashboardV2ImpactRelationship,
    DashboardV2ImpactResponse,
    DashboardV2ImpactScene,
    DashboardV2Payload,
)


DASHBOARD_V2_SCHEMA_VERSION = 2


def build_dashboard_v2_payload(root: Path, *, detail: str = "home") -> dict[str, Any]:
    """Return the stable v2 workspace envelope without changing v1 contracts."""
    snapshot = (
        build_project_home_snapshot(root)
        if detail == "home"
        else build_project_dashboard_snapshot(root)
    )
    graph = build_dashboard_graph(snapshot, root) if detail != "home" else None
    dashboard_map = build_dashboard_map(snapshot, graph) if graph is not None else None
    payload = DashboardV2Payload(
        detail="home" if detail == "home" else "full",
        snapshot=snapshot,
        graph=graph or _empty_graph_model(),
        map=dashboard_map or _empty_map_model(),
        workspace=_workspace_summary(snapshot),
        agents=_agent_summary(root, snapshot),
        impact=DashboardV2Impact(
            schema_version=snapshot.semantic_graph.schema_version,
            available=bool(snapshot.semantic_graph.entity_count),
            entity_count=snapshot.semantic_graph.entity_count,
            edge_count=snapshot.semantic_graph.edge_count,
            unresolved_count=snapshot.semantic_graph.unresolved_count,
            capabilities=snapshot.semantic_graph.capabilities,
        ),
    )
    return payload.model_dump(mode="json")


def build_dashboard_v2_impact(
    root: Path,
    *,
    query: str = "",
    relationship: str = "",
    language: str = "",
    confidence: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Expose bounded symbol/file impact data for the map inspector."""
    summary = semantic_graph_summary(
        root,
        query=query,
        relationship=relationship,
        language=language,
        confidence=confidence,
        limit=limit,
    )
    tests = [
        entity
        for entity in summary.entities
        if entity.get("type") == "test"
    ]
    snapshot = build_project_dashboard_snapshot(root)
    graph = build_dashboard_graph(snapshot, root)
    dashboard_map = build_dashboard_map(snapshot, graph)
    scene = _impact_scene(snapshot, dashboard_map, summary.model_dump(mode="json"))
    return DashboardV2ImpactResponse(
        query=query,
        relationship=relationship,
        language=language,
        confidence=confidence,
        available=not summary.capabilities.get("error"),
        summary=summary.model_dump(mode="json"),
        affected_tests=tests,
        entities=scene.entities,
        relationships=scene.relationships,
        scene=scene,
    ).model_dump(mode="json")


def build_dashboard_v2_agents(root: Path) -> dict[str, Any]:
    """Return handoffs and local session surfaces without exposing UUIDs."""
    snapshot = build_project_dashboard_snapshot(root)
    return DashboardV2AgentsResponse(agents=_agent_summary(root, snapshot)).model_dump(mode="json")


def build_dashboard_v2_evidence(root: Path) -> dict[str, Any]:
    snapshot = build_project_dashboard_snapshot(root)
    return DashboardV2Evidence(
        context=snapshot.context.model_dump(mode="json"),
        selected_files=[row.model_dump(mode="json") for row in snapshot.selected_files],
        task_map=[row.model_dump(mode="json") for row in snapshot.task_map],
        observer=snapshot.observer.model_dump(mode="json"),
        timeline=[row.model_dump(mode="json") for row in snapshot.task_timeline],
    ).model_dump(mode="json")


def build_dashboard_v2_actions(root: Path) -> dict[str, Any]:
    snapshot = build_project_dashboard_snapshot(root)
    return DashboardV2Actions(
        suggested=[row.model_dump(mode="json") for row in snapshot.suggested_actions],
        catalog=[row.model_dump(mode="json") for row in snapshot.command_catalog],
    ).model_dump(mode="json")


def build_dashboard_v2_action_inspection(
    root: Path,
    action_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    command = build_dashboard_action_command(action_id, payload)
    inspection = inspect_command(command, root=root, cwd=str(payload.get("cwd") or "") or None)
    snapshot = build_project_dashboard_snapshot(root)
    catalog_item = next((item for item in snapshot.command_catalog if item.id == action_id), None)
    affected_paths = [row.path for row in snapshot.task_map[:50] if row.path]
    target = str(payload.get("target") or payload.get("path") or "").strip()
    if target:
        affected_paths = [target, *[path for path in affected_paths if path != target]]
    return DashboardV2ActionInspection(
        action=action_id,
        command=inspection.command,
        cwd=inspection.cwd,
        purpose=catalog_item.description if catalog_item else f"Run AgentPack action '{action_id}'.",
        risk="medium" if inspection.risky else "low",
        risk_reasons=inspection.risk_reasons,
        affected_paths=affected_paths[:50],
        expected_effect=_expected_effect(action_id),
        confirm_required=inspection.confirm_required,
        allowed=inspection.allowed,
    ).model_dump(mode="json")


def _workspace_summary(snapshot) -> dict[str, Any]:
    return {
        "project": snapshot.project.model_dump(mode="json"),
        "workspace": snapshot.workspace.model_dump(mode="json") if snapshot.workspace else None,
        "task": snapshot.active_task.model_dump(mode="json") if snapshot.active_task else snapshot.task.model_dump(mode="json"),
        "context": snapshot.context.model_dump(mode="json"),
    }


def _expected_effect(action_id: str) -> str:
    effects = {
        "next": "Refresh task routing and show the next recommended AgentPack step.",
        "refresh_context": "Refresh the task-scoped context and update selected-file evidence.",
        "dev_check": "Run the configured development checks and record validation output.",
        "retrieve": "Retrieve the requested context block without changing repository files.",
        "doctor": "Inspect AgentPack integrations and report repair guidance.",
        "finish": "Mark the current task complete and record the supplied summary.",
    }
    return effects.get(action_id, "Run the selected AgentPack action and stream its result.")


def _impact_scene(snapshot, dashboard_map, semantic: dict[str, Any]) -> DashboardV2ImpactScene:
    selected_paths = {row.path for row in snapshot.selected_files}
    task_rows = {row.path: row for row in snapshot.task_map}
    buildings = {building.path: building for building in dashboard_map.buildings}
    entities: list[DashboardV2ImpactEntity] = []
    semantic_ids: dict[str, str] = {}

    for building in dashboard_map.buildings:
        row = task_rows.get(building.path)
        entities.append(
            DashboardV2ImpactEntity(
                id=f"file:{building.path}",
                kind="file",
                label=building.label,
                path=building.path,
                confidence_tier=building.confidence_source,
                task_relevant=building.path in selected_paths or bool(row and row.kind == "selected"),
                risk=building.risk,
                reasons=list(building.reasons),
                actions=list(building.action_refs),
                x=building.x,
                y=max(1.0, building.height),
                z=building.z,
            )
        )

    semantic_paths = sorted({str(item.get("path") or "") for item in semantic.get("entities", []) if item.get("path")})
    for index, path in enumerate(path for path in semantic_paths if path not in buildings):
        row = task_rows.get(path)
        x = float((index % 8) * 8)
        z = float(48 + (index // 8) * 8)
        entities.append(
            DashboardV2ImpactEntity(
                id=f"file:{path}",
                kind="file",
                label=Path(path).name,
                path=path,
                confidence_tier="structured",
                task_relevant=path in selected_paths or path in task_rows,
                risk=row.risk_level if row else "unknown",
                reasons=list(row.reasons) if row else ["Contains indexed semantic entities."],
                actions=["retrieve", "dev_check"],
                x=x,
                y=2.0,
                z=z,
            )
        )

    per_file_index: dict[str, int] = {}
    for item in semantic.get("entities", []):
        key = str(item.get("entity_key") or "")
        path = str(item.get("path") or "")
        entity_type = str(item.get("type") or "external")
        kind = "test" if entity_type == "test" or "test" in path.lower() else "symbol"
        if entity_type in {"external", "unresolved"}:
            kind = "external"
        entity_id = f"semantic:{key}"
        semantic_ids[key] = entity_id
        building = buildings.get(path)
        index = per_file_index.get(path, 0)
        per_file_index[path] = index + 1
        x = building.x + ((index % 3) - 1) * 1.4 if building else 96.0 + (index % 5) * 4.0
        z = building.z + (index // 3) * 1.3 if building else 96.0 + (index // 5) * 4.0
        y = max(2.0, building.height + 1.5) if building else 2.0
        entities.append(
            DashboardV2ImpactEntity(
                id=entity_id,
                kind=kind,
                label=str(item.get("name") or key),
                path=path,
                line=int(item.get("line") or 0),
                parent_id=f"file:{path}" if path else "",
                confidence_tier=str(item.get("confidence_tier") or ""),
                task_relevant=path in selected_paths or path in task_rows,
                risk=task_rows[path].risk_level if path in task_rows else "unknown",
                actions=["retrieve", "dev_check"] if path else [],
                x=x,
                y=y,
                z=z,
            )
        )

    relationships: list[DashboardV2ImpactRelationship] = []
    related: dict[str, set[str]] = {}
    for edge in semantic.get("edges", []):
        source_id = semantic_ids.get(str(edge.get("source") or ""), f"semantic:{edge.get('source') or ''}")
        target_id = semantic_ids.get(str(edge.get("target") or ""), f"semantic:{edge.get('target') or ''}")
        evidence = [
            DashboardV2EvidenceItem(
                kind="source",
                path=str(item.get("path") or ""),
                start_line=int(item.get("start_line") or 0),
                end_line=int(item.get("end_line") or 0),
                source=str(item.get("source") or ""),
                source_hash=str(item.get("source_hash") or ""),
                note=str(item.get("note") or ""),
            )
            for item in edge.get("evidence", [])[:3]
        ]
        tier = str(edge.get("confidence_tier") or "best_effort")
        relevant = any(item.path in selected_paths or item.path in task_rows for item in evidence)
        relationship = DashboardV2ImpactRelationship(
            id=f"relationship:{edge.get('edge_key') or ''}",
            source_id=source_id,
            target_id=target_id,
            relationship=str(edge.get("relationship") or "related"),
            confidence_tier=tier,
            strength={"structured": 1.0, "best_effort": 0.65, "file_level": 0.4}.get(tier, 0.35),
            task_relevant=relevant,
            evidence=evidence,
        )
        relationships.append(relationship)
        related.setdefault(source_id, set()).add(target_id)
        related.setdefault(target_id, set()).add(source_id)

    for action_index, action in enumerate(snapshot.command_catalog[:8]):
        entities.append(
            DashboardV2ImpactEntity(
                id=f"action:{action.id}",
                kind="action",
                label=action.label,
                confidence_tier="catalog",
                task_relevant=bool(action.primary),
                risk=action.risk,
                actions=[action.id],
                x=10.0 + action_index * 7.0,
                y=2.0,
                z=-18.0,
            )
        )

    entities = [entity.model_copy(update={"related_ids": sorted(related.get(entity.id, set()))}) for entity in entities]
    entities.sort(key=lambda item: (not item.task_relevant, item.kind, item.path, item.label))
    relationships.sort(key=lambda item: (not item.task_relevant, -item.strength, item.id))
    unavailable_reason = str(semantic.get("capabilities", {}).get("error") or "")
    return DashboardV2ImpactScene(
        available=not unavailable_reason and bool(entities),
        unavailable_reason=unavailable_reason,
        entities=entities[:500],
        relationships=relationships[:500],
    )


def _empty_graph() -> dict[str, Any]:
    return {"schema_version": 1, "root_id": "task:active", "summary": {}, "nodes": [], "edges": []}


def _empty_map() -> dict[str, Any]:
    return {"schema_version": 1, "summary": {}, "districts": [], "buildings": [], "roads": [], "landmarks": [], "weather": []}


def _empty_graph_model():
    from agentpack.dashboard.models import DashboardGraph

    return DashboardGraph()


def _empty_map_model():
    from agentpack.dashboard.models import DashboardMap

    return DashboardMap()


def _agent_summary(root: Path, snapshot) -> DashboardV2Agents:
    handoffs: list[DashboardV2Handoff] = []
    try:
        records = HandoffStore(root).list()
        for record in records:
            handoffs.append(
                DashboardV2Handoff(
                    name=record.name,
                    status=record.status,
                    created_at=record.created_at.isoformat(),
                    updated_at=record.updated_at.isoformat(),
                    source_provider=record.source.provider,
                    source_session_id=record.source.session_id,
                    target_provider=record.target_provider,
                    target_session_id=record.target_session_id,
                    task=record.report.task,
                    summary=record.report.summary,
                    next_action=record.report.next_action,
                    claim_provider=record.claim.provider if record.claim else "",
                    claim_session_id=record.claim.session_id if record.claim else "",
                )
            )
    except (HandoffError, OSError, ValueError):
        handoffs = []
    sessions = [
        DashboardV2AgentSession(
            provider="agentpack",
            session_id=row.thread_id,
            thread_id=row.thread_id,
            task=row.task,
            status=row.status or "unknown",
            context_status=snapshot.context.status,
            updated_at=row.updated_at,
            worktree=row.worktree,
        )
        for row in snapshot.thread_rows
    ]
    return DashboardV2Agents(
        handoffs=handoffs,
        sessions=sessions,
        threads=[row.model_dump(mode="json") for row in snapshot.thread_rows],
        integrations=[row.model_dump(mode="json") for row in snapshot.integrations],
        mcp_health=snapshot.mcp_health.model_dump(mode="json"),
    )
