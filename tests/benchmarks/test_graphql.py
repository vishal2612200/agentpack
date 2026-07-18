"""GraphQL ranking-quality benchmark (saleor).

object/interface/enum type definitions map to class-kind symbols;
field_definition maps to method-kind symbols, qualified under the
enclosing type. No import edges (GraphQL SDL has no cross-file import
construct in the base spec).

Wall time on this repo is a genuine outlier (~116s/case observed) due to a
pre-existing O(files) cost in ranking.py's test-pairing logic, amplified by
saleor's single schema.graphql carrying 5759 symbols (every field becomes a
qualified method). This is not a bug in the new query -- see
benchmarks/results/infra-schema-baseline.md and the threshold comment in
thresholds.toml for the isolation that confirmed it.
"""

from tests.benchmarks._harness import run_public_repo_benchmark


def test_graphql_ranking_quality(thresholds, capsys):
    cfg = thresholds["graphql"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[graphql] {metrics.as_dict()}")

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
