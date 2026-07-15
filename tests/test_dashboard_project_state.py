from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from agentpack.dashboard.models import (
    ContextHealth,
    DashboardSnapshot,
    ProjectInfo,
    SelectedFileRow,
    TaskControlRow,
    TaskInfo,
)
from agentpack.dashboard.project_state import record_feedback, sync_dashboard_state
from agentpack.dashboard.server import create_dashboard_server


def test_project_state_deduplicates_legacy_task_sources_and_keeps_ids_stable(tmp_path: Path) -> None:
    snapshot = DashboardSnapshot(
        project=ProjectInfo(name="demo", path=str(tmp_path), branch="main"),
        task=TaskInfo(text="make onboarding clearer", state="in_progress", thread_id="thread-a"),
        context=ContextHealth(status="fresh", generated_at="2026-07-16T10:00:00+00:00", packed_tokens=100, raw_tokens=500, saving_pct=80, source_command="agentpack pack"),
        selected_files=[SelectedFileRow(path="src/home.py")],
        task_control=[
            TaskControlRow(scope="global", task="make onboarding clearer", state="in_progress", task_path=".agentpack/task.md"),
            TaskControlRow(scope="thread", thread_id="thread-a", task="make onboarding clearer", state="in_progress", task_path=".agentpack/threads/thread-a/task.md"),
        ],
    )

    first = sync_dashboard_state(tmp_path, snapshot)
    first_id = first["tasks"][0].task_id
    second = sync_dashboard_state(tmp_path, snapshot)

    assert len(first["tasks"]) == 1
    assert second["tasks"][0].task_id == first_id
    assert second["tasks"][0].thread_ids == ["thread-a"]
    assert len(second["runs"]) == 1
    assert (tmp_path / ".agentpack" / "dashboard-state" / "tasks" / f"{first_id}.json").exists()


def test_feedback_is_local_and_does_not_duplicate(tmp_path: Path) -> None:
    from agentpack.dashboard.models import DashboardFeedback

    feedback = DashboardFeedback(feedback_id="feedback-1", task_id="task-1", value="helped", created_at="2026-07-16T10:00:00+00:00")
    record_feedback(tmp_path, feedback)
    record_feedback(tmp_path, feedback)

    rows = (tmp_path / ".agentpack" / "dashboard-state" / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["value"] == "helped"


def test_project_task_api_is_bounded_and_writes_task_compatibility_file(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    headers = {"X-AgentPack-Token": server.state.token, "Content-Type": "application/json"}
    try:
        request = urllib.request.Request(f"{base}/api/project/tasks", data=json.dumps({"title": "add a welcome screen"}).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(request) as response:
            created = json.loads(response.read())
        assert created["task"]["title"] == "add a welcome screen"
        assert (tmp_path / ".agentpack" / "task.md").read_text(encoding="utf-8").strip() == "add a welcome screen"

        request = urllib.request.Request(f"{base}/api/project/tasks?limit=1", headers=headers)
        with urllib.request.urlopen(request) as response:
            listed = json.loads(response.read())
        assert len(listed["tasks"]) == 1
        assert listed["workspace"]["path"] == str(tmp_path.resolve())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
