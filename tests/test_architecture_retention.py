from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from typer.testing import CliRunner

import agentpack.architecture.retention as retention_module
from agentpack.cli import app
from agentpack.architecture.retention import prune_architecture_cache
from agentpack.architecture.service import SCHEMA_VERSION, _extractor_profile_hash, _repo_fingerprint
from agentpack.core.config import load_config


SCHEMA = 8
PROFILE = "abcdef1234567890"
REPO = "1234567890abcdef"
runner = CliRunner()


def test_architecture_retention_defaults_to_three_refs() -> None:
    assert load_config(Path("/tmp/nonexistent-agentpack-test")).architecture.max_cached_refs == 3


def test_prune_keeps_worktree_and_newest_refs_with_reachable_dependencies(tmp_path: Path) -> None:
    cache = tmp_path / ".agentpack" / "architecture"
    cache.mkdir(parents=True)
    for index in range(5):
        commit = f"{index + 1:040x}"
        _write_state(cache, f"ref-{index}", commit, index + 1, record=f"record-{index}", fact=f"fact-{index}")
        _write(cache / f"{commit}-{SCHEMA}-{PROFILE}.json", "snapshot")
    _write_state(cache, "WORKTREE", "worktree-sha", 20, record="record-worktree", fact="fact-worktree")
    _write(cache / f"worktree-manifest-{SCHEMA}-{PROFILE}.json", "worktree")
    _write(cache / "old-7-legacy.json", "preserve unknown file")

    dry_run = prune_architecture_cache(
        tmp_path,
        keep_refs=2,
        dry_run=True,
        force=True,
        schema_version=SCHEMA,
        extractor_profile_hash=PROFILE,
        repo_fingerprint=REPO,
    )

    assert dry_run.deleted_files > 0
    assert (cache / f"{1:040x}-{SCHEMA}-{PROFILE}.json").exists()

    applied = prune_architecture_cache(
        tmp_path,
        keep_refs=2,
        dry_run=False,
        force=True,
        schema_version=SCHEMA,
        extractor_profile_hash=PROFILE,
        repo_fingerprint=REPO,
    )

    assert applied.deleted_bytes > 0
    assert len(
        [
            path
            for path in cache.glob(f"*-{SCHEMA}-{PROFILE}.json")
            if not path.name.startswith("worktree-")
        ]
    ) == 2
    assert len(list(cache.glob(f"worktree-*-{SCHEMA}-{PROFILE}.json"))) == 1
    assert len(list((cache / "state").glob("*.json"))) == 3
    assert len(list((cache / "manifests").glob("*.json"))) == 3
    assert (cache / "records" / "record-3.json").exists()
    assert (cache / "records" / "record-4.json").exists()
    assert (cache / "records" / "record-worktree.json").exists()
    assert not (cache / "records" / "record-0.json").exists()
    assert (cache / "facts" / "fact-3.json").exists()
    assert not (cache / "facts" / "fact-0.json").exists()
    assert (cache / "old-7-legacy.json").exists()


def test_prune_preserves_shared_records_and_facts(tmp_path: Path) -> None:
    cache = tmp_path / ".agentpack" / "architecture"
    cache.mkdir(parents=True)
    _write_state(cache, "ref-a", f"{1:040x}", 1, record="orphan", fact="orphan-fact")
    _write_state(cache, "ref-b", f"{2:040x}", 2, record="shared", fact="shared-fact")
    _write_state(cache, "ref-c", f"{3:040x}", 3, record="shared", fact="shared-fact")
    _write_state(cache, "ref-d", f"{4:040x}", 4, record="kept", fact="kept-fact")
    for index in range(1, 5):
        _write(cache / f"{index:040x}-{SCHEMA}-{PROFILE}.json", "snapshot")
    _write(cache / "records" / "shared.json", "shared")
    _write(cache / "records" / "orphan.json", "orphan")
    _write(cache / "records" / "kept.json", "kept")
    _write(cache / "facts" / "shared-fact.json", "shared")
    _write(cache / "facts" / "orphan-fact.json", "orphan")
    _write(cache / "facts" / "kept-fact.json", "kept")

    prune_architecture_cache(
        tmp_path,
        keep_refs=2,
        dry_run=False,
        force=True,
        schema_version=SCHEMA,
        extractor_profile_hash=PROFILE,
        repo_fingerprint=REPO,
    )

    assert (cache / "records" / "shared.json").exists()
    assert (cache / "facts" / "shared-fact.json").exists()
    assert not (cache / "records" / "orphan.json").exists()
    assert not (cache / "facts" / "orphan-fact.json").exists()


