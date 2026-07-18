"""Python ranking-quality benchmark (pallets-click).

Python routes through the stdlib `ast` extractor, not tree-sitter. This test
exists as a **regression guard**: if any change to the ranker or scan pipeline
lowers Python recall, the test fails. Also serves as the empirical ceiling
reference — 75% here is what "the ranker's mechanism works well" looks like.
"""

from tests.benchmarks._harness import run_public_repo_benchmark


def test_python_ranking_quality(thresholds, capsys):
    cfg = thresholds["python"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[python] {metrics.as_dict()}")

    assert metrics.cases >= cfg["min_cases"]
    assert metrics.avg_recall >= cfg["min_recall"], (
        f"avg recall {metrics.avg_recall:.3f} < threshold {cfg['min_recall']:.3f} — "
        f"regression on the language that already worked; investigate ranker changes"
    )
    assert metrics.avg_token_precision >= cfg["min_token_precision"]
    assert metrics.reason_graph_precision >= cfg["min_reason_graph_precision"]
    assert metrics.reason_content_precision >= cfg["min_reason_content_precision"]
    assert metrics.median_wall_seconds <= cfg["max_median_wall_seconds"]
