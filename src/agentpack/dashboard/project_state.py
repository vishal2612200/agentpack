from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agentpack.core import git
from agentpack.core.task_freshness import read_task_md
from agentpack.core.thread_context import read_task_status
from agentpack.session.events import read_events
from agentpack.dashboard.models import (
    DashboardAnalytics,
    DashboardFeedback,
    DashboardProjectRecord,
    DashboardSnapshot,
    DashboardTaskRecord,
    DashboardTaskRun,
    DashboardTimelineEvent,
    DashboardWorkspaceRecord,
    ContextHealth,
    ProjectInfo,
    TaskControlRow,
    TaskInfo,
)


STATE_VERSION = 1
STATE_DIRNAME = "dashboard-state"
MAX_TASKS = 100
MAX_RUNS = 200
MAX_FEEDBACK = 200


def state_dir(root: Path) -> Path:
    return root / ".agentpack" / STATE_DIRNAME


def build_project_home_snapshot(root: Path) -> DashboardSnapshot:
    root = root.resolve()
    task_text = read_task_md(root) or ""
    task_state = read_task_status(root)
    snapshot = DashboardSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        project=ProjectInfo(
            name=root.name or str(root),
            path=str(root),
            branch=git.current_branch(root) or "",
            git_sha=(git.current_sha(root) or "")[:12],
        ),
        task=TaskInfo(text=task_text, state=task_state or "unknown"),
        context=ContextHealth(status="unknown"),
        task_control=[
            TaskControlRow(
                scope="global",
                task=task_text,
                task_path=".agentpack/task.md",
                state=task_state or "unknown",
                state_path=".agentpack/task_state.md",
                exists=bool(task_text) or (root / ".agentpack" / "task_state.md").exists(),
            )
        ] if task_text else [],
    )
    state = sync_dashboard_state(root, snapshot)
    snapshot.project_record = state["project"]
    snapshot.workspace = state["workspace"]
    snapshot.project_tasks = state["tasks"]
    snapshot.active_task = state["active_task"]
    snapshot.task_runs = state["runs"]
    snapshot.task_timeline = state["timeline"]
    snapshot.dashboard_feedback = state["feedback"]
    snapshot.analytics = state["analytics"]
    snapshot.unassigned_history_count = int(state["unassigned_history_count"])
    return snapshot


def load_dashboard_state(root: Path) -> tuple[DashboardProjectRecord | None, DashboardWorkspaceRecord | None, list[DashboardTaskRecord], list[DashboardTaskRun], list[DashboardFeedback]]:
    directory = state_dir(root)
    project = _load_model(directory / "project.json", DashboardProjectRecord)
    workspace = _load_model(directory / "workspace.json", DashboardWorkspaceRecord)
    tasks = _load_models(directory / "tasks", DashboardTaskRecord)
    runs = _load_models(directory / "runs", DashboardTaskRun)
    feedback = _load_jsonl(directory / "feedback.jsonl", DashboardFeedback)
    return project, workspace, tasks[-MAX_TASKS:], runs[-MAX_RUNS:], feedback[-MAX_FEEDBACK:]


