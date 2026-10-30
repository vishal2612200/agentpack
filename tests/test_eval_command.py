from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.evals import compare_eval_variants, load_eval_cases


def _py_cmd(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "agentpack@example.test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "AgentPack Tests"], cwd=root, check=True)
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "baseline"], cwd=root, check=True)


def _write_cases(root: Path, content: str) -> Path:
    path = root / ".agentpack" / "evals.toml"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    return path


def test_eval_init_creates_idempotent_scaffold(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first = runner.invoke(app, ["eval", "--init"])
    path = tmp_path / ".agentpack" / "evals.toml"
    path.write_text("existing", encoding="utf-8")
    second = runner.invoke(app, ["eval", "--init"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert path.read_text(encoding="utf-8") == "existing"


def test_load_eval_cases_rejects_unknown_failure_class(tmp_path: Path) -> None:
    path = _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "bad-class"\n'
        'task = "fix thing"\n'
        'failure_class = "llm_bad"\n',
    )

    with pytest.raises(ValueError, match="unknown failure_class"):
        load_eval_cases(path)


def test_eval_command_check_failure_exits_nonzero_with_prove_targets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "failing-check"\n'
        'task = "fix thing"\n'
        'failure_class = "verification"\n\n'
        '[[cases.checks]]\n'
        'name = "tests"\n'
        f'command = "{_py_cmd("import sys; sys.exit(7)")}"\n',
    )

    result = CliRunner().invoke(app, ["eval", "--prove-targets"])

    assert result.exit_code == 2, result.output
    assert "failing-check" in result.output
    assert "tests" in result.output


def test_forbidden_changed_file_fails_when_git_diff_touches_glob(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "src" / "db").mkdir(parents=True)
    (tmp_path / "src" / "db" / "schema.py").write_text("change\n", encoding="utf-8")
    _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "forbidden-db"\n'
        'task = "fix auth"\n'
        'failure_class = "over_action"\n'
        'forbidden_changed_files = ["src/db/**"]\n',
    )

    result = CliRunner().invoke(app, ["eval", "--prove-targets"])

    assert result.exit_code == 2, result.output
    assert "forbidden changed files: src/db/schema.py" in result.output


def test_required_changed_file_fails_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "other.py").write_text("change\n", encoding="utf-8")
    _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "missing-required"\n'
        'task = "fix auth"\n'
        'failure_class = "under_action"\n'
        'required_changed_files = ["src/auth/token.py"]\n',
    )

    result = CliRunner().invoke(app, ["eval", "--prove-targets"])

    assert result.exit_code == 2, result.output
    assert "required changed file missing: src/auth/token.py" in result.output


def test_max_changed_files_and_lines_fail_deterministically(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "a.py").write_text("1\n2\n3\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("1\n2\n3\n", encoding="utf-8")
    _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "too-large"\n'
        'task = "fix small bug"\n'
        'failure_class = "over_action"\n'
        'max_changed_files = 1\n'
        'max_changed_lines = 2\n',
    )

    result = CliRunner().invoke(app, ["eval", "--prove-targets"])

    assert result.exit_code == 2, result.output
    assert "changed files > 1" in result.output
    assert "changed lines > 2" in result.output


def test_capture_appends_case_from_current_diff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("change\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--capture",
            "auth-failure",
            "--failure-class",
            "context",
            "--check",
            _py_cmd("import sys; sys.exit(0)"),
        ],
    )

    assert result.exit_code == 0, result.output
    cases = load_eval_cases(tmp_path / ".agentpack" / "evals.toml")
    assert cases[0].id == "auth-failure"
    assert cases[0].required_changed_files == ["src/auth.py"]
    assert cases[0].patch_file == ".agentpack/evals/auth-failure.patch"
    assert (tmp_path / ".agentpack" / "evals" / "auth-failure.patch").exists()
    assert cases[0].checks[0].command.startswith(shlex.quote(sys.executable))


def test_capture_records_agentpack_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "src.py").write_text("change\n", encoding="utf-8")
    (tmp_path / ".agentpack").mkdir(exist_ok=True)
    (tmp_path / ".agentpack" / "context.md").write_text("packed context\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(
        json.dumps({"agent": "codex", "selected_files_meta": [{"path": "src.py"}]}),
        encoding="utf-8",
    )
    (tmp_path / "prompt.txt").write_text("fix src\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--capture",
            "metadata-case",
            "--failure-class",
            "context",
            "--prompt-file",
            "prompt.txt",
        ],
    )

    assert result.exit_code == 0, result.output
    case = load_eval_cases(tmp_path / ".agentpack" / "evals.toml")[0]
    assert case.agent == "codex"
    assert case.prompt_file == "prompt.txt"
    assert case.context_file == ".agentpack/context.md"
    assert case.context_hash
    assert case.selected_files == ["src.py"]
    assert case.agentpack_version


def test_capture_adds_citation_gates_when_manifest_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "src.py").write_text("change\n", encoding="utf-8")
    (tmp_path / ".agentpack").mkdir(exist_ok=True)
    (tmp_path / ".agentpack" / "context.md").write_text("packed context\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "citations.json").write_text(
        json.dumps({"schema_version": 1, "citations": []}),
        encoding="utf-8",
    )
    (tmp_path / ".agentpack" / "pack_metadata.json").write_text(
        json.dumps({"citation_manifest_path": ".agentpack/citations.json"}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--capture",
            "citation-case",
            "--failure-class",
            "context",
        ],
    )

    assert result.exit_code == 0, result.output
    case = load_eval_cases(tmp_path / ".agentpack" / "evals.toml")[0]
    assert case.citation_manifest == ".agentpack/citations.json"
    assert case.min_citation_coverage == 0.75
    assert case.max_invalid_citations == 0


