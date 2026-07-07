from __future__ import annotations

import json
from pathlib import Path

from agentpack.dashboard import app_shell
from agentpack.dashboard.app_shell import write_dashboard_shell
from agentpack.dashboard.graph import build_dashboard_graph
from agentpack.dashboard.models import DashboardSnapshot, ProjectInfo, TaskInfo


def test_dashboard_shell_inlines_assets_with_reordered_vite_tags(tmp_path: Path, monkeypatch) -> None:
    app_dir = tmp_path / "app"
    assets = app_dir / "assets"
    assets.mkdir(parents=True)
    (app_dir / "index.html").write_text(
        "\n".join(
            [
                "<html><head>",
                '<link href="./assets/index.css" data-vite rel="stylesheet">',
                '<script crossorigin src="./assets/index.js" type="module"></script>',
                '<script id="agentpack-dashboard-data">__AGENTPACK_DASHBOARD_DATA_JSON__</script>',
                '<script id="agentpack-dashboard-graph">__AGENTPACK_DASHBOARD_GRAPH_JSON__</script>',
                "</head><body><div id=\"root\"></div></body></html>",
            ]
        ),
        encoding="utf-8",
    )
    (assets / "index.css").write_text(".app{color:red}", encoding="utf-8")
    (assets / "index.js").write_text("console.log('dashboard')", encoding="utf-8")
    monkeypatch.setattr(app_shell, "DASHBOARD_APP_DIR", app_dir)
    snapshot = DashboardSnapshot(project=ProjectInfo(name="repo", path=str(tmp_path)), task=TaskInfo(text="fix auth"))
    graph = build_dashboard_graph(snapshot)
    output = tmp_path / "dashboard.html"

    assert write_dashboard_shell(output, snapshot, graph) is True

    html = output.read_text(encoding="utf-8")
    assert "<style>.app{color:red}</style>" in html
    assert "<script type=\"module\">console.log('dashboard')</script>" in html
    assert "__AGENTPACK_DASHBOARD_DATA_JSON__" not in html
    assert json.loads(html.split('<script id="agentpack-dashboard-data">', 1)[1].split("</script>", 1)[0])["task"]["text"] == "fix auth"
    assert (tmp_path / "assets" / "index.css").exists()


def test_dashboard_shell_falls_back_to_legacy_when_bundle_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_shell, "DASHBOARD_APP_DIR", tmp_path / "missing")
    snapshot = DashboardSnapshot(project=ProjectInfo(name="repo", path=str(tmp_path)), task=TaskInfo(text="fix auth"))

    assert write_dashboard_shell(tmp_path / "dashboard.html", snapshot, build_dashboard_graph(snapshot)) is False
    assert "AgentPack Dashboard" in (tmp_path / "dashboard.html").read_text(encoding="utf-8")
