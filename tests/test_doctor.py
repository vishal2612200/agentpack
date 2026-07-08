from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.commands.doctor import (
    _agentignore_sync_findings,
    _latest_context_path,
    _publish_secret_findings,
    _release_hygiene_findings,
    _safe_fix,
    _source_checkout_warning,
    _thread_conflict_findings,
)
from agentpack.core.command_surface import installed_cli_status, refresh_commands
from agentpack.core.mcp_runtime import McpRuntimeCheck
from agentpack.core.thread_context import append_thread_index, build_thread_index_row
from agentpack.integrations.agents import check_agent_integration


def test_source_checkout_warning_when_importing_installed_package(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src" / "agentpack").mkdir(parents=True)
    package_file = Path("/site-packages/agentpack/commands/doctor.py")

    warning = _source_checkout_warning(root, package_file, "/python", "/bin/agentpack")

    assert warning is not None
    assert "source checkout detected" in warning
    assert "pip install -e ." in warning


def test_source_checkout_warning_skips_local_source_import(tmp_path: Path) -> None:
    root = tmp_path
    source_pkg = root / "src" / "agentpack"
    source_pkg.mkdir(parents=True)
    package_file = source_pkg / "commands" / "doctor.py"

    assert _source_checkout_warning(root, package_file, "/python", "/bin/agentpack") is None


def test_release_hygiene_flags_generated_artifacts(tmp_path: Path, monkeypatch) -> None:
    class Result:
        returncode = 0
        stdout = "?? .agentpack/context.md\n M src/agentpack/commands/pack.py\n?? .coverage\n"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: Result())

    findings = _release_hygiene_findings(tmp_path)

    assert findings
    assert ".agentpack/context.md" in findings[0]
    assert ".coverage" in findings[0]
    assert "pack.py" not in findings[0]