def test_byte_budget_evicts_oldest_optional_refs(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / ".agentpack" / "architecture"
    cache.mkdir(parents=True)
    for index in range(4):
        commit = f"{index + 1:040x}"
        _write_state(cache, f"ref-{index}", commit, index + 1, record=f"record-{index}", fact=f"fact-{index}")
        _write(cache / f"{commit}-{SCHEMA}-{PROFILE}.json", "snapshot")

    state_paths = sorted((cache / "state").glob("*.json"), key=lambda path: path.stat().st_mtime)
    retained_paths = [
        cache / f"{index:040x}-{SCHEMA}-{PROFILE}.json"
        for index in (3, 4)
    ]
    retained_paths += [
        cache / "records" / f"record-{index}.json"
        for index in (2, 3)
    ]
    retained_paths += [
        cache / "facts" / f"fact-{index}.json"
        for index in (2, 3)
    ]
    for state_path in state_paths[-2:]:
        retained_paths.extend((state_path, cache / "manifests" / state_path.name))
    budget = sum(path.stat().st_size for path in retained_paths) + 100
    config = load_config(tmp_path)
    config.architecture.max_cache_bytes = budget
    monkeypatch.setattr(retention_module, "load_config", lambda root: config)

    report = prune_architecture_cache(
        tmp_path,
        keep_refs=3,
        dry_run=False,
        force=True,
        schema_version=SCHEMA,
        extractor_profile_hash=PROFILE,
        repo_fingerprint=REPO,
    )

    assert report.max_cache_bytes == budget
    assert not report.over_budget
    assert (cache / f"{3:040x}-{SCHEMA}-{PROFILE}.json").exists()
    assert (cache / f"{4:040x}-{SCHEMA}-{PROFILE}.json").exists()
    assert not (cache / f"{1:040x}-{SCHEMA}-{PROFILE}.json").exists()
    assert not (cache / f"{2:040x}-{SCHEMA}-{PROFILE}.json").exists()


def test_malformed_current_state_fails_closed_for_dependency_sweep(tmp_path: Path) -> None:
    cache = tmp_path / ".agentpack" / "architecture"
    cache.mkdir(parents=True)
    state_dir = cache / "state"
    state_dir.mkdir()
    _write(state_dir / f"{REPO}-{PROFILE}-deadbeefdeadbeef.json", "not-json")
    _write(cache / f"{1:040x}-{SCHEMA}-{PROFILE}.json", "snapshot")
    _write(cache / "records" / "live.json", "live")
    _write(cache / "facts" / "live.json", "live")

    report = prune_architecture_cache(
        tmp_path,
        keep_refs=2,
        dry_run=False,
        force=True,
        schema_version=SCHEMA,
        extractor_profile_hash=PROFILE,
        repo_fingerprint=REPO,
    )

    assert report.skipped_files
    assert (cache / "records" / "live.json").exists()
    assert (cache / "facts" / "live.json").exists()
    assert (cache / f"{1:040x}-{SCHEMA}-{PROFILE}.json").exists()


def test_prune_cli_defaults_to_dry_run_and_yes_applies(tmp_path: Path, monkeypatch) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    cache = tmp_path / ".agentpack" / "architecture"
    cache.mkdir(parents=True)
    profile = _extractor_profile_hash()
    repo = _repo_fingerprint(tmp_path)
    snapshot = cache / f"{1:040x}-{SCHEMA_VERSION}-{profile}.json"
    snapshot.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    dry_run = runner.invoke(app, ["architecture", "prune", "--json"])
    assert dry_run.exit_code == 0, dry_run.output
    assert json.loads(dry_run.output)["dry_run"] is True
    assert snapshot.exists()

    applied = runner.invoke(app, ["architecture", "prune", "--yes", "--json"])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["dry_run"] is False
    assert not snapshot.exists()
    assert repo


def _write_state(cache: Path, ref: str, commit: str, age: int, *, record: str, fact: str) -> None:
    state_dir = cache / "state"
    manifest_dir = cache / "manifests"
    namespace = f"{age:016x}"
    name = f"{REPO}-{PROFILE}-{namespace}.json"
    payload = {
        "schema_version": SCHEMA,
        "repository_identity": REPO,
        "ref": ref,
        "commit_sha": commit,
        "extractor_profile_hash": PROFILE,
        "record_keys": {"src/file.py": record},
        "manifest": {"files": {"src/file.py": {"cache_path": f"{fact}.json"}}},
        "snapshot": {},
    }
    _write(state_dir / name, json.dumps(payload, indent=2))
    _write(manifest_dir / name, json.dumps({"files": payload["manifest"]["files"]}))
    timestamp = 1_700_000_000 + age
    os.utime(state_dir / name, (timestamp, timestamp))
    os.utime(manifest_dir / name, (timestamp, timestamp))
    _write(cache / "records" / f"{record}.json", record)
    _write(cache / "facts" / f"{fact}.json", fact)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
