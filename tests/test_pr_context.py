from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agentpack.application.pr_context import build_pr_context, resolve_pr_context
from agentpack.architecture.service import build_snapshot_for_ref
from agentpack.core.scanner import file_hash
from agentpack.learning.episodes import record_episode
from agentpack.mcp_server import _get_pr_context_impl


def test_pr_context_uses_immutable_refs_and_architecture_evidence(tmp_path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / "src" / "service.py", "def greet():\n    return 'hello'\n")
    _write(tmp_path / "tests" / "test_service.py", "def test_greet():\n    assert True\n")
    base_sha = _commit_all(tmp_path, "base")

    _write(tmp_path / "src" / "service.py", "def greet():\n    return 'updated'\n")
    head_sha = _commit_all(tmp_path, "head")

    context = build_pr_context(
        tmp_path,
        base_ref=base_sha,
        head_ref=head_sha,
        source="local-fallback",
        focus="review compatibility",
    )

    assert context.base_sha == base_sha
    assert context.head_sha == head_sha
    assert context.focus == "review compatibility"
    assert [item.path for item in context.changed_files] == ["src/service.py"]
    assert context.relevant_tests == ["tests/test_service.py"]
    assert context.affected_entity_keys
    assert "architecture_diff" in context.context_references


def test_local_pr_context_and_mcp_output_require_explicit_fallback(tmp_path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / "src" / "service.py", "VALUE = 1\n")
    _commit_all(tmp_path, "base")
    _write(tmp_path / "src" / "service.py", "VALUE = 2\n")
    head_sha = _commit_all(tmp_path, "head")

    context = resolve_pr_context(tmp_path, focus="review", allow_local_fallback=True)
    payload = json.loads(
        _get_pr_context_impl(
            tmp_path,
            focus="review",
            output_format="json",
            allow_local_fallback=True,
        )
    )

    assert context.source == "local-fallback"
    assert context.head_sha == head_sha
    assert payload["ok"] is True
    assert payload["pr_context"]["head_sha"] == head_sha


def test_pr_context_uses_architecture_node_identity_for_memory(tmp_path) -> None:
    _init_repo(tmp_path)
    source = tmp_path / "src" / "service.py"
    _write(source, "def greet():\n    return 'hello'\n")
    base_sha = _commit_all(tmp_path, "base")
    _write(source, "def greet():\n    return 'updated'\n")
    head_sha = _commit_all(tmp_path, "head")
    snapshot = build_snapshot_for_ref(tmp_path, head_sha)
    entity = next(
        item
        for item in snapshot.entities
        if item.entity_type == "symbol" and item.locator.path == "src/service.py" and item.display_name == "greet"
    )
    node_key = str(entity.metadata["node_key"])
    record_episode(
        tmp_path,
        task="old wording only",
        selected_files=["src/service.py"],
        changed_files=["src/service.py"],
        passed=True,
        touched_nodes=[
            {
                "node_key": node_key,
                "path": "src/service.py",
                "source_hash": file_hash(source),
            }
        ],
    )

    context = build_pr_context(
        tmp_path,
        base_ref=base_sha,
        head_ref=head_sha,
        source="local-fallback",
        focus="new wording only",
    )

    assert context.memory_retrieval_chain["constraints"]["episode_gate"] == "current node identity"
    assert context.memory_retrieval_chain["compatible_episodes"][0]["matched_node_keys"] == [node_key]


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Codex"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "codex@example.com"], cwd=root, check=True, capture_output=True, text=True)


def _commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True, text=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
