from __future__ import annotations

import gzip
import json
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agentpack.core import handoff as handoff_module
from agentpack.cli import app
from agentpack.core.handoff import (
    HandoffError,
    HandoffReport,
    HandoffStore,
    PatchManifest,
    accept_handoff,
    cancel_handoff,
    capture_patch,
    complete_claimed_handoff,
    create_handoff,
    detect_host_session,
    export_handoff,
    import_handoff,
    release_handoff,
)
from agentpack.mcp_server import _get_context_impl


runner = CliRunner()


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=root, input=input_bytes, capture_output=True, check=True)
    return result.stdout.decode("utf-8", "replace").strip()


def repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "remote", "add", "origin", "https://example.com/acme/project.git")
    (root / ".gitignore").write_text(".agentpack/\nignored.txt\n", encoding="utf-8")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    (root / "delete.txt").write_text("delete\n", encoding="utf-8")
    (root / "rename.txt").write_text("rename\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    return root


def report(task: str = "Fix auth session retry") -> dict[str, object]:
    return {
        "task": task,
        "acceptance_criteria": ["Retry is bounded"],
        "summary": "Retry logic is partly implemented.",
        "next_action": "Run the focused retry test.",
        "completed": ["Added retry state"],
        "remaining": ["Add timeout test"],
        "decisions": [{"decision": "Use backoff", "rationale": "Avoid a hot loop"}],
        "blockers": [],
        "validation": [{
            "command": "pytest tests/test_retry.py",
            "outcome": "not_run",
            "tested_sha": "uncommitted",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": "Test not written yet",
        }],
        "changed_files": [],
        "dirty_files": [],
    }


def test_report_requires_not_run_reason() -> None:
    payload = report()
    payload["validation"][0]["reason"] = ""  # type: ignore[index]
    with pytest.raises(ValidationError, match="requires a reason"):
        HandoffReport.model_validate(payload)


def test_patch_manifest_rejects_unsafe_or_inconsistent_paths() -> None:
    with pytest.raises(ValidationError, match="unsafe repository-relative path"):
        PatchManifest(
            sha256="abc",
            base_sha="def",
            compressed_size=1,
            uncompressed_size=1,
            affected_paths=["../outside.txt"],
            post_image_hashes={"../outside.txt": "deleted"},
        )
    with pytest.raises(ValidationError, match="must match"):
        PatchManifest(
            sha256="abc",
            base_sha="def",
            compressed_size=1,
            uncompressed_size=1,
            affected_paths=["inside.txt"],
            post_image_hashes={},
        )


@pytest.mark.parametrize(
    ("variable", "provider"),
    [
        ("CODEX_THREAD_ID", "codex"),
        ("CLAUDE_SESSION_ID", "claude"),
        ("CURSOR_SESSION_ID", "cursor"),
        ("WINDSURF_SESSION_ID", "windsurf"),
        ("GEMINI_SESSION_ID", "gemini"),
        ("ANTIGRAVITY_SESSION_ID", "antigravity"),
        ("CLINE_SESSION_ID", "cline"),
        ("COPILOT_SESSION_ID", "copilot"),
        ("OPENCODE_SESSION_ID", "opencode"),
    ],
)
def test_detect_host_session_supports_every_host(tmp_path: Path, variable: str, provider: str) -> None:
    root = repo(tmp_path)
    session = detect_host_session(root, env={variable: "real-session"})
    assert session.provider == provider
    assert session.session_id == "real-session"


def test_capture_patch_includes_all_git_visible_changes_without_touching_index(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "tracked.txt").write_text("staged\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    index_before = (root / ".git" / "index").read_bytes()
    (root / "tracked.txt").write_text("staged and unstaged\n", encoding="utf-8")
    (root / "untracked.txt").write_text("new\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00\x01\xff\x10")
    (root / "delete.txt").unlink()
    (root / "rename.txt").rename(root / "renamed.txt")
    (root / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    patch, paths, hashes = capture_patch(root)

    assert (root / ".git" / "index").read_bytes() == index_before
    assert {"tracked.txt", "untracked.txt", "binary.bin", "delete.txt", "rename.txt", "renamed.txt"} <= set(paths)
    assert "ignored.txt" not in paths
    assert b"GIT binary patch" in patch
    assert hashes["delete.txt"] == "deleted"


def test_clean_worktree_still_creates_an_empty_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))

    created = create_handoff(root, report(), name="clean-handoff", env={"CODEX_THREAD_ID": "source"})

    assert created.patch.uncompressed_size == 0
    assert created.patch.affected_paths == []
    assert (HandoffStore(root).path(created.name) / "changes.patch.gz").exists()


def test_patch_size_limit_fails_without_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTPACK_HOME", str(home))
    config = root / ".agentpack" / "config.toml"
    config.parent.mkdir()
    config.write_text("[handoff]\nmax_patch_bytes = 8\n", encoding="utf-8")
    (root / "tracked.txt").write_text("a much larger change than eight bytes\n", encoding="utf-8")

    with pytest.raises(HandoffError, match="max_patch_bytes"):
        create_handoff(root, report(), name="too-large", env={"CODEX_THREAD_ID": "source"})
    assert not HandoffStore(root).path("too-large").exists()


def test_create_generates_memorable_collision_names_and_blocks_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
    first = create_handoff(root, report(), env={"CODEX_THREAD_ID": "codex-1"})
    second = create_handoff(root, report(), env={"CODEX_THREAD_ID": "codex-1"})
    assert first.name == "fix-auth-session-retry"
    assert second.name == "fix-auth-session-retry-2"
    assert first.handoff_id.startswith("handoff-")
    assert (HandoffStore(root).path(first.name) / "handoff.json").stat().st_mode & 0o777 == 0o600

    (root / "secret.txt").write_text("token=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN123456\n", encoding="utf-8")
    before = {item.name for item in HandoffStore(root).project_dir.iterdir() if item.is_dir()}
    with pytest.raises(HandoffError, match="secret.txt"):
        create_handoff(root, report("Secret task"), env={"CODEX_THREAD_ID": "codex-1"})
    after = {item.name for item in HandoffStore(root).project_dir.iterdir() if item.is_dir()}
    assert after == before


def test_concurrent_same_name_create_cannot_overwrite_a_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    def create() -> str:
        try:
            return f"created:{create_handoff(root, report(), name='same-name', env={'CODEX_THREAD_ID': 'source'}).name}"
        except HandoffError as exc:
            return f"error:{exc}"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: create(), range(2)))

    assert sum(item.startswith("created:") for item in outcomes) == 1
    assert sum("already exists" in item for item in outcomes) == 1
    stored = HandoffStore(root).load("same-name")
    patch_path = HandoffStore(root).path("same-name") / "changes.patch.gz"
    assert stored.patch.sha256 == handoff_module._sha256(gzip.decompress(patch_path.read_bytes()))


