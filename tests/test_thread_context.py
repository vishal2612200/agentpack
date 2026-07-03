from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentpack.core.thread_context import (
    append_thread_index,
    build_thread_index_row,
    detect_conflicts,
    resolve_thread_id,
    resolve_session_thread_option,
    resolve_thread_option,
    sanitize_thread_id,
    thread_paths,
)


def test_resolve_thread_id_prefers_explicit_over_env() -> None:
    assert resolve_thread_id("explicit", {"AGENTPACK_THREAD_ID": "env"}) == "explicit"


def test_resolve_thread_option_keeps_legacy_mode_without_explicit_auto() -> None:
    env = {"AGENTPACK_THREAD_ID": "env-thread", "CODEX_THREAD_ID": "codex-thread"}

    assert resolve_thread_option(None, env) is None
    assert resolve_thread_option("", env) is None
    assert resolve_thread_option("auto", env) == "env-thread"
    assert resolve_thread_option("explicit", env) == "explicit"


def test_resolve_session_thread_option_uses_ambient_env_by_default() -> None:
    env = {"AGENTPACK_THREAD_ID": "agentpack/session", "CLAUDE_SESSION_ID": "claude/session"}

    assert resolve_session_thread_option("", env) == "agentpack-session"
    assert resolve_session_thread_option(None, env) == "agentpack-session"
    assert resolve_session_thread_option("auto", env) == "agentpack-session"
    assert resolve_session_thread_option("global", env) is None
    assert resolve_session_thread_option("legacy", env) is None
    assert resolve_session_thread_option("explicit", env) == "explicit"


def test_sanitize_thread_id_limits_to_safe_chars() -> None:
    assert sanitize_thread_id(" ../Codex Thread:123?? ") == "Codex-Thread-123"
    assert len(sanitize_thread_id("x" * 120)) == 80


def test_thread_paths_are_scoped(tmp_path) -> None:
    paths = thread_paths(tmp_path, "codex/local")
    assert paths is not None
    assert paths.thread_id == "codex-local"
    assert paths.task == tmp_path / ".agentpack" / "threads" / "codex-local" / "task.md"
    assert paths.metadata.name == "pack_metadata.json"


def test_detect_conflicts_for_same_branch_worktree_and_overlap(tmp_path) -> None:
    current = build_thread_index_row(
        root=tmp_path,
        thread_id="thread-a",
        task="fix auth",
        branch="main",
        selected_files=["src/auth.py"],
        dirty_files=[],
        status="in_progress",
    )
    other = build_thread_index_row(
        root=tmp_path,
        thread_id="thread-b",
        task="refactor auth",
        branch="main",
        selected_files=["src/auth.py", "tests/test_auth.py"],
        dirty_files=[],
        status="in_progress",
    )
    append_thread_index(tmp_path, other)

    result = detect_conflicts(tmp_path, current)

    assert result["warning"] is True
    assert result["conflicts"][0]["thread_id"] == "thread-b"
    assert result["conflicts"][0]["overlap"] == ["src/auth.py"]


def test_detect_conflicts_ignores_different_branch_and_done_or_stale(tmp_path) -> None:
    current = build_thread_index_row(
        root=tmp_path,
        thread_id="thread-a",
        task="fix auth",
        branch="main",
        selected_files=["src/auth.py"],
        dirty_files=[],
        status="in_progress",
    )
    different_branch = build_thread_index_row(
        root=tmp_path,
        thread_id="thread-b",
        task="other",
        branch="feature",
        selected_files=["src/auth.py"],
        dirty_files=[],
        status="in_progress",
    )
    done = build_thread_index_row(
        root=tmp_path,
        thread_id="thread-c",
        task="done",
        branch="main",
        selected_files=["src/auth.py"],
        dirty_files=[],
        status="done",
    )
    stale = build_thread_index_row(
        root=tmp_path,
        thread_id="thread-d",
        task="stale",
        branch="main",
        selected_files=["src/auth.py"],
        dirty_files=[],
        status="in_progress",
    )
    stale["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    append_thread_index(tmp_path, different_branch)
    append_thread_index(tmp_path, done)
    append_thread_index(tmp_path, stale)

    result = detect_conflicts(tmp_path, current)

    assert result["warning"] is False
    assert result["conflicts"] == []
