from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentpack.cli import app


def test_dashboard_html_renders_in_headless_browser(tmp_path: Path, monkeypatch) -> None:
    chrome = _chrome_path()
    if not chrome:
        pytest.skip("Chrome/Chromium is not available for dashboard browser smoke")

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth token expiry\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["dashboard"])
    assert result.exit_code == 0, result.output

    screenshot = tmp_path / "dashboard.png"
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--allow-file-access-from-files",
            "--window-size=1280,900",
            f"--screenshot={screenshot}",
            (tmp_path / ".agentpack" / "dashboard.html").as_uri(),
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
