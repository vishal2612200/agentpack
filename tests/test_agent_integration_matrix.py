from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.mcp_runtime import McpRuntimeCheck
from agentpack.integrations.agents import check_agent_integration

AGENTS = ("claude", "cursor", "windsurf", "codex", "antigravity", "generic")
EXPECTED_FILES = {
    "claude": (
        "CLAUDE.md",
        ".claude/settings.json",
        ".mcp.json",
        ".claude/commands/agentpack.md",
        ".claude/commands/agentpack-review.md",
        ".claude/commands/agentpack-audit.md",
        ".claude/commands/agentpack-learn.md",
    ),
    "cursor": (".cursorrules", ".cursor/rules/agentpack.mdc", ".vscode/tasks.json"),
    "windsurf": (".windsurfrules", ".vscode/tasks.json"),
    "codex": ("AGENTS.md", ".codex/hooks.json"),
    "antigravity": ("GEMINI.md", ".vscode/tasks.json"),
    "generic": (),
}
GIT_AGENTS = {"cursor", "windsurf", "codex", "antigravity"}


@pytest.mark.parametrize("command", ["init", "install"])
@pytest.mark.parametrize("agent", AGENTS)
def test_cli_agent_matrix_is_complete_and_idempotent(tmp_path, monkeypatch, command, agent) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    runner = CliRunner()

    args = [command, "--agent", agent]
    if command == "init":
        args.append("--yes")
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    _assert_agent_ready(tmp_path, agent)


def test_repair_all_installs_every_agent_integration(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    runner = CliRunner()

    result = runner.invoke(app, ["repair", "--agent", "all"])

    assert result.exit_code == 0, result.output
    for agent in AGENTS:
        _assert_agent_ready(tmp_path, agent, strict_git=False)


def test_repair_prints_mcp_missing_extra_remediation(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agentpack.commands.install.check_mcp_runtime",
        lambda root: McpRuntimeCheck(
            status="missing_extra",
            ok=False,
            detail="missing",
            remediation=('pipx inject agentpack-cli "agentpack-cli[mcp]"',),
        ),
    )

    result = CliRunner().invoke(app, ["repair", "--agent", "claude"])

    assert result.exit_code == 0, result.output
    assert "MCP runtime: missing MCP extra" in result.output
    assert 'pipx inject agentpack-cli "agentpack-cli[mcp]"' in result.output


@pytest.mark.parametrize(
    ("agent", "rel", "marker"),
    [
        ("claude", "CLAUDE.md", "Prefer MCP"),
        ("cursor", ".cursorrules", "MCP is the active path"),
        ("windsurf", ".windsurfrules", "MCP is the active path"),
        ("codex", "AGENTS.md", "MCP is the active path"),
        ("antigravity", "GEMINI.md", "MCP is the active path"),
    ],
)
def test_repair_updates_stale_agent_rule_blocks(tmp_path, monkeypatch, agent, rel, marker) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!-- agentpack:start -->\n"
        "Old AgentPack instructions: run agentpack pack --task auto and read context.md\n"
        "<!-- agentpack:end -->\n",
        encoding="utf-8",
    )
    stale_checks = check_agent_integration(tmp_path, agent)
    assert any(not check.ok and "stale AgentPack" in check.detail for check in stale_checks)

    result = CliRunner().invoke(app, ["repair", "--agent", agent])

    assert result.exit_code == 0, result.output
    content = path.read_text(encoding="utf-8")
    assert marker in content
    assert "TOON" in content
    if agent != "claude":
        assert "agentpack:freshness" in content
    assert all(check.ok for check in check_agent_integration(tmp_path, agent))


def test_doctor_agent_all_reports_missing_integrations(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    runner = CliRunner()

    result = runner.invoke(app, ["doctor", "--agent", "all"])

    assert "Agent integration audit" in result.output
    assert "codex" in result.output
    assert ".codex/hooks.json" in result.output


def test_status_deep_prints_agent_health(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    runner = CliRunner()
    runner.invoke(app, ["init", "--yes", "--agent", "codex"])

    result = runner.invoke(app, ["status", "--deep"])

    assert result.exit_code != 0
    assert "Deep health" in result.output
    assert "Active agent" in result.output

def test_codex_install_disables_stale_marketplace_plugin(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        '[plugins."agentpack@awesome-codex-plugins"]\n'
        "enabled = true\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["install", "--agent", "codex"])

    assert result.exit_code == 0, result.output
    codex_config = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert '[plugins."agentpack@local"]' in codex_config
    assert '[plugins."agentpack@awesome-codex-plugins"]' in codex_config
    assert 'enabled = false' in codex_config
    assert all(check.ok for check in check_agent_integration(tmp_path, "codex"))


def _assert_agent_ready(root: Path, agent: str, *, strict_git: bool = True) -> None:
    for rel in EXPECTED_FILES[agent]:
        assert (root / rel).exists(), f"{agent} missing {rel}"

    if agent == "claude":
        settings = json.loads((root / ".claude/settings.json").read_text(encoding="utf-8"))
        text = json.dumps(settings)
        assert text.count("agentpack hook --event SessionStart") == 1
        assert text.count("agentpack hook --event UserPromptSubmit") == 1
        mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        assert mcp["mcpServers"]["agentpack"] == {"command": "agentpack", "args": ["mcp"]}

    if agent == "codex":
        hooks = json.loads((root / ".codex/hooks.json").read_text(encoding="utf-8"))
        text = json.dumps(hooks)
        assert text.count("agentpack hook --event SessionStart") == 1
        assert text.count("agentpack hook --event UserPromptSubmit") == 1
        codex_config = (root / "codex-home" / "config.toml").read_text(encoding="utf-8")
        assert '[plugins."agentpack@local"]' in codex_config
        assert "enabled = true" in codex_config
        assert "[mcp_servers.agentpack]" in codex_config
        assert 'args = ["mcp"]' in codex_config

    if strict_git:
        for event in ("post-commit", "post-merge", "post-checkout"):
            hook = root / ".git" / "hooks" / event
            has_marker = hook.exists() and "agentpack:auto-repack" in hook.read_text(encoding="utf-8")
            assert has_marker is (agent in GIT_AGENTS), f"{agent} git hook {event} marker mismatch"

    checks = check_agent_integration(root, agent)
    assert all(check.ok for check in checks), [check for check in checks if not check.ok]
