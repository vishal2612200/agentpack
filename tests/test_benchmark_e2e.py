from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app


def test_e2e_report_output_contains_reproducible_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    results = tmp_path / "results.jsonl"
    rows = []
    for case_index in range(5):
        for strategy in ("no-context", "agentpack"):
            for trial in range(1, 4):
                rows.append({
                    "case": f"case-{case_index}",
                    "strategy": strategy,
                    "trial": trial,
                    "passed": strategy == "agentpack",
                    "input_tokens": 100,
                    "agent_output_tokens": 50,
                    "estimated_total_cost_usd": 0.01,
                    "duration_s": 1.0,
                    "agent_tool_calls": 2,
                    "time_to_first_expected_file_s": 0.5,
                    "expected_files_touched": ["src/app.py"] if strategy == "agentpack" else [],
                    "missing_expected_edits": [] if strategy == "agentpack" else ["src/app.py"],
                    "agentpack_noise": [],
                    "risk_category": f"risk-{case_index}",
                    "agent_command": "test-agent --model test",
                    "model": "test-model",
                })
    results.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    output = tmp_path / "benchmarks" / "results" / "2026-08-12-v0.4.3-e2e-ab.md"

    result = CliRunner().invoke(
        app,
        ["benchmark", "e2e-report", "--results", str(results), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    report = output.read_text(encoding="utf-8")
    assert "- agentpack version: 0.4.3" in report
    assert "- total runs: 30" in report
    assert "## Trial matrix" in report
    assert "| task success |" in report
