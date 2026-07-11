from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Iterable

from agentpack.core.models import DependencyGraph
from agentpack.core.selection_models import (
    CandidateEvidence,
    OwnerCaseContext,
    OwnerFeatureVector,
    RankedCandidate,
)


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
    owner_context: OwnerCaseContext,
    owner_features: OwnerFeatureVector,
    dependency_graph: DependencyGraph,
    changed_paths: set[str],
    memory_confirmed_paths: set[str] | None = None,
) -> CandidateEvidence:
    """Infer label-free ownership evidence while keeping safety protections orthogonal."""

    path = candidate.path
    reasons = tuple(reason.lower() for reason in candidate.legacy_reasons)
    task_terms = _terms(task)
    codes: list[str] = []
    protections: list[str] = []
    owner_strength = 0
    support_strength = 0
    carrier_strength = 0

    owner_strength, owner_codes = _classify_owner_features(owner_context, owner_features)
    codes.extend(owner_codes)

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
    release_intent = bool(task_terms & {"release", "version", "dependency", "dependencies", "build"})
    if _is_direct_release_metadata(path) and release_signal and release_intent:
        _append(protections, "release_metadata")
        owner_strength = max(owner_strength, 3)
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


def _classify_owner_features(
    context: OwnerCaseContext,
    features: OwnerFeatureVector,
) -> tuple[int, list[str]]:
    anchors = set(features.anchor_codes)
    corroboration = set(features.corroboration_codes)
    penalties = set(features.penalty_codes)
    direct_anchors = anchors & {"definition", "literal_definition", "entrypoint"}
    path_scope = bool(corroboration & {"filename_task_object", "path_task_object", "scope_path"})
    summary = bool(corroboration & {"summary_definition", "summary_public_api", "summary_entrypoint"})
    unique = features.competing_anchor_count == 1 and "non_unique_definition" not in penalties
    scope_ok = "scope_mismatch" not in penalties

    if "broad_test_match" in penalties:
        return (1, ["broad_test_match"]) if direct_anchors else (0, ["broad_test_match"])
    if "literal_definition" in anchors and unique and scope_ok and (path_scope or summary):
        return 3, ["literal_task_object_owner"]
    if "entrypoint" in anchors and unique and scope_ok and (path_scope or summary):
        return 3, ["unique_entrypoint_owner"]
    if "definition" in anchors and unique and scope_ok and path_scope and summary:
        return 3, ["unique_definition_owner"]
    if direct_anchors and not unique and sum((path_scope, summary)) >= 2 and scope_ok:
        return 2, ["non_unique_definition"]
    if "role" in anchors and path_scope and "summary_role_path" in corroboration and scope_ok:
        return 2, ["role_path_owner"]
    if direct_anchors:
        codes = ["direct_owner_anchor"]
        if "non_unique_definition" in penalties:
            codes.append("non_unique_definition")
        if "scope_mismatch" in penalties:
            codes.append("scope_mismatch")
        return 1, codes
    if "call_site_only" in penalties:
        return 0, ["call_site_only"]
    if "generic_path" in penalties:
        return 0, ["generic_path"]
    return 0, []


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


def _is_direct_release_metadata(path: str) -> bool:
    return _is_release_metadata(path) and len(PurePosixPath(path).parts) <= 2


def _is_generated_path(path: str) -> bool:
    parts = set(PurePosixPath(path.lower()).parts)
    return bool(parts & {"build", "coverage", "dist", "generated", "__generated__", "vendor"})
