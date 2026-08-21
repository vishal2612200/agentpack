from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentpack.core.project_index import register_project
from agentpack.learning.competencies import update_learner_profile
from agentpack.learning.models import LearningSession
from agentpack.learning.recommender import (
    recommend_learning_topics,
    record_recommendation_impressions,
)
from agentpack.learning.sessions import (
    append_learning_session,
    complete_learning_session,
    derive_mastery_status,
    read_learning_sessions,
    summarize_mastery,
    summarize_weak_spots,
)


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_agentpack_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "agentpack-home"))


def _project(root: Path) -> Path:
    (root / ".agentpack").mkdir(parents=True)
    (root / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    return root


def _write_task_memories(root: Path, rows: list[dict]) -> None:
    path = root / ".agentpack" / "session-events.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_recommender_selects_now_assessed_weak_spot_and_breadth(tmp_path) -> None:
    root = _project(tmp_path)
    _write_task_memories(
        root,
        [
            {
                "type": "task_memory",
                "timestamp": "2026-07-18T10:00:00+00:00",
                "task_id": "task-cache-1",
                "task": "Fix cache invalidation",
                "status": "done",
                "concepts": ["caching"],
                "changed_files": ["src/cache/store.py"],
            },
            {
                "type": "task_memory",
                "timestamp": "2026-07-19T10:00:00+00:00",
                "task_id": "task-cache-2",
                "task": "Debug failed cache TTL tests",
                "status": "failed",
                "concepts": ["caching"],
                "changed_files": ["src/cache/store.py", "tests/cache/test_store.py"],
            },
        ],
    )
    append_learning_session(
        root,
        LearningSession(
            task="Stabilize API output",
            topic="Stable Serialization",
            question="How should old clients read new fields?",
            expected_points=["additive fields", "stable names"],
            evidence_files=["src/api/schema.py"],
            concepts=["serialization"],
            score=55,
            self_assessment="needs-practice",
            status="completed",
        ),
    )

    first = recommend_learning_topics(root, now=NOW)
    second = recommend_learning_topics(root, now=NOW)

    assert [topic.lane for topic in first.topics] == ["now", "weak_spot", "breadth"]
    assert first.schema_version == 2
    assert len(first.competencies) == 7
    assert first.mastery_summary.needs_practice == 1
    assert all(topic.competency_id and topic.required_artifact for topic in first.topics)
    assert [topic.topic_id for topic in first.topics] == [topic.topic_id for topic in second.topics]
    assert first.topics[0].score_reasons["friction"] == 10
    assert first.topics[0].score_reasons["recurrence"] == 5
    assert first.topics[1].mastery_status == "needs_practice"
    assert first.topics[2].evidence[0].kind == "competency_gap"
    assert "not a demonstrated weakness" in first.topics[2].why_now
    assert all(topic.evidence for topic in first.topics)


def test_current_and_failed_evidence_for_same_capability_keep_distinct_lanes(tmp_path) -> None:
    root = _project(tmp_path)
    _write_task_memories(
        root,
        [
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task_id": "task-api-current",
                "task": "Stabilize API serialization",
                "concepts": ["serialization"],
                "changed_files": ["src/api/schema.py"],
            }
        ],
    )
    append_learning_session(
        root,
        LearningSession(
            task="Stabilize API serialization",
            topic="Stable Serialization",
            question="How should old clients read new fields?",
            expected_points=["additive fields", "stable names"],
            evidence_files=["src/api/schema.py"],
            concepts=["serialization"],
            score=55,
            self_assessment="needs-practice",
            status="completed",
        ),
    )

    recommendations = recommend_learning_topics(root, now=NOW)

    assert [topic.lane for topic in recommendations.topics] == ["now", "weak_spot", "breadth"]
    assert recommendations.topics[0].topic_id != recommendations.topics[1].topic_id


def test_queued_session_is_unassessed_until_scored(tmp_path) -> None:
    root = _project(tmp_path)
    queued = LearningSession(
        session_id="session-queued",
        task="Fix cache TTL",
        topic="Cache Correctness",
        question="What invalidates this cache?",
        concepts=["caching"],
    )
    assert append_learning_session(root, queued)

    assert summarize_weak_spots(root) == []
    assert summarize_mastery(root)["unassessed"] == 1

    completed = complete_learning_session(
        root,
        "session-queued",
        score=45,
        self_assessment="needs-practice",
    )

    assert completed.mastery_status == "needs_practice"
    assert summarize_weak_spots(root)[0]["concept"] == "caching"


