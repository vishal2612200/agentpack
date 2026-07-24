from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.dashboard.app_shell import render_dashboard_shell
from agentpack.dashboard.server import DashboardServerState
from agentpack.dashboard.server import DashboardRequestHandler


runner = CliRunner()


def test_dashboard_handler_ignores_client_disconnect(monkeypatch) -> None:
    def disconnect(_handler) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(BaseHTTPRequestHandler, "handle", disconnect)

    DashboardRequestHandler.handle(object.__new__(DashboardRequestHandler))


def test_dashboard_serves_project_dashboard(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth\n", encoding="utf-8")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("agentpack.commands.dashboard._port_available", lambda host, port: True)

    def fake_serve(root, *, host, port, open_browser):
        calls.append({"root": root, "host": host, "port": port, "open_browser": open_browser})
        return f"http://{host}:{port}/"

    monkeypatch.setattr("agentpack.commands.dashboard.serve_dashboard", fake_serve)

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    call = calls[0]
    assert Path(call["root"]).resolve() == tmp_path.resolve()
    assert call["host"] == "127.0.0.1"
    assert call["port"] == 8765
    assert call["open_browser"] is False
    assert "http://127.0.0.1:8765/" in result.output
    assert not (tmp_path / ".agentpack" / "dashboard.html").exists()


def test_dashboard_json_outputs_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()

    result = runner.invoke(app, ["dashboard", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["project"]["path"] == str(tmp_path)


def test_dashboard_rejects_static_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["dashboard", "--output", "out/dashboard.html"])

    assert result.exit_code == 2, result.output
    assert "Static dashboard output is deprecated" in result.output
    assert not (tmp_path / "out" / "dashboard.html").exists()


def test_dashboard_open_passes_browser_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "agentpack.commands.dashboard.serve_dashboard",
        lambda root, *, host, port, open_browser: calls.append({"root": root, "host": host, "port": port, "open_browser": open_browser}),
    )

    result = runner.invoke(app, ["dashboard", "--open", "--port", "8766"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    call = calls[0]
    assert Path(call["root"]).resolve() == tmp_path.resolve()
    assert call["host"] == "127.0.0.1"
    assert call["port"] == 8766
    assert call["open_browser"] is True


def test_dashboard_uses_free_port_when_default_is_busy(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("agentpack.commands.dashboard._port_available", lambda host, port: False)
    monkeypatch.setattr("agentpack.commands.dashboard._free_port", lambda host: 9876)
    monkeypatch.setattr(
        "agentpack.commands.dashboard.serve_dashboard",
        lambda root, *, host, port, open_browser: calls.append({"host": host, "port": port}),
    )

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.1", "port": 9876}]
    assert "using port 9876 instead" in result.output


def test_dashboard_keeps_explicit_port_strict(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("agentpack.commands.dashboard._port_available", lambda host, port: False)
    monkeypatch.setattr("agentpack.commands.dashboard._free_port", lambda host: 9876)
    monkeypatch.setattr(
        "agentpack.commands.dashboard.serve_dashboard",
        lambda root, *, host, port, open_browser: calls.append({"host": host, "port": port}),
    )

    result = runner.invoke(app, ["dashboard", "--port", "8765"])

    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.1", "port": 8765}]
    assert "using port" not in result.output


def test_dashboard_shell_preserves_token_window_property() -> None:
    html = render_dashboard_shell(token="secret-token")

    assert "window.__AGENTPACK_DASHBOARD_TOKEN__" in html
    assert 'window. = ""' not in html
    assert '"secret-token"' in html


def test_dashboard_server_state_switches_project_and_resets_terminal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    (tmp_path / ".git").mkdir()
    target = tmp_path / "target"
    (target / ".agentpack").mkdir(parents=True)
    (target / ".agentpack" / "config.toml").write_text("[context]\ndefault_budget = 1000\n", encoding="utf-8")
    state = DashboardServerState(root=tmp_path)
    original_terminal = state.terminal

    payload = state.switch_root(str(target))

    assert state.root == target.resolve()
    assert state.terminal is not original_terminal
    assert state.terminal.root == target.resolve()
    assert payload["snapshot"]["project"]["path"] == str(target.resolve())
    index = json.loads((tmp_path / "home" / ".agentpack" / "projects.json").read_text(encoding="utf-8"))
    assert index["projects"][0]["path"] == str(target.resolve())


def test_dashboard_server_state_rejects_invalid_project_switch(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    state = DashboardServerState(root=tmp_path)
    invalid = tmp_path / "plain"
    invalid.mkdir()

    with pytest.raises(ValueError, match="absolute"):
        state.switch_root("relative/path")
    with pytest.raises(ValueError, match="must contain"):
        state.switch_root(str(invalid))
    with pytest.raises(ValueError, match="existing directory"):
        state.switch_root(str(tmp_path / "missing"))
