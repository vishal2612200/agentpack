from __future__ import annotations

import os
import pty
import select
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


FORBIDDEN_SHELL_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<", "2>", "&"}
RISKY_AGENTPACK_SUBCOMMANDS = {
    "finish",
    "init",
    "install",
    "migrate",
    "monitor",
    "prepare-release",
    "release",
    "repair",
    "upgrade",
}
RISKY_FLAGS = {"--fix", "--force", "--global", "--repair-stale", "--refresh-context", "--post-inline-comments", "--yes"}
MAX_EVENTS = 1200


@dataclass(frozen=True)
class CommandInspection:
    command: str
    argv: list[str]
    cwd: str
    allowed: bool
    reason: str
    risky: bool = False
    risk_reasons: list[str] = field(default_factory=list)
    confirm_required: bool = False

    def model_dump(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "argv": self.argv,
            "cwd": self.cwd,
            "allowed": self.allowed,
            "reason": self.reason,
            "risky": self.risky,
            "risk_reasons": self.risk_reasons,
            "confirm_required": self.confirm_required,
        }


@dataclass
class TerminalEvent:
    seq: int
    type: str
    data: str = ""
    status: str = ""
    returncode: int | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "data": self.data,
            "status": self.status,
            "returncode": self.returncode,
        }


class TerminalSession:
    def __init__(
        self,
        inspection: CommandInspection,
        *,
        confirmed: bool = False,
        on_event: Callable[["TerminalSession", TerminalEvent], None] | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex
        self.inspection = inspection
        self.command = inspection.command
        self.cwd = inspection.cwd
        self.confirmed = confirmed
        self.on_event = on_event
        self.status = "starting"
        self.returncode: int | None = None
        self.started_at = time.time()
        self.finished_at: float | None = None
        self._events: list[TerminalEvent] = []
        self._seq = 0
        self._condition = threading.Condition()
        self._master_fd: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._thread = threading.Thread(target=self._run, name=f"agentpack-dashboard-terminal-{self.id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def input(self, data: str) -> None:
        if self._master_fd is None or self.status not in {"starting", "running"}:
            raise RuntimeError("terminal session is not running")
        os.write(self._master_fd, data.encode("utf-8", errors="replace"))

    def kill(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    def events_after(self, seq: int) -> list[TerminalEvent]:
        with self._condition:
            return [event for event in self._events if event.seq > seq]

    def wait_for_event(self, seq: int, timeout: float = 15.0) -> list[TerminalEvent]:
        deadline = time.time() + timeout
        with self._condition:
            while True:
                events = [event for event in self._events if event.seq > seq]
                if events or self.status in {"completed", "failed", "killed"}:
                    return events
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=remaining)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "status": self.status,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "confirmed": self.confirmed,
            "inspection": self.inspection.model_dump(),
        }

    def _run(self) -> None:
        master_fd: int | None = None
        slave_fd: int | None = None
        try:
            master_fd, slave_fd = pty.openpty()
            self._master_fd = master_fd
            self.status = "running"
            self._emit("status", status="running")
            self._process = subprocess.Popen(
                self.inspection.argv,
                cwd=self.cwd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
            os.close(slave_fd)
            slave_fd = None

            while True:
                eof = False
                ready, _, _ = select.select([master_fd], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        chunk = b""
                    if chunk:
                        self._emit("output", data=chunk.decode("utf-8", errors="replace"))
                    else:
                        eof = True
                if self._process.poll() is not None:
                    if eof:
                        break
                    drain_ready, _, _ = select.select([master_fd], [], [], 0)
                    if not drain_ready:
                        break

            self.returncode = self._process.wait()
            self.status = "completed" if self.returncode == 0 else "failed"
            self.finished_at = time.time()
            self._emit("exit", status=self.status, returncode=self.returncode)
        except Exception as exc:
            self.status = "failed"
            self.finished_at = time.time()
            self._emit("error", data=str(exc), status="failed")
        finally:
            if slave_fd is not None:
                try:
                    os.close(slave_fd)
                except OSError:
                    pass
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            self._master_fd = None

    def _emit(self, event_type: str, *, data: str = "", status: str = "", returncode: int | None = None) -> None:
        event: TerminalEvent
        with self._condition:
            self._seq += 1
            event = TerminalEvent(seq=self._seq, type=event_type, data=data, status=status, returncode=returncode)
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                self._events = self._events[-MAX_EVENTS:]
            self._condition.notify_all()
        if self.on_event is not None:
            self.on_event(self, event)


class TerminalSessionManager:
    def __init__(self, root: Path, *, on_event: Callable[[TerminalSession, TerminalEvent], None] | None = None) -> None:
        self.root = root.resolve()
        self.on_event = on_event
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def inspect(self, command: str, cwd: str | None = None) -> CommandInspection:
        return inspect_command(command, root=self.root, cwd=cwd)

    def start(self, command: str, *, cwd: str | None = None, confirmed: bool = False) -> TerminalSession:
        inspection = self.inspect(command, cwd)
        if not inspection.allowed:
            raise ValueError(inspection.reason)
        if inspection.confirm_required and not confirmed:
            raise PermissionError("confirmation required")
        session = TerminalSession(inspection, confirmed=confirmed, on_event=self.on_event)
        with self._lock:
            self._sessions[session.id] = session
        session.start()
        return session

    def get(self, session_id: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(session_id)


def inspect_command(command: str, *, root: Path, cwd: str | None = None) -> CommandInspection:
    clean = " ".join(str(command or "").strip().split())
    resolved_cwd, cwd_reason = _resolve_cwd(root, cwd)
    if cwd_reason:
        return CommandInspection(command=clean, argv=[], cwd=str(resolved_cwd), allowed=False, reason=cwd_reason)
    if not clean:
        return CommandInspection(command=clean, argv=[], cwd=str(resolved_cwd), allowed=False, reason="command is empty")
    if "$(" in clean or "`" in clean:
        return CommandInspection(command=clean, argv=[], cwd=str(resolved_cwd), allowed=False, reason="shell substitution is not allowed")
    try:
        argv = shlex.split(clean)
    except ValueError as exc:
        return CommandInspection(command=clean, argv=[], cwd=str(resolved_cwd), allowed=False, reason=f"command parse failed: {exc}")
    if any(token in FORBIDDEN_SHELL_TOKENS for token in argv):
        return CommandInspection(command=clean, argv=argv, cwd=str(resolved_cwd), allowed=False, reason="shell operators are not allowed")
    if not _is_agentpack_command(argv):
        return CommandInspection(
            command=clean,
            argv=argv,
            cwd=str(resolved_cwd),
            allowed=False,
            reason="only AgentPack commands are allowed from the dashboard",
        )

    risk_reasons = _risk_reasons(argv)
    return CommandInspection(
        command=clean,
        argv=argv,
        cwd=str(resolved_cwd),
        allowed=True,
        reason="AgentPack command allowed",
        risky=bool(risk_reasons),
        risk_reasons=risk_reasons,
        confirm_required=bool(risk_reasons),
    )


def _resolve_cwd(root: Path, cwd: str | None) -> tuple[Path, str]:
    base = root.resolve()
    if not cwd:
        candidate = base
    else:
        raw = Path(cwd).expanduser()
        candidate = raw if raw.is_absolute() else base / raw
        candidate = candidate.resolve()
    if not candidate.exists() or not candidate.is_dir():
        return candidate, "cwd does not exist or is not a directory"
    if candidate != base and base not in candidate.parents:
        return candidate, "cwd must be inside the project root"
    return candidate, ""


def _is_agentpack_command(argv: list[str]) -> bool:
    if not argv:
        return False
    if argv[0] == "agentpack":
        return True
    if len(argv) >= 3 and Path(argv[0]).name.startswith("python") and argv[1] == "-m" and argv[2] in {"agentpack", "agentpack.cli"}:
        return True
    if argv[0] in {"npx", "pnpm", "yarn", "bunx", "uvx"}:
        return any(_token_names_agentpack(token) for token in argv[1:4])
    if len(argv) >= 3 and argv[0] == "pipx" and argv[1] == "run":
        return _token_names_agentpack(argv[2])
    return False


def _token_names_agentpack(token: str) -> bool:
    return token in {"agentpack", "@vishal2612200/agentpack"} or token.endswith("/agentpack")


def _risk_reasons(argv: list[str]) -> list[str]:
    reasons: list[str] = []
    subcommand = _agentpack_subcommand(argv)
    if subcommand in RISKY_AGENTPACK_SUBCOMMANDS:
        reasons.append(f"`agentpack {subcommand}` may modify local configuration, installed integrations, or release state")
    for token in argv:
        if token in RISKY_FLAGS:
            reasons.append(f"`{token}` can modify files or external state")
    return _dedupe(reasons)


def _agentpack_subcommand(argv: list[str]) -> str:
    if not argv:
        return ""
    if argv[0] == "agentpack" and len(argv) > 1:
        return argv[1]
    if len(argv) >= 4 and Path(argv[0]).name.startswith("python") and argv[1] == "-m" and argv[2] in {"agentpack", "agentpack.cli"}:
        return argv[3]
    for index, token in enumerate(argv[:-1]):
        if _token_names_agentpack(token):
            return argv[index + 1]
    return ""


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
