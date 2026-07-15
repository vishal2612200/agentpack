from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agentpack.dashboard.server import create_dashboard_server


def test_semantic_network_filters_receipts_and_mobile_layout(tmp_path: Path) -> None:
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
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="networkidle")
            page.get_by_test_id("semantic-mode-button").click()
            page.get_by_test_id("semantic-network-toolbar").wait_for()

            page.get_by_test_id("semantic-relationship-filter").select_option("calls")
            assert page.locator(".react-flow__edge").count() >= 1
            page.locator(".react-flow__edge").first.click()
            receipt = page.get_by_test_id("semantic-edge-receipt")
            assert "src/auth.py" in receipt.inner_text()
            assert "calls" in receipt.inner_text()

            page.get_by_test_id("semantic-evidence-toggle").click()
            assert page.get_by_test_id("semantic-evidence-table").is_visible()

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
