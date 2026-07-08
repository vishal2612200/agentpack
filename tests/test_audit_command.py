from __future__ import annotations

import json

from typer.testing import CliRunner

from agentpack.cli import app


def test_audit_command_writes_loop_scaffold(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "audit",
            "src/payments",
            "--lens",
            "infra-config",
            "--passes",
            "2",
            "--max-files",
            "10",
            "--minutes",
            "20",
        ],
    )

    assert result.exit_code == 0, result.output
    assert ".agentpack/audit.prompt.md" in result.output
    assert ".agentpack/audit-report.md" in result.output
    assert ".agentpack/audit-atlas.json" in result.output
    assert ".agentpack/audit-findings.json" in result.output

    active_prompt = tmp_path / ".agentpack" / "audit.prompt.md"
    active_report = tmp_path / ".agentpack" / "audit-report.md"
    active_atlas = tmp_path / ".agentpack" / "audit-atlas.json"
    active_findings = tmp_path / ".agentpack" / "audit-findings.json"
    for path in (active_prompt, active_report, active_atlas, active_findings):
        assert path.exists()

    prompt = active_prompt.read_text(encoding="utf-8")
    assert "auditing-codebase-atlas" in prompt
    assert "Codebase Audit Atlas" in prompt
    assert "Promote findings only after the evidence gate passes" in prompt
    assert "Hypotheses, Not Findings" in prompt
    assert "Loop Log" in prompt
    assert "Infrastructure / Config Review" in prompt
    assert ".agentpack/audit-report.md" in prompt

    report = active_report.read_text(encoding="utf-8")
    assert "Developer Review Report" in report
    assert "Project Usage Signals" in report
    assert "Infrastructure / Config Review" in report
    assert "Dockerfile" in report
    assert ".github/workflows/ci.yml" in report
    assert "Hypotheses, Not Findings" in report

    atlas = json.loads(active_atlas.read_text(encoding="utf-8"))
    assert atlas["scope"] == "src/payments"
    assert atlas["lens"] == "infra-config"
    assert atlas["budget"] == {"passes": 2, "max_files": 10, "max_minutes": 20}
    assert atlas["frontier"][0]["area"] == "src/payments"
    assert atlas["findings"] == []
    assert atlas["hypotheses"] == []
    assert atlas["artifacts"]["active_report"] == ".agentpack/audit-report.md"
    assert atlas["artifacts"]["report"].endswith("/report.md")
    signal_paths = {signal["path"] for signal in atlas["project_usage_signals"]["infrastructure_config"]}
    assert "Dockerfile" in signal_paths
    assert ".github/workflows/ci.yml" in signal_paths

    findings = json.loads(active_findings.read_text(encoding="utf-8"))
    assert findings == []

    run_dirs = list((tmp_path / ".agentpack" / "audits" / "src-payments").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "runbook.md").read_text(encoding="utf-8") == prompt
    assert (run_dir / "report.md").read_text(encoding="utf-8") == report
    assert json.loads((run_dir / "atlas.json").read_text(encoding="utf-8")) == atlas
    assert json.loads((run_dir / "findings.json").read_text(encoding="utf-8")) == findings


def test_audit_command_rejects_empty_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["audit", "  "])

    assert result.exit_code == 2, result.output
    assert "Audit scope is required" in result.output
