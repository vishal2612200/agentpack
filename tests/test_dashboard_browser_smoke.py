from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agentpack.dashboard.app_shell import DASHBOARD_APP_DIR, render_dashboard_shell


def test_dashboard_html_renders_in_headless_browser(tmp_path: Path, monkeypatch) -> None:
    chrome = _chrome_path()
    if not chrome:
        pytest.skip("Chrome/Chromium is not available for dashboard browser smoke")

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth token expiry\n", encoding="utf-8")
    app_root = tmp_path / ".agentpack"
    (app_root / "assets").symlink_to(DASHBOARD_APP_DIR / "assets", target_is_directory=True)
    (app_root / "index.html").write_text(render_dashboard_shell(), encoding="utf-8")

    screenshot = tmp_path / "dashboard.png"
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--allow-file-access-from-files",
            "--window-size=1280,900",
            f"--screenshot={screenshot}",
            (tmp_path / ".agentpack" / "index.html").as_uri(),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    assert screenshot.exists()
    assert screenshot.stat().st_size > 10_000


def _chrome_path() -> str:
    for candidate in (
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return ""