def sync_dashboard_state(root: Path, snapshot: DashboardSnapshot) -> dict[str, Any]:
    root = root.resolve()
    now = datetime.now(timezone.utc).isoformat()
    project_id = _project_id(root)
    workspace_id = _workspace_id(project_id, root)
    previous_project, previous_workspace, tasks, runs, feedback = load_dashboard_state(root)
    session_events = read_events(root, limit=MAX_RUNS)

    project = DashboardProjectRecord(
        project_id=project_id,
        name=snapshot.project.name,
        repository_path=_repository_path(root),
        created_at=previous_project.created_at if previous_project else now,
        updated_at=now,
    )
    workspace = DashboardWorkspaceRecord(
        workspace_id=workspace_id,
        project_id=project_id,
        path=str(root),
        branch=snapshot.project.branch,
        git_sha=snapshot.project.git_sha,
        is_current=True,
        updated_at=now,
    )

    by_key = {(item.title, item.workspace_id): item for item in tasks}
    current_task: DashboardTaskRecord | None = None
    for row in snapshot.task_control:
        title = row.task.strip()
        if not title:
            continue
        thread_ids = [row.thread_id] if row.thread_id else []
        key = (title, workspace_id)
        existing = by_key.get(key)
        status = _dashboard_status(row.state or row.status, snapshot.context.status)
        task = existing or DashboardTaskRecord(
            task_id=_task_id(project_id, workspace_id, title, ""),
            project_id=project_id,
            workspace_id=workspace_id,
            title=title,
            created_at=now,
            imported=True,
        )
        task.status = status
        task.updated_at = now
        task.thread_ids = sorted(set(task.thread_ids + thread_ids))
        task.source_paths = sorted(set(task.source_paths + ([row.task_path] if row.task_path else [])))
        task.active = title == snapshot.task.text.strip() and (row.thread_id or "") == (snapshot.task.thread_id or "")
        by_key[key] = task
        if task.active:
            current_task = task

    if snapshot.task.text.strip() and current_task is None:
        title = snapshot.task.text.strip()
        current_task = DashboardTaskRecord(
            task_id=_task_id(project_id, workspace_id, title, snapshot.task.thread_id or ""),
            project_id=project_id,
            workspace_id=workspace_id,
            title=title,
            status=_dashboard_status(snapshot.task.state, snapshot.context.status),
            created_at=now,
            updated_at=now,
            thread_ids=[snapshot.task.thread_id] if snapshot.task.thread_id else [],
            source_paths=[".agentpack/task.md"],
            active=True,
            imported=True,
        )
        by_key[(title, workspace_id)] = current_task

    for task in by_key.values():
        if task.workspace_id == workspace_id:
            task.active = bool(current_task and task.task_id == current_task.task_id)
    tasks = sorted(by_key.values(), key=lambda item: (not item.active, item.updated_at, item.title), reverse=True)[-MAX_TASKS:]

    if current_task and snapshot.context.generated_at:
        pack_event = _matching_pack_event(session_events, current_task.title, snapshot.context)
        run_id = _run_id(current_task.task_id, snapshot.context.generated_at, snapshot.context.source_command)
        if not any(item.run_id == run_id for item in runs):
            selected = [item.path for item in snapshot.selected_files]
            omitted = [item.path for item in snapshot.task_map if item.path not in selected]
            checks = sorted({test for item in snapshot.task_map for test in item.tests_to_run})
            run = DashboardTaskRun(
                run_id=run_id,
                task_id=current_task.task_id,
                session_id=_session_id(project_id, workspace_id, pack_event) if pack_event else "",
                agent=str(pack_event.get("agent") or ""),
                started_at=snapshot.context.generated_at,
                ended_at=snapshot.context.generated_at,
                status="completed" if snapshot.context.status == "fresh" else "needs_attention",
                event_ids=[_event_id(pack_event)] if pack_event else [],
                context_path=str(pack_event.get("context_path") or ""),
                citation_manifest_path=str(pack_event.get("citation_manifest_path") or ""),
                issue_references=_string_list(pack_event.get("issue_references")),
                issue_reference_details=[item for item in pack_event.get("issue_reference_details") or [] if isinstance(item, dict)][:20],
                selected_files=selected[:100],
                omitted_files=omitted[:100],
                checks=checks[:100],
                packed_tokens=snapshot.context.packed_tokens,
                raw_tokens=snapshot.context.raw_tokens,
                saving_pct=snapshot.context.saving_pct,
                unresolved_edges=snapshot.semantic_graph.unresolved_count,
                evidence_refs=[snapshot.context.source_command, snapshot.context.stale_reason],
            )
            runs.append(run)
            current_task.last_run_id = run_id
            current_task.updated_at = now
    runs = sorted(runs, key=lambda item: item.started_at)[-MAX_RUNS:]
    timeline = _build_task_timeline(
        [event for event in session_events if event.get("task_id") == (current_task.task_id if current_task else "")],
        limit=100,
    )

    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    _write_model(directory / "project.json", project)
    _write_model(directory / "workspace.json", workspace)
    for task in tasks:
        _write_model(directory / "tasks" / f"{task.task_id}.json", task)
    for run in runs:
        _write_model(directory / "runs" / f"{run.run_id}.json", run)

    analytics = _analytics(project_id, workspace_id, tasks, runs, feedback, days=7)
    task_runs = [item for item in runs if current_task and item.task_id == current_task.task_id]
    return {
        "project": project,
        "workspace": workspace,
        "tasks": [item for item in tasks if item.project_id == project_id and item.workspace_id == workspace_id],
        "active_task": current_task,
        "runs": task_runs[-50:],
        "timeline": timeline,
        "feedback": [item for item in feedback if current_task and item.task_id == current_task.task_id],
        "analytics": analytics,
        "unassigned_history_count": max(0, len(snapshot.task_history) - len(tasks)),
    }


def record_feedback(root: Path, feedback: DashboardFeedback) -> DashboardFeedback:
    path = state_dir(root) / "feedback.jsonl"
    existing = _load_jsonl(path, DashboardFeedback)
    if not any(item.feedback_id == feedback.feedback_id for item in existing):
        existing.append(feedback)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(path, existing[-MAX_FEEDBACK:])
    return feedback


