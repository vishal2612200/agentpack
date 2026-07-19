from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core.project_index import project_id
from agentpack.learning.models import (
    LearningRecommendationTopic,
    LearningReport,
    LearningSession,
)

SESSIONS_PATH = ".agentpack/learning-sessions.jsonl"
MAX_SESSION_ROWS = 500
MAX_SESSIONS_PER_RUN = 12
MAX_TEXT_CHARS = 500
MAX_LIST_ITEMS = 8


def record_learning_sessions(root: Path, report: LearningReport, *, path: str = SESSIONS_PATH) -> int:
    """Persist queued coach questions from an on-demand learning request."""
    if not report.learning_request:
        return 0
    sessions = sessions_from_report(report)
    if not sessions:
        return 0
    count = 0
    for session in sessions:
        enriched = session.model_copy(
            update={
                "session_id": _new_id("session"),
                "topic_id": session.topic_id or _stable_id("topic", report.task, session.topic),
                "project_id": project_id(root),
                "project_name": root.resolve().name,
                "project_root": str(root.resolve()),
            }
        )
        if append_learning_session(root, enriched, path=path):
            count += 1
    return count


def sessions_from_report(report: LearningReport) -> list[LearningSession]:
    sessions: list[LearningSession] = []
    report_concepts = _bounded_list(report.concepts)
    for topic in report.learning_topics:
        topic_concepts = _bounded_list(topic.concepts or report_concepts)
        for question in topic.questions:
            sessions.append(
                LearningSession(
                    task=_clip(report.task),
                    request=_clip(report.learning_request),
                    mode=question.mode or report.coach_mode,
                    topic=_clip(topic.title),
                    question=_clip(question.question),
                    expected_points=_bounded_list(question.expected_points),
                    evidence_files=_bounded_list(question.evidence_files or topic.files),
                    concepts=topic_concepts,
                )
            )
            if len(sessions) >= MAX_SESSIONS_PER_RUN:
                return sessions
    return sessions


def read_learning_sessions(root: Path, *, limit: int = 50, path: str = SESSIONS_PATH) -> list[LearningSession]:
    sessions, _errors = read_learning_sessions_with_errors(root, limit=limit, path=path)
    return sessions


def read_learning_sessions_with_errors(
    root: Path,
    *,
    limit: int = 50,
    path: str = SESSIONS_PATH,
) -> tuple[list[LearningSession], int]:
    source = root / path
    if not source.exists():
        return [], 0
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 1
    sessions: dict[str, LearningSession] = {}
    errors = 0
    for line in lines[-MAX_SESSION_ROWS:]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                session = normalize_learning_session(LearningSession.model_validate(payload))
                sessions.pop(session.session_id, None)
                sessions[session.session_id] = session
            else:
                errors += 1
        except (json.JSONDecodeError, ValueError, TypeError):
            errors += 1
    return list(sessions.values())[-limit:], errors


