from __future__ import annotations

import json

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.skill_review import create_skill_review_workspace


def _write_skill(root):
    path = root / "skills" / "example-review" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\n"
        "name: example-review\n"
        "description: Review API changes and produce a concise report.\n"
        "---\n\n"
        "# Example Review\n\n"
        "## Workflow\n\n"
        "1. Inspect the API change.\n"
        "2. Return a report with findings.\n\n"
        "## Output Format\n\n"
        "Return findings and validation status.\n",
        encoding="utf-8",
    )
    return path


def test_skill_review_generates_audit_and_balanced_eval_set(tmp_path):
    skill = _write_skill(tmp_path)

    workspace = create_skill_review_workspace(tmp_path, str(skill), eval_count=6)

    assert workspace.review_path.exists()
    assert workspace.findings_path.exists()
    payload = json.loads(workspace.evals_path.read_text(encoding="utf-8"))
    assert len(payload["evals"]) == 6
    assert payload["evals"][0]["prompt"]
    assert payload["evals"][0]["files"] == []
    assert sum(item["should_trigger"] for item in payload["evals"]) == 3
    assert sum(not item["should_trigger"] for item in payload["evals"]) == 3
    findings = json.loads(workspace.findings_path.read_text(encoding="utf-8"))["findings"]
    assert all(item["status"] == "pass" for item in findings)


def test_skill_review_cli_accepts_skill_name_and_json_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path)

    result = CliRunner().invoke(app, ["skill-review", "--skill", "example-review", "--eval-count", "4", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["eval_count"] == 4
    assert payload["evals"].endswith("/evals.json")


def test_skill_review_rejects_odd_eval_count(tmp_path):
    skill = _write_skill(tmp_path)

    result = CliRunner().invoke(app, ["skill-review", "--skill", str(skill), "--eval-count", "5"])

    assert result.exit_code == 1
    assert "even number" in result.output
