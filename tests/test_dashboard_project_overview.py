from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentpack.core.config import load_config
from agentpack.dashboard.project_overview import (
    ProjectConfigConflict,
    ProjectValidationError,
    append_project_event,
    build_project_overview,
    build_project_status_brief,
    build_project_timeline,
    declared_outcomes,
    deterministic_entity_id,
    discover_project_workspaces,
    fold_project_events,
    load_project_profile,
    project_config_revision,
    read_project_events,
    select_project_workspaces,
    update_project_profile,
)
from agentpack.learning.models import LearningSession
from agentpack.learning.sessions import append_learning_session
from agentpack.session.events import record_event


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _repository(root: Path) -> Path:
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "AgentPack Test")
    (root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "fixture")
    return root


def test_project_config_defaults_remain_compatible() -> None:
    config = load_config(Path("/path/that/does/not/exist"))

    assert config.project.display_name == ""
    assert config.project.outcomes == []
    assert config.project.status_stale_days == 14


def test_profile_update_preserves_unknown_toml_and_generates_ids(tmp_path: Path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    config_path = agentpack / "config.toml"
    config_path.write_text(
        "[project]\ninclude_globs = [\"src/**\"]\nunknown_project_key = \"keep\"\n\n"
        "[custom]\nenabled = true\n",
        encoding="utf-8",
    )
    revision = project_config_revision(tmp_path)

    profile = update_project_profile(
        tmp_path,
        {
            "display_name": "AgentPack",
            "purpose": "Keep AI-assisted software changes reviewable.",
            "owners": ["Platform"],
            "stage": "active",
            "links": {"repository": "https://github.com/example/agentpack"},
            "outcomes": [
                {
                    "title": "Ship project dashboard",
                    "milestones": [{"title": "Typed project API", "due_date": "2026-08-01"}],
                }
            ],
        },
        expected_revision=revision,
    )

    text = config_path.read_text(encoding="utf-8")
    assert 'unknown_project_key = "keep"' in text
    assert "[custom]" in text
    assert "enabled = true" in text
    assert profile.display_name == "AgentPack"
    assert profile.config_revision != revision
    outcomes = declared_outcomes(tmp_path)
    assert outcomes[0]["id"] == deterministic_entity_id(profile.project_id, "outcome", "Ship project dashboard")
    assert outcomes[0]["milestones"][0]["id"].startswith("milestone-")


def test_profile_update_rejects_stale_revision_and_unsafe_values(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    revision = project_config_revision(tmp_path)
    update_project_profile(tmp_path, {"display_name": "One"}, expected_revision=revision)

    with pytest.raises(ProjectConfigConflict):
        update_project_profile(tmp_path, {"display_name": "Two"}, expected_revision=revision)
    with pytest.raises(ProjectValidationError, match="http or https"):
        update_project_profile(
            tmp_path,
            {"links": {"docs": "javascript:alert(1)"}},
            expected_revision=project_config_revision(tmp_path),
        )
    with pytest.raises(ProjectValidationError, match="YYYY-MM-DD"):
        update_project_profile(
            tmp_path,
            {"outcomes": [{"title": "Outcome", "target_date": "08/01/2026"}]},
            expected_revision=project_config_revision(tmp_path),
        )


def test_worktree_discovery_is_bounded_and_current_first(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-b", "feature/dashboard", str(linked))

    workspaces, warnings = discover_project_workspaces(root)

    assert warnings == []
    assert [Path(item.path) for item in workspaces] == [root.resolve(), linked.resolve()]
    assert workspaces[0].is_current is True
    assert workspaces[1].branch == "feature/dashboard"
    assert len({item.workspace_id for item in workspaces}) == 2
    assert select_project_workspaces(workspaces, "current") == [workspaces[0]]
    assert select_project_workspaces(workspaces, workspaces[1].workspace_id) == [workspaces[1]]
    with pytest.raises(ProjectValidationError, match="unknown workspace"):
        select_project_workspaces(workspaces, "workspace-missing")


def test_project_events_are_append_only_idempotent_and_fold_latest(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    workspaces, _warnings = discover_project_workspaces(tmp_path)
    first, duplicate = append_project_event(
        tmp_path,
        "project_outcome_status",
        mutation_id="mutation-1",
        entity_id="outcome-1",
        values={"status": "on_track"},
        evidence=[{"kind": "task", "ref": "task-1", "path": "src/service.py"}],
    )
    repeated, was_duplicate = append_project_event(
        tmp_path,
        "project_outcome_status",
        mutation_id="mutation-1",
        entity_id="outcome-1",
        values={"status": "at_risk"},
    )
    append_project_event(
        tmp_path,
        "project_outcome_status",
        mutation_id="mutation-2",
        entity_id="outcome-1",
        values={"status": "at_risk"},
    )
    with (tmp_path / ".agentpack" / "session-events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{malformed\n")

    events, warnings = read_project_events(workspaces)
    folded = fold_project_events(events, "project_outcome_status")

    assert duplicate is False
    assert was_duplicate is True
    assert repeated["event_id"] == first["event_id"]
    assert len(warnings) == 1
    assert warnings[0].startswith("malformed_events:")
    assert len(events) == 2
    assert folded["outcome-1"]["status"] == "at_risk"
    stored = [json.loads(line) for line in (tmp_path / ".agentpack" / "session-events.jsonl").read_text(encoding="utf-8").splitlines() if line.startswith("{") and not line.startswith("{malformed")]
    assert len(stored) == 2


def test_project_event_rejects_non_relative_evidence(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()

    with pytest.raises(ProjectValidationError, match="repository-relative"):
        append_project_event(
            tmp_path,
            "project_risk_upsert",
            mutation_id="mutation-risk",
            entity_id="risk-1",
            evidence=[{"kind": "file", "path": "/tmp/secret"}],
        )


def test_load_profile_uses_legacy_empty_defaults(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()

    profile = load_project_profile(tmp_path)

    assert profile.display_name == tmp_path.name
    assert profile.source == "declared"
    assert profile.config_revision == project_config_revision(tmp_path)


def test_overview_folds_roadmap_state_and_progress_from_milestones(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    revision = project_config_revision(tmp_path)
    profile = update_project_profile(
        tmp_path,
        {
            "display_name": "Roadmap",
            "outcomes": [
                {
                    "title": "Launch dashboard",
                    "owner": "Product",
                    "milestones": [
                        {"title": "Backend", "owner": "Platform", "due_date": "2099-01-01"},
                        {"title": "Frontend", "owner": "Web", "due_date": "2099-02-01"},
                    ],
                }
            ],
        },
        expected_revision=revision,
    )
    outcome = declared_outcomes(tmp_path)[0]
    append_project_event(
        tmp_path,
        "project_outcome_status",
        mutation_id="outcome-status",
        entity_id=outcome["id"],
        values={"status": "on_track"},
        evidence=[{"kind": "task", "ref": "task-roadmap"}],
    )
    append_project_event(
        tmp_path,
        "project_milestone_status",
        mutation_id="milestone-status",
        entity_id=outcome["milestones"][0]["id"],
        values={"status": "done"},
        evidence=[{"kind": "check", "ref": "check-1"}],
    )

    overview = build_project_overview(tmp_path)

    assert overview.profile.project_id == profile.project_id
    assert overview.outcomes[0].status == "on_track"
    assert overview.outcomes[0].progress_pct == 50.0
    assert overview.metrics.milestone_completion_pct == 50.0
    assert overview.metrics.outcome_count == 1
    assert overview.health.dimensions[0].status == "healthy"


def test_initiative_suggestions_require_two_tasks_and_respect_dismissal(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    for index in range(2):
        record_event(
            tmp_path,
            "task_memory",
            {
                "task": f"Improve dashboard flow {index}",
                "concepts": ["dashboard workflow"],
                "changed_files": [f"src/agentpack/dashboard/view_{index}.py"],
            },
        )

    first = build_project_overview(tmp_path)
    suggestion = first.initiative_suggestions[0]
    assert suggestion.score > 0
    assert len(suggestion.task_ids) == 2
    assert len(suggestion.evidence) >= 2

    append_project_event(
        tmp_path,
        "project_initiative_dismissed",
        mutation_id="dismiss-suggestion",
        entity_id=suggestion.suggestion_id,
        values={"evidence_task_ids": suggestion.task_ids},
    )
    assert suggestion.suggestion_id not in {
        item.suggestion_id for item in build_project_overview(tmp_path).initiative_suggestions
    }

    record_event(
        tmp_path,
        "task_memory",
        {
            "task": "Improve dashboard flow with project health",
            "concepts": ["dashboard workflow"],
            "changed_files": ["src/agentpack/dashboard/health.py"],
        },
    )
    reranked = build_project_overview(tmp_path)
    reappeared = next(item for item in reranked.initiative_suggestions if item.suggestion_id == suggestion.suggestion_id)
    assert len(reappeared.task_ids) == 3


@pytest.mark.parametrize(
    ("check_kind", "status", "git_sha", "expected"),
    [
        ("development", "passed", "current", "healthy"),
        ("development", "failed", "current", "blocked"),
        ("development", "passed", "old", "stale"),
        ("release", "passed", "current", "healthy"),
        ("release", "failed", "current", "blocked"),
        ("release", "failed", "old", "stale"),
    ],
)
def test_health_check_transitions(
    tmp_path: Path,
    check_kind: str,
    status: str,
    git_sha: str,
    expected: str,
) -> None:
    root = _repository(tmp_path / "repo")
    (root / ".agentpack").mkdir()
    current = _git(root, "rev-parse", "HEAD")
    record_event(
        root,
        "check_completed",
        {
            "check_kind": check_kind,
            "status": status,
            "returncode": 0 if status == "passed" else 1,
            "git_sha": current if git_sha == "current" else "0" * 40,
            "command": "agentpack check",
        },
    )

    dimensions = {item.dimension: item for item in build_project_overview(root).health.dimensions}
    dimension = "validation" if check_kind == "development" else "release"
    assert dimensions[dimension].status == expected


def test_architecture_and_delivery_health_transitions(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    (root / ".agentpack").mkdir()
    update_project_profile(
        root,
        {
            "outcomes": [
                {
                    "title": "Ship",
                    "milestones": [{"title": "Gate", "due_date": "2099-01-01"}],
                }
            ]
        },
        expected_revision=project_config_revision(root),
    )
    milestone_id = declared_outcomes(root)[0]["milestones"][0]["id"]
    append_project_event(
        root,
        "project_milestone_status",
        mutation_id="block-gate",
        entity_id=milestone_id,
        values={"status": "blocked"},
    )
    record_event(
        root,
        "check_completed",
        {
            "check_kind": "architecture",
            "status": "passed",
            "git_sha": _git(root, "rev-parse", "HEAD"),
            "blocking_violations": 0,
            "advisory_violations": 2,
        },
    )

    dimensions = {item.dimension: item for item in build_project_overview(root).health.dimensions}

    assert dimensions["delivery"].status == "blocked"
    assert dimensions["architecture"].status == "attention"


def test_context_and_knowledge_keep_missing_or_unassessed_unknown(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    unassessed = LearningSession(
        session_id="session-unassessed",
        topic_id="topic-context",
        task="Understand context",
        topic="Context",
        question="What makes context fresh?",
    )
    append_learning_session(tmp_path, unassessed)

    dimensions = {item.dimension: item for item in build_project_overview(tmp_path).health.dimensions}
    assert dimensions["context"].status == "unknown"
    assert dimensions["knowledge"].status == "unknown"

    (tmp_path / ".agentpack" / "task.md").write_text("Understand context\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(
        json.dumps({"task": "Understand context", "generated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    append_learning_session(
        tmp_path,
        unassessed.model_copy(
            update={
                "session_id": "session-needs-practice",
                "score": 50,
                "self_assessment": "needs-practice",
                "status": "completed",
            }
        ),
    )
    dimensions = {item.dimension: item for item in build_project_overview(tmp_path).health.dimensions}
    assert dimensions["context"].status == "healthy"
    assert dimensions["knowledge"].status == "attention"


def test_timeline_is_deduplicated_filterable_and_bounded(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    (root / ".agentpack").mkdir()
    record_event(root, "task_started", {"task": "Timeline task", "summary": "Started"})
    record_event(
        root,
        "check_completed",
        {"check_kind": "review", "status": "passed", "git_sha": _git(root, "rev-parse", "HEAD")},
    )

    timeline = build_project_timeline(root, limit=2)
    reviews = build_project_timeline(root, kind="review", limit=50)

    assert len(timeline) == 2
    assert len({item.event_id for item in timeline}) == len(timeline)
    assert reviews and all(item.kind == "review" for item in reviews)


def test_briefs_are_deterministic_redacted_and_mode_specific(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    (root / ".agentpack").mkdir()
    secret = "a" * 44
    update_project_profile(
        root,
        {
            "display_name": "AgentPack",
            "purpose": f"Project status token={secret}",
            "owners": ["Platform"],
        },
        expected_revision=project_config_revision(root),
    )
    summary = build_project_status_brief(root, mode="summary")
    engineering = build_project_status_brief(root, mode="engineering")

    assert "[REDACTED:api-key]" in summary.markdown
    assert str(root) not in summary.markdown
    assert "## Engineering Evidence" not in summary.markdown
    assert "## Engineering Evidence" in engineering.markdown
    assert len(engineering.markdown.encode("utf-8")) <= 20 * 1024


def test_selected_worktree_config_is_authoritative(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    (root / ".agentpack").mkdir()
    update_project_profile(
        root,
        {"display_name": "Authoritative"},
        expected_revision=project_config_revision(root),
    )
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-b", "feature/profile", str(linked))
    (linked / ".agentpack").mkdir()
    update_project_profile(
        linked,
        {"display_name": "Other worktree"},
        expected_revision=project_config_revision(linked),
    )

    overview = build_project_overview(root, workspace="all")

    assert overview.profile.display_name == "Authoritative"
    assert len(overview.workspaces) == 2
