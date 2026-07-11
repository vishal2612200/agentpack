from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Iterable

from agentpack.core.models import DependencyGraph
from agentpack.core.selection_models import CandidateEvidence, RankedCandidate


_TASK_STOPWORDS = {
    "add",
    "and",
    "change",
    "fix",
    "for",
    "from",
    "into",
    "make",
    "remove",
    "the",
    "this",
    "use",
    "with",
}
_RELEASE_NAMES = {
    "changelog.md",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "version.go",
}


def build_candidate_evidence(
    candidate: RankedCandidate,
    *,
    task: str,
    summary: Any,
    dependency_graph: DependencyGraph,
    changed_paths: set[str],
    memory_confirmed_paths: set[str] | None = None,
) -> CandidateEvidence:
    """Infer label-free ownership evidence while keeping safety protections orthogonal."""

    path = candidate.path
    reasons = tuple(reason.lower() for reason in candidate.legacy_reasons)
    summary_data = _summary_dict(summary)
    task_terms = _terms(task)
    path_terms = _terms(path)
    summary_terms = _summary_terms(summary_data)
    corroborated = _has_path_corroboration(reasons, task_terms, path_terms, summary_terms)
    codes: list[str] = []
    protections: list[str] = []
    owner_strength = 0
    support_strength = 0
    carrier_strength = 0

    definition = _has_reason(reasons, "matched define:", "multi-token defines match")
    literal_definition = _has_reason(reasons, "literal definition match:")
    entrypoint = _has_reason(reasons, "matched entrypoint:")
    role = _has_reason(reasons, "implementation role match", "matched role keyword:")

    if literal_definition and corroborated:
        owner_strength = 3
        _append(codes, "literal_definition_owner")
    elif entrypoint and corroborated:
        owner_strength = 3
        _append(codes, "entrypoint_owner")
    elif definition and corroborated:
        owner_strength = 3
        _append(codes, "definition_owner")
    elif role and corroborated:
        owner_strength = 2
        _append(codes, "role_owner")
    elif definition or literal_definition or entrypoint:
        owner_strength = 1
        _append(codes, "uncorroborated_owner_signal")

    node = dependency_graph.nodes.get(path)
    direct_dependency = _has_reason(reasons, "direct dependency")
    cross_layer = _has_reason(reasons, "cross-layer related")
    recall_neighbor = _has_reason(reasons, "recall neighbor of")
    if direct_dependency and node is not None and (node.imports or node.imported_by):
        support_strength = max(support_strength, 3)
        _append(codes, "dependency_support")
    elif direct_dependency or cross_layer:
        support_strength = max(support_strength, 2)
        _append(codes, "dependency_support")
    elif recall_neighbor:
        support_strength = max(support_strength, 1)
        _append(codes, "recall_neighbor")

    paired_test = (
        _is_test_path(path)
        and PurePosixPath(path.lower()).name not in {"__init__.py", "conftest.py"}
        and _has_reason(reasons, "test for high-scoring")
    )
    if paired_test:
        support_strength = max(support_strength, 3)
        _append(codes, "paired_test")

    if _has_reason(reasons, "matched call:", "call site"):
        carrier_strength = 3
        _append(codes, "call_site_carrier")
        if owner_strength == 0:
            support_strength = max(support_strength, 1)
            _append(codes, "caller_support")
    elif _has_reason(reasons, "keyword phrase match:", "quoted literal match:"):
        carrier_strength = 2
        _append(codes, "phrase_carrier")
    elif _has_reason(reasons, "content keyword match", "direct content evidence", "matched ranking keyword:"):
        carrier_strength = 1
        _append(codes, "content_carrier")

    memory_paths = memory_confirmed_paths or set()
    if path in changed_paths:
        _append(protections, "changed")
        owner_strength = max(owner_strength, 3)
        _append(codes, "changed_owner")
    if path in memory_paths or _has_reason(reasons, "episodic memory similar task", "learning feedback miss"):
        _append(protections, "memory_confirmed")
        owner_strength = max(owner_strength, 3)
        _append(codes, "memory_owner")
    release_signal = _has_reason(reasons, "release/version metadata", "build/dependency metadata")
    if _is_release_metadata(path) and release_signal:
        _append(protections, "release_metadata")
        if task_terms & {"release", "version", "dependency", "dependencies", "build"}:
            owner_strength = max(owner_strength, 2)
            _append(codes, "release_owner")
    if _is_generated_path(path):
        _append(protections, "generated")
    if _has_reason(reasons, "secret redaction candidate"):
        _append(protections, "redaction_sensitive")
    if (
        _explicit_test_task(task)
        and _is_test_path(path)
        and _has_reason(reasons, "explicit test task file")
    ):
        _append(protections, "explicit_task_test")
        owner_strength = max(owner_strength, 3)
        _append(codes, "explicit_test_owner")

    return CandidateEvidence(
        owner_strength=owner_strength,
        support_strength=support_strength,
        carrier_strength=carrier_strength,
        codes=tuple(codes),
        protections=tuple(protections),
    )


def _summary_dict(summary: Any) -> dict[str, Any]:
    if summary is None:
        return {}
    if isinstance(summary, dict):
        return summary
    dump = getattr(summary, "model_dump", None)
    return dump() if callable(dump) else {}


def _summary_terms(summary: dict[str, Any]) -> set[str]:
    values: list[str] = []
    for key in ("role", "domain", "defines", "entrypoints", "public_api", "ranking_keywords"):
        value = summary.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return _terms(" ".join(values))


def _has_path_corroboration(
    reasons: tuple[str, ...],
    task_terms: set[str],
    path_terms: set[str],
    summary_terms: set[str],
) -> bool:
    explicit = _has_reason(
        reasons,
        "filename keyword match",
        "conventional scope path match",
        "multi-term path match",
    )
    scoped_overlap = len(task_terms & path_terms) >= 1
    multi_source_overlap = len(task_terms & path_terms & summary_terms) >= 1
    return explicit or scoped_overlap or multi_source_overlap


def _terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", value.lower().replace("_", "-"))
        if len(term) >= 3 and term not in _TASK_STOPWORDS
    }


def _has_reason(reasons: Iterable[str], *prefixes: str) -> bool:
    return any(any(prefix in reason for prefix in prefixes) for reason in reasons)


def _append(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    return (
        lowered.startswith(("test/", "tests/", "integration/"))
        or "/test/" in lowered
        or "/tests/" in lowered
        or "/__tests__/" in lowered
        or name.endswith(("_test.go", "_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
        or "test" in PurePosixPath(lowered).stem
    )


def _explicit_test_task(task: str) -> bool:
    lowered = task.strip().lower()
    return (
        lowered.startswith(("test", "add test", "add missing validation test"))
        or "regression test" in lowered
        or "(test)" in lowered
        or "refactor(test)" in lowered
    )


def _is_release_metadata(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    return name in _RELEASE_NAMES or "changelog" in name or "version" in name


def _is_generated_path(path: str) -> bool:
    parts = set(PurePosixPath(path.lower()).parts)
    return bool(parts & {"build", "coverage", "dist", "generated", "__generated__", "vendor"})
