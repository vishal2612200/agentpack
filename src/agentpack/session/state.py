from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
import uuid

from agentpack.session.identity import session_id as migrated_session_id

AGENTPACK_DIR = ".agentpack"
SESSION_FILE = ".agentpack/session.json"
TASK_FILE = ".agentpack/task.md"
CONTEXT_FILE = ".agentpack/context.md"
COMPACT_FILE = ".agentpack/context.compact.md"
ACTIVITY_LOG = ".agentpack/activity.log"

TASK_FILE_TEMPLATE = """\
# Current Task

Write or update the current coding task here.

AgentPack will refresh context based on this task.
"""


@dataclass
class SessionState:
    active: bool
    started_at: Optional[str]
    agent: str = "generic"
    mode: str = "balanced"
    session_id: str = ""
    external_thread_ids: list[str] = field(default_factory=list)
    context_file: str = CONTEXT_FILE
    compact_context_file: str = COMPACT_FILE
    task_file: str = TASK_FILE
    last_refresh_at: Optional[str] = None
    last_task_hash: str = ""
    last_git_hash: str = ""
    last_resolved_agent: str = ""
    refresh_count: int = 0


def load_session(root: Path) -> Optional[SessionState]:
    """Load session state from .agentpack/session.json. Returns None if missing."""
    session_path = root / SESSION_FILE
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
        state = SessionState(
            active=data.get("active", False),
            started_at=data.get("started_at"),
            agent=data.get("agent", "generic"),
            mode=data.get("mode", "balanced"),
            session_id=data.get("session_id", ""),
            external_thread_ids=[str(item) for item in data.get("external_thread_ids", []) if item]
            if isinstance(data.get("external_thread_ids", []), list)
            else [],
            context_file=data.get("context_file", CONTEXT_FILE),
            compact_context_file=data.get("compact_context_file", COMPACT_FILE),
            task_file=data.get("task_file", TASK_FILE),
            last_refresh_at=data.get("last_refresh_at"),
            last_task_hash=data.get("last_task_hash", ""),
            last_git_hash=data.get("last_git_hash", ""),
            last_resolved_agent=data.get("last_resolved_agent", ""),
            refresh_count=data.get("refresh_count", 0),
        )
        if not state.session_id:
            state.session_id = migrated_session_id(root)
            if state.session_id:
                save_session(root, state)
        return state
    except FileNotFoundError:
        return None


def save_session(root: Path, state: SessionState) -> None:
    """Write session state to .agentpack/session.json."""
    session_path = root / SESSION_FILE
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(asdict(state), indent=2, default=str),
        encoding="utf-8",
    )


def create_session(root: Path, agent: str, mode: str) -> SessionState:
    """Create a new active session, write session.json, create task.md if missing."""
    (root / AGENTPACK_DIR).mkdir(parents=True, exist_ok=True)

    task_path = root / TASK_FILE
    if not task_path.exists():
        task_path.write_text(TASK_FILE_TEMPLATE, encoding="utf-8")

    state = SessionState(
        active=True,
        started_at=datetime.now(timezone.utc).isoformat(),
        agent=agent,
        mode=mode,
        session_id="session-" + uuid.uuid4().hex[:20],
        external_thread_ids=[],
    )
    save_session(root, state)
    from agentpack.session.events import record_event

    record_event(root, "session_started", {"agent": agent}, source="session")
    return state


def stop_session(root: Path) -> None:
    """Mark the active session as inactive and update session.json."""
    state = load_session(root)
    if state is None:
        return
    state.active = False
    save_session(root, state)
    from agentpack.session.events import record_event

    record_event(root, "session_stopped", {"agent": state.agent}, source="session")


def log_activity(root: Path, message: str) -> None:
    """Append a timestamped line to .agentpack/activity.log."""
    log_path = root / ACTIVITY_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {message}\n")
