from __future__ import annotations

import json
import hashlib
import mimetypes
import re
import secrets
import shlex
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agentpack.core import git
from agentpack.core.project_index import register_project
from agentpack.core.handoff import HandoffError, accept_handoff, release_handoff
from agentpack.core.task_freshness import write_task_md
from agentpack.dashboard.action_history import read_action_history, record_dashboard_action
from agentpack.dashboard.actions import DashboardActionError, build_dashboard_action_command, update_dashboard_config
from agentpack.dashboard.app_shell import DASHBOARD_APP_DIR, render_dashboard_shell
from agentpack.dashboard.collectors import build_project_dashboard_snapshot, semantic_graph_summary
from agentpack.dashboard.graph import build_dashboard_graph
from agentpack.dashboard.map import build_dashboard_map
from agentpack.dashboard.models import DashboardFeedback
from agentpack.dashboard.project_state import analytics_for_range, build_project_home_snapshot, create_dashboard_task, get_project_task, record_feedback, task_detail_payload, task_event_is_in_scope, task_timeline, update_task
from agentpack.dashboard.project_overview import (
    ProjectConfigConflict,
    ProjectReadOnlyError,
    ProjectValidationError,
    apply_project_profile_update,
    build_project_overview,
    build_project_status_brief,
    build_project_timeline,
    project_config_revision,
    record_project_status_event,
)
from agentpack.learning.recommender import recommend_learning_topics
from agentpack.session.events import record_event
from agentpack.session.identity import repository_path
from agentpack.dashboard.terminal import TerminalEvent, TerminalSession, TerminalSessionManager
from agentpack.dashboard.v2 import (
    build_dashboard_v2_actions,
    build_dashboard_v2_action_inspection,
    build_dashboard_v2_agents,
    build_dashboard_v2_evidence,
    build_dashboard_v2_impact,
    build_dashboard_v2_payload,
)
from agentpack.dashboard.contracts import (
    DashboardV2ActionInspectionResponse,
    DashboardV2ActionRequest,
    DashboardV2ActionRunResponse,
    DashboardV2AgentOperationResponse,
    DashboardV2Error,
    DashboardV2Handoff,
    DashboardV2HandoffOperationRequest,
    ProjectEventRequest,
    ProjectProfileUpdateRequest,
)


DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8765


def _bounded_query_int(query: dict[str, list[str]], key: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(maximum, int(query.get(key, [str(default)])[0] or default)))
    except (TypeError, ValueError):
        return default


def _public_handoff(record) -> dict[str, Any]:
    payload = record.model_dump(mode="json")
    payload.pop("handoff_id", None)
    return payload


def _dashboard_v2_handoff(record) -> DashboardV2Handoff:
    return DashboardV2Handoff(
        name=record.name,
        status=record.status,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        source_provider=record.source.provider,
        source_session_id=record.source.session_id,
        target_provider=record.target_provider,
        target_session_id=record.target_session_id,
        task=record.report.task,
        summary=record.report.summary,
        next_action=record.report.next_action,
        claim_provider=record.claim.provider if record.claim else "",
        claim_session_id=record.claim.session_id if record.claim else "",
    )


def _dashboard_v2_error(message: str, *, kind: str = "server_error", retryable: bool = False, detail: str = "") -> dict[str, Any]:
    return DashboardV2Error(error=message, kind=kind, retryable=retryable, detail=detail).model_dump(mode="json")


def _handoff_error_kind(message: str) -> tuple[str, HTTPStatus, bool]:
    lowered = message.lower()
    if "different repository" in lowered or "diverge" in lowered or "source commit" in lowered:
        return "repository_mismatch", HTTPStatus.CONFLICT, False
    if "another session" in lowered or "already claimed" in lowered or "busy" in lowered:
        return "handoff_conflict", HTTPStatus.CONFLICT, True
    if "source worktree changed" in lowered or "stale" in lowered:
        return "stale_handoff", HTTPStatus.CONFLICT, True
    if "restricted to provider" in lowered or "restricted to a different" in lowered:
        return "permission_denied", HTTPStatus.FORBIDDEN, False
    return "handoff_conflict", HTTPStatus.CONFLICT, True


