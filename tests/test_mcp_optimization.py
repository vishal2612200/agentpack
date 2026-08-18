"""Regression tests for MCP cache, isolation, bounds, timeout, and telemetry controls."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentpack.application.pack_service import PackPlanner, PackRequest, PackTimeoutError
from agentpack.core.jsonl import append_record
from agentpack.mcp_server import (
    _McpSession,
    _compress_output_impl,
    _explain_file_impl,
    _graph_index,
    _repo_root,
    _truncate_to_budget,
)
from agentpack.router.service import _route_cache_key


def test_repo_root_honors_explicit_workspace(monkeypatch, tmp_path):
    (tmp_path / ".agentpack").mkdir()
    monkeypatch.setenv("AGENTPACK_ROOT", str(tmp_path))

    assert _repo_root() == tmp_path.resolve()


def test_route_cache_key_ignores_telemetry_files(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    first = _route_cache_key(tmp_path, "fix auth")
    (tmp_path / ".agentpack" / "metrics.jsonl").write_text("{}\n", encoding="utf-8")

    assert _route_cache_key(tmp_path, "fix auth") == first


def test_route_cache_key_includes_routing_event_files(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    first = _route_cache_key(tmp_path, "fix auth")
    (tmp_path / ".agentpack" / "session-events.jsonl").write_text("{}\n", encoding="utf-8")
    second = _route_cache_key(tmp_path, "fix auth")
    (tmp_path / ".agentpack" / "observer-events.jsonl").write_text("{}\n", encoding="utf-8")

    assert second != first
    assert _route_cache_key(tmp_path, "fix auth") != second


def test_mcp_session_reuses_plan_for_same_workspace_and_task(tmp_path):
    session = _McpSession()
    plan = MagicMock()
    with patch("agentpack.application.pack_service.PackPlanner") as planner_cls, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        planner_cls.return_value.plan.return_value = plan

        first = session.plan(tmp_path, task="fix auth", thread_id="thread-a")
        second = session.plan(tmp_path, task="fix auth", thread_id="thread-a")

    assert first is second is plan
    planner_cls.return_value.plan.assert_called_once()


def test_expired_pack_request_fails_before_work(tmp_path):
    request = PackRequest(
        root=tmp_path,
        agent="generic",
        task="fix auth",
        mode="balanced",
        budget=0,
        since=None,
        refresh=False,
        deadline=0.0,
    )

    with pytest.raises(PackTimeoutError, match="startup"):
        PackPlanner().plan(request)


def test_explain_file_returns_bounded_timeout_error(tmp_path):
    result = _explain_file_impl(tmp_path, "src/auth.py", timeout_s=0.000001)

    assert "MCP request timed out" in result


def test_compress_output_bounds_input(tmp_path):
    result = _compress_output_impl(tmp_path, "ERROR important\n" + "noise\n" * 100, max_input_chars=32)

    assert "input truncated by AgentPack" in result


def test_jsonl_writer_retains_latest_records(tmp_path):
    path = tmp_path / ".agentpack" / "metrics.jsonl"
    for index in range(12):
        append_record(path, {"index": index, "payload": "x" * 600}, max_records=5)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) <= 5
    assert rows[-1]["index"] == 11


def test_graph_index_reuses_index_for_same_snapshot(tmp_path):
    snapshot = MagicMock(schema_version=1, ref="HEAD", commit_sha="abc", file_hashes={"a.py": "1"})
    fake_index = object()
    with patch("agentpack.architecture.service.build_snapshot_for_ref", return_value=snapshot) as build, \
         patch("agentpack.architecture.index.SemanticGraphIndex", return_value=fake_index) as index_cls:
        first = _graph_index(tmp_path)
        second = _graph_index(tmp_path)

    assert first is second is fake_index
    assert build.call_count == 2
    assert all(call.kwargs["cache_validation"] == "manifest" for call in build.call_args_list)
    index_cls.assert_called_once_with(snapshot)


def test_truncate_budget_does_not_duplicate_accumulated_tokens():
    text = "# Header\n\n## File Context\n\n" + "\n".join(
        f"### file_{index}.py\n\n{'x' * 200}" for index in range(20)
    )

    result = _truncate_to_budget(text, max_tokens=100)

    assert "Truncated" in result
    assert len(result) < len(text)
