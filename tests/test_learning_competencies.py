from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agentpack.core.project_index import project_id
from agentpack.learning.competencies import (
    derive_competency_summaries,
    load_learner_profile,
    map_to_competency,
    update_learner_profile,
)
from agentpack.learning.models import LearnerProfile, LearningProof, LearningSession, RubricResult, VerificationEvidence
from agentpack.learning.sessions import (
    LearningProofConflictError,
    append_learning_session,
    complete_learning_session,
    record_learning_proof,
)


EXPECTED = ["Explain the contract", "Provide verification"]


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".agentpack").mkdir(parents=True)
    return root


def _session(root: Path, *, task_id: str, proof_requirement: str = "reasoning") -> LearningSession:
    return LearningSession(
        session_id=f"session-{task_id}",
        topic_id="topic-implementation",
        project_id=project_id(root),
        project_name=root.name,
        project_root=str(root),
        task_id=task_id,
        task=f"Implement {task_id}",
        topic="Implementation proof",
        question="Explain and verify the change",
        expected_points=EXPECTED,
        competency_id="implementation",
        proof_requirement=proof_requirement,
        required_artifact="A code change",
    )


def _proof(*, kind: str = "reasoning", artifact: str = "", ratings: tuple[str, str] = ("met", "met")) -> LearningProof:
    return LearningProof(
        kind=kind,
        answer="The contract remains additive and the focused check passes.",
        rubric_results=[
            RubricResult(criterion=criterion, rating=rating, evidence="Specific host evaluation")
            for criterion, rating in zip(EXPECTED, ratings, strict=True)
        ],
        artifact_paths=[artifact] if artifact else [],
        verification_evidence=[
            VerificationEvidence(command="pytest -q tests/test_feature.py", exit_code=0, summary="1 passed")
        ]
        if kind == "artifact"
        else [],
        self_assessment="mastered",
        evaluator="codex",
    )


def _status(root: Path) -> str:
    profile = LearnerProfile()
    return next(
        item.status
        for item in derive_competency_summaries([root], profile)
        if item.competency_id == "implementation"
    )


def test_profile_defaults_update_permissions_and_malformed_fallback(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTPACK_HOME", str(home))

    profile, warnings = load_learner_profile()
    assert profile.role == "general"
    assert profile.target_level == "unspecified"
    assert warnings == []

    updated = update_learner_profile(role="backend", target_level="mid")
    path = home / "learner-profile.json"
    assert updated.role == "backend"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    path.write_text("{bad json", encoding="utf-8")
    fallback, warnings = load_learner_profile()
    assert fallback == LearnerProfile()
    assert warnings and path.read_text(encoding="utf-8") == "{bad json"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Define acceptance criteria and scope", "product_reasoning"),
        ("Implement an API contract", "implementation"),
        ("Add a regression test", "quality"),
        ("Analyze concurrency and consistency", "systems"),
        ("Write a rollback runbook", "production"),
        ("Verify authorization at the trust boundary", "security"),
        ("Write a handoff and review response", "collaboration"),
    ],
)
def test_deterministic_competency_mapping(text: str, expected: str) -> None:
    assert map_to_competency(task=text) == expected


def test_unknown_concepts_do_not_create_demonstrated_weakness() -> None:
    assert map_to_competency(concepts=["novel widget protocol"]) is None
    summaries = derive_competency_summaries([], LearnerProfile(role="backend"))
    assert len(summaries) == 7
    assert {item.status for item in summaries} == {"unassessed"}
    assert {item.competency_id for item in summaries if item.role_emphasis} == {
        "implementation",
        "systems",
        "security",
    }


def test_two_distinct_passing_proofs_with_verified_artifact_master_competency(tmp_path: Path) -> None:
    root = _project(tmp_path)
    artifact = root / "src" / "feature.py"
    artifact.parent.mkdir()
    artifact.write_text("VALUE = 1\n", encoding="utf-8")

    first = _session(root, task_id="task-one")
    second = _session(root, task_id="task-two", proof_requirement="artifact")
    assert append_learning_session(root, first)
    assert append_learning_session(root, second)

    recorded, duplicate = record_learning_proof(root, first.session_id, _proof())
    assert recorded.score == 100 and duplicate is False
    assert _status(root) == "developing"

    recorded, duplicate = record_learning_proof(root, second.session_id, _proof(kind="artifact", artifact="src/feature.py"))
    assert recorded.artifact_hashes["src/feature.py"]
    assert duplicate is False
    assert _status(root) == "mastered"

    retried, duplicate = record_learning_proof(root, second.session_id, _proof(kind="artifact", artifact="src/feature.py"))
    assert retried.proof_hash == recorded.proof_hash
    assert duplicate is True

    artifact.unlink()
    retried, duplicate = record_learning_proof(root, second.session_id, _proof(kind="artifact", artifact="src/feature.py"))
    assert retried.proof_hash == recorded.proof_hash
    assert duplicate is True


