from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from agentpack.dashboard.models import (
    DashboardGraph,
    DashboardMap,
    DashboardMapSummary,
    DashboardSnapshot,
    MapBuilding,
    MapDistrict,
    MapLandmark,
    MapRoad,
    MapWeather,
)

ROAD_TYPES = {"selected_because", "tested_by", "memory_influenced", "may_break", "retrieve_ref"}
MAX_ROADS = 120


def build_dashboard_map(snapshot: DashboardSnapshot, graph: DashboardGraph) -> DashboardMap:
    """Build the branded map contract from existing dashboard snapshot and graph data."""

    task_map_by_path = {row.path: row for row in snapshot.task_map if row.path}
    selected_by_path = {row.path: row for row in snapshot.selected_files if row.path}
    file_nodes = [node for node in graph.nodes if node.type == "file" and node.path]
    memory_linked = {
        edge.target.removeprefix("file:")
        for edge in graph.edges
        if edge.type == "memory_influenced" and edge.target.startswith("file:")
    }
    max_score = max(
        [
            *(row.score for row in snapshot.task_map),
            *(row.score for row in snapshot.selected_files),
            *(node.score for node in file_nodes),
            0.0,
        ]
    )
    denominator = max(max_score, 1.0)

    grouped: dict[str, list[MapBuilding]] = defaultdict(list)
    for node in file_nodes:
        path = node.path
        task_row = task_map_by_path.get(path)
        selected_row = selected_by_path.get(path)
        score = (task_row.score if task_row and task_row.score else 0.0) or (selected_row.score if selected_row else 0.0) or node.score
        confidence = _clamp(score / denominator, 0.08, 1.0)
        risk = (task_row.risk_level if task_row else node.risk) or "unknown"
        district_id = _district_id(path)
        reasons = []
        if task_row:
            reasons = task_row.why_selected or task_row.risk_reasons
        elif selected_row:
            reasons = selected_row.reasons
        grouped[district_id].append(
            MapBuilding(
                id=node.id,
                node_id=node.id,
                label=node.label,
                path=path,
                district_id=district_id,
                score=score,
                confidence=confidence,
                height=round(4 + 42 * confidence, 2),
                risk=risk,
                selected=node.selected,
                include_mode=(task_row.include_mode if task_row else selected_row.include_mode if selected_row else "") or "",
                memory_linked=path in memory_linked,
                tests=(task_row.tests_to_run if task_row else [])[:6],
                reasons=reasons[:6],
                actions=node.actions,
                color=_building_color(risk=risk, memory_linked=path in memory_linked),
            )
        )

    districts, buildings = _position_districts(grouped)
    roads = _map_roads(graph)
    landmarks = _landmarks(snapshot, graph)
    weather = _weather(snapshot, buildings)

    return DashboardMap(
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=DashboardMapSummary(
            district_count=len(districts),
            building_count=len(buildings),
            road_count=len(roads),
            selected_buildings=sum(1 for item in buildings if item.selected),
            high_risk_buildings=sum(1 for item in buildings if item.risk == "high"),
            max_score=max_score,
            stale=snapshot.context.status != "fresh",
        ),
        districts=districts,
        buildings=buildings,
        roads=roads,
        landmarks=landmarks,
        weather=weather,
    )


