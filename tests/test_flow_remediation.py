from __future__ import annotations

import subprocess
from pathlib import Path

from agentpack.application.pack_service import ChangeDetector
from agentpack.application.pr_context import _github_pr_metadata
from agentpack.commands.benchmark_e2e import E2ECase, _run_e2e_case
from agentpack.core.models import FileInfo
from agentpack.commands import review_cmd


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def test_change_detector_keeps_staged_files_separate(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    (tmp_path / "base.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "unstaged.py").write_text("value = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.py", "unstaged.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "staged.py").write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.py"], cwd=tmp_path, check=True)
    (tmp_path / "unstaged.py").write_text("value = 3\n", encoding="utf-8")
    files = [
        FileInfo(path=name, abs_path=tmp_path / name, size_bytes=10, estimated_tokens=3)
        for name in ("staged.py", "unstaged.py")
    ]

    changes = ChangeDetector().detect(files, tmp_path, None)

    assert changes.all_changed == {"staged.py", "unstaged.py"}
    assert changes.git_staged == {"staged.py"}


def test_pr_metadata_never_runs_ambient_gh_lookup(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("agentpack.application.pr_context._git", lambda _root, args: calls.append(args) or "{}")

    assert _github_pr_metadata(tmp_path, None) is None
    assert calls == []


def test_review_metadata_never_runs_ambient_gh_lookup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(review_cmd.shutil, "which", lambda _name: "/usr/bin/gh")

    assert review_cmd._gh_pr_metadata(tmp_path, None) is None


def test_e2e_setup_failure_is_recorded_and_blocks_success(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    case = E2ECase(
        name="setup-failure",
        repo=repo,
        task="change app",
        setup_command="python -c 'import sys; sys.exit(3)'",
        test_command="python -c 'pass'",
    )

    result = _run_e2e_case(
        case,
        strategy="no-context",
        trial=1,
        agent_command="python -c 'pass'",
        timeout=10,
        keep_workdir=True,
        run_id="e2e-test",
        tested_commit="a" * 40,
    )

    assert result.setup_returncode == 3
    assert result.passed is False
    assert result.run_id == "e2e-test"
    assert result.tested_commit == "a" * 40


def test_release_workflow_has_no_version_specific_evidence_bypass() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert "Bypass evidence for v0.4.3" not in workflow
    assert "release-evidence" in workflow
    assert "needs: [test, tree-sitter-floor, release-evidence, route-performance]" in workflow
