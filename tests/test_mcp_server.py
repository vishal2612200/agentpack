"""Tests for mcp_server.py — _repo_root, _truncate_to_budget, get_context staleness, explain_file, get_related_files, get_stats."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock


from agentpack.mcp_server import (
    _repo_root,
    _readiness_impl,
    _truncate_to_budget,
    _get_context_impl,
    _get_delta_context_impl,
    _get_task_map_impl,
    _get_stats_impl,
    _retrieve_context_impl,
    _compress_output_impl,
    _explain_file_impl,
    _get_related_files_impl,
    _resolve_mcp_task,
    _pack_context_impl,
    _route_task_impl,
    _validate_toon_impl,
)


# ---------------------------------------------------------------------------
# _repo_root
# ---------------------------------------------------------------------------

def test_repo_root_finds_agentpack_dir(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    deep = tmp_path / "src" / "pkg"
    deep.mkdir(parents=True)
    with patch("agentpack.mcp_server.Path") as mock_path_cls:
        mock_path_cls.cwd.return_value = deep
        # Use real Path for parents traversal
        with patch.object(Path, "cwd", return_value=deep):
            result = _repo_root()
    assert result == tmp_path


def test_repo_root_fallback_to_cwd(tmp_path):
    with patch.object(Path, "cwd", return_value=tmp_path):
        result = _repo_root()
    assert result == tmp_path


# ---------------------------------------------------------------------------
# _truncate_to_budget
# ---------------------------------------------------------------------------

def _make_pack(n_files: int = 5, chars_per_file: int = 500) -> str:
    header = "# AgentPack Context for Claude\n\n## Token Stats\n\nRaw: 10000\n\n"
    file_section = "\n## File Context\n\n"
    for i in range(n_files):
        file_section += f"\n### src/file_{i}.py\n\n" + "x" * chars_per_file + "\n"
    return header + file_section


def test_truncate_noop_when_under_budget():
    short = "x" * 100
    assert _truncate_to_budget(short, max_tokens=1000) == short


def test_truncate_applies_when_over_budget():
    large = _make_pack(n_files=20, chars_per_file=1000)
    result = _truncate_to_budget(large, max_tokens=10)
    assert len(result) <= 10 * 4 + 300  # budget_chars + truncation message overhead
    assert "Truncated" in result


def test_truncate_keeps_header():
    large = _make_pack(n_files=20, chars_per_file=500)
    result = _truncate_to_budget(large, max_tokens=100)
    assert "# AgentPack Context for Claude" in result
    assert "## Token Stats" in result


def test_truncate_message_mentions_omitted_files():
    large = _make_pack(n_files=20, chars_per_file=500)
    result = _truncate_to_budget(large, max_tokens=50)
    assert "files omitted" in result or "Truncated" in result


def test_truncate_no_truncation_marker_when_fits():
    small = _make_pack(n_files=1, chars_per_file=10)
    result = _truncate_to_budget(small, max_tokens=10000)
    assert "Truncated" not in result


def test_readiness_impl_defaults_to_toon(tmp_path):
    result = _readiness_impl(tmp_path)

    assert "@format toon" in result
    assert "mcp_server: agentpack" in result
    assert "latest_context:" in result


def test_readiness_impl_can_emit_json(tmp_path):
    result = _readiness_impl(tmp_path, "json")

    payload = json.loads(result)
    assert payload["mcp_server"] == "agentpack"
    assert payload["recommended_next_tool"] in {"route_task", "start_task", "get_context", "get_delta_context"}
    assert "token_hint" in payload


def test_route_task_impl_can_emit_toon(tmp_path):
    mocked = MagicMock()
    mocked.model_dump.return_value = {"task": "fix auth", "selected_files": [{"path": "src/auth.py", "score": 10}]}

    with patch("agentpack.router.service.RouteService") as MockService:
        MockService.return_value.route_task.return_value = mocked
        result = _route_task_impl(tmp_path, "fix auth", "toon")

    assert "@format toon" in result
    assert "task: fix auth" in result
    assert "selected_files[path|score]:" in result


def test_route_task_impl_defaults_to_toon(tmp_path):
    mocked = MagicMock()
    mocked.model_dump.return_value = {"task": "fix auth"}

    with patch("agentpack.router.service.RouteService") as MockService:
        MockService.return_value.route_task.return_value = mocked
        result = _route_task_impl(tmp_path, "fix auth")

    assert result.startswith("@format toon\n@root agentpack_route\n")
    assert "task: fix auth" in result


def test_route_task_impl_can_emit_json(tmp_path):
    mocked = MagicMock()
    mocked.model_dump.return_value = {"task": "fix auth"}

    with patch("agentpack.router.service.RouteService") as MockService:
        MockService.return_value.route_task.return_value = mocked
        result = _route_task_impl(tmp_path, "fix auth", "json")

    payload = json.loads(result)
    assert payload["task"] == "fix auth"


def test_mcp_compress_output_preserves_error(tmp_path):
    result = _compress_output_impl(tmp_path, "noise\n" * 30 + "ERROR src/app.py:10 failed\n", kind="pytest")

    assert "ERROR src/app.py:10 failed" in result


def test_mcp_validate_toon_accepts_content(tmp_path):
    result = _validate_toon_impl(
        tmp_path,
        content="@format toon\n@root sample\nname: demo\nitems[]:\n  - one\n",
        output_format="json",
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["root"] == "sample"
    assert payload["parsed_type"] == "dict"


def test_mcp_validate_toon_rejects_missing_format(tmp_path):
    result = _validate_toon_impl(tmp_path, content="name: demo\n", output_format="json")

    payload = json.loads(result)
    assert payload["ok"] is False
    assert "missing required @format toon" in payload["error"]


def test_mcp_validate_toon_accepts_schema_json_fallback(tmp_path):
    result = _validate_toon_impl(
        tmp_path,
        content=json.dumps({"findings": [], "coverage": "complete"}),
        schema="review-findings",
        allow_json=True,
        output_format="json",
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["schema"] == "review-findings"
    assert payload["input_format"] == "json"
    assert payload["canonical_available"] is True


def test_mcp_validate_toon_can_return_canonical_toon(tmp_path):
    result = _validate_toon_impl(
        tmp_path,
        content=json.dumps({"findings": [], "coverage": "complete"}),
        schema="review-findings",
        allow_json=True,
        return_canonical=True,
        output_format="json",
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["canonical_root"] == "review_findings"
    assert payload["canonical_input_format"] == "json"
    assert payload["canonical_toon"].startswith("@format toon\n@root review_findings\n")
    assert "findings[]:" in payload["canonical_toon"]


def test_mcp_validate_toon_requires_one_source(tmp_path):
    result = _validate_toon_impl(tmp_path, output_format="json")

    payload = json.loads(result)
    assert payload["ok"] is False
    assert "provide exactly one" in payload["error"]


def test_mcp_retrieve_context_missing_registry(tmp_path):
    result = _retrieve_context_impl(tmp_path, path="src/app.py")

    assert "No pack registry found" in result


def test_mcp_get_task_map_returns_json(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(
        json.dumps(
            {
                "task_map": {
                    "schema_version": 1,
                    "task": "fix auth",
                    "files": [
                        {
                            "path": "src/auth.py",
                            "kind": "selected",
                            "risk_level": "medium",
                            "retrieve_ref": "src__auth.py:abc123",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    payload = json.loads(_get_task_map_impl(tmp_path, "json"))

    assert payload["task"] == "fix auth"
    assert payload["files"][0]["retrieve_ref"] == "src__auth.py:abc123"


def test_mcp_retrieve_context_supports_targets_and_kind(tmp_path):
    from agentpack.core.models import ContextPack, FileInfo, OmittedRelevantFile, SelectedFile
    from agentpack.core.pack_registry import save_pack_registry
    from agentpack.core.scanner import file_hash

    source = tmp_path / "src.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    pack = ContextPack(
        task="test",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=10,
        raw_repo_tokens=100,
        after_ignore_tokens=100,
        estimated_savings_percent=90,
        changed_files=["src.py"],
        selected_files=[SelectedFile(path="src.py", score=100, include_mode="summary", reasons=["modified"], summary="selected")],
        omitted_relevant_files=[
            OmittedRelevantFile(path="src.py", score=80, estimated_tokens=10, suggested_mode="full", omission_reason="omitted")
        ],
        receipts=[],
        freshness={"snapshot_root_hash": "abc", "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    info = FileInfo(path="src.py", abs_path=source, size_bytes=source.stat().st_size, estimated_tokens=10, hash=file_hash(source))
    save_pack_registry(tmp_path, pack, [info])

    result = _retrieve_context_impl(tmp_path, targets=["src.py"], kind="omitted")

    assert "- kind: omitted" in result
    assert "omitted" in result


def test_mcp_retrieve_context_reports_truncated_targets(tmp_path):
    from agentpack.core.models import ContextPack, FileInfo, SelectedFile
    from agentpack.core.pack_registry import save_pack_registry
    from agentpack.core.scanner import file_hash

    source = tmp_path / "src.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    pack = ContextPack(
        task="test",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=10,
        raw_repo_tokens=100,
        after_ignore_tokens=100,
        estimated_savings_percent=90,
        changed_files=["src.py"],
        selected_files=[SelectedFile(path="src.py", score=100, include_mode="summary", reasons=["modified"], summary="selected")],
        omitted_relevant_files=[],
        receipts=[],
        freshness={"snapshot_root_hash": "abc", "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    info = FileInfo(path="src.py", abs_path=source, size_bytes=source.stat().st_size, estimated_tokens=10, hash=file_hash(source))
    save_pack_registry(tmp_path, pack, [info])

    result = _retrieve_context_impl(tmp_path, targets=["src.py"] * 13)

    assert "Note: retrieve_context targets truncated to first 12; 1 target(s) not retrieved." in result


# ---------------------------------------------------------------------------
# get_context — staleness signal
# ---------------------------------------------------------------------------

def _write_metadata(root: Path, root_hash: str, token_estimate: int = 1000) -> None:
    meta = {
        "context_path": ".agentpack/context.claude.md",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_root_hash": root_hash,
        "task": "test",
        "agent": "claude",
        "mode": "balanced",
        "budget": 25000,
        "token_estimate": token_estimate,
    }
    (root / ".agentpack").mkdir(exist_ok=True)
    (root / ".agentpack" / "pack_metadata.json").write_text(json.dumps(meta))


def _write_snapshot(root: Path, root_hash: str) -> None:
    snap = {"version": 1, "root_hash": root_hash, "created_at": "2026-01-01T00:00:00+00:00", "files": {}}
    (root / ".agentpack" / "snapshots").mkdir(parents=True, exist_ok=True)
    (root / ".agentpack" / "snapshots" / "latest.json").write_text(json.dumps(snap))


def test_get_context_returns_empty_when_no_pack(tmp_path):
    assert _get_context_impl(tmp_path) == ""


def test_get_context_fresh_when_hashes_match(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "context.claude.md").write_text("# pack content")
    _write_metadata(tmp_path, root_hash="abc123", token_estimate=5000)
    _write_snapshot(tmp_path, root_hash="abc123")

    result = _get_context_impl(tmp_path)
    assert "Context is fresh" in result
    assert "5,000 tokens" in result
    assert "# pack content" in result


def test_get_context_reads_thread_scoped_pack(tmp_path):
    scoped = tmp_path / ".agentpack" / "threads" / "codex-local"
    scoped.mkdir(parents=True)
    (scoped / "context.claude.md").write_text("# scoped pack")
    (scoped / "pack_metadata.json").write_text(json.dumps({
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_root_hash": "abc123",
        "task": "thread task",
        "token_estimate": 100,
    }))
    _write_snapshot(tmp_path, root_hash="abc123")

    result = _get_context_impl(tmp_path, thread_id="codex-local")

    assert "Context is fresh" in result
    assert "# scoped pack" in result


def test_get_context_refuses_done_thread_context(tmp_path):
    scoped = tmp_path / ".agentpack" / "threads" / "codex-local"
    scoped.mkdir(parents=True)
    (scoped / "context.md").write_text("# old done pack")
    (scoped / "task_state.md").write_text("Status: done\nSummary: Finished\n", encoding="utf-8")

    result = _get_context_impl(tmp_path, thread_id="codex-local")

    assert "marked done" in result
    assert "Completed context will not be reused" in result
    assert "# old done pack" not in result


def test_get_context_auto_refreshes_when_hashes_differ(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "context.claude.md").write_text("# pack content")
    _write_metadata(tmp_path, root_hash="abc123")
    _write_snapshot(tmp_path, root_hash="def456")

    with patch("agentpack.mcp_server._pack_context_impl", return_value="# refreshed") as mock_pack:
        result = _get_context_impl(tmp_path)

    mock_pack.assert_called_once_with(tmp_path, task="", max_tokens=20000)
    assert "Context auto-refreshed because repo snapshot changed" in result


def test_get_context_auto_refreshes_when_task_md_differs(tmp_path):
    from agentpack.mcp_server import _get_context_impl

    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "context.md").write_text("# AgentPack Context\n")
    (tmp_path / ".agentpack" / "task.md").write_text("fix different task\n")
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(json.dumps({
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_root_hash": "abc123",
        "task": "fix old task",
        "token_estimate": 100,
    }))
    snap_dir = tmp_path / ".agentpack" / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "latest.json").write_text(json.dumps({"root_hash": "abc123"}))

    with patch("agentpack.mcp_server._pack_context_impl", return_value="# fresh context") as mock_pack:
        result = _get_context_impl(tmp_path)

    mock_pack.assert_called_once_with(tmp_path, task="", max_tokens=20000)
    assert "Context auto-refreshed" in result
    assert ".agentpack/task.md differs from the packed task" in result
    assert "# fresh context" in result


def test_get_context_falls_back_when_task_auto_refresh_fails(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "context.md").write_text("# old context")
    (tmp_path / ".agentpack" / "task.md").write_text("fix current task\n")
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(json.dumps({
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_root_hash": "abc123",
        "task": "fix old task",
        "token_estimate": 100,
    }))
    snap_dir = tmp_path / ".agentpack" / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "latest.json").write_text(json.dumps({"root_hash": "abc123"}))

    with patch("agentpack.mcp_server._pack_context_impl", side_effect=RuntimeError("boom")):
        result = _get_context_impl(tmp_path)

    assert "Auto-refresh failed: boom" in result
    assert "`pack_context()` to retry" in result
    assert "Stale Context Provenance" in result
    assert "available_cli_commands" in result
    assert "direct `rg`, PR diff inspection, and target-file reads" in result
    assert "# old context" in result


def test_get_context_stale_header_includes_provenance(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "context.md").write_text("# old context")
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(json.dumps({
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_root_hash": "old",
        "task": "fix old task",
        "token_estimate": 100,
        "freshness": {
            "agentpack_version": "0.1.0",
            "cwd": "/tmp/old",
            "git_root": "/tmp/repo",
            "git_branch": "main",
        },
    }))
    snap_dir = tmp_path / ".agentpack" / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "latest.json").write_text(json.dumps({"root_hash": "new"}))

    with patch("agentpack.mcp_server._pack_context_impl", side_effect=RuntimeError("boom")):
        result = _get_context_impl(tmp_path)

    assert "STALE AgentPack context" in result
    assert "Stale Context Provenance" in result
    assert "fix old task" in result
    assert "0.1.0" in result
    assert "refresh_command" in result


def test_get_context_auto_refreshes_when_snapshot_differs(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "context.md").write_text("# old context")
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth\n")
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(json.dumps({
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_root_hash": "old",
        "task": "fix auth",
        "token_estimate": 100,
    }))
    snap_dir = tmp_path / ".agentpack" / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "latest.json").write_text(json.dumps({"root_hash": "new"}))

    with patch("agentpack.mcp_server._pack_context_impl", return_value="# refreshed") as mock_pack:
        result = _get_context_impl(tmp_path)

    mock_pack.assert_called_once_with(tmp_path, task="", max_tokens=20000)
    assert "Context auto-refreshed because repo snapshot changed" in result
    assert "# refreshed" in result


def test_get_context_auto_refreshes_when_no_metadata(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "context.claude.md").write_text("# pack content")
    _write_snapshot(tmp_path, root_hash="abc123")

    with patch("agentpack.mcp_server._pack_context_impl", return_value="# refreshed") as mock_pack:
        result = _get_context_impl(tmp_path)

    mock_pack.assert_called_once_with(tmp_path, task="", max_tokens=20000)
    assert "Context auto-refreshed because pack metadata missing" in result


def test_get_context_auto_refreshes_when_no_snapshot(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "context.claude.md").write_text("# pack content")
    _write_metadata(tmp_path, root_hash="abc123")

    with patch("agentpack.mcp_server._pack_context_impl", return_value="# refreshed") as mock_pack:
        result = _get_context_impl(tmp_path)

    mock_pack.assert_called_once_with(tmp_path, task="", max_tokens=20000)
    assert "Context auto-refreshed because repo snapshot missing" in result


def test_resolve_mcp_task_writes_task_md(tmp_path):
    result = _resolve_mcp_task(tmp_path, "  fix auth   token  ")

    assert result == "fix auth token"
    assert (tmp_path / ".agentpack" / "task.md").read_text(encoding="utf-8") == "fix auth token\n"


def test_resolve_mcp_task_reads_existing_task_md(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("fix cached context\n", encoding="utf-8")

    assert _resolve_mcp_task(tmp_path) == "fix cached context"


def test_resolve_mcp_task_refuses_scoped_global_fallback(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("old global task\n", encoding="utf-8")

    try:
        _resolve_mcp_task(tmp_path, thread_id="claude-local")
    except ValueError as exc:
        assert "No task is set for AgentPack session claude-local" in str(exc)
    else:
        raise AssertionError("expected scoped MCP task resolution to refuse global task fallback")


def test_pack_context_impl_uses_mcp_task_and_returns_context(tmp_path):
    mock_result = MagicMock()
    mock_result.pack = MagicMock()
    mock_result.pack.task = "fix auth"
    with patch("agentpack.application.pack_service.PackService") as MockService, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"), \
         patch("agentpack.renderers.markdown.render_claude", return_value="# context"):
        MockService.return_value.run.return_value = mock_result
        result = _pack_context_impl(tmp_path, task="fix auth", max_tokens=1000)

    assert result == "# context"
    assert (tmp_path / ".agentpack" / "task.md").read_text(encoding="utf-8") == "fix auth\n"
    request = MockService.return_value.run.call_args[0][0]
    assert request.task == "fix auth"
    assert request.task_source == "mcp"


def test_mcp_first_end_to_end_fixture(tmp_path):
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "auth.py").write_text(
        "from .session import session_age\n\n"
        "def validate_token(token: str) -> bool:\n"
        "    return token == 'ok' and session_age() >= 0\n",
        encoding="utf-8",
    )
    (src / "session.py").write_text(
        "def session_age() -> int:\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (tests / "test_auth.py").write_text(
        "from src.auth import validate_token\n\n"
        "def test_validate_token():\n"
        "    assert validate_token('ok')\n",
        encoding="utf-8",
    )

    context = _pack_context_impl(tmp_path, task="fix auth token validation", max_tokens=8000)
    cached = _get_context_impl(tmp_path)
    related = _get_related_files_impl(tmp_path, "src/auth.py", depth=1)
    explained = _explain_file_impl(tmp_path, "src/auth.py")

    assert (tmp_path / ".agentpack" / "task.md").read_text(encoding="utf-8") == "fix auth token validation\n"
    assert "fix auth token validation" in context
    assert "src/auth.py" in context
    assert "Context is fresh" in cached
    assert "src/session.py" in related
    assert "tests/test_auth.py" in related
    assert "## src/auth.py" in explained


# ---------------------------------------------------------------------------
# _get_stats_impl
# ---------------------------------------------------------------------------

def _write_metadata_full(root: Path, **overrides) -> None:
    meta = {
        "context_path": ".agentpack/context.claude.md",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_root_hash": "abc",
        "task": "fix login bug",
        "agent": "claude",
        "mode": "balanced",
        "budget": 25000,
        "token_estimate": 4200,
    }
    meta.update(overrides)
    (root / ".agentpack").mkdir(exist_ok=True)
    (root / ".agentpack" / "pack_metadata.json").write_text(json.dumps(meta))


def test_get_stats_no_metadata(tmp_path):
    result = _get_stats_impl(tmp_path)
    assert "No pack metadata found" in result


def test_get_stats_returns_task_and_tokens(tmp_path):
    _write_metadata_full(tmp_path)
    result = _get_stats_impl(tmp_path)
    assert "fix login bug" in result
    assert "4,200" in result
    assert "claude" in result
    assert "balanced" in result


def test_get_stats_includes_metrics_when_present(tmp_path):
    _write_metadata_full(tmp_path)
    metrics = {
        "ts": "2026-01-01T00:00:00+00:00",
        "task": "fix login bug",
        "mode": "balanced",
        "packed_tokens": 4200,
        "raw_tokens": 50000,
        "saving_pct": 91.6,
        "selected_files": 8,
        "changed_files": 3,
        "selected_paths": [],
        "phases": {},
        "total_s": 1.23,
    }
    (tmp_path / ".agentpack" / "metrics.jsonl").write_text(json.dumps(metrics) + "\n")
    result = _get_stats_impl(tmp_path)
    assert "Last pack run" in result
    assert "91.6%" in result
    assert "1.23s" in result


def test_get_stats_includes_selection_f1_when_present(tmp_path):
    _write_metadata_full(tmp_path)
    metrics = {
        "task": "t", "mode": "balanced", "packed_tokens": 1, "raw_tokens": 100,
        "saving_pct": 99.0, "selected_files": 1, "changed_files": 0,
        "selected_paths": [], "phases": {}, "total_s": 0.5,
        "selection_f1": 0.875,
    }
    (tmp_path / ".agentpack" / "metrics.jsonl").write_text(json.dumps(metrics) + "\n")
    result = _get_stats_impl(tmp_path)
    assert "0.875" in result


def test_get_stats_corrupt_metadata(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text("not json")
    result = _get_stats_impl(tmp_path)
    assert "Failed to read pack metadata" in result


def test_get_delta_context_returns_latest_delta(tmp_path):
    _write_metadata_full(
        tmp_path,
        freshness={"delta_summary": "Selected delta: +1 new, -0 removed"},
        selected_files_meta=[
            {"path": "src/hooks.py", "mode": "diff", "why": "modified"},
        ],
    )
    result = _get_delta_context_impl(tmp_path)
    assert "Selected delta" in result
    assert "src/hooks.py" in result


# ---------------------------------------------------------------------------
# _explain_file_impl — uses mocked PackPlanner
# ---------------------------------------------------------------------------

def _make_mock_plan(path: str, score: float = 150.0, reasons: list[str] | None = None):
    from agentpack.core.models import DependencyGraph, DependencyNode

    reasons = reasons or ["modified"]
    fi = MagicMock()
    fi.path = path
    fi.estimated_tokens = 300

    plan = MagicMock()
    plan.scored = [(fi, score, reasons)]
    plan.selected = []
    plan.summaries = {}
    plan.scan_result = MagicMock()
    plan.scan_result.packable = [fi]

    graph = DependencyGraph()
    graph.nodes[path] = DependencyNode(
        path=path,
        imports=["src/other.py"],
        imported_by=["src/main.py"],
    )
    plan.dep_graph = graph
    return plan


def test_explain_file_unknown_path(tmp_path):
    mock_plan = _make_mock_plan("src/auth.py")
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = _explain_file_impl(tmp_path, "nonexistent.py", task="test task")
    assert "not found in scoring data" in result


def test_explain_file_returns_score_and_signals(tmp_path):
    mock_plan = _make_mock_plan("src/auth.py", score=200.0, reasons=["modified", "staged"])
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = _explain_file_impl(tmp_path, "src/auth.py", task="fix auth bug")
    assert "## src/auth.py" in result
    assert "200" in result
    assert "modified" in result
    assert "staged" in result
    assert "fix auth bug" in result


def test_explain_file_falls_back_to_task_md(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("add stripe webhook")
    mock_plan = _make_mock_plan("src/pay.py")
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = _explain_file_impl(tmp_path, "src/pay.py")
    assert "add stripe webhook" in result


def test_explain_file_shows_dep_graph(tmp_path):
    mock_plan = _make_mock_plan("src/auth.py")
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = _explain_file_impl(tmp_path, "src/auth.py", task="t")
    assert "src/other.py" in result
    assert "src/main.py" in result


# ---------------------------------------------------------------------------
# _get_related_files_impl — uses mocked PackPlanner
# ---------------------------------------------------------------------------

def _make_graph_plan(nodes: dict[str, dict]):
    from agentpack.core.models import DependencyGraph, DependencyNode

    graph = DependencyGraph()
    for path, data in nodes.items():
        graph.nodes[path] = DependencyNode(
            path=path,
            imports=data.get("imports", []),
            imported_by=data.get("imported_by", []),
            tests=data.get("tests", []),
        )
    plan = MagicMock()
    plan.dep_graph = graph
    return plan


def test_get_related_files_no_neighbours(tmp_path):
    plan = _make_graph_plan({"src/lone.py": {}})
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = plan
        result = _get_related_files_impl(tmp_path, "src/lone.py")
    assert "No related files found" in result


def test_get_related_files_direct_imports(tmp_path):
    plan = _make_graph_plan({
        "src/a.py": {"imports": ["src/b.py"], "imported_by": ["src/c.py"]},
    })
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = plan
        result = _get_related_files_impl(tmp_path, "src/a.py", depth=1)
    assert "src/b.py" in result
    assert "src/c.py" in result
    assert "imports" in result
    assert "imported_by" in result


def test_get_related_files_depth2(tmp_path):
    plan = _make_graph_plan({
        "src/a.py": {"imports": ["src/b.py"]},
        "src/b.py": {"imports": ["src/c.py"]},
    })
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = plan
        result = _get_related_files_impl(tmp_path, "src/a.py", depth=2)
    assert "src/b.py" in result
    assert "src/c.py" in result
    assert "hop 2" in result


def test_get_related_files_depth_clamped(tmp_path):
    plan = _make_graph_plan({"src/a.py": {}})
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = plan
        # depth=99 should clamp to 2, not crash
        result = _get_related_files_impl(tmp_path, "src/a.py", depth=99)
    assert "No related files found" in result


def test_get_related_files_excludes_self(tmp_path):
    plan = _make_graph_plan({
        "src/a.py": {"imports": ["src/a.py", "src/b.py"]},
    })
    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.adapters.detect.detect_agent", return_value="generic"):
        MockPlanner.return_value.plan.return_value = plan
        result = _get_related_files_impl(tmp_path, "src/a.py", depth=1)
    # src/a.py should not appear as its own neighbour
    lines = [ln for ln in result.splitlines() if "src/a.py" in ln and "Related files for" not in ln]
    assert not lines
