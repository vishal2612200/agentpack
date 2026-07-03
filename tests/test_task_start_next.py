from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app


def test_task_set_show_clear_global(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["task", "set", "fix auth bug"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".agentpack" / "task.md").read_text(encoding="utf-8") == "fix auth bug\n"

    result = runner.invoke(app, ["task", "show", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["task"] == "fix auth bug"

    result = runner.invoke(app, ["task", "clear"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".agentpack" / "task.md").exists()


def test_task_set_thread_scoped_and_auto(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTPACK_THREAD_ID", "codex-local")
    runner = CliRunner()

    result = runner.invoke(app, ["task", "set", "thread task", "--thread", "auto"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".agentpack" / "threads" / "codex-local" / "task.md").read_text(encoding="utf-8") == "thread task\n"
    assert not (tmp_path / ".agentpack" / "task.md").exists()


def test_start_writes_task_and_delegates_pack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        (tmp_path / ".agentpack").mkdir(exist_ok=True)
        (tmp_path / ".agentpack" / "pack_metadata.json").write_text('{"context_path": ".agentpack/context.md"}', encoding="utf-8")
        return Result()

    monkeypatch.setattr("agentpack.commands.start_cmd.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["start", "fix cache", "--pack-only", "--thread", "codex-local"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".agentpack" / "threads" / "codex-local" / "task.md").read_text(encoding="utf-8") == "fix cache\n"
    assert calls and calls[0][-2:] == ["--thread", "codex-local"]
    assert "Context ready" in result.output


def test_next_recommends_init_when_uninitialized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["next", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["recommendations"][0]["kind"] == "init"
    assert payload["recommendations"][0]["safe_to_continue"] == "no; initialize first"


def test_next_recommends_missing_task_for_initialized_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.next_cmd._context_is_fresh", lambda _root, **_kwargs: (True, "fresh"))

    result = CliRunner().invoke(app, ["next", "--json"])

    assert result.exit_code == 0, result.output
    kinds = [item["kind"] for item in json.loads(result.output)["recommendations"]]
    assert "missing_task" in kinds
    missing = next(item for item in json.loads(result.output)["recommendations"] if item["kind"] == "missing_task")
    assert "Generic or placeholder tasks" in missing["why_it_matters"]


def test_next_fix_does_not_refresh_missing_session_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-local")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task.md").write_text("old global task\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr("agentpack.commands.next_cmd.run_refresh", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = CliRunner().invoke(app, ["next", "--fix", "--json"])

    assert result.exit_code == 0, result.output
    kinds = [item["kind"] for item in json.loads(result.output)["recommendations"]]
    assert "missing_task" in kinds
    assert "stale_context" in kinds
    assert calls == []