def analytics_for_range(root: Path, value: str = "7d") -> DashboardAnalytics:
    project, workspace, tasks, runs, feedback = load_dashboard_state(root.resolve())
    if project is None or workspace is None:
        return DashboardAnalytics(range="30d" if value == "30d" else "7d", available=False, unavailable_reason="No task or run evidence has been recorded yet.")
    return _analytics(project.project_id, workspace.workspace_id, tasks, runs, feedback, days=30 if value == "30d" else 7)


def task_timeline(root: Path, task_id: str, *, limit: int = 100) -> list[DashboardTimelineEvent]:
    """Return bounded, normalized event history for one project-scoped task."""
    bounded_limit = max(1, min(100, int(limit)))
    events = read_events(root.resolve(), limit=MAX_RUNS * 2)
    return _build_task_timeline([event for event in events if event.get("task_id") == task_id], limit=bounded_limit)


def create_dashboard_task(root: Path, title: str, *, description: str = "", status: str = "todo") -> DashboardTaskRecord:
    root = root.resolve()
    project_id = _project_id(root)
    workspace_id = _workspace_id(project_id, root)
    project, workspace, tasks, _runs, _feedback = load_dashboard_state(root)
    now = datetime.now(timezone.utc).isoformat()
    task = DashboardTaskRecord(
        task_id=_task_id(project_id, workspace_id, title, ""),
        project_id=project_id,
        workspace_id=workspace_id,
        title=title.strip(),
        description=description.strip(),
        status=status if status in {"todo", "in_progress", "needs_attention", "done"} else "todo",
        created_at=now,
        updated_at=now,
        source_paths=[".agentpack/task.md"],
        active=True,
    )
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    if project is None:
        project = DashboardProjectRecord(project_id=project_id, name=root.name or str(root), repository_path=_repository_path(root), created_at=now, updated_at=now)
        _write_model(directory / "project.json", project)
    if workspace is None:
        workspace = DashboardWorkspaceRecord(workspace_id=workspace_id, project_id=project_id, path=str(root), is_current=True, updated_at=now)
        _write_model(directory / "workspace.json", workspace)
    _write_model(directory / "tasks" / f"{task.task_id}.json", task)
    return task


def update_task(root: Path, task_id: str, *, title: str | None = None, status: str | None = None) -> DashboardTaskRecord | None:
    directory = state_dir(root) / "tasks"
    task = _load_model(directory / f"{task_id}.json", DashboardTaskRecord)
    if task is None:
        return None
    if title is not None and title.strip():
        task.title = title.strip()
    if status in {"todo", "in_progress", "needs_attention", "done"}:
        task.status = status
    task.updated_at = datetime.now(timezone.utc).isoformat()
    _write_model(directory / f"{task.task_id}.json", task)
    return task


def _analytics(project_id: str, workspace_id: str, tasks: list[DashboardTaskRecord], runs: list[DashboardTaskRun], feedback: list[DashboardFeedback], *, days: int = 7) -> DashboardAnalytics:
    scoped_tasks = [item for item in tasks if item.project_id == project_id and item.workspace_id == workspace_id]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    scoped_runs = [item for item in runs if _parse_time(item.started_at) is None or _parse_time(item.started_at) >= cutoff]
    if not scoped_tasks and not scoped_runs:
        return DashboardAnalytics(range="30d" if days == 30 else "7d", available=False, unavailable_reason="No task or run evidence has been recorded yet.")
    packs = len(scoped_runs)
    average = sum(item.saving_pct for item in scoped_runs) / packs if packs else 0.0
    feedback_counts: dict[str, int] = {}
    for item in feedback:
        feedback_counts[item.value] = feedback_counts.get(item.value, 0) + 1
    return DashboardAnalytics(
        range="30d" if days == 30 else "7d",
        available=True,
        tasks_total=len(scoped_tasks),
        tasks_completed=sum(item.status == "done" for item in scoped_tasks),
        runs_total=len(scoped_runs),
        context_packs=packs,
        files_selected=sum(len(item.selected_files) for item in scoped_runs),
        files_omitted=sum(len(item.omitted_files) for item in scoped_runs),
        packed_tokens=sum(item.packed_tokens for item in scoped_runs),
        raw_tokens=sum(item.raw_tokens for item in scoped_runs),
        average_saving_pct=round(average, 1),
        checks_total=sum(len(item.checks) for item in scoped_runs),
        unresolved_edges=sum(item.unresolved_edges for item in scoped_runs),
        feedback_counts=feedback_counts,
        evidence=sorted({ref for item in scoped_runs for ref in item.evidence_refs if ref}),
    )


def _dashboard_status(state: str, context_status: str) -> str:
    value = (state or "").lower()
    if value == "done":
        return "done"
    if value == "blocked" or context_status in {"stale", "missing"}:
        return "needs_attention"
    if value in {"in_progress", "active"}:
        return "in_progress"
    return "todo"


