from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from agentpack.analysis.ranking import KeywordPlan
from agentpack.core.selection_models import (
    OwnerCaseContext,
    OwnerFeatureVector,
    RankedCandidate,
)


_ANCHOR_REASON_PREFIXES = {
    "definition": ("matched define:", "matched definition:", "multi-token defines match"),
    "entrypoint": ("matched entrypoint:",),
    "literal_definition": ("literal definition match:",),
}
_GENERIC_OBJECTS = {
    "add",
    "build",
    "change",
    "chore",
    "config",
    "file",
    "fix",
    "implementation",
    "release",
    "remove",
    "test",
    "update",
}
_GENERIC_DEFINITIONS = {
    "build",
    "config",
    "create",
    "get",
    "handle",
    "index",
    "init",
    "load",
    "main",
    "options",
    "plugin",
    "process",
    "run",
    "setup",
    "transform",
}
_METADATA_NAMES = {"package.json", "pom.xml", "pyproject.toml"}


def build_owner_case_context(
    task: str,
    keyword_plan: KeywordPlan,
    candidates: Sequence[RankedCandidate],
    summaries: Mapping[str, Any],
) -> OwnerCaseContext:
    """Build comparative anchor counts without consulting benchmark labels."""

    del task  # The keyword plan is the ranking pipeline's normalized task contract.
    literal_phrases = tuple(dict.fromkeys(_normalize(value) for value in keyword_plan.literal_phrases if _normalize(value)))
    task_objects = tuple(dict.fromkeys(
        value
        for value in (
            *literal_phrases,
            *(_normalize(term) for term in keyword_plan.concrete_terms),
            *(_normalize(term) for term in keyword_plan.task_scope_terms),
        )
        if value and value not in _GENERIC_OBJECTS
    ))
    counts: Counter[str] = Counter()
    for candidate in candidates[:50]:
        summary = _summary_dict(summaries.get(candidate.path))
        for kind, value in _candidate_anchors(candidate, summary):
            if _matches_any(value, task_objects):
                counts[f"{kind}:{value}"] += 1

    return OwnerCaseContext(
        task_objects=task_objects,
        scope_terms=tuple(dict.fromkeys(_normalize(term) for term in keyword_plan.task_scope_terms if _normalize(term))),
        literal_phrases=literal_phrases,
        anchor_counts=tuple(sorted(counts.items())),
        candidate_count=len(candidates),
    )


def extract_owner_features(
    candidate: RankedCandidate,
    summary: Any,
    context: OwnerCaseContext,
) -> OwnerFeatureVector:
    """Extract direct anchors, independent corroboration groups, and penalties."""

    summary_data = _summary_dict(summary)
    anchors = _candidate_anchors(candidate, summary_data)
    path = candidate.path.lower()
    path_terms = _terms(path)
    filename_terms = _terms(PurePosixPath(path).stem)
    matched_objects = tuple(sorted(
        task_object
        for task_object in context.task_objects
        if _object_matches_terms(task_object, path_terms)
        or any(_values_match(task_object, value) for _kind, value in anchors)
    ))
    direct_reason_kinds = {
        kind
        for kind, prefixes in _ANCHOR_REASON_PREFIXES.items()
        if any(prefix in reason.lower() for reason in candidate.legacy_reasons for prefix in prefixes)
    }
    anchor_codes = tuple(dict.fromkeys(
        kind
        for kind, value in anchors
        if kind in direct_reason_kinds or _matches_any(value, context.task_objects)
    ))

    corroboration: list[str] = []
    if any(_object_matches_terms(value, filename_terms) for value in context.task_objects):
        corroboration.append("filename_task_object")
    if any(_object_matches_terms(value, path_terms) for value in context.task_objects):
        corroboration.append("path_task_object")
    if context.scope_terms and set(context.scope_terms) <= path_terms:
        corroboration.append("scope_path")
    for field, code in (
        ("defines", "summary_definition"),
        ("public_api", "summary_public_api"),
        ("entrypoints", "summary_entrypoint"),
    ):
        if any(_matches_any(value, context.task_objects) for value in _summary_values(summary_data, field)):
            corroboration.append(code)
    role_values = _summary_values(summary_data, "role")
    if role_values and any(_terms(role) & path_terms for role in role_values):
        corroboration.append("summary_role_path")

    penalties: list[str] = []
    reasons = tuple(reason.lower() for reason in candidate.legacy_reasons)
    if not anchor_codes and any("matched call:" in reason or "call site" in reason for reason in reasons):
        penalties.append("call_site_only")
    if _is_test_path(path) and "explicit test task file" not in reasons:
        penalties.append("broad_test_match")
    if _is_generated_docs_or_example(path):
        penalties.append("generated_docs_example")
    if context.scope_terms and not set(context.scope_terms) <= path_terms:
        penalties.append("scope_mismatch")
    if PurePosixPath(path).name in _METADATA_NAMES and len(PurePosixPath(path).parts) > 2:
        penalties.append("unrelated_metadata")

    count_map = dict(context.anchor_counts)
    competing_count = max(
        (count_map.get(f"{kind}:{value}", 0) for kind, value in anchors if kind in anchor_codes),
        default=0,
    )
    anchor_values = [value for kind, value in anchors if kind in anchor_codes]
    if competing_count > 1 or any(value in _GENERIC_DEFINITIONS for value in anchor_values):
        penalties.append("non_unique_definition")
    if not matched_objects and ("path_task_object" not in corroboration):
        penalties.append("generic_path")

    return OwnerFeatureVector(
        anchor_codes=anchor_codes,
        corroboration_codes=tuple(dict.fromkeys(corroboration)),
        penalty_codes=tuple(dict.fromkeys(penalties)),
        matched_task_objects=matched_objects,
        competing_anchor_count=competing_count,
    )


