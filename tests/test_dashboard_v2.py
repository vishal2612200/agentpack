from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from agentpack.dashboard.server import create_dashboard_server
from agentpack.dashboard.models import ThreadRow
from agentpack.dashboard.v2 import _agent_summary, build_dashboard_v2_impact, build_dashboard_v2_payload


def _request(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"X-AgentPack-Token": token})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _assert_schema(schema: dict, definition: str, payload: dict) -> None:
    validator = Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"})
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, [f"{list(error.path)}: {error.message}" for error in errors]


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
        schema = json.loads(Path("docs/schemas/dashboard-v2.schema.json").read_text(encoding="utf-8"))
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


def test_dashboard_v2_envelope_matches_canonical_schema(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("schema contract\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")

    payload = build_dashboard_v2_payload(tmp_path, detail="home")
    schema = json.loads(Path("docs/schemas/dashboard-v2.schema.json").read_text(encoding="utf-8"))

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
        schema = json.loads(Path("docs/schemas/dashboard-v2.schema.json").read_text(encoding="utf-8"))
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
    schema = json.loads(Path("docs/schemas/dashboard-v2.schema.json").read_text(encoding="utf-8"))
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
