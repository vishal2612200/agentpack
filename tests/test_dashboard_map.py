from __future__ import annotations

from agentpack.dashboard.action_history import read_action_history, record_dashboard_action
from agentpack.dashboard.graph import build_dashboard_graph
from agentpack.dashboard.map import build_dashboard_map
from agentpack.dashboard.models import (
    ContextHealth,
    DashboardSnapshot,
    McpHealth,
    ProjectInfo,
    SelectedFileRow,
    TaskInfo,
    TaskMapFileRow,
)


def test_dashboard_map_builds_districts_and_building_confidence() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="polish dashboard map"),
        context=ContextHealth(status="fresh"),
        selected_files=[
            SelectedFileRow(path="src/agentpack/dashboard/server.py", include_mode="full", score=200, reasons=["entrypoint"]),
            SelectedFileRow(path="frontend/dashboard/src/App.tsx", include_mode="symbols", score=100, reasons=["UI"]),
        ],
        task_map=[
            TaskMapFileRow(path="src/agentpack/dashboard/server.py", kind="selected", score=200, risk_level="high", tests_to_run=["tests/test_dashboard_command.py"]),
            TaskMapFileRow(path="frontend/dashboard/src/App.tsx", kind="selected", score=100, risk_level="low"),
            TaskMapFileRow(path="docs/commands.md", kind="omitted", score=0, risk_level="low"),
        ],
    )
    graph = build_dashboard_graph(snapshot)

    dashboard_map = build_dashboard_map(snapshot, graph)
    buildings = {building.path: building for building in dashboard_map.buildings}
    districts = {district.id: district for district in dashboard_map.districts}

    assert {"source:src/agentpack", "frontend:dashboard", "knowledge:docs"} <= set(districts)
    assert buildings["src/agentpack/dashboard/server.py"].height == 46.0
    assert buildings["src/agentpack/dashboard/server.py"].confidence == 1.0
    assert buildings["src/agentpack/dashboard/server.py"].color == "#ff7a7f"
    assert buildings["src/agentpack/dashboard/server.py"].building_type == "source"
    assert buildings["src/agentpack/dashboard/server.py"].building_tier == "tower"
    assert buildings["src/agentpack/dashboard/server.py"].confidence_source == "task_map"
    assert "retrieve" in buildings["src/agentpack/dashboard/server.py"].action_refs
    assert buildings["frontend/dashboard/src/App.tsx"].height == 26.68
    assert buildings["frontend/dashboard/src/App.tsx"].building_type == "frontend"
    assert buildings["docs/commands.md"].confidence == 0.08
    assert buildings["docs/commands.md"].building_type == "docs"
    assert dashboard_map.summary.selected_buildings == 2
    assert dashboard_map.summary.high_risk_buildings == 1
    assert dashboard_map.summary.building_type_counts["source"] == 1
    assert dashboard_map.summary.confidence_source_counts["task_map"] == 2
    assert dashboard_map.summary.confidence_source_counts["fallback"] == 1


def test_dashboard_map_preserves_roads_and_weather() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="fix mcp"),
        context=ContextHealth(status="stale", stale_reason="task changed"),
        mcp_health=McpHealth(status="warning", runtime_detail="stdio waiting"),
        selected_files=[SelectedFileRow(path="src/agentpack/mcp_server.py", score=20)],
        task_map=[TaskMapFileRow(path="src/agentpack/mcp_server.py", kind="selected", score=20, risk_level="medium")],
    )
    graph = build_dashboard_graph(snapshot)

    dashboard_map = build_dashboard_map(snapshot, graph)

    assert any(road.type == "selected_because" for road in dashboard_map.roads)
    assert any(road.route_class == "expressway" for road in dashboard_map.roads)
    assert all(road.relationship_source for road in dashboard_map.roads)
    assert any(item.id == "context" for item in dashboard_map.weather)
    assert any(item.id == "mcp" for item in dashboard_map.weather)
    assert dashboard_map.summary.stale is True


def test_action_history_merges_start_and_finish_without_output(tmp_path) -> None:
    record_dashboard_action(
        tmp_path,
        action_id="session-1",
        session_id="session-1",
        command="agentpack doctor --agent all",
        cwd=str(tmp_path),
        status="running",
        confirmed=False,
    )
    record_dashboard_action(
        tmp_path,
        action_id="session-1",
        session_id="session-1",
        command="agentpack doctor --agent all",
        cwd=str(tmp_path),
        status="completed",
        confirmed=False,
        returncode=0,
    )

    rows = read_action_history(tmp_path)

    assert len(rows) == 1
    assert rows[0].status == "completed"
    assert rows[0].started_at
    assert rows[0].ended_at
    assert rows[0].command == "agentpack doctor --agent all"
    assert rows[0].returncode == 0
    assert rows[0].duration_ms is not None
