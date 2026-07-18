from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core import git
from agentpack.observer.models import ObserverEntity, ObserverEvent
from agentpack.session.events import read_events, record_event


DEFAULT_OBSERVER_EVENTS_PATH = ".agentpack/observer-events.jsonl"
DEFAULT_OBSERVER_BRIEF_PATH = ".agentpack/observer-brief.md"
MAX_TASK_CHARS = 500
MAX_EVIDENCE = 20
MAX_ENTITIES = 40


def record_observation(
    root: Path,
    event_type: str,
    *,
    task: str = "",
    source: str = "",
    outcome: str = "",
    confidence: float = 0.0,
    entities: list[ObserverEntity] | None = None,
    evidence: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    output_path: str = DEFAULT_OBSERVER_EVENTS_PATH,
) -> None:
    event = ObserverEvent(
        type=event_type,
        timestamp=datetime.now(timezone.utc).isoformat(),
        task=_clip(task, MAX_TASK_CHARS),
        source=source,
        repo=str(root),
        branch=git.current_branch(root) if git.is_git_repo(root) else "",
        git_sha=git.current_sha(root) if git.is_git_repo(root) else "",
        outcome=outcome,
        confidence=max(0.0, min(1.0, confidence)),
        entities=(entities or [])[:MAX_ENTITIES],
        evidence=_str_list(evidence)[:MAX_EVIDENCE],
        payload=payload or {},
    )
    record_event(root, event_type, event.model_dump(exclude={"type", "timestamp"}), output_path=output_path)


def read_observations(
    root: Path,
    *,
    output_path: str = DEFAULT_OBSERVER_EVENTS_PATH,
    limit: int = 500,
) -> list[dict[str, Any]]:
    return read_events(root, output_path=output_path, limit=limit)


def record_task_observation(root: Path, payload: dict[str, Any]) -> None:
    task = str(payload.get("task") or "")
    changed = _str_list(payload.get("changed_files"))
    selected = _str_list(payload.get("selected_files"))
    missed = [path for path in changed if path not in set(selected)]
    entities = [
        ObserverEntity(kind="file", id=path, path=path, source="changed")
        for path in changed[:MAX_ENTITIES]
    ]
    evidence = [*changed[:10], *selected[:10]]
    record_observation(
        root,
        "task_memory",
        task=task,
        source="task_memory",
        outcome=str(payload.get("status") or ""),
        confidence=0.7 if changed or selected else 0.35,
        entities=entities,
        evidence=evidence,
        payload={
            "stage": payload.get("stage") or "",
            "status": payload.get("status") or "",
            "summary": payload.get("summary") or "",
            "changed_files": changed,
            "selected_files": selected,
            "selected_misses": missed[:20],
            "concepts": _str_list(payload.get("concepts")),
            "tests": _str_list(payload.get("tests")),
        },
    )


def record_route_observation(
    root: Path,
    *,
    task: str,
    selected_files: list[dict[str, Any]],
    observer_notes: list[dict[str, Any]] | None = None,
) -> None:
    paths = [str(item.get("path") or "") for item in selected_files if isinstance(item, dict)]
    record_observation(
        root,
        "route",
        task=task,
        source="route",
        outcome="planned",
        confidence=0.55,
        entities=[ObserverEntity(kind="file", id=path, path=path, source="selected") for path in paths[:MAX_ENTITIES]],
        evidence=paths[:MAX_EVIDENCE],
        payload={
            "selected_files": paths[:30],
            "observer_notes": observer_notes or [],
        },
    )


def record_learning_observation(
    root: Path,
    *,
    task: str,
    concepts: list[str],
    selected_hits: int,
    selected_misses: int,
    learning_request: str = "",
    learning_sessions: int = 0,
) -> None:
    record_observation(
        root,
        "learn",
        task=task,
        source="learn",
        outcome="generated",
        confidence=0.65,
        evidence=concepts[:MAX_EVIDENCE],
        payload={
            "concepts": concepts[:20],
            "selected_hits": selected_hits,
            "selected_misses": selected_misses,
            "learning_request": learning_request,
            "learning_sessions": learning_sessions,
        },
    )


def record_learning_feedback_observation(root: Path, *, task: str, feedback: str, target: str = "") -> None:
    record_observation(
        root,
        "learn_feedback",
        task=task,
        source="learn",
        outcome=feedback,
        confidence=0.7,
        evidence=[target] if target else [],
        payload={"feedback": feedback, "target": target},
    )


def record_review_observation(
    root: Path,
    *,
    task: str,
    status: str,
    changed_files: list[str] | None = None,
    findings_count: int = 0,
    posted_status: str = "",
) -> None:
    files = _str_list(changed_files)
    record_observation(
        root,
        "review_outcome" if findings_count or posted_status else "review_preflight",
        task=task,
        source="review",
        outcome=status,
        confidence=0.75 if files else 0.45,
        entities=[ObserverEntity(kind="file", id=path, path=path, source="review") for path in files[:MAX_ENTITIES]],
        evidence=files[:MAX_EVIDENCE],
        payload={
            "changed_files": files[:30],
            "findings_count": findings_count,
            "posted_status": posted_status,
        },
    )


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _clip(value: str, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"
