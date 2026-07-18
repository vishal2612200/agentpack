"""TypeScript ranking-quality benchmark (vite).

TS currently routes through the regex extractor. If we later swap TS to
tree-sitter (Tier 5 in the roadmap), the expected lift lands here first.
Bumps to `min_recall` and `min_reason_content_precision` are the two rows
to watch when tree-sitter for TS ships.
"""

from tests.benchmarks._harness import run_public_repo_benchmark


def test_typescript_ranking_quality(thresholds, capsys):
    cfg = thresholds["typescript"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[typescript] {metrics.as_dict()}")

    assert metrics.cases >= cfg["min_cases"]
    assert metrics.avg_recall >= cfg["min_recall"]
    assert metrics.avg_token_precision >= cfg["min_token_precision"]
    assert metrics.reason_graph_precision >= cfg["min_reason_graph_precision"]
    assert metrics.reason_content_precision >= cfg["min_reason_content_precision"]
    assert metrics.median_wall_seconds <= cfg["max_median_wall_seconds"]