def test_same_worktree_claim_is_atomic_idempotent_and_releasable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
    created = create_handoff(root, report(), name="auth-session-retry", env={"CODEX_THREAD_ID": "source"})

    accepted, _ = accept_handoff(root, created.name, env={"CLAUDE_SESSION_ID": "destination"})
    repeated, _ = accept_handoff(root, created.name, env={"CLAUDE_SESSION_ID": "destination"})
    assert accepted.status == repeated.status == "claimed"
    with pytest.raises(HandoffError, match="another session"):
        accept_handoff(root, created.name, env={"CURSOR_SESSION_ID": "other"})
    released = release_handoff(root, created.name, env={"CLAUDE_SESSION_ID": "destination"})
    assert released.status == "ready"
    cancelled = cancel_handoff(root, created.name)
    assert cancelled.status == "cancelled"
    state = root / ".agentpack" / "threads" / "source" / "task_state.md"
    assert "Status: in_progress" in state.read_text(encoding="utf-8")


def test_source_context_becomes_terminal_after_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    scoped = root / ".agentpack" / "threads" / "source"
    scoped.mkdir(parents=True)
    (scoped / "context.md").write_text("stale source context\n", encoding="utf-8")
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    create_handoff(root, report(), name="terminal-source", env={"CODEX_THREAD_ID": "source"})

    result = _get_context_impl(root, "source")
    assert "marked handed_off" in result
    assert "Handed-off context will not be reused" in result
    assert "stale source context" not in result


