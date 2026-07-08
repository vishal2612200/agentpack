from __future__ import annotations

from collections import Counter, defaultdict
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
    node_kind_by_id = {node.id: node.type for node in graph.nodes}
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
        score, confidence_source = _score_and_source(task_row.score if task_row else 0.0, selected_row.score if selected_row else 0.0, node.score)
        base_confidence = _clamp(score / denominator, 0.0, 1.0)
        selected_boost = 0.04 if node.selected and base_confidence < 1 else 0.0
        memory_boost = 0.03 if path in memory_linked and base_confidence < 1 else 0.0
        confidence = _clamp(base_confidence + selected_boost + memory_boost, 0.08, 1.0)
        risk = (task_row.risk_level if task_row else node.risk) or "unknown"
        building_type = _building_type(path)
        district_id = _district_id(path, building_type)
        layout_group = _layout_group(path, building_type)
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
                building_type=building_type,
                building_tier=_building_tier(building_type, confidence),
                confidence_source=confidence_source,
                confidence_breakdown={
                    "score": round(score, 3),
                    "max_score": round(max_score, 3),
                    "base_confidence": round(base_confidence, 4),
                    "selected_boost": selected_boost,
                    "memory_boost": memory_boost,
                    "normalized_confidence": round(confidence, 4),
                    "source": confidence_source,
                    "selected": node.selected,
                    "memory_linked": path in memory_linked,
                },
                layout_group=layout_group,
                action_refs=_action_refs(task_row=task_row, selected=node.selected),
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
    roads = _map_roads(graph, node_kind_by_id)
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
            building_type_counts=dict(sorted(Counter(item.building_type for item in buildings).items())),
            route_class_counts=dict(sorted(Counter(item.route_class for item in roads).items())),
            confidence_source_counts=dict(sorted(Counter(item.confidence_source for item in buildings).items())),
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
    ordered_districts = sorted(grouped, key=lambda item: (_district_order(item), item))
    for index, district_id in enumerate(ordered_districts):
        district_buildings = sorted(grouped[district_id], key=lambda item: (-int(item.selected), -item.score, item.path))
        district_x, district_z = _district_position(index, district_id)
        columns = max(2, min(3, int(len(district_buildings) ** 0.5) + 1))
        for building_index, building in enumerate(district_buildings):
            row = building_index // columns
            column = building_index % columns
            local_x = column * 14.8 + (6.8 if row % 2 else 0.0)
            local_z = row * 13.2
            building.x = round(district_x + local_x, 2)
            building.z = round(district_z + local_z, 2)
            buildings.append(building)
        districts.append(
            MapDistrict(
                id=district_id,
                label=_district_label(district_id),
                path=_district_path(district_id),
                x=district_x,
                z=district_z,
                building_count=len(district_buildings),
                selected_count=sum(1 for item in district_buildings if item.selected),
            )
        )
    return districts, buildings


