from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentpack.core.config import load_config
from agentpack.dashboard.project_overview import (
    ProjectConfigConflict,
    ProjectValidationError,
    append_project_event,
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
    assert warnings == []
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
