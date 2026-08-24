from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentpack.core.project_index import load_project_index, project_id, register_project


def test_register_project_writes_and_updates_global_index(tmp_path) -> None:
    index = tmp_path / "home" / "projects.json"
    repo = tmp_path / "repo"
    (repo / ".agentpack").mkdir(parents=True)
    (repo / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")

    first = register_project(repo, index, now=datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc))
    second = register_project(repo, index, now=datetime(2026, 7, 8, 11, 0, tzinfo=timezone.utc))

    rows = load_project_index(index)
    assert first["path"] == str(repo.resolve())
    assert first["project_id"] == project_id(repo)
    assert second["project_id"] == first["project_id"]
    assert second["first_seen_at"] == first["first_seen_at"]
    assert len(rows) == 1
    assert rows[0]["path"] == str(repo.resolve())
    assert rows[0]["last_seen_at"].startswith("2026-07-08T11:00:00")


def test_load_project_index_ignores_missing_and_malformed_files(tmp_path) -> None:
    index = tmp_path / "projects.json"

    assert load_project_index(index) == []

    index.write_text("{not-json", encoding="utf-8")
    assert load_project_index(index) == []

    index.write_text(json.dumps({"projects": [{"path": ""}, {"path": "/repo"}]}), encoding="utf-8")
    assert load_project_index(index) == [{"path": "/repo", "project_id": project_id(Path("/repo"))}]


def test_load_project_index_recomputes_legacy_id_for_reachable_path(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    index = tmp_path / "projects.json"
    index.write_text(json.dumps({"projects": [{"path": str(repo), "project_id": "legacy-path-hash"}]}), encoding="utf-8")

    rows = load_project_index(index)

    assert rows[0]["project_id"] == project_id(repo)