def test_doctor_release_hygiene_warning_does_not_fail_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "generated.md").write_text("generated\n", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        '{"hooks":{"SessionStart":[{"hooks":[{"command":"agentpack hook --event SessionStart"}]}]},'
        '"mcpServers":{"agentpack":{"command":"agentpack","args":["mcp"]}}}',
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"agentpack":{"command":"agentpack","args":["mcp"]}}}',
        encoding="utf-8",
    )
    (tmp_path / ".claude" / "commands").mkdir()
    (tmp_path / ".claude" / "commands" / "agentpack.md").write_text("agentpack\n", encoding="utf-8")
    (tmp_path / ".claude" / "commands" / "agentpack-review.md").write_text("agentpack review\n", encoding="utf-8")
    (tmp_path / ".claude" / "commands" / "agentpack-audit.md").write_text("agentpack audit\n", encoding="utf-8")
    (tmp_path / ".claude" / "commands" / "agentpack-learn.md").write_text("agentpack learn\n", encoding="utf-8")

    monkeypatch.setattr("agentpack.commands.doctor.installed_cli_status", lambda: {
        "agentpack_version": "0.0.0",
        "binary": "/bin/agentpack",
        "importable_commands": ["pack"],
        "help_commands": ["pack"],
    })
    monkeypatch.setattr("agentpack.commands.doctor.available_cli_commands", lambda: ("pack",))
    monkeypatch.setattr("agentpack.commands.doctor.shutil.which", lambda name: "/bin/agentpack" if name == "agentpack" else None)
    monkeypatch.setattr("agentpack.commands.doctor._source_checkout_warning", lambda *args, **kwargs: None)
    monkeypatch.setattr("agentpack.commands.doctor._GIT_TEMPLATE_DIR", tmp_path / "templates")
    hooks = tmp_path / "templates" / "hooks"
    hooks.mkdir(parents=True)
    for name in ("post-checkout", "post-commit", "post-merge"):
        (hooks / name).write_text("# agentpack:global\n", encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.doctor._detect_rc_file", lambda: tmp_path / ".zshrc")
    (tmp_path / ".zshrc").write_text("# agentpack:chpwd:start\n", encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.doctor.subprocess.run", _doctor_successful_subprocess(tmp_path))
    monkeypatch.setattr("agentpack.commands.doctor._agentignore_sync_findings", lambda root: ["synced: .agentignore present"])
    monkeypatch.setattr("agentpack.commands.doctor._thread_conflict_findings", lambda root: [])
    monkeypatch.setattr("agentpack.commands.doctor._publish_secret_findings", lambda root: [])
    monkeypatch.setattr(
        "agentpack.commands.doctor.check_mcp_runtime",
        lambda root: McpRuntimeCheck(status="stdio_waiting", ok=True, detail="waiting"),
    )

    result = CliRunner().invoke(app, ["doctor", "--agent", "generic"])

    assert result.exit_code == 0, result.output
    assert "MCP registration" in result.output
    assert "MCP runtime" in result.output
    assert "agentpack mcp starts and waits for stdio" in result.output
    assert "Live host exposure" in result.output
    assert "cannot be proven from CLI" in result.output
    assert "generated/local artifacts present" in result.output
    assert "agentpack-review.md" in result.output
    assert "agentpack-audit.md" in result.output
    assert "agentpack-learn.md" in result.output
    assert "warning only" in result.output
    assert "All checks passed" in result.output


def test_publish_secret_findings_warns_when_npm_token_missing(tmp_path: Path) -> None:
    (tmp_path / "npm").mkdir()
    (tmp_path / "npm" / "package.json").write_text("{}", encoding="utf-8")

    findings = _publish_secret_findings(tmp_path, env={})

    assert findings
    assert "NPM_TOKEN" in findings[0]


def test_publish_secret_findings_accepts_npm_token(tmp_path: Path) -> None:
    (tmp_path / "npm").mkdir()
    (tmp_path / "npm" / "package.json").write_text("{}", encoding="utf-8")

    assert _publish_secret_findings(tmp_path, env={"NPM_TOKEN": "secret"}) == []


def test_latest_context_path_uses_metadata_path(tmp_path: Path) -> None:
    agentpack_dir = tmp_path / ".agentpack"
    agentpack_dir.mkdir()
    (agentpack_dir / "context.md").write_text("fresh", encoding="utf-8")
    (agentpack_dir / "context.claude.md").write_text("old", encoding="utf-8")
    (agentpack_dir / "pack_metadata.json").write_text(
        '{"context_path": ".agentpack/context.md"}',
        encoding="utf-8",
    )

    assert _latest_context_path(tmp_path) == agentpack_dir / "context.md"


def test_agentignore_sync_findings_warn_when_import_block_stale(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("backend/.serverless/\n", encoding="utf-8")
    (tmp_path / ".agentignore").write_text("custom/\n", encoding="utf-8")

    findings = _agentignore_sync_findings(tmp_path)

    assert findings == ["imported .agentignore rules are stale; run `agentpack ignore sync`."]


def test_agentignore_sync_findings_report_synced_imports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("agentpack.core.ignore._git_config_excludesfile", lambda: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".gitignore").write_text("backend/.serverless/\n", encoding="utf-8")
    status = tmp_path / ".agentignore"
    status.write_text(
        "custom/\n\n"
        "# agentpack:imported-gitignore:start\n"
        "# Imported from git ignore sources because these look like generated/noisy paths\n"
        "backend/.serverless/\n"
        "# agentpack:imported-gitignore:end\n",
        encoding="utf-8",
    )

    findings = _agentignore_sync_findings(tmp_path)

    assert findings
    assert findings[0].startswith("synced: Imported 1 generated/noisy rules")


def test_doctor_safe_fix_syncs_agentignore(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("agentpack.core.ignore._git_config_excludesfile", lambda: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".gitignore").write_text("backend/.serverless/\n", encoding="utf-8")

    _safe_fix(tmp_path, "generic")

    content = (tmp_path / ".agentignore").read_text(encoding="utf-8")
    assert "backend/.serverless/" in content


def test_doctor_safe_fix_all_repairs_every_local_agent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    import subprocess

    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)

    _safe_fix(tmp_path, "all")

    for agent in ("claude", "cursor", "windsurf", "codex", "antigravity", "generic"):
        assert all(check.ok for check in check_agent_integration(tmp_path, agent)), agent


def test_doctor_thread_conflict_findings_report_overlap(tmp_path: Path) -> None:
    current = build_thread_index_row(
        root=tmp_path,
        thread_id="thread-a",
        task="fix auth",
        branch="main",
        selected_files=["src/auth.py"],
        dirty_files=[],
        status="in_progress",
    )
    other = build_thread_index_row(
        root=tmp_path,
        thread_id="thread-b",
        task="fix auth tests",
        branch="main",
        selected_files=["src/auth.py", "tests/test_auth.py"],
        dirty_files=[],
        status="in_progress",
    )
    stamp = datetime.now(timezone.utc).isoformat()
    current["updated_at"] = stamp
    other["updated_at"] = stamp
    append_thread_index(tmp_path, current)
    append_thread_index(tmp_path, other)

    findings = _thread_conflict_findings(tmp_path)

    assert findings
    assert "thread-a overlaps thread-b" in findings[0] or "thread-b overlaps thread-a" in findings[0]


def test_command_surface_status_reports_repair_command() -> None:
    status = installed_cli_status()

    assert status["agentpack_version"]
    assert "importable_commands" in status
    assert status["repair_command"]
    assert refresh_commands("auto").primary.startswith("agentpack ")


def _doctor_successful_subprocess(root: Path):
    def fake_run(args, *pargs, **kwargs):
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        result = Result()
        if args[:3] == ["git", "config", "--global"]:
            result.stdout = str(root / "templates")
        elif args[:2] == ["git", "status"]:
            result.stdout = "?? .agent/generated.md\n"
        elif args == ["agentpack", "--version"]:
            result.stdout = "0.0.0\n"
        return result

    return fake_run
