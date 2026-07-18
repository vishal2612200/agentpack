from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from agentpack.architecture.compat import LegacyGraphQuery
from agentpack.architecture.index import SemanticGraphIndex
from agentpack.core.models import ContextPack, DependencyGraph, OmittedRelevantFile, SelectedFile
from agentpack.core.pack_registry import PackRegistry


RiskLevel = Literal["low", "medium", "high"]
TaskMapKind = Literal["selected", "omitted"]


class TaskMapFile(BaseModel):
    path: str
    kind: TaskMapKind
    include_mode: str = ""
    score: float = 0.0
    tokens: int = 0
    why_selected: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    tests_to_run: list[str] = Field(default_factory=list)
    may_break: list[str] = Field(default_factory=list)
    retrieve_ref: str = ""


class TaskMap(BaseModel):
    schema_version: int = 1
    task: str
    generated_at: str = ""
    files: list[TaskMapFile] = Field(default_factory=list)
    risk_counts: dict[str, int] = Field(default_factory=dict)
    tests_to_run: list[str] = Field(default_factory=list)
    high_risk_files: list[str] = Field(default_factory=list)


class GraphQuery(Protocol):
    def file_relations(self, path: str) -> dict[str, list[str]]: ...
    def relationship_receipts(self, path: str, *, limit: int = 50) -> list[dict]: ...


def build_task_map(pack: ContextPack, graph: SemanticGraphIndex, registry: PackRegistry) -> TaskMap:
    """Build a task map from the canonical semantic graph."""
    if isinstance(graph, DependencyGraph):
        return build_task_map_legacy(pack, graph, registry)
    return _build_task_map(pack, graph, registry)


def build_task_map_legacy(pack: ContextPack, graph: DependencyGraph, registry: PackRegistry) -> TaskMap:
    """Compatibility boundary for callers that still provide DependencyGraph."""
    return _build_task_map(pack, LegacyGraphQuery(graph), registry)


def _build_task_map(pack: ContextPack, graph: GraphQuery, registry: PackRegistry) -> TaskMap:
    records = {
        (record.kind, record.path): record
        for record in registry.records
        if not record.symbol
    }
    files: list[TaskMapFile] = []
    for selected in pack.selected_files:
        record = records.get(("selected", selected.path))
        risk_level, risk_reasons = _selected_risk(selected, graph)
        tests = _tests_for(selected.path, graph)
        files.append(
            TaskMapFile(
                path=selected.path,
                kind="selected",
                include_mode=selected.include_mode,
                score=round(selected.score, 1),
                tokens=record.tokens if record else _estimated_selected_tokens(selected),
                why_selected=_why(selected.reasons),
                risk_level=risk_level,
                risk_reasons=risk_reasons,
                tests_to_run=tests,
                may_break=_may_break(selected.path, graph),
                retrieve_ref=record.block_id if record else "",
            )
        )
    for omitted in pack.omitted_relevant_files:
        record = records.get(("omitted", omitted.path))
        files.append(
            TaskMapFile(
                path=omitted.path,
                kind="omitted",
                include_mode=omitted.suggested_mode,
                score=round(omitted.score, 1),
                tokens=record.tokens if record else omitted.estimated_tokens,
                why_selected=_why(omitted.reasons or [omitted.omission_reason]),
                risk_level=omitted.risk,
                risk_reasons=_omitted_risk_reasons(omitted),
                tests_to_run=_tests_for(omitted.path, graph),
                may_break=_may_break(omitted.path, graph, omitted=True),
                retrieve_ref=record.block_id if record else "",
            )
        )

    counts = Counter(item.risk_level for item in files)
    tests_to_run = sorted({test for item in files for test in item.tests_to_run})
    high_risk = [item.path for item in files if item.risk_level == "high"]
    return TaskMap(
        task=pack.task,
        generated_at=str(pack.freshness.get("generated_at") or ""),
        files=files,
        risk_counts={level: counts.get(level, 0) for level in ("high", "medium", "low")},
        tests_to_run=tests_to_run,
        high_risk_files=high_risk,
    )


def task_map_for_path(task_map: dict[str, object], path: str, kind: str | None = None) -> dict[str, object]:
    for item in task_map.get("files", []) if isinstance(task_map, dict) else []:
        if not isinstance(item, dict):
            continue
        if item.get("path") != path:
            continue
        if kind and item.get("kind") != kind:
            continue
        return item
    return {}


