from __future__ import annotations

import json
import hashlib
import mimetypes
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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
from agentpack.session.events import record_event
from agentpack.dashboard.terminal import TerminalEvent, TerminalSession, TerminalSessionManager
from agentpack.dashboard.v2 import (
    build_dashboard_v2_actions,
    build_dashboard_v2_action_inspection,
    build_dashboard_v2_agents,
    build_dashboard_v2_evidence,
    build_dashboard_v2_impact,
    build_dashboard_v2_payload,
)
from agentpack.dashboard.contracts import DashboardV2Error


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


def _dashboard_v2_error(message: str, *, kind: str = "server_error", retryable: bool = False, detail: str = "") -> dict[str, Any]:
    return DashboardV2Error(error=message, kind=kind, retryable=retryable, detail=detail).model_dump(mode="json")


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
        root = Path(session.cwd).resolve()
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
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            detail = urllib.parse.parse_qs(parsed.query).get("detail", ["home"])[0]
            payload = build_dashboard_v2_payload(self.server.state.root, detail=detail)
            payload["action_history"] = [row.model_dump(mode="json") for row in read_action_history(self.server.state.root)]
            self._send_json(payload)
            return
        if parsed.path == "/api/dashboard/v2/impact":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
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
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            self._send_json(build_dashboard_v2_agents(self.server.state.root))
            return
        if parsed.path == "/api/dashboard/v2/evidence":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            self._send_json(build_dashboard_v2_evidence(self.server.state.root))
            return
        if parsed.path == "/api/dashboard/v2/actions":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
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
            self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if parsed.path == "/api/commands/inspect":
            payload = self._read_json()
            command = str(payload.get("command") or "")
            cwd = str(payload.get("cwd") or "") or None
            inspection = self.server.state.terminal.inspect(command, cwd)
            self._send_json({"inspection": inspection.model_dump()})
            return
        if parsed.path == "/api/dashboard/v2/actions/run":
            payload = self._read_json()
            self._run_typed_action(payload)
            return
        if parsed.path == "/api/dashboard/v2/actions/inspect":
            payload = self._read_json()
            action_id = str(payload.get("action") or payload.get("action_id") or "").strip()
            if not action_id:
                self._send_json(_dashboard_v2_error("action is required", kind="invalid_request"), status=HTTPStatus.BAD_REQUEST)
                return
            try:
                inspection = build_dashboard_v2_action_inspection(self.server.state.root, action_id, payload)
            except (DashboardActionError, ValueError) as exc:
                self._send_json(_dashboard_v2_error(str(exc), kind="invalid_action"), status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"inspection": inspection})
            return
        if parsed.path in {"/api/dashboard/v2/agents/resume", "/api/dashboard/v2/agents/release"}:
            payload = self._read_json()
            name = str(payload.get("name") or "").strip()
            if not name:
                self._send_json(_dashboard_v2_error("handoff name is required", kind="invalid_request"), status=HTTPStatus.BAD_REQUEST)
                return
            try:
                if parsed.path.endswith("/resume"):
                    record, warnings = accept_handoff(self.server.state.root, name)
                    result = {"handoff": _public_handoff(record), "warnings": warnings}
                else:
                    result = {"handoff": _public_handoff(release_handoff(self.server.state.root, name))}
            except HandoffError as exc:
                self._send_json(_dashboard_v2_error(str(exc), kind="handoff_conflict"), status=HTTPStatus.CONFLICT)
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

    def _run_typed_action(self, payload: dict[str, Any]) -> None:
        action_id = str(payload.get("action") or payload.get("action_id") or "")
        try:
            command = build_dashboard_action_command(action_id, payload)
        except DashboardActionError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._start_terminal_command(command, confirmed=bool(payload.get("confirmed")))

    def _start_terminal_command(self, command: str, *, cwd: str | None = None, confirmed: bool = False) -> None:
        try:
            session = self.server.state.terminal.start(command, cwd=cwd, confirmed=confirmed)
        except PermissionError:
            inspection = self.server.state.terminal.inspect(command, cwd)
            self._send_json(
                {"error": "confirmation required", "command": command, "inspection": inspection.model_dump()},
                status=HTTPStatus.CONFLICT,
            )
            return
        except ValueError as exc:
            self._send_json({"error": str(exc), "command": command}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json({"session": session.summary(), "command": command})

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
