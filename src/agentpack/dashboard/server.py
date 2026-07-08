from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agentpack.core.project_index import register_project
from agentpack.dashboard.actions import DashboardActionError, build_dashboard_action_command, update_dashboard_config
from agentpack.dashboard.app_shell import DASHBOARD_APP_DIR, render_dashboard_shell
from agentpack.dashboard.collectors import build_project_dashboard_snapshot
from agentpack.dashboard.graph import build_dashboard_graph
from agentpack.dashboard.terminal import TerminalSessionManager


DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8765


@dataclass
class DashboardServerState:
    root: Path
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    terminal: TerminalSessionManager = field(init=False)
    lock: threading.RLock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self.lock = threading.RLock()
        self.terminal = TerminalSessionManager(self.root)

    def payload(self) -> dict[str, Any]:
        with self.lock:
            root = self.root
        snapshot = build_project_dashboard_snapshot(root)
        graph = build_dashboard_graph(snapshot, root)
        return {
            "snapshot": snapshot.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
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
            self.terminal = TerminalSessionManager(resolved)
        try:
            register_project(resolved)
        except Exception:
            pass
        return self.payload()


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
            self._send_json(self.server.state.payload())
            return
        if parsed.path in {"/api/config", "/api/tasks", "/api/threads"}:
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            self._send_json(self._section_payload(parsed.path))
            return
        if parsed.path == "/api/projects":
            if not self._authorized(parsed):
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            self._send_json(self._section_payload(parsed.path))
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

    def _section_payload(self, path: str) -> dict[str, Any]:
        snapshot = build_project_dashboard_snapshot(self.server.state.root)
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
            return {"projects": [row.model_dump(mode="json") for row in snapshot.projects]}
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
