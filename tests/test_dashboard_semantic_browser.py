from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from agentpack.core.project_index import register_project
from agentpack.dashboard import app_shell as dashboard_app_shell
from agentpack.dashboard import server as dashboard_server_module
from agentpack.dashboard.server import create_dashboard_server


@pytest.fixture(scope="module")
def built_dashboard_app(tmp_path_factory) -> Path:
    frontend = Path(__file__).resolve().parents[1] / "frontend" / "dashboard"
    if not shutil.which("npm") or not (frontend / "node_modules").is_dir():
        pytest.skip("dashboard Node dependencies are not installed")
    output = tmp_path_factory.mktemp("dashboard-app")
    subprocess.run(
        ["npm", "--prefix", str(frontend), "run", "build", "--", "--outDir", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    return output


def test_dashboard_modes_impact_navigation_and_responsive_layout(tmp_path: Path, monkeypatch, built_dashboard_app: Path) -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text(
        "[project]\n"
        'display_name = "AgentPack Browser Fixture"\n'
        'purpose = "Keep project outcomes and engineering evidence connected."\n'
        'owners = ["Platform"]\n'
        'audiences = ["Developers", "Product"]\n'
        'stage = "active"\n\n'
        "[[project.outcomes]]\n"
        'id = "outcome-dashboard"\n'
        'title = "Ship project dashboard"\n'
        'description = "Expose project progress without task-count proxies."\n'
        'owner = "Platform"\n'
        'target_date = "2026-08-01"\n\n'
        "[[project.outcomes.milestones]]\n"
        'id = "milestone-contracts"\n'
        'title = "Project contracts"\n'
        'owner = "Platform"\n'
        'due_date = "2026-07-25"\n',
        encoding="utf-8",
    )
    (tmp_path / ".agentpack" / "task.md").write_text("trace auth validation\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def validate(value):\n    return missing_dependency(value)\n",
        encoding="utf-8",
    )

    server = _create_dashboard_server(tmp_path, built_dashboard_app, monkeypatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            executable = _chrome_path()
            browser = playwright.chromium.launch(headless=True, executable_path=executable or None, args=["--use-angle=swiftshader"])
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                permissions=["clipboard-read", "clipboard-write"],
                accept_downloads=True,
            )
            page = context.new_page()
            partial_warnings = [
                "inaccessible_worktree: /private/tmp/agentpack-stale-a",
                "inaccessible_worktree: /private/tmp/agentpack-stale-b",
                "inaccessible_worktree: /private/tmp/agentpack-stale-c",
                "inaccessible_worktree: /private/tmp/agentpack-stale-d",
                "partial_result: limited worktree discovery to 20",
                "empty_roadmap: no declared project outcomes",
            ]

            def serve_partial_home(route) -> None:
                response = route.fetch()
                body = response.json()
                body["snapshot"]["project_overview"].update({"partial": True, "warnings": partial_warnings})
                route.fulfill(status=response.status, content_type="application/json", body=json.dumps(body))

            page.route("**/api/dashboard/v2?detail=home", serve_partial_home)
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="networkidle")
            page.unroute("**/api/dashboard/v2?detail=home", serve_partial_home)
            workspace = page.get_by_test_id("dashboard-workspace")
            overview = page.get_by_test_id("project-overview-view")
            overview.wait_for()
            assert "AgentPack Browser Fixture" in overview.inner_text()
            assert "Ship project dashboard" in overview.inner_text()
            assert "Project contracts" in overview.inner_text()
            assert page.locator(".sidebar > .nav-list > .nav-item").all_inner_texts() == [
                "Overview",
                "Roadmap",
                "Work",
                "Health",
                "Knowledge",
            ]
            assert "Runtime nominal" not in page.locator("body").inner_text()
            data_notice = page.get_by_test_id("project-data-notice")
            assert "Some project information is unavailable" in data_notice.inner_text()
            assert "4 registered worktrees are unavailable" in data_notice.inner_text()
            assert "/private/tmp" not in data_notice.inner_text()
            data_notice.locator("summary").click()
            assert "Review 3 data gaps" in data_notice.inner_text()
            assert "4 registered worktrees were excluded" in data_notice.inner_text()
            assert "agentpack-stale-a" not in data_notice.inner_text()
            page.get_by_role("button", name="Engineering", exact=True).click()
            assert workspace.get_attribute("data-presentation-mode") == "build"
            assert "inaccessible_worktree" in data_notice.inner_text()
            assert "agentpack-stale-a" in data_notice.inner_text()
            assert "/private/tmp" not in data_notice.inner_text()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.reload(wait_until="networkidle")
            assert workspace.get_attribute("data-presentation-mode") == "build"
            page.get_by_role("button", name="Summary", exact=True).click()
            page.get_by_title("Copy Summary brief").click()
            page.get_by_text("Status brief copied.", exact=True).wait_for()
            assert "# AgentPack Browser Fixture Status" in page.evaluate("navigator.clipboard.readText()")
            with page.expect_download() as download_info:
                page.get_by_title("Download Summary brief").click()
            assert download_info.value.suggested_filename.endswith("summary-status.md")

            for name, test_id in (
                ("Roadmap", "project-roadmap-view"),
                ("Health", "project-health-view"),
                ("Work", "project-work-view"),
                ("Knowledge", "project-knowledge-summary"),
            ):
                page.get_by_role("button", name=name, exact=True).click()
                page.get_by_test_id(test_id).wait_for()
                if name == "Roadmap":
                    assert "Project contracts" in page.get_by_test_id(test_id).inner_text()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.get_by_role("button", name="Overview", exact=True).click()
            overview.wait_for()
            page.get_by_role("button", name="View all activity", exact=False).click()
            page.get_by_test_id("project-activity-view").wait_for()
            page.get_by_role("button", name="Overview", exact=True).click()
            overview.wait_for()

            page.keyboard.press("Control+k")
            palette = page.get_by_role("dialog", name="AgentPack command palette")
            palette.wait_for()
            assert "Navigate" in palette.inner_text()
            assert "Project evidence" in palette.inner_text()
            palette.get_by_placeholder("Search project evidence or run an action").fill("Project contracts")
            palette.get_by_text("Project contracts", exact=True).click()
            page.get_by_test_id("project-roadmap-view").wait_for()
            page.get_by_role("button", name="Overview", exact=True).click()

            page.locator(".runtime-status-trigger").click()
            status_dialog = page.get_by_role("dialog", name="Dashboard evidence status")
            status_dialog.wait_for()
            for signal in ("API", "Snapshot", "Context", "MCP"):
                assert signal in status_dialog.inner_text()
            assert "Source:" in status_dialog.inner_text()
            assert "Observed:" in status_dialog.inner_text()
            status_dialog.get_by_role("button", name="Close dialog").click()

            cache_key = page.evaluate(
                "Object.keys(localStorage).find((key) => "
                "key.startsWith('agentpack.dashboard.last-known.v1.')) || ''"
            )
            assert cache_key
            page.route("**/api/dashboard/v2?detail=home", lambda route: route.abort())
            page.reload(wait_until="domcontentloaded")
            page.get_by_text("Last known", exact=False).first.wait_for()
            assert page.get_by_role("button", name="Edit profile", exact=False).is_disabled()
            page.unroute("**/api/dashboard/v2?detail=home")
            page.get_by_role("button", name="Retry", exact=True).first.click()
            page.locator(".runtime-status-trigger.live").wait_for()

            page.evaluate(
                "(key) => localStorage.setItem(key, "
                "JSON.stringify({schema_version: 1, cached_at: 'invalid', status: {}}))",
                cache_key,
            )
            page.route("**/api/dashboard/v2?detail=home", lambda route: route.abort())
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("heading", name="Dashboard failed to load", exact=True).wait_for()
            assert page.evaluate("(key) => localStorage.getItem(key)", cache_key) is None
            page.unroute("**/api/dashboard/v2?detail=home")
            page.get_by_role("button", name="Retry", exact=True).click()
            page.locator(".runtime-status-trigger.live").wait_for()

            for viewport in ({"width": 1024, "height": 768}, {"width": 390, "height": 844}):
                page.set_viewport_size(viewport)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.set_viewport_size({"width": 1440, "height": 900})
            screenshot_dir = os.environ.get("AGENTPACK_DASHBOARD_SCREENSHOT_DIR", "").strip()
            if screenshot_dir:
                Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(screenshot_dir) / "workspace-desktop.png"))

            page.get_by_text("Explore", exact=True).click()
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
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_learning_recommendations_scope_and_copy(tmp_path: Path, monkeypatch, built_dashboard_app: Path) -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))

    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "config.toml").write_text("[context]\n", encoding="utf-8")
    (agentpack / "task.md").write_text("Improve CLI output\n", encoding="utf-8")
    (agentpack / "session-events.jsonl").write_text(
        json.dumps(
            {
                "type": "task_memory",
                "timestamp": "2026-07-19T10:00:00+00:00",
                "task_id": "task-cli",
                "task": "Improve CLI output",
                "status": "done",
                "concepts": ["CLI design"],
                "changed_files": ["src/agentpack/commands/learn.py"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    register_project(tmp_path)

    server = _create_dashboard_server(tmp_path, built_dashboard_app, monkeypatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            executable = _chrome_path()
            browser = playwright.chromium.launch(headless=True, executable_path=executable or None)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                permissions=["clipboard-read", "clipboard-write"],
            )
            page = context.new_page()
            local_requests = {"count": 0}

            def handle_local_recommendations(route) -> None:
                local_requests["count"] += 1
                if local_requests["count"] == 1:
                    route.fulfill(status=503, content_type="application/json", body='{"error":"temporary"}')
                    return
                route.continue_()

            page.route("**/api/learning/recommendations?scope=local", handle_local_recommendations)
            competency_ids = [
                "product_reasoning",
                "implementation",
                "quality",
                "systems",
                "production",
                "security",
                "collaboration",
            ]
            page.route(
                "**/api/learning/recommendations?scope=global",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "schema_version": 2,
                            "recommendation_id": "recommendation-empty",
                            "scope": "global",
                            "generated_at": "2026-07-19T10:00:00+00:00",
                            "topics": [],
                            "warnings": ["insufficient_history: fewer than three evidence-backed topics are available"],
                            "mastery_summary": {"mastered": 0, "developing": 0, "needs_practice": 0, "unassessed": 7},
                            "profile": {"schema_version": 1, "role": "general", "target_level": "unspecified", "updated_at": ""},
                            "competencies": [
                                {
                                    "competency_id": competency_id,
                                    "name": competency_id.replace("_", " ").title(),
                                    "status": "unassessed",
                                    "passing_proofs": 0,
                                    "verified_artifacts": 0,
                                    "latest_evidence": "",
                                    "latest_score": None,
                                    "role_emphasis": False,
                                }
                                for competency_id in competency_ids
                            ],
                        }
                    ),
                ),
            )
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="networkidle")
            page.get_by_role("button", name="Knowledge", exact=True).click()

            scope = page.get_by_role("group", name="Learning recommendation scope")
            scope.wait_for()
            assert scope.get_by_role("button", name="This project", exact=True).get_attribute("class") == "active"
            page.locator(".learning-error").wait_for()
            assert "503" in page.locator(".learning-error").inner_text()
            page.get_by_role("button", name="Retry", exact=True).click()
            topics = page.locator(".learning-topic-row")
            topics.first.wait_for()
            assert 1 <= topics.count() <= 3
            assert page.locator(".competency-row").count() == 8
            command = topics.first.locator("code").inner_text()
            topics.first.get_by_role("button", name="Copy command", exact=False).click()
            assert page.evaluate("navigator.clipboard.readText()") == command
            with page.expect_response(lambda response: response.url.endswith("/api/learning/sessions/start") and response.status == 200):
                topics.first.get_by_role("button", name="Start", exact=True).click()
            page.get_by_text("Active Learning Session", exact=True).wait_for()
            page.get_by_role("button", name="Copy coaching prompt", exact=True).click()
            assert "Evaluate every expected point" in page.evaluate("navigator.clipboard.readText()")

            with page.expect_response(lambda response: response.url.endswith("/api/learning/profile") and response.request.method == "POST"):
                page.get_by_label("Learner role").select_option("backend")
            assert page.get_by_label("Learner role").input_value() == "backend"

            with page.expect_response(lambda response: "scope=global" in response.url and response.status == 200):
                scope.get_by_role("button", name="All projects", exact=True).click()
            assert scope.get_by_role("button", name="All projects", exact=True).get_attribute("class") == "active"
            page.get_by_text("No evidence-backed topics yet", exact=False).wait_for()
            assert "insufficient_history" in page.locator(".learning-warning").inner_text()
            with page.expect_response(lambda response: "scope=local" in response.url and response.status == 200):
                scope.get_by_role("button", name="This project", exact=True).click()
            topics.first.wait_for()
            before_focus = local_requests["count"]
            with page.expect_response(lambda response: "scope=local" in response.url and response.status == 200):
                page.dispatch_event("body", "focus")
                page.evaluate("window.dispatchEvent(new Event('focus'))")
            assert local_requests["count"] > before_focus

            screenshot_dir = os.environ.get("AGENTPACK_DASHBOARD_SCREENSHOT_DIR", "").strip()
            if screenshot_dir:
                Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(screenshot_dir) / "learning-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            if screenshot_dir:
                page.screenshot(path=str(Path(screenshot_dir) / "learning-mobile.png"), full_page=True)
            context.close()
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


def _create_dashboard_server(root: Path, app_dir: Path, monkeypatch):
    monkeypatch.setattr(dashboard_app_shell, "DASHBOARD_APP_DIR", app_dir)
    monkeypatch.setattr(dashboard_server_module, "DASHBOARD_APP_DIR", app_dir)
    return create_dashboard_server(root, port=0)


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
