from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core.project_index import project_id
from agentpack.learning.competencies import (
    canonical_json_hash,
    competency_artifact,
    competency_for_session,
    competency_proof_requirement,
    derive_competency_summaries,
    load_learner_profile,
    map_to_competency,
    readable_registered_project_roots,
    stable_task_id,
)
from agentpack.learning.models import (
    LearningProof,
    LearningRecommendationTopic,
    LearningReport,
    LearningSession,
)

SESSIONS_PATH = ".agentpack/learning-sessions.jsonl"
MAX_SESSION_ROWS = 500
MAX_SESSIONS_PER_RUN = 12
MAX_TEXT_CHARS = 500
MAX_LIST_ITEMS = 8


class LearningProofConflictError(ValueError):
    pass


def record_learning_sessions(root: Path, report: LearningReport, *, path: str = SESSIONS_PATH) -> int:
    """Persist queued coach questions from an on-demand learning request."""
    if not report.learning_request:
        return 0
    sessions = sessions_from_report(report)
    if not sessions:
        return 0
    profile, _warnings = load_learner_profile()
    count = 0
    for session in sessions:
        project_id_value = project_id(root)
        competency_id = competency_for_session(session) or "implementation"
        enriched = session.model_copy(
            update={
                "session_id": _new_id("session"),
                "topic_id": session.topic_id or _stable_id("topic", report.task, session.topic),
                "project_id": project_id_value,
                "project_name": root.resolve().name,
                "project_root": str(root.resolve()),
                "task_id": stable_task_id(project_id_value, report.task),
                "competency_id": competency_id,
                "target_level": profile.target_level,
                "proof_requirement": competency_proof_requirement(competency_id),
                "required_artifact": competency_artifact(competency_id),
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
    mutation_id: str = "",
) -> LearningSession:
    question = topic.questions[0] if topic.questions else None
    selected_mode = mode or topic.default_mode or (question.mode if question else "study")
    task = next((item.task for item in topic.evidence if item.task), topic.title)
    task_id = next((item.task_id for item in topic.evidence if item.task_id), "")
    return LearningSession(
        session_id=_new_id("session"),
        mutation_id=mutation_id,
        topic_id=topic.topic_id,
        recommendation_id=recommendation_id,
        project_id=topic.project.project_id,
        project_name=topic.project.name,
        project_root=topic.project.root,
        task_id=task_id or stable_task_id(topic.project.project_id, task),
        task=_clip(task),
        request=f"recommended:{topic.lane}",
        mode=selected_mode,
        topic=_clip(topic.title),
        question=_clip(question.question if question else topic.exercise),
        expected_points=_bounded_list(question.expected_points if question else [topic.completion_check]),
        evidence_files=_bounded_list([item.path for item in topic.evidence if item.path]),
        concepts=_bounded_list(topic.concepts),
        competency_id=topic.competency_id,
        target_level=topic.target_level,
        proof_requirement=topic.proof_requirement,
        required_artifact=topic.required_artifact,
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
    """Record deprecated score-only completion evidence.

    Legacy evidence remains readable and can move a competency to developing,
    but it never contributes a passing proof toward mastery.
    """
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
            "legacy_evidence": True,
            "status": "completed",
            "updated_at": updated_at,
        }
    )
    updated = normalize_learning_session(updated)
    if not append_learning_session(root, updated):
        raise ValueError(f"Could not write learning session: {session_id}")
    return updated


def record_learning_proof(
    root: Path,
    session_id: str,
    proof: LearningProof,
) -> tuple[LearningSession, bool]:
    session = find_learning_session(root, session_id)
    if session is None:
        raise ValueError(f"Learning session not found: {session_id}")

    if session.status == "completed":
        proof_hash = canonical_json_hash(
            {
                "proof": proof.model_dump(mode="json", exclude={"evaluated_at"}),
                "artifact_hashes": session.artifact_hashes,
            }
        )
        if session.proof_hash and session.proof_hash == proof_hash:
            return session, True
        raise LearningProofConflictError("Learning session already has a different proof; start a new session")

    artifact_hashes = _validate_proof(root, session, proof)
    proof_hash = canonical_json_hash(
        {
            "proof": proof.model_dump(mode="json", exclude={"evaluated_at"}),
            "artifact_hashes": artifact_hashes,
        }
    )

    profile, _warnings = load_learner_profile()
    roots = readable_registered_project_roots(root)
    before = {
        item.competency_id: item.status
        for item in derive_competency_summaries(roots, profile)
    }
    score = _proof_score(session, proof)
    timestamp = datetime.now(timezone.utc).isoformat()
    stored_proof = proof.model_copy(update={"evaluated_at": proof.evaluated_at or timestamp})
    updated = session.model_copy(
        update={
            "answer": proof.answer.strip(),
            "score": score,
            "self_assessment": proof.self_assessment,
            "proof": stored_proof,
            "proof_hash": proof_hash,
            "artifact_hashes": artifact_hashes,
            "legacy_evidence": False,
            "status": "completed",
            "updated_at": timestamp,
        }
    )
    updated = normalize_learning_session(updated)
    if not append_learning_session(root, updated):
        raise ValueError(f"Could not write learning session: {session_id}")

    after = {
        item.competency_id: item.status
        for item in derive_competency_summaries(roots, profile)
    }
    competency_id = updated.competency_id or competency_for_session(updated)
    competency_status = after.get(competency_id, updated.mastery_status) if competency_id else updated.mastery_status
    _record_proof_event(root, updated, status=competency_status)
    if competency_id and before.get(competency_id) != after.get(competency_id):
        _record_competency_event(
            root,
            competency_id=competency_id,
            previous_status=before.get(competency_id, "unassessed"),
            status=after.get(competency_id, updated.mastery_status),
            score=score,
        )
    return updated, False