def _selected_risk(selected: SelectedFile, graph: GraphQuery) -> tuple[RiskLevel, list[str]]:
    score = 0
    reasons: list[str] = []
    path = selected.path
    reason_text = " ".join(selected.reasons).lower()
    path_text = path.lower()
    relations = _file_relations(graph, path)

    if any(marker in reason_text for marker in ("modified", "staged", "recently modified", "github pr file")):
        score += 2
        reasons.append("changed in current working context")
    if _critical_path(path_text) or _critical_reason(reason_text):
        score += 3
        reasons.append("touches contract, security, deploy, data, or API surface")
    if len(relations["imported_by"]) >= 5:
        score += 3
        reasons.append(_dependency_reason(graph, path, len(relations["imported_by"])))
    elif len(relations["imported_by"]) >= 2:
        score += 2
        reasons.append(_dependency_reason(graph, path, len(relations["imported_by"])))
    if _looks_like_source(path) and not _tests_for(path, graph):
        score += 1
        reasons.append("no related tests found in dependency map")
    if selected.include_mode == "summary":
        score += 1
        reasons.append("summary-only context")

    if score >= 5:
        return "high", reasons[:4]
    if score >= 2:
        return "medium", reasons[:4]
    return "low", reasons[:4] or ["localized selected context"]


def _omitted_risk_reasons(omitted: OmittedRelevantFile) -> list[str]:
    reasons: list[str] = []
    if omitted.risk == "high":
        reasons.append("high-risk omitted relevant file")
    elif omitted.risk == "medium":
        reasons.append("medium-risk omitted relevant file")
    if omitted.omission_reason:
        reasons.append(omitted.omission_reason)
    reasons.extend(omitted.reasons[:2])
    return reasons[:4] or ["omitted due to context budget"]


def _tests_for(path: str, graph: GraphQuery) -> list[str]:
    if _looks_like_test(path):
        return [path]
    tests = [test for test in _file_relations(graph, path)["tests"] if _looks_like_test(test)]
    return sorted(dict.fromkeys(tests))[:5]


def _may_break(path: str, graph: GraphQuery, *, omitted: bool = False) -> list[str]:
    relations = _file_relations(graph, path)
    impacts: list[str] = []
    if omitted:
        impacts.append("selected change may depend on this omitted file")
    if relations["imported_by"]:
        shown = ", ".join(relations["imported_by"][:4])
        suffix = f"; +{len(relations['imported_by']) - 4} more" if len(relations["imported_by"]) > 4 else ""
        impacts.append(f"reverse dependents: {shown}{suffix}")
    if _critical_path(path.lower()):
        impacts.append("shared contract/config/runtime path")
    return impacts[:4]


def _file_relations(graph: GraphQuery, path: str) -> dict[str, list[str]]:
    return graph.file_relations(path)


def _dependency_reason(graph: GraphQuery, path: str, count: int) -> str:
    receipts = [
        row for row in graph.relationship_receipts(path, limit=20)
        if row["relationship"] == "imports" and row["target"] == path
    ]
    evidence = next((item for row in receipts for item in row["evidence"] if item.get("path")), None)
    if evidence:
        location = evidence["path"]
        if evidence.get("start_line"):
            location += f":{evidence['start_line']}"
        receipt = receipts[0]
        return (
            f"{count} reverse dependents ({receipt['relationship']} "
            f"{receipt.get('source_entity_key', '')}->{receipt.get('target_entity_key', '')}; "
            f"edge {receipt['edge_key']} at {location}; "
            f"confidence {receipt.get('confidence_tier', 'unknown')}; "
            f"evidence {receipt.get('evidence_reference', '')})"
        )
    return f"{count} reverse dependents"


def _why(reasons: list[str]) -> list[str]:
    return [str(reason) for reason in reasons[:4] if str(reason).strip()]


def _estimated_selected_tokens(selected: SelectedFile) -> int:
    if selected.content:
        return max(1, len(selected.content) // 4)
    if selected.summary:
        return max(1, len(selected.summary) // 4)
    return 0


def _critical_path(path_text: str) -> bool:
    parts = set(PurePosixPath(path_text).parts)
    name = PurePosixPath(path_text).name
    stem_tokens = set(name.rsplit(".", 1)[0].replace("-", "_").split("_"))
    critical_terms = {
        "auth",
        "security",
        "billing",
        "payment",
        "payments",
        "migration",
        "migrations",
        "schema",
        "schemas",
        "model",
        "models",
        "api",
        "routes",
        "controllers",
        "config",
        "deploy",
        "workflow",
        "workflows",
        "docker",
    }
    if parts & critical_terms or stem_tokens & critical_terms:
        return True
    return name in {"dockerfile", "pyproject.toml", "package.json"} or name.endswith((".yml", ".yaml"))


def _critical_reason(reason_text: str) -> bool:
    return any(
        token in reason_text
        for token in (
            "api route",
            "endpoint",
            "schema",
            "config",
            "deploy",
            "secret",
            "external system",
            "side effect",
            "release/version metadata",
            "build/dependency metadata",
        )
    )


def _looks_like_test(path: str) -> bool:
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    return (
        lower.startswith(("tests/", "test/"))
        or "/tests/" in lower
        or "/__tests__/" in lower
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".test.js", ".spec.js"))
    )


def _looks_like_source(path: str) -> bool:
    return not _looks_like_test(path) and path.lower().endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt", ".rs"))
