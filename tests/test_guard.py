from __future__ import annotations

import re
import subprocess

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.integrations.agents import check_agent_integration


def _normalized_output(output: str) -> str:
    return re.sub(r"\s+", " ", output)


def test_guard_fails_without_context_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["guard", "--agent", "generic"])

    assert result.exit_code == 1
    assert "Context pack unsafe" in result.output
    assert "agentpack guard --repair-stale --refresh-context" in result.output
    assert "What failed: missing context pack metadata" in result.output
    assert "Safe to continue: no; refresh or use direct rg/git evidence" in result.output


def test_guard_refreshes_missing_context_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix guard freshness gap\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--refresh-context"])

    assert result.exit_code == 0, result.output
    assert "Refreshing context" in result.output
    assert "Context pack fresh" in result.output
    assert (tmp_path / ".agentpack" / "context.md").exists()


def test_guard_blocks_refresh_when_tracked_tree_is_dirty(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix guard git preflight\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--refresh-context"])

    assert result.exit_code == 1
    assert "tracked local changes present" in result.output
    assert "Safe to continue: no; resolve git state" in result.output
    assert not (tmp_path / ".agentpack" / "context.md").exists()


def test_guard_refreshes_dirty_tree_when_targets_are_confirmed(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix guard git preflight\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--refresh-context", "--allow-dirty-targets"])

    assert result.exit_code == 0, result.output
    output = _normalized_output(result.output)
    assert "Dirty tracked files confirmed by --allow-dirty-targets" in output
    assert "context refresh may proceed" in output
    assert "without git sync" in output
    assert "Refreshing context" in result.output
    assert "Context pack fresh" in result.output
    assert (tmp_path / ".agentpack" / "context.md").exists()


def test_guard_explains_dirty_target_confirmation_without_refresh(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix guard git preflight\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    refresh_result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--refresh-context", "--allow-dirty-targets"])
    assert refresh_result.exit_code == 0, refresh_result.output

    result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--allow-dirty-targets"])

    assert result.exit_code == 0, result.output
    output = _normalized_output(result.output).lower()
    assert "git preflight will not block" in output
    assert "pass --refresh-context to refresh stale context" in output
    assert "Refreshing context" not in result.output


def test_guard_plain_uses_global_task_even_with_ambient_thread_env(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-env")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix guard env behavior\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")

    guard_result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--refresh-context"])

    assert guard_result.exit_code == 0, guard_result.output
    assert "Context pack fresh" in guard_result.output
    assert (tmp_path / ".agentpack" / "context.md").exists()


def test_guard_thread_auto_refuses_global_task(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-env")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix guard env behavior\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")

    guard_result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--thread", "auto"])

    assert guard_result.exit_code == 1
    assert "missing task for AgentPack session codex-env" in guard_result.output


def test_guard_global_opt_out_uses_legacy_context(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-env")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix guard env behavior\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")

    pack_result = CliRunner().invoke(app, ["pack", "--agent", "generic", "--thread", "global"])
    guard_result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--thread", "global"])

    assert pack_result.exit_code == 0, pack_result.output
    assert guard_result.exit_code == 0, guard_result.output
    assert "Context pack fresh" in guard_result.output


def test_guard_ignores_generated_antigravity_citation_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix guard generated citation handling\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")

    pack_result = CliRunner().invoke(app, ["guard", "--agent", "generic", "--refresh-context"])
    assert pack_result.exit_code == 0, pack_result.output

    citation_path = tmp_path / ".agent" / "skills" / "agentpack" / "citations.json"
    citation_path.parent.mkdir(parents=True)
    citation_path.write_text("{}\n", encoding="utf-8")

    guard_result = CliRunner().invoke(app, ["guard", "--agent", "generic"])

    assert guard_result.exit_code == 0, guard_result.output
    assert "Context pack fresh" in guard_result.output


def test_guard_repairs_stale_agent_integration(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Repair stale codex integration\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "<!-- agentpack:start -->\n"
        "Old AgentPack instructions: run agentpack pack --task auto and read context.md\n"
        "<!-- agentpack:end -->\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["guard", "--agent", "codex", "--repair-stale", "--refresh-context"],
    )

    assert result.exit_code == 0, result.output
    assert "Repairing AgentPack integration: codex" in result.output
    assert all(check.ok for check in check_agent_integration(tmp_path, "codex"))
    assert "agentpack guard --agent codex --repair-stale --refresh-context" in (
        tmp_path / "AGENTS.md"
    ).read_text(encoding="utf-8")
