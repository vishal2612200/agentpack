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
    DashboardV2Agents,
    DashboardV2Evidence,
    DashboardV2Handoff,
    DashboardV2Impact,
    DashboardV2ImpactResponse,
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
    return DashboardV2ImpactResponse(
        query=query,
        relationship=relationship,
        language=language,
        confidence=confidence,
        available=not summary.capabilities.get("error"),
        summary=summary.model_dump(mode="json"),
        affected_tests=tests,
    ).model_dump(mode="json")


def build_dashboard_v2_agents(root: Path) -> dict[str, Any]:
    """Return handoffs and local session surfaces without exposing UUIDs."""
    snapshot = build_project_dashboard_snapshot(root)
    return {"schema_version": DASHBOARD_V2_SCHEMA_VERSION, "agents": _agent_summary(root, snapshot).model_dump(mode="json")}


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
            provider=row.agent or "generic",
            session_id=row.thread_id,
            thread_id=row.thread_id,
            task=row.task,
            status=row.status or "unknown",
            context_status=snapshot.context.status,
            updated_at=row.updated_at,
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