def _map_roads(graph: DashboardGraph, node_kind_by_id: dict[str, str]) -> list[MapRoad]:
    roads = [
        MapRoad(
            id=edge.id,
            source=edge.source,
            target=edge.target,
            type=edge.type,
            confidence=edge.confidence,
            reason=edge.reason,
            route_class=_route_class(edge.type, edge.confidence),
            relationship_strength=_relationship_strength(edge.type, edge.confidence),
            relationship_source=_relationship_source(edge.type, edge.confidence),
            source_kind=node_kind_by_id.get(edge.source, _kind_from_id(edge.source)),
            target_kind=node_kind_by_id.get(edge.target, _kind_from_id(edge.target)),
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


def _score_and_source(task_score: float, selected_score: float, node_score: float) -> tuple[float, str]:
    if task_score:
        return task_score, "task_map"
    if selected_score:
        return selected_score, "selected_file"
    if node_score:
        return node_score, "graph_node"
    return 0.0, "fallback"


def _building_type(path: str) -> str:
    if path.startswith("tests/") or "/test_" in path or path.endswith("_test.py") or path.endswith(".test.tsx") or path.endswith(".test.ts"):
        return "test"
    if path.startswith("docs/") or path.lower().endswith((".md", ".mdx", ".rst")):
        return "docs"
    if path.startswith(("frontend/", "web/", "ui/")) or path.endswith((".tsx", ".jsx", ".css")):
        return "frontend"
    if path.startswith((".claude/", ".codex/", ".cursor/", ".vscode/")) or "mcp" in path.lower():
        return "integration"
    if path.startswith((".github/", "scripts/", "tools/")) or any(part in path for part in ("workflow", "release", "ci")):
        return "workflow"
    if path.endswith((".toml", ".json", ".yaml", ".yml", ".ini", ".env")) or "config" in path.lower() or "settings" in path.lower():
        return "config"
    if "memory" in path.lower() or "skill" in path.lower() or "learning" in path.lower():
        return "memory"
    if "/" not in path:
        return "root"
    if path.startswith(("src/", "lib/", "packages/", "npm/")) or path.endswith((".py", ".ts", ".js", ".go", ".rs", ".java")):
        return "source"
    return "unknown"


def _building_tier(building_type: str, confidence: float) -> str:
    if building_type in {"test", "config", "docs", "integration", "workflow", "memory"}:
        return "service"
    if confidence >= 0.78:
        return "tower"
    if confidence >= 0.36:
        return "block"
    return "pavilion"


def _layout_group(path: str, building_type: str) -> str:
    if building_type == "source" and path.startswith("src/agentpack/"):
        return "source-core"
    if building_type == "frontend":
        return "interface"
    if building_type == "test":
        return "civic-tests"
    if building_type == "config":
        return "infrastructure"
    if building_type == "integration":
        return "ports"
    if building_type == "docs":
        return "knowledge"
    if building_type in {"workflow", "memory"}:
        return building_type
    return "root"


def _district_id(path: str, building_type: str) -> str:
    group = _layout_group(path, building_type)
    if group == "source-core":
        return "source:src/agentpack"
    if group == "interface":
        return "frontend:dashboard" if path.startswith("frontend/dashboard/") else "frontend"
    if group == "civic-tests":
        return "civic:tests"
    if group == "infrastructure":
        return "infra:config"
    if group == "ports":
        return "ports:integrations"
    if group == "knowledge":
        return "knowledge:docs"
    if group == "workflow":
        return "workflow:automation"
    if group == "memory":
        return "memory:skills"
    if path.startswith("src/agentpack/"):
        return "source:src/agentpack"
    if path.startswith("frontend/dashboard/"):
        return "frontend:dashboard"
    if path.startswith("tests/"):
        return "civic:tests"
    if path.startswith("docs/"):
        return "knowledge:docs"
    if path.startswith("npm/"):
        return "source:npm"
    if path.startswith(".github/"):
        return "workflow:github"
    parts = [part for part in path.split("/") if part]
    return f"root:{parts[0]}" if len(parts) > 1 else "root"


def _action_refs(*, task_row: object | None, selected: bool) -> list[str]:
    refs = ["open_file", "explain_why", "retrieve", "refresh_context"]
    if task_row and getattr(task_row, "tests_to_run", None):
        refs.append("run_tests")
    if selected:
        refs.append("ignore_suggest")
    return refs


def _route_class(edge_type: str, confidence: float) -> str:
    strength = _relationship_strength(edge_type, confidence)
    if strength >= 0.78:
        return "expressway"
    if strength >= 0.5:
        return "highway"
    if strength >= 0.26:
        return "county"
    return "local"


def _relationship_strength(edge_type: str, confidence: float) -> float:
    defaults = {
        "selected_because": 0.86,
        "tested_by": 0.64,
        "memory_influenced": 0.58,
        "retrieve_ref": 0.48,
        "may_break": 0.42,
    }
    typed_default = defaults.get(edge_type, 0.22)
    if confidence:
        return round(max(_clamp(confidence, 0.0, 1.0), typed_default), 4)
    return typed_default


def _relationship_source(edge_type: str, confidence: float) -> str:
    if confidence:
        return "graph_edge"
    if edge_type == "tested_by":
        return "test_hint"
    if edge_type == "memory_influenced":
        return "memory"
    if edge_type in {"selected_because", "may_break", "retrieve_ref"}:
        return "task_map"
    return "fallback"


def _kind_from_id(node_id: str) -> str:
    if node_id.startswith("file:"):
        return "file"
    if node_id.startswith("task:"):
        return "task"
    if node_id.startswith("test:"):
        return "test"
    if node_id.startswith("action:"):
        return "action"
    return "unknown"


def _district_order(district_id: str) -> int:
    prefixes = ["source:", "frontend:", "civic:", "infra:", "ports:", "knowledge:", "workflow:", "memory:", "root:"]
    for index, prefix in enumerate(prefixes):
        if district_id.startswith(prefix):
            return index
    return len(prefixes)


def _district_position(index: int, district_id: str) -> tuple[float, float]:
    preferred = {
        "source:src/agentpack": (28.0, 26.0),
        "frontend:dashboard": (96.0, 26.0),
        "civic:tests": (28.0, 92.0),
        "infra:config": (-34.0, 34.0),
        "ports:integrations": (156.0, 34.0),
        "knowledge:docs": (96.0, 92.0),
        "workflow:automation": (156.0, 92.0),
        "memory:skills": (-34.0, 92.0),
    }
    if district_id in preferred:
        return preferred[district_id]
    return ((index % 3) * 72.0, 150.0 + (index // 3) * 58.0)


def _district_label(district_id: str) -> str:
    labels = {
        "source:src/agentpack": "Core source district",
        "frontend:dashboard": "Dashboard interface district",
        "civic:tests": "Test services",
        "infra:config": "Config infrastructure",
        "ports:integrations": "Integration ports",
        "knowledge:docs": "Knowledge district",
        "workflow:automation": "Workflow command center",
        "memory:skills": "Memory and skills district",
    }
    return labels.get(district_id, district_id.replace(":", " / "))


def _district_path(district_id: str) -> str:
    if ":" not in district_id:
        return "" if district_id == "root" else district_id
    _, value = district_id.split(":", 1)
    return "" if value in {"config", "integrations", "automation", "skills"} else value


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
