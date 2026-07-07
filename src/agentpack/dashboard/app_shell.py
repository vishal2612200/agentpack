from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from agentpack.dashboard.models import DashboardGraph, DashboardSnapshot
from agentpack.dashboard.renderers import render_dashboard_html


DASHBOARD_APP_DIR = Path(__file__).resolve().parents[1] / "data" / "dashboard_app"


def write_dashboard_shell(
    output_path: Path,
    snapshot: DashboardSnapshot,
    graph: DashboardGraph,
    *,
    data_filename: str = "dashboard-data.json",
    graph_filename: str = "dashboard-graph.json",
) -> bool:
    """Write the bundled cockpit shell.

    Returns true when the modern React/Vite shell was used. If the bundled app is
    absent in an editable checkout, writes the legacy static dashboard as a
    compatibility fallback.
    """

    index = DASHBOARD_APP_DIR / "index.html"
    if not index.exists():
        output_path.write_text(render_dashboard_html(snapshot), encoding="utf-8")
        return False

    html = index.read_text(encoding="utf-8")
    html = html.replace("__AGENTPACK_DASHBOARD_DATA__", data_filename)
    html = html.replace("__AGENTPACK_DASHBOARD_GRAPH__", graph_filename)
    html = html.replace("__AGENTPACK_DASHBOARD_DATA_JSON__", _json_for_script(snapshot.model_dump(mode="json")))
    html = html.replace("__AGENTPACK_DASHBOARD_GRAPH_JSON__", _json_for_script(graph.model_dump(mode="json")))
    html = _inline_built_assets(html)
    output_path.write_text(html, encoding="utf-8")
    _copy_assets(output_path.parent)
    return True


def _copy_assets(target_dir: Path) -> None:
    source = DASHBOARD_APP_DIR / "assets"
    if not source.exists() or not source.is_dir():
        return
    target = target_dir / "assets"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _json_for_script(value: object) -> str:
    return json.dumps(value, sort_keys=True).replace("<", "\\u003c")


def _inline_built_assets(html: str) -> str:
    css = DASHBOARD_APP_DIR / "assets" / "index.css"
    js = DASHBOARD_APP_DIR / "assets" / "index.js"
    if css.exists():
        css_text = css.read_text(encoding="utf-8")
        html = _replace_asset_tag(
            html,
            pattern=r"<link\b(?=[^>]*\bhref=[\"']\./assets/index\.css[\"'])(?=[^>]*\brel=[\"']stylesheet[\"'])[^>]*>\s*",
            replacement=f"<style>{css_text}</style>\n",
        )
    if js.exists():
        js_text = js.read_text(encoding="utf-8").replace("</script", "<\\/script")
        html = _replace_asset_tag(
            html,
            pattern=r"<script\b(?=[^>]*\bsrc=[\"']\./assets/index\.js[\"'])[^>]*>\s*</script>\s*",
            replacement=f"<script type=\"module\">{js_text}</script>\n",
        )
    return html


def _replace_asset_tag(html: str, *, pattern: str, replacement: str) -> str:
    updated, count = re.subn(pattern, lambda _match: replacement, html, count=1, flags=re.IGNORECASE)
    return updated if count else html
