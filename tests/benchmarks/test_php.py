"""PHP ranking-quality benchmark (laravel-framework).

The current backend enables tree-sitter symbol + `use`-statement import edges
for PHP files. When you deepen the PHP query (PHPDoc, attributes, method
signatures) or add PSR-4 resolution to turn raw-string imports into resolved
graph edges, raise the corresponding thresholds in thresholds.toml.
"""

from tests.benchmarks._harness import run_public_repo_benchmark


def test_php_ranking_quality(thresholds, capsys):
    cfg = thresholds["php"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[php] {metrics.as_dict()}")

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
    assert metrics.reason_graph_precision >= cfg["min_reason_graph_precision"], (
        f"reason graph precision {metrics.reason_graph_precision:.3f} < threshold "
        f"{cfg['min_reason_graph_precision']:.3f} — tree-sitter `use` edges are "
        f"the main lever for this signal on PHP"
    )
    assert metrics.reason_content_precision >= cfg["min_reason_content_precision"], (
        f"reason content precision {metrics.reason_content_precision:.3f} < "
        f"threshold {cfg['min_reason_content_precision']:.3f}"
    )
    assert metrics.median_wall_seconds <= cfg["max_median_wall_seconds"], (
        f"median wall time {metrics.median_wall_seconds:.2f}s > budget "
        f"{cfg['max_median_wall_seconds']:.2f}s"
    )
