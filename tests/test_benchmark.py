from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.models import DependencyGraph, FileInfo, Receipt
from agentpack.commands.benchmark import (
    BenchmarkCase,
    CaseResult,
    E2ECase,
    PublicRepoCase,
    PublicRepoSpec,
    ReleaseGateConfig,
    _precision_recall,
    _ownership_metrics,
    _owner_evidence_report,
    _selection_v2_evidence_diagnostics,
    _skill_metrics,
    _sample_fixture_cases,
    _load_cases,
    _load_e2e_cases,
    _scaffold_cases,
    _run_case,
    _run_e2e_case,
    _persist_result,
    _load_history_cases,
    _random_baseline,
    _write_results_template,
    _public_benchmark_markdown,
    _write_public_benchmark_table,
    _quality_status,
    _load_public_repo_specs,
    _load_release_gate_config,
    _filter_public_repo_specs,
    _ensure_public_repo_clone,
    _run_public_repo_suite,
    _write_public_repo_lock,
    _public_changed_files,
    _public_commit_changed_files,
    _sample_public_history_cases,
    _write_anonymous_benchmark_report,
    _is_test_path,
    _expected_files_touched,
    _unexpected_files_touched,
    _timeout_result,
    _e2e_prompt,
    _e2e_ab_metrics,
    _e2e_ab_markdown,
    _ensure_git_commit,
    _e2e_cases_template,
    _estimate_token_cost,
    _process_output_tokens,
    _estimate_agent_tool_calls,
    _time_to_first_expected_file,
    _candidate_recall_at,
    _candidate_precision_at,
    _miss_failure_type,
    _path_family,
    _reason_family_precision,
    _selected_family_tokens,
    _low_budget_extra_file_waste,
    _low_budget_waste_summary,
    _write_results_jsonl,
    _replacement_pair_diagnostics,
    _same_scope_replacement_opportunities,
    _plausibly_useful_selected_noise,
    _label_audit_summary,
    _benchmark_ablation_report,
    _benchmark_fixed_selected_excerpt_projection,
    _fixed_selected_excerpt_projection,
    _label_free_tiered_excerpt_projection,
    _ast_checkpoint_memory_excerpt_projection,
    _ranked_test_skeleton_excerpt_projection,
    _ranked_test_symbol_carrier_excerpt_projection,
    _ranked_source_churn_excerpt_projection,
    _ranked_source_metadata_excerpt_projection,
    _ranked_metadata_summary_excerpt_projection,
    _mav_span_excerpt_projection,
    _neutral_mav_span_excerpt_projection,
    _oracle_non_expected_excerpt_ceiling,
    _source_excerpt_confidence_tier,
    _benchmark_mav_score,
    _ast_memory_signal_counts,
    _benchmark_intent_profile,
    _owner_file_recall,
    _expected_family_recall,
    _expected_include_mode_diagnostics,
    _expected_rank_distribution,
    _package_boundary_diagnostics,
)
from agentpack.analysis.ranking import build_keyword_plan


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_result(
    selected: list[str],
    expected: list[str],
    *,
    packed_tokens: int = 1000,
    raw_tokens: int = 10000,
    after_ignore_tokens: int = 8000,
    rank_at_k: int | None = None,
    candidate_recall_at_20: float | None = None,
    candidate_recall_at_50: float | None = None,
    candidate_recall_at_100: float | None = None,
    candidate_precision_at_3: float | None = None,
    candidate_precision_at_5: float | None = None,
    low_budget_extra_file_waste: int | None = None,
    precision_delta_if_drop_last_summary: float | None = None,
    expected_token_coverage: float | None = None,
    selected_family_tokens: dict[str, int] | None = None,
    selected_family_waste_tokens: dict[str, int] | None = None,
    reason_family_precision: dict[str, dict[str, float]] | None = None,
    failure_type_counts: dict[str, int] | None = None,
    noise_pct: float | None = None,
    random_f1: float | None = None,
    missed_expected: list[dict] | None = None,
    top_candidates: list[dict] | None = None,
    selection_diagnostics: dict | None = None,
) -> CaseResult:
    return CaseResult(
        case=BenchmarkCase(task="t", expected_files=expected),
        packed_tokens=packed_tokens,
        raw_tokens=raw_tokens,
        after_ignore_tokens=after_ignore_tokens,
        saving_pct=(1 - packed_tokens / raw_tokens) * 100,
        saving_pct_honest=(1 - packed_tokens / after_ignore_tokens) * 100,
        selected_paths=selected,
        selected_tokens={p: 100 for p in selected},
        changed_covered=0,
        changed_total=0,
        total_s=0.1,
        phase_times={},
        rank_at_k=rank_at_k,
        candidate_recall_at_20=candidate_recall_at_20,
        candidate_recall_at_50=candidate_recall_at_50,
        candidate_recall_at_100=candidate_recall_at_100,
        candidate_precision_at_3=candidate_precision_at_3,
        candidate_precision_at_5=candidate_precision_at_5,
        low_budget_extra_file_waste=low_budget_extra_file_waste,
        precision_delta_if_drop_last_summary=precision_delta_if_drop_last_summary,
        expected_token_coverage=expected_token_coverage,
        selected_family_tokens=selected_family_tokens or {},
        selected_family_waste_tokens=selected_family_waste_tokens or {},
        reason_family_precision=reason_family_precision or {},
        failure_type_counts=failure_type_counts or {},
        noise_pct=noise_pct,
        random_precision=None,
        random_recall=None,
        random_f1=random_f1,
        missed_expected=missed_expected or [],
        top_candidates=top_candidates or [],
        selection_diagnostics=selection_diagnostics or {},
    )


# ---------------------------------------------------------------------------
# _precision_recall
# ---------------------------------------------------------------------------


def test_ensure_git_commit_fetches_missing_shallow_commit(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    cat_file_results = [1, 0]

    def fake_run(command, **kwargs):
        parts = [str(part) for part in command]
        calls.append(parts)
        if parts[:3] == ["git", "cat-file", "-e"]:
            return subprocess.CompletedProcess(parts, cat_file_results.pop(0), "", "")
        return subprocess.CompletedProcess(parts, 0, "", "")

    monkeypatch.setattr("agentpack.commands.benchmark.subprocess.run", fake_run)

    _ensure_git_commit(tmp_path, "abc123")

    assert ["git", "fetch", "--quiet", "--depth", "2", "origin", "abc123"] in calls

def test_precision_recall_perfect() -> None:
    r = _make_result(["a.py", "b.py"], ["a.py", "b.py"])
    p, rec, f1 = _precision_recall(r)
    assert p == 1.0
    assert rec == 1.0
    assert f1 == 1.0


def test_precision_recall_zero_recall() -> None:
    r = _make_result(["c.py", "d.py"], ["a.py", "b.py"])
    p, rec, f1 = _precision_recall(r)
    assert p == 0.0
    assert rec == 0.0
    assert f1 == 0.0


def test_precision_recall_partial() -> None:
    r = _make_result(["a.py", "c.py"], ["a.py", "b.py"])
    p, rec, f1 = _precision_recall(r)
    assert p == pytest.approx(0.5)
    assert rec == pytest.approx(0.5)
    assert f1 == pytest.approx(0.5)


def test_precision_recall_no_expected_returns_zeros() -> None:
    r = _make_result(["a.py"], [])
    p, rec, f1 = _precision_recall(r)
    assert p == 0.0 and rec == 0.0 and f1 == 0.0


def test_precision_recall_empty_selected() -> None:
    r = _make_result([], ["a.py"])
    p, rec, f1 = _precision_recall(r)
    assert f1 == 0.0


def test_candidate_recall_at_k_counts_expected_files_in_ranked_candidates() -> None:
    scored_paths = ["noise.py", "a.py", "more_noise.py", "b.py"]

    assert _candidate_recall_at(scored_paths, {"a.py", "b.py", "c.py"}, 2) == pytest.approx(1 / 3)
    assert _candidate_recall_at(scored_paths, {"a.py", "b.py", "c.py"}, 4) == pytest.approx(2 / 3)
    assert _candidate_recall_at(scored_paths, set(), 4) == 0.0


def test_candidate_precision_at_k_counts_noise_in_ranked_candidates() -> None:
    scored_paths = ["noise.py", "a.py", "more_noise.py", "b.py"]

    assert _candidate_precision_at(scored_paths, {"a.py", "b.py"}, 3) == pytest.approx(1 / 3)
    assert _candidate_precision_at(scored_paths, {"a.py", "b.py"}, 4) == pytest.approx(0.5)
    assert _candidate_precision_at([], {"a.py"}, 4) == 0.0


def test_path_family_and_selected_family_tokens_group_noise_sources() -> None:
    tokens = {
        "src/parser.ts": 100,
        "playground/css/index.ts": 200,
        "docs/parser.md": 300,
        "tests/parser.spec.ts": 400,
        "package.json": 50,
    }

    assert _path_family("src/parser.ts") == "source"
    assert _path_family("playground/css/index.ts") == "examples"
    assert _path_family("docs/parser.md") == "docs"
    assert _selected_family_tokens(list(tokens), tokens) == {
        "config": 50,
        "docs": 300,
        "examples": 200,
        "source": 100,
        "test": 400,
    }


def test_reason_family_precision_counts_expected_signal_quality() -> None:
    selected = [
        SimpleNamespace(path="src/expected.py", reasons=["filename keyword match", "content keyword match (2)"]),
        SimpleNamespace(path="docs/noise.md", reasons=["filename keyword match", "matched role keyword: docs"]),
    ]

    stats = _reason_family_precision(selected, {"src/expected.py"})

    assert stats["filename"]["selected"] == 2
    assert stats["filename"]["expected"] == 1
    assert stats["filename"]["precision"] == pytest.approx(0.5)
    assert stats["content"]["precision"] == pytest.approx(1.0)
    assert stats["summary"]["precision"] == pytest.approx(0.0)


def test_miss_failure_type_classifies_benchmark_funnel_stage() -> None:
    fi = SimpleNamespace()

    assert _miss_failure_type(fi=None, scored_info=None, status="", selected_count=3) == "EXPECTED_NOT_FOUND"
    assert _miss_failure_type(fi=fi, scored_info=None, status="", selected_count=3) == "EXPECTED_NOT_SCORED"
    assert _miss_failure_type(
        fi=fi,
        scored_info={"rank": 120, "score": 10},
        status="ranked but not selected",
        selected_count=3,
    ) == "EXPECTED_RANKED_LOW"
    assert _miss_failure_type(
        fi=fi,
        scored_info={"rank": 5, "score": 100},
        status="compressed context cap reached",
        selected_count=3,
    ) == "EXPECTED_SKIPPED"
    assert _miss_failure_type(
        fi=fi,
        scored_info={"rank": 4, "score": 100},
        status="ranked but not selected",
        selected_count=3,
    ) == "NOISE_SELECTED_ABOVE_EXPECTED"


def test_low_budget_extra_file_waste_reports_drop_last_summary_delta() -> None:
    selected = [
        SimpleNamespace(path="expected.py", include_mode="summary"),
        SimpleNamespace(path="noise.py", include_mode="summary"),
    ]

    waste, delta = _low_budget_extra_file_waste(
        selected=selected,
        selected_tokens={"expected.py": 100, "noise.py": 100},
        expected_files={"expected.py"},
        packed_tokens=200,
        expected_tokens=100,
        budget=2000,
        changed_files_source="no live changes detected",
    )

    assert waste == 100
    assert delta == pytest.approx(0.5)


def test_low_budget_extra_file_waste_ignores_expected_last_summary() -> None:
    selected = [
        SimpleNamespace(path="noise.py", include_mode="summary"),
        SimpleNamespace(path="expected.py", include_mode="summary"),
    ]

    waste, delta = _low_budget_extra_file_waste(
        selected=selected,
        selected_tokens={"expected.py": 100, "noise.py": 100},
        expected_files={"expected.py"},
        packed_tokens=200,
        expected_tokens=100,
        budget=2000,
        changed_files_source="no live changes detected",
    )

    assert waste == 0
    assert delta == pytest.approx(-0.5)


def test_low_budget_waste_summary_averages_observed_cases() -> None:
    rows = [
        _make_result(
            ["expected.py", "noise.py"],
            ["expected.py"],
            low_budget_extra_file_waste=100,
            precision_delta_if_drop_last_summary=0.5,
        ),
        _make_result(
            ["noise.py", "expected.py"],
            ["expected.py"],
            low_budget_extra_file_waste=0,
            precision_delta_if_drop_last_summary=-0.5,
        ),
        _make_result(["other.py"], ["expected.py"]),
    ]

    avg_waste, avg_delta, cases = _low_budget_waste_summary(rows)

    assert cases == 2
    assert avg_waste == pytest.approx(50)
    assert avg_delta == pytest.approx(0.0)


def test_replacement_pair_diagnostics_parse_marginal_receipts() -> None:
    rows = _replacement_pair_diagnostics(
        receipts=[
            Receipt(
                path="src/noise.py",
                action="excluded",
                reason="marginal slot replaced by src/expected.py",
            )
        ],
        scored_map={
            "src/noise.py": {"rank": 1, "score": 300.0, "reasons": ["filename keyword match"]},
            "src/expected.py": {
                "rank": 5,
                "score": 260.0,
                "reasons": ["matched define: expected", "content keyword match (4)"],
            },
        },
        selected_tokens={"src/noise.py": 120},
    )

    assert rows == [{
        "displaced": "src/noise.py",
        "challenger": "src/expected.py",
        "displaced_score": 300.0,
        "challenger_score": 260.0,
        "challenger_rank": 5,
        "displaced_tokens": 120,
        "challenger_reasons": ["matched define: expected", "content keyword match (4)"],
        "displaced_reasons": ["filename keyword match"],
    }]


def test_same_scope_replacement_opportunities_find_token_neutral_stronger_miss() -> None:
    rows = _same_scope_replacement_opportunities(
        missed_expected=[{
            "path": "packages/vite/src/node/server/index.ts",
            "status": "compressed context cap reached",
            "rank": 12,
            "score": 240.0,
            "reasons": ["matched define: createServer", "content keyword match (4)"],
            "cap_block_diagnostic": {"candidate_tokens": 150},
        }],
        selected_noise=[{
            "path": "packages/vite/src/node/server/middleware.ts",
            "tokens": 180,
            "rank": 8,
            "score": 90.0,
            "reasons": ["filename keyword match"],
        }],
        scored_map={
            "packages/vite/src/node/server/index.ts": {
                "rank": 12,
                "score": 240.0,
                "reasons": ["matched define: createServer", "content keyword match (4)"],
                "estimated_tokens": 999,
            },
        },
    )

    assert rows == [{
        "missed": "packages/vite/src/node/server/index.ts",
        "selected_noise": "packages/vite/src/node/server/middleware.ts",
        "scope": "packages/vite/src/node/server",
        "missed_rank": 12,
        "noise_rank": 8,
        "missed_score": 240.0,
        "noise_score": 90.0,
        "missed_tokens": 150,
        "noise_tokens": 180,
        "token_delta": -30,
        "missed_evidence": 187.0,
        "noise_evidence": 34.5,
        "evidence_gain": 152.5,
        "missed_reasons": ["matched define: createServer", "content keyword match (4)"],
        "noise_reasons": ["filename keyword match"],
    }]


def test_same_scope_replacement_opportunities_ignore_unrelated_or_larger_miss() -> None:
    rows = _same_scope_replacement_opportunities(
        missed_expected=[{
            "path": "src/auth/session.py",
            "status": "compressed context cap reached",
            "rank": 9,
            "score": 250.0,
            "reasons": ["matched define: verify_session", "content keyword match (5)"],
        }],
        selected_noise=[{
            "path": "docs/session.md",
            "tokens": 300,
            "rank": 3,
            "score": 50.0,
            "reasons": ["filename keyword match"],
        }, {
            "path": "src/auth/cache.py",
            "tokens": 100,
            "rank": 4,
            "score": 50.0,
            "reasons": ["filename keyword match"],
        }],
        scored_map={
            "src/auth/session.py": {
                "rank": 9,
                "score": 250.0,
                "reasons": ["matched define: verify_session", "content keyword match (5)"],
                "estimated_tokens": 160,
            },
        },
    )

    assert rows == []


def test_plausibly_useful_selected_noise_flags_same_package_noise() -> None:
    rows = _plausibly_useful_selected_noise(
        selected_noise=[{
            "path": "packages/vite/src/node/server/middleware.ts",
            "tokens": 180,
            "rank": 8,
            "score": 90.0,
            "reasons": ["filename keyword match"],
        }, {
            "path": "docs/server.md",
            "tokens": 80,
            "rank": 2,
            "score": 120.0,
            "reasons": ["filename keyword match"],
        }],
        expected_set={"packages/vite/src/node/server/index.ts"},
        scored_map={},
    )

    assert rows == [{
        "path": "packages/vite/src/node/server/middleware.ts",
        "family": "source",
        "scope": "packages/vite/src/node/server",
        "workspace_package": "packages/vite",
        "rank": 8,
        "score": 90.0,
        "tokens": 180,
        "plausibility_reasons": [
            "same_or_related_scope_as_expected",
            "same_workspace_package_as_expected",
        ],
        "selection_reasons": ["filename keyword match"],
    }]


def test_label_audit_summary_estimates_plausible_unlabeled_tokens() -> None:
    summary = _label_audit_summary(
        selected_noise=[
            {"path": "packages/vite/src/node/server/middleware.ts", "tokens": 180},
            {"path": "docs/server.md", "tokens": 80},
        ],
        plausibly_useful=[
            {"path": "packages/vite/src/node/server/middleware.ts", "tokens": 180},
        ],
        packed_tokens=1000,
    )

    assert summary == {
        "selected_noise_count": 2,
        "selected_noise_tokens": 260,
        "plausibly_useful_count": 1,
        "plausibly_useful_tokens": 180,
        "audited_noise_tokens": 80,
        "adjusted_token_precision": 0.92,
    }


def test_ast_memory_signal_counts_sum_checkpoint_diagnostics() -> None:
    results = [
        _make_result(
            ["src/service.py"],
            ["src/service.py"],
            selection_diagnostics={
                "ast_checkpoint_memory_excerpt_projection": {
                    "memory_signal_selected_files": 2,
                    "memory_signal_projected_files": 1,
                }
            },
        ),
        _make_result(
            ["src/other.py"],
            [],
            selection_diagnostics={
                "ast_checkpoint_memory_excerpt_projection": {
                    "memory_signal_selected_files": 0,
                    "memory_signal_projected_files": 0,
                }
            },
        ),
    ]

    assert _ast_memory_signal_counts(results) == (2, 1)


def test_fixed_selected_excerpt_projection_preserves_selected_file_set(tmp_path: Path) -> None:
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "import os",
            "",
            "def target_handler(request):",
            "    value = request.get('target')",
            "    return value.upper()",
            "",
            "def unrelated_helper():",
            *[f"    value_{index} = {index}" for index in range(80)],
            "    return value_0",
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/service.py",
            include_mode="skeleton",
            reasons=["matched define: target_handler", "content keyword match (2)"],
            symbols=[],
        )
    ]

    projection = _fixed_selected_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 420},
        selected_modes={"src/service.py": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={
            "src/service.py": SimpleNamespace(path="src/service.py", content=source.read_text(encoding="utf-8")),
        },
        task="fix target handler",
        changed_paths=set(),
    )

    assert projection["selected_file_count_before"] == 1
    assert projection["selected_file_count_after"] == 1
    assert projection["selected_file_set_unchanged"] is True
    assert projection["projected_selected_tokens"] < projection["baseline_selected_tokens"]
    assert projection["projected_files"][0]["path"] == "src/service.py"

    guarded_projection = _fixed_selected_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 420},
        selected_modes={"src/service.py": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={
            "src/service.py": SimpleNamespace(path="src/service.py", content=source.read_text(encoding="utf-8")),
        },
        task="fix target handler",
        changed_paths=set(),
        guarded=True,
    )

    assert guarded_projection["removed_tokens"] == 0
    assert guarded_projection["projected_file_count"] == 0