def _position_districts(grouped: dict[str, list[MapBuilding]]) -> tuple[list[MapDistrict], list[MapBuilding]]:
    districts: list[MapDistrict] = []
    buildings: list[MapBuilding] = []
    for index, district_id in enumerate(sorted(grouped)):
        district_buildings = sorted(grouped[district_id], key=lambda item: (-int(item.selected), -item.score, item.path))
        district_x = (index % 4) * 32.0
        district_z = (index // 4) * 28.0
        columns = max(2, min(5, int(len(district_buildings) ** 0.5) + 1))
        for building_index, building in enumerate(district_buildings):
            local_x = (building_index % columns) * 5.4
            local_z = (building_index // columns) * 5.4
            building.x = round(district_x + local_x, 2)
            building.z = round(district_z + local_z, 2)
            buildings.append(building)
        districts.append(
            MapDistrict(
                id=district_id,
                label=district_id,
                path="" if district_id == "root" else district_id,
                x=district_x,
                z=district_z,
                building_count=len(district_buildings),
                selected_count=sum(1 for item in district_buildings if item.selected),
            )
        )
    return districts, buildings


def _map_roads(graph: DashboardGraph) -> list[MapRoad]:
    roads = [
        MapRoad(
            id=edge.id,
            source=edge.source,
            target=edge.target,
            type=edge.type,
            confidence=edge.confidence,
            reason=edge.reason,
        )
        for edge in graph.edges
        if edge.type in ROAD_TYPES
    ]
    return sorted(roads, key=lambda item: (item.confidence, item.type, item.id), reverse=True)[:MAX_ROADS]


def _landmarks(snapshot: DashboardSnapshot, graph: DashboardGraph) -> list[MapLandmark]:
    active_thread = snapshot.task.thread_id or "global"
    action_nodes = [node for node in graph.nodes if node.type == "action"][:8]
    landmarks = [
        MapLandmark(id="task:active", label="Active task", type="task", status=snapshot.task.state, detail=snapshot.task.text, tone="primary", x=-10, z=8),
        MapLandmark(id="landmark:context", label="Context", type="context", status=snapshot.context.status, detail=snapshot.context.stale_reason, tone=_tone(snapshot.context.status), x=-10, z=18),
        MapLandmark(id="landmark:mcp", label="MCP", type="mcp", status=snapshot.mcp_health.status, detail=snapshot.mcp_health.runtime_detail, tone=_tone(snapshot.mcp_health.status), x=-10, z=28),
        MapLandmark(id="landmark:thread", label="Thread", type="thread", status=active_thread, detail=f"{snapshot.threads.active_count} active", tone="neutral", x=-10, z=38),
        MapLandmark(id="landmark:settings", label="Settings", type="settings", status="editable", detail=snapshot.config.path, tone="neutral", x=-10, z=48),
        MapLandmark(id="landmark:workflow", label="Workflow", type="workflow", status=snapshot.loop.status or "ready", detail=snapshot.loop.next_action, tone=_tone(snapshot.loop.status), x=-10, z=58),
    ]
    for index, node in enumerate(action_nodes):
        landmarks.append(
            MapLandmark(
                id=node.id,
                label=node.label,
                type="action",
                status=node.status or "action",
                detail=node.summary,
                tone="neutral",
                x=126.0,
                z=10.0 + index * 6.0,
            )
        )
    return landmarks


def _weather(snapshot: DashboardSnapshot, buildings: list[MapBuilding]) -> list[MapWeather]:
    weather: list[MapWeather] = []
    if snapshot.context.status != "fresh":
        weather.append(MapWeather(id="context", label="Context stale", tone="warn", detail=snapshot.context.stale_reason or snapshot.context.status))
    high_risk = sum(1 for item in buildings if item.risk == "high")
    if high_risk:
        weather.append(MapWeather(id="risk", label=f"{high_risk} high-risk files", tone="risk", detail="Review impact and tests before acting."))
    if snapshot.mcp_health.status != "healthy":
        weather.append(MapWeather(id="mcp", label="MCP needs attention", tone="warn", detail=snapshot.mcp_health.runtime_detail or snapshot.mcp_health.status))
    if not any(item.tests for item in buildings):
        weather.append(MapWeather(id="tests", label="No test roads", tone="warn", detail="Task map has no validation hints."))
    drifted = [item for item in snapshot.integrations if item.status not in {"present", "healthy", "ok"}]
    if drifted:
        weather.append(MapWeather(id="integrations", label=f"{len(drifted)} integration checks", tone="warn", detail="Repair or inspect host integration files."))
    return weather


def _district_id(path: str) -> str:
    if path.startswith("src/agentpack/"):
        return "src/agentpack"
    if path.startswith("frontend/dashboard/"):
        return "frontend/dashboard"
    if path.startswith("tests/"):
        return "tests"
    if path.startswith("docs/"):
        return "docs"
    if path.startswith("npm/"):
        return "npm"
    if path.startswith(".github/"):
        return ".github"
    parts = [part for part in path.split("/") if part]
    return parts[0] if len(parts) > 1 else "root"


def _building_color(*, risk: str, memory_linked: bool) -> str:
    if memory_linked:
        return "#38cfd3"
    if risk == "high":
        return "#ff7a7f"
    if risk in {"medium", "warn", "warning"}:
        return "#f7cf62"
    if risk == "low":
        return "#6ed49a"
    return "#8da2c0"


def _tone(value: str) -> str:
    if value in {"healthy", "fresh", "ready", "present", "done"}:
        return "good"
    if value in {"missing", "high", "risk", "failed", "blocked"}:
        return "risk"
    if value in {"warning", "warn", "stale", "unknown"}:
        return "warn"
    return "neutral"


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))
