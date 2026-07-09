"""Dockerfile ranking-quality benchmark (docker-library/python).

Named build stages (`FROM x AS name`) map to class-kind symbols; `ARG`
declarations map to variable-kind symbols. No import edges are captured
(COPY --from=<stage> references a build stage, not a file path).

Recall is low on this repo by construction: most commits touch 6-8 nearly
identical per-variant Dockerfiles (alpine/bookworm/slim/trixie/windows) that
differ only in a version string, so correctly ranking one variant highly
while still missing its siblings is expected, not a regression signal.
"""
import pytest

from tests.benchmarks._harness import run_public_repo_benchmark


def test_dockerfile_ranking_quality(thresholds, capsys):
    cfg = thresholds["dockerfile"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[dockerfile] {metrics.as_dict()}")

    assert metrics.cases >= cfg["min_cases"], (
        f"sampled only {metrics.cases} cases (< {cfg['min_cases']}) — "
        f"check that sample_history in public-repos.toml is high enough"
    )
    assert metrics.avg_recall >= cfg["min_recall"], (
        f"avg recall {metrics.avg_recall:.3f} < threshold {cfg['min_recall']:.3f} "
        f"(regression on {cfg['repo']})"
    )
    assert metrics.avg_token_precision >= cfg["min_token_precision"], (
        f"avg token precision {metrics.avg_token_precision:.3f} < threshold "
        f"{cfg['min_token_precision']:.3f}"
    )
    assert metrics.reason_content_precision >= cfg["min_reason_content_precision"], (
        f"reason content precision {metrics.reason_content_precision:.3f} < "
        f"threshold {cfg['min_reason_content_precision']:.3f}"
    )
    assert metrics.median_wall_seconds <= cfg["max_median_wall_seconds"], (
        f"median wall time {metrics.median_wall_seconds:.2f}s > budget "
        f"{cfg['max_median_wall_seconds']:.2f}s"
    )