def test_duplicate_task_proofs_do_not_master_and_later_result_downgrades(tmp_path: Path) -> None:
    root = _project(tmp_path)
    artifact = root / "feature.py"
    artifact.write_text("VALUE = 1\n", encoding="utf-8")
    first = _session(root, task_id="same-task").model_copy(update={"session_id": "session-same-task-reasoning"})
    second = _session(root, task_id="same-task", proof_requirement="artifact").model_copy(
        update={"session_id": "session-same-task-artifact"}
    )
    assert append_learning_session(root, first)
    assert append_learning_session(root, second)
    record_learning_proof(root, first.session_id, _proof())
    record_learning_proof(root, second.session_id, _proof(kind="artifact", artifact="feature.py"))
    assert _status(root) == "developing"

    third = _session(root, task_id="later-task")
    assert append_learning_session(root, third)
    record_learning_proof(root, third.session_id, _proof(ratings=("partial", "met")))
    assert _status(root) == "developing"

    fourth = _session(root, task_id="failed-task")
    assert append_learning_session(root, fourth)
    record_learning_proof(root, fourth.session_id, _proof(ratings=("missing", "partial")))
    assert _status(root) == "needs_practice"


def test_proof_validation_rejects_incomplete_rubric_artifact_failures_and_escape(tmp_path: Path) -> None:
    root = _project(tmp_path)
    session = _session(root, task_id="task-validation", proof_requirement="artifact")
    assert append_learning_session(root, session)
    artifact = root / "feature.py"
    artifact.write_text("VALUE = 1\n", encoding="utf-8")

    incomplete = _proof(kind="artifact", artifact="feature.py").model_copy(
        update={"rubric_results": [RubricResult(criterion=EXPECTED[0], rating="met")]}
    )
    with pytest.raises(ValueError, match="every expected point"):
        record_learning_proof(root, session.session_id, incomplete)

    failed = _proof(kind="artifact", artifact="feature.py").model_copy(
        update={"verification_evidence": [VerificationEvidence(command="pytest", exit_code=1)]}
    )
    with pytest.raises(ValueError, match="exit successfully"):
        record_learning_proof(root, session.session_id, failed)

    escaped = _proof(kind="artifact", artifact="../outside.py")
    with pytest.raises(ValueError, match="traversal"):
        record_learning_proof(root, session.session_id, escaped)

    outside = tmp_path / "outside.py"
    outside.write_text("secret\n", encoding="utf-8")
    link = root / "link.py"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(ValueError, match="escapes"):
        record_learning_proof(root, session.session_id, _proof(kind="artifact", artifact="link.py"))


def test_different_proof_conflicts_and_legacy_evidence_cannot_master(tmp_path: Path) -> None:
    root = _project(tmp_path)
    session = _session(root, task_id="task-conflict")
    assert append_learning_session(root, session)
    record_learning_proof(root, session.session_id, _proof())
    with pytest.raises(LearningProofConflictError):
        record_learning_proof(root, session.session_id, _proof(ratings=("partial", "met")))

    legacy = _session(root, task_id="legacy-task").model_copy(update={"session_id": "session-legacy"})
    assert append_learning_session(root, legacy)
    complete_learning_session(root, legacy.session_id, score=95, self_assessment="mastered")
    assert _status(root) == "developing"


def test_latest_competency_result_uses_chronological_timestamp_order(tmp_path: Path) -> None:
    root = _project(tmp_path)
    earlier = _session(root, task_id="earlier").model_copy(
        update={
            "status": "completed",
            "score": 100,
            "proof": _proof(),
            "updated_at": "2026-01-01T10:00:00+05:30",
        }
    )
    later = _session(root, task_id="later").model_copy(
        update={
            "status": "completed",
            "score": 25,
            "proof": _proof(ratings=("missing", "partial")),
            "updated_at": "2026-01-01T05:00:00+00:00",
        }
    )
    assert append_learning_session(root, earlier)
    assert append_learning_session(root, later)

    assert _status(root) == "needs_practice"


def test_profile_role_and_level_validation(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    with pytest.raises(ValueError, match="role must be"):
        update_learner_profile(role="database", path=path)
    with pytest.raises(ValueError, match="level must be"):
        update_learner_profile(target_level="principal", path=path)
    assert not path.exists()


def test_profile_file_contains_only_bounded_global_fields(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    update_learner_profile(role="mobile", target_level="senior", path=path)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "schema_version",
        "role",
        "target_level",
        "updated_at",
    }