def test_single_session_status_never_claims_mastery() -> None:
    base = LearningSession(task="Review retries", topic="Safe Retry Logic")

    assert derive_mastery_status(base) == "unassessed"
    assert derive_mastery_status(base.model_copy(update={"score": 69, "self_assessment": "mastered"})) == "needs_practice"
    assert derive_mastery_status(base.model_copy(update={"score": 75, "self_assessment": "mastered"})) == "developing"
    assert derive_mastery_status(base.model_copy(update={"score": 80, "self_assessment": "needs-practice"})) == "needs_practice"
    assert derive_mastery_status(base.model_copy(update={"score": 80, "self_assessment": "mastered"})) == "developing"


def test_recommendation_history_applies_cooldown(tmp_path) -> None:
    root = _project(tmp_path)
    _write_task_memories(
        root,
        [
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task_id": "task-cli",
                "task": "Improve CLI command output",
                "status": "done",
                "concepts": ["CLI design"],
                "changed_files": ["src/commands/learn.py"],
            }
        ],
    )
    initial = recommend_learning_topics(root, now=NOW)
    recorded = record_recommendation_impressions(initial)

    reranked = recommend_learning_topics(root, now=NOW.replace(hour=13))

    assert not [warning for warning in recorded.warnings if "could not record" in warning]
    assert reranked.topics[0].score_reasons["cooldown"] == -25


def test_active_work_receives_active_and_recent_relevance(tmp_path) -> None:
    root = _project(tmp_path)
    (root / ".agentpack" / "task-starts.jsonl").write_text(
        json.dumps(
            {
                "task_id": "task-active",
                "task": "Fix authentication token expiry",
                "started_at": NOW.isoformat(),
                "selected_files": ["src/auth/token.py"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    recommendations = recommend_learning_topics(root, now=NOW)

    assert recommendations.topics[0].score_reasons["current_relevance"] == 30
    assert recommendations.topics[0].score_reasons["recent_relevance"] == 20


def test_free_text_biases_ranking_without_changing_formula_score(tmp_path) -> None:
    root = _project(tmp_path)
    _write_task_memories(
        root,
        [
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task_id": "task-auth",
                "task": "Review authentication behavior",
                "concepts": ["authentication"],
                "changed_files": ["src/auth.py"],
            },
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task_id": "task-retry",
                "task": "Review retry behavior",
                "concepts": ["retry logic"],
                "changed_files": ["src/retry.py"],
            },
        ],
    )

    recommendations = recommend_learning_topics(root, request="retry", now=NOW)

    assert recommendations.topics[0].concepts == ["retry logic"]
    assert recommendations.topics[0].score == 25
    assert "request_match" not in recommendations.topics[0].score_reasons


def test_role_emphasis_adds_bonus_without_changing_competency_mapping(tmp_path) -> None:
    root = _project(tmp_path)
    update_learner_profile(role="backend", target_level="mid")
    _write_task_memories(
        root,
        [
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task_id": "task-auth",
                "task": "Verify authorization behavior",
                "concepts": ["authorization"],
                "changed_files": ["src/auth.py"],
            },
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task_id": "task-test",
                "task": "Add a regression test",
                "concepts": ["testing"],
                "changed_files": ["tests/test_feature.py"],
            },
        ],
    )

    recommendations = recommend_learning_topics(root, now=NOW)

    assert recommendations.profile.role == "backend"
    assert recommendations.profile.target_level == "mid"
    assert recommendations.topics[0].competency_id == "security"
    assert recommendations.topics[0].score_reasons["role_emphasis"] == 10


def test_newer_friction_bypasses_cooldown_across_iso_formats(tmp_path) -> None:
    root = _project(tmp_path)
    _write_task_memories(
        root,
        [
            {
                "type": "task_memory",
                "timestamp": "2026-07-19T09:00:00+00:00",
                "task_id": "task-cli-1",
                "task": "Improve CLI output",
                "status": "done",
                "concepts": ["CLI design"],
                "changed_files": ["src/commands/learn.py"],
            }
        ],
    )
    topic_id = recommend_learning_topics(root, now=NOW).topics[0].topic_id
    _write_task_memories(
        root,
        [
            {
                "type": "learning_recommendation_shown",
                "timestamp": "2026-07-19T10:00:00Z",
                "topic_id": topic_id,
            },
            {
                "type": "task_memory",
                "timestamp": "2026-07-19T11:00:00+00:00",
                "task_id": "task-cli-2",
                "task": "Debug failed CLI output test",
                "status": "failed",
                "concepts": ["CLI design"],
                "changed_files": ["src/commands/learn.py"],
            },
        ],
    )

    reranked = recommend_learning_topics(root, now=NOW)

    assert "cooldown" not in reranked.topics[0].score_reasons
    assert reranked.topics[0].score_reasons["friction"] == 10


