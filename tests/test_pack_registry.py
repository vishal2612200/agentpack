from __future__ import annotations

from pathlib import Path

from agentpack.core.models import ContextPack, FileInfo, OmittedRelevantFile, SelectedFile, Symbol
from agentpack.core.pack_registry import load_pack_registry, retrieve_from_registry, save_pack_registry
from agentpack.core.scanner import file_hash


def test_pack_registry_retrieves_selected_stored_content(tmp_path: Path):
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
        selected_files=[
            SelectedFile(path="src.py", score=100, include_mode="full", reasons=["modified"], content="def run():\n    return 1\n")
        ],
        receipts=[],
        freshness={"snapshot_root_hash": "abc", "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    info = FileInfo(path="src.py", abs_path=source, size_bytes=source.stat().st_size, estimated_tokens=10, hash="h1")

    save_pack_registry(tmp_path, pack, [info])
    result = retrieve_from_registry(tmp_path, path="src.py")

    assert "def run()" in result
    assert "block_id" in result


def test_pack_registry_refuses_stale_full_retrieval(tmp_path: Path):
    source = tmp_path / "src.py"
    source.write_text("old\n", encoding="utf-8")
    pack = ContextPack(
        task="test",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=10,
        raw_repo_tokens=100,
        after_ignore_tokens=100,
        estimated_savings_percent=90,
        changed_files=[],
        selected_files=[],
        omitted_relevant_files=[
            OmittedRelevantFile(path="src.py", score=80, estimated_tokens=10, suggested_mode="full")
        ],
        receipts=[],
        freshness={"snapshot_root_hash": "abc", "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    info = FileInfo(path="src.py", abs_path=source, size_bytes=4, estimated_tokens=10, hash="old-hash")
    save_pack_registry(tmp_path, pack, [info])
    source.write_text("new\n", encoding="utf-8")

    result = retrieve_from_registry(tmp_path, path="src.py", mode="full")

    assert "changed since the latest pack registry" in result


def test_pack_registry_full_retrieval_accepts_matching_scanner_hash(tmp_path: Path):
    source = tmp_path / "src.py"
    source.write_text("def current():\n    return 1\n", encoding="utf-8")
    pack = ContextPack(
        task="test",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=10,
        raw_repo_tokens=100,
        after_ignore_tokens=100,
        estimated_savings_percent=90,
        changed_files=[],
        selected_files=[],
        omitted_relevant_files=[
            OmittedRelevantFile(path="src.py", score=80, estimated_tokens=10, suggested_mode="full")
        ],
        receipts=[],
        freshness={"snapshot_root_hash": "abc", "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    info = FileInfo(path="src.py", abs_path=source, size_bytes=source.stat().st_size, estimated_tokens=10, hash=file_hash(source))
    save_pack_registry(tmp_path, pack, [info])

    result = retrieve_from_registry(tmp_path, path="src.py", mode="full")

    assert "def current()" in result


def test_pack_registry_retrieves_symbol_block_by_id(tmp_path: Path):
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
        selected_files=[
            SelectedFile(
                path="src.py",
                score=100,
                include_mode="symbols",
                reasons=["symbol keyword match"],
                symbols=[
                    Symbol(name="run", kind="function", start_line=1, end_line=2, signature="def run():", body="def run():\n    return 1")
                ],
            )
        ],
        receipts=[],
        freshness={"snapshot_root_hash": "abc", "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    info = FileInfo(path="src.py", abs_path=source, size_bytes=source.stat().st_size, estimated_tokens=10, hash=file_hash(source))

    save_pack_registry(tmp_path, pack, [info])
    registry = load_pack_registry(tmp_path)
    assert registry is not None
    block_id = next(record.block_id for record in registry.records if record.symbol == "run")
    result = retrieve_from_registry(tmp_path, block_id=block_id)

    assert "- symbol: run" in result
    assert "return 1" in result


def test_pack_registry_can_filter_selected_and_omitted_same_path(tmp_path: Path):
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
        selected_files=[
            SelectedFile(path="src.py", score=100, include_mode="summary", reasons=["modified"], summary="selected summary")
        ],
        omitted_relevant_files=[
            OmittedRelevantFile(
                path="src.py",
                score=80,
                estimated_tokens=10,
                suggested_mode="full",
                omission_reason="omitted summary",
            )
        ],
        receipts=[],
        freshness={"snapshot_root_hash": "abc", "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    info = FileInfo(path="src.py", abs_path=source, size_bytes=source.stat().st_size, estimated_tokens=10, hash=file_hash(source))

    save_pack_registry(tmp_path, pack, [info])

    selected = retrieve_from_registry(tmp_path, path="src.py", kind="selected")
    omitted = retrieve_from_registry(tmp_path, path="src.py", kind="omitted")
    assert "- kind: selected" in selected
    assert "selected summary" in selected
    assert "- kind: omitted" in omitted
    assert "omitted summary" in omitted
