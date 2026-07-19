from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from agentpack.dashboard.server import _dashboard_check_kind, create_dashboard_server
from agentpack.dashboard.models import ThreadRow
from agentpack.dashboard.v2 import _agent_summary, build_dashboard_v2_impact, build_dashboard_v2_payload

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "schemas" / "dashboard-v2.schema.json"


def _request(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"X-AgentPack-Token": token})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_request(url: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-AgentPack-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _assert_schema(schema: dict, definition: str, payload: dict) -> None:
    validator = Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"})
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, [f"{list(error.path)}: {error.message}" for error in errors]


def test_dashboard_check_classification_does_not_duplicate_finish() -> None:
    assert _dashboard_check_kind("agentpack dev-check") == "development"
    assert _dashboard_check_kind("agentpack release-check --profile ci") == "release"
    assert _dashboard_check_kind("python -m agentpack.cli review") == "review"
    assert _dashboard_check_kind("agentpack architecture check") == "architecture"
    assert _dashboard_check_kind("agentpack finish --summary done") == ""


def test_dashboard_v2_envelope_is_versioned_and_hides_handoff_uuid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("inspect auth flow\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def validate(value):\n    return value\n", encoding="utf-8")

    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = _request(
            f"http://127.0.0.1:{server.server_address[1]}/api/dashboard/v2?detail=home",
            server.state.token,
        )
        assert payload["schema_version"] == 2
        assert payload["detail"] == "home"
        assert payload["workspace"]["project"]["name"] == tmp_path.name
        assert "handoff_id" not in json.dumps(payload["agents"])
        evidence = _request(
            f"http://127.0.0.1:{server.server_address[1]}/api/dashboard/v2/evidence",
            server.state.token,
        )
        actions = _request(
            f"http://127.0.0.1:{server.server_address[1]}/api/dashboard/v2/actions",
            server.state.token,
        )
        agents = _request(
            f"http://127.0.0.1:{server.server_address[1]}/api/dashboard/v2/agents",
            server.state.token,
        )
        assert evidence["schema_version"] == 2
        assert actions["schema_version"] == 2
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        _assert_schema(schema, "evidenceResponse", evidence)
        _assert_schema(schema, "actionsResponse", actions)
        _assert_schema(schema, "agentsResponse", agents)

        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_address[1]}/api/dashboard/v2",
                timeout=30,
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
            unauthorized = json.loads(exc.read().decode("utf-8"))
            _assert_schema(schema, "errorResponse", unauthorized)
            assert unauthorized["kind"] == "unauthorized"
        else:
            raise AssertionError("v2 dashboard endpoint must require its dashboard token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_learning_recommendations_endpoint_is_typed_and_read_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "config.toml").write_text("[context]\n", encoding="utf-8")
    events = agentpack / "session-events.jsonl"
    events.write_text(
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
    before = events.read_text(encoding="utf-8")
    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = _request(
            f"http://127.0.0.1:{server.server_address[1]}/api/learning/recommendations?scope=local",
            server.state.token,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["schema_version"] == 1
    assert payload["scope"] == "local"
    assert payload["topics"][0]["topic_id"].startswith("topic-")
    assert payload["topics"][0]["start_command"].startswith("agentpack learn --topic")
    assert set(payload["mastery_summary"]) == {"mastered", "developing", "needs_practice", "unassessed"}
    _assert_schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")), "learningRecommendationSet", payload)
    assert events.read_text(encoding="utf-8") == before


def test_project_endpoints_are_authenticated_bounded_and_read_only_on_get(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "config.toml").write_text(
        "[project]\ndisplay_name = \"Project API\"\n"
        "[[project.outcomes]]\nid = \"outcome-api\"\ntitle = \"Ship API\"\n",
        encoding="utf-8",
    )
    events = agentpack / "session-events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event_id": "event-task",
                "event_type": "task_started",
                "type": "task_started",
                "occurred_at": "2026-07-19T10:00:00+00:00",
                "timestamp": "2026-07-19T10:00:00+00:00",
                "task": "Build project API",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    before = events.read_text(encoding="utf-8")
    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        overview = _request(f"{base}/api/project/overview?workspace=current", server.state.token)
        timeline = _request(f"{base}/api/project/timeline?workspace=all&limit=1", server.state.token)
        brief = _request(f"{base}/api/project/brief?mode=summary", server.state.token)
        dashboard = _request(f"{base}/api/dashboard/v2?detail=home", server.state.token)

        assert overview["profile"]["display_name"] == "Project API"
        assert overview["selected_workspace"] == "current"
        assert len(timeline["timeline"]) <= 1
        assert brief["mode"] == "summary"
        assert brief["markdown"].startswith("# Project API Status")
        assert dashboard["snapshot"]["project_overview"]["project_id"] == overview["project_id"]
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        _assert_schema(schema, "ProjectOverview", overview)
        _assert_schema(schema, "ProjectTimelineResponse", timeline)
        _assert_schema(schema, "ProjectStatusBrief", brief)
        with pytest.raises(urllib.error.HTTPError) as invalid:
            _request(f"{base}/api/project/overview?workspace=workspace-missing", server.state.token)
        assert invalid.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert events.read_text(encoding="utf-8") == before


def test_project_profile_api_is_revisioned_and_idempotent(tmp_path: Path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "config.toml").write_text("[project]\ndisplay_name = \"Before\"\n", encoding="utf-8")
    revision = hashlib.sha256((agentpack / "config.toml").read_bytes()).hexdigest()
    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        payload = {
            "mutation_id": "profile-1",
            "expected_revision": revision,
            "profile": {"display_name": "After", "purpose": "Project-level visibility", "stage": "active"},
        }
        first = _post_request(f"{base}/api/project/profile", server.state.token, payload)
        repeated = _post_request(f"{base}/api/project/profile", server.state.token, payload)
        assert first["duplicate"] is False
        assert repeated["duplicate"] is True
        assert repeated["profile"] == first["profile"]

        with pytest.raises(urllib.error.HTTPError) as conflict:
            _post_request(
                f"{base}/api/project/profile",
                server.state.token,
                {**payload, "mutation_id": "profile-2", "profile": {"display_name": "Conflict"}},
            )
        assert conflict.value.code == 409
        conflict_payload = json.loads(conflict.value.read().decode("utf-8"))
        assert conflict_payload["config_revision"] == first["profile"]["config_revision"]

        with pytest.raises(urllib.error.HTTPError) as malformed:
            _post_request(
                f"{base}/api/project/profile",
                server.state.token,
                {
                    "mutation_id": "profile-3",
                    "expected_revision": first["profile"]["config_revision"],
                    "profile": {"unknown": True},
                },
            )
        assert malformed.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_project_profile_api_serializes_concurrent_revision_writes(tmp_path: Path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    config = agentpack / "config.toml"
    config.write_text("[project]\ndisplay_name = \"Before\"\n", encoding="utf-8")
    revision = hashlib.sha256(config.read_bytes()).hexdigest()
    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def update_profile(mutation_id: str) -> int:
        try:
            _post_request(
                f"http://127.0.0.1:{server.server_address[1]}/api/project/profile",
                server.state.token,
                {
                    "mutation_id": mutation_id,
                    "expected_revision": revision,
                    "profile": {"display_name": mutation_id},
                },
            )
        except urllib.error.HTTPError as exc:
            return exc.code
        return 200

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(update_profile, ["profile-a", "profile-b"]))
        assert sorted(statuses) == [200, 409]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_project_event_api_validates_scope_and_returns_duplicate_result(tmp_path: Path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "config.toml").write_text(
        "[project]\n[[project.outcomes]]\nid = \"outcome-owned\"\ntitle = \"Owned outcome\"\n",
        encoding="utf-8",
    )
    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        payload = {
            "event_type": "project_outcome_status",
            "mutation_id": "status-1",
            "entity_id": "outcome-owned",
            "status": "on_track",
            "evidence": [{"kind": "task", "ref": "task-1", "path": "src/service.py"}],
        }
        first = _post_request(f"{base}/api/project/events", server.state.token, payload)
        repeated = _post_request(
            f"{base}/api/project/events",
            server.state.token,
            {**payload, "status": "at_risk"},
        )
        assert first["duplicate"] is False
        assert repeated["duplicate"] is True
        assert repeated["event"]["event_id"] == first["event"]["event_id"]
        assert repeated["project_overview"]["outcomes"][0]["status"] == "on_track"

        with pytest.raises(urllib.error.HTTPError) as invalid:
            _post_request(
                f"{base}/api/project/events",
                server.state.token,
                {**payload, "mutation_id": "status-2", "entity_id": "outcome-other"},
            )
        assert invalid.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_project_profile_api_returns_forbidden_for_read_only_config(tmp_path: Path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    config = agentpack / "config.toml"
    config.write_text("[project]\ndisplay_name = \"Read only\"\n", encoding="utf-8")
    revision = hashlib.sha256(config.read_bytes()).hexdigest()
    config.chmod(0o444)
    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as forbidden:
            _post_request(
                f"http://127.0.0.1:{server.server_address[1]}/api/project/profile",
                server.state.token,
                {
                    "mutation_id": "profile-read-only",
                    "expected_revision": revision,
                    "profile": {"display_name": "Blocked"},
                },
            )
        assert forbidden.value.code == 403
    finally:
        config.chmod(0o644)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_v2_envelope_matches_canonical_schema(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("schema contract\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")

    payload = build_dashboard_v2_payload(tmp_path, detail="home")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, [f"{list(error.path)}: {error.message}" for error in errors]
    assert "handoff_id" not in json.dumps(payload)


def test_dashboard_v2_action_inspection_returns_explainable_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("inspect action\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")

    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/dashboard/v2/actions/inspect",
            data=json.dumps({"action": "refresh_context"}).encode("utf-8"),
            headers={"X-AgentPack-Token": server.state.token, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        _assert_schema(schema, "actionInspectionResponse", payload)
        inspection = payload["inspection"]
        assert inspection["schema_version"] == 2
        assert inspection["purpose"]
        assert inspection["expected_effect"]
        assert "command" in inspection
        assert "affected_paths" in inspection
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_v2_impact_filters_tree_sitter_entities(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def validate(value):\n    return value\n\nclass Session:\n    pass\n",
        encoding="utf-8",
    )

    payload = build_dashboard_v2_impact(tmp_path, query="validate", limit=20)

    assert payload["schema_version"] == 2
    assert payload["query"] == "validate"
    assert payload["available"] is True
    assert any(entity["name"].endswith("validate") for entity in payload["summary"]["entities"])
    assert any(entity["id"].startswith("file:") for entity in payload["scene"]["entities"])
    assert any(entity["id"].startswith("semantic:") for entity in payload["entities"])
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    _assert_schema(schema, "impactResponse", payload)


def test_dashboard_v2_rejects_malformed_and_unknown_action_fields(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("validate requests\n", encoding="utf-8")
    server = create_dashboard_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        url = f"{base}/api/dashboard/v2/actions/inspect"
        for body in (b"{not-json", json.dumps({"action": "next", "unknown": True}).encode("utf-8"), json.dumps({"action": "", "budget": 0}).encode("utf-8")):
            request = urllib.request.Request(url, data=body, headers={"X-AgentPack-Token": server.state.token, "Content-Type": "application/json"}, method="POST")
            with pytest.raises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=30)
            assert raised.value.code == 400
            payload = json.loads(raised.value.read().decode("utf-8"))
            assert payload["schema_version"] == 2
            assert payload["kind"] in {"malformed_json", "invalid_request"}

        for path, body in (("resume", {}), ("release", {"name": "handoff", "unknown": True})):
            request = urllib.request.Request(
                f"{base}/api/dashboard/v2/agents/{path}",
                data=json.dumps(body).encode("utf-8"),
                headers={"X-AgentPack-Token": server.state.token, "Content-Type": "application/json"},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=30)
            assert raised.value.code == 400
            payload = json.loads(raised.value.read().decode("utf-8"))
            assert payload["kind"] == "invalid_request"

        v1 = _request(f"{base}/api/dashboard?detail=home", server.state.token)
        assert "schema_version" not in v1
        assert {"snapshot", "graph", "map"}.issubset(v1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_v2_agents_serialize_active_thread_worktree(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    snapshot = SimpleNamespace(
        context=SimpleNamespace(status="fresh"),
        thread_rows=[ThreadRow(thread_id="session-1", task="Fix auth", status="active", worktree="/workspace/auth")],
        integrations=[],
        mcp_health=SimpleNamespace(model_dump=lambda **_: {"status": "healthy"}),
    )

    agents = _agent_summary(tmp_path, snapshot).model_dump(mode="json")

    assert agents["sessions"] == [{
        "provider": "agentpack",
        "session_id": "session-1",
        "thread_id": "session-1",
        "task": "Fix auth",
        "status": "active",
        "context_status": "fresh",
        "updated_at": "",
        "worktree": "/workspace/auth",
    }]