def normalize_learning_session(session: LearningSession) -> LearningSession:
    session_id = session.session_id or _stable_id("session", session.task, session.topic, session.question, session.created_at)
    topic_id = session.topic_id or _stable_id("topic", session.task, session.topic or ",".join(session.concepts))
    project_id_value = session.project_id or (
        project_id(Path(session.project_root)) if session.project_root else ""
    )
    task_id = session.task_id or stable_task_id(project_id_value, session.task)
    competency_id = session.competency_id or map_to_competency(
        concepts=session.concepts,
        task=" ".join([session.task, session.topic, session.question]),
        paths=session.evidence_files,
        evidence_kind="prior_assessment",
    )
    legacy_evidence = session.legacy_evidence or bool(
        session.status == "completed" and session.score is not None and session.proof is None
    )
    return session.model_copy(
        update={
            "session_id": session_id,
            "topic_id": topic_id,
            "project_id": project_id_value,
            "task_id": task_id,
            "competency_id": competency_id,
            "legacy_evidence": legacy_evidence,
            "updated_at": session.updated_at or session.created_at,
            "mastery_status": derive_mastery_status(session),
        }
    )


def derive_mastery_status(session: LearningSession) -> str:
    if session.score is None:
        return "unassessed"
    if session.score < 70 or session.self_assessment == "needs-practice":
        return "needs_practice"
    # Mastery is an aggregate competency state, never a claim from one session.
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


def _validate_proof(root: Path, session: LearningSession, proof: LearningProof) -> dict[str, str]:
    if not proof.answer.strip():
        raise ValueError("proof answer must not be empty")
    if proof.self_assessment not in {"mastered", "developing", "needs-practice"}:
        raise ValueError("proof self-assessment is required")
    expected = [_criterion_key(value) for value in session.expected_points if value.strip()]
    if not expected:
        raise ValueError("learning session has no expected points to evaluate")
    actual = [_criterion_key(result.criterion) for result in proof.rubric_results]
    if len(actual) != len(set(actual)):
        raise ValueError("proof rubric contains duplicate criteria")
    missing = [value for value in expected if value not in actual]
    extra = [value for value in actual if value not in expected]
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if extra:
            detail.append("unexpected: " + ", ".join(extra))
        raise ValueError("proof rubric must evaluate every expected point (" + "; ".join(detail) + ")")

    if proof.kind == "artifact":
        if not proof.artifact_paths:
            raise ValueError("artifact proof requires at least one artifact path")
        if not proof.verification_evidence:
            raise ValueError("artifact proof requires verification evidence")
        failed = [item.command for item in proof.verification_evidence if item.exit_code != 0]
        if failed:
            raise ValueError("artifact proof verification commands must all exit successfully")
    elif session.proof_requirement == "artifact":
        raise ValueError("this learning session requires artifact proof")

    artifact_hashes: dict[str, str] = {}
    for raw_path in proof.artifact_paths:
        relative, resolved = _resolve_artifact(root, raw_path)
        artifact_hashes[relative] = _file_hash(resolved)
    return artifact_hashes


def _proof_score(session: LearningSession, proof: LearningProof) -> int:
    ratings = {_criterion_key(item.criterion): item.rating for item in proof.rubric_results}
    values = {"missing": 0, "partial": 1, "met": 2}
    earned = sum(values[ratings[_criterion_key(point)]] for point in session.expected_points if point.strip())
    maximum = len([point for point in session.expected_points if point.strip()]) * 2
    return round((earned / maximum) * 100)


def _resolve_artifact(root: Path, raw_path: str) -> tuple[str, Path]:
    clean = raw_path.strip()
    if not clean:
        raise ValueError("artifact path must not be empty")
    provided = Path(clean).expanduser()
    if ".." in provided.parts:
        raise ValueError(f"artifact path traversal is not allowed: {raw_path}")
    project_root = root.resolve()
    candidate = provided if provided.is_absolute() else project_root / provided
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"artifact path does not exist: {raw_path}") from exc
    if resolved == project_root or project_root not in resolved.parents:
        raise ValueError(f"artifact path escapes the owning project: {raw_path}")
    if not resolved.is_file():
        raise ValueError(f"artifact path is not a file: {raw_path}")
    return resolved.relative_to(project_root).as_posix(), resolved


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _criterion_key(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _record_proof_event(root: Path, session: LearningSession, *, status: str) -> None:
    try:
        from agentpack.session.events import record_event

        record_event(
            root,
            "learning_proof_recorded",
            {
                "session_id": session.session_id,
                "topic_id": session.topic_id,
                "project_id": session.project_id,
                "task_id": session.task_id,
                "competency_id": session.competency_id,
                "status": status,
                "score": session.score,
            },
            source="learn",
        )
    except OSError:
        pass


def _record_competency_event(
    root: Path,
    *,
    competency_id: str,
    previous_status: str,
    status: str,
    score: int,
) -> None:
    try:
        from agentpack.session.events import record_event

        record_event(
            root,
            "competency_status_changed",
            {
                "competency_id": competency_id,
                "previous_status": previous_status,
                "status": status,
                "score": score,
            },
            source="learn",
        )
    except OSError:
        pass


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
