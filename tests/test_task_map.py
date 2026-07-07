from __future__ import annotations

from pathlib import Path

from agentpack.core.models import ContextPack, DependencyGraph, DependencyNode, FileInfo, OmittedRelevantFile, SelectedFile
from agentpack.core.pack_registry import build_pack_registry
from agentpack.core.task_map import build_task_map


def test_task_map_adds_risk_tests_impact_and_retrieve_refs(tmp_path: Path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir()
    source.write_text("def validate():\n    return True\n", encoding="utf-8")
    test = tmp_path / "tests" / "test_auth.py"
    test.parent.mkdir()
    test.write_text("def test_validate():\n    assert True\n", encoding="utf-8")
    omitted = tmp_path / "src" / "auth_config.py"
    omitted.write_text("TOKEN_TTL = 10\n", encoding="utf-8")
    pack = ContextPack(
        task="fix auth token expiry",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=120,
        raw_repo_tokens=1000,
        after_ignore_tokens=1000,
        estimated_savings_percent=88,
        changed_files=["src/auth.py"],
        selected_files=[
            SelectedFile(
                path="src/auth.py",
                score=220,
                include_mode="full",
                reasons=["modified", "matched define: validate"],
                content=source.read_text(encoding="utf-8"),
            )
        ],
        omitted_relevant_files=[
            OmittedRelevantFile(
                path="src/auth_config.py",
                score=180,
                estimated_tokens=20,
                suggested_mode="summary",
                omission_reason="budget exhausted",
                risk="high",
                reasons=["config file"],
            )
        ],
        receipts=[],
        freshness={"generated_at": "2026-07-07T00:00:00+00:00", "snapshot_root_hash": "root"},
    )
    packable = [
        FileInfo(path="src/auth.py", abs_path=source, size_bytes=source.stat().st_size, estimated_tokens=30, hash="h1"),
        FileInfo(path="src/auth_config.py", abs_path=omitted, size_bytes=omitted.stat().st_size, estimated_tokens=20, hash="h2"),
    ]
    graph = DependencyGraph(
        nodes={
            "src/auth.py": DependencyNode(
                path="src/auth.py",
                imported_by=["src/api.py", "src/session.py"],
                tests=["tests/test_auth.py"],
            ),
            "src/auth_config.py": DependencyNode(path="src/auth_config.py"),
        }
    )

    registry = build_pack_registry(pack, packable)
    task_map = build_task_map(pack, graph, registry)

    selected = next(item for item in task_map.files if item.path == "src/auth.py")
    assert selected.risk_level == "high"
    assert selected.tests_to_run == ["tests/test_auth.py"]
    assert selected.may_break
    assert selected.retrieve_ref
    omitted_row = next(item for item in task_map.files if item.path == "src/auth_config.py")
    assert omitted_row.kind == "omitted"
    assert omitted_row.risk_level == "high"
    assert omitted_row.retrieve_ref
    assert task_map.risk_counts["high"] == 2
