"""Ruby ranking-quality benchmark (rails).

Symbols route through tree-sitter (Phase 1); `require_relative` already
resolves to real repo-file import edges (unlike PHP's raw-string `use`
edges). Numbers here are genuinely low — Rails is a huge monorepo and a
single-file low-context task subject is a hard case for keyword ranking
regardless of language. See the note in thresholds.toml for the miss
analysis that confirmed this isn't a config bug.
"""

from tests.benchmarks._harness import run_public_repo_benchmark


def test_ruby_ranking_quality(thresholds, capsys):
    cfg = thresholds["ruby"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[ruby] {metrics.as_dict()}")

    assert metrics.cases >= cfg["min_cases"], (
        f"sampled only {metrics.cases} cases (< {cfg['min_cases']}) — "
        f"check that sample_history in public-repos.toml is high enough"
    )
    assert metrics.avg_recall >= cfg["min_recall"], (
        f"avg recall {metrics.avg_recall:.3f} < threshold {cfg['min_recall']:.3f} "
        f"(regression on {cfg['repo']})"
    )
    assert metrics.avg_token_precision >= cfg["min_token_precision"]
    assert metrics.reason_graph_precision >= cfg["min_reason_graph_precision"], (
        f"reason graph precision {metrics.reason_graph_precision:.3f} < "
        f"threshold {cfg['min_reason_graph_precision']:.3f} — require_relative "
        f"resolution is the main lever for this signal on Ruby"
    )
    assert metrics.reason_content_precision >= cfg["min_reason_content_precision"]
    assert metrics.median_wall_seconds <= cfg["max_median_wall_seconds"]
