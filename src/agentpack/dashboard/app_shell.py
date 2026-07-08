from __future__ import annotations

import json
from pathlib import Path


DASHBOARD_APP_DIR = Path(__file__).resolve().parents[1] / "data" / "dashboard_app"


def render_dashboard_shell(*, api_base: str = "", token: str = "") -> str:
    """Render the bundled cockpit shell for the local dashboard server."""
    index = DASHBOARD_APP_DIR / "index.html"
    if not index.exists():
        raise FileNotFoundError(f"dashboard app bundle not found at {index}")

    html = index.read_text(encoding="utf-8")
    html = html.replace('"__AGENTPACK_DASHBOARD_API__"', json.dumps(api_base))
    html = html.replace('"__AGENTPACK_DASHBOARD_TOKEN__"', json.dumps(token))
    return html
