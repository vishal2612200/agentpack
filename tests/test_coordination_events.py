from __future__ import annotations

import json
from pathlib import Path

from agentpack.session.events import read_events, record_event
from agentpack.session.state import create_session, load_session, stop_session


def test_session_id_is_persisted_and_lifecycle_events_are_linked(tmp_path: Path) -> None:
    state = create_session(tmp_path, agent="codex", mode="balanced")
    loaded = load_session(tmp_path)

    assert loaded is not None
    assert loaded.session_id == state.session_id
    events = read_events(tmp_path)
    assert events[0]["event_type"] == "session_started"
    assert events[0]["session_id"] == state.session_id

    stop_session(tmp_path)
    assert read_events(tmp_path)[-1]["event_type"] == "session_stopped"
    assert read_events(tmp_path)[-1]["session_id"] == state.session_id


def test_record_event_assigns_canonical_identity_and_unique_ids(tmp_path: Path) -> None:
    create_session(tmp_path, agent="claude", mode="lite")
    first = record_event(tmp_path, "pack", {"task": "prepare auth context", "thread_id": "claude-1"}, source="mcp")
    second = record_event(tmp_path, "pack", {"task": "prepare auth context", "thread_id": "claude-1"}, source="mcp")

    assert first["event_type"] == "context_prepared"
    assert first["type"] == "pack"
    assert first["project_id"].startswith("project-")
    assert first["workspace_id"].startswith("workspace-")
    assert first["task_id"].startswith("task-")
    assert first["session_id"].startswith("session-")
    assert first["external_thread_ids"] == ["claude-1"]
    assert first["event_id"] != second["event_id"]
    assert load_session(tmp_path).external_thread_ids == ["claude-1"]


def test_legacy_events_are_normalized_without_rewriting_history(tmp_path: Path) -> None:
    path = tmp_path / ".agentpack" / "session-events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"type": "pack", "timestamp": "2026-07-16T10:00:00+00:00", "task": "legacy task"}) + "\n"
        + "not json\n",
        encoding="utf-8",
    )

    events = read_events(tmp_path)

    assert len(events) == 1
    assert events[0]["event_type"] == "context_prepared"
    assert events[0]["type"] == "pack"
    assert events[0]["event_id"].startswith("event-")
    assert path.read_text(encoding="utf-8").count("not json") == 1


def test_legacy_session_gets_deterministic_migrated_id(tmp_path: Path) -> None:
    path = tmp_path / ".agentpack" / "session.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"active": True, "started_at": "2026-07-16T10:00:00+00:00", "agent": "codex"}), encoding="utf-8")

    first = load_session(tmp_path)
    second = load_session(tmp_path)

    assert first is not None and second is not None
    assert first.session_id.startswith("session-")
    assert first.session_id == second.session_id
