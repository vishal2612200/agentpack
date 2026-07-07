from __future__ import annotations

import json

from agentpack.dashboard.graph import build_dashboard_graph
from agentpack.dashboard.models import (
    ContextHealth,
    DashboardSnapshot,
    ProjectInfo,
    ReviewRunRow,
    SelectedFileRow,
    SelectedSymbolRow,
    SuggestedAction,
    TaskInfo,
    TaskMapFileRow,
)


def test_dashboard_graph_builds_task_context_nodes() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="fix auth token expiry", state="in_progress"),
        context=ContextHealth(status="fresh"),
        selected_files=[SelectedFileRow(path="src/auth.py", include_mode="full", score=120, reasons=["task keyword match"])],
        task_map=[
            TaskMapFileRow(
                path="src/auth.py",
                kind="selected",
                score=120,
                risk_level="high",
                why_selected=["task keyword match"],
                tests_to_run=["tests/test_auth.py"],
                may_break=["reverse dependents: src/api.py"],
                retrieve_ref="src__auth.py:abc123",
            ),
            TaskMapFileRow(
                path="src/session.py",
                kind="omitted",
                score=60,
                risk_level="medium",
                why_selected=["related import"],
            ),
        ],
        suggested_actions=[SuggestedAction(label="Run tests", command="pytest tests/test_auth.py", reason="Validate auth")],
    )

    graph = build_dashboard_graph(snapshot)
    nodes = {node.id: node for node in graph.nodes}
    edges = {edge.id: edge for edge in graph.edges}

    assert nodes["task:active"].label == "fix auth token expiry"
    assert nodes["file:src/auth.py"].selected is True
    assert nodes["file:src/auth.py"].risk == "high"
    assert nodes["file:src/session.py"].selected is False
    assert nodes["test:tests/test_auth.py"].type == "test"
    assert any(edge.type == "selected_because" for edge in edges.values())
    assert any(edge.type == "omitted_because" for edge in edges.values())
    assert any(edge.type == "tested_by" for edge in edges.values())
    assert graph.summary.selected_files == 1
    assert graph.summary.omitted_files == 1
    assert graph.summary.high_risk_files == 1


def test_dashboard_graph_links_task_memory_to_files() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="fix cache ttl"),
        selected_files=[SelectedFileRow(path="src/cache.py")],
        task_map=[TaskMapFileRow(path="src/cache.py", kind="selected")],
        learning_memories=[
            {
                "task": "Fix cache invalidation",
                "status": "done",
                "changed_files": ["src/cache.py"],
                "selected_files": [],
            }
        ],
    )

    graph = build_dashboard_graph(snapshot)

    assert any(node.type == "episode" and node.label == "Fix cache invalidation" for node in graph.nodes)
    assert any(node.type == "task" and node.id.startswith("task:memory:") and node.label == "Fix cache invalidation" for node in graph.nodes)
    assert any(edge.type == "memory_influenced" and edge.label == "related task" for edge in graph.edges)
    assert any(edge.type == "memory_influenced" and edge.target == "file:src/cache.py" for edge in graph.edges)
    assert graph.summary.memory_nodes == 1


def test_dashboard_graph_uses_concise_task_title() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="Implement dashboard graph visualization with distinct AST, memory, review, and task nodes"),
    )

    graph = build_dashboard_graph(snapshot)
    task = next(node for node in graph.nodes if node.id == "task:active")

    assert task.label == "Implement dashboard graph visualization with distinct AST,"
    assert task.summary == "Implement dashboard graph visualization with distinct AST, memory, review, and task nodes"


def test_dashboard_graph_builds_symbol_nodes_for_selected_files() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="fix token refresh"),
        selected_files=[
            SelectedFileRow(
                path="src/auth.py",
                include_mode="symbols",
                score=130,
                reasons=["symbol keyword match"],
                symbols=[
                    SelectedSymbolRow(
                        name="refresh_token",
                        kind="function",
                        start_line=12,
                        end_line=24,
                        signature="def refresh_token(user_id: str) -> Token",
                        summary="Refreshes an expired auth token.",
                        node_id="node:refresh-token",
                        signature_hash="sig123",
                        source_hash="filehash",
                    )
                ],
            )
        ],
        task_map=[TaskMapFileRow(path="src/auth.py", kind="selected", include_mode="symbols")],
    )

    graph = build_dashboard_graph(snapshot)
    nodes = {node.id: node for node in graph.nodes}
    symbol = nodes["symbol:node:refresh-token"]

    assert symbol.type == "symbol"
    assert symbol.path == "src/auth.py"
    assert symbol.metadata["symbol"] == "refresh_token"
    assert symbol.evidence[0].line == 12
    assert any(edge.type == "contains" and edge.source == "file:src/auth.py" and edge.target == symbol.id for edge in graph.edges)


