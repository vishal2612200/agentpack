from __future__ import annotations

import json
import subprocess

from typer.testing import CliRunner

from agentpack.architecture.budgets import compare_budget
from agentpack.architecture.ci import load_verified_ci_artifact, write_ci_artifacts
from agentpack.architecture.pr_map import build_pr_map
from agentpack.cli import app


def test_budget_warns_on_growth_and_quality_regression() -> None:
    result = compare_budget(
        {"entity_count": 130, "edge_count": 100, "artifact_bytes": 100, "unresolved_ratio": 0.08, "fallback_ratio": 0.01, "build_seconds": 3},
        {"entity_count": 100, "edge_count": 100, "artifact_bytes": 100, "unresolved_ratio": 0.02, "fallback_ratio": 0.01, "build_seconds": 1},
    )

    assert result["status"] == "warn"
    assert any("entity_count grew" in item for item in result["warnings"])
    assert any("unresolved_ratio worsened" in item for item in result["warnings"])
    assert any("build_seconds reached" in item for item in result["warnings"])


def test_verified_ci_artifact_requires_matching_head(tmp_path) -> None:
    diff = tmp_path / "diff.json"
    check = tmp_path / "check.json"
    diff.write_text(json.dumps({"head_sha": "head"}), encoding="utf-8")
    check.write_text(json.dumps({"head_sha": "head", "git_sha": "head", "violations": []}), encoding="utf-8")
    output = tmp_path / "artifacts"
    write_ci_artifacts(diff_path=diff, check_path=check, output_dir=output)

    assert load_verified_ci_artifact(output, head_sha="head") is not None
    assert load_verified_ci_artifact(output, head_sha="other") is None
    assert json.loads((output / "architecture-receipt.json").read_text(encoding="utf-8"))["git_sha"] == "head"


def test_baseline_command_writes_source_free_metrics(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    _commit(tmp_path, "initial")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["architecture", "baseline", "--ref", "HEAD", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metrics"]["entity_count"] > 0
    assert "source_hashes" not in json.dumps(payload)


def test_pr_map_contains_changed_nodes_and_policy_projection(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    (tmp_path / "src" / "main.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    head = _commit(tmp_path, "head")

    payload = build_pr_map(tmp_path, base_ref=base, head_ref=head)

    assert payload["base_sha"] == base
    assert payload["head_sha"] == head
    assert payload["summary"]["changed"] >= 1
    assert "policies" in payload
    assert "diff" not in payload["policies"]


def test_pr_map_keeps_unchanged_endpoint_for_added_road(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    (tmp_path / "src" / "app.py").write_text(
        "from .lib import helper\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    head = _commit(tmp_path, "add dependency")

    payload = build_pr_map(tmp_path, base_ref=base, head_ref=head)

    context_nodes = {node["id"] for node in payload["nodes"] if node["status"] == "context"}
    assert context_nodes
    assert any(edge["target"] in context_nodes for edge in payload["edges"] if edge["status"] == "added")


def _init_repo(root) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True, text=True)


def _commit(root, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True, text=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
