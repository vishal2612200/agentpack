from __future__ import annotations

import json

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.config import LoopConfig
from agentpack.core.loop_protocol import initialize_loop


def test_next_json_emits_stable_top_level_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["next", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) >= {"recommendations", "ok"}
    assert isinstance(data["recommendations"], list)
    assert isinstance(data["ok"], bool)


def test_next_recommends_loop_runner_when_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth\n", encoding="utf-8")
    initialize_loop(tmp_path, "fix auth", LoopConfig(runner="", verification_commands=["pytest -q"]))
    monkeypatch.setattr("agentpack.commands.next_cmd._context_is_fresh", lambda _root, **_kwargs: (True, "fresh"))

    result = CliRunner().invoke(app, ["next", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert any(item["kind"] == "loop_runner_missing" for item in payload["recommendations"])


def test_next_recommends_skills_index_when_auto_refresh_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth\n", encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.next_cmd._context_is_fresh", lambda _root, **_kwargs: (True, "fresh"))

    def fail_index(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("agentpack.commands.next_cmd.ensure_inventory_index", fail_index)

    result = CliRunner().invoke(app, ["next", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert any(item["kind"] == "skills_index_failed" for item in payload["recommendations"])