def test_global_recommendations_use_registered_projects_and_skip_missing(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTPACK_HOME", str(home))
    first = _project(tmp_path / "first")
    second = _project(tmp_path / "second")
    _write_task_memories(
        first,
        [
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task": "Fix auth token expiry",
                "task_id": "auth",
                "concepts": ["authentication"],
                "changed_files": ["src/auth/token.py"],
            }
        ],
    )
    _write_task_memories(
        second,
        [
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task": "Bound worker retries",
                "task_id": "retry",
                "concepts": ["retry logic"],
                "changed_files": ["src/worker/retry.py"],
            }
        ],
    )
    register_project(first, now=NOW)
    register_project(second, now=NOW)
    index = json.loads((home / "projects.json").read_text(encoding="utf-8"))
    index["projects"].append(
        {
            "path": str(tmp_path / "missing"),
            "name": "missing",
            "last_seen_at": NOW.isoformat(),
        }
    )
    (home / "projects.json").write_text(json.dumps(index), encoding="utf-8")

    recommendations = recommend_learning_topics(first, global_scope=True, now=NOW)

    assert recommendations.scope == "global"
    assert len({topic.project.project_id for topic in recommendations.topics[:2]}) == 2
    assert any("missing or inaccessible" in warning for warning in recommendations.warnings)
    assert all("--project" in topic.start_command for topic in recommendations.topics)


def test_global_recommendations_skip_read_only_projects(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTPACK_HOME", str(home))
    writable = _project(tmp_path / "writable")
    read_only = _project(tmp_path / "read-only")
    for root, task_id in ((writable, "writable"), (read_only, "read-only")):
        _write_task_memories(
            root,
            [
                {
                    "type": "task_memory",
                    "timestamp": NOW.isoformat(),
                    "task_id": task_id,
                    "task": f"Review {task_id} retry behavior",
                    "concepts": ["retry logic"],
                    "changed_files": ["src/retry.py"],
                }
            ],
        )
        register_project(root, now=NOW)
    real_access = os.access

    def fake_access(path, mode):
        if Path(path) == read_only / ".agentpack" and mode == os.W_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr("agentpack.learning.recommender.os.access", fake_access)

    recommendations = recommend_learning_topics(writable, global_scope=True, now=NOW)

    assert {topic.project.root for topic in recommendations.topics} == {str(writable)}
    assert any("read-only" in warning for warning in recommendations.warnings)


def test_recommender_returns_insufficient_history_without_inventing_topics(
    tmp_path,
) -> None:
    root = _project(tmp_path)

    recommendations = recommend_learning_topics(root, now=NOW)

    assert recommendations.topics == []
    assert recommendations.warnings == ["insufficient_history: fewer than three evidence-backed topics are available"]


def test_recommender_keeps_partial_results_when_artifacts_are_malformed(tmp_path) -> None:
    root = _project(tmp_path)
    _write_task_memories(
        root,
        [
            {
                "type": "task_memory",
                "timestamp": NOW.isoformat(),
                "task_id": "task-cli",
                "task": "Improve CLI output",
                "concepts": ["CLI design"],
                "changed_files": ["src/commands/learn.py"],
            }
        ],
    )
    (root / ".agentpack" / "episodic-cases.jsonl").write_text("{bad-json\n", encoding="utf-8")
    (root / ".agentpack" / "learning-sessions.jsonl").write_text("[]\n", encoding="utf-8")

    recommendations = recommend_learning_topics(root, now=NOW)

    assert recommendations.topics
    assert any("malformed learning records" in warning for warning in recommendations.warnings)
    assert any("malformed learning sessions" in warning for warning in recommendations.warnings)


def test_legacy_sessions_load_with_stable_ids_without_rewriting(tmp_path) -> None:
    root = _project(tmp_path)
    path = root / ".agentpack" / "learning-sessions.jsonl"
    legacy = {
        "task": "Review cache behavior",
        "topic": "Cache Correctness",
        "question": "When is the cache stale?",
        "concepts": ["caching"],
        "created_at": "2026-07-18T10:00:00+00:00",
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    first = read_learning_sessions(root)[0]
    second = read_learning_sessions(root)[0]

    assert first.session_id == second.session_id
    assert first.topic_id == second.topic_id
    assert first.mastery_status == "unassessed"
    assert path.read_text(encoding="utf-8") == before
