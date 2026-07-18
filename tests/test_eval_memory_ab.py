from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.commands.eval_cmd import _memory_ab_prove_target_failure, _run_memory_ab
import json

from agentpack.core.evals import EvalCase, EvalCheck, run_eval_case
from agentpack.learning.episodes import record_episode


def test_eval_memory_ab_compares_memory_selection(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text(
        "[context]\ndefault_budget = 8000\nmemory_boost_weight = 60\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "retry_after.py").write_text(
        "def retry_after_handling():\n    return 'retry-after'\n",
        encoding="utf-8",
    )
    record_episode(
        tmp_path,
        task="retry after handling",
        selected_files=["src/retry_after.py"],
        changed_files=["src/retry_after.py"],
        passed=True,
    )
    case = EvalCase(
        id="retry-after",
        task="retry after handling",
        failure_class="context",
        required_changed_files=["src/retry_after.py"],
    )

    comparison = _run_memory_ab(tmp_path, [case])

    assert comparison["rows"][0]["memory_hits"] >= comparison["rows"][0]["baseline_hits"]
    assert comparison["rows"][0]["memory_signal_selected_files"] >= 1
    assert comparison["rows"][0]["memory_signal_compacted_files"] == 0
    assert comparison["memory_signals_tested"] is True
    assert comparison["memory_signal_selected_files"] >= 1
    assert comparison["memory_signal_compacted_files"] == 0
    assert _memory_ab_prove_target_failure(comparison) == ""
    assert comparison["regressed"] == 0


def test_eval_memory_ab_prove_targets_fails_without_memory_signals(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text(
        "[context]\ndefault_budget = 8000\nmemory_boost_weight = 60\n",
        encoding="utf-8",
    )
    (tmp_path / ".agentpack" / "evals.toml").write_text(
        '[[cases]]\n'
        'id = "retry-after"\n'
        'task = "retry after handling"\n'
        'failure_class = "context"\n'
        'required_changed_files = ["src/retry_after.py"]\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "retry_after.py").write_text(
        "def retry_after_handling():\n    return 'retry-after'\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["eval", "--memory-ab", "--prove-targets"])

    assert result.exit_code == 2, result.output
    assert "no memory-confirmed selected files" in result.output


def test_eval_memory_ab_can_run_deterministic_checks(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\ndefault_budget = 8000\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hidden.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    case = EvalCase(
        id="checks",
        task="retry after handling",
        failure_class="context",
        checks=[EvalCheck(name="ok", command="python -c 'print(1)'", timeout_s=10)],
    )

    comparison = _run_memory_ab(tmp_path, [case], run_checks=True)

    assert comparison["rows"][0]["baseline_passed"] is True
    assert comparison["rows"][0]["memory_passed"] is True
    assert comparison["memory_signal_compacted_files"] == 0
    assert comparison["regressed"] == 0


def test_eval_native_checks_validate_citation_manifest(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "citations.json").write_text(
        json.dumps({
            "schema_version": 1,
            "citations": [
                {"path": "src/app.py", "start_line": 1, "end_line": 1, "kind": "code"},
                {"path": "src/app.py", "start_line": 99, "end_line": 99, "kind": "code"},
            ],
        }),
        encoding="utf-8",
    )
    case = EvalCase(
        id="citations",
        task="validate citations",
        failure_class="context",
        citation_manifest=".agentpack/citations.json",
        min_citation_coverage=0.75,
        max_invalid_citations=0,
    )

    result = run_eval_case(tmp_path, case)

    assert result.citation_coverage == 0.5
    assert result.invalid_citation_count == 1
    assert not result.passed
    assert {check.name for check in result.failed_checks} == {"min_citation_coverage", "max_invalid_citations"}
