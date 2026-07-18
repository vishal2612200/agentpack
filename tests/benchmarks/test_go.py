"""Go ranking-quality benchmark (gin).

Go routes through the regex extractor; imports are captured as raw strings.
Baseline established from the existing suite. Regression guard for the same
reason as Python — proves changes to shared code don't drift Go quality.
"""

from tests.benchmarks._harness import run_public_repo_benchmark


def test_go_ranking_quality(thresholds, capsys):
    cfg = thresholds["go"]
    metrics = run_public_repo_benchmark(cfg["repo"])
    with capsys.disabled():
        print(f"\n[go] {metrics.as_dict()}")

    assert metrics.cases >= cfg["min_cases"]
    assert metrics.avg_recall >= cfg["min_recall"]
    assert metrics.avg_token_precision >= cfg["min_token_precision"]
    assert metrics.reason_graph_precision >= cfg["min_reason_graph_precision"]
    assert metrics.reason_content_precision >= cfg["min_reason_content_precision"]
    assert metrics.median_wall_seconds <= cfg["max_median_wall_seconds"]
