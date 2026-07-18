from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.commands.quickstart import _quickstart_state, _shell_single_quote


def test_shell_single_quote_escapes_quotes() -> None:
    assert _shell_single_quote("fix user's auth") == "'fix user'\"'\"'s auth'"


def test_quickstart_state_for_new_repo(tmp_path: Path) -> None:
    state = _quickstart_state(tmp_path, "fix auth token expiry", "balanced")

    steps = state["steps"]
    assert ("first", "agentpack init --yes --mode balanced", "create config, cache dir, session, and task file") in steps
    assert any("agentpack start 'fix auth token expiry'" in cmd for _, cmd, _ in steps)
    assert any(cmd == "agentpack doctor --agent auto" for _, cmd, _ in steps)
    assert any("agentpack benchmark --init" in cmd for cmd, _ in state["optional"])


def test_quickstart_state_detects_existing_task(tmp_path: Path) -> None:
    task_path = tmp_path / ".agentpack" / "task.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text("fix payment retry\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")

    state = _quickstart_state(tmp_path, "", "balanced")

    assert "Repo has setup" in state["summary"]
    assert any("Current task: fix payment retry" in note for note in state["notes"])
    assert any(cmd == "agentpack next --fix" for _, cmd, _ in state["steps"])


def test_quickstart_write_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["quickstart", "--task", "fix cache bug", "--write"])

    assert result.exit_code == 0
    assert (tmp_path / ".agentpack" / "task.md").read_text(encoding="utf-8") == "fix cache bug\n"
    assert "Saved task: fix cache bug" in result.output
    assert "Optional later" in result.output


def test_quickstart_write_task_uses_session_thread(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTPACK_THREAD_ID", "codex-local")

    result = CliRunner().invoke(app, ["quickstart", "--task", "fix cache bug", "--write"])

    assert result.exit_code == 0
    assert (tmp_path / ".agentpack" / "threads" / "codex-local" / "task.md").read_text(encoding="utf-8") == "fix cache bug\n"
    assert not (tmp_path / ".agentpack" / "task.md").exists()
    assert "Using AgentPack session: codex-local" in result.output
