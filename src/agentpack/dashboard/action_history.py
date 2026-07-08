from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentpack.dashboard.models import ActionHistoryRow

HISTORY_FILE = ".agentpack/dashboard-actions.jsonl"
MAX_HISTORY_ROWS = 80


def record_dashboard_action(
    root: Path,
    *,
    action_id: str,
    command: str,
    cwd: str,
    status: str,
    confirmed: bool = False,
    returncode: int | None = None,
    session_id: str = "",
    duration_ms: int | None = None,
    output_summary: str = "",
    follow_up_actions: list[str] | None = None,
) -> None:
    path = root / HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    row = ActionHistoryRow(
        action_id=action_id,
        label=_label_from_command(command),
        command=command,
        cwd=cwd,
        status=status,
        started_at=now if status in {"starting", "running"} else "",
        ended_at=now if status in {"completed", "failed", "killed"} else "",
        returncode=returncode,
        confirmed=confirmed,
        source="dashboard",
        session_id=session_id or action_id,
        duration_ms=duration_ms,
        output_summary=output_summary[:500],
        follow_up_actions=follow_up_actions or [],
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n")


def read_action_history(root: Path, *, limit: int = MAX_HISTORY_ROWS) -> list[ActionHistoryRow]:
    rows = [*_read_dashboard_history(root), *_read_agentpack_session_events(root)]
    merged: dict[str, ActionHistoryRow] = {}
    for row in rows:
        current = merged.get(row.action_id)
        if current is None:
            merged[row.action_id] = row
            continue
        merged[row.action_id] = _merge_action_rows(current, row)
    return sorted(merged.values(), key=_sort_key, reverse=True)[:limit]


def _read_dashboard_history(root: Path) -> list[ActionHistoryRow]:
    path = root / HISTORY_FILE
    if not path.exists():
        return []
    rows: list[ActionHistoryRow] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(ActionHistoryRow.model_validate(data))
    return rows


def _read_agentpack_session_events(root: Path) -> list[ActionHistoryRow]:
    path = root / ".agentpack" / "session-events.jsonl"
    if not path.exists():
        return []
    rows: list[ActionHistoryRow] = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()[-120:]):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        event_type = str(data.get("type") or data.get("event_type") or "")
        task = str(data.get("task") or data.get("summary") or event_type or "AgentPack event")
        timestamp = str(data.get("timestamp") or data.get("recorded_at") or data.get("started_at") or "")
        ended_at = str(data.get("completed_at") or data.get("ended_at") or "")
        rows.append(
            ActionHistoryRow(
                action_id=f"session-event:{timestamp}:{index}",
                label=event_type or "AgentPack event",
                command=task,
                cwd=str(root),
                status=str(data.get("status") or event_type or "recorded"),
                started_at=timestamp,
                ended_at=ended_at,
                source="agentpack",
                duration_ms=_duration_ms(timestamp, ended_at),
                output_summary=str(data.get("result") or data.get("detail") or data.get("summary") or "")[:500],
            )
        )
    return rows


def _merge_action_rows(left: ActionHistoryRow, right: ActionHistoryRow) -> ActionHistoryRow:
    data = left.model_dump(mode="json")
    incoming = right.model_dump(mode="json")
    for key, value in incoming.items():
        if key == "returncode" and value is not None:
            data[key] = value
        elif isinstance(value, list):
            if value:
                data[key] = value
        elif isinstance(value, dict):
            if value:
                data[key] = value
        elif value not in {"", None, False}:
            data[key] = value
    data["started_at"] = left.started_at or right.started_at
    computed_duration = _duration_ms(data.get("started_at") or "", data.get("ended_at") or "")
    data["duration_ms"] = computed_duration if computed_duration is not None else data.get("duration_ms")
    return ActionHistoryRow.model_validate(data)


def _sort_key(row: ActionHistoryRow) -> str:
    return row.ended_at or row.started_at


def _label_from_command(command: str) -> str:
    parts = command.split()
    if len(parts) >= 2 and parts[0].endswith("agentpack"):
        return f"agentpack {parts[1]}"
    if len(parts) >= 4 and parts[1:3] == ["-m", "agentpack.cli"]:
        return f"agentpack {parts[3]}"
    return parts[0] if parts else "dashboard action"


def _duration_ms(started_at: str, ended_at: str) -> int | None:
    if not started_at or not ended_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds() * 1000))
