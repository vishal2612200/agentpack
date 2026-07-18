from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agentpack.commands.hook_cmd import (
    _mcp_installed,
    _load_top_files,
    _load_pack_task,
    _current_root_hash,
    _git_sync_decision_note,
    _run_git_auto_repack,
    _run_user_prompt_submit,
    _review_stage_gate_note,
    _looks_like_coding_prompt,
    _looks_like_review_prompt,
    _looks_like_task_switch,
    _resolve_task,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    agentpack_dir = tmp_path / ".agentpack"
    agentpack_dir.mkdir()
    snapshots_dir = agentpack_dir / "snapshots"
    snapshots_dir.mkdir()
    return tmp_path


def _write_snapshot(repo: Path, root_hash: str = "abc123") -> None:
    snap = repo / ".agentpack" / "snapshots" / "latest.json"
    snap.write_text(json.dumps({"root_hash": root_hash, "files": {}}))


def _write_metrics(repo: Path, selected_paths: list[str]) -> None:
    rec = {"ts": "2026-01-01T00:00:00Z", "task": "test", "selected_paths": selected_paths}
    (repo / ".agentpack" / "metrics.jsonl").write_text(json.dumps(rec) + "\n")


def _write_metadata(repo: Path, task: str = "fix login", root_hash: str | None = None) -> None:
    meta = {"task": task, "token_estimate": 5000}
    if root_hash is not None:
        meta["snapshot_root_hash"] = root_hash
    (repo / ".agentpack" / "pack_metadata.json").write_text(json.dumps(meta))


def _write_task(repo: Path, task: str = "fix login") -> None:
    (repo / ".agentpack" / "task.md").write_text(task + "\n", encoding="utf-8")


class TestMcpInstalled:
    def test_local_mcp_json(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {"command": "agentpack", "args": ["mcp"]}}}))
        assert _mcp_installed(repo) is True

    def test_no_mcp(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        assert _mcp_installed(repo) is False

    def test_mcp_json_no_agentpack_entry(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {}}}))
        assert _mcp_installed(repo) is False


class TestLoadTopFiles:
    def test_returns_top_n(self, repo: Path) -> None:
        paths = [f"src/file{i}.py" for i in range(10)]
        _write_metrics(repo, paths)
        result = _load_top_files(repo, n=5)
        assert len(result) == 5
        assert result[0]["path"] == "src/file0.py"

    def test_no_metrics(self, repo: Path) -> None:
        assert _load_top_files(repo) == []


class TestLoadPackTask:
    def test_reads_task(self, repo: Path) -> None:
        _write_metadata(repo, task="fix auth")
        assert _load_pack_task(repo) == "fix auth"

    def test_missing(self, repo: Path) -> None:
        assert _load_pack_task(repo) == ""


class TestCurrentRootHash:
    def test_reads_hash(self, repo: Path) -> None:
        _write_snapshot(repo, "deadbeef")
        assert _current_root_hash(repo) == "deadbeef"

    def test_missing(self, repo: Path) -> None:
        assert _current_root_hash(repo) is None