def test_benchmark_fixed_selected_excerpt_projection_aggregates_records() -> None:
    records = [
        {
            "selection_diagnostics": {
                "fixed_selected_excerpt_projection": {
                    "selected_file_set_unchanged": True,
                    "baseline_selected_tokens": 500,
                    "projected_selected_tokens": 300,
                    "baseline_expected_tokens": 200,
                    "projected_expected_tokens": 180,
                    "removed_tokens": 200,
                    "expected_token_loss": 20,
                    "strict_noise_removed": 180,
                    "projected_file_count": 2,
                    "token_precision_delta": 0.2,
                    "projected_files": [{"path": "src/a.py"}],
                }
            },
            "task": "case a",
        },
        {
            "selection_diagnostics": {
                "fixed_selected_excerpt_projection": {
                    "selected_file_set_unchanged": True,
                    "baseline_selected_tokens": 100,
                    "projected_selected_tokens": 90,
                    "baseline_expected_tokens": 100,
                    "projected_expected_tokens": 90,
                    "removed_tokens": 10,
                    "expected_token_loss": 10,
                    "strict_noise_removed": 0,
                    "projected_file_count": 1,
                    "token_precision_delta": 0.0,
                    "projected_files": [{"path": "src/b.py"}],
                }
            },
            "task": "case b",
        },
    ]

    report = _benchmark_fixed_selected_excerpt_projection(records)

    assert report["cases"] == 2
    assert report["selected_file_set_violations"] == 0
    assert report["removed_tokens"] == 210
    assert report["expected_token_loss"] == 30
    assert report["strict_noise_removed"] == 180
    assert report["projected_files"] == 3
    assert report["projected_aggregate_token_precision"] == pytest.approx(270 / 390)


