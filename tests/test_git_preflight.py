from __future__ import annotations

import subprocess
from pathlib import Path

from agentpack.core.git_preflight import run_git_preflight


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True)


def test_git_preflight_continues_outside_git_repo(tmp_path) -> None:
    result = run_git_preflight(tmp_path)

    assert result.action == "continue"
    assert result.reason == "not a git repository"


def test_git_preflight_blocks_tracked_dirty_tree(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")

    result = run_git_preflight(tmp_path)

    assert result.action == "blocked_dirty"
    assert result.tracked_dirty_count == 1
    assert "tracked local changes present" in result.reason
    assert "app.py" in result.dirty_sample

