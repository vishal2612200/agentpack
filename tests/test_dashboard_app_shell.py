from __future__ import annotations

import json

import pytest

from agentpack.dashboard import app_shell


def test_dashboard_shell_injects_server_configuration(tmp_path, monkeypatch) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text(
        '<script>window.api = "__AGENTPACK_DASHBOARD_API__";</script>'
        '<script>window.token = "__AGENTPACK_DASHBOARD_TOKEN__";</script>'
        '<script>window.project = "__AGENTPACK_PROJECT_ID__";</script>',
        encoding="utf-8",
    )
    monkeypatch.setattr(app_shell, "DASHBOARD_APP_DIR", app_dir)

    html = app_shell.render_dashboard_shell(api_base="/api", token="secret-token", project_id="project-123")

    assert json.dumps("/api") in html
    assert json.dumps("secret-token") in html
    assert json.dumps("project-123") in html


def test_dashboard_shell_requires_bundled_index(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_shell, "DASHBOARD_APP_DIR", tmp_path / "missing")

    with pytest.raises(FileNotFoundError, match="dashboard app bundle not found"):
        app_shell.render_dashboard_shell()
