"""Shared helper for the per-language ranking benchmark tests.

Runs `agentpack benchmark --public-repos --public-repo-filter <repo>
--benchmark-jsonl <path>` and parses the machine-readable JSONL. Aggregates
per-case metrics into the numbers `thresholds.toml` gates on.

The benchmark caches repo clones under `.agentpack/public-repos/` and reuses
them across runs, so the second time each language test executes it only
pays the pack cost, not the clone cost.
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class BenchmarkAggregate:
    """Aggregate ranking-quality metrics across sampled cases."""

    cases: int
    avg_recall: float
    avg_token_precision: float
    reason_graph_precision: float
    reason_content_precision: float
    reason_symbol_precision: float
    relationship_precision: float | None
    relationship_recall: float | None
    source_line_grounding: float | None
    path_correctness: float | None
    first_correct_file_rate: float
    routing_recall: float
    incremental_rebuild_seconds: float | None
    median_wall_seconds: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "cases": self.cases,
            "avg_recall": round(self.avg_recall, 4),
            "avg_token_precision": round(self.avg_token_precision, 4),
            "reason_graph_precision": round(self.reason_graph_precision, 4),
            "reason_content_precision": round(self.reason_content_precision, 4),
            "reason_symbol_precision": round(self.reason_symbol_precision, 4),
            "relationship_precision": round(self.relationship_precision, 4) if self.relationship_precision is not None else None,
            "relationship_recall": round(self.relationship_recall, 4) if self.relationship_recall is not None else None,
            "source_line_grounding": round(self.source_line_grounding, 4) if self.source_line_grounding is not None else None,
            "path_correctness": round(self.path_correctness, 4) if self.path_correctness is not None else None,
            "first_correct_file_rate": round(self.first_correct_file_rate, 4),
            "routing_recall": round(self.routing_recall, 4),
            "incremental_rebuild_seconds": round(self.incremental_rebuild_seconds, 4) if self.incremental_rebuild_seconds is not None else None,
            "median_wall_seconds": round(self.median_wall_seconds, 3),
        }


def run_public_repo_benchmark(repo: str, *, refresh: bool = False) -> BenchmarkAggregate:
    """Run the public-repo benchmark for `repo`, return aggregate metrics."""
    with tempfile.NamedTemporaryFile(
        suffix=".jsonl", delete=False, dir=tempfile.gettempdir()
    ) as f:
        jsonl_path = Path(f.name)

    argv = [
        "agentpack", "benchmark",
        "--public-repos",
        "--public-repo-filter", repo,
        "--benchmark-jsonl", str(jsonl_path),
        "--no-public-table",
    ]
    if refresh:
        argv.append("--refresh-public-repos")

    result = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not jsonl_path.exists() or jsonl_path.stat().st_size == 0:
        raise RuntimeError(
            f"benchmark produced no JSONL output for repo={repo}\n"
            f"stdout tail: {result.stdout[-2000:]}\n"
            f"stderr tail: {result.stderr[-2000:]}"
        )

    cases = _load_cases(jsonl_path)
    jsonl_path.unlink(missing_ok=True)
    if not cases:
        raise RuntimeError(f"benchmark produced empty JSONL for repo={repo}")
    return _aggregate(cases)


def _load_cases(jsonl_path: Path) -> list[dict]:
    out: list[dict] = []
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"skipping unparseable jsonl line: {line[:200]}", file=sys.stderr)
    return out


def _aggregate(cases: list[dict]) -> BenchmarkAggregate:
    recalls = [_case_recall(c) for c in cases]
    precisions = [c.get("precision", 0.0) or 0.0 for c in cases]
    graph_precs = [_reason_precision(c, "graph") for c in cases]
    content_precs = [_reason_precision(c, "content") for c in cases]
    symbol_precs = [_reason_precision(c, "symbol") for c in cases]
    walls = [_case_wall_seconds(c) for c in cases]

    return BenchmarkAggregate(
        cases=len(cases),
        avg_recall=statistics.fmean(recalls),
        avg_token_precision=statistics.fmean(precisions),
        # Family-precision metrics only meaningful over cases where the signal
        # actually fired; averaging with None-cases skews toward zero. Mean over
        # cases that had at least one selection via that signal.
        reason_graph_precision=_mean_over_fired(graph_precs),
        reason_content_precision=_mean_over_fired(content_precs),
        reason_symbol_precision=_mean_over_fired(symbol_precs),
        relationship_precision=_optional_mean(cases, "relationship_precision"),
        relationship_recall=_optional_mean(cases, "relationship_recall"),
        source_line_grounding=_optional_mean(cases, "source_line_grounding"),
        path_correctness=_optional_mean(cases, "path_correctness"),
        first_correct_file_rate=statistics.fmean(_first_correct_file(c) for c in cases),
        routing_recall=_optional_mean(cases, "routing_recall", fallback_key="candidate_recall_at_20"),
        incremental_rebuild_seconds=_optional_mean(cases, "incremental_rebuild_seconds"),
        median_wall_seconds=statistics.median(walls) if walls else 0.0,
    )


def _case_recall(case: dict) -> float:
    """Recall for a single case = (expected - missed) / expected."""
    expected = case.get("expected_files") or []
    if not expected:
        return 0.0
    misses = case.get("misses") or []
    return max(0.0, (len(expected) - len(misses)) / len(expected))


def _case_wall_seconds(case: dict) -> float:
    """Sum of ranker-relevant phase timings (excludes agent-run wall)."""
    phases = case.get("phases") or {}
    if not phases:
        return 0.0
    return float(sum(v for v in phases.values() if isinstance(v, (int, float))))


def _reason_precision(case: dict, family: str) -> float | None:
    reasons = case.get("reason_family_precision") or {}
    entry = reasons.get(family)
    if not entry:
        return None
    selected = entry.get("selected") or 0
    if not selected:
        return None
    return float(entry.get("precision") or 0.0)


def _mean_over_fired(values: list[float | None]) -> float:
    fired = [v for v in values if v is not None]
    if not fired:
        return 0.0
    return statistics.fmean(fired)


def _optional_mean(cases: list[dict], key: str, *, fallback_key: str | None = None) -> float | None:
    values = [
        float(case.get(key) if case.get(key) is not None else case.get(fallback_key))
        for case in cases
        if (case.get(key) is not None or (fallback_key and case.get(fallback_key) is not None))
    ]
    return statistics.fmean(values) if values else None


def _first_correct_file(case: dict) -> float:
    expected = set(case.get("expected_files") or [])
    selected = set(case.get("selected_paths") or [])
    return 1.0 if expected & selected else 0.0
