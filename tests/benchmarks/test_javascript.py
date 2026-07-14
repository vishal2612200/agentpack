"""JavaScript ranking-quality benchmark (expressjs/express).

Currently routes through the same regex extractor as TypeScript. If TS gets
swapped to tree-sitter, JS will follow (same code branch). Baseline recall
of ~55% is the number to lift.
"""

from tests.benchmarks._harness import run_public_repo_benchmark


def test_javascript_ranking_quality(thresholds, capsys):
    cfg = thresholds["javascript"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[javascript] {metrics.as_dict()}")

    assert metrics.cases >= cfg["min_cases"]
    assert metrics.avg_recall >= cfg["min_recall"]
    assert metrics.avg_token_precision >= cfg["min_token_precision"]
    assert metrics.reason_graph_precision >= cfg["min_reason_graph_precision"]
    assert metrics.reason_content_precision >= cfg["min_reason_content_precision"]
    assert metrics.median_wall_seconds <= cfg["max_median_wall_seconds"]
