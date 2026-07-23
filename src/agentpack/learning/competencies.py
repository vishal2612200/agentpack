from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentpack.core.project_index import agentpack_home, load_project_index
from agentpack.learning.models import (
    CompetencyId,
    CompetencySummary,
    LearnerProfile,
    LearningMasterySummary,
    LearningSession,
)


PROFILE_SCHEMA_VERSION = 1
PROFILE_FILENAME = "learner-profile.json"

COMPETENCY_DEFINITIONS: dict[CompetencyId, dict[str, str]] = {
    "product_reasoning": {
        "name": "Product reasoning",
        "artifact": "Acceptance criteria, a scoped proposal, or a decision note",
        "proof_requirement": "reasoning",
    },
    "implementation": {
        "name": "Implementation",
        "artifact": "A code, API, or data-contract change",
        "proof_requirement": "artifact",
    },
    "quality": {
        "name": "Quality",
        "artifact": "A regression test, debugging trace, or review finding",
        "proof_requirement": "artifact",
    },
    "systems": {
        "name": "Systems",
        "artifact": "An ADR, data-flow analysis, or failure/consistency analysis",
        "proof_requirement": "reasoning",
    },
    "production": {
        "name": "Production",
        "artifact": "A runbook, observability check, deployment, or rollback plan",
        "proof_requirement": "artifact",
    },
    "security": {
        "name": "Security",
        "artifact": "A threat analysis, authorization test, or validation evidence",
        "proof_requirement": "artifact",
    },
    "collaboration": {
        "name": "Collaboration",
        "artifact": "A review response, handoff, or design summary",
        "proof_requirement": "reasoning",
    },
}

ROLE_EMPHASIS: dict[str, set[CompetencyId]] = {
    "frontend": {"product_reasoning", "implementation", "quality"},
    "backend": {"implementation", "systems", "security"},
    "mobile": {"implementation", "quality", "production"},
    "platform": {"systems", "production", "security"},
    "general": set(),
}

ROLE_DRILL_FRAMING = {
    "frontend": "accessibility, browser state, or UI performance",
    "backend": "APIs, data integrity, or concurrency",
    "mobile": "lifecycle, offline behavior, or release safety",
    "platform": "delivery, infrastructure, or observability",
    "general": "the concrete engineering trade-offs in the task",
}

_EXPECTED_POINTS: dict[CompetencyId, list[str]] = {
    "product_reasoning": [
        "State the user or product outcome and acceptance criteria",
        "Explain the chosen scope and one rejected alternative",
        "Name a measurable validation signal",
    ],
    "implementation": [
        "Explain the implementation and the contract it changes",
        "Identify an edge case or failure mode",
        "Provide a focused verification result",
    ],
    "quality": [
        "Describe the failure or regression being prevented",
        "Explain why the test or debugging evidence is discriminating",
        "Provide the focused verification result",
    ],
    "systems": [
        "Trace the relevant data or control flow",
        "Explain a consistency, ordering, or failure trade-off",
        "Describe how the design recovers or degrades",
    ],
    "production": [
        "Identify the production signal and expected operating range",
        "Explain deployment or rollback safety",
        "Provide an operational verification result",
    ],
    "security": [
        "Identify the asset, trust boundary, and threat",
        "Explain the authorization or validation control",
        "Provide a negative-path verification result",
    ],
    "collaboration": [
        "Summarize the decision for another engineer",
        "Address a concrete review question or trade-off",
        "State the next action, owner, or handoff condition",
    ],
}

_KEYWORDS: dict[CompetencyId, tuple[str, ...]] = {
    "product_reasoning": (
        "acceptance criteria",
        "user need",
        "product requirement",
        "scope",
        "proposal",
        "trade-off",
        "decision note",
    ),
    "implementation": (
        "implementation",
        "code change",
        "api",
        "cli",
        "frontend",
        "backend",
        "mobile",
        "serialization",
        "configuration",
        "mcp",
    ),
    "quality": (
        "regression",
        "test",
        "testing",
        "debug",
        "bug",
        "review finding",
        "failure reproduction",
    ),
    "systems": (
        "architecture",
        "system design",
        "data flow",
        "consistency",
        "concurrency",
        "ordering",
        "retry",
        "cache",
        "queue",
    ),
    "production": (
        "production",
        "deployment",
        "release",
        "rollback",
        "runbook",
        "observability",
        "monitoring",
        "incident",
        "on-call",
    ),
    "security": (
        "security",
        "threat",
        "authorization",
        "authentication",
        "validation",
        "permission",
        "secret",
        "injection",
    ),
    "collaboration": (
        "review response",
        "handoff",
        "design summary",
        "stakeholder",
        "communication",
        "pairing",
        "review comment",
    ),
}


