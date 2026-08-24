from __future__ import annotations

import json
from pathlib import Path

from agentpack.core.project_index import register_project
from agentpack.dashboard.contracts import CachedProjectProfile, CachedProjectStatus
from agentpack.dashboard.github import _normalize_repository
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
