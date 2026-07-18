from __future__ import annotations

import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from agentpack.core import git


@dataclass(frozen=True)
class GitPreflight:
    branch: str
    upstream: str
    clean: bool
    tracked_dirty_count: int
    untracked_count: int
    ahead: int
    behind: int
    action: str
    reason: str
    fetch_ok: bool
    fetch_error: str = ""
    dirty_sample: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["dirty_sample"] = list(self.dirty_sample)
        return payload


def run_git_preflight(root: Path, *, allow_ff_pull: bool = False) -> GitPreflight:
    if not git.is_git_repo(root):
        return GitPreflight(
            branch="",
            upstream="",
            clean=True,
            tracked_dirty_count=0,
            untracked_count=0,
            ahead=0,
            behind=0,
            action="continue",
            reason="not a git repository",
            fetch_ok=True,
        )

    fetch_ok, fetch_error = _git_fetch(root)
    summary = git.working_tree_summary(root)
    branch = str(summary.get("branch") or "")
    upstream = str(summary.get("upstream") or "")
    staged = int(summary.get("staged_count") or 0)
    unstaged = int(summary.get("unstaged_count") or 0)
    untracked = int(summary.get("untracked_count") or 0)
    tracked_dirty = staged + unstaged
    ahead = int(summary.get("ahead") or 0)
    behind = int(summary.get("behind") or 0)
    dirty_sample = tuple(str(item) for item in (summary.get("dirty_sample") or [])[:8])

    if not fetch_ok:
        return _result(
            branch,
            upstream,
            tracked_dirty,
            untracked,
            ahead,
            behind,
            "fetch_failed",
            f"git fetch failed: {fetch_error}",
            False,
            fetch_error,
            dirty_sample,
        )
    if tracked_dirty:
        return _result(
            branch,
            upstream,
            tracked_dirty,
            untracked,
            ahead,
            behind,
            "blocked_dirty",
            "tracked local changes present; not syncing automatically",
            True,
            "",
            dirty_sample,
        )
    if not upstream:
        return _result(
            branch,
            upstream,
            tracked_dirty,
            untracked,
            ahead,
            behind,
            "fetch_only",
            "no upstream branch configured",
            True,
            "",
            dirty_sample,
        )
    if ahead and behind:
        return _result(
            branch,
            upstream,
            tracked_dirty,
            untracked,
            ahead,
            behind,
            "blocked_diverged",
            "local and upstream branches diverged; rebase or merge decision required",
            True,
            "",
            dirty_sample,
        )
    if behind:
        if untracked:
            return _result(
                branch,
                upstream,
                tracked_dirty,
                untracked,
                ahead,
                behind,
                "fetch_only",
                "behind upstream but untracked files exist; not pulling automatically",
                True,
                "",
                dirty_sample,
            )
        if not allow_ff_pull:
            return _result(
                branch,
                upstream,
                tracked_dirty,
                untracked,
                ahead,
                behind,
                "blocked_behind",
                "branch is behind upstream; rerun with a clean tree and fast-forward pull enabled",
                True,
                "",
                dirty_sample,
            )
        pull = _run_git(root, ["git", "pull", "--ff-only"])
        if pull.returncode != 0:
            error = (pull.stderr or pull.stdout or f"git pull exited {pull.returncode}").strip()
            return _result(
                branch,
                upstream,
                tracked_dirty,
                untracked,
                ahead,
                behind,
                "blocked_pull_failed",
                f"git pull --ff-only failed: {error}",
                True,
                "",
                dirty_sample,
            )
        summary = git.working_tree_summary(root)
        return _result(
            str(summary.get("branch") or branch),
            str(summary.get("upstream") or upstream),
            int(summary.get("staged_count") or 0) + int(summary.get("unstaged_count") or 0),
            int(summary.get("untracked_count") or 0),
            int(summary.get("ahead") or 0),
            int(summary.get("behind") or 0),
            "ff_pull",
            "fast-forwarded from upstream",
            True,
            "",
            tuple(str(item) for item in (summary.get("dirty_sample") or [])[:8]),
        )
    return _result(
        branch,
        upstream,
        tracked_dirty,
        untracked,
        ahead,
        behind,
        "continue",
        "branch is current enough to proceed",
        True,
        "",
        dirty_sample,
    )


def _result(
    branch: str,
    upstream: str,
    tracked_dirty: int,
    untracked: int,
    ahead: int,
    behind: int,
    action: str,
    reason: str,
    fetch_ok: bool,
    fetch_error: str,
    dirty_sample: tuple[str, ...],
) -> GitPreflight:
    return GitPreflight(
        branch=branch,
        upstream=upstream,
        clean=tracked_dirty == 0 and untracked == 0,
        tracked_dirty_count=tracked_dirty,
        untracked_count=untracked,
        ahead=ahead,
        behind=behind,
        action=action,
        reason=reason,
        fetch_ok=fetch_ok,
        fetch_error=fetch_error,
        dirty_sample=dirty_sample,
    )


def _git_fetch(root: Path) -> tuple[bool, str]:
    remotes = _run_git(root, ["git", "remote"])
    if remotes.returncode != 0:
        return False, (remotes.stderr or remotes.stdout or "git remote failed").strip()
    if not remotes.stdout.strip():
        return True, ""
    result = _run_git(root, ["git", "fetch", "--quiet", "--all", "--prune"])
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or f"git fetch exited {result.returncode}").strip()


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))
