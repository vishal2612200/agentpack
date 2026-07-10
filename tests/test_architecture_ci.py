from __future__ import annotations

import json

from typer.testing import CliRunner

from agentpack.architecture.ci import write_ci_artifacts
from agentpack.cli import app


def test_ci_artifacts_are_source_free_and_render_summary(tmp_path) -> None:
    diff = tmp_path / "raw-diff.json"
    check = tmp_path / "raw-check.json"
    diff.write_text(
        json.dumps(
            {
                "affected_domains": ["src"],
                "added_edges": [{"path": "/private/source.py", "source_hash": "secret"}],
                "removed_edges": [],
                "repo_fingerprint": "private",
            }
        ),
        encoding="utf-8",
    )
    check.write_text(json.dumps({"violations": [{"blocking": True}]}), encoding="utf-8")

    artifacts = write_ci_artifacts(diff_path=diff, check_path=check, output_dir=tmp_path / "artifacts")
    payload = json.loads((tmp_path / "artifacts" / "architecture-diff.json").read_text(encoding="utf-8"))
    summary = (tmp_path / "artifacts" / "architecture-diff.md").read_text(encoding="utf-8")

    assert artifacts["receipt"].endswith("architecture-receipt.json")
    assert "repo_fingerprint" not in payload
    assert "source_hash" not in json.dumps(payload)
    assert "[redacted-path]" in json.dumps(payload)
    assert "Blocking invariant results: 1" in summary


def test_ci_init_architecture_writes_hardened_pull_request_workflow(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["ci", "init", "--architecture", "--json"])
    payload = json.loads(result.output)
    workflow = tmp_path / ".github" / "workflows" / "agentpack-architecture.yml"
    content = workflow.read_text(encoding="utf-8")

    assert result.exit_code == 0, result.output
    assert payload["written"] is True
    assert "pull_request:" in content
    assert "pull_request_target" not in content
    assert 'python -m pip install ".[tree-sitter]"' in content
    assert "architecture-diff.json" in content
    assert "actions/upload-artifact@v4" in content
    assert "actions/github-script@v7" in content
    assert "persist-credentials: false" in content
