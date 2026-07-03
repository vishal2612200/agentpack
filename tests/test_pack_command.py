from __future__ import annotations

import subprocess

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.thread_context import append_thread_index, build_thread_index_row


def test_pack_accepts_explicit_task_text(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / "auth.py").write_text("def check_auth(): return True\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["pack", "--agent", "generic", "--task", "fix auth bug"])

    assert result.exit_code == 0, result.output
    assert "Task from --task: fix auth bug" in result.output
    assert (tmp_path / ".agentpack" / "task.md").read_text(encoding="utf-8") == "fix auth bug\n"
    assert (tmp_path / ".agentpack" / "context.md").exists()


def test_pack_help_directs_tasks_to_task_md() -> None:
    result = CliRunner().invoke(app, ["pack", "--help"])

    assert result.exit_code == 0
    assert "Task text to pack" in result.output
    assert ".agentpack/task.md" in result.output


def test_pack_auto_repairs_stale_agent_rule_block(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Pack should self-heal stale codex rule\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "<!-- agentpack:start -->\n"
        "Old AgentPack instructions: run agentpack pack --task auto and read context.md\n"
        "<!-- agentpack:end -->\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["pack", "--agent", "codex"])

    assert result.exit_code == 0, result.output
    assert "Auto-repaired stale AgentPack integration for codex" in result.output
    assert "agentpack guard --agent codex --repair-stale --refresh-context" in (
        tmp_path / "AGENTS.md"
    ).read_text(encoding="utf-8")


def test_pack_plain_uses_ambient_thread_env_and_refuses_global_task(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-env")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix auth bug\n", encoding="utf-8")
    (tmp_path / "auth.py").write_text("def check_auth(): return True\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["pack", "--agent", "generic"])

    assert result.exit_code == 1
    assert "No task is set for AgentPack session codex-env" in result.output
    assert "Fix auth bug" not in result.output
    assert not (tmp_path / ".agentpack" / "threads" / "codex-env" / "context.md").exists()


def test_pack_global_opt_out_uses_legacy_task(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-env")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix auth bug\n", encoding="utf-8")
    (tmp_path / "auth.py").write_text("def check_auth(): return True\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["pack", "--agent", "generic", "--thread", "global"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".agentpack" / "context.md").exists()
    assert not (tmp_path / ".agentpack" / "threads" / "codex-env" / "context.md").exists()


def test_pack_accepts_lite_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Fix auth bug\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "config.toml").write_text("[context_lite]\nbudget = 1200\n", encoding="utf-8")
    (tmp_path / "auth.py").write_text("def check_auth(): return True\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["pack", "--agent", "generic", "--mode", "lite"])

    assert result.exit_code == 0, result.output
    assert "Context Pack Ready" in result.output
    assert (tmp_path / ".agentpack" / "context.md").exists()


def test_pack_thread_auto_uses_agentpack_thread_env(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTPACK_THREAD_ID", "codex/env")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "threads" / "codex-env").mkdir(parents=True)
    (tmp_path / ".agentpack" / "threads" / "codex-env" / "task.md").write_text("Fix auth bug\n", encoding="utf-8")
    (tmp_path / "auth.py").write_text("def check_auth(): return True\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["pack", "--agent", "generic", "--thread", "auto"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".agentpack" / "threads" / "codex-env" / "context.md").exists()


def test_pack_thread_conflict_prints_terminal_warning(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack" / "threads" / "thread-a").mkdir(parents=True)
    (tmp_path / ".agentpack" / "threads" / "thread-a" / "task.md").write_text("Fix auth bug\n", encoding="utf-8")
    (tmp_path / "auth.py").write_text("def check_auth(): return True\n", encoding="utf-8")
    append_thread_index(
        tmp_path,
        build_thread_index_row(
            root=tmp_path,
            thread_id="thread-b",
            task="Refactor auth",
            branch="",
            selected_files=["auth.py"],
            dirty_files=[],
            status="in_progress",
        ),
    )

    result = CliRunner().invoke(app, ["pack", "--agent", "generic", "--thread", "thread-a"])

    assert result.exit_code == 0, result.output
    assert "Concurrent context warning" in result.output
    assert "thread-b" in result.output
