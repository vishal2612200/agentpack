from __future__ import annotations

from pathlib import Path
import shlex

import pytest

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.commands.quickstart import _quickstart_state, _shell_single_quote


def test_shell_single_quote_escapes_quotes() -> None:
    assert _shell_single_quote("fix user's auth") == "'fix user'\"'\"'s auth'"


def test_quickstart_state_for_new_repo(tmp_path: Path) -> None:
    state = _quickstart_state(tmp_path, "fix auth token expiry", "balanced")

    steps = state["steps"]
    assert len([step for step in steps if step[0] != "verify"]) == 1
    assert any("agentpack work 'fix auth token expiry' --mode balanced" in cmd for _, cmd, _ in steps)
    assert not (tmp_path / ".agentpack").exists()
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


@pytest.mark.parametrize("initialized", [False, True])
def test_quickstart_preserves_task_mode_and_thread(tmp_path: Path, initialized: bool) -> None:
    if initialized:
        (tmp_path / ".agentpack").mkdir()
        (tmp_path / ".agentpack/config.toml").write_text("[context]\n", encoding="utf-8")
    task = "fix user's payment retry"
    state = _quickstart_state(tmp_path, task, "deep", written=True, thread_id="codex-local")
    argv = shlex.split(next(cmd for label, cmd, _ in state["steps"] if label == "next"))
    assert argv.count("--thread") == 1
    assert argv[argv.index("--thread") + 1] == "codex-local"
    assert argv[argv.index("--mode") + 1] == "deep"
    if not initialized:
        assert argv[:3] == ["agentpack", "work", task]
