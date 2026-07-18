from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app


def test_dev_check_json_orchestrates_stages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return Result()

    monkeypatch.setattr("agentpack.commands.dev_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["dev-check", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert [stage["name"] for stage in payload["stages"]] == ["docs-check", "ruff", "mypy", "pytest", "npm-version-sync", "npm-launcher"]
    assert any(call[1:] == ["-m", "pytest", "-q", "-m", "not slow"] for call in calls)
    assert calls


def test_dev_check_prints_failed_stage_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_run(command, **kwargs):
        if "-m" in [str(part) for part in command] and "pytest" in [str(part) for part in command]:
            return type("Result", (), {"returncode": 1, "stdout": "FAILED tests/test_x.py::test_name\n", "stderr": ""})()
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("agentpack.commands.dev_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["dev-check"])

    assert result.exit_code == 1
    assert "FAILED tests/test_x.py::test_name" in result.output


def test_verify_wheel_json_uses_existing_wheel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    wheel = tmp_path / "dist" / "agentpack_cli-1.0.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_text("wheel", encoding="utf-8")

    monkeypatch.setattr(
        "agentpack.commands.verify_wheel._run",
        lambda _root, name, command: {"name": name, "command": " ".join(command), "returncode": 0, "detail": "ok"},
    )

    result = CliRunner().invoke(app, ["verify-wheel", "--skip-build", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["wheel"].endswith(".whl")


def test_release_prepare_json_orchestrates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [1.2.3] — 2026-05-26\n\n### Added\n- Added release notes.\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return Result()

    monkeypatch.setattr("agentpack.commands.release_cmd.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agentpack.commands.release_cmd.run_verify_wheel",
        lambda: {"passed": True, "stages": [{"name": "build", "command": "python -m build", "returncode": 0, "detail": ""}]},
    )

    result = CliRunner().invoke(app, ["release", "prepare", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert [stage["name"] for stage in payload["stages"]] == [
        "release-check",
        "benchmark-public-table",
        "verify-wheel:build",
        "github-release-notes",
    ]
    notes_path = Path(payload["release_notes"])
    assert notes_path.name == "github-release-notes-v1.2.3.md"
    notes_text = notes_path.read_text(encoding="utf-8")
    assert "AgentPack v1.2.3" in notes_text
    assert "Added release notes." in notes_text
    assert "gh release create v1.2.3" in notes_text
    assert "--check-release-branch" in calls[0]
    assert "--check-registry" in calls[0]
    assert "--tag" in calls[0]
    assert "v1.2.3" in calls[0]


def test_release_check_json_emits_stable_top_level_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.0.1"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [0.0.1] — 2026-01-01\n\n### Added\n- init.\n", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", lambda *a, **kw: Result())

    result = CliRunner().invoke(app, ["release-check", "--skip-benchmark", "--skip-build", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) >= {"passed", "profile", "stages"}
    assert isinstance(data["passed"], bool)
    assert isinstance(data["stages"], list)
    assert all({"name", "status"} <= set(stage) for stage in data["stages"])


def test_ci_init_json_emits_stable_top_level_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["ci", "init", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) >= {"path", "written", "overwritten"}
    assert data["written"] is True
    assert data["overwritten"] is False
    assert data["path"].endswith("agentpack.yml")
