import stat
import subprocess
from pathlib import Path

from agentpack.integrations.git_hooks import (
    _AGENTPACK_END_MARKER,
    _AGENTPACK_MARKER,
    _HOOK_EVENTS,
    inspect_git_hook,
    install_git_hooks,
    remove_git_hooks,
)


def _make_git_repo(tmp_path: Path) -> Path:
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    return tmp_path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class TestInstallGitHooks:
    def test_creates_hooks_in_empty_repo(self, tmp_path):
        root = _make_git_repo(tmp_path)
        results = install_git_hooks(root, agent="cursor")
        assert set(results.keys()) == set(_HOOK_EVENTS)
        for event in _HOOK_EVENTS:
            hook = root / ".git" / "hooks" / event
            assert hook.exists()
            assert "agentpack.cli" in hook.read_text()
            assert "GitAutoRepack" in hook.read_text()
            assert "cursor" in hook.read_text()

    def test_hooks_are_executable(self, tmp_path):
        root = _make_git_repo(tmp_path)
        install_git_hooks(root, agent="cursor")
        for event in _HOOK_EVENTS:
            hook = root / ".git" / "hooks" / event
            assert hook.stat().st_mode & stat.S_IXUSR

    def test_idempotent(self, tmp_path):
        root = _make_git_repo(tmp_path)
        install_git_hooks(root, agent="cursor")
        results2 = install_git_hooks(root, agent="cursor")
        for action in results2.values():
            assert action == "unchanged"

    def test_appends_to_existing_hook(self, tmp_path):
        root = _make_git_repo(tmp_path)
        hook = root / ".git" / "hooks" / "post-commit"
        hook.write_text("#!/bin/sh\necho 'existing hook'\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        results = install_git_hooks(root, agent="windsurf")
        assert results["post-commit"] == "appended"
        content = hook.read_text()
        assert "existing hook" in content
        assert "GitAutoRepack" in content

    def test_returns_empty_if_no_git_dir(self, tmp_path):
        results = install_git_hooks(tmp_path, agent="cursor")
        assert results == {}

    def test_agent_name_in_hook(self, tmp_path):
        root = _make_git_repo(tmp_path)
        install_git_hooks(root, agent="codex")
        hook = root / ".git" / "hooks" / "post-commit"
        assert "--agent codex" in hook.read_text()
        assert "agentpack.cli" in hook.read_text()

    def test_repairs_legacy_trailing_fragment(self, tmp_path):
        root = _make_git_repo(tmp_path)
        hook = root / ".git" / "hooks" / "post-checkout"
        hook.write_text("#!/bin/sh\n# agentpack:auto-repack\npython -m agentpack.cli hook --event GitAutoRepack\nent auto\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

        install_git_hooks(root, agent="codex")
        content = hook.read_text()
        assert _AGENTPACK_MARKER in content
        assert _AGENTPACK_END_MARKER in content
        assert "ent auto" not in content
        assert inspect_git_hook(content, "codex").state == "valid"

    def test_preserves_mixed_hook_content_when_repairing(self, tmp_path):
        root = _make_git_repo(tmp_path)
        hook = root / ".git" / "hooks" / "post-commit"
        hook.write_text("#!/bin/sh\necho before\n# agentpack:auto-repack\nent auto\necho after\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

        install_git_hooks(root, agent="codex")
        content = hook.read_text()
        assert "echo before" in content
        assert "echo after" in content
        assert "ent auto" not in content
        assert inspect_git_hook(content, "codex").state == "valid"

    def test_repairs_incomplete_managed_block_without_stale_command(self, tmp_path):
        root = _make_git_repo(tmp_path)
        hook = root / ".git" / "hooks" / "post-commit"
        hook.write_text(
            "#!/bin/sh\necho before\n# agentpack:auto-repack:start\n"
            "python -m agentpack.cli hook --event GitAutoRepack --agent old\n"
            "echo after\n"
        )
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

        install_git_hooks(root, agent="codex")

        content = hook.read_text()
        assert content.count("GitAutoRepack") == 1
        assert "--agent old" not in content
        assert "echo before" in content
        assert "echo after" in content
        assert inspect_git_hook(content, "codex").state == "valid"

    def test_duplicate_blocks_are_normalized(self, tmp_path):
        root = _make_git_repo(tmp_path)
        install_git_hooks(root, agent="codex")
        hook = root / ".git" / "hooks" / "post-commit"
        hook.write_text(hook.read_text() + hook.read_text())

        assert inspect_git_hook(hook.read_text(), "codex").state == "duplicate"
        install_git_hooks(root, agent="codex")
        assert inspect_git_hook(hook.read_text(), "codex").state == "valid"

    def test_uses_common_hooks_directory_from_linked_worktree(self, tmp_path):
        root = tmp_path / "repo"
        linked = tmp_path / "linked"
        root.mkdir()
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.name", "AgentPack Test")
        _git(root, "config", "user.email", "agentpack@example.com")
        _git(root, "commit", "--allow-empty", "-m", "initial")
        _git(root, "worktree", "add", "-b", "linked", str(linked))

        results = install_git_hooks(linked, agent="codex")

        assert set(results) == set(_HOOK_EVENTS)
        for event in _HOOK_EVENTS:
            hook = root / ".git" / "hooks" / event
            assert hook.exists()
            assert _AGENTPACK_MARKER in hook.read_text(encoding="utf-8")

        remove_git_hooks(linked)
        for event in _HOOK_EVENTS:
            hook = root / ".git" / "hooks" / event
            assert not hook.exists() or _AGENTPACK_MARKER not in hook.read_text(encoding="utf-8")


class TestRemoveGitHooks:
    def test_removes_installed_hooks(self, tmp_path):
        root = _make_git_repo(tmp_path)
        install_git_hooks(root, agent="cursor")
        remove_git_hooks(root)
        for event in _HOOK_EVENTS:
            hook = root / ".git" / "hooks" / event
            assert not hook.exists() or "agentpack" not in hook.read_text()

    def test_preserves_existing_content(self, tmp_path):
        root = _make_git_repo(tmp_path)
        hook = root / ".git" / "hooks" / "post-commit"
        hook.write_text("#!/bin/sh\necho 'keep me'\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        install_git_hooks(root, agent="cursor")
        remove_git_hooks(root)
        assert hook.exists()
        assert "keep me" in hook.read_text()
        assert "agentpack" not in hook.read_text()

    def test_noop_if_not_installed(self, tmp_path):
        root = _make_git_repo(tmp_path)
        results = remove_git_hooks(root)
        assert all(v == "unchanged" for v in results.values())