def _candidate_anchors(candidate: RankedCandidate, summary: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    anchors: list[tuple[str, str]] = []
    for reason in candidate.legacy_reasons:
        lowered = reason.lower()
        for kind, prefixes in _ANCHOR_REASON_PREFIXES.items():
            for prefix in prefixes:
                if prefix not in lowered:
                    continue
                value = lowered.split(prefix, 1)[1].lstrip(" +0123456789:")
                value = value.split(" +", 1)[0]
                if normalized := _normalize(value):
                    anchors.append((kind, normalized))
                break
    for field, kind in (("defines", "definition"), ("public_api", "definition"), ("entrypoints", "entrypoint")):
        anchors.extend((kind, value) for value in _summary_values(summary, field))
    reasons = "\n".join(candidate.legacy_reasons).lower()
    if "implementation role match" in reasons or "matched role keyword:" in reasons:
        anchors.extend(("role", value) for value in _summary_values(summary, "role"))
    return tuple(dict.fromkeys(anchors))


def _summary_dict(summary: Any) -> dict[str, Any]:
    if summary is None:
        return {}
    if isinstance(summary, dict):
        return summary
    dump = getattr(summary, "model_dump", None)
    return dump() if callable(dump) else {}


def _summary_values(summary: dict[str, Any], field: str) -> tuple[str, ...]:
    raw = summary.get(field)
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else []
    return tuple(value for item in values if (value := _normalize(str(item))))


def _normalize(value: str) -> str:
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return " ".join(re.findall(r"[a-z0-9]+", split.lower().replace("_", "-").replace("/", " ")))


def _terms(value: str) -> set[str]:
    return set(_normalize(value).split())


def _values_match(left: str, right: str) -> bool:
    left_terms = _terms(left)
    right_terms = _terms(right)
    return bool(left_terms and right_terms and (left_terms <= right_terms or right_terms <= left_terms))


def _matches_any(value: str, task_objects: Sequence[str]) -> bool:
    return any(_values_match(value, task_object) for task_object in task_objects)


def _object_matches_terms(task_object: str, terms: set[str]) -> bool:
    object_terms = _terms(task_object)
    return bool(object_terms and object_terms <= terms)


def _is_test_path(path: str) -> bool:
    parts = set(PurePosixPath(path).parts)
    name = PurePosixPath(path).name
    return bool(parts & {"test", "tests", "__tests__"}) or name.endswith(
        ("_test.go", "_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
    )


def _is_generated_docs_or_example(path: str) -> bool:
    parts = set(PurePosixPath(path).parts)
    return bool(parts & {"build", "dist", "docs", "examples", "fixtures", "generated", "vendor"})