def append_learning_session(root: Path, session: LearningSession, *, path: str = SESSIONS_PATH) -> bool:
    output = root / path
    normalized = normalize_learning_session(session)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(normalized.model_dump(mode="json"), sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def session_from_recommendation(
    topic: LearningRecommendationTopic,
    *,
    recommendation_id: str,
    mode: str = "",
) -> LearningSession:
    question = topic.questions[0] if topic.questions else None
    selected_mode = mode or topic.default_mode or (question.mode if question else "study")
    task = next((item.task for item in topic.evidence if item.task), topic.title)
    return LearningSession(
        session_id=_new_id("session"),
        topic_id=topic.topic_id,
        recommendation_id=recommendation_id,
        project_id=topic.project.project_id,
        project_name=topic.project.name,
        project_root=topic.project.root,
        task=_clip(task),
        request=f"recommended:{topic.lane}",
        mode=selected_mode,
        topic=_clip(topic.title),
        question=_clip(question.question if question else topic.exercise),
        expected_points=_bounded_list(question.expected_points if question else [topic.completion_check]),
        evidence_files=_bounded_list([item.path for item in topic.evidence if item.path]),
        concepts=_bounded_list(topic.concepts),
    )


def find_learning_session(root: Path, session_id: str) -> LearningSession | None:
    return next(
        (session for session in read_learning_sessions(root, limit=MAX_SESSION_ROWS) if session.session_id == session_id),
        None,
    )


def complete_learning_session(
    root: Path,
    session_id: str,
    *,
    score: int,
    self_assessment: str,
    note: str = "",
) -> LearningSession:
    session = find_learning_session(root, session_id)
    if session is None:
        raise ValueError(f"Learning session not found: {session_id}")
    if not 0 <= score <= 100:
        raise ValueError("--score must be between 0 and 100")
    if self_assessment not in {"mastered", "needs-practice"}:
        raise ValueError("--self-assessment must be mastered or needs-practice")
    updated_at = datetime.now(timezone.utc).isoformat()
    updated = session.model_copy(
        update={
            "score": score,
            "self_assessment": self_assessment,
            "note": _clip(note),
            "status": "completed",
            "updated_at": updated_at,
        }
    )
    updated = normalize_learning_session(updated)
    if not append_learning_session(root, updated):
        raise ValueError(f"Could not write learning session: {session_id}")
    return updated


def normalize_learning_session(session: LearningSession) -> LearningSession:
    session_id = session.session_id or _stable_id("session", session.task, session.topic, session.question, session.created_at)
    topic_id = session.topic_id or _stable_id("topic", session.task, session.topic or ",".join(session.concepts))
    return session.model_copy(
        update={
            "session_id": session_id,
            "topic_id": topic_id,
            "updated_at": session.updated_at or session.created_at,
            "mastery_status": derive_mastery_status(session),
        }
    )


def derive_mastery_status(session: LearningSession) -> str:
    if session.score is None:
        return "unassessed"
    if session.score < 70 or session.self_assessment == "needs-practice":
        return "needs_practice"
    if session.score >= 80 and session.self_assessment == "mastered":
        return "mastered"
    return "developing"


def summarize_mastery(root: Path) -> dict[str, int]:
    latest: dict[str, LearningSession] = {}
    for session in read_learning_sessions(root, limit=MAX_SESSION_ROWS):
        latest[session.topic_id or session.topic.lower()] = session
    summary = {"mastered": 0, "developing": 0, "needs_practice": 0, "unassessed": 0}
    for session in latest.values():
        summary[derive_mastery_status(session)] += 1
    return summary


def summarize_weak_spots(root: Path, *, limit: int = 6) -> list[dict[str, Any]]:
    sessions = read_learning_sessions(root, limit=MAX_SESSION_ROWS)
    buckets: dict[str, dict[str, Any]] = {}
    mode_counts: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for session in sessions:
        concepts = session.concepts or ([session.topic] if session.topic else [])
        if derive_mastery_status(session) != "needs_practice":
            continue
        for concept in concepts[:MAX_LIST_ITEMS]:
            key = concept.strip().lower()
            if not key:
                continue
            bucket = buckets.setdefault(
                key,
                {
                    "concept": concept,
                    "count": 0,
                    "latest_task": "",
                    "latest_question": "",
                    "evidence_files": [],
                    "mode": "",
                },
            )
            bucket["count"] += 1
            bucket["latest_task"] = session.task
            bucket["latest_question"] = session.question
            bucket["evidence_files"] = _merge_lists(bucket["evidence_files"], session.evidence_files)
            mode_counts[key][session.mode] += 1
    for key, bucket in buckets.items():
        modes = mode_counts[key]
        bucket["mode"] = max(modes.items(), key=lambda item: (item[1], item[0]))[0] if modes else ""
    return sorted(buckets.values(), key=lambda item: (-int(item["count"]), str(item["concept"])))[:limit]


def _merge_lists(left: list[str], right: list[str]) -> list[str]:
    seen = set(left)
    merged = list(left)
    for item in right:
        if item and item not in seen:
            seen.add(item)
            merged.append(item)
    return merged[:MAX_LIST_ITEMS]


def _bounded_list(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clip(value, 160)
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
        if len(result) >= MAX_LIST_ITEMS:
            break
    return result


def _clip(value: str, limit: int = MAX_TEXT_CHARS) -> str:
    clean = " ".join(str(value or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _stable_id(prefix: str, *parts: str) -> str:
    value = "|".join(str(part or "").strip().lower() for part in parts)
    return f"{prefix}-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _new_id(prefix: str) -> str:
    return f"{prefix}-" + uuid.uuid4().hex[:20]
