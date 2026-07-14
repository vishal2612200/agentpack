"""Java ranking-quality benchmark (spring-petclinic).

Symbols now route through tree-sitter (Phase 1); imports remain raw
strings (no classpath/package resolution yet — that's Phase 2 of the
language-coverage plan). `reason_graph_precision` here reflects the
existing java_imports.py regex extractor, not tree-sitter.
"""

from tests.benchmarks._harness import run_public_repo_benchmark


def test_java_ranking_quality(thresholds, capsys):
    cfg = thresholds["java"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[java] {metrics.as_dict()}")

    assert metrics.cases >= cfg["min_cases"], (
        f"sampled only {metrics.cases} cases (< {cfg['min_cases']}) — "
        f"check that sample_history in public-repos.toml is high enough, "
        f"or that the repo's mainline isn't merge-commit-dominated"
    )
    assert metrics.avg_recall >= cfg["min_recall"], (
        f"avg recall {metrics.avg_recall:.3f} < threshold {cfg['min_recall']:.3f} "
        f"(regression on {cfg['repo']})"
    )
    assert metrics.avg_token_precision >= cfg["min_token_precision"]
    assert metrics.reason_graph_precision >= cfg["min_reason_graph_precision"]
    assert metrics.reason_content_precision >= cfg["min_reason_content_precision"], (
        f"reason content precision {metrics.reason_content_precision:.3f} < "
        f"threshold {cfg['min_reason_content_precision']:.3f} — tree-sitter "
        f"symbol extraction is the main lever for this signal on Java"
    )
    assert metrics.median_wall_seconds <= cfg["max_median_wall_seconds"]