def test_dashboard_graph_links_task_memory_to_matching_symbols() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="fix cache ttl"),
        selected_files=[
            SelectedFileRow(
                path="src/cache.py",
                include_mode="symbols",
                symbols=[
                    SelectedSymbolRow(
                        name="refresh_cache",
                        kind="function",
                        start_line=10,
                        end_line=18,
                        signature="def refresh_cache(ttl: int) -> None",
                        summary="Refreshes cache entries after TTL changes.",
                        node_id="node:refresh-cache",
                    ),
                    SelectedSymbolRow(
                        name="serialize_value",
                        kind="function",
                        start_line=25,
                        end_line=30,
                        signature="def serialize_value(value: object) -> str",
                        summary="Serializes values.",
                        node_id="node:serialize-value",
                    ),
                ],
            )
        ],
        task_map=[TaskMapFileRow(path="src/cache.py", kind="selected", include_mode="symbols")],
        learning_memories=[
            {
                "task": "Fix cache ttl bug",
                "status": "done",
                "concepts": ["caching"],
                "changed_files": ["src/cache.py"],
                "selected_files": [],
            }
        ],
    )

    graph = build_dashboard_graph(snapshot)
    edges = [edge for edge in graph.edges if edge.type == "memory_influenced"]

    assert any(edge.target == "file:src/cache.py" for edge in edges)
    symbol_edges = [edge for edge in edges if edge.target.startswith("symbol:")]
    assert len(symbol_edges) == 1
    assert symbol_edges[0].target == "symbol:node:refresh-cache"
    assert "concept:caching" in symbol_edges[0].evidence[0].ref
    assert symbol_edges[0].evidence[0].line == 10


def test_dashboard_graph_builds_review_nodes() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="review auth change"),
        review_runs=[
            ReviewRunRow(
                run_id="run-123",
                branch_prefix="feature/auth",
                generated_at="2026-07-07T10:00:00Z",
                target_number=42,
                diff_source="github",
                changed_files_count=3,
                status="findings_ready",
                preflight_path=".agentpack/reviews/feature/auth/run-123/preflight.json",
                understanding_path=".agentpack/reviews/feature/auth/run-123/understanding.md",
                findings_path=".agentpack/reviews/feature/auth/run-123/findings.json",
                resume_command="agentpack review --resume run-123",
            )
        ],
    )

    graph = build_dashboard_graph(snapshot)
    nodes = {node.id: node for node in graph.nodes}
    review = nodes["review:run-123"]

    assert review.type == "review"
    assert review.label == "PR #42"
    assert review.status == "findings_ready"
    assert review.metadata["run_id"] == "run-123"
    assert any(action.command == "agentpack review --resume run-123" for action in review.actions)
    assert any(edge.type == "reviewed_by" and edge.target == review.id for edge in graph.edges)


def test_dashboard_graph_reads_timeline_memory_and_marks_stale(tmp_path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "episodic-cases.jsonl").write_text(
        json.dumps(
            {
                "episode_id": "episode:abc",
                "completed_at": "2026-07-07T00:00:00Z",
                "visible_reason": "similar prior task",
                "path_hashes": {"src/missing.py": "hash"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot = DashboardSnapshot(project=ProjectInfo(name="repo", path=str(tmp_path)), task=TaskInfo(text="fix auth"))

    graph = build_dashboard_graph(snapshot, tmp_path)
    episode = next(node for node in graph.nodes if node.id == "episode:episode:abc")

    assert episode.stale is True
    assert episode.evidence[0].summary == "similar prior task"


def test_dashboard_graph_caps_nodes_deterministically() -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="repo", path="/tmp/repo"),
        task=TaskInfo(text="large task"),
        task_map=[
            TaskMapFileRow(path=f"src/file_{index}.py", kind="omitted")
            for index in range(30)
        ],
    )

    graph = build_dashboard_graph(snapshot, max_nodes=12)

    assert len(graph.nodes) == 12
    assert graph.summary.truncated is True
    assert graph.summary.max_nodes == 12
    assert graph.summary.truncated_reason == "node limit reached"
    assert graph.model_dump(mode="json")["schema_version"] == 1