class TestRunUserPromptSubmit:
    def _capture_output(self, repo: Path, stdin_data: dict, monkeypatch) -> dict:
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_data)))
        outputs = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))
        with patch("subprocess.Popen"):
            _run_user_prompt_submit(repo)
        assert outputs, "No output printed"
        return json.loads(outputs[0])

    def _capture_outputs(self, repo: Path, stdin_data: dict, monkeypatch) -> list[str]:
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_data)))
        outputs = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))
        with patch("subprocess.Popen"):
            _run_user_prompt_submit(repo)
        return outputs

    def test_mcp_installed_hint_format(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["src/a.py", "src/b.py"])
        _write_metadata(repo, task="fix login", root_hash="hash1")
        _write_task(repo, "fix login")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))

        out = self._capture_output(repo, {"prompt": "fix the login bug"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]

        assert "agentpack_pack_context" in ctx
        assert "src/a.py" in ctx
        assert len(ctx) < 1000  # tiny hint, not full injection

    def test_prompt_hook_uses_ambient_session_task(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "claude/local")
        scoped = repo / ".agentpack" / "threads" / "claude-local"
        scoped.mkdir(parents=True)
        (repo / ".agentpack" / "task.md").write_text("old global task\n", encoding="utf-8")
        (scoped / "task.md").write_text("fix scoped login\n", encoding="utf-8")
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["src/scoped.py"])
        (scoped / "pack_metadata.json").write_text(
            json.dumps({"task": "fix scoped login", "snapshot_root_hash": "hash1", "token_estimate": 100}),
            encoding="utf-8",
        )
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))

        out = self._capture_output(repo, {"prompt": "fix scoped login"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]

        assert "task: fix scoped login" in ctx
        assert "old global task" not in ctx
        assert "src/scoped.py" in ctx

    def test_stale_review_context_suppresses_file_hints(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["src/old.py"])
        _write_metadata(repo, task="fix login", root_hash="hash1")
        _write_task(repo, "fix login")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))

        out = self._capture_output(repo, {"prompt": "review PR 1127 load test changes"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]

        assert "AgentPack STALE" in ctx
        assert "REVIEW DETECTED" in ctx
        assert "BYPASS REQUIRED" in ctx
        assert 'agentpack_pack_context(task="review PR 1127 load test changes")' in ctx
        assert "If the AgentPack MCP tool is visible" in ctx
        assert "packed task: fix login" in ctx
        assert "src/old.py" not in ctx

    def test_fresh_review_context_keeps_file_hints_and_preflight(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["src/reviewed.py"])
        _write_metadata(repo, task="review PR 1127 load test changes", root_hash="hash1")
        _write_task(repo, "review PR 1127 load test changes")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))

        out = self._capture_output(repo, {"prompt": "review PR 1127 load test changes"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]

        assert "index fresh" in ctx
        assert "REVIEW DETECTED" in ctx
        assert "BYPASS REQUIRED" not in ctx
        assert "src/reviewed.py" in ctx

    def test_review_preflight_reminder_only_repeats_when_snapshot_changes(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["src/reviewed.py"])
        _write_metadata(repo, task="review PR 1127 load test changes", root_hash="hash1")
        _write_task(repo, "review PR 1127 load test changes")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))

        first = self._capture_output(repo, {"prompt": "review PR 1127 load test changes"}, monkeypatch)
        second = self._capture_output(repo, {"prompt": "review PR 1127 load test changes"}, monkeypatch)
        _write_snapshot(repo, "hash2")
        third = self._capture_output(repo, {"prompt": "review PR 1127 load test changes"}, monkeypatch)

        assert "REVIEW DETECTED" in first["hookSpecificOutput"]["additionalContext"]
        assert "REVIEW DETECTED" not in second["hookSpecificOutput"]["additionalContext"]
        assert "REVIEW DETECTED" in third["hookSpecificOutput"]["additionalContext"]

    def test_no_mcp_capped_fallback(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["src/a.py", "src/b.py"])
        _write_metadata(repo, task="fix login", root_hash="hash1")
        _write_task(repo, "fix login")

        out = self._capture_output(repo, {"prompt": "fix login"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]

        assert len(ctx) <= 3000
        assert "src/a.py" in ctx
        assert "MCP unavailable: run `agentpack repair --agent auto`" in ctx
        assert "agentpack install" in ctx  # nudge toward MCP

    def test_mcp_status_hint_only_emits_once_per_task(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["src/a.py"])
        _write_metadata(repo, task="fix login", root_hash="hash1")
        _write_task(repo, "fix login")

        first = self._capture_output(repo, {"prompt": "fix login"}, monkeypatch)
        second = self._capture_output(repo, {"prompt": "fix login"}, monkeypatch)

        assert "MCP unavailable" in first["hookSpecificOutput"]["additionalContext"]
        assert "MCP unavailable" not in second["hookSpecificOutput"]["additionalContext"]

    def test_no_mcp_review_preflight_uses_guard_fallback(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["src/reviewed.py"])
        _write_metadata(repo, task="review PR 1127 load test changes", root_hash="hash1")
        _write_task(repo, "review PR 1127 load test changes")

        out = self._capture_output(repo, {"prompt": "review PR 1127 load test changes"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]

        assert "REVIEW DETECTED" in ctx
        assert "agentpack guard --agent auto --repair-stale --refresh-context" in ctx
        assert "src/reviewed.py" in ctx

    def test_runtime_infra_task_suppresses_unrelated_hints_and_names_source_of_truth(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        _write_metrics(repo, ["payments/stripe.py", "billing/provider.py"])
        _write_metadata(repo, task="fix OTP WAF Copilot CloudFormation rules", root_hash="hash1")
        _write_task(repo, "fix OTP WAF Copilot CloudFormation rules")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))

        out = self._capture_output(repo, {"prompt": "fix OTP WAF Copilot CloudFormation rules"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]

        assert "Selected-file hints suppressed" in ctx
        assert "AgentPack: guardrail + context frame only." in ctx
        assert "Source of truth: GitHub PR/head, clean worktree, rendered deploy config" in ctx
        assert "payments/stripe.py" not in ctx
        assert "agentpack_get_context()" not in ctx

    def test_deploy_task_keeps_deploy_hints_and_names_live_sources(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        metrics = {
            "selected_hints": [
                {"path": "src/agentpack/commands/review_cmd.py", "why": "review workflow"},
                {"path": ".github/workflows/prod-deploy.yml", "why": "workflow"},
                {"path": "copilot/api/manifest.yml", "why": "copilot manifest"},
                {"path": "copilot/api/overrides/cfn.patches.yml", "why": "iam patch"},
            ]
        }
        (repo / ".agentpack" / "metrics.jsonl").write_text(json.dumps(metrics) + "\n")
        _write_metadata(repo, task="deploy phoenix production with copilot", root_hash="hash1")
        _write_task(repo, "deploy phoenix production with copilot")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))

        out = self._capture_output(repo, {"prompt": "deploy phoenix production with copilot"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]

        assert "AgentPack: guardrail + context frame only." in ctx
        assert "Source of truth: GitHub PR/head, clean worktree, rendered deploy config" in ctx
        assert ".github/workflows/prod-deploy.yml" in ctx
        assert "copilot/api/manifest.yml" in ctx
        assert "copilot/api/overrides/cfn.patches.yml" in ctx
        assert "src/agentpack/commands/review_cmd.py" not in ctx

    def test_review_stage_gate_note_blocks_incomplete_active_review(self, repo: Path) -> None:
        (repo / ".agentpack" / "review-state.json").write_text(
            json.dumps({"status": "awaiting_judge"}),
            encoding="utf-8",
        )

        note = _review_stage_gate_note(repo, review_intent=True)

        assert "REVIEW STAGE BLOCK" in note
        assert "Judge findings artifact missing" in note
        assert "agentpack review --check" in note

    def test_review_stage_gate_ignores_stale_branch_preflight(self, repo: Path) -> None:
        import subprocess

        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, check=True)
        (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True)
        (repo / ".agentpack" / "review-preflight.json").write_text(
            json.dumps(
                {
                    "git": {"branch": "feat/toon-validator"},
                    "paths": {"run_dir": ".agentpack/reviews/pr-48/run"},
                }
            ),
            encoding="utf-8",
        )
        (repo / ".agentpack" / "review-state.json").write_text(
            json.dumps({"status": "blocked_invalid_artifact"}),
            encoding="utf-8",
        )

        note = _review_stage_gate_note(repo, review_intent=True)

        assert "REVIEW PREFLIGHT STALE" in note
        assert "prepared for branch feat/toon-validator" in note
        assert "current branch is main" in note
        assert "active review artifact invalid" not in note

    def test_hard_cap_enforced(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "hash1")
        # Many files to potentially produce long output
        paths = [f"src/module_{i}/very_long_filename_{i}.py" for i in range(50)]
        _write_metrics(repo, paths)
        _write_metadata(repo, task="x" * 200, root_hash="hash1")
        _write_task(repo, "x" * 200)

        out = self._capture_output(repo, {"prompt": "test"}, monkeypatch)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert len(ctx) <= 3000

    def test_repo_changed_marks_refresh_pending_without_repack(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "newhash")
        _write_metadata(repo, task="fix login bug", root_hash="oldhash")
        _write_task(repo, "fix login bug")

        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "fix login bug"})))
        outputs = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))

        with patch("subprocess.Popen") as mock_popen, \
             patch("agentpack.commands.hook_cmd._infer_live_task", return_value="fix login bug"):
            _run_user_prompt_submit(repo)

        mock_popen.assert_not_called()
        assert (repo / ".agentpack" / "task.md").read_text(encoding="utf-8") == "fix login bug\n"
        ctx = json.loads(outputs[0])["hookSpecificOutput"]["additionalContext"]
        assert "refresh pending" in ctx
        assert "agentpack_get_context()" not in ctx

    def test_repo_unchanged_no_repack(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "samehash")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))
        _write_metrics(repo, ["src/a.py"])
        _write_metadata(repo, root_hash="samehash")
        _write_task(repo)

        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "test"})))
        outputs = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))

        with patch("subprocess.Popen") as mock_popen, \
             patch("agentpack.commands.hook_cmd._infer_live_task", return_value="general development"):
            _run_user_prompt_submit(repo)

        mock_popen.assert_not_called()

    def test_task_switch_updates_task_md_and_marks_refresh_pending(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "samehash")
        (repo / ".agentpack" / "task.md").write_text("add production-grade Kundali generation API\n")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))
        _write_metrics(repo, ["src/old.py"])
        _write_metadata(repo, task="add production-grade Kundali generation API", root_hash="samehash")

        import io
        prompt = "fix numerology dashboard layout"
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": prompt})))
        outputs = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))

        with patch("agentpack.commands.hook_cmd._run_blocking_pack", return_value=(True, "")) as mock_pack, \
             patch("subprocess.Popen") as mock_popen:
            _run_user_prompt_submit(repo)

        mock_pack.assert_not_called()
        mock_popen.assert_not_called()
        assert (repo / ".agentpack" / "task.md").read_text(encoding="utf-8") == prompt + "\n"
        ctx = json.loads(outputs[0])["hookSpecificOutput"]["additionalContext"]
        assert "refresh pending" in ctx
        assert f"task: {prompt}" in ctx

    def test_task_switch_blocking_refresh_failure_keeps_prompt_context(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "samehash")
        (repo / ".agentpack" / "task.md").write_text("migrate DB schema\n")
        (repo / ".agentpack" / "config.toml").write_text(
            "[hooks]\nblocking_task_refresh = true\n",
            encoding="utf-8",
        )
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))
        _write_metrics(repo, ["src/old.py"])
        _write_metadata(repo, task="migrate DB schema", root_hash="samehash")

        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "fix login bug"})))
        outputs = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))

        with patch("agentpack.commands.hook_cmd._run_blocking_pack", return_value=(False, "boom")):
            _run_user_prompt_submit(repo)

        ctx = json.loads(outputs[0])["hookSpecificOutput"]["additionalContext"]
        assert "refresh failed" in ctx
        assert "refresh error: boom" in ctx
        assert "task: fix login bug" in ctx