def test_concurrent_consumers_allow_exactly_one_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
    created = create_handoff(root, report(), name="single-consumer", env={"CODEX_THREAD_ID": "source"})

    def claim(session: str) -> str:
        try:
            accepted, _ = accept_handoff(root, created.name, env={"CLAUDE_SESSION_ID": session})
            return f"claimed:{accepted.claim.session_id if accepted.claim else ''}"
        except HandoffError as exc:
            return f"error:{exc}"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(claim, ["consumer-one", "consumer-two"]))

    assert sum(item.startswith("claimed:") for item in outcomes) == 1
    assert sum("already claimed by another session" in item for item in outcomes) == 1


def test_complete_claimed_handoff_matches_current_real_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
    created = create_handoff(root, report(), name="finish-me", env={"CODEX_THREAD_ID": "source"})
    accept_handoff(root, created.name, env={"CLAUDE_SESSION_ID": "destination"})

    completed = complete_claimed_handoff(root, env={"CLAUDE_SESSION_ID": "destination"})

    assert completed is not None
    assert completed.status == "completed"


def test_cross_worktree_resume_applies_patch_and_warns_for_unrelated_dirty_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    destination = tmp_path / "destination"
    git(root, "worktree", "add", "-q", str(destination), "-b", "destination")
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("handoff change\n", encoding="utf-8")
    (root / "new.txt").write_text("new file\n", encoding="utf-8")
    created = create_handoff(root, report(), name="cross-worktree", env={"CODEX_THREAD_ID": "source"})
    (destination / "unrelated.txt").write_text("leave me\n", encoding="utf-8")

    accepted, warnings = accept_handoff(destination, created.name, env={"CLAUDE_SESSION_ID": "destination"})

    assert accepted.status == "claimed"
    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "handoff change\n"
    assert (destination / "new.txt").read_text(encoding="utf-8") == "new file\n"
    assert any("unrelated.txt" in warning for warning in warnings)

    release_handoff(destination, created.name, env={"CLAUDE_SESSION_ID": "destination"})
    reclaimed, _ = accept_handoff(destination, created.name, env={"CURSOR_SESSION_ID": "next-session"})
    assert reclaimed.status == "claimed"


def test_cross_worktree_resume_rejects_dirty_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    destination = tmp_path / "destination"
    git(root, "worktree", "add", "-q", str(destination), "-b", "destination")
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("source change\n", encoding="utf-8")
    created = create_handoff(root, report(), name="dirty-overlap", env={"CODEX_THREAD_ID": "source"})
    (destination / "tracked.txt").write_text("destination change\n", encoding="utf-8")

    with pytest.raises(HandoffError, match="dirty affected paths"):
        accept_handoff(destination, created.name, env={"CLAUDE_SESSION_ID": "destination"})
    assert HandoffStore(destination).load(created.name).status == "ready"


def test_descendant_history_is_allowed_when_patch_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    destination = tmp_path / "destination"
    git(root, "worktree", "add", "-q", str(destination), "-b", "destination")
    (destination / "descendant.txt").write_text("committed later\n", encoding="utf-8")
    git(destination, "add", "descendant.txt")
    git(destination, "commit", "-qm", "descendant")
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("source change\n", encoding="utf-8")
    created = create_handoff(root, report(), name="descendant", env={"CODEX_THREAD_ID": "source"})

    accepted, _ = accept_handoff(destination, created.name, env={"CLAUDE_SESSION_ID": "destination"})

    assert accepted.status == "claimed"
    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "source change\n"


def test_divergent_history_is_rejected_before_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    destination = tmp_path / "destination"
    git(root, "worktree", "add", "-q", str(destination), "-b", "destination")
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("source change\n", encoding="utf-8")
    created = create_handoff(root, report(), name="divergent", env={"CODEX_THREAD_ID": "source"})
    git(destination, "checkout", "-q", "--orphan", "unrelated-history")
    git(destination, "rm", "-q", "-rf", ".")
    (destination / "unrelated.txt").write_text("new root\n", encoding="utf-8")
    git(destination, "add", "-A")
    git(destination, "commit", "-qm", "unrelated root")

    with pytest.raises(HandoffError, match="diverges"):
        accept_handoff(destination, created.name, env={"CLAUDE_SESSION_ID": "destination"})
    assert HandoffStore(destination).load(created.name).status == "ready"