def _project_id(root: Path) -> str:
    identity = _git_common_dir(root) or _repository_path(root)
    return "project-" + _digest(identity)


def _workspace_id(project_id: str, root: Path) -> str:
    return "workspace-" + _digest(f"{project_id}:{root.resolve()}")


def _repository_path(root: Path) -> str:
    command = ["git", "rev-parse", "--show-toplevel"]
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    return str(Path(result.stdout.strip()).resolve()) if result and result.returncode == 0 and result.stdout.strip() else str(root.resolve())


def _git_common_dir(root: Path) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if not result or result.returncode != 0 or not result.stdout.strip():
        return ""
    path = Path(result.stdout.strip())
    return str((root / path).resolve() if not path.is_absolute() else path.resolve())


def _task_id(project_id: str, workspace_id: str, title: str, thread_id: str) -> str:
    return "task-" + _digest(f"{project_id}:{workspace_id}:{thread_id}:{title.strip()}")


def _run_id(task_id: str, generated_at: str, source: str) -> str:
    return "run-" + _digest(f"{task_id}:{generated_at}:{source}")


def _session_id(project_id: str, workspace_id: str, event: dict[str, Any]) -> str:
    if event.get("session_id"):
        return str(event["session_id"])
    agent = str(event.get("agent") or "")
    context_path = str(event.get("context_path") or "")
    timestamp = str(event.get("timestamp") or "")
    return "session-" + _digest(f"{project_id}:{workspace_id}:{agent}:{context_path}:{timestamp[:10]}")


def _event_id(event: dict[str, Any]) -> str:
    if event.get("event_id"):
        return str(event["event_id"])
    return "event-" + _digest("|".join(str(event.get(key) or "") for key in ("type", "timestamp", "task", "context_path")))


def _matching_pack_event(events: list[dict[str, Any]], task: str, _context: ContextHealth) -> dict[str, Any]:
    task = task.strip()
    for event in reversed(events):
        if event.get("type") != "pack" and event.get("event_type") != "context_prepared":
            continue
        if task and str(event.get("task") or "").strip() != task:
            continue
        return event
    return {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _build_task_timeline(events: list[dict[str, Any]], *, limit: int) -> list[DashboardTimelineEvent]:
    labels = {
        "session_started": "Work session started",
        "task_started": "Task started",
        "context_prepared": "AI context prepared",
        "pack": "AI context prepared",
        "memory_recorded": "Memory recorded",
        "check_completed": "Checks completed",
        "github_reference_attached": "GitHub reference attached",
        "task_completed": "Task completed",
        "session_stopped": "Work session ended",
    }
    rows: list[DashboardTimelineEvent] = []
    for event in events[-limit:]:
        event_type = str(event.get("event_type") or event.get("type") or "event")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        summary = str(payload.get("summary") or payload.get("status") or "")
        rows.append(
            DashboardTimelineEvent(
                event_id=_event_id(event),
                event_type=event_type,
                label=labels.get(event_type, event_type.replace("_", " ").capitalize()),
                occurred_at=str(event.get("occurred_at") or event.get("timestamp") or ""),
                project_id=str(event.get("project_id") or ""),
                workspace_id=str(event.get("workspace_id") or ""),
                task_id=str(event.get("task_id") or ""),
                session_id=_session_id("", "", event),
                agent=str(event.get("agent") or ""),
                source=str(event.get("source") or "legacy"),
                summary=summary[:240],
                context_path=str(payload.get("context_path") or event.get("context_path") or ""),
                issue_references=_string_list(payload.get("issue_references") or event.get("issue_references")),
                evidence=[item for item in event.get("evidence", []) if isinstance(item, dict)][:20],
            )
        )
    return rows


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load_model(path: Path, model: Any) -> Any | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        item = model.model_validate(payload)
        return item if int(getattr(item, "schema_version", STATE_VERSION)) == STATE_VERSION else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _load_models(directory: Path, model: Any) -> list[Any]:
    if not directory.exists():
        return []
    rows: list[Any] = []
    for path in sorted(directory.glob("*.json")):
        item = _load_model(path, model)
        if item is not None:
            rows.append(item)
    return rows


def _load_jsonl(path: Path, model: Any) -> list[Any]:
    if not path.exists():
        return []
    rows: list[Any] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-MAX_FEEDBACK:]:
        try:
            payload = json.loads(line)
            rows.append(model.model_validate(payload))
        except (ValueError, json.JSONDecodeError):
            continue
    return rows


def _write_model(path: Path, model: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(item.model_dump(mode="json"), sort_keys=True) + "\n" for item in rows), encoding="utf-8")
    temporary.replace(path)
