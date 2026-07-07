from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    atomic_writes: list[Path] = []

    def fake_atomic_write(path: Path, text: str) -> None:
        atomic_writes.append(path)
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(app_shell, "_atomic_write", fake_atomic_write)
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
    assert atomic_writes == [output]


def test_dashboard_shell_falls_back_to_legacy_when_bundle_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_shell, "DASHBOARD_APP_DIR", tmp_path / "missing")
    atomic_writes: list[Path] = []

    def fake_atomic_write(path: Path, text: str) -> None:
        atomic_writes.append(path)
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(app_shell, "_atomic_write", fake_atomic_write)
    snapshot = DashboardSnapshot(project=ProjectInfo(name="repo", path=str(tmp_path)), task=TaskInfo(text="fix auth"))

    assert write_dashboard_shell(tmp_path / "dashboard.html", snapshot, build_dashboard_graph(snapshot)) is False
    assert "AgentPack Dashboard" in (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert atomic_writes == [tmp_path / "dashboard.html"]


@pytest.mark.parametrize(
    ("index_html", "asset_name"),
    [
        (
            '<html><head><script src="./assets/index.js"></script></head><body></body></html>',
            "./assets/index.css",
        ),
        (
            '<html><head><link rel="stylesheet" href="./assets/index.css"></head><body></body></html>',
            "./assets/index.js",
        ),
    ],
)
def test_dashboard_shell_errors_when_required_asset_tag_is_missing(
    tmp_path: Path,
    monkeypatch,
    index_html: str,
    asset_name: str,
) -> None:
    app_dir = tmp_path / "app"
    assets = app_dir / "assets"
    assets.mkdir(parents=True)
    (app_dir / "index.html").write_text(index_html, encoding="utf-8")
    (assets / "index.css").write_text(".app{color:red}", encoding="utf-8")
    (assets / "index.js").write_text("console.log('dashboard')", encoding="utf-8")
    monkeypatch.setattr(app_shell, "DASHBOARD_APP_DIR", app_dir)
    snapshot = DashboardSnapshot(project=ProjectInfo(name="repo", path=str(tmp_path)), task=TaskInfo(text="fix auth"))

    with pytest.raises(RuntimeError, match=f"Dashboard bundle asset tag not found: {asset_name}"):
        write_dashboard_shell(tmp_path / "dashboard.html", snapshot, build_dashboard_graph(snapshot))
