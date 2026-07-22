from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentpack.core.project_index import load_project_index, project_id
from agentpack.learning.competencies import (
    load_learner_profile,
    readable_registered_project_roots,
    update_learner_profile,
)
from agentpack.learning.models import LearnerProfile, LearningProof, LearningRecommendationSet, LearningSession
from agentpack.learning.recommender import find_recommended_topic, recommend_learning_topics
from agentpack.learning.sessions import (
    append_learning_session,
    complete_learning_session,
    find_learning_session,
    read_learning_sessions,
    record_learning_proof,
    session_from_recommendation,
)
from agentpack.session.events import record_event


VALID_COACH_MODES = {"study", "quiz", "interview", "failure", "review", "system-design"}


class LearningProjectError(ValueError):
    pass


class LearningProjectNotFoundError(LearningProjectError):
    pass


class LearningProjectReadOnlyError(LearningProjectError):
    pass


def get_learning_profile() -> tuple[LearnerProfile, list[str]]:
    return load_learner_profile()


def set_learning_profile(root: Path, *, role: str = "", target_level: str = "") -> LearnerProfile:
    return update_learner_profile(role=role, target_level=target_level, event_root=root)


def set_learning_profile_mutation(
    root: Path,
    *,
    mutation_id: str,
    role: str,
    target_level: str,
) -> tuple[LearnerProfile, bool]:
    from agentpack.session.events import read_events

    for event in reversed(read_events(root, limit=200)):
        if event.get("type") != "learner_profile_updated" or event.get("mutation_id") != mutation_id:
            continue
        if event.get("role") != role or event.get("target_level") != target_level:
            raise ValueError("mutation_id was already used for a different learner profile update")
        profile, _warnings = load_learner_profile()
        return profile, True
    profile = update_learner_profile(
        role=role,
        target_level=target_level,
        event_root=root,
        mutation_id=mutation_id,
    )
    return profile, False


def get_learning_recommendations(
    root: Path,
    *,
    request: str = "",
    scope: str = "local",
) -> LearningRecommendationSet:
    if scope not in {"local", "global"}:
        raise ValueError("scope must be local or global")
    return recommend_learning_topics(
        root,
        request=request,
        global_scope=scope == "global",
    )


def start_learning_session(
    root: Path,
    topic_id: str,
    *,
    project_id_value: str = "",
    mode: str = "",
    mutation_id: str = "",
) -> tuple[LearningSession, bool]:
    if mode and mode not in VALID_COACH_MODES:
        raise ValueError("mode must be study, quiz, interview, failure, review, or system-design")
    target = resolve_learning_project(root, project_id_value)
    _assert_project_writable(target)
    if mutation_id:
        duplicate = next(
            (
                session
                for session in read_learning_sessions(target, limit=500)
                if session.mutation_id == mutation_id
            ),
            None,
        )
        if duplicate is not None:
            return duplicate, True

    target, topic, recommendation_id = find_recommended_topic(
        root.resolve(),
        topic_id,
        project_id_value=project_id_value,
    )
    _assert_project_writable(target)
    session = session_from_recommendation(
        topic,
        recommendation_id=recommendation_id,
        mode=mode,
        mutation_id=mutation_id,
    )
    if not append_learning_session(target, session):
        raise LearningProjectReadOnlyError(f"Could not write learning session in {target}")
    try:
        record_event(
            target,
            "learning_session_started",
            {
                "session_id": session.session_id,
                "topic_id": session.topic_id,
                "recommendation_id": recommendation_id,
                "project_id": topic.project.project_id,
                "task_id": session.task_id,
                "competency_id": session.competency_id,
                "mode": session.mode,
            },
            source="learn",
        )
    except OSError:
        pass
    return session, False


def complete_learning_session_with_proof(
    root: Path,
    session_id: str,
    proof: LearningProof | dict[str, Any],
) -> tuple[LearningSession, bool]:
    owner = find_learning_session_owner(root, session_id)
    _assert_project_writable(owner)
    try:
        validated = proof if isinstance(proof, LearningProof) else LearningProof.model_validate(proof)
    except ValidationError as exc:
        detail = "; ".join(error["msg"] for error in exc.errors()[:5])
        raise ValueError(f"invalid learning proof: {detail}") from exc
    return record_learning_proof(owner, session_id, validated)


def complete_learning_session_legacy(
    root: Path,
    session_id: str,
    *,
    score: int,
    self_assessment: str,
    note: str = "",
) -> LearningSession:
    owner = find_learning_session_owner(root, session_id)
    _assert_project_writable(owner)
    return complete_learning_session(
        owner,
        session_id,
        score=score,
        self_assessment=self_assessment,
        note=note,
    )


def find_learning_session_owner(root: Path, session_id: str) -> Path:
    owner = next(
        (
            candidate
            for candidate in readable_registered_project_roots(root)
            if find_learning_session(candidate, session_id) is not None
        ),
        None,
    )
    if owner is None:
        raise LearningProjectNotFoundError(f"Learning session not found: {session_id}")
    return owner


def resolve_learning_project(root: Path, project_id_value: str = "") -> Path:
    current = root.expanduser().resolve()
    if not project_id_value or project_id(current) == project_id_value:
        return current
    for row in load_project_index():
        if str(row.get("project_id") or "") != project_id_value:
            continue
        candidate = Path(str(row.get("path") or "")).expanduser().resolve()
        if candidate.is_dir():
            return candidate
        break
    raise LearningProjectNotFoundError(f"Project not found: {project_id_value}")


def coaching_prompt(session: LearningSession) -> str:
    expected = "\n".join(f"- {point}" for point in session.expected_points)
    files = "\n".join(f"- {path}" for path in session.evidence_files) or "- No direct file evidence"
    return (
        "Coach this AgentPack learning session one question at a time.\n\n"
        f"Session: {session.session_id}\n"
        f"Competency: {session.competency_id or 'unmapped'}\n"
        f"Target level: {session.target_level}\n"
        f"Question: {session.question}\n"
        f"Required proof: {session.proof_requirement}\n"
        f"Required artifact: {session.required_artifact}\n\n"
        f"Expected points:\n{expected}\n\n"
        f"Evidence files:\n{files}\n\n"
        "Evaluate every expected point as missing, partial, or met. Collect artifact paths and successful "
        "verification commands when artifact proof is required, then submit structured proof through AgentPack."
    )


def _assert_project_writable(root: Path) -> None:
    if not root.is_dir():
        raise LearningProjectNotFoundError(f"Project is inaccessible: {root}")
    artifact_root = root / ".agentpack"
    if not artifact_root.is_dir() or not os.access(artifact_root, os.R_OK):
        raise LearningProjectNotFoundError(f"Project is unregistered or inaccessible: {root}")
    try:
        mode = artifact_root.stat().st_mode
    except OSError as exc:
        raise LearningProjectNotFoundError(f"Project is inaccessible: {root}") from exc
    writable_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    if not os.access(artifact_root, os.W_OK) or not mode & writable_bits:
        raise LearningProjectReadOnlyError(f"Project is read-only: {root}")