class TestRunGitAutoRepack:
    def test_skips_uninitialized_repo(self, tmp_path, monkeypatch) -> None:
        with patch("subprocess.Popen") as mock_popen:
            _run_git_auto_repack(tmp_path, "auto")
        mock_popen.assert_not_called()

    def test_spawns_pack_for_initialized_repo(self, tmp_path, monkeypatch) -> None:
        (tmp_path / ".agentpack").mkdir()
        (tmp_path / ".agentpack" / "config.toml").write_text("[project]\n", encoding="utf-8")

        with patch("subprocess.Popen") as mock_popen:
            _run_git_auto_repack(tmp_path, "codex")

        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[1:4] == ["-m", "agentpack.cli", "pack"]
        assert "--agent" in args
        assert "codex" in args

    def test_task_switch_can_be_disabled_in_config(self, repo: Path, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: repo)
        _write_snapshot(repo, "samehash")
        (repo / ".agentpack" / "task.md").write_text("add production-grade Kundali generation API\n")
        (repo / ".agentpack" / "config.toml").write_text(
            "[hooks]\ntask_switch_detection = false\n",
            encoding="utf-8",
        )
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentpack": {}}}))
        _write_metrics(repo, ["src/old.py"])
        _write_metadata(repo, task="add production-grade Kundali generation API", root_hash="samehash")

        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "fix numerology dashboard layout"})))
        outputs = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))

        with patch("subprocess.Popen") as mock_popen:
            _run_user_prompt_submit(repo)

        mock_popen.assert_not_called()
        assert (repo / ".agentpack" / "task.md").read_text(encoding="utf-8") == (
            "add production-grade Kundali generation API\n"
        )
        ctx = json.loads(outputs[0])["hookSpecificOutput"]["additionalContext"]
        assert "index fresh" in ctx
        assert "task: add production-grade Kundali generation API" in ctx

    def test_no_task_coding_prompt_only_hints_once(self, repo: Path, monkeypatch) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "fix login bug"})))
        first: list[str] = []
        monkeypatch.setattr("builtins.print", lambda x: first.append(x))
        with patch("subprocess.Popen"):
            _run_user_prompt_submit(repo)

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "fix login bug"})))
        second: list[str] = []
        monkeypatch.setattr("builtins.print", lambda x: second.append(x))
        with patch("subprocess.Popen"):
            _run_user_prompt_submit(repo)

        assert len(first) == 1
        assert "No active task" in json.loads(first[0])["hookSpecificOutput"]["additionalContext"]
        assert second == []

    def test_no_task_coding_prompt_includes_git_sync_decision(self, tmp_path: Path, monkeypatch) -> None:
        import io
        import subprocess

        (tmp_path / ".agentpack").mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
        (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True)
        (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "fix login bug"})))
        outputs: list[str] = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))
        _run_user_prompt_submit(tmp_path)

        ctx = json.loads(outputs[0])["hookSpecificOutput"]["additionalContext"]
        assert "GIT SYNC DECISION" in ctx
        assert "tracked_dirty=1" in ctx
        assert "decide whether tracked local changes are the task target" in ctx

    def test_no_task_chat_prompt_stays_silent(self, repo: Path, monkeypatch) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "why is this slow?"})))
        outputs: list[str] = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))
        with patch("subprocess.Popen"):
            _run_user_prompt_submit(repo)

        assert outputs == []

    def test_chat_prompt_with_active_task_stays_silent_and_fast(self, repo: Path, monkeypatch) -> None:
        import io

        _write_task(repo, "fix login flow")
        (repo / ".agentpack" / "config.toml").write_text(
            "[hooks]\nblocking_task_refresh = true\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "does agentpack help with token saving?"})))
        outputs: list[str] = []
        monkeypatch.setattr("builtins.print", lambda x: outputs.append(x))

        with patch("agentpack.commands.hook_cmd._current_root_hash", side_effect=AssertionError("should stay cheap")), \
             patch("agentpack.commands.hook_cmd._run_blocking_pack", side_effect=AssertionError("should not pack")):
            _run_user_prompt_submit(repo)

        assert outputs == []

    def test_git_sync_decision_note_is_local_status_only(self, tmp_path: Path) -> None:
        import subprocess

        subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
        (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True)

        note = _git_sync_decision_note(tmp_path)

        assert "GIT SYNC DECISION" in note
        assert "upstream=(none)" in note
        assert "no upstream configured" in note

    def test_session_start_clears_no_task_reminder(self, repo: Path) -> None:
        reminder = repo / ".agentpack" / ".no_task_reminded"
        reminder.write_text("1", encoding="utf-8")

        from agentpack.commands.hook_cmd import _run_session_start

        _run_session_start(repo)

        assert not reminder.exists()


class TestLooksLikeCodingPrompt:
    def test_slash_command_rejected(self):
        assert not _looks_like_coding_prompt("/caveman ultra")
        assert not _looks_like_coding_prompt("/agentpack")

    def test_coding_verbs_accepted(self):
        assert _looks_like_coding_prompt("fix the login bug")
        assert _looks_like_coding_prompt("add pagination to the API")
        assert _looks_like_coding_prompt("refactor auth module")
        assert _looks_like_coding_prompt("implement OAuth flow")
        assert _looks_like_coding_prompt("review PR 1127 load test changes")

    def test_chat_prompt_rejected(self):
        assert not _looks_like_coding_prompt("why does this work?")
        assert not _looks_like_coding_prompt("explain connection pooling")

    def test_empty_rejected(self):
        assert not _looks_like_coding_prompt("")

class TestLooksLikeReviewPrompt:
    def test_review_pr_accepted(self):
        assert _looks_like_review_prompt("review PR 1127 load test changes")
        assert _looks_like_review_prompt("gh pr diff 1127 then review findings")
        assert _looks_like_review_prompt("@agentpack-review focus on backward compatibility")

    def test_review_chatter_rejected(self):
        assert not _looks_like_review_prompt("did we use agentpack-review in the review process?")
        assert not _looks_like_review_prompt("why did this work?")


class TestResolveTask:
    def test_task_md_wins_over_related_coding_prompt(self, tmp_path):
        (tmp_path / ".agentpack").mkdir()
        (tmp_path / ".agentpack" / "task.md").write_text("fix login flow")
        result = _resolve_task(tmp_path, "fix login bug")
        assert result == "fix login flow"

    def test_distinct_coding_prompt_wins_over_stale_task_md(self, tmp_path):
        (tmp_path / ".agentpack").mkdir()
        (tmp_path / ".agentpack" / "task.md").write_text("migrate DB schema")
        result = _resolve_task(tmp_path, "fix login bug")
        assert result == "fix login bug"

    def test_distinct_coding_prompt_can_be_ignored_when_switch_detection_disabled(self, tmp_path):
        (tmp_path / ".agentpack").mkdir()
        (tmp_path / ".agentpack" / "task.md").write_text("migrate DB schema")
        result = _resolve_task(tmp_path, "fix login bug", task_switch_detection=False)
        assert result == "migrate DB schema"

    def test_coding_prompt_used_when_no_task_md(self, tmp_path):
        (tmp_path / ".agentpack").mkdir()
        result = _resolve_task(tmp_path, "fix the auth token")
        assert result == "fix the auth token"

    def test_slash_command_falls_back_to_auto(self, tmp_path):
        (tmp_path / ".agentpack").mkdir()
        result = _resolve_task(tmp_path, "/caveman ultra")
        assert result == "auto"

    def test_non_coding_prompt_falls_back_to_auto(self, tmp_path):
        (tmp_path / ".agentpack").mkdir()
        result = _resolve_task(tmp_path, "why does the DB pool work?")
        assert result == "auto"


class TestTaskSwitchDetection:
    def test_detects_disjoint_coding_task(self):
        assert _looks_like_task_switch(
            "add production-grade Kundali generation API",
            "fix numerology dashboard layout",
        )

    def test_related_task_is_not_switch(self):
        assert not _looks_like_task_switch("fix login flow", "fix login button bug")

    def test_vague_prompt_with_reference_is_switch(self):
        assert _looks_like_task_switch("fix login flow", "can you fix these")

    def test_min_terms_requires_more_concrete_overlap(self):
        assert not _looks_like_task_switch("fix login flow", "fix dashboard", min_terms=2)
