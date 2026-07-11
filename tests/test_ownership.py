from pathlib import Path

from agentpack.analysis.ownership import build_candidate_evidence
from agentpack.core.models import DependencyGraph, DependencyNode, FileInfo
from agentpack.core.selection_models import adapt_ranked_candidate


def _candidate(path: str, reasons: list[str]):
    info = FileInfo(
        path=path,
        abs_path=Path(path),
        size_bytes=100,
        estimated_tokens=25,
    )
    return adapt_ranked_candidate(info, 100.0, reasons)


def test_definition_owner_requires_path_or_scope_corroboration() -> None:
    without_path = build_candidate_evidence(
        _candidate("src/misc.py", ["matched define: authenticate"]),
        task="fix auth token validation",
        summary={"defines": ["authenticate"]},
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
    )
    with_path = build_candidate_evidence(
        _candidate(
            "src/auth/token.py",
            ["matched define: authenticate", "conventional scope path match"],
        ),
        task="fix auth token validation",
        summary={"defines": ["authenticate"]},
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
    )

    assert without_path.owner_strength == 1
    assert with_path.owner_strength == 3
    assert "definition_owner" in with_path.codes


def test_literal_definition_owner_keeps_carrier_evidence_independent() -> None:
    evidence = build_candidate_evidence(
        _candidate(
            "src/click/types.py",
            [
                "literal definition match: convert type",
                "multi-term path match +70",
                "content keyword match (2)",
            ],
        ),
        task="extract convert type helper",
        summary={"defines": ["convert_type"]},
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
    )

    assert evidence.owner_strength == 3
    assert evidence.carrier_strength == 1
    assert "literal_definition_owner" in evidence.codes


def test_dependency_and_paired_test_signals_build_support_evidence() -> None:
    graph = DependencyGraph(
        nodes={
            "tests/test_auth.py": DependencyNode(
                path="tests/test_auth.py",
                imports=["src/auth.py"],
            )
        }
    )
    evidence = build_candidate_evidence(
        _candidate(
            "tests/test_auth.py",
            ["test for high-scoring src/auth.py", "direct dependency of changed file"],
        ),
        task="fix auth token validation",
        summary={"test_hints": ["src/auth.py"]},
        dependency_graph=graph,
        changed_paths=set(),
    )

    assert evidence.support_strength == 3
    assert "paired_test" in evidence.codes
    assert "dependency_support" in evidence.codes


def test_protected_signals_are_deterministic_and_do_not_need_labels() -> None:
    candidate = _candidate(
        "tests/test_release.py",
        ["explicit test task file", "release/version metadata", "secret redaction candidate"],
    )
    kwargs = {
        "task": "test release token redaction",
        "summary": {"role": "test"},
        "dependency_graph": DependencyGraph(),
        "changed_paths": {"tests/test_release.py"},
        "memory_confirmed_paths": {"tests/test_release.py"},
    }

    first = build_candidate_evidence(candidate, **kwargs)
    second = build_candidate_evidence(candidate, **kwargs)

    assert first == second
    assert first.owner_strength == 3
    assert first.protections == (
        "changed",
        "memory_confirmed",
        "redaction_sensitive",
        "explicit_task_test",
    )


def test_direct_release_metadata_signal_is_protected() -> None:
    evidence = build_candidate_evidence(
        _candidate("pyproject.toml", ["release/version metadata"]),
        task="start version 3.1.0",
        summary={},
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
    )

    assert "release_metadata" in evidence.protections


def test_generated_paths_are_protected() -> None:
    evidence = build_candidate_evidence(
        _candidate("src/generated/client.py", ["content keyword match (1)"]),
        task="fix generated client",
        summary={},
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
    )

    assert "generated" in evidence.protections


def test_unrelated_nested_package_metadata_is_not_protected() -> None:
    evidence = build_candidate_evidence(
        _candidate("fixtures/invalid/package.json", ["config file"]),
        task="fix ssr stacktrace column",
        summary={},
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
    )

    assert "release_metadata" not in evidence.protections


def test_recall_neighbor_is_weak_support_not_a_direct_dependency() -> None:
    evidence = build_candidate_evidence(
        _candidate("src/neighbor.py", ["recall neighbor of src/owner.py"]),
        task="fix owner behavior",
        summary={},
        dependency_graph=DependencyGraph(
            nodes={
                "src/neighbor.py": DependencyNode(
                    path="src/neighbor.py",
                    imports=["src/owner.py"],
                )
            }
        ),
        changed_paths=set(),
    )

    assert evidence.support_strength == 1
    assert evidence.codes == ("recall_neighbor",)


def test_test_package_initializer_is_not_paired_support() -> None:
    evidence = build_candidate_evidence(
        _candidate("tests/__init__.py", ["test for high-scoring src/owner.py"]),
        task="remove deprecated owner behavior",
        summary={},
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
    )

    assert evidence.support_strength == 0


def test_unrelated_test_path_is_not_protected_by_task_type_alone() -> None:
    evidence = build_candidate_evidence(
        _candidate("playground/demo/__test__/serve.ts", ["content keyword match (1)"]),
        task="test: avoid scanner warnings",
        summary={},
        dependency_graph=DependencyGraph(),
        changed_paths=set(),
    )

    assert "explicit_task_test" not in evidence.protections