def test_oracle_non_expected_excerpt_ceiling_freezes_expected_files(tmp_path: Path) -> None:
    expected = tmp_path / "src" / "expected.py"
    noise = tmp_path / "src" / "noise.py"
    expected.parent.mkdir()
    expected.write_text(
        "\n".join([
            "import os",
            "",
            "def expected_handler(request):",
            "    return request.user",
            "",
            *[f"def expected_helper_{index}(): return {index}" for index in range(50)],
        ]),
        encoding="utf-8",
    )
    noise.write_text(
        "\n".join([
            "import pathlib",
            "import subprocess",
            "",
            "def broad_helper():",
            "    return pathlib.Path.cwd()",
            "",
            *[f"def unrelated_{index}(): return {index}" for index in range(70)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/expected.py",
            include_mode="skeleton",
            reasons=["matched define: expected_handler"],
            symbols=[],
        ),
        SimpleNamespace(
            path="src/noise.py",
            include_mode="skeleton",
            reasons=["content keyword match (1)", "filename keyword match"],
            symbols=[],
        ),
    ]

    projection = _oracle_non_expected_excerpt_ceiling(
        selected=selected,
        selected_tokens={"src/expected.py": 500, "src/noise.py": 300},
        selected_modes={"src/expected.py": "skeleton", "src/noise.py": "skeleton"},
        expected_set={"src/expected.py"},
        file_by_path={
            "src/expected.py": SimpleNamespace(path="src/expected.py", content=expected.read_text(encoding="utf-8")),
            "src/noise.py": SimpleNamespace(path="src/noise.py", content=noise.read_text(encoding="utf-8")),
        },
        task="fix expected handler",
        changed_paths=set(),
    )

    assert projection["oracle_uses_expected_labels"] is True
    assert projection["selected_file_set_unchanged"] is True
    assert projection["baseline_expected_tokens"] == 500
    assert projection["projected_expected_tokens"] == 500
    assert projection["expected_token_loss"] == 0
    assert projection["projected_selected_tokens"] < projection["baseline_selected_tokens"]
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["projected_files"][0]["path"] == "src/noise.py"


def test_oracle_non_expected_excerpt_ceiling_aggregates_records() -> None:
    records = [
        {
            "selection_diagnostics": {
                "oracle_non_expected_excerpt_ceiling": {
                    "selected_file_set_unchanged": True,
                    "baseline_selected_tokens": 800,
                    "projected_selected_tokens": 560,
                    "baseline_expected_tokens": 500,
                    "projected_expected_tokens": 500,
                    "removed_tokens": 240,
                    "expected_token_loss": 0,
                    "strict_noise_removed": 240,
                    "projected_file_count": 1,
                    "token_precision_delta": 0.2679,
                    "projected_files": [{"path": "src/noise.py"}],
                }
            },
            "task": "oracle case",
        }
    ]

    report = _benchmark_fixed_selected_excerpt_projection(
        records,
        diagnostic_key="oracle_non_expected_excerpt_ceiling",
        policy="oracle_non_expected_excerpt_ceiling_v1",
    )

    assert report["policy"] == "oracle_non_expected_excerpt_ceiling_v1"
    assert report["cases"] == 1
    assert report["selected_file_set_violations"] == 0
    assert report["removed_tokens"] == 240
    assert report["expected_token_loss"] == 0
    assert report["strict_noise_removed"] == 240
    assert report["projected_aggregate_token_precision"] == pytest.approx(500 / 560)


def test_source_excerpt_confidence_tier_classifies_strong_and_weak_files() -> None:
    strong = _source_excerpt_confidence_tier(
        path="src/service.py",
        mode="skeleton",
        reasons=[
            "matched define: target_handler",
            "direct content evidence +170",
            "content keyword match (3)",
        ],
        current_tokens=400,
        changed_paths=set(),
        symbols=[],
    )
    weak = _source_excerpt_confidence_tier(
        path="src/utils.py",
        mode="skeleton",
        reasons=["content keyword match (1)", "filename keyword match", "recently modified"],
        current_tokens=400,
        changed_paths=set(),
        symbols=[],
    )

    assert strong["tier"] == "strong"
    assert strong["role_tier"] == "strong_action_owner"
    assert weak["tier"] == "weak"
    assert strong["score"] > weak["score"]


def test_source_excerpt_confidence_tier_splits_strong_carrier_from_action_owner() -> None:
    owner = _source_excerpt_confidence_tier(
        path="src/service.py",
        mode="skeleton",
        reasons=[
            "matched define: target_handler",
            "direct content evidence +170",
            "content keyword match (3)",
        ],
        current_tokens=500,
        changed_paths=set(),
        symbols=[],
    )
    carrier = _source_excerpt_confidence_tier(
        path="gin.go",
        mode="skeleton",
        reasons=[
            "matched define: Engine",
            "direct dependency of changed file",
            "content keyword match (1)",
        ],
        current_tokens=700,
        changed_paths=set(),
        symbols=[],
    )

    assert owner["tier"] == "strong"
    assert owner["role_tier"] == "strong_action_owner"
    assert owner["strong_carrier"] is False
    assert carrier["tier"] == "strong"
    assert carrier["role_tier"] == "strong_carrier"
    assert carrier["strong_action_owner"] is False
    assert carrier["guarded_strong_carrier"] is True
    assert "hub_symbol_carrier" in carrier["carrier_reasons"]
    assert "large_low_density_symbol_carrier" in carrier["carrier_reasons"]


def test_label_free_tiered_excerpt_projection_protects_strong_and_shrinks_weak(tmp_path: Path) -> None:
    expected = tmp_path / "src" / "service.py"
    weak = tmp_path / "src" / "utils.py"
    expected.parent.mkdir()
    expected.write_text(
        "\n".join([
            "import os",
            "",
            "def target_handler(request):",
            "    return request.user",
            "",
            *[f"def expected_helper_{index}(): return {index}" for index in range(50)],
        ]),
        encoding="utf-8",
    )
    weak.write_text(
        "\n".join([
            "import pathlib",
            "",
            "def broad_helper():",
            "    return pathlib.Path.cwd()",
            "",
            *[f"def unrelated_{index}(): return {index}" for index in range(70)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/service.py",
            include_mode="skeleton",
            reasons=[
                "matched define: target_handler",
                "direct content evidence +170",
                "content keyword match (3)",
            ],
            symbols=[],
        ),
        SimpleNamespace(
            path="src/utils.py",
            include_mode="skeleton",
            reasons=["content keyword match (1)", "filename keyword match", "recently modified"],
            symbols=[],
        ),
    ]

    projection = _label_free_tiered_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 500, "src/utils.py": 300},
        selected_modes={"src/service.py": "skeleton", "src/utils.py": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={
            "src/service.py": SimpleNamespace(path="src/service.py", content=expected.read_text(encoding="utf-8")),
            "src/utils.py": SimpleNamespace(path="src/utils.py", content=weak.read_text(encoding="utf-8")),
        },
        task="fix target handler",
        changed_paths=set(),
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["tier_counts"] == {"strong_action_owner": 1, "weak": 1}
    assert projection["projected_tier_counts"] == {"weak": 1}
    assert projection["baseline_expected_tokens"] == 500
    assert projection["projected_expected_tokens"] == 500
    assert projection["expected_token_loss"] == 0
    assert projection["removed_tokens_by_tier"]["weak"] == projection["removed_tokens"]
    assert projection["projected_files"][0]["path"] == "src/utils.py"


def test_risk_aware_tiered_excerpt_projection_protects_structural_medium_file(tmp_path: Path) -> None:
    source = tmp_path / "context.go"
    source.write_text(
        "\n".join([
            "package render",
            "",
            "func renderContextAbort() error {",
            "    return nil",
            "}",
            "",
            *[f"func unrelated{index}() int {{ return {index} }}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="context.go",
            include_mode="skeleton",
            reasons=[
                "content keyword match (2)",
                "direct dependency of changed file",
            ],
            symbols=[],
        )
    ]
    common_kwargs = {
        "selected": selected,
        "selected_tokens": {"context.go": 500},
        "selected_modes": {"context.go": "skeleton"},
        "expected_set": {"context.go"},
        "file_by_path": {"context.go": SimpleNamespace(path="context.go", content=source.read_text(encoding="utf-8"))},
        "task": "fix render context abort",
        "changed_paths": set(),
    }

    rejected_policy = _label_free_tiered_excerpt_projection(**common_kwargs)
    risk_aware = _label_free_tiered_excerpt_projection(
        **common_kwargs,
        policy="risk_aware_tiered_source_excerpt_v1",
    )

    assert rejected_policy["projected_file_count"] == 1
    assert rejected_policy["expected_token_loss"] > 0
    assert risk_aware["tier_counts"] == {"medium": 1}
    assert risk_aware["projected_file_count"] == 0
    assert risk_aware["expected_token_loss"] == 0


def test_risk_aware_tiered_excerpt_projection_protects_strong_files(tmp_path: Path) -> None:
    source = tmp_path / "docs.md"
    source.write_text(
        "\n".join([
            "# Render docs",
            "",
            "The render context handles failures.",
            "",
            *[f"unrelated line {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="docs.md",
            include_mode="summary",
            reasons=[
                "matched define: render_context",
                "content keyword match (3)",
                "direct content evidence +170",
                "config file",
            ],
            symbols=[],
        )
    ]

    projection = _label_free_tiered_excerpt_projection(
        selected=selected,
        selected_tokens={"docs.md": 500},
        selected_modes={"docs.md": "summary"},
        expected_set=set(),
        file_by_path={"docs.md": SimpleNamespace(path="docs.md", content=source.read_text(encoding="utf-8"))},
        task="fix render context",
        changed_paths=set(),
        policy="risk_aware_tiered_source_excerpt_v1",
    )

    assert projection["tier_counts"] == {"strong_action_owner": 1}
    assert projection["projected_file_count"] == 0
    assert projection["removed_tokens"] == 0


def test_strong_carrier_excerpt_projection_shrinks_carrier_not_owner(tmp_path: Path) -> None:
    owner = tmp_path / "src" / "service.py"
    carrier = tmp_path / "gin.go"
    owner.parent.mkdir()
    owner.write_text(
        "\n".join([
            "import os",
            "",
            "def target_handler(request):",
            "    return request.user",
            "",
            *[f"def expected_helper_{index}(): return {index}" for index in range(70)],
        ]),
        encoding="utf-8",
    )
    carrier.write_text(
        "\n".join([
            "package gin",
            "",
            "type Engine struct {",
            "    RouterGroup RouterGroup",
            "}",
            "",
            "func NewEngine() *Engine {",
            "    return &Engine{}",
            "}",
            "",
            *[f"func unrelated{index}() int {{ return {index} }}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/service.py",
            include_mode="skeleton",
            reasons=[
                "matched define: target_handler",
                "direct content evidence +170",
                "content keyword match (3)",
            ],
            symbols=[],
        ),
        SimpleNamespace(
            path="gin.go",
            include_mode="skeleton",
            reasons=[
                "matched define: Engine",
                "direct dependency of changed file",
                "content keyword match (1)",
            ],
            symbols=[],
        ),
    ]

    projection = _label_free_tiered_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 500, "gin.go": 700},
        selected_modes={"src/service.py": "skeleton", "gin.go": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={
            "src/service.py": SimpleNamespace(path="src/service.py", content=owner.read_text(encoding="utf-8")),
            "gin.go": SimpleNamespace(path="gin.go", content=carrier.read_text(encoding="utf-8")),
        },
        task="fix engine routing",
        changed_paths=set(),
        policy="strong_carrier_source_excerpt_v1",
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["tier_counts"] == {"strong_carrier": 1, "strong_action_owner": 1}
    assert projection["projected_tier_counts"] == {"strong_carrier": 1}
    assert projection["expected_token_loss"] == 0
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["projected_files"][0]["path"] == "gin.go"
    assert projection["projected_files"][0]["base_tier"] == "strong"
    assert projection["projected_files"][0]["strong_carrier"] is True


def test_guarded_strong_carrier_projection_protects_structural_hub(tmp_path: Path) -> None:
    source = tmp_path / "context.go"
    source.write_text(
        "\n".join([
            "package gin",
            "",
            "type Context struct {",
            "    handlers []HandlerFunc",
            "}",
            "",
            "func (c *Context) Abort() {",
            "    c.index = abortIndex",
            "}",
            "",
            *[f"func unrelated{index}() int {{ return {index} }}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="context.go",
            include_mode="skeleton",
            reasons=[
                "matched define: Context",
                "matched role keyword: context",
                "content keyword match (2)",
            ],
            symbols=[],
        )
    ]
    common_kwargs = {
        "selected": selected,
        "selected_tokens": {"context.go": 700},
        "selected_modes": {"context.go": "skeleton"},
        "expected_set": {"context.go"},
        "file_by_path": {"context.go": SimpleNamespace(path="context.go", content=source.read_text(encoding="utf-8"))},
        "task": "fix context abort",
        "changed_paths": set(),
    }

    raw = _label_free_tiered_excerpt_projection(
        **common_kwargs,
        policy="strong_carrier_source_excerpt_v1",
    )
    guarded = _label_free_tiered_excerpt_projection(
        **common_kwargs,
        policy="guarded_strong_carrier_source_excerpt_v1",
    )

    assert raw["tier_counts"] == {"strong_carrier": 1}
    assert raw["projected_file_count"] == 1
    assert raw["expected_token_loss"] > 0
    assert guarded["tier_counts"] == {"strong_carrier": 1}
    assert guarded["projected_file_count"] == 0
    assert guarded["expected_token_loss"] == 0


def test_ast_checkpoint_memory_excerpt_projection_uses_summary_symbol_spans(tmp_path: Path) -> None:
    owner = tmp_path / "src" / "service.py"
    carrier = tmp_path / "gin.go"
    owner.parent.mkdir()
    owner.write_text(
        "\n".join([
            "import os",
            "",
            "def target_handler(request):",
            "    return request.user",
            "",
            *[f"def expected_helper_{index}(): return {index}" for index in range(70)],
        ]),
        encoding="utf-8",
    )
    carrier.write_text(
        "\n".join([
            "package gin",
            "",
            "// Engine routes HTTP requests.",
            "type Engine struct {",
            "    RouterGroup RouterGroup",
            "}",
            "",
            "func NewEngine() *Engine {",
            "    return &Engine{}",
            "}",
            "",
            *[f"func unrelated{index}() int {{ return {index} }}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/service.py",
            include_mode="skeleton",
            reasons=[
                "matched define: target_handler",
                "direct content evidence +170",
                "content keyword match (3)",
            ],
            symbols=[],
        ),
        SimpleNamespace(
            path="gin.go",
            include_mode="skeleton",
            reasons=[
                "matched define: Engine",
                "direct dependency of changed file",
                "content keyword match (1)",
            ],
            symbols=[],
        ),
    ]

    projection = _ast_checkpoint_memory_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 500, "gin.go": 900},
        selected_modes={"src/service.py": "skeleton", "gin.go": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={
            "src/service.py": SimpleNamespace(path="src/service.py", content=owner.read_text(encoding="utf-8")),
            "gin.go": SimpleNamespace(path="gin.go", content=carrier.read_text(encoding="utf-8")),
        },
        summaries={
            "src/service.py": {
                "entrypoints": ["target_handler"],
                "defines": ["target_handler"],
            },
            "gin.go": {
                "defines": ["Engine"],
                "calls": ["NewEngine"],
                "symbols": [{
                    "name": "Engine",
                    "kind": "type",
                    "start_line": 4,
                    "end_line": 6,
                    "signature": "type Engine struct",
                    "summary": "Engine routing support",
                    "body": "type Engine struct { RouterGroup RouterGroup }",
                }],
            },
        },
        task="fix engine routing",
        changed_paths=set(),
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["memory_signals_tested"] is False
    assert projection["memory_signal_selected_files"] == 0
    assert projection["tier_counts"] == {"ast_checkpoint_owner": 1, "ast_checkpoint_carrier": 1}
    assert projection["projected_tier_counts"] == {"ast_checkpoint_carrier": 1}
    assert projection["baseline_expected_tokens"] == 500
    assert projection["projected_expected_tokens"] == 500
    assert projection["expected_token_loss"] == 0
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["projected_files"][0]["path"] == "gin.go"
    assert projection["projected_files"][0]["reason"] == "ast_checkpoint_symbol_spans"
    assert "guarded_strong_carrier" in projection["projected_files"][0]["checkpoint_reasons"]
    assert projection["projected_files"][0]["memory_signal"] is False


def test_ast_checkpoint_memory_excerpt_projection_protects_structural_owner(tmp_path: Path) -> None:
    source = tmp_path / "context.go"
    source.write_text(
        "\n".join([
            "package gin",
            "",
            "type Context struct {",
            "    handlers []HandlerFunc",
            "}",
            "",
            "func (c *Context) Abort() {",
            "    c.index = abortIndex",
            "}",
            "",
            *[f"func unrelated{index}() int {{ return {index} }}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="context.go",
            include_mode="skeleton",
            reasons=[
                "matched define: Context",
                "matched role keyword: context",
                "content keyword match (2)",
            ],
            symbols=[],
        )
    ]

    projection = _ast_checkpoint_memory_excerpt_projection(
        selected=selected,
        selected_tokens={"context.go": 700},
        selected_modes={"context.go": "skeleton"},
        expected_set={"context.go"},
        file_by_path={"context.go": SimpleNamespace(path="context.go", content=source.read_text(encoding="utf-8"))},
        summaries={
            "context.go": {
                "defines": ["Context", "Abort"],
                "symbols": [{
                    "name": "Context",
                    "kind": "type",
                    "start_line": 3,
                    "end_line": 5,
                    "signature": "type Context struct",
                    "summary": "Request context state",
                    "body": "type Context struct { handlers []HandlerFunc }",
                }],
            },
        },
        task="fix context abort",
        changed_paths=set(),
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["tier_counts"] == {"ast_checkpoint_owner": 1}
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["removed_tokens"] == 0


def test_ast_checkpoint_memory_excerpt_projection_protects_literal_public_api_owner(tmp_path: Path) -> None:
    source = tmp_path / "packages" / "vite" / "src" / "node" / "index.ts"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join([
            "export function parseAst(code: string) {",
            "  return parse(code)",
            "}",
            "",
            "export async function parseAstAsync(code: string) {",
            "  return parseAst(code)",
            "}",
            "",
            *[f"export const unrelated{index} = {index}" for index in range(90)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="packages/vite/src/node/index.ts",
            include_mode="skeleton",
            reasons=[
                "symbol keyword match",
                "literal definition match: parse ast async",
                "matched define: parseAst",
                "quoted literal match: parse ast async",
            ],
            symbols=[],
        )
    ]

    projection = _ast_checkpoint_memory_excerpt_projection(
        selected=selected,
        selected_tokens={"packages/vite/src/node/index.ts": 700},
        selected_modes={"packages/vite/src/node/index.ts": "skeleton"},
        expected_set={"packages/vite/src/node/index.ts"},
        file_by_path={
            "packages/vite/src/node/index.ts": SimpleNamespace(
                path="packages/vite/src/node/index.ts",
                content=source.read_text(encoding="utf-8"),
            )
        },
        summaries={
            "packages/vite/src/node/index.ts": {
                "defines": ["parseAst", "parseAstAsync"],
                "public_api": ["parseAst", "parseAstAsync"],
                "symbols": [{
                    "name": "parseAst",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": "export function parseAst(code: string)",
                    "summary": "Parse AST public API",
                    "body": "export function parseAst(code: string) { return parse(code) }",
                }, {
                    "name": "parseAstAsync",
                    "kind": "function",
                    "start_line": 5,
                    "end_line": 7,
                    "signature": "export async function parseAstAsync(code: string)",
                    "summary": "Parse AST async public API",
                    "body": "export async function parseAstAsync(code: string) { return parseAst(code) }",
                }],
            },
        },
        task="correct parseAst parseAstAsync deprecation hints",
        changed_paths=set(),
    )

    assert projection["tier_counts"] == {"ast_checkpoint_owner": 1}
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["removed_tokens"] == 0


def test_ast_checkpoint_memory_excerpt_projection_keeps_release_metadata_carrier_compressible(tmp_path: Path) -> None:
    source = tmp_path / "src" / "markupsafe" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join([
            "from importlib.metadata import version",
            "",
            "class Markup(str):",
            "    def __html__(self):",
            "        return self",
            "",
            "def _has_version():",
            "    return version('markupsafe')",
            "",
            *[f"def unrelated_{index}(): return {index}" for index in range(90)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/markupsafe/__init__.py",
            include_mode="skeleton",
            reasons=[
                "matched call: importlib.metadata.version",
                "content keyword match (2)",
                "direct content evidence +120",
                "release/version metadata",
            ],
            symbols=[],
        )
    ]

    projection = _ast_checkpoint_memory_excerpt_projection(
        selected=selected,
        selected_tokens={"src/markupsafe/__init__.py": 700},
        selected_modes={"src/markupsafe/__init__.py": "skeleton"},
        expected_set=set(),
        file_by_path={
            "src/markupsafe/__init__.py": SimpleNamespace(
                path="src/markupsafe/__init__.py",
                content=source.read_text(encoding="utf-8"),
            )
        },
        summaries={
            "src/markupsafe/__init__.py": {
                "calls": ["importlib.metadata.version", "version"],
                "defines": ["Markup"],
                "symbols": [{
                    "name": "Markup",
                    "kind": "class",
                    "start_line": 3,
                    "end_line": 5,
                    "signature": "class Markup(str)",
                    "summary": "HTML markup string",
                    "body": "class Markup(str): def __html__(self): return self",
                }],
            },
        },
        task="start version 3.1.0",
        changed_paths=set(),
    )

    assert projection["tier_counts"] == {"ast_checkpoint_carrier": 1}
    assert projection["projected_tier_counts"] == {"ast_checkpoint_carrier": 1}
    assert projection["projected_file_count"] == 1
    assert projection["expected_token_loss"] == 0
    assert projection["strict_noise_removed"] == projection["removed_tokens"]


def test_ast_checkpoint_memory_excerpt_projection_compresses_ranked_test_support(tmp_path: Path) -> None:
    source = tmp_path / "tests" / "test_options.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "class CustomOption:",
            "    def get_help_record(self):",
            "        return 'help'",
            "",
            *[f"def unrelated_option_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="tests/test_options.py",
            include_mode="skeleton",
            reasons=[
                "filename keyword match",
                "matched ranking keyword: help",
                "matched define: CustomOption.get_help_record",
            ],
            symbols=[],
        )
    ]

    projection = _ast_checkpoint_memory_excerpt_projection(
        selected=selected,
        selected_tokens={"tests/test_options.py": 700},
        selected_modes={"tests/test_options.py": "skeleton"},
        expected_set=set(),
        file_by_path={
            "tests/test_options.py": SimpleNamespace(
                path="tests/test_options.py",
                content=source.read_text(encoding="utf-8"),
            )
        },
        summaries={
            "tests/test_options.py": {
                "defines": ["CustomOption.get_help_record"],
                "test_hints": ["help"],
                "symbols": [{
                    "name": "CustomOption.get_help_record",
                    "kind": "method",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": "def get_help_record(self)",
                    "summary": "Help record test support",
                    "body": "def get_help_record(self): return 'help'",
                }],
            },
        },
        task="Add missing space between option help text and deprecation label",
        changed_paths=set(),
        scored_map={"tests/test_options.py": {"rank": 4}},
    )

    assert projection["tier_counts"] == {"ast_checkpoint_carrier": 1}
    assert projection["projected_tier_counts"] == {"ast_checkpoint_carrier": 1}
    assert projection["projected_files"][0]["rank"] == 4
    assert "ranked_test_support_carrier" in projection["projected_files"][0]["checkpoint_reasons"]
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["expected_token_loss"] == 0


def test_ast_checkpoint_memory_excerpt_projection_uses_scored_reasons_for_ranked_support(tmp_path: Path) -> None:
    source = tmp_path / "tests" / "test_options.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "class CustomOption:",
            "    def get_help_record(self):",
            "        return 'help'",
            "",
            *[f"def unrelated_option_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="tests/test_options.py",
            include_mode="skeleton",
            reasons=[
                "symbol keyword match",
                "matched define: CustomOption.get_help_record",
            ],
            symbols=[],
        )
    ]

    projection = _ast_checkpoint_memory_excerpt_projection(
        selected=selected,
        selected_tokens={"tests/test_options.py": 700},
        selected_modes={"tests/test_options.py": "skeleton"},
        expected_set=set(),
        file_by_path={
            "tests/test_options.py": SimpleNamespace(
                path="tests/test_options.py",
                content=source.read_text(encoding="utf-8"),
            )
        },
        summaries={
            "tests/test_options.py": {
                "defines": ["CustomOption.get_help_record"],
                "test_hints": ["help"],
                "symbols": [{
                    "name": "CustomOption.get_help_record",
                    "kind": "method",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": "def get_help_record(self)",
                    "summary": "Help record test support",
                    "body": "def get_help_record(self): return 'help'",
                }],
            },
        },
        task="Add missing space between option help text and deprecation label",
        changed_paths=set(),
        scored_map={
            "tests/test_options.py": {
                "rank": 4,
                "score": 250.0,
                "reasons": [
                    "filename keyword match",
                    "matched ranking keyword: help",
                ],
            }
        },
    )

    assert projection["tier_counts"] == {"ast_checkpoint_carrier": 1}
    assert projection["projected_file_count"] == 1
    assert projection["projected_files"][0]["rank"] == 4
    assert "ranked_test_support_carrier" in projection["projected_files"][0]["checkpoint_reasons"]
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["expected_token_loss"] == 0


def test_ranked_test_skeleton_excerpt_projection_compresses_low_action_support(tmp_path: Path) -> None:
    carrier = tmp_path / "tests" / "test_options.py"
    owner = tmp_path / "tests" / "test_shell_completion.py"
    carrier.parent.mkdir()
    carrier.write_text(
        "\n".join([
            "class CustomOption:",
            "    def get_help_record(self):",
            "        return 'help'",
            "",
            *[f"def unrelated_option_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    owner.write_text(
        "\n".join([
            "def test_completion_item_data():",
            "    assert 'multiline help'",
            "",
            *[f"def unrelated_completion_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="tests/test_options.py",
            include_mode="skeleton",
            reasons=["symbol keyword match", "matched define: CustomOption.get_help_record"],
            symbols=[],
        ),
        SimpleNamespace(
            path="tests/test_shell_completion.py",
            include_mode="skeleton",
            reasons=[
                "filename keyword match",
                "matched ranking keyword: help",
                "quoted literal match: multiline help",
                "explicit test task file",
            ],
            symbols=[],
        ),
    ]

    projection = _ranked_test_skeleton_excerpt_projection(
        selected=selected,
        selected_tokens={"tests/test_options.py": 700, "tests/test_shell_completion.py": 700},
        selected_modes={"tests/test_options.py": "skeleton", "tests/test_shell_completion.py": "skeleton"},
        expected_set={"tests/test_shell_completion.py"},
        file_by_path={
            "tests/test_options.py": SimpleNamespace(
                path="tests/test_options.py",
                content=carrier.read_text(encoding="utf-8"),
            ),
            "tests/test_shell_completion.py": SimpleNamespace(
                path="tests/test_shell_completion.py",
                content=owner.read_text(encoding="utf-8"),
            ),
        },
        task="Add missing space between option help text and deprecation label",
        changed_paths=set(),
        scored_map={
            "tests/test_options.py": {
                "rank": 4,
                "score": 250.0,
                "reasons": ["filename keyword match", "matched ranking keyword: help"],
            },
            "tests/test_shell_completion.py": {
                "rank": 4,
                "score": 260.0,
                "reasons": ["explicit test task file", "quoted literal match: multiline help"],
            },
        },
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["candidate_file_count"] == 2
    assert projection["eligible_file_count"] == 1
    assert projection["projected_tier_counts"] == {"ranked_test_skeleton_carrier": 1}
    assert projection["projected_files"][0]["path"] == "tests/test_options.py"
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["expected_token_loss"] == 0


def test_ranked_test_symbol_carrier_excerpt_projection_compresses_symbol_only_test(tmp_path: Path) -> None:
    source = tmp_path / "tests" / "test_options.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "class EnumSentinel:",
            "    pass",
            "",
            "def test_boolean_switch():",
            "    assert EnumSentinel",
            "",
            *[f"def unrelated_option_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="tests/test_options.py",
            include_mode="skeleton",
            reasons=[
                "symbol keyword match",
                "matched ranking keyword: sentinel",
                "matched define: EnumSentinel",
            ],
            symbols=[],
        ),
    ]

    projection = _ranked_test_symbol_carrier_excerpt_projection(
        selected=selected,
        selected_tokens={"tests/test_options.py": 700},
        selected_modes={"tests/test_options.py": "skeleton"},
        expected_set=set(),
        file_by_path={
            "tests/test_options.py": SimpleNamespace(
                path="tests/test_options.py",
                content=source.read_text(encoding="utf-8"),
            ),
        },
        task="Use default=True as a sentinel for non-boolean flags",
        changed_paths=set(),
        scored_map={
            "tests/test_options.py": {
                "rank": 2,
                "score": 250.0,
                "reasons": ["matched define: EnumSentinel"],
            },
        },
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["candidate_file_count"] == 1
    assert projection["eligible_file_count"] == 1
    assert projection["projected_tier_counts"] == {"ranked_test_symbol_carrier": 1}
    assert projection["projected_files"][0]["path"] == "tests/test_options.py"
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["expected_token_loss"] == 0


def test_ranked_test_symbol_carrier_excerpt_projection_protects_action_owner_tests(tmp_path: Path) -> None:
    direct = tmp_path / "tests" / "test_shell_completion.py"
    entrypoint = tmp_path / "tests" / "test_testing.py"
    direct.parent.mkdir()
    direct.write_text(
        "\n".join([
            "def test_completion_item_data():",
            "    assert 'multiline help'",
            "",
            *[f"def unrelated_completion_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    entrypoint.write_text(
        "\n".join([
            "def test_python_input():",
            "    input('hidden')",
            "",
            *[f"def unrelated_testing_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="tests/test_shell_completion.py",
            include_mode="skeleton",
            reasons=[
                "matched define: test_completion_item_data",
                "direct content evidence +120",
            ],
            symbols=[],
        ),
        SimpleNamespace(
            path="tests/test_testing.py",
            include_mode="skeleton",
            reasons=[
                "matched entrypoint: CLI command: test-python-input",
                "matched define: test_python_input",
            ],
            symbols=[],
        ),
    ]

    projection = _ranked_test_symbol_carrier_excerpt_projection(
        selected=selected,
        selected_tokens={"tests/test_shell_completion.py": 700, "tests/test_testing.py": 700},
        selected_modes={"tests/test_shell_completion.py": "skeleton", "tests/test_testing.py": "skeleton"},
        expected_set={"tests/test_shell_completion.py", "tests/test_testing.py"},
        file_by_path={
            "tests/test_shell_completion.py": SimpleNamespace(
                path="tests/test_shell_completion.py",
                content=direct.read_text(encoding="utf-8"),
            ),
            "tests/test_testing.py": SimpleNamespace(
                path="tests/test_testing.py",
                content=entrypoint.read_text(encoding="utf-8"),
            ),
        },
        task="Ensure fish completion handles multiline help strings correctly",
        changed_paths=set(),
        scored_map={
            "tests/test_shell_completion.py": {"rank": 1, "score": 260.0, "reasons": []},
            "tests/test_testing.py": {"rank": 2, "score": 240.0, "reasons": []},
        },
    )

    assert projection["candidate_file_count"] == 2
    assert projection["eligible_file_count"] == 0
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["projection_miss_reasons"] == {"protected_action_owner_signal": 2}


def test_ranked_test_symbol_carrier_excerpt_projection_protects_path_aligned_test(tmp_path: Path) -> None:
    source = tmp_path / "tests" / "test_shell_completion.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def test_completion_item_data():",
            "    assert 'help'",
            "",
            *[f"def unrelated_completion_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="tests/test_shell_completion.py",
            include_mode="skeleton",
            reasons=[
                "symbol keyword match",
                "matched ranking keyword: completion",
                "matched define: test_completion_item_data",
            ],
            symbols=[],
        ),
    ]

    projection = _ranked_test_symbol_carrier_excerpt_projection(
        selected=selected,
        selected_tokens={"tests/test_shell_completion.py": 700},
        selected_modes={"tests/test_shell_completion.py": "skeleton"},
        expected_set={"tests/test_shell_completion.py"},
        file_by_path={
            "tests/test_shell_completion.py": SimpleNamespace(
                path="tests/test_shell_completion.py",
                content=source.read_text(encoding="utf-8"),
            ),
        },
        task="Fix shell completion help output",
        changed_paths=set(),
        scored_map={
            "tests/test_shell_completion.py": {
                "rank": 1,
                "score": 260.0,
                "reasons": ["matched define: test_completion_item_data"],
            },
        },
    )

    assert projection["candidate_file_count"] == 1
    assert projection["eligible_file_count"] == 0
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["projection_miss_reasons"] == {"protected_path_task_alignment": 1}


def test_ranked_test_symbol_carrier_excerpt_projection_protects_refactor_test_owner(tmp_path: Path) -> None:
    source = tmp_path / "context_test.go"
    source.write_text(
        "\n".join([
            "func TestContextGetInt(t *testing.T) {",
            "    for i := range 10 {",
            "        _ = i",
            "    }",
            "}",
            "",
            *[f"func TestUnrelated{index}(t *testing.T) {{}}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="context_test.go",
            include_mode="skeleton",
            reasons=[
                "symbol keyword match",
                "matched define: TestContextGetInt",
                "content keyword match (3)",
                "recently modified",
            ],
            symbols=[],
        ),
    ]

    projection = _ranked_test_symbol_carrier_excerpt_projection(
        selected=selected,
        selected_tokens={"context_test.go": 700},
        selected_modes={"context_test.go": "skeleton"},
        expected_set={"context_test.go"},
        file_by_path={
            "context_test.go": SimpleNamespace(
                path="context_test.go",
                content=source.read_text(encoding="utf-8"),
            ),
        },
        task="refactor: for loop can be modernized using range over int",
        changed_paths=set(),
        scored_map={
            "context_test.go": {
                "rank": 2,
                "score": 260.0,
                "reasons": ["matched define: TestContextGetInt"],
            },
        },
    )

    assert projection["candidate_file_count"] == 1
    assert projection["eligible_file_count"] == 0
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["projection_miss_reasons"] == {"protected_refactor_test_owner": 1}


def test_ranked_source_churn_excerpt_projection_compresses_nonstructural_carrier(tmp_path: Path) -> None:
    source = tmp_path / "src" / "formatting.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def normalize_option_label(value):",
            "    cleaned = value.strip()",
            "    return cleaned.replace('_', '-')",
            "",
            *[f"def unrelated_formatter_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/formatting.py",
            include_mode="skeleton",
            reasons=["matched define: normalize_option_label"],
            symbols=[],
        ),
    ]

    projection = _ranked_source_churn_excerpt_projection(
        selected=selected,
        selected_tokens={"src/formatting.py": 700},
        selected_modes={"src/formatting.py": "skeleton"},
        expected_set=set(),
        file_by_path={
            "src/formatting.py": SimpleNamespace(
                path="src/formatting.py",
                content=source.read_text(encoding="utf-8"),
            ),
        },
        task="Adjust option label formatting",
        changed_paths=set(),
        scored_map={
            "src/formatting.py": {
                "rank": 4,
                "score": 240.0,
                "reasons": ["recently modified high churn"],
            },
        },
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["candidate_file_count"] == 1
    assert projection["eligible_file_count"] == 1
    assert projection["projected_tier_counts"] == {"ranked_source_churn_carrier": 1}
    assert projection["projected_files"][0]["path"] == "src/formatting.py"
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["expected_token_loss"] == 0


def test_ranked_source_churn_excerpt_projection_protects_structural_and_literal_sources(tmp_path: Path) -> None:
    structural = tmp_path / "src" / "core.py"
    literal = tmp_path / "src" / "formatting.py"
    structural.parent.mkdir()
    structural.write_text(
        "\n".join([
            "def configure_context(value):",
            "    return value",
            "",
            *[f"def unrelated_core_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    literal.write_text(
        "\n".join([
            "def normalize_option_label(value):",
            "    return 'missing-space'",
            "",
            *[f"def unrelated_formatter_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/core.py",
            include_mode="skeleton",
            reasons=["matched define: configure_context", "recently modified high churn"],
            symbols=[],
        ),
        SimpleNamespace(
            path="src/formatting.py",
            include_mode="skeleton",
            reasons=[
                "matched define: normalize_option_label",
                "literal definition match: missing-space",
                "recently modified high churn",
            ],
            symbols=[],
        ),
    ]

    projection = _ranked_source_churn_excerpt_projection(
        selected=selected,
        selected_tokens={"src/core.py": 700, "src/formatting.py": 700},
        selected_modes={"src/core.py": "skeleton", "src/formatting.py": "skeleton"},
        expected_set={"src/core.py", "src/formatting.py"},
        file_by_path={
            "src/core.py": SimpleNamespace(
                path="src/core.py",
                content=structural.read_text(encoding="utf-8"),
            ),
            "src/formatting.py": SimpleNamespace(
                path="src/formatting.py",
                content=literal.read_text(encoding="utf-8"),
            ),
        },
        task="Fix missing-space option label formatting",
        changed_paths=set(),
        scored_map={
            "src/core.py": {"rank": 4, "score": 260.0, "reasons": ["recently modified high churn"]},
            "src/formatting.py": {"rank": 4, "score": 250.0, "reasons": ["recently modified high churn"]},
        },
    )

    assert projection["candidate_file_count"] == 2
    assert projection["eligible_file_count"] == 0
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["projection_miss_reasons"] == {
        "protected_structural_risk": 1,
        "protected_literal_evidence": 1,
    }


def test_ranked_source_metadata_excerpt_projection_compresses_metadata_carrier(tmp_path: Path) -> None:
    source = tmp_path / "src" / "click" / "shell_completion.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join([
            "def _check_version(value):",
            "    return value",
            "",
            *[f"def unrelated_completion_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/click/shell_completion.py",
            include_mode="skeleton",
            reasons=[
                "symbol keyword match",
                "matched ranking keyword: version",
                "matched define: _check_version",
            ],
            symbols=[],
        ),
    ]

    projection = _ranked_source_metadata_excerpt_projection(
        selected=selected,
        selected_tokens={"src/click/shell_completion.py": 700},
        selected_modes={"src/click/shell_completion.py": "skeleton"},
        expected_set=set(),
        file_by_path={
            "src/click/shell_completion.py": SimpleNamespace(
                path="src/click/shell_completion.py",
                content=source.read_text(encoding="utf-8"),
            ),
        },
        task="start version 8.5.0",
        changed_paths=set(),
        scored_map={
            "src/click/shell_completion.py": {
                "rank": 2,
                "score": 240.0,
                "reasons": ["matched define: _check_version"],
            },
        },
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["candidate_file_count"] == 1
    assert projection["eligible_file_count"] == 1
    assert projection["projected_tier_counts"] == {"ranked_source_metadata_carrier": 1}
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["expected_token_loss"] == 0


def test_ranked_source_metadata_excerpt_projection_protects_package_init_and_structural_sources(tmp_path: Path) -> None:
    package_init = tmp_path / "src" / "itsdangerous" / "__init__.py"
    structural = tmp_path / "src" / "click" / "core.py"
    package_init.parent.mkdir(parents=True)
    structural.parent.mkdir(parents=True, exist_ok=True)
    package_init.write_text(
        "\n".join([
            "__version__ = '2.1.0'",
            "",
            *[f"def unrelated_init_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    structural.write_text(
        "\n".join([
            "def get_default_map(value):",
            "    return value",
            "",
            *[f"def unrelated_core_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/itsdangerous/__init__.py",
            include_mode="skeleton",
            reasons=["release/version metadata", "recently modified"],
            symbols=[],
        ),
        SimpleNamespace(
            path="src/click/core.py",
            include_mode="skeleton",
            reasons=["matched define: get_default_map", "release/version metadata"],
            symbols=[],
        ),
    ]

    projection = _ranked_source_metadata_excerpt_projection(
        selected=selected,
        selected_tokens={"src/itsdangerous/__init__.py": 700, "src/click/core.py": 700},
        selected_modes={"src/itsdangerous/__init__.py": "skeleton", "src/click/core.py": "skeleton"},
        expected_set={"src/itsdangerous/__init__.py", "src/click/core.py"},
        file_by_path={
            "src/itsdangerous/__init__.py": SimpleNamespace(
                path="src/itsdangerous/__init__.py",
                content=package_init.read_text(encoding="utf-8"),
            ),
            "src/click/core.py": SimpleNamespace(
                path="src/click/core.py",
                content=structural.read_text(encoding="utf-8"),
            ),
        },
        task="start version 2.1.0",
        changed_paths=set(),
        scored_map={
            "src/itsdangerous/__init__.py": {"rank": 1, "score": 260.0, "reasons": []},
            "src/click/core.py": {"rank": 2, "score": 240.0, "reasons": []},
        },
    )

    assert projection["candidate_file_count"] == 2
    assert projection["eligible_file_count"] == 0
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["projection_miss_reasons"] == {
        "protected_package_init_metadata_owner": 1,
        "protected_structural_risk": 1,
    }


def test_ranked_metadata_summary_excerpt_projection_compresses_ancillary_summary(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main" / "java" / "org" / "example" / "OwnerController.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join([
            "package org.example;",
            "",
            "class OwnerController {",
            "    void listOwners() {}",
            "}",
            "",
            *[f"class UnrelatedVersionCarrier{index} {{}}" for index in range(80)],
        ]),
        encoding="utf-8",
    )
    path = "src/main/java/org/example/OwnerController.java"
    selected = [
        SimpleNamespace(
            path=path,
            include_mode="summary",
            reasons=[
                "content keyword match (1)",
                "implementation role match",
                "high churn (9 commits)",
                "cross-layer related implementation",
            ],
            symbols=[],
        ),
    ]

    projection = _ranked_metadata_summary_excerpt_projection(
        selected=selected,
        selected_tokens={path: 300},
        selected_modes={path: "summary"},
        expected_set=set(),
        file_by_path={
            path: SimpleNamespace(
                path=path,
                content=source.read_text(encoding="utf-8"),
            ),
        },
        task="Update to current versions",
        changed_paths=set(),
        scored_map={path: {"rank": 3, "score": 250.0, "reasons": []}},
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["candidate_file_count"] == 1
    assert projection["eligible_file_count"] == 1
    assert projection["projected_tier_counts"] == {"ranked_metadata_summary_carrier": 1}
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["expected_token_loss"] == 0


def test_ranked_metadata_summary_excerpt_projection_protects_confirmed_summaries(tmp_path: Path) -> None:
    config_path = ".github/workflows/publish.yaml"
    owner_path = "src/main/java/org/example/OwnerRepository.java"
    selected = [
        SimpleNamespace(
            path=config_path,
            include_mode="summary",
            reasons=["content keyword match (1)", "recently modified"],
            symbols=[],
        ),
        SimpleNamespace(
            path=owner_path,
            include_mode="summary",
            reasons=["direct content evidence +170", "content keyword match (3)"],
            symbols=[],
        ),
    ]

    projection = _ranked_metadata_summary_excerpt_projection(
        selected=selected,
        selected_tokens={config_path: 180, owner_path: 180},
        selected_modes={config_path: "summary", owner_path: "summary"},
        expected_set={config_path, owner_path},
        file_by_path={},
        task="Update to current versions",
        changed_paths=set(),
        scored_map={
            config_path: {"rank": 1, "score": 300.0, "reasons": []},
            owner_path: {"rank": 3, "score": 260.0, "reasons": []},
        },
    )

    assert projection["candidate_file_count"] == 2
    assert projection["eligible_file_count"] == 0
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["projection_miss_reasons"] == {
        "protected_owner_rank": 1,
        "protected_confirmed_action_signal": 1,
    }


def test_ast_checkpoint_memory_excerpt_projection_protects_literal_test_owner_with_rank(tmp_path: Path) -> None:
    source = tmp_path / "tests" / "test_shell_completion.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def test_completion_item_data():",
            "    assert 'multiline help'",
            "",
            *[f"def unrelated_completion_test_{index}(): return {index}" for index in range(100)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="tests/test_shell_completion.py",
            include_mode="skeleton",
            reasons=[
                "filename keyword match",
                "matched ranking keyword: help",
                "matched define: test_completion_item_data",
                "quoted literal match: multiline help",
            ],
            symbols=[],
        )
    ]

    projection = _ast_checkpoint_memory_excerpt_projection(
        selected=selected,
        selected_tokens={"tests/test_shell_completion.py": 700},
        selected_modes={"tests/test_shell_completion.py": "skeleton"},
        expected_set={"tests/test_shell_completion.py"},
        file_by_path={
            "tests/test_shell_completion.py": SimpleNamespace(
                path="tests/test_shell_completion.py",
                content=source.read_text(encoding="utf-8"),
            )
        },
        summaries={
            "tests/test_shell_completion.py": {
                "defines": ["test_completion_item_data"],
                "test_hints": ["multiline help"],
                "symbols": [{
                    "name": "test_completion_item_data",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                    "signature": "def test_completion_item_data()",
                    "summary": "Tests multiline help completion item data",
                    "body": "def test_completion_item_data(): assert 'multiline help'",
                }],
            },
        },
        task="Ensure fish completion handles multiline help strings correctly",
        changed_paths=set(),
        scored_map={"tests/test_shell_completion.py": {"rank": 4}},
    )

    assert projection["tier_counts"] == {"ast_checkpoint_owner": 1}
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0
    assert projection["removed_tokens"] == 0


def test_ast_checkpoint_memory_excerpt_projection_reports_memory_signal(tmp_path: Path) -> None:
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def target_handler(request):",
            "    return request.user",
            "",
            *[f"def unrelated_{index}(): return {index}" for index in range(80)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/service.py",
            include_mode="skeleton",
            reasons=[
                "matched define: target_handler",
                "learning feedback miss boost +24",
                "content keyword match (1)",
            ],
            symbols=[],
        )
    ]

    projection = _ast_checkpoint_memory_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 700},
        selected_modes={"src/service.py": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={"src/service.py": SimpleNamespace(path="src/service.py", content=source.read_text(encoding="utf-8"))},
        summaries={
            "src/service.py": {
                "defines": ["target_handler"],
                "symbols": [{
                    "name": "target_handler",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                    "signature": "def target_handler(request)",
                    "summary": "Handle target requests",
                    "body": "def target_handler(request): return request.user",
                }],
            },
        },
        task="fix target handler",
        changed_paths=set(),
    )

    assert projection["memory_signals_tested"] is True
    assert projection["memory_signal_selected_files"] == 1
    assert projection["memory_signal_projected_files"] == 0
    assert projection["tier_counts"] == {"ast_checkpoint_owner": 1}
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0


def test_mav_span_excerpt_projection_compresses_high_density_carrier(tmp_path: Path) -> None:
    owner = tmp_path / "src" / "service.py"
    carrier = tmp_path / "gin.go"
    owner.parent.mkdir()
    owner.write_text(
        "\n".join([
            "def target_handler(request):",
            "    return request.user",
            "",
            *[f"def expected_helper_{index}(): return {index}" for index in range(70)],
        ]),
        encoding="utf-8",
    )
    carrier.write_text(
        "\n".join([
            "package gin",
            "",
            "// Engine routes HTTP requests.",
            "type Engine struct {",
            "    RouterGroup RouterGroup",
            "}",
            "",
            "func NewEngine() *Engine {",
            "    return &Engine{}",
            "}",
            "",
            *[f"func unrelated{index}() int {{ return {index} }}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/service.py",
            include_mode="skeleton",
            reasons=[
                "matched define: target_handler",
                "direct content evidence +170",
                "content keyword match (3)",
            ],
            symbols=[],
        ),
        SimpleNamespace(
            path="gin.go",
            include_mode="skeleton",
            reasons=[
                "matched define: Engine",
                "direct dependency of changed file",
                "content keyword match (1)",
            ],
            symbols=[],
        ),
    ]

    projection = _mav_span_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 500, "gin.go": 900},
        selected_modes={"src/service.py": "skeleton", "gin.go": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={
            "src/service.py": SimpleNamespace(path="src/service.py", content=owner.read_text(encoding="utf-8")),
            "gin.go": SimpleNamespace(path="gin.go", content=carrier.read_text(encoding="utf-8")),
        },
        summaries={
            "src/service.py": {
                "entrypoints": ["target_handler"],
                "defines": ["target_handler"],
            },
            "gin.go": {
                "defines": ["Engine"],
                "calls": ["NewEngine"],
                "symbols": [{
                    "name": "Engine",
                    "kind": "type",
                    "start_line": 4,
                    "end_line": 6,
                    "signature": "type Engine struct",
                    "summary": "Engine routing support",
                    "body": "type Engine struct { RouterGroup RouterGroup }",
                }],
            },
        },
        task="fix engine routing",
        changed_paths=set(),
    )

    assert projection["selected_file_set_unchanged"] is True
    assert projection["tier_counts"] == {"mav_action_owner": 1, "mav_evidence_carrier": 1}
    assert projection["projected_tier_counts"] == {"mav_evidence_carrier": 1}
    assert projection["expected_token_loss"] == 0
    assert projection["strict_noise_removed"] == projection["removed_tokens"]
    assert projection["projected_files"][0]["path"] == "gin.go"
    assert projection["projected_files"][0]["reason"] == "mav_per_token_spans"
    assert projection["projected_files"][0]["mav_density"] > 0


def test_mav_span_excerpt_projection_protects_memory_confirmed_owner(tmp_path: Path) -> None:
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def target_handler(request):",
            "    return request.user",
            "",
            *[f"def unrelated_{index}(): return {index}" for index in range(80)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/service.py",
            include_mode="skeleton",
            reasons=[
                "matched define: target_handler",
                "episodic memory similar task; overlap=target; confidence=0.70 boost +12",
                "content keyword match (1)",
            ],
            symbols=[],
        )
    ]

    projection = _mav_span_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 700},
        selected_modes={"src/service.py": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={"src/service.py": SimpleNamespace(path="src/service.py", content=source.read_text(encoding="utf-8"))},
        summaries={
            "src/service.py": {
                "defines": ["target_handler"],
                "symbols": [{
                    "name": "target_handler",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                    "signature": "def target_handler(request)",
                    "summary": "Handle target requests",
                    "body": "def target_handler(request): return request.user",
                }],
            },
        },
        task="fix target handler",
        changed_paths=set(),
    )

    assert projection["memory_signals_tested"] is True
    assert projection["memory_signal_selected_files"] == 1
    assert projection["memory_signal_projected_files"] == 0
    assert projection["tier_counts"] == {"mav_action_owner": 1}
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0


def test_neutral_mav_span_optimizer_compresses_structural_evidence_carrier(tmp_path: Path) -> None:
    owner = tmp_path / "src" / "router.py"
    carrier = tmp_path / "src" / "context.py"
    owner.parent.mkdir()
    owner.write_text(
        "\n".join([
            "def route_request(request):",
            "    return request.context",
            "",
            *[f"def expected_helper_{index}(): return {index}" for index in range(70)],
        ]),
        encoding="utf-8",
    )
    carrier.write_text(
        "\n".join([
            "class RequestContext:",
            "    def route_context(self):",
            "        return self.request",
            "",
            *[f"def unrelated_context_helper_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/router.py",
            include_mode="skeleton",
            reasons=[
                "matched define: route_request",
                "direct content evidence +170",
                "content keyword match (3)",
            ],
            symbols=[],
        ),
        SimpleNamespace(
            path="src/context.py",
            include_mode="skeleton",
            reasons=[
                "matched define: RequestContext",
                "direct dependency of changed file",
                "content keyword match (1)",
            ],
            symbols=[],
        ),
    ]
    summaries = {
        "src/router.py": {
            "entrypoints": ["route_request"],
            "defines": ["route_request"],
        },
        "src/context.py": {
            "defines": ["RequestContext"],
            "symbols": [{
                "name": "RequestContext",
                "kind": "class",
                "start_line": 1,
                "end_line": 3,
                "signature": "class RequestContext",
                "summary": "Request context routing support",
                "body": "class RequestContext: def route_context(self): return self.request",
            }],
        },
    }
    file_by_path = {
        "src/router.py": SimpleNamespace(path="src/router.py", content=owner.read_text(encoding="utf-8")),
        "src/context.py": SimpleNamespace(path="src/context.py", content=carrier.read_text(encoding="utf-8")),
    }

    old_projection = _mav_span_excerpt_projection(
        selected=selected,
        selected_tokens={"src/router.py": 500, "src/context.py": 900},
        selected_modes={"src/router.py": "skeleton", "src/context.py": "skeleton"},
        expected_set={"src/router.py"},
        file_by_path=file_by_path,
        summaries=summaries,
        task="fix request context routing",
        changed_paths=set(),
    )
    neutral_projection = _neutral_mav_span_excerpt_projection(
        selected=selected,
        selected_tokens={"src/router.py": 500, "src/context.py": 900},
        selected_modes={"src/router.py": "skeleton", "src/context.py": "skeleton"},
        expected_set={"src/router.py"},
        file_by_path=file_by_path,
        summaries=summaries,
        task="fix request context routing",
        changed_paths=set(),
    )

    assert old_projection["projected_file_count"] == 0
    assert neutral_projection["selected_file_set_unchanged"] is True
    assert neutral_projection["projected_tier_counts"] == {"neutral_mav_carrier": 1}
    assert neutral_projection["expected_token_loss"] == 0
    assert neutral_projection["strict_noise_removed"] == neutral_projection["removed_tokens"]
    assert neutral_projection["projected_files"][0]["path"] == "src/context.py"
    assert neutral_projection["projected_files"][0]["compression_score"] >= neutral_projection["projected_files"][0]["compression_threshold"]


def test_neutral_mav_span_optimizer_protects_memory_confirmed_owner(tmp_path: Path) -> None:
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def target_handler(request):",
            "    return request.user",
            "",
            *[f"def unrelated_{index}(): return {index}" for index in range(80)],
        ]),
        encoding="utf-8",
    )
    selected = [
        SimpleNamespace(
            path="src/service.py",
            include_mode="skeleton",
            reasons=[
                "matched define: target_handler",
                "episodic memory similar task; overlap=target; confidence=0.70 boost +12",
                "content keyword match (1)",
            ],
            symbols=[],
        )
    ]

    projection = _neutral_mav_span_excerpt_projection(
        selected=selected,
        selected_tokens={"src/service.py": 700},
        selected_modes={"src/service.py": "skeleton"},
        expected_set={"src/service.py"},
        file_by_path={"src/service.py": SimpleNamespace(path="src/service.py", content=source.read_text(encoding="utf-8"))},
        summaries={
            "src/service.py": {
                "defines": ["target_handler"],
                "symbols": [{
                    "name": "target_handler",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                    "signature": "def target_handler(request)",
                    "summary": "Handle target requests",
                    "body": "def target_handler(request): return request.user",
                }],
            },
        },
        task="fix target handler",
        changed_paths=set(),
    )

    assert projection["memory_signals_tested"] is True
    assert projection["memory_signal_selected_files"] == 1
    assert projection["memory_signal_projected_files"] == 0
    assert projection["tier_counts"] == {"neutral_mav_action_owner": 1}
    assert projection["projected_file_count"] == 0
    assert projection["expected_token_loss"] == 0


def test_benchmark_ablation_report_computes_tiered_oracle_capture_rate() -> None:
    records = [{
        "task": "tiered case",
        "recall": 1.0,
        "token_precision": 100 / 400,
        "expected_files": ["src/service.py"],
        "selected_tokens": {"src/service.py": 100, "src/utils.py": 300},
        "selection_diagnostics": {
            "oracle_non_expected_excerpt_ceiling": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 400,
                "projected_selected_tokens": 150,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 250,
                "expected_token_loss": 0,
                "strict_noise_removed": 250,
                "projected_file_count": 1,
                "token_precision_delta": round((100 / 150) - (100 / 400), 4),
                "projected_files": [{"path": "src/utils.py"}],
            },
            "label_free_tiered_excerpt_projection": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 400,
                "projected_selected_tokens": 250,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 150,
                "expected_token_loss": 0,
                "strict_noise_removed": 150,
                "projected_file_count": 1,
                "token_precision_delta": round((100 / 250) - (100 / 400), 4),
                "tier_counts": {"strong": 1, "weak": 1},
                "projected_tier_counts": {"weak": 1},
                "removed_tokens_by_tier": {"weak": 150},
                "strict_noise_removed_by_tier": {"weak": 150},
                "expected_loss_by_tier": {},
                "projected_files": [{"path": "src/utils.py"}],
            },
            "strong_carrier_excerpt_projection": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 400,
                "projected_selected_tokens": 300,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 100,
                "expected_token_loss": 0,
                "strict_noise_removed": 100,
                "projected_file_count": 1,
                "token_precision_delta": round((100 / 300) - (100 / 400), 4),
                "tier_counts": {"strong_carrier": 1, "strong_action_owner": 1},
                "projected_tier_counts": {"strong_carrier": 1},
                "removed_tokens_by_tier": {"strong_carrier": 100},
                "strict_noise_removed_by_tier": {"strong_carrier": 100},
                "expected_loss_by_tier": {},
                "projected_files": [{"path": "src/carrier.py"}],
            },
            "guarded_strong_carrier_excerpt_projection": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 400,
                "projected_selected_tokens": 320,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 80,
                "expected_token_loss": 0,
                "strict_noise_removed": 80,
                "projected_file_count": 1,
                "token_precision_delta": round((100 / 320) - (100 / 400), 4),
                "tier_counts": {"strong_carrier": 1, "strong_action_owner": 1},
                "projected_tier_counts": {"strong_carrier": 1},
                "removed_tokens_by_tier": {"strong_carrier": 80},
                "strict_noise_removed_by_tier": {"strong_carrier": 80},
                "expected_loss_by_tier": {},
                "projected_files": [{"path": "src/carrier.py"}],
            },
            "ast_checkpoint_memory_excerpt_projection": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 400,
                "projected_selected_tokens": 310,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 90,
                "expected_token_loss": 0,
                "strict_noise_removed": 90,
                "projected_file_count": 1,
                "memory_signal_selected_files": 2,
                "memory_signal_projected_files": 1,
                "memory_signals_tested": True,
                "token_precision_delta": round((100 / 310) - (100 / 400), 4),
                "tier_counts": {"ast_checkpoint_carrier": 1, "ast_checkpoint_owner": 1},
                "projected_tier_counts": {"ast_checkpoint_carrier": 1},
                "removed_tokens_by_tier": {"ast_checkpoint_carrier": 90},
                "strict_noise_removed_by_tier": {"ast_checkpoint_carrier": 90},
                "expected_loss_by_tier": {},
                "projected_files": [{"path": "src/carrier.py"}],
            },
            "mav_span_excerpt_projection": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 400,
                "projected_selected_tokens": 280,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 120,
                "expected_token_loss": 0,
                "strict_noise_removed": 120,
                "projected_file_count": 1,
                "memory_signal_selected_files": 0,
                "memory_signal_projected_files": 0,
                "memory_signals_tested": False,
                "token_precision_delta": round((100 / 280) - (100 / 400), 4),
                "tier_counts": {"mav_evidence_carrier": 1, "mav_action_owner": 1},
                "projected_tier_counts": {"mav_evidence_carrier": 1},
                "removed_tokens_by_tier": {"mav_evidence_carrier": 120},
                "strict_noise_removed_by_tier": {"mav_evidence_carrier": 120},
                "expected_loss_by_tier": {},
                "projected_files": [{"path": "src/carrier.py"}],
            },
            "neutral_mav_span_excerpt_projection": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 400,
                "projected_selected_tokens": 240,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 160,
                "expected_token_loss": 0,
                "strict_noise_removed": 160,
                "projected_file_count": 1,
                "memory_signal_selected_files": 0,
                "memory_signal_projected_files": 0,
                "memory_signals_tested": False,
                "token_precision_delta": round((100 / 240) - (100 / 400), 4),
                "tier_counts": {"neutral_mav_carrier": 1, "neutral_mav_action_owner": 1},
                "projected_tier_counts": {"neutral_mav_carrier": 1},
                "removed_tokens_by_tier": {"neutral_mav_carrier": 160},
                "strict_noise_removed_by_tier": {"neutral_mav_carrier": 160},
                "expected_loss_by_tier": {},
                "projected_files": [{"path": "src/carrier.py"}],
            },
        },
    }]

    report = _benchmark_ablation_report(records, min_token_precision=0.5)
    tiered = report["label_free_tiered_excerpt_projection"]
    carrier = report["strong_carrier_excerpt_projection"]
    guarded_carrier = report["guarded_strong_carrier_excerpt_projection"]
    ast_checkpoint = report["ast_checkpoint_memory_excerpt_projection"]
    mav_span = report["mav_span_excerpt_projection"]
    neutral_mav = report["neutral_mav_span_excerpt_projection"]

    assert tiered["aggregate_token_precision_delta"] == pytest.approx((100 / 250) - (100 / 400))
    assert tiered["oracle_capture_rate"] == pytest.approx(((100 / 250) - (100 / 400)) / ((100 / 150) - (100 / 400)))
    assert tiered["removed_tokens_by_tier"] == {"weak": 150}
    assert carrier["aggregate_token_precision_delta"] == pytest.approx((100 / 300) - (100 / 400))
    assert carrier["oracle_capture_rate"] == pytest.approx(((100 / 300) - (100 / 400)) / ((100 / 150) - (100 / 400)))
    assert carrier["removed_tokens_by_tier"] == {"strong_carrier": 100}
    assert guarded_carrier["aggregate_token_precision_delta"] == pytest.approx((100 / 320) - (100 / 400))
    assert guarded_carrier["removed_tokens_by_tier"] == {"strong_carrier": 80}
    assert ast_checkpoint["aggregate_token_precision_delta"] == pytest.approx((100 / 310) - (100 / 400))
    assert ast_checkpoint["removed_tokens_by_tier"] == {"ast_checkpoint_carrier": 90}
    assert ast_checkpoint["memory_signals_tested"] is True
    assert ast_checkpoint["memory_signal_selected_files"] == 2
    assert ast_checkpoint["memory_signal_projected_files"] == 1
    assert mav_span["aggregate_token_precision_delta"] == pytest.approx((100 / 280) - (100 / 400))
    assert mav_span["oracle_capture_rate"] == pytest.approx(((100 / 280) - (100 / 400)) / ((100 / 150) - (100 / 400)))
    assert mav_span["removed_tokens_by_tier"] == {"mav_evidence_carrier": 120}
    assert mav_span["memory_signals_tested"] is False
    assert neutral_mav["aggregate_token_precision_delta"] == pytest.approx((100 / 240) - (100 / 400))
    assert neutral_mav["oracle_capture_rate"] == pytest.approx(((100 / 240) - (100 / 400)) / ((100 / 150) - (100 / 400)))
    assert neutral_mav["removed_tokens_by_tier"] == {"neutral_mav_carrier": 160}
    assert neutral_mav["memory_signals_tested"] is False


def test_benchmark_ablation_report_audits_oracle_missed_signatures() -> None:
    records = [{
        "task": "fix render context",
        "recall": 1.0,
        "token_precision": 100 / 600,
        "expected_files": ["render.go"],
        "selected_tokens": {"render.go": 100, "context.go": 500},
        "selected_modes": {"render.go": "skeleton", "context.go": "skeleton"},
        "selection_diagnostics": {
            "selected_noise": [{
                "path": "context.go",
                "family": "source",
                "mode": "skeleton",
                "rank": 2,
                "score": 450.0,
                "tokens": 500,
                "reasons": [
                    "direct dependency of changed file",
                    "content keyword match (2)",
                ],
            }],
            "oracle_non_expected_excerpt_ceiling": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 600,
                "projected_selected_tokens": 180,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 420,
                "expected_token_loss": 0,
                "strict_noise_removed": 420,
                "projected_file_count": 1,
                "token_precision_delta": round((100 / 180) - (100 / 600), 4),
                "projected_files": [{
                    "path": "context.go",
                    "family": "source",
                    "mode": "skeleton",
                    "current_tokens": 500,
                    "projected_tokens": 80,
                    "removed_tokens": 420,
                    "matched_terms": ["render", "context"],
                }],
            },
            "risk_aware_tiered_excerpt_projection": {
                "selected_file_set_unchanged": True,
                "baseline_selected_tokens": 600,
                "projected_selected_tokens": 600,
                "baseline_expected_tokens": 100,
                "projected_expected_tokens": 100,
                "removed_tokens": 0,
                "expected_token_loss": 0,
                "strict_noise_removed": 0,
                "projected_file_count": 0,
                "token_precision_delta": 0.0,
                "projected_files": [],
            },
        },
    }]

    report = _benchmark_ablation_report(records, min_token_precision=0.5)
    audit = report["oracle_miss_signature_audit"]

    assert audit["missed_files"] == 1
    assert audit["missed_oracle_tokens"] == 420
    assert audit["signature_counts"]["dependency_neighbor_non_owner"] == 1
    assert audit["signature_tokens"]["evidence_carrier_not_action_owner"] == 420
    assert audit["signature_marginal_union"][0]["signature"] == "evidence_carrier_not_action_owner"
    assert audit["signature_marginal_union"][0]["removed_tokens"] == 420
    assert audit["signature_marginal_union"][0]["expected_token_loss"] == 0
    assert audit["top_missed"][0]["path"] == "context.go"


def test_benchmark_intent_profile_classifies_dependency_and_miss_families() -> None:
    profile = _benchmark_intent_profile(
        task="Upgrade Spring Boot and update Docker images",
        expected_files={"pom.xml", "src/test/java/acme/AppTests.java"},
        missed_expected=[
            {"path": "src/test/java/acme/AppTests.java"},
            {"path": "pom.xml"},
        ],
        selected_noise=[{"path": "src/main/java/acme/App.java", "family": "source"}],
    )

    assert profile["primary"] == "dependency_release"
    assert profile["expected_family_counts"] == {"config": 1, "test": 1}
    assert profile["missed_family_counts"] == {"config": 1, "test": 1}
    assert profile["selected_noise_family_counts"] == {"source": 1}
    assert "task:dependency_release:upgrade" in profile["signals"]


def test_owner_family_include_rank_and_package_diagnostics() -> None:
    expected = {
        "packages/vite/src/node/server/index.ts",
        "packages/vite/src/node/server/index.spec.ts",
        "docs/server.md",
    }
    selected = {
        "packages/vite/src/node/server/index.ts",
        "docs/server.md",
    }
    selected_modes = {
        "packages/vite/src/node/server/index.ts": "skeleton",
        "docs/server.md": "summary",
    }
    scored_map = {
        "packages/vite/src/node/server/index.ts": {"rank": 2},
        "packages/vite/src/node/server/index.spec.ts": {"rank": 9},
        "docs/server.md": {"rank": 21},
    }

    assert _owner_file_recall(selected_set=selected, expected_set=expected) == {
        "owner_files": ["packages/vite/src/node/server/index.ts"],
        "selected": 1,
        "total": 1,
        "recall": 1.0,
        "owner_family": "source",
    }
    assert _expected_family_recall(selected_set=selected, expected_set=expected) == {
        "docs": {"selected": 1.0, "expected": 1.0, "recall": 1.0},
        "source": {"selected": 1.0, "expected": 1.0, "recall": 1.0},
        "test": {"selected": 0.0, "expected": 1.0, "recall": 0.0},
    }
    assert _expected_include_mode_diagnostics(expected_set=expected, selected_modes=selected_modes) == {
        "selected_expected_count": 2,
        "expected_count": 3,
        "mode_counts": {"skeleton": 1, "summary": 1},
        "by_family": {"docs": {"summary": 1}, "source": {"skeleton": 1}},
        "source_code_block_rate": 1.0,
        "test_code_block_rate": 0.0,
        "summary_only_expected_rate": 0.5,
    }
    assert _expected_rank_distribution(expected, scored_map) == {
        "ranked_expected_count": 3,
        "unranked_expected_count": 0,
        "median": 9,
        "p90": 21,
        "min": 2,
        "max": 21,
        "buckets": {"1_3": 1, "4_8": 0, "9_20": 1, "21_plus": 1},
    }
    assert _package_boundary_diagnostics(
        selected_paths=[
            "packages/vite/src/node/server/index.ts",
            "packages/playground/src/main.ts",
            "docs/server.md",
        ],
        expected_set=expected,
    ) == {
        "expected_packages": ["docs", "packages/vite"],
        "selected_expected_package_files": 2,
        "selected_cross_package_files": 1,
        "selected_package_match_rate": 0.667,
    }


# ---------------------------------------------------------------------------
# saving_pct fields
# ---------------------------------------------------------------------------

def test_saving_pct_honest_lower_than_vs_raw() -> None:
    r = _make_result(["a.py"], [], packed_tokens=500, raw_tokens=10000, after_ignore_tokens=2000)
    assert r.saving_pct == pytest.approx(95.0)
    assert r.saving_pct_honest == pytest.approx(75.0)
    assert r.saving_pct_honest < r.saving_pct


# ---------------------------------------------------------------------------
# _scaffold_cases
# ---------------------------------------------------------------------------

def test_scaffold_cases_creates_file(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    out = _scaffold_cases(tmp_path)
    assert out.exists()
    assert "[[cases]]" in out.read_text()


def test_scaffold_cases_idempotent(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    out1 = _scaffold_cases(tmp_path)
    out1.write_text("existing content", encoding="utf-8")
    out2 = _scaffold_cases(tmp_path)
    assert out2.read_text() == "existing content"


def test_write_results_template_creates_publishable_markdown(tmp_path: Path) -> None:
    out = _write_results_template(tmp_path, date="2026-05-15")
    content = out.read_text(encoding="utf-8")

    assert out == tmp_path / "benchmarks" / "results" / "2026-05-15.md"
    assert "AgentPack Benchmark Results" in content
    assert "avg recall" in content
    assert "agentpack benchmark --compare --misses" in content


def test_public_benchmark_markdown_renders_table() -> None:
    result = _make_result(
        ["src/auth.py", "tests/test_auth.py"],
        ["src/auth.py"],
        noise_pct=40.0,
        rank_at_k=1,
        low_budget_extra_file_waste=120,
        precision_delta_if_drop_last_summary=0.08,
    )
    result.case.task = "real-api: fix auth token expiry"
    result.case.task_type = "backend-api"

    content = _public_benchmark_markdown([result], suite="real repos", version="0.3.0")

    assert "AgentPack Public Benchmark Table" in content
    assert "real-api" in content
    assert "fix auth token expiry" in content
    assert "avg recall" in content
    assert "avg last-summary waste" in content
    assert "+8.0%" in content
    assert "| Repo / suite | Task | Type | Mode | Budget | Packed tokens | Recall | Cand R@50 | Cand P@3 |" in content
    assert "60.0%" in content


def test_write_public_benchmark_table(tmp_path: Path) -> None:
    result = _make_result(["a.py"], ["a.py"], noise_pct=0.0)

    out = _write_public_benchmark_table(tmp_path, [result], suite="real repos", date="2026-05-15")

    assert out == tmp_path / "benchmarks" / "results" / "2026-05-15-public.md"
    assert "real repos" in out.read_text(encoding="utf-8")


def test_benchmark_release_gate_maps_to_public_repo_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "release-repos.lock.toml").write_text(
        "[gate]\nmin_recall=0.65\nmin_token_precision=0.50\nmin_scored_cases=1\n\n[[repos]]\nname='empty'\nurl='x'\n",
        encoding="utf-8",
    )
    mocked = _make_result(["a.py"], ["a.py"], noise_pct=0.0)
    mocked.case.task = "repo: fix thing"
    mocked.case.task_type = "python"
    with patch("agentpack.commands.benchmark._load_public_repo_specs", return_value=[SimpleNamespace(name="repo", cases=[object()])]), \
         patch("agentpack.commands.benchmark._run_public_repo_suite", return_value=[mocked]) as run_suite, \
         patch("agentpack.commands.benchmark._write_public_benchmark_table") as write_table:
        result = CliRunner().invoke(app, ["benchmark", "--release-gate", "--no-public-table"])

    assert result.exit_code == 0, result.output
    assert "Release gate" in result.output
    assert run_suite.called
    assert not write_table.called


def test_load_release_gate_config_ignores_custom_manifest_without_gate(tmp_path: Path) -> None:
    manifest = tmp_path / "public-repos.toml"
    manifest.write_text("[[repos]]\nname='custom'\nurl='x'\n", encoding="utf-8")

    assert _load_release_gate_config(manifest) == ReleaseGateConfig()


def test_load_release_gate_config_reads_committed_floors(tmp_path: Path) -> None:
    manifest = tmp_path / "release-repos.lock.toml"
    manifest.write_text(
        "[gate]\nmin_recall=0.671\nmin_token_precision=0.506\nmin_scored_cases=107\n",
        encoding="utf-8",
    )

    assert _load_release_gate_config(manifest) == ReleaseGateConfig(
        min_recall=0.671,
        min_token_precision=0.506,
        min_scored_cases=107,
    )


def test_release_gate_rejects_lock_with_too_few_scored_cases(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "release-repos.lock.toml").write_text(
        "[gate]\nmin_scored_cases=2\n\n[[repos]]\nname='empty'\nurl='x'\n",
        encoding="utf-8",
    )
    mocked = _make_result(["a.py"], ["a.py"], noise_pct=0.0)

    with patch("agentpack.commands.benchmark._load_public_repo_specs", return_value=[SimpleNamespace(name="repo", cases=[object()])]), \
         patch("agentpack.commands.benchmark._run_public_repo_suite", return_value=[mocked]):
        result = CliRunner().invoke(app, ["benchmark", "--release-gate", "--no-public-table"])

    assert result.exit_code == 2
    assert "Release gate scored too few cases: 1 < 2" in result.output


def test_benchmark_public_suite_reproduce_maps_to_public_repo_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "public-repos.toml").write_text(
        "[[repos]]\nname='empty'\nurl='x'\nsample_history=1\n",
        encoding="utf-8",
    )
    mocked = _make_result(["a.py"], ["a.py"], noise_pct=0.0)
    mocked.case.task = "repo: fix thing"
    with patch(
        "agentpack.commands.benchmark._load_public_repo_specs",
        return_value=[SimpleNamespace(name="repo", cases=[], sample_history=1)],
    ), patch("agentpack.commands.benchmark._run_public_repo_suite", return_value=[mocked]) as run_suite, patch(
        "agentpack.commands.benchmark._write_public_benchmark_table"
    ) as write_table:
        result = CliRunner().invoke(app, ["benchmark", "--public-suite", "--reproduce", "v0.3.20"])

    assert result.exit_code == 0, result.output
    assert "Public suite" in result.output
    assert run_suite.called
    assert write_table.called


def test_filter_public_repo_specs_by_repo_and_task_type() -> None:
    specs = [
        PublicRepoSpec(
            name="gin",
            url="https://example.test/gin.git",
            sample_history=20,
            task_type="go-service",
            cases=[
                PublicRepoCase(
                    commit="abc",
                    task="fix go",
                    expected_files=["a.go"],
                    task_type="go-service",
                )
            ],
        ),
        PublicRepoSpec(
            name="vite",
            url="https://example.test/vite.git",
            sample_history=20,
            task_type="typescript",
            cases=[
                PublicRepoCase(
                    commit="def",
                    task="fix ts",
                    expected_files=["a.ts"],
                    task_type="typescript",
                )
            ],
        ),
    ]

    filtered = _filter_public_repo_specs(
        specs,
        repo_filter="gin,vite",
        task_type_filter="go-service",
    )

    assert [spec.name for spec in filtered] == ["gin"]
    assert filtered[0].sample_history == 20
    assert [case.task for case in filtered[0].cases] == ["fix go"]


def test_write_results_jsonl_uses_benchmark_record_shape(tmp_path: Path) -> None:
    result = _make_result(["a.py"], ["a.py"], noise_pct=0.0)
    result.case.task_type = "python"
    out = _write_results_jsonl(tmp_path / "bench" / "results.jsonl", [result])

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["task"] == result.case.task
    assert rows[0]["task_type"] == "python"
    assert rows[0]["expected_files"] == ["a.py"]
    assert rows[0]["selected_paths"] == ["a.py"]
    assert rows[0]["recall"] == 1.0
    assert rows[0]["token_precision"] == 1.0
    assert rows[0]["misses"] == []


def test_benchmark_ablation_report_quantifies_prune_ceiling() -> None:
    records = [
        {
            "task": "noisy case",
            "recall": 1.0,
            "token_precision": 0.25,
            "expected_files": ["a.py"],
            "selected_tokens": {"a.py": 100, "noise.py": 300},
            "failure_type_counts": {"NOISE_SELECTED_ABOVE_EXPECTED": 1},
            "selected_family_waste_tokens": {"source": 300},
            "selection_diagnostics": {
                "label_audit": {
                    "selected_noise_tokens": 300,
                    "plausibly_useful_tokens": 50,
                    "audited_noise_tokens": 250,
                }
            },
        },
        {
            "task": "all noise",
            "recall": 0.0,
            "token_precision": 0.0,
            "expected_files": ["b.py"],
            "selected_tokens": {"noise2.py": 200},
            "failure_type_counts": {"EXPECTED_SKIPPED": 1},
            "selected_family_waste_tokens": {"test": 200},
            "selection_diagnostics": {
                "label_audit": {
                    "selected_noise_tokens": 200,
                    "plausibly_useful_tokens": 0,
                    "audited_noise_tokens": 200,
                }
            },
        },
    ]

    report = _benchmark_ablation_report(records, min_token_precision=0.5)

    assert report["scored_cases"] == 2
    assert report["selected_tokens"] == 600
    assert report["expected_selected_tokens"] == 100
    assert report["strict_noise_tokens"] == 500
    assert report["aggregate_noise_removal_to_target"] == 400
    assert report["case_noise_removal_to_target"] == 400
    assert report["zero_expected_selected_cases"] == 1
    assert report["zero_expected_selected_tokens"] == 200
    assert report["audited_true_noise_tokens"] == 450
    assert report["projected_avg_tp_remove_true_noise"] == pytest.approx(5 / 6)
    assert report["projected_aggregate_tp_remove_true_noise"] == pytest.approx(2 / 3)
    assert report["failure_type_counts"] == {
        "NOISE_SELECTED_ABOVE_EXPECTED": 1,
        "EXPECTED_SKIPPED": 1,
    }
    assert report["selected_waste_family_tokens"] == {"source": 300, "test": 200}


def test_benchmark_ablation_report_scores_heuristic_prune_false_positives() -> None:
    records = [{
        "task": "release metadata noise",
        "recall": 1.0,
        "token_precision": 100 / 460,
        "expected_files": ["a.py"],
        "selected_tokens": {
            "a.py": 100,
            "pyproject.toml": 80,
            "tests/test_options.py": 50,
            "src/pkg/__init__.py": 30,
            "src/pkg/core.py": 200,
        },
        "selection_diagnostics": {
            "selected_noise": [
                {
                    "path": "pyproject.toml",
                    "family": "config",
                    "mode": "skeleton",
                    "rank": 12,
                    "tokens": 80,
                    "reasons": ["config file", "release/version metadata"],
                },
                {
                    "path": "tests/test_options.py",
                    "family": "test",
                    "mode": "summary",
                    "rank": 5,
                    "tokens": 50,
                    "reasons": ["matched define: ConfigParamType.convert"],
                },
                {
                    "path": "src/pkg/__init__.py",
                    "family": "source",
                    "mode": "skeleton",
                    "rank": 2,
                    "tokens": 30,
                    "reasons": ["matched call: importlib.metadata.version", "release/version metadata"],
                },
                {
                    "path": "src/pkg/core.py",
                    "family": "source",
                    "mode": "skeleton",
                    "rank": 3,
                    "tokens": 200,
                    "reasons": ["direct content evidence +270", "matched define: core"],
                },
            ],
            "selected_not_expected_but_plausibly_useful": [
                {"path": "src/pkg/__init__.py", "tokens": 30},
                {"path": "src/pkg/core.py", "tokens": 200},
            ],
            "label_audit": {
                "selected_noise_tokens": 360,
                "plausibly_useful_tokens": 230,
                "audited_noise_tokens": 130,
            },
        },
    }]

    report = _benchmark_ablation_report(records, min_token_precision=0.5)

    heuristic = report["heuristic_prune"]
    assert heuristic["pruned_tokens"] == 160
    assert heuristic["pruned_true_noise_tokens"] == 130
    assert heuristic["pruned_plausibly_useful_tokens"] == 30
    assert heuristic["true_noise_purity"] == pytest.approx(130 / 160)
    assert heuristic["plausibly_useful_prune_fraction"] == pytest.approx(30 / 230)
    assert heuristic["projected_aggregate_token_precision"] == pytest.approx(1 / 3)
    assert heuristic["prune_reason_counts"] == {
        "release_metadata": 1,
        "unsupported_release_metadata": 2,
        "low_rank_config": 1,
        "weak_test_summary": 1,
    }


def test_benchmark_mav_score_prefers_direct_source_over_weak_config_noise() -> None:
    direct_source = {
        "path": "src/server/open_browser.py",
        "family": "source",
        "rank": 4,
        "score": 420.0,
        "tokens": 90,
        "reasons": [
            "content keyword match (3)",
            "matched call: open_browser",
            "keyword phrase match: create react app",
            "direct content evidence +170",
        ],
    }
    weak_config = {
        "path": ".github/workflows/tests.yaml",
        "family": "config",
        "rank": 3,
        "score": 250.0,
        "tokens": 85,
        "reasons": ["content keyword match (1)", "config file", "recently modified"],
    }

    assert _benchmark_mav_score(direct_source) > _benchmark_mav_score(weak_config) + 80


def test_benchmark_mav_score_protects_structural_support() -> None:
    structural_source = {
        "path": "src/click/__init__.py",
        "family": "source",
        "rank": 9,
        "score": 180.0,
        "tokens": 70,
        "reasons": [
            "content keyword match (5)",
            "recall neighbor of src/click/decorators.py",
            "second-pass recall neighbor of src/click/types.py",
        ],
    }
    weak_config = {
        "path": ".github/workflows/tests.yaml",
        "family": "config",
        "rank": 3,
        "score": 250.0,
        "tokens": 85,
        "reasons": ["content keyword match (2)", "config file", "recently modified", "high churn (38 commits)"],
    }

    assert _benchmark_mav_score(structural_source) > 20
    assert _benchmark_mav_score(weak_config) < 20


def test_benchmark_ablation_report_includes_mav_tradeoffs() -> None:
    records = [{
        "task": "replace weak workflow with owner",
        "recall": 0.5,
        "token_precision": 100 / 290,
        "expected_files": ["a.py", "b.py"],
        "selected_tokens": {
            "a.py": 100,
            ".github/workflows/tests.yaml": 90,
            "src/plausible.py": 100,
        },
        "misses": [{
            "path": "b.py",
            "rank": 2,
            "score": 500.0,
            "family": "source",
            "failure_type": "EXPECTED_SKIPPED",
            "status": "compressed context cap reached",
            "reasons": ["matched call: build_owner", "content keyword match (3)", "direct content evidence +170"],
            "cap_block_diagnostic": {
                "candidate_has_strong_evidence": True,
                "candidate_tokens": 80,
                "block_reason": "no replaceable selected compressed noise",
            },
        }],
        "selection_diagnostics": {
            "selected_noise": [
                {
                    "path": ".github/workflows/tests.yaml",
                    "family": "config",
                    "mode": "skeleton",
                    "rank": 3,
                    "score": 250.0,
                    "tokens": 90,
                    "reasons": ["content keyword match (1)", "config file", "recently modified"],
                },
                {
                    "path": "src/plausible.py",
                    "family": "source",
                    "mode": "skeleton",
                    "rank": 4,
                    "score": 220.0,
                    "tokens": 100,
                    "reasons": [
                        "content keyword match (3)",
                        "recall neighbor of src/owner.py",
                        "second-pass recall neighbor of src/service.py",
                    ],
                },
            ],
            "selected_not_expected_but_plausibly_useful": [
                {"path": "src/plausible.py", "tokens": 100},
            ],
            "label_audit": {
                "selected_noise_tokens": 190,
                "plausibly_useful_tokens": 100,
                "audited_noise_tokens": 90,
            },
        },
    }]

    report = _benchmark_ablation_report(records, min_token_precision=0.5)

    mav = report["mav_ablation"]
    assert mav["guarded_prune"]["pruned_true_noise_tokens"] == 90
    assert mav["guarded_prune"]["pruned_plausibly_useful_tokens"] == 0
    assert mav["replacement"]["accepted_replacements"] == 1
    assert mav["replacement"]["added_expected_tokens"] == 80


def test_benchmark_ablation_report_includes_activation_gate_tradeoffs() -> None:
    records = [
        {
            "task": "test(render): add regression tests",
            "recall": 0.5,
            "token_precision": 100 / 400,
            "expected_files": ["render/expected.go", "render/expected_test.go"],
            "selected_tokens": {
                "render/expected.go": 100,
                "context.go": 120,
                "pyproject.toml": 80,
                "render/plausible.go": 100,
            },
            "misses": [{
                "path": "render/expected_test.go",
                "rank": 2,
                "family": "test",
                "status": "compressed context cap reached",
                "cap_block_diagnostic": {"candidate_tokens": 200},
            }],
            "selection_diagnostics": {
                "intent_profile": {"primary": "test_focus"},
                "selected_noise": [
                    {
                        "path": "context.go",
                        "family": "source",
                        "mode": "skeleton",
                        "rank": 12,
                        "score": 280.0,
                        "tokens": 120,
                        "reasons": ["symbol keyword match", "content keyword match (2)"],
                    },
                    {
                        "path": "pyproject.toml",
                        "family": "config",
                        "mode": "skeleton",
                        "rank": 3,
                        "score": 300.0,
                        "tokens": 80,
                        "reasons": ["content keyword match (1)", "config file"],
                    },
                    {
                        "path": "render/plausible.go",
                        "family": "source",
                        "mode": "skeleton",
                        "rank": 4,
                        "score": 350.0,
                        "tokens": 100,
                        "reasons": ["direct dependency of changed file", "matched define: Render"],
                    },
                ],
                "selected_not_expected_but_plausibly_useful": [
                    {"path": "render/plausible.go", "tokens": 100},
                ],
                "label_audit": {
                    "selected_noise_tokens": 300,
                    "plausibly_useful_tokens": 100,
                    "audited_noise_tokens": 200,
                },
            },
        },
        {
            "task": "Fix broken fish completion",
            "recall": 1.0,
            "token_precision": 100 / 340,
            "expected_files": ["src/click/shell_completion.py"],
            "selected_tokens": {
                "src/click/shell_completion.py": 100,
                "tests/test_shell_completion.py": 150,
                "tests/test_useful.py": 90,
            },
            "misses": [{
                "path": "src/click/core.py",
                "rank": 8,
                "family": "source",
                "status": "summary score below floor",
            }],
            "selection_diagnostics": {
                "intent_profile": {"primary": "source_behavior"},
                "selected_noise": [
                    {
                        "path": "tests/test_shell_completion.py",
                        "family": "test",
                        "mode": "skeleton",
                        "rank": 2,
                        "score": 380.0,
                        "tokens": 150,
                        "reasons": ["symbol keyword match", "matched define: test_completion_item_data"],
                    },
                    {
                        "path": "tests/test_useful.py",
                        "family": "test",
                        "mode": "skeleton",
                        "rank": 3,
                        "score": 360.0,
                        "tokens": 90,
                        "reasons": [
                            "symbol keyword match",
                            "matched define: test_completion",
                            "test for high-scoring src/click/shell_completion.py",
                        ],
                    },
                ],
                "selected_not_expected_but_plausibly_useful": [
                    {"path": "tests/test_useful.py", "tokens": 90},
                ],
                "label_audit": {
                    "selected_noise_tokens": 240,
                    "plausibly_useful_tokens": 90,
                    "audited_noise_tokens": 150,
                },
            },
        },
    ]

    report = _benchmark_ablation_report(records, min_token_precision=0.5)

    activation = report["activation_gate"]
    assert activation["policy"] == "two_stage_activation_v1_offline"
    profile = activation["activation_only"]
    assert profile["pruned_tokens"] == 350
    assert profile["pruned_true_noise_tokens"] == 350
    assert profile["pruned_plausibly_useful_tokens"] == 0
    assert profile["true_noise_purity"] == pytest.approx(1.0)
    assert profile["projected_aggregate_token_precision"] == pytest.approx(200 / 390)
    assert profile["reason_counts"] == {
        "test_task_late_source_without_costimulus": 1,
        "non_action_family_without_costimulus": 1,
        "non_test_task_test_symbol_define_without_costimulus": 1,
    }
    combined = activation["activation_plus_guarded_mav"]
    assert combined["pruned_tokens"] == 350
    assert combined["pruned_plausibly_useful_tokens"] == 0
    atom_80 = activation["atom_ceiling"]["profiles"][0]
    assert atom_80["atom_tokens"] == 80
    assert atom_80["atoms"] == 2
    assert atom_80["added_expected_tokens"] == 160
    assert atom_80["projected_aggregate_token_precision"] == pytest.approx(360 / 550)


def test_benchmark_ablation_report_audits_ranked_expected_skips() -> None:
    records = [{
        "task": "fix ranked miss",
        "recall": 0.5,
        "token_precision": 0.25,
        "expected_files": ["src/a.py", "tests/test_a.py"],
        "selected_tokens": {"src/a.py": 100, "src/noise.py": 300},
        "misses": [
            {
                "path": "tests/test_a.py",
                "rank": 3,
                "score": 410.0,
                "family": "test",
                "failure_type": "EXPECTED_SKIPPED",
                "status": "compressed context cap reached; no live changed-file signal",
                "reasons": ["matched call: do_work"],
                "would_select_with_one_more_slot": True,
                "cap_block_diagnostic": {
                    "candidate_has_strong_evidence": True,
                    "block_reason": "no replaceable selected compressed noise",
                },
                "selected_noise_file_that_beat_expected": {
                    "path": "src/noise.py",
                    "family": "source",
                },
            },
            {
                "path": "src/low.py",
                "rank": 15,
                "score": 22.0,
                "family": "source",
                "failure_type": "EXPECTED_RANKED_LOW",
                "status": "summary score below floor; no live changed-file signal",
                "reasons": ["content keyword match (1)"],
            },
            {
                "path": "src/missing.py",
                "rank": None,
                "family": "source",
                "failure_type": "EXPECTED_SKIPPED",
                "status": "not ranked",
            },
        ],
    }]

    report = _benchmark_ablation_report(records, min_token_precision=0.5)

    audit = report["ranked_skip_audit"]
    assert audit["missed_expected_files"] == 3
    assert audit["ranked_missed_expected_files"] == 2
    assert audit["high_ranked_missed_expected_files"] == 1
    assert audit["high_ranked_strong_evidence_files"] == 1
    assert audit["would_select_with_one_more_slot"] == 1
    assert audit["status_counts"] == {
        "compressed_context_cap": 1,
        "summary_score_floor": 1,
        "not_ranked": 1,
    }
    assert audit["failure_type_counts"] == {
        "EXPECTED_SKIPPED": 2,
        "EXPECTED_RANKED_LOW": 1,
    }
    assert audit["blocker_family_counts"] == {"source": 1}
    assert audit["cap_block_reason_counts"] == {"no replaceable selected compressed noise": 1}
    assert audit["top_ranked_misses"][0]["path"] == "tests/test_a.py"


def test_benchmark_ablation_report_audits_zero_expected_selected_cases() -> None:
    records = [{
        "task": "zero expected selected",
        "recall": 0.0,
        "token_precision": 0.0,
        "expected_files": ["src/owner.py", "tests/test_owner.py"],
        "selected_tokens": {
            ".github/workflows/tests.yaml": 90,
            "src/plausible.py": 110,
        },
        "misses": [
            {
                "path": "src/owner.py",
                "rank": 3,
                "score": 410.0,
                "family": "source",
                "failure_type": "EXPECTED_SKIPPED",
                "status": "compressed context cap reached; no live changed-file signal",
                "reasons": ["matched call: build_owner"],
                "cap_block_diagnostic": {
                    "candidate_has_strong_evidence": True,
                    "block_reason": "no replaceable selected compressed noise",
                },
            },
            {
                "path": "tests/test_owner.py",
                "rank": 18,
                "score": 55.0,
                "family": "test",
                "failure_type": "EXPECTED_RANKED_LOW",
                "status": "summary score below floor; no live changed-file signal",
                "reasons": ["content keyword match (1)"],
            },
        ],
        "selection_diagnostics": {
            "selected_noise": [
                {
                    "path": ".github/workflows/tests.yaml",
                    "family": "config",
                    "mode": "skeleton",
                    "rank": 4,
                    "score": 250.0,
                    "tokens": 90,
                    "reasons": ["content keyword match (2)", "config file", "recently modified"],
                },
                {
                    "path": "src/plausible.py",
                    "family": "source",
                    "mode": "skeleton",
                    "rank": 5,
                    "score": 220.0,
                    "tokens": 110,
                    "reasons": ["matched call: helper", "direct content evidence +120"],
                },
            ],
            "selected_not_expected_but_plausibly_useful": [
                {"path": "src/plausible.py", "tokens": 110},
            ],
            "label_audit": {
                "selected_noise_tokens": 200,
                "plausibly_useful_tokens": 110,
                "audited_noise_tokens": 90,
            },
        },
    }]

    report = _benchmark_ablation_report(records, min_token_precision=0.5)

    audit = report["zero_expected_audit"]
    assert audit["cases"] == 1
    assert audit["selected_tokens"] == 200
    assert audit["audited_true_noise_tokens"] == 90
    assert audit["plausibly_useful_tokens"] == 110
    assert audit["high_ranked_missed_expected_files"] == 1
    assert audit["high_ranked_strong_evidence_files"] == 1
    assert audit["miss_status_counts"] == {
        "compressed_context_cap": 1,
        "summary_score_floor": 1,
    }
    assert audit["selected_family_tokens"] == {"source": 110, "config": 90}
    assert audit["cap_block_reason_counts"] == {"no replaceable selected compressed noise": 1}
    assert audit["guarded_mav_reason_counts"] == {
        "config_family": 1,
        "content_only": 1,
        "recent_only": 1,
    }
    assert audit["top_cases"][0]["top_miss_path"] == "src/owner.py"
    assert audit["top_cases"][0]["selected_family_mix"] == "source=110t, config=90t"


def test_benchmark_cli_ablation_jsonl_reports_oracle(tmp_path: Path) -> None:
    rows = [
        {
            "task": "noisy case",
            "recall": 1.0,
            "token_precision": 0.25,
            "expected_files": ["a.py"],
            "selected_tokens": {"a.py": 100, "noise.py": 300},
            "misses": [{
                "path": "b.py",
                "rank": 2,
                "failure_type": "EXPECTED_SKIPPED",
                "status": "compressed context cap reached",
            }],
            "selection_diagnostics": {
                "label_audit": {"audited_noise_tokens": 250},
                "oracle_non_expected_excerpt_ceiling": {
                    "selected_file_set_unchanged": True,
                    "baseline_selected_tokens": 400,
                    "projected_selected_tokens": 150,
                    "baseline_expected_tokens": 100,
                    "projected_expected_tokens": 100,
                    "removed_tokens": 250,
                    "expected_token_loss": 0,
                    "strict_noise_removed": 250,
                    "projected_file_count": 1,
                    "token_precision_delta": 0.4167,
                    "projected_files": [{"path": "noise.py"}],
                },
                "label_free_tiered_excerpt_projection": {
                    "selected_file_set_unchanged": True,
                    "baseline_selected_tokens": 400,
                    "projected_selected_tokens": 250,
                    "baseline_expected_tokens": 100,
                    "projected_expected_tokens": 100,
                    "removed_tokens": 150,
                    "expected_token_loss": 0,
                    "strict_noise_removed": 150,
                    "projected_file_count": 1,
                    "token_precision_delta": 0.15,
                    "tier_counts": {"strong": 1, "weak": 1},
                    "projected_tier_counts": {"weak": 1},
                    "removed_tokens_by_tier": {"weak": 150},
                    "strict_noise_removed_by_tier": {"weak": 150},
                    "projected_files": [{"path": "noise.py"}],
                },
            },
        }
    ]
    jsonl = tmp_path / "results.jsonl"
    jsonl.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["benchmark", "--ablation-jsonl", str(jsonl)])

    assert result.exit_code == 0
    assert "Benchmark Ablation Oracle" in result.output
    assert "aggregate noise removal" in result.output
    assert "audited true noise" in result.output
    assert "heuristic prune policy" in result.output
    assert "MAV Offline Ablation" in result.output
    assert "Activation Gate Ablation" in result.output
    assert "Label-Free Tiered Excerpt Projection" in result.output
    assert "Oracle Non-Expected Excerpt Ceiling" in result.output
    assert "Ranked Expected Skip Audit" in result.output


# ---------------------------------------------------------------------------
# _load_cases
# ---------------------------------------------------------------------------

def test_load_cases_parses_toml(tmp_path: Path) -> None:
    f = tmp_path / "bench.toml"
    f.write_text(
        '[[cases]]\ntask = "fix bug"\nmode = "minimal"\nexpected_files = ["a.py"]\n',
        encoding="utf-8",
    )
    cases = _load_cases(f)
    assert len(cases) == 1
    assert cases[0].task == "fix bug"
    assert cases[0].mode == "balanced"
    assert cases[0].expected_files == ["a.py"]
    assert cases[0].task_type == "general"


def test_load_cases_parses_task_type(tmp_path: Path) -> None:
    f = tmp_path / "bench.toml"
    f.write_text(
        '[[cases]]\ntask = "fix bug"\ntask_type = "backend-api"\nexpected_files = ["a.py"]\n',
        encoding="utf-8",
    )
    cases = _load_cases(f)
    assert cases[0].task_type == "backend-api"


def test_load_cases_parses_workspace(tmp_path: Path) -> None:
    f = tmp_path / "bench.toml"
    f.write_text(
        '[[cases]]\ntask = "fix bug"\nworkspace = "apps/web"\nexpected_files = ["apps/web/a.ts"]\n',
        encoding="utf-8",
    )
    cases = _load_cases(f)
    assert cases[0].workspace == "apps/web"


def test_load_cases_parses_expected_and_avoid_skills(tmp_path: Path) -> None:
    f = tmp_path / "bench.toml"
    f.write_text(
        '[[cases]]\n'
        'task = "fix auth bug"\n'
        'expected_skills = ["pytest-debugging", "auth-review"]\n'
        'avoid_skills = ["frontend-review"]\n',
        encoding="utf-8",
    )

    cases = _load_cases(f)

    assert cases[0].expected_skills == ["pytest-debugging", "auth-review"]
    assert cases[0].avoid_skills == ["frontend-review"]


def test_skill_metrics_scores_recall_precision_mrr_and_noise() -> None:
    recall, precision, mrr, noise = _skill_metrics(
        ["pytest-debugging", "frontend-review", "auth-review"],
        expected_skills=["auth-review", "pytest-debugging"],
        avoid_skills=["frontend-review"],
    )

    assert recall == 1.0
    assert precision == pytest.approx(2 / 3)
    assert mrr == 1.0
    assert noise == pytest.approx(1 / 3)


def test_persist_result_records_skill_keyword_quality_metrics(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    result = CaseResult(
        case=BenchmarkCase(
            task="review PR for SQL injection and code quality",
            expected_skills=["code-reviewer"],
            avoid_skills=["generic-writing"],
        ),
        packed_tokens=1000,
        raw_tokens=10000,
        after_ignore_tokens=8000,
        saving_pct=90.0,
        saving_pct_honest=87.5,
        selected_paths=[],
        selected_tokens={},
        changed_covered=0,
        changed_total=0,
        total_s=0.1,
        phase_times={},
        selected_skills=["code-reviewer", "generic-writing"],
        skill_recall_at_3=1.0,
        skill_precision_at_3=0.5,
        skill_mrr=1.0,
        skill_noise_rate=0.5,
        skill_token_cost=245,
    )

    _persist_result(tmp_path, result)

    record = json.loads((tmp_path / ".agentpack" / "benchmark_results.jsonl").read_text(encoding="utf-8"))
    assert record["selected_skills"] == ["code-reviewer", "generic-writing"]
    assert record["skill_recall_at_3"] == 1.0
    assert record["skill_precision_at_3"] == 0.5
    assert record["skill_mrr"] == 1.0
    assert record["skill_noise_rate"] == 0.5
    assert record["skill_token_cost"] == 245


def test_load_cases_defaults_mode(tmp_path: Path) -> None:
    f = tmp_path / "bench.toml"
    f.write_text('[[cases]]\ntask = "add feature"\n', encoding="utf-8")
    cases = _load_cases(f)
    assert cases[0].mode == "balanced"


def test_load_cases_empty_returns_empty(tmp_path: Path) -> None:
    f = tmp_path / "bench.toml"
    f.write_text("", encoding="utf-8")
    assert _load_cases(f) == []


def test_sample_fixture_cases_use_recall_friendly_owner_terms() -> None:
    cases = {
        item.case.task: item.case.expected_files
        for item in _sample_fixture_cases(Path("tests/fixtures"))
    }

    assert cases["fix Python slugify parsing edge case"] == ["src/py/utils.py"]
    assert cases["fix Dockerfile build for Go server main deployment"] == [
        "Dockerfile",
        "cmd/server/main.go",
    ]


def test_load_public_repo_specs_parses_manifest(tmp_path: Path) -> None:
    f = tmp_path / "public.toml"
    f.write_text(
        '[[repos]]\n'
        'name = "click"\n'
        'url = "https://github.com/pallets/click.git"\n'
        'ref = "main"\n\n'
        'sample_history = 12\n'
        'task_type = "python-cli"\n'
        'mode = "balanced"\n'
        'budget = 2000\n'
        'include_globs = ["src/**/*.py", "tests/**/*.py"]\n'
        'exclude_globs = ["docs/**"]\n'
        'max_changed_files = 6\n\n'
        '[[repos.cases]]\n'
        'commit = "abc123"\n'
        'task = "fix hidden prompt input"\n'
        'task_type = "python-cli"\n'
        'expected_files = ["src/click/termui.py", "tests/test_termui.py"]\n',
        encoding="utf-8",
    )

    specs = _load_public_repo_specs(f)

    assert len(specs) == 1
    assert specs[0].name == "click"
    assert specs[0].url.endswith("/click.git")
    assert specs[0].sample_history == 12
    assert specs[0].include_globs == ["src/**/*.py", "tests/**/*.py"]
    assert specs[0].exclude_globs == ["docs/**"]
    assert specs[0].max_changed_files == 6
    assert specs[0].cases[0].commit == "abc123"
    assert specs[0].cases[0].expected_files == ["src/click/termui.py", "tests/test_termui.py"]


def test_load_public_repo_specs_reads_ownership_labels(tmp_path: Path) -> None:
    manifest = tmp_path / "release-repos.lock.toml"
    manifest.write_text(
        '[[repos]]\nname = "demo"\nurl = "https://example.com/demo.git"\n'
        '[[repos.cases]]\ncommit = "abc"\ntask = "fix auth"\n'
        'expected_files = ["src/auth.py", "tests/test_auth.py", "CHANGELOG.md"]\n'
        'action_owner_files = ["src/auth.py"]\n'
        'required_support_files = ["tests/test_auth.py"]\n'
        'incidental_changed_files = ["CHANGELOG.md"]\n'
        'optional_context_files = ["src/tokens.py"]\n',
        encoding="utf-8",
    )

    case = _load_public_repo_specs(manifest)[0].cases[0]

    assert case.action_owner_files == ["src/auth.py"]
    assert case.required_support_files == ["tests/test_auth.py"]
    assert case.incidental_changed_files == ["CHANGELOG.md"]
    assert case.optional_context_files == ["src/tokens.py"]


def test_load_public_repo_specs_rejects_non_partitioned_ownership_labels(tmp_path: Path) -> None:
    manifest = tmp_path / "release-repos.lock.toml"
    manifest.write_text(
        '[[repos]]\nname = "demo"\nurl = "https://example.com/demo.git"\n'
        '[[repos.cases]]\ncommit = "abc"\ntask = "fix auth"\n'
        'expected_files = ["src/auth.py", "tests/test_auth.py"]\n'
        'action_owner_files = ["src/auth.py"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="partition expected_files"):
        _load_public_repo_specs(manifest)


def test_load_public_repo_specs_keeps_custom_manifest_without_ownership_labels(tmp_path: Path) -> None:
    manifest = tmp_path / "public-repos.toml"
    manifest.write_text(
        '[[repos]]\nname = "demo"\nurl = "https://example.com/demo.git"\n'
        '[[repos.cases]]\ncommit = "abc"\ntask = "fix auth"\n'
        'expected_files = ["src/auth.py"]\n',
        encoding="utf-8",
    )

    case = _load_public_repo_specs(manifest)[0].cases[0]

    assert case.action_owner_files == []
    assert case.required_support_files == []
    assert case.incidental_changed_files == []
    assert case.optional_context_files == []


def test_ownership_metrics_are_independent_from_legacy_expected_files() -> None:
    case = BenchmarkCase(
        task="fix auth",
        expected_files=["src/auth.py", "tests/test_auth.py", "CHANGELOG.md"],
        action_owner_files=["src/auth.py"],
        required_support_files=["tests/test_auth.py"],
        incidental_changed_files=["CHANGELOG.md"],
        optional_context_files=["src/tokens.py"],
    )

    metrics = _ownership_metrics(
        case,
        {"src/auth.py", "src/tokens.py", "CHANGELOG.md", "src/noise.py"},
    )

    assert metrics == {
        "owner_recall": 1.0,
        "support_recall": 0.0,
        "useful_context_precision": 0.5,
        "selected_incidental_files": ["CHANGELOG.md"],
        "incidental_selection_rate": 1.0,
    }


def test_selection_v2_evidence_diagnostic_uses_labels_only_for_scoring(tmp_path: Path) -> None:
    file_info = FileInfo(
        path="src/authenticate.py",
        abs_path=tmp_path / "src/auth.py",
        size_bytes=100,
        estimated_tokens=25,
    )

    diagnostic = _selection_v2_evidence_diagnostics(
        ranked_scored=[(
            file_info,
            120.0,
            ["matched define: authenticate", "filename keyword match"],
        )],
        task="fix authenticate validation",
        summaries={"src/authenticate.py": {"defines": ["authenticate"]}},
        keyword_plan=build_keyword_plan("fix authenticate validation"),
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
        action_owner_files={"src/authenticate.py"},
        required_support_files=set(),
        incidental_changed_files=set(),
        optional_context_files=set(),
    )

    assert diagnostic["owner_label_recall"] == 1.0
    assert diagnostic["protected_file_misclassifications"] == 0
    assert diagnostic["candidates"][0]["label"] == "action_owner"
    assert diagnostic["candidates"][0]["owner_strength"] == 3


def test_owner_evidence_report_scores_repositories_and_drift() -> None:
    records = [
        {
            "repository": "vite",
            "task": "fix optimizer",
            "selected_paths": ["src/optimizer.ts"],
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "token_precision": 1.0,
            "packed_tokens": 20,
            "selection_diagnostics": {"selection_v2": {"evidence": {
                "protected_file_misclassifications": 0,
                "candidates": [
                    {
                        "path": "src/optimizer.ts",
                        "rank": 1,
                        "owner_strength": 3,
                        "legacy_owner_strength": 1,
                        "support_strength": 0,
                        "carrier_strength": 0,
                        "codes": ["unique_definition_owner"],
                        "protections": [],
                        "owner_features": {"anchor_codes": ["definition"]},
                        "label": "action_owner",
                    },
                    {
                        "path": "tests/optimizer.spec.ts",
                        "rank": 2,
                        "owner_strength": 0,
                        "legacy_owner_strength": 0,
                        "support_strength": 1,
                        "carrier_strength": 3,
                        "codes": ["call_site_only"],
                        "protections": [],
                        "owner_features": {"penalty_codes": ["broad_test_match"]},
                        "label": "required_support",
                    },
                ],
            }}},
        }
    ]

    report = _owner_evidence_report(records)

    assert report["micro_by_min_strength"]["3"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["per_repository"]["vite"]["legacy_strong_recall"] == 0.0
    assert report["owner_availability"]["r@20"] == 1.0
    assert report["path_family_confusion"]["test"]["tn"] == 1
    assert report["determinism"] == {
        "minimum_case_repetitions": 1,
        "three_run_coverage": False,
        "feature_drift_groups": 0,
        "selected_path_drift_groups": 0,
        "legacy_metric_drift_groups": 0,
    }
    assert report["passed"] is False


def test_load_public_repo_specs_defaults_to_balanced_mode(tmp_path: Path) -> None:
    f = tmp_path / "public.toml"
    f.write_text(
        '[[repos]]\n'
        'name = "repo"\n'
        'url = "https://example.test/repo.git"\n\n'
        '[[repos.cases]]\n'
        'commit = "abc123"\n'
        'task = "fix bug"\n'
        'expected_files = ["src/app.ts"]\n',
        encoding="utf-8",
    )

    specs = _load_public_repo_specs(f)

    assert specs[0].mode == "balanced"
    assert specs[0].cases[0].mode == "balanced"


def test_write_public_repo_lock_round_trips_as_explicit_cases(tmp_path: Path) -> None:
    out = tmp_path / "public-lock.toml"
    _write_public_repo_lock(
        out,
        [
            PublicRepoSpec(
                name="vite",
                url="https://github.com/vitejs/vite.git",
                ref="main",
                sample_history=0,
                task_type="typescript",
                mode="balanced",
                budget=4000,
                include_globs=["packages/**/*.ts"],
                exclude_globs=["docs/**"],
                max_changed_files=8,
                cases=[
                    PublicRepoCase(
                        commit="abc123",
                        task='fix "quoted" task',
                        task_type="typescript",
                        mode="balanced",
                        budget=4000,
                        workspace="packages/vite",
                        expected_files=["packages/vite/src/node/index.ts"],
                    )
                ],
            )
        ],
    )

    specs = _load_public_repo_specs(out)

    assert specs[0].sample_history == 0
    assert specs[0].include_globs == ["packages/**/*.ts"]
    assert specs[0].exclude_globs == ["docs/**"]
    assert specs[0].cases[0].commit == "abc123"
    assert specs[0].cases[0].task == 'fix "quoted" task'
    assert specs[0].cases[0].workspace == "packages/vite"
    assert specs[0].cases[0].expected_files == ["packages/vite/src/node/index.ts"]


def test_sample_public_history_cases_uses_commit_subject_and_changed_files(tmp_path: Path) -> None:
    from agentpack.commands import benchmark as benchmark_mod

    spec = benchmark_mod.PublicRepoSpec(
        name="repo",
        url="https://example.test/repo.git",
        ref="main",
        sample_history=2,
        task_type="typescript",
        mode="balanced",
        budget=3000,
        include_globs=["src/**/*.ts", "tests/*.ts", "tests/**/*.ts"],
    )

    def fake_git_lines(_cwd: Path, args: list[str]) -> list[str]:
        if args[0] == "log":
            return [
                "c1\x00Fix auth client",
                "c2\x00Update docs only",
                "c3\x00Fix parser",
            ]
        if args[-1] == "c1":
            return ["src/auth/client.ts", "tests/auth.test.ts"]
        if args[-1] == "c2":
            return ["docs/readme.md"]
        if args[-1] == "c3":
            return ["src/parser/index.ts"]
        return []

    with patch("agentpack.commands.benchmark._git_lines", side_effect=fake_git_lines), \
         patch("agentpack.commands.benchmark._git_stdout", return_value="parent"), \
         patch("agentpack.commands.benchmark._public_path_exists_at_commit", return_value=True):
        cases = _sample_public_history_cases(tmp_path, spec)

    assert [case.commit for case in cases] == ["c1", "c3"]
    assert cases[0].task == "Fix auth client"
    assert cases[0].expected_files == ["src/auth/client.ts", "tests/auth.test.ts"]
    assert cases[0].task_type == "typescript"
    assert cases[0].mode == "balanced"
    assert cases[0].budget == 3000


def test_public_commit_changed_files_filters_noise_added_files_and_large_commits(tmp_path: Path) -> None:
    def exists_in_parent(_repo: Path, _commit: str, path: str) -> bool:
        return path != "src/new.py"

    with patch("agentpack.commands.benchmark._git_stdout", return_value="parent"), \
         patch("agentpack.commands.benchmark._public_path_exists_at_commit", side_effect=exists_in_parent), \
         patch("agentpack.commands.benchmark._git_lines", return_value=[
             "src/app.py",
             "src/new.py",
             "docs/readme.md",
             "package-lock.json",
         ]):
        files = _public_commit_changed_files(
            tmp_path,
            "abc123",
            include_globs=["src/**/*.py", "src/*.py"],
            exclude_globs=["docs/**"],
            max_changed_files=2,
        )

    assert files == ["src/app.py"]


def test_ensure_public_repo_clone_uses_full_shallow_clone(tmp_path: Path) -> None:
    spec = PublicRepoSpec(name="repo", url="https://example.test/repo.git", ref="main")

    with patch("agentpack.commands.benchmark._run_git") as run_git:
        repo = _ensure_public_repo_clone(spec, tmp_path / "cache", depth=25)

    assert repo == tmp_path / "cache" / "repo"
    clone_args = run_git.call_args_list[0].args[1]
    assert clone_args == [
        "clone",
        "--quiet",
        "--depth",
        "25",
        "https://example.test/repo.git",
        str(repo),
    ]
    assert "--filter=blob:none" not in clone_args
    assert any(call.args[1] == ["checkout", "--quiet", "main"] for call in run_git.call_args_list)
    assert any(call.args[1] == ["reset", "--hard", "--quiet", "main"] for call in run_git.call_args_list)
    assert any(call.args[1] == ["clean", "-ffd", "--quiet"] for call in run_git.call_args_list)


def test_run_public_repo_suite_uses_parent_checkout(tmp_path: Path, monkeypatch) -> None:
    from agentpack.commands import benchmark as benchmark_mod

    monkeypatch.chdir(tmp_path)

    spec = benchmark_mod.PublicRepoSpec(
        name="click",
        url="https://example.test/click.git",
        cases=[
            benchmark_mod.PublicRepoCase(
                commit="abc123",
                task="fix prompt",
                expected_files=["src/click/termui.py"],
                task_type="python-cli",
                budget=1200,
            ),
        ],
    )

    observed_roots: list[Path] = []

    def run_git(cwd: Path, args: list[str]) -> None:
        if args[0] == "init":
            Path(args[-1]).mkdir(parents=True)

    def run_case(root: Path, case: BenchmarkCase) -> CaseResult:
        observed_roots.append(root)
        assert root.exists()
        return _make_result(["src/click/termui.py"], case.expected_files)

    with patch("agentpack.commands.benchmark._ensure_public_repo_clone", return_value=Path("cache")), \
         patch("agentpack.commands.benchmark._ensure_git_commit") as ensure_commit, \
         patch("agentpack.commands.benchmark._git_stdout", return_value="parent123") as git_stdout, \
         patch("agentpack.commands.benchmark._run_git", side_effect=run_git) as mocked_run_git, \
         patch("agentpack.commands.benchmark._run_case", side_effect=run_case) as mocked_run_case:
        results = _run_public_repo_suite(tmp_path, [spec], cache_dir=tmp_path / "cache")

    assert len(results) == 1
    case_arg = mocked_run_case.call_args.args[1]
    assert case_arg.task == "fix prompt"
    assert case_arg.task_type == "python-cli"
    assert case_arg.budget == 1200
    assert [call.args for call in ensure_commit.call_args_list] == [
        (tmp_path / "cache", "abc123"),
        (tmp_path / "cache", "parent123"),
    ]
    git_stdout.assert_called_once_with(tmp_path / "cache", ["rev-parse", "abc123^"])
    assert any(call.args[1][0:2] == ["init", "--quiet"] for call in mocked_run_git.call_args_list)
    assert any(call.args[1] == ["remote", "add", "origin", str(tmp_path / "cache")] for call in mocked_run_git.call_args_list)
    assert any(call.args[1] == ["fetch", "--quiet", "--depth", "1", "origin", "parent123"] for call in mocked_run_git.call_args_list)
    assert any(call.args[1] == ["checkout", "--force", "--quiet", "FETCH_HEAD"] for call in mocked_run_git.call_args_list)
    assert any(call.args[1] == ["reset", "--hard", "--quiet", "parent123"] for call in mocked_run_git.call_args_list)
    assert any(call.args[1] == ["clean", "-ffd", "--quiet"] for call in mocked_run_git.call_args_list)
    assert observed_roots and all(not root.exists() for root in observed_roots)


def test_run_public_repo_suite_checkout_error_names_case(tmp_path: Path) -> None:
    from agentpack.commands import benchmark as benchmark_mod

    spec = benchmark_mod.PublicRepoSpec(
        name="vite",
        url="https://example.test/vite.git",
        cases=[
            benchmark_mod.PublicRepoCase(
                commit="abc123",
                task="fix vite",
                expected_files=["packages/vite/src/node/server/index.ts"],
            ),
        ],
    )
    checkout_error = subprocess.CalledProcessError(
        1,
        ["git", "checkout", "--force", "--quiet", "FETCH_HEAD"],
        stderr="pathspec parent123 did not match any file(s) known to git",
    )

    with patch("agentpack.commands.benchmark._ensure_public_repo_clone", return_value=tmp_path / "cache"), \
         patch("agentpack.commands.benchmark._ensure_git_commit"), \
         patch("agentpack.commands.benchmark._git_stdout", return_value="parent123"), \
        patch("agentpack.commands.benchmark._run_git", side_effect=[None, None, None, checkout_error]):
        with pytest.raises(RuntimeError) as excinfo:
            _run_public_repo_suite(tmp_path, [spec], cache_dir=tmp_path / "cache")

    message = str(excinfo.value)
    assert "repo=vite" in message
    assert "commit=abc123" in message
    assert "parent=parent123" in message
    assert "git checkout --force --quiet FETCH_HEAD" in message
    assert "pathspec parent123" in message


# ---------------------------------------------------------------------------
# _persist_result
# ---------------------------------------------------------------------------

def test_persist_result_writes_jsonl(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    r = _make_result(
        ["a.py", "b.py"],
        ["a.py"],
        rank_at_k=3,
        candidate_recall_at_20=0.2,
        candidate_recall_at_50=0.5,
        candidate_recall_at_100=1.0,
        candidate_precision_at_3=0.333,
        candidate_precision_at_5=0.4,
        low_budget_extra_file_waste=100,
        precision_delta_if_drop_last_summary=0.125,
        expected_token_coverage=0.5,
        selected_family_tokens={"source": 100, "docs": 50},
        selected_family_waste_tokens={"docs": 50},
        reason_family_precision={"filename": {"selected": 2.0, "expected": 1.0, "precision": 0.5}},
        failure_type_counts={"EXPECTED_SKIPPED": 1},
        noise_pct=30.0,
        random_f1=0.2,
        top_candidates=[{
            "path": "a.py",
            "rank": 1,
            "score": 10.0,
            "family": "source",
            "selected": True,
            "expected": True,
            "reasons": ["symbol keyword match"],
        }],
        selection_diagnostics={
            "selected_noise": [{
                "path": "b.py",
                "family": "source",
                "tokens": 100,
                "mode": None,
                "rank": 2,
                "score": 5.0,
                "reasons": ["filename keyword match"],
            }],
            "selected_noise_family_tokens": {"source": 100},
            "expected_ranked_not_selected": 0,
            "missed_expected_count": 0,
        },
    )
    _persist_result(tmp_path, r)

    out = tmp_path / ".agentpack" / "benchmark_results.jsonl"
    assert out.exists()
    record = json.loads(out.read_text().strip())
    assert record["task"] == "t"
    assert record["task_type"] == "general"
    assert record["after_ignore_tokens"] == 8000
    assert "saving_pct_honest" in record
    assert record["rank_at_k"] == 3
    assert record["candidate_recall_at_20"] == pytest.approx(0.2)
    assert record["candidate_recall_at_50"] == pytest.approx(0.5)
    assert record["candidate_recall_at_100"] == pytest.approx(1.0)
    assert record["candidate_precision_at_3"] == pytest.approx(0.333)
    assert record["candidate_precision_at_5"] == pytest.approx(0.4)
    assert record["low_budget_extra_file_waste"] == 100
    assert record["precision_delta_if_drop_last_summary"] == pytest.approx(0.125)
    assert record["expected_token_coverage"] == pytest.approx(0.5)
    assert record["selected_family_tokens"] == {"source": 100, "docs": 50}
    assert record["selected_family_waste_tokens"] == {"docs": 50}
    assert record["reason_family_precision"]["filename"]["precision"] == pytest.approx(0.5)
    assert record["failure_type_counts"] == {"EXPECTED_SKIPPED": 1}
    assert record["noise_pct"] == pytest.approx(30.0)
    assert record["token_precision"] == pytest.approx(0.7)
    assert record["random_f1"] == pytest.approx(0.2)
    assert record["top_candidates"][0]["path"] == "a.py"
    assert record["selection_diagnostics"]["selected_noise"][0]["path"] == "b.py"


def test_quality_status_passes_on_recall_and_token_precision() -> None:
    result = _make_result(
        ["a.py", "b.py"],
        ["a.py"],
        noise_pct=40.0,
    )
    passed, metrics = _quality_status([result])

    assert passed is True
    assert metrics["avg_recall"] == 1.0
    assert metrics["avg_token_precision"] == pytest.approx(0.6)


def test_quality_status_fails_without_expected_files() -> None:
    passed, metrics = _quality_status([_make_result(["a.py"], [])])

    assert passed is False
    assert metrics["cases"] == 0


def test_persist_result_appends(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    r = _make_result(["a.py"], [])
    _persist_result(tmp_path, r)
    _persist_result(tmp_path, r)
    lines = (tmp_path / ".agentpack" / "benchmark_results.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2


def test_write_anonymous_benchmark_report_contains_no_source_paths(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "benchmark.toml").write_text(
        '[[cases]]\ntask = "fix bug"\nexpected_files = ["src/app.py"]\n',
        encoding="utf-8",
    )
    (tmp_path / ".agentpack" / "benchmark_results.jsonl").write_text(
        json.dumps({"recall": 1.0, "token_precision": 0.5, "misses": []}) + "\n",
        encoding="utf-8",
    )

    report_md, report_json = _write_anonymous_benchmark_report(tmp_path)

    markdown = report_md.read_text(encoding="utf-8")
    data = json.loads(report_json.read_text(encoding="utf-8"))
    assert "No source code uploaded: true" in markdown
    assert "src/app.py" not in markdown
    assert data["cases"] == 1
    assert data["recall"] == 1.0
    assert data["source_paths_included"] is False


def test_persist_result_no_gt_fields_are_none(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    r = _make_result(["a.py"], [])
    _persist_result(tmp_path, r)
    record = json.loads((tmp_path / ".agentpack" / "benchmark_results.jsonl").read_text().strip())
    assert record["precision"] is None
    assert record["rank_at_k"] is None
    assert record["noise_pct"] is None


# ---------------------------------------------------------------------------
# _load_history_cases
# ---------------------------------------------------------------------------

def test_load_history_cases_returns_unique_tasks(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    metrics = tmp_path / ".agentpack" / "metrics.jsonl"
    records = [
        {"task": "fix auth", "mode": "balanced"},
        {"task": "fix auth", "mode": "balanced"},  # duplicate
        {"task": "add rate limit", "mode": "deep"},
        {"task": "refactor db", "mode": "balanced"},
    ]
    metrics.write_text("\n".join(json.dumps(r) for r in records))
    cases = _load_history_cases(tmp_path, 10)
    tasks = [c.task for c in cases]
    assert len(tasks) == len(set(tasks))
    assert set(tasks) == {"fix auth", "add rate limit", "refactor db"}


def test_load_history_cases_respects_n(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    metrics = tmp_path / ".agentpack" / "metrics.jsonl"
    records = [{"task": f"task {i}", "mode": "balanced"} for i in range(10)]
    metrics.write_text("\n".join(json.dumps(r) for r in records))
    cases = _load_history_cases(tmp_path, 3)
    assert len(cases) == 3


def test_load_history_cases_missing_file(tmp_path: Path) -> None:
    cases = _load_history_cases(tmp_path, 5)
    assert cases == []


# ---------------------------------------------------------------------------
# _random_baseline
# ---------------------------------------------------------------------------

def test_random_baseline_respects_budget() -> None:
    paths = [f"f{i}.py" for i in range(20)]
    token_map = {p: 100 for p in paths}
    selected, p, r, f1 = _random_baseline(paths, token_map, ["f0.py", "f1.py"], budget=500)
    total = sum(token_map.get(p, 0) for p in selected)
    assert total <= 500


def test_random_baseline_no_expected_returns_zeros() -> None:
    _, p, r, f1 = _random_baseline(["a.py"], {"a.py": 100}, [], budget=1000)
    assert p == 0.0 and r == 0.0 and f1 == 0.0


def test_random_baseline_perfect_hit() -> None:
    paths = ["a.py"]
    _, p, r, f1 = _random_baseline(paths, {"a.py": 100}, ["a.py"], budget=1000)
    assert f1 == 1.0


# ---------------------------------------------------------------------------
# _run_case (mocked plan)
# ---------------------------------------------------------------------------

def _make_mock_plan(files: int = 10, tokens: int = 5000):
    fi = MagicMock()
    fi.estimated_tokens = 100
    fi.path = "src/foo.py"
    fi.ignored = False
    fi.binary = False

    scan_result = MagicMock()
    scan_result.packable = [fi]
    scan_result.all_files = [fi] * files

    sf = MagicMock()
    sf.path = "src/foo.py"
    sf.content = "x" * 100
    sf.summary = ""
    sf.symbols = []
    sf.reasons = ["filename keyword match"]
    sf.include_mode = "summary"
    sf.score = 1.0

    scored_fi = MagicMock()
    scored_fi.path = "src/foo.py"

    plan = MagicMock()
    plan.selected = [sf]
    plan.scan_result = scan_result
    plan.all_changed = {"src/foo.py"}
    plan.phase_times = {"scan": 0.1, "rank": 0.05}
    plan.scored = [(scored_fi, 1.0, ["keyword_match"])]
    plan.receipts = []
    plan.summaries = {}
    plan.changed_files_source = "git working tree"
    return plan


def test_run_case_returns_result(tmp_path: Path) -> None:
    case = BenchmarkCase(task="fix bug", mode="balanced")
    mock_plan = _make_mock_plan()

    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.application.pack_service._sf_tokens", return_value=50):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = _run_case(tmp_path, case)

    assert result.packed_tokens > 0
    assert result.saving_pct >= 0
    assert result.saving_pct_honest >= 0
    assert result.after_ignore_tokens > 0
    assert result.total_s >= 0
    assert result.changed_covered == 1
    assert result.changed_total == 1
    assert result.selected_tokens is not None


def test_run_case_with_expected_files_sets_quality_fields(tmp_path: Path) -> None:
    case = BenchmarkCase(task="fix bug", mode="balanced", expected_files=["src/foo.py"])
    mock_plan = _make_mock_plan()

    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.application.pack_service._sf_tokens", return_value=50):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = _run_case(tmp_path, case)

    assert result.rank_at_k == 1
    assert result.noise_pct is not None
    assert result.expected_token_coverage is not None
    assert result.selected_family_tokens
    assert result.reason_family_precision
    assert result.random_f1 is not None


def test_run_case_records_miss_diagnostics(tmp_path: Path) -> None:
    case = BenchmarkCase(task="fix bug", mode="balanced", expected_files=["src/missing.py"])
    mock_plan = _make_mock_plan()
    mock_plan.receipts = [Receipt(path="src/missing.py", action="excluded", reason="budget exhausted")]

    missing_fi = MagicMock()
    missing_fi.path = "src/missing.py"
    missing_fi.estimated_tokens = 200
    missing_fi.ignored = False
    missing_fi.binary = False
    mock_plan.scan_result.packable = mock_plan.scan_result.packable + [missing_fi]
    mock_plan.scan_result.all_files = mock_plan.scan_result.all_files + [missing_fi]
    mock_plan.scored = mock_plan.scored + [(missing_fi, 42.0, ["filename keyword match"])]

    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.application.pack_service._sf_tokens", return_value=50):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = _run_case(tmp_path, case)

    assert result.top_candidates[0]["path"] == "src/missing.py"
    assert result.top_candidates[0]["expected"] is True
    assert result.selection_diagnostics["selected_noise"][0]["path"] == "src/foo.py"

    miss = result.missed_expected[0]
    assert miss["path"] == "src/missing.py"
    assert miss["status"] == "budget exhausted"
    assert miss["failure_type"] == "EXPECTED_SKIPPED"
    assert miss["family"] == "source"
    assert miss["rank"] == 1
    assert miss["score"] == 42.0
    assert miss["reasons"] == ["filename keyword match"]
    assert miss["basis"] == mock_plan.changed_files_source
    assert miss["would_select_with_one_more_slot"] is True
    assert miss["score_delta_vs_last_selected"] == pytest.approx(41.0)
    assert miss["selected_noise_file_that_beat_expected"] is None
    assert miss["cap_block_diagnostic"] is None


def test_run_case_records_cap_block_diagnostic(tmp_path: Path) -> None:
    case = BenchmarkCase(task="fix config", mode="balanced", expected_files=["src/missing.py"])
    mock_plan = _make_mock_plan()
    mock_plan.receipts = [Receipt(path="src/missing.py", action="excluded", reason="compressed context cap reached")]

    missing_fi = MagicMock()
    missing_fi.path = "src/missing.py"
    missing_fi.estimated_tokens = 200
    missing_fi.ignored = False
    missing_fi.binary = False
    mock_plan.scan_result.packable = mock_plan.scan_result.packable + [missing_fi]
    mock_plan.scan_result.all_files = mock_plan.scan_result.all_files + [missing_fi]
    mock_plan.scored = mock_plan.scored + [(
        missing_fi,
        220.0,
        ["config file", "content keyword match (3)", "matched define: missing_config"],
    )]
    mock_plan.summaries = {
        "src/missing.py": {
            "summary": "Missing config owner.",
            "symbols": [{"signature": "def missing_config(): ..."}],
        }
    }

    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.application.pack_service._sf_tokens", return_value=50):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = _run_case(tmp_path, case)

    diagnostic = result.missed_expected[0]["cap_block_diagnostic"]
    assert diagnostic["candidate_mode"] == "skeleton"
    assert diagnostic["candidate_has_strong_evidence"] is True
    assert diagnostic["replaceable_selected_tokens"] == 50
    assert diagnostic["replaceable_selected"][0]["path"] == "src/foo.py"
    assert diagnostic["block_reason"] == "replacement appears feasible"


def test_benchmark_cli_single_task(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from agentpack.cli import app
    import os
    os.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()

    mock_plan = _make_mock_plan()
    runner = CliRunner()

    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.application.pack_service._sf_tokens", return_value=500):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = runner.invoke(app, ["benchmark", "--task", "fix auth bug"])

    assert result.exit_code == 0
    assert "fix auth bug" in result.output


def test_benchmark_cli_init(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from agentpack.cli import app
    import os
    os.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()

    runner = CliRunner()
    result = runner.invoke(app, ["benchmark", "--init"])
    assert result.exit_code == 0
    assert (tmp_path / ".agentpack" / "benchmark.toml").exists()


def test_benchmark_cli_from_history(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from agentpack.cli import app
    import os
    os.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()

    metrics = tmp_path / ".agentpack" / "metrics.jsonl"
    metrics.write_text(json.dumps({"task": "fix auth", "mode": "balanced"}) + "\n")

    mock_plan = _make_mock_plan()
    runner = CliRunner()

    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.application.pack_service._sf_tokens", return_value=500):
        MockPlanner.return_value.plan.return_value = mock_plan
        result = runner.invoke(app, ["benchmark", "--from-history", "5"])

    assert result.exit_code == 0
    assert "fix auth" in result.output


def test_sample_fixture_cases_include_framework_repos() -> None:
    fixtures_root = Path(__file__).parent / "fixtures"
    fixture_cases = _sample_fixture_cases(fixtures_root)

    fixture_names = {c.fixture for c in fixture_cases}
    assert {
        "py_fastapi_app",
        "nextjs_app",
        "mixed_repo",
        "django_rest_app",
        "go_service",
        "rails_app",
    } <= fixture_names
    assert all(c.root.exists() for c in fixture_cases)
    assert all(c.case.expected_files for c in fixture_cases)
    assert {c.case.task_type for c in fixture_cases} >= {"backend-api", "frontend-web", "infrastructure"}


def test_benchmark_cli_sample_fixtures_uses_temp_copies(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner
    from agentpack.cli import app

    source_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(source_root)

    runner = CliRunner()
    fixture_agentpack = source_root / "tests" / "fixtures" / "py_fastapi_app" / ".agentpack"

    with patch("agentpack.commands.benchmark._run_case") as run_case:
        run_case.side_effect = lambda _root, _case: _make_result(["src/app/auth.py"], ["src/app/auth.py"])
        result = runner.invoke(app, ["benchmark", "--sample-fixtures"])

    assert result.exit_code == 0
    assert "sample fixture benchmark" in result.output
    assert "py_fastapi_app" in result.output
    assert not fixture_agentpack.exists()


def test_benchmark_result_persisted_after_run(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from agentpack.cli import app
    import os
    os.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()

    mock_plan = _make_mock_plan()
    runner = CliRunner()

    with patch("agentpack.application.pack_service.PackPlanner") as MockPlanner, \
         patch("agentpack.application.pack_service._sf_tokens", return_value=500):
        MockPlanner.return_value.plan.return_value = mock_plan
        runner.invoke(app, ["benchmark", "--task", "fix auth bug"])

    out = tmp_path / ".agentpack" / "benchmark_results.jsonl"
    assert out.exists()
    record = json.loads(out.read_text().strip())
    assert record["task"] == "fix auth bug"
    assert "saving_pct_honest" in record
    assert "after_ignore_tokens" in record
    assert "misses" in record


def test_benchmark_cli_misses_prints_diagnostics(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from agentpack.cli import app
    import os
    os.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "benchmark.toml").write_text(
        '[[cases]]\n'
        'task = "fix kundali"\n'
        'expected_files = ["backend/src/services/astrology.service.ts"]\n',
        encoding="utf-8",
    )

    runner = CliRunner()
    mocked = _make_result(
        selected=["frontend/app/charts/page.tsx"],
        expected=["backend/src/services/astrology.service.ts"],
        missed_expected=[{
            "path": "backend/src/services/astrology.service.ts",
            "status": "summary score below floor",
            "rank": 18,
            "score": 54.0,
            "reasons": ["filename keyword match"],
        }],
    )

    with patch("agentpack.commands.benchmark._run_case", return_value=mocked):
        result = runner.invoke(app, ["benchmark", "--misses"])

    assert result.exit_code == 0
    assert "miss details" in result.output
    assert "astrology.service.ts" in result.output


def test_load_e2e_cases_reads_guard_and_expected_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cases = tmp_path / "cases.toml"
    cases.write_text(
        f"""
[[cases]]
name = "guarded"
repo = "{repo}"
task = "fix guarded case"
test_command = "pytest"
protected_paths = ["tests/test_guard.py"]
expected_edit_paths = ["src/guard.py"]
""",
        encoding="utf-8",
    )

    loaded = _load_e2e_cases(tmp_path, cases)

    assert loaded[0].protected_paths == ["tests/test_guard.py"]
    assert loaded[0].expected_edit_paths == ["src/guard.py"]


def test_e2e_changed_file_classification_helpers() -> None:
    changed = _public_changed_files([
        ".agentpack_e2e_prompt.txt",
        ".agentpack/",
        "src/__pycache__/",
        "src/app.py",
        "tests/test_app.py",
        "frontend/button.test.tsx",
    ])

    assert changed == ["frontend/button.test.tsx", "src/app.py", "tests/test_app.py"]
    assert _is_test_path("tests/test_app.py")
    assert _is_test_path("src/test/java/org/example/AppTests.java")
    assert _is_test_path("frontend/button.test.tsx")
    assert _is_test_path("context_test.go")
    assert not _is_test_path("src/app.py")
    assert _expected_files_touched(changed, ["src/app.py"]) == ["src/app.py"]
    assert _unexpected_files_touched(changed, ["src/app.py"]) == ["frontend/button.test.tsx", "tests/test_app.py"]
    assert _unexpected_files_touched(changed, []) == []


def test_timeout_result_records_failed_process() -> None:
    exc = subprocess.TimeoutExpired(["agent"], timeout=7, output=b"partial", stderr=b"slow")

    result = _timeout_result(["agent"], exc)

    assert result.returncode == 124
    assert result.stdout == "partial"
    assert "Timed out after 7 seconds" in result.stderr


def test_e2e_cost_helpers_estimate_prompt_and_output_cost() -> None:
    result = subprocess.CompletedProcess(
        args=["agent"],
        returncode=0,
        stdout="exec_command rg auth\nhello world",
        stderr="apply_patch done",
    )

    assert _process_output_tokens(result) >= 1
    assert _estimate_agent_tool_calls(result) >= 2
    assert _estimate_token_cost(1_000_000, 2.5) == 2.5
    assert _estimate_token_cost(1000, 0.0) == 0.0


def test_time_to_first_expected_file_uses_mtime_delta(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("print('ok')\n", encoding="utf-8")
    start = target.stat().st_mtime - 2.0

    delta = _time_to_first_expected_file(tmp_path, ["src/app.py"], start)

    assert delta == pytest.approx(2.0, abs=0.1)


def test_run_e2e_case_fails_when_protected_file_changes(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_guard.py").write_text("def test_guard():\n    assert True\n", encoding="utf-8")
    agent = tmp_path / "agent.py"
    agent.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1], 'tests/test_guard.py').write_text('def test_guard():\\n    assert True\\n# edited\\n')\n",
        encoding="utf-8",
    )
    case = E2ECase(
        name="protected",
        repo=repo,
        task="do not edit tests",
        test_command="python -c 'pass'",
        protected_paths=["tests/test_guard.py"],
        expected_edit_paths=["src/app.py"],
    )

    result = _run_e2e_case(
        case,
        strategy="no-context",
        trial=1,
        agent_command=f"python {agent} {{repo}} {{prompt}}",
        timeout=10,
        input_cost_per_mtok=1.0,
        output_cost_per_mtok=2.0,
        keep_workdir=True,
    )

    assert not result.passed
    assert result.protected_files_changed == ["tests/test_guard.py"]
    assert result.test_files_changed == ["tests/test_guard.py"]
    assert result.missing_expected_edits == ["src/app.py"]
    assert result.agent_output_tokens >= 1
    assert result.estimated_total_cost_usd > 0
    assert result.agent_tool_calls >= 0
    assert result.time_to_first_expected_file_s is None
    assert result.agentpack_noise == []


def test_e2e_hybrid_prompt_combines_grep_and_lite(tmp_path: Path) -> None:
    case = E2ECase(name="hybrid", repo=tmp_path, task="fix auth", test_command="pytest")

    with patch("agentpack.commands.benchmark._grep_context", return_value="grep-hit"), \
         patch("agentpack.commands.benchmark._agentpack_lite_context", return_value="lite-map"):
        prompt = _e2e_prompt(case, "hybrid", tmp_path)

    assert "grep-hit" in prompt
    assert "lite-map" in prompt


def test_e2e_ab_metrics_reports_saved_tool_tokens_cost_time_and_success(tmp_path: Path) -> None:
    records = [
        {
            "strategy": "no-context",
            "passed": False,
            "input_tokens": 1000,
            "agent_output_tokens": 500,
            "estimated_total_cost_usd": 0.03,
            "duration_s": 60,
            "agent_tool_calls": 12,
            "time_to_first_expected_file_s": 40,
            "expected_files_touched": [],
            "missing_expected_edits": ["src/app.py"],
        },
        {
            "strategy": "agentpack",
            "passed": True,
            "input_tokens": 1200,
            "agent_output_tokens": 100,
            "estimated_total_cost_usd": 0.02,
            "duration_s": 45,
            "agent_tool_calls": 6,
            "time_to_first_expected_file_s": 10,
            "expected_files_touched": ["src/app.py"],
            "missing_expected_edits": [],
            "agentpack_noise": ["unexpected README"],
        },
    ]

    metrics = _e2e_ab_metrics(records, baseline="no-context", treatment="agentpack")
    markdown = _e2e_ab_markdown(records, baseline="no-context", treatment="agentpack", source=tmp_path / "results.jsonl")

    assert metrics["deltas"]["success_rate_pp"] == pytest.approx(100.0)
    assert metrics["deltas"]["tool_calls_saved"] == pytest.approx(6.0)
    assert metrics["deltas"]["token_cost_saved_usd"] == pytest.approx(0.01)
    assert metrics["deltas"]["time_to_first_correct_file_saved_s"] == pytest.approx(30.0)
    assert metrics["treatment"]["noise_rate"] == pytest.approx(1.0)
    assert "tool calls" in markdown
    assert "time to first correct file" in markdown
    assert "AgentPack noise cases" in markdown


def test_e2e_agentpack_lite_prompt_uses_compact_map(tmp_path: Path) -> None:
    case = E2ECase(name="lite", repo=tmp_path, task="fix refund", test_command="pytest")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text(
        "[context_lite]\nbudget = 1234\nmax_selected_files = 1\nmax_omitted_files = 1\nmax_stubs = 1\nsummary_chars = 50\n",
        encoding="utf-8",
    )
    selected = SimpleNamespace(
        path="src/refund.py",
        include_mode="summary",
        score=123.0,
        reasons=["keyword match"],
        summary="Refund service summary",
        symbols=[SimpleNamespace(signature="def refund_order(order_id):")],
    )
    omitted = SimpleNamespace(
        path="api/refund_route.py",
        risk="high",
        score=95.0,
        reasons=["caller of refund_order"],
        omission_reason="budget exhausted",
    )
    fake_result = SimpleNamespace(
        pack=SimpleNamespace(
            selected_files=[selected],
            omitted_relevant_files=[omitted],
            changed_files=["src/refund.py"],
        )
    )

    with patch("agentpack.commands.benchmark.PackService") as service:
        service.return_value.run.return_value = fake_result
        prompt = _e2e_prompt(case, "agentpack-lite", tmp_path)

    request = service.return_value.run.call_args.args[0]
    assert request.budget == 1234
    assert request.mode == "lite"
    assert "Selected File Map" in prompt
    assert "`src/refund.py`" in prompt
    assert "High-Risk Omitted Files" in prompt
    assert "`api/refund_route.py`" in prompt
    assert "def refund_order(order_id):" in prompt


def test_e2e_cases_template_scaffolds_guarded_hard_categories(tmp_path: Path) -> None:
    content = _e2e_cases_template(tmp_path)

    assert "caller_signature_change" in content
    assert "api_service_model_contract" in content
    assert "protected_paths" in content
    assert "expected_edit_paths" in content
    assert "agentpack-lite,hybrid,agentpack" in content


def test_benchmark_e2e_init_writes_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["benchmark", "e2e-init"])

    assert result.exit_code == 0, result.output
    out = tmp_path / ".agentpack" / "e2e_cases.toml"
    assert out.exists()
    assert "guarded E2E benchmark cases" in out.read_text(encoding="utf-8")
