from __future__ import annotations

from pathlib import Path

import pytest

from agentpack.mcp_server import _get_graph_neighbors_impl, _query_graph_impl, _shortest_path_impl


def test_mcp_graph_tools_default_to_bounded_evidence(tmp_path: Path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("def validate():\n    return missing_dependency()\n", encoding="utf-8")

    query = _query_graph_impl(tmp_path, "validate", output_format="json")
    assert "@format json" not in query
    assert "validate" in query

    neighbors = _get_graph_neighbors_impl(tmp_path, "src.auth", output_format="json")
    assert "neighbors" in neighbors


def test_mcp_shortest_path_supports_compact_and_full_detail(tmp_path: Path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("def validate():\n    return missing_dependency()\n", encoding="utf-8")

    compact = _shortest_path_impl(tmp_path, "src.auth", "missing_dependency", output_format="json")
    full = _shortest_path_impl(tmp_path, "src.auth", "missing_dependency", detail="full", output_format="json")

    assert "path" in compact
    assert "path" in full


def test_mcp_graph_rejects_unbounded_or_invalid_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="text must not be empty"):
        _query_graph_impl(tmp_path, "", output_format="json")
    with pytest.raises(ValueError, match="limit must be at least 1"):
        _get_graph_neighbors_impl(tmp_path, "missing", limit=0, output_format="json")
    with pytest.raises(ValueError, match="unsupported graph relationship"):
        _query_graph_impl(tmp_path, "auth", relationship="not-a-relation", output_format="json")
