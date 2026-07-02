from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app


def test_diagnose_selection_json_and_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(
        json.dumps(
            {
                "task": "fix auth",
                "context_path": ".agentpack/context.md",
                "freshness": {"generic_task_ratio": 0.8},
                "selected_files_meta": [
                    {"path": "big.py", "mode": "full", "tokens": 2000, "reasons": ["content keyword match (2)"]},
                    {"path": "small.py", "mode": "summary", "tokens": 20, "reasons": ["filename keyword match"]},
                ],
                "pack_handoff": {
                    "skipped_uncertain": {
                        "excluded_reason_counts": {
                            "compressed context cap reached": 3,
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["diagnose-selection", "--json", "--write"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["largest_token_consumers"][0]["path"] == "big.py"
    assert payload["selection_explanations"][0]["why_selected"] == ["content keyword match"]
    assert payload["omission_summary"][0]["reason"] == "compressed context cap reached"
    assert (tmp_path / ".agentpack" / "selection_diagnosis.md").exists()
    report = (tmp_path / ".agentpack" / "selection_diagnosis.md").read_text(encoding="utf-8")
    assert "## Why Selected" in report
    assert "## Why Not Selected" in report


def test_ignore_suggest_and_apply(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "bundle.js").write_text("x = 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["ignore", "suggest", "--json"])
    assert result.exit_code == 0, result.output
    suggestions = json.loads(result.output)["suggestions"]
    assert {"pattern": "dist/", "reason": "generated/cache directory present in repo scan"} in suggestions

    result = CliRunner().invoke(app, ["ignore", "apply"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".agentignore").exists()

    result = CliRunner().invoke(app, ["ignore", "apply", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    assert "dist/" in (tmp_path / ".agentignore").read_text(encoding="utf-8")