def learner_profile_path(path: Path | None = None) -> Path:
    return path or (agentpack_home() / PROFILE_FILENAME)


def load_learner_profile(path: Path | None = None) -> tuple[LearnerProfile, list[str]]:
    profile_path = learner_profile_path(path)
    if not profile_path.exists():
        return LearnerProfile(), []
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
        profile = LearnerProfile.model_validate(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValidationError):
        return LearnerProfile(), ["learner_profile_invalid: using safe defaults; the file was not changed"]
    return profile, []


def update_learner_profile(
    *,
    role: str = "",
    target_level: str = "",
    path: Path | None = None,
    event_root: Path | None = None,
    mutation_id: str = "",
) -> LearnerProfile:
    current, _warnings = load_learner_profile(path)
    payload = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "role": role or current.role,
        "target_level": target_level or current.target_level,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        profile = LearnerProfile.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(_profile_validation_message(exc)) from exc

    output = learner_profile_path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(profile.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(output)
        try:
            output.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass

    if event_root is not None:
        try:
            from agentpack.session.events import record_event

            record_event(
                event_root,
                "learner_profile_updated",
                {
                    "role": profile.role,
                    "target_level": profile.target_level,
                    "mutation_id": mutation_id,
                },
                source="learn",
            )
        except OSError:
            pass
    return profile


def role_emphasizes(role: str, competency_id: CompetencyId) -> bool:
    return competency_id in ROLE_EMPHASIS.get(role, set())


def role_drill_framing(role: str) -> str:
    return ROLE_DRILL_FRAMING.get(role, ROLE_DRILL_FRAMING["general"])


def competency_artifact(competency_id: CompetencyId) -> str:
    return COMPETENCY_DEFINITIONS[competency_id]["artifact"]


def competency_proof_requirement(competency_id: CompetencyId) -> str:
    return COMPETENCY_DEFINITIONS[competency_id]["proof_requirement"]


def competency_expected_points(competency_id: CompetencyId) -> list[str]:
    return list(_EXPECTED_POINTS[competency_id])


def map_to_competency(
    *,
    concepts: list[str] | None = None,
    task: str = "",
    paths: list[str] | None = None,
    evidence_kind: str = "",
) -> CompetencyId | None:
    values = [task, *(concepts or []), *(paths or [])]
    haystack = " ".join(values).lower().replace("_", " ").replace("-", " ")
    scores: dict[CompetencyId, int] = {key: 0 for key in COMPETENCY_DEFINITIONS}
    for competency_id, phrases in _KEYWORDS.items():
        for phrase in phrases:
            normalized = phrase.replace("-", " ")
            if normalized in haystack:
                scores[competency_id] += 3 if " " in normalized else 1

    normalized_paths = [value.lower().replace("\\", "/") for value in paths or []]
    if any("test" in value for value in normalized_paths):
        scores["quality"] += 4
    if any(value.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt")) for value in normalized_paths):
        scores["implementation"] += 2
    if any("security" in value or "auth" in value for value in normalized_paths):
        scores["security"] += 3
    if any("deploy" in value or "infra" in value or "runbook" in value for value in normalized_paths):
        scores["production"] += 3
    if any("adr" in value or "architecture" in value for value in normalized_paths):
        scores["systems"] += 3

    best_score = max(scores.values(), default=0)
    if best_score > 0:
        return next(key for key in COMPETENCY_DEFINITIONS if scores[key] == best_score)
    if evidence_kind in {"current_change", "task", "episode"} and normalized_paths:
        return "implementation"
    if evidence_kind in {"procedure", "system_boundary"}:
        return "systems"
    return None


def competency_for_session(session: LearningSession) -> CompetencyId | None:
    if session.competency_id:
        return session.competency_id
    return map_to_competency(
        concepts=session.concepts,
        task=" ".join([session.task, session.topic, session.question]),
        paths=session.evidence_files,
        evidence_kind="prior_assessment",
    )


def readable_registered_project_roots(root: Path) -> list[Path]:
    current = root.expanduser().resolve()
    roots = [current]
    seen = {str(current)}
    for row in load_project_index():
        raw = str(row.get("path") or "").strip()
        if not raw:
            continue
        candidate = Path(raw).expanduser().resolve()
        marker = str(candidate)
        if marker in seen or not candidate.is_dir():
            continue
        artifact_root = candidate / ".agentpack"
        if not artifact_root.is_dir() or not os.access(artifact_root, os.R_OK):
            continue
        seen.add(marker)
        roots.append(candidate)
    return roots


def derive_competency_summaries(
    roots: list[Path],
    profile: LearnerProfile,
) -> list[CompetencySummary]:
    from agentpack.learning.sessions import MAX_SESSION_ROWS, read_learning_sessions

    sessions: list[LearningSession] = []
    for root in roots:
        sessions.extend(read_learning_sessions(root, limit=MAX_SESSION_ROWS))

    result: list[CompetencySummary] = []
    for competency_id, definition in COMPETENCY_DEFINITIONS.items():
        assessed = [
            session
            for session in sessions
            if session.status == "completed"
            and session.score is not None
            and competency_for_session(session) == competency_id
        ]
        assessed.sort(key=lambda item: (_session_timestamp(item), item.session_id))
        latest = assessed[-1] if assessed else None
        passing = [session for session in assessed if _is_structured_passing_proof(session)]
        proof_identities = {
            (session.project_id or _project_key(session), session.task_id)
            for session in passing
            if session.task_id
        }
        artifact_hashes = {
            digest
            for session in passing
            if session.proof is not None
            and session.proof.kind == "artifact"
            and session.proof.verification_evidence
            and all(item.exit_code == 0 for item in session.proof.verification_evidence)
            for digest in session.artifact_hashes.values()
            if digest
        }
        status = _aggregate_status(latest, len(proof_identities), bool(artifact_hashes))
        result.append(
            CompetencySummary(
                competency_id=competency_id,
                name=definition["name"],
                status=status,
                passing_proofs=len(proof_identities),
                verified_artifacts=len(artifact_hashes),
                latest_evidence=_latest_evidence(latest),
                latest_score=latest.score if latest else None,
                role_emphasis=role_emphasizes(profile.role, competency_id),
            )
        )
    return result


def mastery_summary_from_competencies(competencies: list[CompetencySummary]) -> LearningMasterySummary:
    counts = {"mastered": 0, "developing": 0, "needs_practice": 0, "unassessed": 0}
    for competency in competencies:
        counts[competency.status] += 1
    return LearningMasterySummary(**counts)


def _aggregate_status(latest: LearningSession | None, passing_count: int, has_artifact: bool) -> str:
    if latest is None or latest.score is None:
        return "unassessed"
    if latest.score < 70 or latest.self_assessment == "needs-practice":
        return "needs_practice"
    if latest.score < 80 or latest.legacy_evidence or latest.proof is None:
        return "developing"
    return "mastered" if passing_count >= 2 and has_artifact else "developing"


def _is_structured_passing_proof(session: LearningSession) -> bool:
    return bool(
        session.proof is not None
        and not session.legacy_evidence
        and session.score is not None
        and session.score >= 80
        and session.self_assessment in {"mastered", "developing"}
    )


def _latest_evidence(session: LearningSession | None) -> str:
    if session is None:
        return ""
    project = session.project_name or Path(session.project_root).name
    detail = session.task or session.topic
    return " · ".join(value for value in (project, detail) if value)[:500]


def _session_timestamp(session: LearningSession) -> float:
    value = session.updated_at or session.created_at
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _project_key(session: LearningSession) -> str:
    return hashlib.sha256(session.project_root.encode("utf-8")).hexdigest()[:16]


def _profile_validation_message(exc: ValidationError) -> str:
    fields = {str(error.get("loc", [""])[0]) for error in exc.errors()}
    if "role" in fields:
        return "role must be frontend, backend, mobile, platform, or general"
    if "target_level" in fields:
        return "level must be unspecified, junior, mid, senior, or staff"
    return "invalid learner profile"


def canonical_json_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_task_id(project_id_value: str, task: str) -> str:
    normalized = re.sub(r"\s+", " ", task.strip().lower())
    value = f"{project_id_value}|{normalized}"
    return "task-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
