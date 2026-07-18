from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from agentpack.learning.models import LearningReport, LearningSession

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
    output = root / path
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as fh:
            for session in sessions:
                fh.write(json.dumps(session.model_dump(mode="json"), sort_keys=True) + "\n")
    except OSError:
        return 0
    return len(sessions)


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
    source = root / path
    if not source.exists():
        return []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    sessions: list[LearningSession] = []
    for line in lines[-min(max(limit * 3, limit), MAX_SESSION_ROWS) :]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                sessions.append(LearningSession.model_validate(payload))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return sessions[-limit:]


def summarize_weak_spots(root: Path, *, limit: int = 6) -> list[dict[str, Any]]:
    sessions = read_learning_sessions(root, limit=MAX_SESSION_ROWS)
    buckets: dict[str, dict[str, Any]] = {}
    mode_counts: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for session in sessions:
        concepts = session.concepts or ([session.topic] if session.topic else [])
        weak = session.score is None or session.score < 70 or session.status in {"queued", "needs_review"}
        if not weak:
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