@dataclass
class DashboardServerState:
    root: Path
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    terminal: TerminalSessionManager = field(init=False)
    lock: threading.RLock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self.lock = threading.RLock()
        self.terminal = TerminalSessionManager(self.root, on_event=self._record_terminal_event)

    def payload(self) -> dict[str, Any]:
        with self.lock:
            root = self.root
        snapshot = build_project_dashboard_snapshot(root)
        graph = build_dashboard_graph(snapshot, root)
        dashboard_map = build_dashboard_map(snapshot, graph)
        action_history = read_action_history(root)
        return {
            "snapshot": snapshot.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
            "map": dashboard_map.model_dump(mode="json"),
            "action_history": [row.model_dump(mode="json") for row in action_history],
        }

    def home_payload(self) -> dict[str, Any]:
        with self.lock:
            root = self.root
        snapshot = build_project_home_snapshot(root)
        return {
            "snapshot": snapshot.model_dump(mode="json"),
            "graph": {"schema_version": 1, "root_id": "task:active", "summary": {}, "nodes": [], "edges": []},
            "map": {"schema_version": 1, "summary": {}, "districts": [], "buildings": [], "roads": [], "landmarks": [], "weather": []},
            "action_history": [],
        }

    def switch_root(self, path: str) -> dict[str, Any]:
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            raise ValueError("project path must be absolute")
        resolved = raw.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError("project path must be an existing directory")
        if not _valid_project_root(resolved):
            raise ValueError("project path must contain .git or .agentpack/config.toml")
        with self.lock:
            self.root = resolved
            self.terminal = TerminalSessionManager(resolved, on_event=self._record_terminal_event)
        try:
            register_project(resolved)
        except Exception:
            pass
        return self.payload()

    def _record_terminal_event(self, session: TerminalSession, event: TerminalEvent) -> None:
        status = event.status or session.status
        root = Path(repository_path(Path(session.cwd))).resolve()
        if event.type == "status" and status == "running":
            record_dashboard_action(
                root,
                action_id=session.id,
                session_id=session.id,
                command=session.command,
                cwd=session.cwd,
                status=status,
                confirmed=session.confirmed,
            )
        elif event.type in {"exit", "error"}:
            record_dashboard_action(
                root,
                action_id=session.id,
                session_id=session.id,
                command=session.command,
                cwd=session.cwd,
                status=status,
                confirmed=session.confirmed,
                returncode=event.returncode,
                duration_ms=session.duration_ms,
                output_summary=session.output_summary,
                follow_up_actions=_follow_up_actions(session.command, status),
            )
            check_kind = _dashboard_check_kind(session.command)
            if check_kind:
                blocking, advisory = _architecture_counts(session.output_summary) if check_kind == "architecture" else (0, 0)
                record_event(
                    root,
                    "check_completed",
                    {
                        "check_kind": check_kind,
                        "command": session.command,
                        "status": "passed" if event.returncode == 0 else "failed",
                        "returncode": event.returncode,
                        "git_sha": git.current_sha(root) or "",
                        "branch": git.current_branch(root) or "",
                        "summary": session.output_summary,
                        "blocking_violations": blocking,
                        "advisory_violations": advisory,
                        "evidence": [
                            {
                                "kind": "command",
                                "ref": check_kind,
                                "summary": session.output_summary[:500],
                            }
                        ],
                    },
                    source="dashboard",
                )


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], state: DashboardServerState) -> None:
        self.state = state
        super().__init__(server_address, DashboardRequestHandler)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"", "/"}:
            self._send_html(render_dashboard_shell(token=self.server.state.token))
            return
        if parsed.path == "/api/dashboard":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            detail = urllib.parse.parse_qs(parsed.query).get("detail", ["full"])[0]
            self._send_json(self.server.state.home_payload() if detail == "home" else self.server.state.payload())
            return
        if parsed.path == "/api/dashboard/v2":
            if not self._authorized(parsed):
                self._send_v2_auth_error()
                return
            detail = urllib.parse.parse_qs(parsed.query).get("detail", ["home"])[0]
            payload = build_dashboard_v2_payload(self.server.state.root, detail=detail)
            payload["action_history"] = [row.model_dump(mode="json") for row in read_action_history(self.server.state.root)]
            self._send_json(payload)
            return
        if parsed.path == "/api/learning/recommendations":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            scope = urllib.parse.parse_qs(parsed.query).get("scope", ["local"])[0]
            if scope not in {"local", "global"}:
                self._send_json({"error": "scope must be local or global"}, status=HTTPStatus.BAD_REQUEST)
                return
            recommendations = recommend_learning_topics(
                self.server.state.root,
                global_scope=scope == "global",
            )
            self._send_json(recommendations.model_dump(mode="json"))
            return
        if parsed.path == "/api/project/overview":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            query = urllib.parse.parse_qs(parsed.query)
            workspace = query.get("workspace", ["all"])[0]
            try:
                overview = build_project_overview(self.server.state.root, workspace=workspace)
            except ProjectValidationError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(overview.model_dump(mode="json"))
            return
        if parsed.path == "/api/project/timeline":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            query = urllib.parse.parse_qs(parsed.query)
            workspace = query.get("workspace", ["all"])[0]
            kind = query.get("kind", [""])[0].strip()
            if len(kind) > 64:
                self._send_json({"error": "kind must be at most 64 characters"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                timeline = build_project_timeline(
                    self.server.state.root,
                    workspace=workspace,
                    kind=kind,
                    limit=_bounded_query_int(query, "limit", 50, 200),
                )
            except ProjectValidationError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"timeline": [item.model_dump(mode="json") for item in timeline]})
            return
        if parsed.path == "/api/project/brief":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            mode = urllib.parse.parse_qs(parsed.query).get("mode", ["summary"])[0]
            try:
                brief = build_project_status_brief(self.server.state.root, mode=mode)
            except ProjectValidationError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(brief.model_dump(mode="json"))
            return
        if parsed.path == "/api/dashboard/v2/impact":
            if not self._authorized(parsed):
                self._send_v2_auth_error()
                return
            query = urllib.parse.parse_qs(parsed.query)
            payload = build_dashboard_v2_impact(
                self.server.state.root,
                query=query.get("query", [""])[0],
                relationship=query.get("relationship", [""])[0],
                language=query.get("language", [""])[0],
                confidence=query.get("confidence", [""])[0],
                limit=_bounded_query_int(query, "limit", 200, 500),
            )
            self._send_json(payload)
            return
        if parsed.path == "/api/dashboard/v2/agents":
            if not self._authorized(parsed):
                self._send_v2_auth_error()
                return
            self._send_json(build_dashboard_v2_agents(self.server.state.root))
            return
        if parsed.path == "/api/dashboard/v2/evidence":
            if not self._authorized(parsed):
                self._send_v2_auth_error()
                return
            self._send_json(build_dashboard_v2_evidence(self.server.state.root))
            return
        if parsed.path == "/api/dashboard/v2/actions":
            if not self._authorized(parsed):
                self._send_v2_auth_error()
                return
            self._send_json(build_dashboard_v2_actions(self.server.state.root))
            return
        if parsed.path in {"/api/map", "/api/graph", "/api/actions/history"}:
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            self._send_json(self._section_payload(parsed.path, parsed.query))
            return
        if parsed.path in {"/api/config", "/api/tasks", "/api/threads"}:
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            self._send_json(self._section_payload(parsed.path, parsed.query))
            return
        if parsed.path in {"/api/projects", "/api/project", "/api/project/tasks", "/api/project/analytics"} or parsed.path.startswith("/api/project/tasks/"):
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            if parsed.path.startswith("/api/project/tasks/"):
                task_id = parsed.path.removeprefix("/api/project/tasks/").strip("/").removesuffix("/timeline").strip("/")
                valid = task_event_is_in_scope(self.server.state.root, task_id) if parsed.path.endswith("/timeline") else get_project_task(self.server.state.root, task_id) is not None
                if not valid:
                    self._send_json({"error": "task not found"}, status=HTTPStatus.NOT_FOUND)
                    return
            self._send_json(self._section_payload(parsed.path, parsed.query))
            return
        if parsed.path.startswith("/api/terminal/") and parsed.path.endswith("/events"):
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            self._stream_terminal_events(parsed)
            return
        if parsed.path.startswith("/assets/"):
            self._serve_asset(parsed.path.removeprefix("/assets/"))
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not self._authorized(parsed):
            if parsed.path.startswith("/api/dashboard/v2/"):
                self._send_v2_auth_error()
            else:
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if parsed.path == "/api/project/profile":
            request = self._read_v2_model(ProjectProfileUpdateRequest)
            if request is None:
                return
            assert isinstance(request, ProjectProfileUpdateRequest)
            try:
                profile, duplicate = apply_project_profile_update(
                    self.server.state.root,
                    mutation_id=request.mutation_id,
                    expected_revision=request.expected_revision,
                    updates=request.profile.model_dump(mode="json", exclude_none=True),
                )
            except ProjectReadOnlyError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return
            except ProjectConfigConflict as exc:
                self._send_json(
                    {
                        "error": str(exc),
                        "config_revision": project_config_revision(self.server.state.root),
                        "profile": build_project_overview(self.server.state.root).profile.model_dump(mode="json"),
                    },
                    status=HTTPStatus.CONFLICT,
                )
                return
            except ProjectValidationError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(
                {
                    "profile": profile.model_dump(mode="json"),
                    "duplicate": duplicate,
                    "project_overview": build_project_overview(self.server.state.root).model_dump(mode="json"),
                }
            )
            return
        if parsed.path == "/api/project/events":
            request = self._read_v2_model(ProjectEventRequest)
            if request is None:
                return
            assert isinstance(request, ProjectEventRequest)
            try:
                event, duplicate = record_project_status_event(
                    self.server.state.root,
                    request.model_dump(mode="json"),
                )
            except ProjectReadOnlyError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return
            except ProjectValidationError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(
                {
                    "event": event,
                    "duplicate": duplicate,
                    "project_overview": build_project_overview(self.server.state.root).model_dump(mode="json"),
                }
            )
            return
        if parsed.path == "/api/commands/inspect":
            payload = self._read_json()
            command = str(payload.get("command") or "")
            cwd = str(payload.get("cwd") or "") or None
            inspection = self.server.state.terminal.inspect(command, cwd)
            self._send_json({"inspection": inspection.model_dump()})
            return
        if parsed.path == "/api/dashboard/v2/actions/run":
            request = self._read_v2_model(DashboardV2ActionRequest)
            if request is not None:
                self._run_typed_action(request.model_dump(mode="json", by_alias=True), v2=True)
            return
        if parsed.path == "/api/dashboard/v2/actions/inspect":
            request = self._read_v2_model(DashboardV2ActionRequest)
            if request is None:
                return
            payload = request.model_dump(mode="json", by_alias=True)
            action_id = request.action.strip()
            try:
                inspection = build_dashboard_v2_action_inspection(self.server.state.root, action_id, payload)
            except (DashboardActionError, ValueError) as exc:
                self._send_json(_dashboard_v2_error(str(exc), kind="invalid_action"), status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(DashboardV2ActionInspectionResponse(inspection=inspection).model_dump(mode="json"))
            return
        if parsed.path in {"/api/dashboard/v2/agents/resume", "/api/dashboard/v2/agents/release"}:
            request = self._read_v2_model(DashboardV2HandoffOperationRequest)
            if request is None:
                return
            name = request.name.strip()
            try:
                if parsed.path.endswith("/resume"):
                    record, warnings = accept_handoff(self.server.state.root, name)
                    result = DashboardV2AgentOperationResponse(
                        handoff=_dashboard_v2_handoff(record), warnings=warnings
                    ).model_dump(mode="json")
                else:
                    result = DashboardV2AgentOperationResponse(
                        handoff=_dashboard_v2_handoff(release_handoff(self.server.state.root, name))
                    ).model_dump(mode="json")
            except HandoffError as exc:
                kind, status, retryable = _handoff_error_kind(str(exc))
                self._send_json(_dashboard_v2_error(str(exc), kind=kind, retryable=retryable), status=status)
                return
            self._send_json(result)
            return
        if parsed.path == "/api/terminal/start":
            payload = self._read_json()
            command = str(payload.get("command") or "")
            cwd = str(payload.get("cwd") or "") or None
            confirmed = bool(payload.get("confirmed"))
            self._start_terminal_command(command, cwd=cwd, confirmed=confirmed)
            return
        if parsed.path == "/api/config/update":
            payload = self._read_json()
            try:
                config = update_dashboard_config(self.server.state.root, payload.get("updates") if "updates" in payload else payload)
            except DashboardActionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:
                self._send_json({"error": f"config update failed: {exc}"}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"config": config.model_dump(mode="json")})
            return
        if parsed.path == "/api/action/run":
            self._run_typed_action(self._read_json())
            return
        if parsed.path == "/api/projects/switch":
            payload = self._read_json()
            try:
                switched = self.server.state.switch_root(str(payload.get("path") or ""))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(switched)
            return
        if parsed.path == "/api/project/tasks":
            payload = self._read_json()
            title = str(payload.get("title") or payload.get("task") or "").strip()
            if not title:
                self._send_json({"error": "title is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            write_task_md(self.server.state.root, title)
            task = create_dashboard_task(self.server.state.root, title, description=str(payload.get("description") or ""), status=str(payload.get("status") or "todo"))
            self._send_json({"task": task.model_dump(mode="json"), "dashboard": self.server.state.payload()})
            return
        if parsed.path == "/api/project/tasks/update":
            payload = self._read_json()
            task_id = str(payload.get("task_id") or "")
            previous = get_project_task(self.server.state.root, task_id) if task_id else None
            task = update_task(self.server.state.root, task_id, title=payload.get("title"), status=payload.get("status")) if task_id else None
            if task is None:
                self._send_json({"error": "task not found"}, status=HTTPStatus.NOT_FOUND)
                return
            if task.status == "done" and (previous is None or previous.status != "done"):
                record_event(
                    self.server.state.root,
                    "task_completed",
                    {"task": task.title, "task_id": task.task_id, "summary": "Marked done in the dashboard."},
                    source="dashboard",
                )
            self._send_json({"task": task.model_dump(mode="json"), "dashboard": self.server.state.payload()})
            return
        if parsed.path == "/api/project/feedback":
            payload = self._read_json()
            task_id = str(payload.get("task_id") or "")
            value = str(payload.get("value") or "")
            if not task_id or value not in {"helped", "partly_helped", "missed_context", "not_sure"}:
                self._send_json({"error": "task_id and a valid feedback value are required"}, status=HTTPStatus.BAD_REQUEST)
                return
            run_id = str(payload.get("run_id") or "")
            feedback_id = "feedback-" + hashlib.sha256(f"{task_id}:{run_id}:{value}:{payload.get('note') or ''}".encode("utf-8")).hexdigest()[:20]
            feedback = record_feedback(
                self.server.state.root,
                DashboardFeedback(
                    feedback_id=feedback_id,
                    task_id=task_id,
                    run_id=run_id,
                    value=value,
                    note=str(payload.get("note") or "")[:500],
                    created_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._send_json({"feedback": feedback.model_dump(mode="json")})
            return
        typed_paths = {
            "/api/tasks/set": "set_task",
            "/api/tasks/clear": "clear_task",
            "/api/state/set": "set_state",
            "/api/threads/archive": "archive_thread",
            "/api/threads/prune": "prune_threads",
            "/api/context/refresh": "refresh_context",
            "/api/context/route": "route_context",
            "/api/integrations/repair": "repair_integration",
        }
        if parsed.path in typed_paths:
            payload = self._read_json()
            payload.setdefault("action", typed_paths[parsed.path])
            self._run_typed_action(payload)
            return
        if parsed.path.startswith("/api/terminal/") and parsed.path.endswith("/input"):
            session = self._session_from_path(parsed.path, suffix="/input")
            if session is None:
                self._send_error(HTTPStatus.NOT_FOUND, "terminal session not found")
                return
            payload = self._read_json()
            try:
                session.input(str(payload.get("data") or ""))
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.CONFLICT)
                return
            self._send_json({"ok": True})
            return
        if parsed.path.startswith("/api/terminal/") and parsed.path.endswith("/kill"):
            session = self._session_from_path(parsed.path, suffix="/kill")
            if session is None:
                self._send_error(HTTPStatus.NOT_FOUND, "terminal session not found")
                return
            session.kill()
            self._send_json({"ok": True})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def _authorized(self, parsed: urllib.parse.ParseResult) -> bool:
        token = self.headers.get("X-AgentPack-Token", "")
        if token == self.server.state.token:
            return True
        query = urllib.parse.parse_qs(parsed.query)
        return query.get("token", [""])[0] == self.server.state.token

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _read_v2_model(self, model: type[BaseModel]) -> BaseModel | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._send_json(_dashboard_v2_error("request body is required", kind="invalid_request"), status=HTTPStatus.BAD_REQUEST)
            return None
        try:
            raw = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(_dashboard_v2_error("request body must be valid JSON", kind="malformed_json"), status=HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(raw, dict):
            self._send_json(_dashboard_v2_error("request body must be an object", kind="invalid_request"), status=HTTPStatus.BAD_REQUEST)
            return None
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            detail = "; ".join(error["msg"] for error in exc.errors()[:5])
            self._send_json(_dashboard_v2_error("request validation failed", kind="invalid_request", detail=detail), status=HTTPStatus.BAD_REQUEST)
            return None

    def _section_payload(self, path: str, query_string: str = "") -> dict[str, Any]:
        project_path = path == "/api/project" or path.startswith("/api/project/")
        snapshot = build_project_home_snapshot(self.server.state.root) if project_path else build_project_dashboard_snapshot(self.server.state.root)
        if path == "/api/graph":
            query = urllib.parse.parse_qs(query_string)
            try:
                limit = int(query.get("limit", ["200"])[0] or "200")
            except (TypeError, ValueError):
                limit = 200
            graph = semantic_graph_summary(
                self.server.state.root,
                relationship=query.get("relationship", [""])[0],
                confidence=query.get("confidence", [""])[0],
                language=query.get("language", [""])[0],
                evidence_source=query.get("evidence_source", [""])[0],
                query=query.get("query", [""])[0],
                limit=limit,
            )
            return {"semantic_graph": graph.model_dump(mode="json")}
        if path == "/api/map":
            graph = build_dashboard_graph(snapshot, self.server.state.root)
            return {"map": build_dashboard_map(snapshot, graph).model_dump(mode="json")}
        if path == "/api/actions/history":
            return {"action_history": [row.model_dump(mode="json") for row in read_action_history(self.server.state.root)]}
        if path == "/api/config":
            return {"config": snapshot.config.model_dump(mode="json")}
        if path == "/api/tasks":
            return {"tasks": [row.model_dump(mode="json") for row in snapshot.task_control]}
        if path == "/api/threads":
            return {
                "threads": [row.model_dump(mode="json") for row in snapshot.thread_rows],
                "summary": snapshot.threads.model_dump(mode="json"),
            }
        if path == "/api/projects":
            return {
                "projects": [row.model_dump(mode="json") for row in snapshot.projects],
                "current_project": snapshot.project_record.model_dump(mode="json") if snapshot.project_record else None,
                "current_workspace": snapshot.workspace.model_dump(mode="json") if snapshot.workspace else None,
                "tasks": [row.model_dump(mode="json") for row in snapshot.project_tasks],
            }
        if path == "/api/project":
            return {
                "project": snapshot.project_record.model_dump(mode="json") if snapshot.project_record else None,
                "workspace": snapshot.workspace.model_dump(mode="json") if snapshot.workspace else None,
                "active_task": snapshot.active_task.model_dump(mode="json") if snapshot.active_task else None,
                "tasks": [row.model_dump(mode="json") for row in snapshot.project_tasks[:100]],
                "analytics": snapshot.analytics.model_dump(mode="json"),
                "unassigned_history_count": snapshot.unassigned_history_count,
            }
        if path == "/api/project/tasks":
            query = urllib.parse.parse_qs(query_string)
            status = query.get("status", [""])[0]
            try:
                limit = max(1, min(100, int(query.get("limit", ["50"])[0] or "50")))
            except (TypeError, ValueError):
                limit = 50
            tasks = snapshot.project_tasks
            if status in {"todo", "in_progress", "needs_attention", "done"}:
                tasks = [row for row in tasks if row.status == status]
            return {"tasks": [row.model_dump(mode="json") for row in tasks[:limit]], "workspace": snapshot.workspace.model_dump(mode="json") if snapshot.workspace else None}
        if path == "/api/project/analytics":
            query = urllib.parse.parse_qs(query_string)
            value = query.get("range", ["7d"])[0]
            return {"analytics": analytics_for_range(self.server.state.root, value).model_dump(mode="json")}
        if path.startswith("/api/project/tasks/") and path.endswith("/timeline"):
            task_id = path.removeprefix("/api/project/tasks/").removesuffix("/timeline").strip("/")
            query = urllib.parse.parse_qs(query_string)
            try:
                limit = max(1, min(100, int(query.get("limit", ["50"])[0] or "50")))
            except (TypeError, ValueError):
                limit = 50
            return {"timeline": [row.model_dump(mode="json") for row in task_timeline(self.server.state.root, task_id, limit=limit)]}
        if path.startswith("/api/project/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            detail = task_detail_payload(self.server.state.root, task_id, limit=100)
            if detail is None:
                return {"error": "task not found"}
            return detail
        return {}

    def _run_typed_action(self, payload: dict[str, Any], *, v2: bool = False) -> None:
        action_id = str(payload.get("action") or payload.get("action_id") or "")
        try:
            command = build_dashboard_action_command(action_id, payload)
        except DashboardActionError as exc:
            if v2:
                self._send_json(_dashboard_v2_error(str(exc), kind="invalid_action"), status=HTTPStatus.BAD_REQUEST)
            else:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._start_terminal_command(command, cwd=str(payload.get("cwd") or "") or None, confirmed=bool(payload.get("confirmed")), v2=v2)

    def _start_terminal_command(self, command: str, *, cwd: str | None = None, confirmed: bool = False, v2: bool = False) -> None:
        try:
            session = self.server.state.terminal.start(command, cwd=cwd, confirmed=confirmed)
        except PermissionError:
            inspection = self.server.state.terminal.inspect(command, cwd)
            payload = _dashboard_v2_error("confirmation required", kind="action_conflict") if v2 else {"error": "confirmation required", "command": command, "inspection": inspection.model_dump()}
            self._send_json(payload, status=HTTPStatus.CONFLICT)
            return
        except ValueError as exc:
            payload = _dashboard_v2_error(str(exc), kind="invalid_action") if v2 else {"error": str(exc), "command": command}
            self._send_json(payload, status=HTTPStatus.BAD_REQUEST)
            return
        payload = DashboardV2ActionRunResponse(session=session.summary(), command=command).model_dump(mode="json") if v2 else {"session": session.summary(), "command": command}
        self._send_json(payload)

    def _send_v2_auth_error(self) -> None:
        self._send_json(_dashboard_v2_error("invalid dashboard token", kind="unauthorized"), status=HTTPStatus.UNAUTHORIZED)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _serve_asset(self, relative: str) -> None:
        path = (DASHBOARD_APP_DIR / "assets" / relative).resolve()
        asset_root = (DASHBOARD_APP_DIR / "assets").resolve()
        if asset_root not in path.parents or not path.exists() or not path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "asset not found")
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_terminal_events(self, parsed: urllib.parse.ParseResult) -> None:
        session_id = parsed.path.removeprefix("/api/terminal/").removesuffix("/events").strip("/")
        session = self.server.state.terminal.get(session_id)
        if session is None:
            self._send_error(HTTPStatus.NOT_FOUND, "terminal session not found")
            return
        query = urllib.parse.parse_qs(parsed.query)
        seq = int(query.get("after", ["0"])[0] or "0")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        while True:
            events = session.wait_for_event(seq)
            for event in events:
                seq = event.seq
                data = json.dumps(event.model_dump(), sort_keys=True)
                self.wfile.write(f"id: {event.seq}\ndata: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
            if session.status in {"completed", "failed", "killed"} and not events:
                break

    def _session_from_path(self, path: str, *, suffix: str):
        session_id = path.removeprefix("/api/terminal/").removesuffix(suffix).strip("/")
        return self.server.state.terminal.get(session_id)


def create_dashboard_server(root: Path, *, host: str = DEFAULT_DASHBOARD_HOST, port: int = DEFAULT_DASHBOARD_PORT) -> DashboardHTTPServer:
    state = DashboardServerState(root=root)
    return DashboardHTTPServer((host, port), state)


def serve_dashboard(root: Path, *, host: str = DEFAULT_DASHBOARD_HOST, port: int = DEFAULT_DASHBOARD_PORT, open_browser: bool = False) -> str:
    server = create_dashboard_server(root, host=host, port=port)
    url = f"http://{host}:{server.server_port}/"
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return url


def _valid_project_root(path: Path) -> bool:
    return path.is_dir() and ((path / ".git").exists() or (path / ".agentpack" / "config.toml").exists())


def _dashboard_check_kind(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    command_parts = [part for part in parts if part]
    if "agentpack" not in command_parts and "agentpack.cli" not in command_parts:
        return ""
    joined = " ".join(command_parts)
    if re.search(r"\bagentpack(?:\.cli)?\s+finish\b", joined):
        return ""
    if re.search(r"\brelease-check\b", joined):
        return "release"
    if re.search(r"\barchitecture\b", joined):
        return "architecture"
    if re.search(r"\breview\b", joined):
        return "review"
    if re.search(r"\bdev-check\b", joined):
        return "development"
    return ""


def _architecture_counts(summary: str) -> tuple[int, int]:
    def count(label: str) -> int:
        match = re.search(rf"{label}[^0-9]*(\d+)", summary, re.IGNORECASE)
        return int(match.group(1)) if match else 0

    return count("blocking"), count("advisory")


def _follow_up_actions(command: str, status: str) -> list[str]:
    if status not in {"completed", "failed"}:
        return []
    if status == "failed":
        return ["doctor_all", "refresh_context"]
    if "agentpack guard" in command or "agentpack pack" in command:
        return ["next", "dev_check"]
    if "agentpack repair" in command or "agentpack install" in command:
        return ["doctor_all"]
    if "agentpack task" in command or "agentpack work" in command:
        return ["refresh_context", "next"]
    return []
