from __future__ import annotations

from pathlib import Path

from agentpack.architecture.service import build_snapshot_for_ref
from agentpack.architecture.index import SemanticGraphIndex
from agentpack.commands.benchmark import _semantic_graph_metrics
from tests.benchmarks.semantic_compare import aggregate, compare


def test_semantic_benchmark_scores_relationships_lines_and_paths(tmp_path: Path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def validate(value):\n    return missing_dependency(value)\n",
        encoding="utf-8",
    )
    graph = SemanticGraphIndex(build_snapshot_for_ref(tmp_path))

    metrics = _semantic_graph_metrics(
        graph,
        {
            "relationships": [
                {
                    "source": "validate",
                    "relationship": "calls",
                    "target": "missing_dependency",
                    "path": "src/auth.py",
                    "start_line": 2,
                    "end_line": 2,
                }
            ],
            "paths": [
                {
                    "source": "src.auth",
                    "target": "missing_dependency",
                    "relationships": ["contains", "calls"],
                }
            ],
        },
        {"deps": 0.01},
    )

    assert metrics is not None
    assert metrics["relationship_precision"] == 1.0
    assert metrics["relationship_recall"] == 1.0
    assert metrics["source_line_grounding"] == 1.0
    assert metrics["path_correctness"] == 1.0


def test_semantic_benchmark_comparison_requires_matching_fixture_metadata() -> None:
    before = [{
        "fixture": "python",
        "fixture_version": "v1",
        "extractor_profile_hash": "profile-a",
        "ground_truth_status": "available",
        "relationship_precision": 0.5,
    }]
    after = [{
        "fixture": "python",
        "fixture_version": "v2",
        "extractor_profile_hash": "profile-a",
        "ground_truth_status": "available",
        "relationship_precision": 0.9,
    }]

    result = compare(before, after)

    assert result["python"] == {"status": "metadata_mismatch", "keys": ["fixture_version"]}


def test_semantic_benchmark_aggregation_preserves_unavailable_metrics() -> None:
    result = aggregate([{
        "fixture": "java",
        "fixture_version": "v1",
        "extractor_profile_hash": "profile-a",
        "ground_truth_status": "unavailable",
        "relationship_precision": None,
    }])

    assert result["java"]["ground_truth_status"] == "unavailable"
    assert result["java"]["relationship_precision"] is None