def test_capture_redacts_secrets_from_patch_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    secret = "sk-" + "a" * 48
    (tmp_path / "settings.py").write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--capture",
            "secret-case",
            "--failure-class",
            "context",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Redacted 1 secret" in result.output
    patch = (tmp_path / ".agentpack" / "evals" / "secret-case.patch").read_text(encoding="utf-8")
    assert secret not in patch
    assert "[REDACTED:openai-key]" in patch
    case = load_eval_cases(tmp_path / ".agentpack" / "evals.toml")[0]
    assert case.patch_redaction_warnings
    assert "openai-key" in case.patch_redaction_warnings[0]


def test_eval_writes_jsonl_result_with_taxonomy_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "passing"\n'
        'task = "fix thing"\n'
        'failure_class = "context"\n'
        'failure_source = "agent_failed"\n\n'
        '[[cases.checks]]\n'
        'name = "tests"\n'
        f'command = "{_py_cmd("import sys; sys.exit(0)")}"\n',
    )

    result = CliRunner().invoke(app, ["eval"])

    assert result.exit_code == 0, result.output
    row = json.loads((tmp_path / ".agentpack" / "eval_results.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["case_id"] == "passing"
    assert row["passed"] is True
    assert row["variant"] == "agentpack"
    assert row["failure_class"] == "context"
    assert row["failure_source"] == "agent_failed"


def test_eval_replay_applies_captured_patch_in_isolated_worktree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("change\n", encoding="utf-8")
    capture = CliRunner().invoke(
        app,
        [
            "eval",
            "--capture",
            "replay-case",
            "--failure-class",
            "context",
            "--check",
            _py_cmd("from pathlib import Path; assert Path('src/auth.py').read_text() == 'change\\n'"),
        ],
    )
    assert capture.exit_code == 0, capture.output
    (tmp_path / "src" / "auth.py").unlink()

    result = CliRunner().invoke(app, ["eval", "--replay", "--prove-targets"])

    assert result.exit_code == 0, result.output
    row = json.loads((tmp_path / ".agentpack" / "eval_results.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["passed"] is True
    assert row["patch_file"] == ".agentpack/evals/replay-case.patch"


def test_eval_check_retries_mark_flaky_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    command = _py_cmd(
        "from pathlib import Path; import sys; p = Path('marker'); "
        "exists = p.exists(); p.write_text('x'); sys.exit(0 if exists else 1)"
    )
    _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "retry-case"\n'
        'task = "fix flaky test"\n'
        'failure_class = "flaky"\n\n'
        '[[cases.checks]]\n'
        'name = "tests"\n'
        f"command = '''{command}'''\n"
        "retries = 1\n",
    )

    result = CliRunner().invoke(app, ["eval", "--prove-targets"])

    assert result.exit_code == 0, result.output
    row = json.loads((tmp_path / ".agentpack" / "eval_results.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["checks"][0]["attempts"] == 2
    assert row["checks"][0]["flaky"] is True


def test_eval_variant_is_persisted_for_attribution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "baseline-case"\n'
        'task = "fix thing"\n'
        'failure_class = "context"\n\n'
        '[[cases.checks]]\n'
        'name = "tests"\n'
        f'command = "{_py_cmd("import sys; sys.exit(0)")}"\n',
    )

    result = CliRunner().invoke(app, ["eval", "--variant", "baseline"])

    assert result.exit_code == 0, result.output
    row = json.loads((tmp_path / ".agentpack" / "eval_results.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["variant"] == "baseline"


def test_compare_eval_variants_reports_improvement() -> None:
    comparison = compare_eval_variants(
        [
            {"case_id": "auth-timeout", "variant": "baseline", "passed": False},
            {"case_id": "auth-timeout", "variant": "agentpack", "passed": True},
        ],
        "baseline",
        "agentpack",
    )

    assert comparison["improved"] == 1
    assert comparison["regressed"] == 0
    assert comparison["rows"][0]["status"] == "improved"


def test_eval_compare_variants_cli_reads_result_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_jsonl = tmp_path / ".agentpack" / "eval_results.jsonl"
    _write_jsonl.parent.mkdir()
    _write_jsonl.write_text(
        json.dumps({"case_id": "auth-timeout", "variant": "baseline", "passed": False}) + "\n"
        + json.dumps({"case_id": "auth-timeout", "variant": "agentpack", "passed": True}) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["eval", "--compare-variants", "baseline:agentpack"])

    assert result.exit_code == 0, result.output
    assert "improved" in result.output
    assert "auth-timeout" in result.output


def test_eval_watch_with_max_runs_automates_single_cycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_cases(
        tmp_path,
        '[[cases]]\n'
        'id = "watch-case"\n'
        'task = "fix thing"\n'
        'failure_class = "context"\n\n'
        '[[cases.checks]]\n'
        'name = "tests"\n'
        f'command = "{_py_cmd("import sys; sys.exit(0)")}"\n',
    )

    result = CliRunner().invoke(app, ["eval", "--watch", "--max-runs", "1", "--interval", "0.01"])

    assert result.exit_code == 0, result.output
    assert "Watching deterministic evals" in result.output
    rows = (tmp_path / ".agentpack" / "eval_results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1


def test_eval_ci_template_scaffolds_github_action(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["eval", "--ci-template"])

    assert result.exit_code == 0, result.output
    workflow = tmp_path / ".github" / "workflows" / "agentpack-eval.yml"
    assert workflow.exists()
    assert "actions/checkout@v7" in workflow.read_text(encoding="utf-8")
    assert "agentpack eval --cases benchmarks/evals.toml --replay --prove-targets" in workflow.read_text(encoding="utf-8")
