from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

from agentpack.core.project_index import register_project
from agentpack.dashboard.contracts import CachedProjectProfile, CachedProjectStatus
from agentpack.dashboard.github import _GhError, _gh_json, _normalize_repository, _refresh_one
from agentpack.dashboard.models import ProjectHealthSnapshot, ProjectMetrics
from agentpack.dashboard.portfolio import build_portfolio_payload, write_status_cache


def _project(root: Path, key: str, *, display_name: str = "") -> None:
    (root / ".agentpack").mkdir(parents=True)
    (root / ".agentpack" / "config.toml").write_text(
        f'[project]\nkey = "{key}"\ndisplay_name = "{display_name or key}"\n',
        encoding="utf-8",
    )
    register_project(root)


def test_portfolio_groups_index_rows_and_preserves_stale_projects(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    first = tmp_path / "first"
    second = tmp_path / "second"
    _project(first, "first", display_name="First Project")
    _project(second, "second", display_name="Second Project")
    payload = build_portfolio_payload(first, include_inferred=False)
    names = {item["name"] for item in payload["projects"]}
    assert {"First Project", "Second Project"} <= names
    assert all(item["stale"] for item in payload["projects"])
    assert payload["partial"] is True


def test_portfolio_infers_exact_manifest_dependency_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    consumer = tmp_path / "consumer"
    provider = tmp_path / "provider"
    similar = tmp_path / "similar"
    _project(consumer, "consumer")
    _project(provider, "provider")
    _project(similar, "provider-tools")
    (consumer / "package.json").write_text(json.dumps({"name": "consumer", "dependencies": {"provider": "workspace:*"}}), encoding="utf-8")
    (provider / "package.json").write_text(json.dumps({"name": "provider"}), encoding="utf-8")
    (similar / "package.json").write_text(json.dumps({"name": "provider-tools"}), encoding="utf-8")
    payload = build_portfolio_payload(consumer, include_inferred=True)
    edges = [item for item in payload["relations"] if item["type"] == "depends_on"]
    assert len(edges) == 1
    assert edges[0]["label"] == "provider"
    assert edges[0]["confidence"] == 0.9
    assert edges[0]["declared"] is False


def test_portfolio_uses_index_relations_when_legacy_cache_profile_lacks_relations(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home" / ".agentpack"
    monkeypatch.setenv("AGENTPACK_HOME", str(home))
    root = tmp_path / "root"
    _project(root, "root")
    index_path = home / "projects.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["projects"][0]["relations"] = [{"target": "missing", "type": "depends_on", "label": "Missing dependency"}]
    index_path.write_text(json.dumps(index), encoding="utf-8")
    write_status_cache(CachedProjectStatus(
        project_id=index["projects"][0]["project_id"],
        generated_at="2026-08-24T00:00:00+00:00",
        profile=CachedProjectProfile(display_name="Cached root"),
        metrics=ProjectMetrics(),
        health=ProjectHealthSnapshot(),
    ))

    payload = build_portfolio_payload(root, include_inferred=False)

    assert payload["relations"][0]["label"] == "Missing dependency"
    assert payload["relations"][0]["unresolved"] is True


def test_github_repository_normalization_rejects_non_github_shapes() -> None:
    assert _normalize_repository("https://github.com/openai/agentpack.git") == "openai/agentpack"
    assert _normalize_repository("git@github.com:openai/agentpack.git") == "openai/agentpack"
    assert _normalize_repository("https://example.com/openai/agentpack") == ""


def test_github_refresh_collects_normalized_records_and_writes_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    root = tmp_path / "workspace"
    root.mkdir()
    project = {
        "project_id": "project-1",
        "git_sha": "a" * 40,
        "links": {"github": "https://github.com/acme/agentpack.git"},
        "workspaces": [{"path": str(root)}],
    }
    head_sha = "b" * 40

    def fake_gh_json(arguments: list[str], *, timeout: int):
        assert timeout == 10
        if arguments[:2] == ["pr", "list"]:
            return [{"number": 12, "title": "Ship Atlas", "isDraft": False, "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN", "headRefOid": head_sha, "updatedAt": "2026-08-29T10:00:00Z"}]
        if arguments[:2] == ["api", "repos/acme/agentpack/commits/" + head_sha + "/check-runs"]:
            return [{"name": "ci", "conclusion": "success"}]
        if arguments[:2] == ["issue", "list"]:
            return [{"number": 7, "title": "Attention", "updatedAt": "2026-08-29T09:00:00Z", "labels": [{"name": "bug"}]}]
        if arguments[:2] == ["release", "list"]:
            return [{"tagName": "v1.2.3", "publishedAt": "2026-08-28T10:00:00Z"}]
        raise AssertionError(arguments)

    monkeypatch.setattr("agentpack.dashboard.github._gh_json", fake_gh_json)

    result = _refresh_one(project, Event())

    assert result["status"] == "ok"
    assert result["repository"] == "acme/agentpack"
    assert result["pull_requests"][0]["number"] == 12
    assert result["checks"] == [{"name": "ci", "conclusion": "success", "sha": head_sha, "pr_number": 12}]
    assert result["open_issue_count"] == 1
    assert result["latest_release"] == {"tag": "v1.2.3", "published_at": "2026-08-28T10:00:00Z"}
    cache = json.loads((tmp_path / "home" / ".agentpack" / "dashboard" / "github" / "project-1.json").read_text(encoding="utf-8"))
    assert cache == {key: value for key, value in result.items() if key != "project_id"}


def test_github_refresh_rejects_malformed_pr_sha_without_check_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    root = tmp_path / "workspace"
    root.mkdir()
    project = {"project_id": "project-1", "links": {"github": "acme/agentpack"}, "workspaces": [{"path": str(root)}]}
    calls: list[list[str]] = []

    def fake_gh_json(arguments: list[str], *, timeout: int):
        calls.append(arguments)
        if arguments[:2] == ["pr", "list"]:
            return [{"number": 12, "headRefOid": "not-a-sha"}]
        if arguments[:2] == ["issue", "list"]:
            return []
        if arguments[:2] == ["release", "list"]:
            return []
        raise AssertionError(arguments)

    monkeypatch.setattr("agentpack.dashboard.github._gh_json", fake_gh_json)

    result = _refresh_one(project, Event())

    assert result["status"] == "ok"
    assert result["checks"] == []
    assert not any(arguments[:1] == ["api"] for arguments in calls)


def test_github_cli_failures_are_bounded_and_do_not_expose_stderr(monkeypatch) -> None:
    class Result:
        returncode = 1
        stdout = ""
        stderr = "rate limit exceeded token=secret-fixture"

    monkeypatch.setattr("agentpack.dashboard.github.subprocess.run", lambda *args, **kwargs: Result())

    with pytest.raises(_GhError) as error:
        _gh_json(["pr", "list"], timeout=10)

    assert error.value.kind == "rate_limited"
    assert "secret-fixture" not in error.value.remediation


def test_github_cli_timeout_returns_remediation(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("agentpack.dashboard.github.subprocess.run", timeout)

    with pytest.raises(_GhError) as error:
        _gh_json(["issue", "list"], timeout=10)

    assert error.value.kind == "timeout"
    assert "retry" in error.value.remediation


def test_github_cache_is_marked_last_known_after_fifteen_minutes(tmp_path: Path, monkeypatch) -> None:
    from agentpack.dashboard.portfolio import github_cache_path, read_github_cache

    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home" / ".agentpack"))
    github_cache_path("project-1").parent.mkdir(parents=True)
    fetched_at = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    github_cache_path("project-1").write_text(json.dumps({"status": "ok", "fetched_at": fetched_at}), encoding="utf-8")

    cache = read_github_cache("project-1")

    assert cache is not None
    assert cache["stale"] is True
    assert cache["cache_age_seconds"] >= 15 * 60
