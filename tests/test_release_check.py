from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app


def test_release_check_json_orchestrates_stages(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] — 2026-05-26\n", encoding="utf-8")

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return Result()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--skip-benchmark", "--skip-build", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert [stage["name"] for stage in payload["stages"]] == [
        "changelog",
        "version-sync",
        "pytest-plugin-deps",
        "ruff",
        "mypy",
        "pytest",
        "npm-launcher-tests",
    ]
    assert calls[0] == ["node", "npm/test/version-sync.test.js"]
    pytest_call = next(call for call in calls if "pytest" in call)
    assert pytest_call == ["python", "-m", "pytest", "tests/", "-q", "--cov", "--cov-report=term-missing", "-m", "not slow"] or pytest_call[1:] == ["-m", "pytest", "tests/", "-q", "--cov", "--cov-report=term-missing", "-m", "not slow"]


def test_release_check_allows_explicit_tagged_evidence_bypass(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "agentpack-cli"\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3]\n", encoding="utf-8")
    init_file = tmp_path / "src" / "agentpack" / "__init__.py"
    init_file.parent.mkdir(parents=True)
    init_file.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    (npm_dir / "package.json").write_text('{"version": "1.2.3"}', encoding="utf-8")

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return Result()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(
        app,
        [
            "release-check",
            "--tag",
            "v1.2.3",
            "--allow-release-evidence-bypass",
            "--release-evidence-bypass-reason",
            "emergency security fix",
            "--skip-build",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    evidence = next(stage for stage in payload["stages"] if stage["name"] == "release-evidence")
    assert "EXPLICIT BYPASS" in evidence["detail"]
    assert not any("benchmark-release-gate" in call for call in calls)


def test_release_check_requires_reason_for_evidence_bypass(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "agentpack-cli"\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")
    init_file = tmp_path / "src" / "agentpack" / "__init__.py"
    init_file.parent.mkdir(parents=True)
    init_file.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    (npm_dir / "package.json").write_text('{"version": "1.2.3"}', encoding="utf-8")
    monkeypatch.setattr(
        "agentpack.commands.release_check.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )

    result = CliRunner().invoke(
        app,
        ["release-check", "--tag", "v1.2.3", "--allow-release-evidence-bypass", "--skip-build", "--json"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    evidence = next(stage for stage in payload["stages"] if stage["name"] == "release-evidence")
    assert "requires --release-evidence-bypass-reason" in evidence["detail"]


def test_release_check_docs_profile_skips_build_benchmark_and_uses_focused_tests(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] — 2026-05-26\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for name in ("test_docs_links.py", "test_codex_plugin.py", "test_native_integrations.py"):
        (tests_dir / name).write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return Result()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--profile", "docs", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "docs"
    assert [stage["name"] for stage in payload["stages"]] == [
        "changelog",
        "version-sync",
        "pytest-plugin-deps",
        "ruff",
        "mypy",
        "pytest",
    ]
    assert not any("benchmark" in " ".join(call) for call in calls)
    assert not any("build" in call for call in calls)
    pytest_call = next(call for call in calls if "pytest" in call)
    assert "tests/test_docs_links.py" in pytest_call
    assert "tests/test_codex_plugin.py" in pytest_call
    assert "tests/test_native_integrations.py" in pytest_call
    assert "--cov" not in pytest_call


def test_release_check_ci_profile_skips_build_benchmark_and_coverage(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] — 2026-05-26\n", encoding="utf-8")

    calls: list[tuple[list[str], dict[str, object]]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(([str(part) for part in command], kwargs))
        return Result()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--profile", "ci", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "ci"
    assert [stage["name"] for stage in payload["stages"]] == [
        "changelog",
        "version-sync",
        "pytest-plugin-deps",
        "ruff",
        "mypy",
        "pytest",
        "npm-launcher-tests",
    ]
    assert "build" not in {stage["name"] for stage in payload["stages"]}
    assert "benchmark-release-gate" not in {stage["name"] for stage in payload["stages"]}
    pytest_call, _pytest_kwargs = next((call, kwargs) for call, kwargs in calls if "pytest" in call)
    assert pytest_call == ["python", "-m", "pytest", "tests/", "-q", "-m", "not slow"] or pytest_call[1:] == ["-m", "pytest", "tests/", "-q", "-m", "not slow"]


def test_release_check_auto_uses_docs_profile_for_docs_plugin_only_diff(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] — 2026-05-26\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_docs_links.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str = ""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        parts = [str(part) for part in command]
        if parts[:4] == ["git", "diff", "--name-only", "HEAD"]:
            return Result("README.md\n.codex-plugin/plugin.json\ndocs/codex-plugin.md\n")
        if parts[:3] == ["git", "ls-files", "--others"]:
            return Result("skills/agentpack.md\n")
        return Result()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "docs"
    assert "benchmark-release-gate" not in {stage["name"] for stage in payload["stages"]}
    assert "build" not in {stage["name"] for stage in payload["stages"]}


def test_release_check_auto_uses_github_push_diff_for_docs_only_ci(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] — 2026-05-26\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_docs_links.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    event = tmp_path / "event.json"
    event.write_text('{"before": "abc123", "after": "def456"}', encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str = ""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        parts = [str(part) for part in command]
        if parts[:3] == ["git", "diff", "--name-only"]:
            return Result("README.md\ndocs/commands.md\n.codex-plugin/plugin.json\n")
        return Result()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "docs"
    assert "benchmark-release-gate" not in {stage["name"] for stage in payload["stages"]}
    assert "build" not in {stage["name"] for stage in payload["stages"]}


def test_release_check_auto_keeps_full_profile_for_source_diff(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] — 2026-05-26\n", encoding="utf-8")

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str = ""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        parts = [str(part) for part in command]
        if parts[:4] == ["git", "diff", "--name-only", "HEAD"]:
            return Result("src/agentpack/commands/release_check.py\n")
        if parts[:3] == ["git", "ls-files", "--others"]:
            return Result()
        return Result()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "full"
    assert "benchmark-release-gate" in {stage["name"] for stage in payload["stages"]}
    assert "build" in {stage["name"] for stage in payload["stages"]}


def test_release_check_fails_missing_changelog_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    result = CliRunner().invoke(app, ["release-check", "--skip-benchmark", "--skip-build"])

    assert result.exit_code == 1
    assert "Missing CHANGELOG.md entry for 1.2.3" in result.output


def test_release_check_tag_version_must_match_packages(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "agentpack-cli"\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")
    init_file = tmp_path / "src" / "agentpack" / "__init__.py"
    init_file.parent.mkdir(parents=True)
    init_file.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    (npm_dir / "package.json").write_text('{"version": "1.2.3"}', encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    result = CliRunner().invoke(app, ["release-check", "--tag", "v1.2.4", "--skip-benchmark", "--skip-build", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    failed = {stage["name"]: stage for stage in payload["stages"] if stage["status"] == "failed"}
    assert failed["tag-version"]["detail"] == "tag=1.2.4 pyproject=1.2.3 __init__=1.2.3 npm=1.2.3"


def test_release_check_build_uses_temp_outdir(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")
    build_commands: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        parts = [str(part) for part in command]
        if "-m" in parts and "build" in parts:
            build_commands.append(parts)
        return Result()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--skip-benchmark", "--json"])

    assert result.exit_code == 0
    assert build_commands
    assert "--outdir" in build_commands[0]
    assert Path(build_commands[0][build_commands[0].index("--outdir") + 1]).name.startswith("agentpack-build-")


def test_release_check_prints_failed_stage_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        if "-m" in [str(part) for part in command] and "pytest" in [str(part) for part in command]:
            return type("Result", (), {"returncode": 1, "stdout": "FAILED tests/test_x.py::test_name\n", "stderr": ""})()
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--skip-benchmark", "--skip-build"])

    assert result.exit_code == 1
    assert "FAILED tests/test_x.py::test_name" in result.output


def test_release_check_fails_pytest_plugin_config_without_dev_dependency(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'version = "1.2.3"',
                "",
                "[project.optional-dependencies]",
                'dev = ["pytest", "ruff"]',
                "",
                "[tool.pytest.ini_options]",
                'asyncio_default_fixture_loop_scope = "function"',
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    result = CliRunner().invoke(app, ["release-check", "--skip-benchmark", "--skip-build"])

    assert result.exit_code == 1
    assert "pytest-asyncio options" in result.output


def test_release_check_release_branch_guard_requires_origin_release_branch(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        if [str(part) for part in command] == ["git", "branch", "-r", "--contains", "HEAD"]:
            return type("Result", (), {"returncode": 0, "stdout": "  origin/main\n", "stderr": ""})()
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["release-check", "--check-release-branch", "--skip-benchmark", "--skip-build"])

    assert result.exit_code == 1
    assert "origin/release/*" in result.output


def test_release_check_registry_guard_fails_existing_versions(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "agentpack-cli"\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    (npm_dir / "package.json").write_text('{"name": "@example/agentpack", "version": "1.2.3"}', encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.release_check.subprocess.run", lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr("agentpack.commands.release_check._registry_url_exists", lambda _url: (True, "already published"))

    result = CliRunner().invoke(app, ["release-check", "--check-registry", "--skip-benchmark", "--skip-build", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    failed = {stage["name"]: stage for stage in payload["stages"] if stage["status"] == "failed"}
    assert failed["pypi-version-available"]["detail"] == "already published"
    assert failed["npm-version-available"]["detail"] == "already published"
