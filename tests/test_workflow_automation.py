from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.config import LoopConfig
from agentpack.core.loop_protocol import LoopCommandResult, initialize_loop, load_loop_state, save_loop_state


def test_work_initializes_starts_and_runs_next(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        if "init" in command:
            (tmp_path / ".agentpack").mkdir()
            (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
        return Result()

    monkeypatch.setattr("agentpack.commands.workflow_cmd.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["work", "fix auth", "--thread", "codex-local", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert [stage["name"] for stage in payload["stages"]] == ["init", "start", "next"]
    assert any("start" in call for call in calls)
    events = (tmp_path / ".agentpack" / "session-events.jsonl").read_text(encoding="utf-8").splitlines()
    task_memory = [json.loads(line) for line in events if json.loads(line).get("type") == "task_memory"]
    assert task_memory[-1]["task"] == "fix auth"
    assert task_memory[-1]["stage"] == "work_start"
    assert task_memory[-1]["status"] == "ready"
    assert "cwd" not in task_memory[-1]
    assert task_memory[-1]["provenance"]["cwd"]


def test_work_run_dry_run_writes_loop_state_without_runner_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("agentpack.commands.workflow_cmd.subprocess.run", lambda *args, **kwargs: Result())

    result = CliRunner().invoke(
        app,
        [
            "work",
            "fix auth",
            "--run",
            "--dry-run",
            "--runner",
            "python -c 'raise SystemExit(9)'",
            "--verify",
            "python -c 'print(1)'",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["loop_plan"]["runner"] == "python -c 'raise SystemExit(9)'"
    assert load_loop_state(tmp_path).task == "fix auth"


def test_work_run_resolves_runner_adapter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("agentpack.commands.workflow_cmd.subprocess.run", lambda *args, **kwargs: Result())
    monkeypatch.setattr("agentpack.commands.workflow_cmd.resolve_runner_adapter", lambda adapter, root: "echo adapter")

    result = CliRunner().invoke(
        app,
        ["work", "fix auth", "--run", "--dry-run", "--runner-adapter", "claude", "--verify", "python -c 'print(1)'", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["loop_plan"]["runner"] == "echo adapter"


def test_work_run_requires_runner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n[loop]\nverification_commands = [\"python -c 'print(1)'\"]\n", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("agentpack.commands.workflow_cmd.subprocess.run", lambda *args, **kwargs: Result())

    result = CliRunner().invoke(app, ["work", "fix auth", "--run", "--pack-only"])

    assert result.exit_code == 1
    assert "Ralph Loop runner missing" in result.output


def test_work_run_executes_generic_runner_and_verification(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    monkeypatch.setattr("agentpack.commands.workflow_cmd.run_refresh", lambda *args, **kwargs: {"ok": True})

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("agentpack.commands.workflow_cmd.subprocess.run", lambda *args, **kwargs: Result())

    result = CliRunner().invoke(
        app,
        [
            "work",
            "fix auth",
            "--run",
            "--pack-only",
            "--runner",
            "python -c 'print(\"runner\")'",
            "--verify",
            "python -c 'print(\"verify\")'",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["loop_summary"]["status"] == "ready_to_finish"
    assert load_loop_state(tmp_path).status == "ready_to_finish"


def test_finish_runs_diagnosis_capture_checks_done_and_archive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack" / "threads" / "codex-local").mkdir(parents=True)
    (tmp_path / ".agentpack" / "threads" / "codex-local" / "task.md").write_text("fix auth\n", encoding="utf-8")
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return Result()

    monkeypatch.setattr("agentpack.commands.workflow_cmd.subprocess.run", fake_run)

    result = CliRunner().invoke(app, ["finish", "--since", "main", "--thread", "codex-local", "--archive-thread", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert [stage["name"] for stage in payload["stages"]] == [
        "diagnose-selection",
        "benchmark-capture",
        "dev-check",
        "state-done",
        "threads-archive",
    ]
    assert any(call[:3] == [call[0], "-m", "agentpack.cli"] for call in calls)
    events = [
        json.loads(line)
        for line in (tmp_path / ".agentpack" / "session-events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    check = next(event for event in events if event["event_type"] == "check_completed")
    assert check["check_kind"] == "development"
    assert check["returncode"] == 0
    assert "git_sha" in check
    assert "workspace_id" in check
    assert check["evidence"][0]["kind"] == "command"


def test_finish_blocks_when_loop_is_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth\n", encoding="utf-8")
    initialize_loop(tmp_path, "fix auth", LoopConfig(runner="agent", verification_commands=["pytest -q"]))

    monkeypatch.setattr("agentpack.commands.workflow_cmd._context_is_fresh", lambda *args, **kwargs: (True, "fresh"))

    result = CliRunner().invoke(
        app,
        ["finish", "--task", "fix auth", "--skip-checks", "--skip-diagnosis", "--skip-benchmark-capture", "--json"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["passed"] is False
    assert payload["loop_blockers"][0]["kind"] == "loop_not_ready"


def test_finish_marks_ready_loop_done(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n[loop]\nrequire_clean_tree = false\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth\n", encoding="utf-8")
    state = initialize_loop(tmp_path, "fix auth", LoopConfig(runner="agent", verification_commands=["pytest -q"], require_clean_tree=False))
    state.status = "ready_to_finish"
    state.last_verification = LoopCommandResult(command="pytest -q", returncode=0, output_excerpt="passed")
    state.acceptance_file = ".agentpack/loop_acceptance.md"
    state.last_diff.changed = True
    save_loop_state(tmp_path, state)

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("agentpack.commands.workflow_cmd.subprocess.run", lambda *args, **kwargs: Result())
    monkeypatch.setattr("agentpack.commands.workflow_cmd._context_is_fresh", lambda *args, **kwargs: (True, "fresh"))

    result = CliRunner().invoke(
        app,
        [
            "finish",
            "--task",
            "fix auth",
            "--skip-checks",
            "--skip-diagnosis",
            "--skip-benchmark-capture",
            "--allow-empty-capture",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_loop_state(tmp_path).status == "done"


def test_finish_blocks_ready_loop_with_empty_diff_without_allow_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n[loop]\nrequire_clean_tree = false\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth\n", encoding="utf-8")
    state = initialize_loop(tmp_path, "fix auth", LoopConfig(runner="agent", verification_commands=["pytest -q"], require_clean_tree=False))
    state.status = "ready_to_finish"
    state.last_verification = LoopCommandResult(command="pytest -q", returncode=0, output_excerpt="passed")
    state.acceptance_file = ".agentpack/loop_acceptance.md"
    save_loop_state(tmp_path, state)
    monkeypatch.setattr("agentpack.commands.workflow_cmd._context_is_fresh", lambda *args, **kwargs: (True, "fresh"))

    result = CliRunner().invoke(
        app,
        ["finish", "--task", "fix auth", "--skip-checks", "--skip-diagnosis", "--skip-benchmark-capture", "--json"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["loop_blockers"][0]["kind"] == "empty_loop_diff"


def test_loop_smoke_uses_deterministic_runner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["loop-smoke", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True


def test_loop_metrics_outputs_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "loop_metrics.jsonl").write_text(
        json.dumps({"outcome": "ready_to_finish", "iterations": 2}) + "\n"
        + json.dumps({"outcome": "blocked", "iterations": 1, "failure_class": "test_assertion"}) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["loop-metrics", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["runs"] == 2
    assert payload["ready_to_finish"] == 1
    assert payload["blocked"] == 1


def test_loop_rollback_reverses_current_tracked_diff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test User", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["loop-rollback", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["applied"] is True
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_ci_init_writes_workflow_and_refuses_overwrite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first = runner.invoke(app, ["ci", "init", "--json"])
    second = runner.invoke(app, ["ci", "init", "--json"])

    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["written"] is True
    workflow = tmp_path / ".github" / "workflows" / "agentpack.yml"
    assert workflow.exists()
    workflow_text = workflow.read_text(encoding="utf-8")
    assert "python -m agentpack.cli loop-smoke --json" in workflow_text
    assert "fetch-depth: 0" in workflow_text
    assert 'python -m pip install -e ".[dev]"' in workflow_text
    assert 'python -m pip install -e ".[dev]" build' not in workflow_text
    assert "python -m agentpack.cli release-check --profile ci" in workflow_text
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["written"] is False


def test_next_fix_all_safe_initializes_and_writes_diagnosis(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        (tmp_path / ".agentpack").mkdir(exist_ok=True)
        (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
        return Result()

    monkeypatch.setattr("agentpack.commands.next_cmd.subprocess.run", fake_run)
    monkeypatch.setattr("agentpack.commands.next_cmd._context_is_fresh", lambda _root, **_kwargs: (True, "fresh"))

    result = CliRunner().invoke(app, ["next", "--fix-all-safe", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["fixes"][0]["kind"] == "init"
