from __future__ import annotations

import os
import shutil
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from agentpack.dashboard.server import create_dashboard_server


def test_dashboard_modes_impact_navigation_and_responsive_layout(tmp_path: Path) -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("trace auth validation\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def validate(value):\n    return missing_dependency(value)\n",
        encoding="utf-8",
    )

    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            executable = _chrome_path()
            browser = playwright.chromium.launch(headless=True, executable_path=executable or None, args=["--use-angle=swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="networkidle")
            workspace = page.get_by_test_id("dashboard-workspace")
            page.get_by_role("button", name="Build", exact=True).click()
            assert workspace.get_attribute("data-presentation-mode") == "build"
            page.reload(wait_until="networkidle")
            assert workspace.get_attribute("data-presentation-mode") == "build"
            page.get_by_role("button", name="Explain", exact=True).click()
            screenshot_dir = os.environ.get("AGENTPACK_DASHBOARD_SCREENSHOT_DIR", "").strip()
            if screenshot_dir:
                Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(screenshot_dir) / "workspace-desktop.png"))

            page.get_by_role("button", name="Impact map", exact=True).click()
            canvas = page.locator(".city-canvas-wrap canvas")
            canvas.wait_for(timeout=15_000)
            canvas.scroll_into_view_if_needed()
            assert _wait_for_canvas_variance(page, canvas) > 4
            page.set_viewport_size({"width": 1024, "height": 768})
            assert _wait_for_canvas_variance(page, canvas) > 4
            if screenshot_dir:
                page.screenshot(path=str(Path(screenshot_dir) / "workspace-tablet.png"))
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_test_id("semantic-mode-button").click()
            page.get_by_test_id("semantic-network-toolbar").wait_for()

            page.get_by_test_id("semantic-relationship-filter").select_option("calls")
            page.wait_for_function("document.querySelectorAll('.react-flow__edge').length > 0")
            assert page.locator(".react-flow__edge").count() >= 1
            page.locator(".react-flow__edge").first.click()
            receipt = page.get_by_test_id("semantic-edge-receipt")
            assert "src/auth.py" in receipt.inner_text()
            assert "calls" in receipt.inner_text().lower()

            page.get_by_test_id("semantic-evidence-toggle").click()
            assert page.get_by_test_id("semantic-evidence-table").is_visible()

            page.get_by_role("button", name="Table", exact=True).click()
            page.locator(".map-table").wait_for()

            for viewport in ({"width": 1024, "height": 768}, {"width": 390, "height": 844}):
                page.set_viewport_size(viewport)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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


def _canvas_color_count(page, canvas) -> int:
    box = canvas.bounding_box()
    assert box is not None
    viewport = Image.open(BytesIO(page.screenshot())).convert("RGB")
    image = viewport.crop((int(box["x"]), int(box["y"]), int(box["x"] + box["width"]), int(box["y"] + box["height"])))
    image.thumbnail((160, 100))
    colors = image.getcolors(maxcolors=image.width * image.height)
    return len(colors or [])


def _wait_for_canvas_variance(page, canvas) -> int:
    count = 0
    for _ in range(20):
        count = _canvas_color_count(page, canvas)
        if count > 4:
            return count
        time.sleep(0.5)
    return count