def test_clean_descendant_conflict_fails_apply_check_and_stays_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    destination = tmp_path / "destination"
    git(root, "worktree", "add", "-q", str(destination), "-b", "destination")
    (destination / "tracked.txt").write_text("conflicting committed change\n", encoding="utf-8")
    git(destination, "add", "tracked.txt")
    git(destination, "commit", "-qm", "conflict")
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("source change\n", encoding="utf-8")
    created = create_handoff(root, report(), name="apply-failure", env={"CODEX_THREAD_ID": "source"})

    with pytest.raises(HandoffError, match="git apply --check failed"):
        accept_handoff(destination, created.name, env={"CLAUDE_SESSION_ID": "destination"})

    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "conflicting committed change\n"
    assert HandoffStore(destination).load(created.name).status == "ready"


def test_post_image_failure_rolls_back_and_leaves_handoff_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    destination = tmp_path / "destination"
    git(root, "worktree", "add", "-q", str(destination), "-b", "destination")
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("source change\n", encoding="utf-8")
    created = create_handoff(root, report(), name="rollback", env={"CODEX_THREAD_ID": "source"})
    monkeypatch.setattr("agentpack.core.handoff.file_fingerprint", lambda _root, _path: "wrong")

    with pytest.raises(HandoffError, match="post-image verification failed"):
        accept_handoff(destination, created.name, env={"CLAUDE_SESSION_ID": "destination"})

    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "base\n"
    assert HandoffStore(destination).load(created.name).status == "ready"


def test_rollback_failure_surfaces_both_errors_and_leaves_handoff_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    destination = tmp_path / "destination"
    git(root, "worktree", "add", "-q", str(destination), "-b", "destination")
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("source change\n", encoding="utf-8")
    created = create_handoff(root, report(), name="rollback-failure", env={"CODEX_THREAD_ID": "source"})
    monkeypatch.setattr(handoff_module, "file_fingerprint", lambda _root, _path: "wrong")
    original_apply = handoff_module._apply_patch

    def fail_rollback(root_arg: Path, patch: bytes, *, check: bool = False, reverse: bool = False) -> None:
        if reverse:
            raise HandoffError("rollback refused")
        original_apply(root_arg, patch, check=check, reverse=reverse)

    monkeypatch.setattr(handoff_module, "_apply_patch", fail_rollback)

    with pytest.raises(HandoffError, match="rollback refused") as error:
        accept_handoff(destination, created.name, env={"CLAUDE_SESSION_ID": "destination"})

    assert "post-image verification failed" in str(error.value)
    assert HandoffStore(destination).load(created.name).status == "ready"
    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "source change\n"


def test_export_import_checksums_portability_and_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = repo(tmp_path, "source")
    (source / "tracked.txt").write_text("portable\n", encoding="utf-8")
    source_home = tmp_path / "source-home"
    monkeypatch.setenv("AGENTPACK_HOME", str(source_home))
    created = create_handoff(source, report(), name="portable", env={"CODEX_THREAD_ID": "private-session"})
    bundle = export_handoff(source, created.name, tmp_path / "portable.agentpack-handoff.zip")
    with zipfile.ZipFile(bundle) as archive:
        exported = json.loads(archive.read("handoff.json"))
        assert exported["source"]["session_id"] == ""
        assert exported["repository"]["worktree"] == ""

    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(source), str(clone))
    git(clone, "remote", "set-url", "origin", "https://example.com/acme/project.git")
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "clone-home"))
    imported = import_handoff(clone, bundle)
    assert imported.status == "ready"
    with pytest.raises(HandoffError, match="collision"):
        import_handoff(clone, bundle)


def test_cli_noninteractive_resume_requires_name_when_multiple_are_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path / "home"))
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
    create_handoff(root, report("First task"), env={"CODEX_THREAD_ID": "one"})
    create_handoff(root, report("Second task"), env={"CODEX_THREAD_ID": "one"})

    result = runner.invoke(app, ["handoff", "resume"])

    assert result.exit_code == 1
    assert "multiple ready handoffs exist" in result.output
