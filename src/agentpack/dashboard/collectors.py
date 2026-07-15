from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from agentpack.core import git
from agentpack.core.config import DEFAULT_CONFIG, load_config
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.loop_protocol import load_loop_state
from agentpack.core.mcp_runtime import check_mcp_runtime
from agentpack.core.project_index import load_project_index
from agentpack.core.task_freshness import task_freshness
from agentpack.core.thread_context import list_thread_rows
from agentpack.architecture.service import build_snapshot_for_ref
from agentpack.dashboard.models import (
    ArtifactRow,
    BenchmarkSummary,
    CommandCatalogItem,
    ContextHealth,
    DashboardConfigField,
    DashboardConfigSection,
    DashboardConfigSummary,
    DashboardSnapshot,
    IntegrationFileRow,
    LearningArtifact,
    LearningMemory,
    LearningWeakSpot,
    McpHealth,
    McpRegistration,
    ObserverInsightRow,
    ObserverSummary,
    LoopSummary,
    ProjectInfo,
    ProjectCandidate,
    SelectedFileRow,
    SkillFeedbackStatus,
    SkillDomainSummary,
    SkillInventoryRow,
    SkillInventorySourceSummary,
    SkillRow,
    SkillSection,
    SkillsInventorySummary,
    SuggestedAction,
    TaskControlRow,
    TaskHistoryRow,
    TaskInfo,
    TaskMapFileRow,
    ThreadRow,
    ThreadSummary,
    SemanticGraphSummary,
)
from agentpack.dashboard.project_state import sync_dashboard_state
from agentpack.learning.sessions import summarize_weak_spots
from agentpack.learning.task_memory import recent_task_memories, recent_task_start_snapshots
from agentpack.mcp_server import MCP_TOOL_NAMES
from agentpack.observer.brief import build_observer_brief
from agentpack.router.models import SkillArtifact
from agentpack.router.skills_index import ensure_inventory_index


MAX_JSONL_ROWS = 500
MAX_RECENT_FEEDBACK = 20
MAX_EXCERPT_CHARS = 1200
MAX_REASONS = 5
MAX_MISSES = 20
MAX_SKILL_INVENTORY_ROWS = 100
MAX_METADATA_ITEMS = 8
MAX_INFERRED_DOMAINS = 3
MIN_BM25_DOMAIN_SCORE = 1.0
CONFIG_SECTIONS = (
    "project",
    "context",
    "context_lite",
    "summary",
    "learning",
    "runtime",
    "loop",
    "hooks",
    "skills",
    "agentic",
    "agents",
    "scoring",
)
EDITABLE_CONFIG_FIELDS = {
    "context.default_budget",
    "context.default_mode",
    "context.include_tests",
    "context.include_configs",
    "context.include_receipts",
    "context.broad_context",
    "context.memory_feedback",
    "context_lite.budget",
    "context_lite.max_selected_files",
    "context_lite.max_omitted_files",
    "loop.enabled",
    "loop.runner",
    "loop.runner_adapter",
    "loop.verification_commands",
    "loop.require_verification",
    "loop.max_iterations",
    "hooks.task_switch_detection",
    "hooks.blocking_task_refresh",
    "skills.paths",
    "skills.max_selected",
    "skills.always_recommend",
    "skills.allow_external_side_effects",
}
CONFIG_FIELD_DOCS: dict[str, dict[str, Any]] = {
    "context.default_budget": {
        "description": "Token budget per normal context pack.",
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context.default_mode": {
        "description": "Default context packing mode.",
        "allowed_values": ["lite", "balanced", "deep"],
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context.include_tests": {
        "description": "Include test files as eligible context candidates.",
        "allowed_values": ["true", "false"],
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context.include_configs": {
        "description": "Include config and settings files as eligible context candidates.",
        "allowed_values": ["true", "false"],
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context.include_receipts": {
        "description": "Include local AgentPack receipts when they help explain context selection.",
        "allowed_values": ["true", "false"],
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context.broad_context": {
        "description": "Controls curated repo-wide inventory for reviews, audits, and repository overviews.",
        "allowed_values": ["auto", "off", "on"],
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context.memory_feedback": {
        "description": "Allows prior ranking feedback and episodic eval outcomes to provide small ranking boosts.",
        "allowed_values": ["auto", "off"],
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context_lite.budget": {
        "description": "Token budget used by lite context packs.",
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context_lite.max_selected_files": {
        "description": "Maximum selected files in lite mode.",
        "doc_ref": "docs/configuration.md#configuration",
    },
    "context_lite.max_omitted_files": {
        "description": "Maximum omitted candidates surfaced in lite mode.",
        "doc_ref": "docs/configuration.md#configuration",
    },
    "loop.enabled": {
        "description": "Enables the optional guarded local runner loop.",
        "allowed_values": ["true", "false"],
        "doc_ref": "docs/configuration.md#loop",
    },
    "loop.runner": {
        "description": "Local command used by agentpack work --run. Empty means no runner is configured.",
        "doc_ref": "docs/configuration.md#loop",
    },
    "loop.runner_adapter": {
        "description": "Optional adapter that resolves a local runner command when the matching executable is present.",
        "allowed_values": ["", "claude", "codex", "cursor"],
        "doc_ref": "docs/configuration.md#loop",
    },
    "loop.verification_commands": {
        "description": "Commands that prove a loop iteration or finish state.",
        "doc_ref": "docs/configuration.md#loop",
    },
    "loop.require_verification": {
        "description": "Requires verification evidence before loop completion.",
        "allowed_values": ["true", "false"],
        "doc_ref": "docs/configuration.md#loop",
    },
    "loop.max_iterations": {
        "description": "Maximum loop iterations before the task is considered blocked.",
        "doc_ref": "docs/configuration.md#loop",
    },
    "hooks.task_switch_detection": {
        "description": "Lets prompt hooks detect clearly different coding prompts when a real task exists.",
        "allowed_values": ["true", "false"],
        "doc_ref": "docs/configuration.md#configuration",
    },
    "hooks.blocking_task_refresh": {
        "description": "When enabled, prompt-submit hooks may block for a fresh pack if context is stale.",
        "allowed_values": ["true", "false"],
        "doc_ref": "docs/configuration.md#configuration",
    },
    "skills.paths": {
        "description": "Skill and rule source paths used by route and MCP route_task.",
        "doc_ref": "docs/configuration.md#configuration",
    },
    "skills.max_selected": {
        "description": "Maximum task-specific skills selected for a route.",
        "doc_ref": "docs/configuration.md#configuration",
    },
    "skills.always_recommend": {
        "description": "Skill keys that should be recommended for relevant coding tasks.",
        "doc_ref": "docs/configuration.md#configuration",
    },
    "skills.allow_external_side_effects": {
        "description": "Allows skills marked as external side-effecting to be recommended.",
        "allowed_values": ["true", "false"],
        "doc_ref": "docs/configuration.md#configuration",
    },
}

_DOMAIN_CORPUS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("career", ("academic", "ats", "career", "cover-letter", "cv", "interview", "job", "linkedin", "offer", "portfolio", "reference", "resume", "salary")),
    ("testing", ("ab-test", "cypress", "junit", "playwright", "pytest", "qa", "regression", "tdd", "test", "testing")),
    ("ai", ("agent", "ai", "anthropic", "claude", "codex", "embedding", "llm", "openai", "prompt", "rag")),
    ("agent tooling", ("agentpack", "atlassian", "gmail", "google-drive", "linear", "mcp", "plugin", "tool")),
    ("android", ("android", "emulator", "gradle", "jetpack", "kotlin")),
    ("ios", ("appkit", "ios", "macos", "swift", "swiftui", "xcode")),
    ("frontend", ("angular", "component", "css", "frontend", "html", "react", "svelte", "ui", "ux", "vue", "web")),
    ("backend", ("backend", "database", "django", "express", "fastapi", "flask", "node", "server")),
    ("api", ("api", "graphql", "openapi", "rest", "webhook")),
    ("architecture", ("architect", "architecture", "clean", "ddd", "design", "pattern", "patterns", "refactor")),
    ("debugging", ("bug", "debug", "debugging", "stack-trace")),
    ("devops", ("ci", "cloud", "deploy", "docker", "kubernetes", "release")),
    ("data", ("analytics", "data", "dataset", "dbt", "spreadsheet", "sql", "warehouse")),
    ("security", ("auth", "oauth", "privacy", "secret", "security", "threat")),
    ("product", ("gtm", "marketing", "prd", "pricing", "product", "strategy")),
    ("documentation", ("article", "docs", "documentation", "readme", "writing")),
    ("cpp", ("c++", "cmake", "cpp", "ctest", "googletest", "gtest")),
    ("java", ("java", "spring")),
    ("python", ("pandas", "python")),
    ("php", ("laravel", "php", "symfony")),
    ("embedded", ("embedded", "firmware", "microcontroller", "rtos")),
    ("finance", ("banknifty", "bse", "nifty", "nse", "stock", "stocks", "trading")),
)

_DOMAIN_DOCUMENT_FREQUENCY = Counter(
    term
    for _domain, terms in _DOMAIN_CORPUS
    for term in set(terms)
)


def build_project_dashboard_snapshot(root: Path) -> DashboardSnapshot:
    root = root.resolve()
    agentpack_dir = root / ".agentpack"
    meta = load_pack_metadata(root) if agentpack_dir.exists() else None
    task_text = _read_task(agentpack_dir / "task.md") or str((meta or {}).get("task") or "")
    freshness = task_freshness(root, meta) if meta else None
    context = _context_health(meta, freshness)
    selected_files = _selected_files(meta)
    feedback_rows = _load_jsonl(agentpack_dir / "skill_feedback.jsonl")
    skill_section = _skill_section(meta, feedback_rows)
    skills_inventory = _skills_inventory_summary(root, initialized=agentpack_dir.exists())
    learning = _learning_artifacts(agentpack_dir)
    learning_memories = _learning_memories(root)
    learning_weak_spots = _learning_weak_spots(root)
    observer = _observer_summary(root, task_text)
    benchmarks = _benchmark_summary(
        _load_jsonl(agentpack_dir / "metrics.jsonl"),
        _load_jsonl(agentpack_dir / "benchmark_results.jsonl"),
    )
    threads = _thread_summary(root, meta)
    mcp_health = _mcp_health(root)
    loop = _loop_summary(root)
    actions = _suggested_actions(agentpack_dir, task_text, context, learning, benchmarks, feedback_rows)
    config = _config_summary(root)
    thread_rows = _thread_rows(root, meta)
    task_control = _task_control_rows(root, meta)
    task_history = _task_history_rows(root, task_control, thread_rows)
    projects = _project_candidates(root, thread_rows, task_history)
    semantic_graph = _semantic_graph_summary(root)

    snapshot = DashboardSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        project=_project_info(root, meta),
        task=TaskInfo(
            text=task_text,
            state=_task_state(agentpack_dir / "task_state.md"),
            thread_id=_thread_id(meta),
        ),
        context=context,
        selected_files=selected_files,
        task_map=_task_map_files(meta),
        skills=skill_section,
        skills_inventory=skills_inventory,
        skill_feedback=_feedback_summary(feedback_rows),
        learning=learning,
        learning_memories=learning_memories,
        learning_weak_spots=learning_weak_spots,
        observer=observer,
        benchmarks=benchmarks,
        threads=threads,
        mcp_health=mcp_health,
        loop=loop,
        suggested_actions=actions,
        config=config,
        task_control=task_control,
        thread_rows=thread_rows,
        integrations=_integration_files(root, mcp_health),
        command_catalog=_command_catalog(),
        artifacts=_artifact_rows(root),
        projects=projects,
        task_history=task_history,
        semantic_graph=semantic_graph,
    )
    state = sync_dashboard_state(root, snapshot)
    snapshot.project_record = state["project"]
    snapshot.workspace = state["workspace"]
    snapshot.project_tasks = state["tasks"]
    snapshot.active_task = state["active_task"]
    snapshot.task_runs = state["runs"]
    snapshot.dashboard_feedback = state["feedback"]
    snapshot.analytics = state["analytics"]
    snapshot.unassigned_history_count = int(state["unassigned_history_count"])
    return snapshot


def semantic_graph_summary(
    root: Path,
    *,
    relationship: str = "",
    confidence: str = "",
    language: str = "",
    evidence_source: str = "",
    query: str = "",
    limit: int = 200,
) -> SemanticGraphSummary:
    try:
        snapshot = build_snapshot_for_ref(root)
    except Exception as exc:
        return SemanticGraphSummary(capabilities={"error": str(exc)})
    relationship_counts: Counter[str] = Counter(edge.edge_type for edge in snapshot.edges)
    entity_by_key = {entity.entity_key: entity for entity in snapshot.entities}
    safe_limit = max(1, min(int(limit), 500))
    query_terms = [term.lower() for term in query.split() if term.strip()]

    def matches_entity(entity) -> bool:
        if entity is None:
            return False
        if language and entity.language != language:
            return False
        if not query_terms:
            return True
        haystack = " ".join((entity.qualified_name, entity.display_name, entity.locator.path)).lower()
        return all(term in haystack for term in query_terms)

    filtered_edges = [
        edge
        for edge in snapshot.edges
        if (not relationship or edge.edge_type == relationship)
        and (not confidence or edge.confidence_tier == confidence)
        and (not evidence_source or any(evidence.source == evidence_source for evidence in edge.evidence))
        and (
            not query_terms
            or matches_entity(entity_by_key.get(edge.source_entity_key))
            or matches_entity(entity_by_key.get(edge.target_entity_key))
        )
        and (
            not language
            or matches_entity(entity_by_key.get(edge.source_entity_key))
            or matches_entity(entity_by_key.get(edge.target_entity_key))
        )
    ][:safe_limit]
    visible_keys = {key for edge in filtered_edges for key in (edge.source_entity_key, edge.target_entity_key)}
    visible_entities = [entity for entity in snapshot.entities if entity.entity_key in visible_keys]
    entities = [
        {
            "entity_key": entity.entity_key,
            "type": entity.entity_type,
            "name": entity.qualified_name,
            "path": entity.locator.path,
            "line": entity.locator.start_line,
            "language": entity.language,
            "confidence_tier": entity.confidence_tier,
        }
        for entity in visible_entities[: safe_limit * 2]
    ]
    edges = [
        {
            "edge_key": edge.edge_key,
            "relationship": edge.edge_type,
            "source": edge.source_entity_key,
            "target": edge.target_entity_key,
            "source_name": entity_by_key.get(edge.source_entity_key).qualified_name if entity_by_key.get(edge.source_entity_key) else edge.source_entity_key,
            "target_name": entity_by_key.get(edge.target_entity_key).qualified_name if entity_by_key.get(edge.target_entity_key) else edge.target_entity_key,
            "confidence_tier": edge.confidence_tier,
            "evidence": [evidence.model_dump(mode="json") for evidence in edge.evidence],
        }
        for edge in filtered_edges
    ]
    return SemanticGraphSummary(
        schema_version=snapshot.schema_version,
        commit_sha=snapshot.commit_sha,
        entity_count=len(snapshot.entities),
        edge_count=len(snapshot.edges),
        unresolved_count=sum(1 for entity in snapshot.entities if entity.entity_type in {"external", "unresolved"}),
        capabilities=snapshot.capabilities,
        cache_stats=snapshot.cache_stats,
        relationship_counts=dict(sorted(relationship_counts.items())),
        entities=entities,
        edges=edges,
    )


_semantic_graph_summary = semantic_graph_summary


def _project_info(root: Path, meta: dict[str, Any] | None) -> ProjectInfo:
    branch = str((meta or {}).get("git_branch") or "")
    sha = str((meta or {}).get("git_sha") or "")
    if git.is_git_repo(root):
        branch = branch or (git.current_branch(root) or "")
        sha = sha or (git.current_sha(root) or "")
    return ProjectInfo(name=root.name, path=str(root), branch=branch, git_sha=sha[:12])


def _thread_id(meta: dict[str, Any] | None) -> str | None:
    concurrent = (meta or {}).get("concurrent_context")
    if isinstance(concurrent, dict):
        thread_id = concurrent.get("thread_id")
        if thread_id:
            return str(thread_id)
    thread_id = (meta or {}).get("thread_id")
    return str(thread_id) if thread_id else None


def _read_task(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _task_state(path: Path) -> str:
    if not path.exists():
        return "unknown"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"
    valid = {"planned", "in_progress", "blocked", "done"}
    for line in lines:
        if line.lower().startswith("status:"):
            value = line.split(":", 1)[1].strip()
            return value if value in valid else "unknown"
    return "unknown"


def _context_health(meta: dict[str, Any] | None, freshness: Any) -> ContextHealth:
    if not meta:
        return ContextHealth(status="missing")

    status = "fresh"
    stale_reason = ""
    if freshness is not None and getattr(freshness, "is_stale", False):
        status = "stale"
        stale_reason = getattr(freshness, "reason", "") or ""
    elif isinstance(meta.get("freshness"), dict):
        freshness_status = str(meta["freshness"].get("status") or "").lower()
        if freshness_status in {"fresh", "stale", "missing", "unknown"}:
            status = freshness_status
        stale_reason = str(meta["freshness"].get("reason") or meta["freshness"].get("stale_reason") or "")

    selected = meta.get("selected_files_meta") or []
    packed_tokens = _as_int(meta.get("token_estimate"), _as_int(meta.get("packed_tokens"), 0))
    raw_tokens = _as_int(meta.get("raw_tokens"), 0)
    saving_pct = _as_float(meta.get("saving_pct"), 0.0)
    if saving_pct == 0.0 and raw_tokens > 0 and packed_tokens > 0:
        saving_pct = round((1 - packed_tokens / raw_tokens) * 100, 1)

    freshness_data = meta.get("freshness") if isinstance(meta.get("freshness"), dict) else {}
    return ContextHealth(
        status=status,
        generated_at=str(meta.get("generated_at") or ""),
        mode=str(meta.get("mode") or ""),
        packed_tokens=packed_tokens,
        raw_tokens=raw_tokens,
        saving_pct=saving_pct,
        selected_files_count=len(selected) if isinstance(selected, list) else 0,
        stale_reason=stale_reason,
        source_command=str(
            meta.get("source_command")
            or freshness_data.get("source_command")
            or ""
        ),
    )


def _selected_files(meta: dict[str, Any] | None) -> list[SelectedFileRow]:
    rows: list[SelectedFileRow] = []
    for item in (meta or {}).get("selected_files_meta") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            SelectedFileRow(
                path=str(item.get("path") or ""),
                include_mode=str(item.get("mode") or item.get("include_mode") or ""),
                score=_as_float(item.get("score"), 0.0),
                tokens=_as_int(item.get("tokens"), _as_int(item.get("estimated_tokens"), 0)),
                reasons=_string_list(item.get("reasons"))[:MAX_REASONS],
            )
        )
    return rows


def _task_map_files(meta: dict[str, Any] | None) -> list[TaskMapFileRow]:
    task_map = (meta or {}).get("task_map") or {}
    files = task_map.get("files") if isinstance(task_map, dict) else []
    rows: list[TaskMapFileRow] = []
    if not isinstance(files, list):
        return rows
    for item in files[:50]:
        if not isinstance(item, dict):
            continue
        rows.append(
            TaskMapFileRow(
                path=str(item.get("path") or ""),
                kind=str(item.get("kind") or ""),
                include_mode=str(item.get("include_mode") or ""),
                score=_as_float(item.get("score"), 0.0),
                risk_level=str(item.get("risk_level") or "low"),
                risk_reasons=_string_list(item.get("risk_reasons"))[:MAX_REASONS],
                why_selected=_string_list(item.get("why_selected"))[:MAX_REASONS],
                tests_to_run=_string_list(item.get("tests_to_run"))[:MAX_REASONS],
                may_break=_string_list(item.get("may_break"))[:MAX_REASONS],
                retrieve_ref=str(item.get("retrieve_ref") or ""),
            )
        )
    return rows


def _skill_section(meta: dict[str, Any] | None, feedback_rows: list[dict[str, Any]]) -> SkillSection:
    feedback = _feedback_summary_by_skill(feedback_rows)
    return SkillSection(
        task_specific=_skill_rows((meta or {}).get("selected_skills") or [], feedback),
        baseline=_skill_rows((meta or {}).get("baseline_skills") or [], feedback),
    )


def _skill_rows(values: list[Any], feedback: dict[str, SkillFeedbackStatus]) -> list[SkillRow]:
    rows: list[SkillRow] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        skill = item.get("skill") if isinstance(item.get("skill"), dict) else item
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or item.get("name") or "")
        if not name:
            continue
        rows.append(
            SkillRow(
                name=name,
                path=str(skill.get("path") or ""),
                confidence=_as_float(item.get("confidence"), 0.0),
                score=_as_float(item.get("score"), 0.0),
                side_effect_level=str(skill.get("side_effect_level") or ""),
                status=feedback.get(name.lower(), "none"),
                reasons=_string_list(item.get("reasons"))[:MAX_REASONS],
            )
        )
    return rows


def _skills_inventory_summary(root: Path, *, initialized: bool) -> SkillsInventorySummary:
    if not initialized:
        return SkillsInventorySummary(index_error="AgentPack is not initialized.")
    try:
        cfg = load_config(root)
        result = ensure_inventory_index(root, cfg.skills.paths)
    except Exception as exc:
        return SkillsInventorySummary(index_error=str(exc))

    inventory = result.document.inventory
    rows = [_skill_inventory_row(skill) for skill in inventory.skills]
    names: dict[str, int] = {}
    for row in rows:
        key = row.name.lower()
        names[key] = names.get(key, 0) + 1
    return SkillsInventorySummary(
        available=True,
        index_refreshed=result.refreshed,
        index_reason=result.reason,
        total_skills=len(inventory.skills),
        total_rules=len(inventory.rules),
        uncategorized_count=sum(1 for row in rows if row.domains == ["uncategorized"]),
        missing_metadata_count=sum(1 for row in rows if row.metadata_quality == "inferred"),
        duplicate_names=sorted(name for name, count in names.items() if count > 1),
        sources=[
            SkillInventorySourceSummary(
                configured_path=source.configured_path,
                resolved_path=source.resolved_path,
                exists=source.exists,
                file_count=source.file_count,
            )
            for source in result.document.sources
        ],
        domains=_domain_counts(rows),
        rows=rows[:MAX_SKILL_INVENTORY_ROWS],
    )


def _skill_inventory_row(skill: SkillArtifact) -> SkillInventoryRow:
    explicit_metadata = bool(
        skill.domains
        or skill.task_types
        or skill.languages
        or skill.frameworks
        or skill.applies_to_paths
        or skill.anti_paths
        or skill.anti_triggers
    )
    domains, domain_confidence, domain_source = _skill_domains(skill)
    metadata_quality = "explicit" if explicit_metadata else "inferred"
    return SkillInventoryRow(
        name=skill.name,
        path=skill.path,
        source=skill.source,
        domains=domains,
        languages=skill.languages,
        frameworks=skill.frameworks,
        side_effect_level=skill.side_effect_level,
        metadata_quality=metadata_quality,
        metadata=_skill_metadata(
            skill,
            domains=domains,
            quality=metadata_quality,
            domain_confidence=domain_confidence,
            domain_source=domain_source,
        ),
        domain_confidence=domain_confidence,
        domain_source=domain_source,
    )


def _skill_domains(skill: SkillArtifact) -> tuple[list[str], float, str]:
    if skill.domains:
        return skill.domains, 1.0, "explicit domains"
    if skill.task_types:
        return skill.task_types, 1.0, "explicit task_types"
    scored = _bm25_skill_domains(skill)
    if scored:
        top_score = scored[0][1]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        confidence = top_score / (top_score + second_score + 1.0)
        return [domain for domain, _score in scored[:MAX_INFERRED_DOMAINS]], round(confidence, 2), "bm25"
    keyword_domains = _keyword_skill_domains(skill)
    if keyword_domains != ["uncategorized"]:
        return keyword_domains, 0.35, "keyword fallback"
    return keyword_domains, 0.0, "none"


def _bm25_skill_domains(skill: SkillArtifact) -> list[tuple[str, float]]:
    text = _skill_domain_text(skill)
    token_counts = Counter(_domain_token_list(text))
    if not token_counts:
        return []

    scored: list[tuple[str, float]] = []
    total_domains = len(_DOMAIN_CORPUS)
    for domain, terms in _DOMAIN_CORPUS:
        score = 0.0
        for term in terms:
            term_count = token_counts.get(term, 0)
            if "-" in term and term.replace("-", " ") in text.lower():
                term_count += 1
            if term_count <= 0:
                continue
            document_frequency = _DOMAIN_DOCUMENT_FREQUENCY[term]
            idf = math.log(1 + (total_domains - document_frequency + 0.5) / (document_frequency + 0.5))
            score += idf * ((term_count * 2.2) / (term_count + 1.2))
        if score >= MIN_BM25_DOMAIN_SCORE:
            scored.append((domain, score))
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def _keyword_skill_domains(skill: SkillArtifact) -> list[str]:
    text = _skill_domain_text(skill)
    tokens = set(_domain_token_list(text))
    scores: dict[str, int] = {}
    for domain, terms in _DOMAIN_CORPUS:
        score = sum(2 if term in tokens else 0 for term in terms)
        score += sum(1 for term in terms if "-" in term and term.replace("-", " ") in text.lower())
        if score:
            scores[domain] = score
    if not scores:
        return ["uncategorized"]
    return [
        domain
        for domain, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:MAX_INFERRED_DOMAINS]
    ]


def _skill_domain_text(skill: SkillArtifact) -> str:
    skill_path = Path(skill.path)
    return " ".join(
        [
            skill.name,
            skill.description,
            skill_path.parent.name,
            " ".join(skill.triggers[:20]),
            " ".join(skill.frameworks),
            " ".join(skill.languages),
            " ".join(skill.tools_required),
        ]
    )


def _skill_metadata(
    skill: SkillArtifact,
    *,
    domains: list[str],
    quality: str,
    domain_confidence: float,
    domain_source: str,
) -> list[str]:
    items: list[str] = []
    if domains != ["uncategorized"]:
        items.append(f"domain source: {domain_source}")
        items.append("domain confidence: " + f"{domain_confidence:.2f}")
    if skill.domains:
        items.append("domain: " + ", ".join(skill.domains))
    if quality == "inferred" and domains != ["uncategorized"]:
        items.append("inferred domains: " + ", ".join(domains))
    if skill.description:
        items.append("description: " + " ".join(skill.description.split()))
    if skill.task_types:
        items.append("task: " + ", ".join(skill.task_types))
    if skill.languages:
        items.append("language: " + ", ".join(skill.languages))
    if skill.frameworks:
        items.append("framework: " + ", ".join(skill.frameworks))
    if skill.tools_required:
        items.append("tools: " + ", ".join(skill.tools_required))
    if skill.applies_to_paths:
        items.append("paths: " + ", ".join(skill.applies_to_paths[:3]))
    name_parts = _trigger_name_parts(skill.name)
    evidenced_terms = set(_domain_token_list(skill.description))
    path = Path(skill.path)
    hidden_triggers = {
        skill.name.lower().replace("_", "-"),
        path.parent.name.lower().replace("_", "-"),
        path.stem.lower().replace("_", "-"),
    }
    candidate_triggers = [
        trigger
        for trigger in skill.triggers
        if _show_skill_trigger(trigger, hidden_triggers=hidden_triggers, name_parts=name_parts, evidenced_terms=evidenced_terms)
    ]
    useful_triggers = _rank_skill_triggers(candidate_triggers, skill=skill, domains=domains, evidenced_terms=evidenced_terms)[:8]
    if useful_triggers:
        items.append("triggers: " + ", ".join(useful_triggers))
    return items[:MAX_METADATA_ITEMS]


def _show_skill_trigger(
    trigger: str,
    *,
    hidden_triggers: set[str],
    name_parts: set[str],
    evidenced_terms: set[str],
) -> bool:
    if trigger in hidden_triggers:
        return False
    parts = trigger.split("-")
    if trigger in name_parts:
        return trigger in evidenced_terms
    if len(parts) > 1:
        return not all(part in name_parts for part in parts)
    return not any(part in name_parts for part in parts)


def _rank_skill_triggers(
    triggers: list[str],
    *,
    skill: SkillArtifact,
    domains: list[str],
    evidenced_terms: set[str],
) -> list[str]:
    domain_terms = set(domains) | set(skill.domains) | set(skill.languages) | set(skill.frameworks) | set(skill.tools_required)
    indexed = list(enumerate(triggers))

    def score(item: tuple[int, str]) -> tuple[int, int]:
        index, trigger = item
        parts = trigger.split("-")
        value = 0
        if len(parts) > 1:
            value += 100 + min(len(parts), 3) * 8
            if any(char.isdigit() for char in trigger) or any(marker in trigger for marker in ("+", ".")):
                value += 4
        else:
            value -= 30
            if trigger in evidenced_terms:
                value += 20
            if trigger in domain_terms:
                value += 20
            if any(char.isdigit() for char in trigger) or any(marker in trigger for marker in ("+", ".")):
                value += 8
            if trigger not in domain_terms and trigger not in evidenced_terms and trigger not in {"ai", "go", "ui"}:
                value -= 25
        return (-value, index)

    return [trigger for _index, trigger in sorted(indexed, key=score)]


def _trigger_name_parts(value: str) -> set[str]:
    normalized = value.lower().replace("_", "-")
    return {part for part in re.split(r"[^a-z0-9]+", normalized) if part}


def _domain_token_list(text: str) -> list[str]:
    lower = text.lower().replace("_", "-")
    raw_tokens = re.findall(r"[a-z][a-z0-9+.-]{1,}", lower)
    split_tokens = [
        part
        for token in raw_tokens
        for part in token.split("-")
        if len(part) > 1
    ]
    return raw_tokens + split_tokens


def _clip(value: str, max_chars: int) -> str:
    clean = " ".join(value.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "…"


def _domain_counts(rows: list[SkillInventoryRow]) -> list[SkillDomainSummary]:
    counts: dict[str, int] = {}
    for row in rows:
        for domain in row.domains:
            counts[domain] = counts.get(domain, 0) + 1
    return [
        SkillDomainSummary(name=name, count=count)
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-MAX_JSONL_ROWS:]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _feedback_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "recent": rows[-MAX_RECENT_FEEDBACK:],
        "summary_by_skill": _feedback_summary_by_skill(rows),
    }


def _feedback_summary_by_skill(rows: list[dict[str, Any]]) -> dict[str, SkillFeedbackStatus]:
    status: dict[str, SkillFeedbackStatus] = {}
    precedence = {
        "none": 0,
        "recommended_only": 1,
        "ignored": 2,
        "used_helpful": 3,
        "used_noisy": 4,
        "bad_recommendation": 5,
    }

    def assign(skill: Any, new_status: SkillFeedbackStatus) -> None:
        key = str(skill).strip().lower()
        if not key:
            return
        current = status.get(key, "none")
        if precedence[new_status] >= precedence[current]:
            status[key] = new_status

    for row in rows:
        for skill in _string_list(row.get("recommended_skills")):
            assign(skill, "recommended_only")
        for skill in _string_list(row.get("used_skills")):
            feedback = str(row.get("user_feedback") or "").lower()
            assign(skill, "used_noisy" if feedback in {"bad", "noisy", "unhelpful", "not-helpful"} else "used_helpful")
        for skill in _string_list(row.get("ignored_skills")):
            assign(skill, "ignored")
        for skill in _string_list(row.get("bad_recommendations")):
            assign(skill, "bad_recommendation")
    return status


def _learning_artifacts(agentpack_dir: Path) -> list[LearningArtifact]:
    artifacts = [
        ("Learning notes", "learning.md"),
        ("Daily summary", "daily-summary.md"),
        ("Agent lessons", "agent-lessons.md"),
        ("Skill progress", "skills-progress.json"),
        ("Learning feedback", "learning-feedback.jsonl"),
    ]
    return [
        LearningArtifact(
            label=label,
            path=f".agentpack/{name}",
            exists=(agentpack_dir / name).exists(),
            excerpt=_bounded_excerpt(agentpack_dir / name),
        )
        for label, name in artifacts
    ]


def _learning_memories(root: Path) -> list[LearningMemory]:
    rows: list[LearningMemory] = []
    for item in reversed(recent_task_memories(root, limit=6)):
        rows.append(
            LearningMemory(
                task=str(item.get("task") or ""),
                stage=str(item.get("stage") or ""),
                status=str(item.get("status") or ""),
                branch=str(item.get("branch") or ""),
                git_sha=str(item.get("git_sha") or "")[:12],
                concepts=_string_list(item.get("concepts"))[:6],
                changed_files=_string_list(item.get("changed_files"))[:8],
                selected_files=_string_list(item.get("selected_files"))[:8],
            )
        )
    return rows


def _observer_summary(root: Path, task: str) -> ObserverSummary:
    try:
        brief = build_observer_brief(root, task=task)
    except Exception as exc:
        return ObserverSummary(
            event_types={"error": 1},
            insights=[
                ObserverInsightRow(
                    kind="error",
                    title="Observer summary unavailable",
                    detail=str(exc),
                    action="Run `agentpack guard --agent codex --repair-stale --refresh-context` and retry.",
                    confidence=0.0,
                )
            ],
        )
    event_types = brief.stats.get("types") if isinstance(brief.stats, dict) else {}
    if not isinstance(event_types, dict):
        event_types = {}
    return ObserverSummary(
        generated_at=brief.generated_at,
        events=_as_int(brief.stats.get("events") if isinstance(brief.stats, dict) else 0, 0),
        event_types={str(key): _as_int(value, 0) for key, value in event_types.items()},
        insights=[
            ObserverInsightRow(
                kind=insight.kind,
                title=insight.title,
                detail=insight.detail,
                action=insight.action,
                confidence=insight.confidence,
                related_files=insight.related_files,
                evidence=insight.evidence,
            )
            for insight in brief.insights[:8]
        ],
    )


def _learning_weak_spots(root: Path) -> list[LearningWeakSpot]:
    rows: list[LearningWeakSpot] = []
    for item in summarize_weak_spots(root, limit=6):
        rows.append(
            LearningWeakSpot(
                concept=str(item.get("concept") or ""),
                count=_as_int(item.get("count"), 0),
                mode=str(item.get("mode") or ""),
                latest_task=str(item.get("latest_task") or ""),
                latest_question=str(item.get("latest_question") or ""),
                evidence_files=_string_list(item.get("evidence_files"))[:6],
            )
        )
    return rows


def _bounded_excerpt(path: Path, limit: int = MAX_EXCERPT_CHARS) -> str:
    if not path.exists() or path.suffix == ".jsonl":
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:limit]


def _benchmark_summary(metrics_rows: list[dict[str, Any]], benchmark_rows: list[dict[str, Any]]) -> BenchmarkSummary:
    numeric_keys = [
        "selection_recall",
        "selection_precision",
        "selection_token_precision",
        "rank_at_k",
        "skill_recall_at_3",
        "skill_precision_at_3",
        "skill_mrr",
        "skill_noise_rate",
    ]
    recent = metrics_rows[-10:] + benchmark_rows[-10:]
    averages: dict[str, float] = {}
    for key in numeric_keys:
        values = [_as_float(row[key], 0.0) for row in recent if _is_number(row.get(key))]
        if values:
            averages[key] = sum(values) / len(values)
    latest = (benchmark_rows or metrics_rows or [{}])[-1]
    misses = [miss for row in benchmark_rows[-5:] for miss in (row.get("misses") or row.get("missed_expected") or []) if isinstance(miss, dict)]
    return BenchmarkSummary(latest=latest, averages=averages, misses=misses[:MAX_MISSES])


def _config_summary(root: Path) -> DashboardConfigSummary:
    path = root / ".agentpack" / "config.toml"
    valid = True
    error = ""
    if path.exists():
        try:
            with path.open("rb") as fh:
                tomllib.load(fh)
        except Exception as exc:
            valid = False
            error = str(exc)
    cfg = load_config(root)
    effective = cfg.model_dump(mode="json")
    defaults = DEFAULT_CONFIG.model_dump(mode="json")
    sections: list[DashboardConfigSection] = []
    for section in CONFIG_SECTIONS:
        values = effective.get(section)
        default_values = defaults.get(section)
        if not isinstance(values, dict):
            continue
        fields: list[DashboardConfigField] = []
        default_map = default_values if isinstance(default_values, dict) else {}
        for key, value in values.items():
            field_id = f"{section}.{key}"
            docs = CONFIG_FIELD_DOCS.get(field_id, {}) if field_id in EDITABLE_CONFIG_FIELDS else {}
            fields.append(
                DashboardConfigField(
                    section=section,
                    key=str(key),
                    value=value,
                    default=default_map.get(key),
                    value_type=_config_value_type(value),
                    editable=field_id in EDITABLE_CONFIG_FIELDS,
                    source="default" if value == default_map.get(key) else "project",
                    description=str(docs.get("description") or ""),
                    allowed_values=[str(item) for item in docs.get("allowed_values") or []],
                    doc_ref=str(docs.get("doc_ref") or ""),
                )
            )
        sections.append(DashboardConfigSection(name=section, fields=fields))
    return DashboardConfigSummary(
        path=str(path),
        exists=path.exists(),
        valid=valid,
        error=error,
        sections=sections,
        editable_fields=sorted(EDITABLE_CONFIG_FIELDS),
    )


def _config_value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "string"


def _task_control_rows(root: Path, meta: dict[str, Any] | None) -> list[TaskControlRow]:
    agentpack_dir = root / ".agentpack"
    rows = [
        _task_control_row(
            root,
            scope="global",
            thread_id=None,
            task_path=agentpack_dir / "task.md",
            state_path=agentpack_dir / "task_state.md",
        )
    ]
    thread_ids = {_thread_id(meta) or ""}
    thread_ids.update(str(row.get("thread_id") or "") for row in list_thread_rows(root, active_only=True))
    for thread_id in sorted(thread_id for thread_id in thread_ids if thread_id):
        base = agentpack_dir / "threads" / thread_id
        rows.append(
            _task_control_row(
                root,
                scope="thread",
                thread_id=thread_id,
                task_path=base / "task.md",
                state_path=base / "task_state.md",
            )
        )
    return rows


def _task_control_row(
    root: Path,
    *,
    scope: str,
    thread_id: str | None,
    task_path: Path,
    state_path: Path,
) -> TaskControlRow:
    status, summary = _read_state_summary(state_path)
    return TaskControlRow(
        scope="thread" if scope == "thread" else "global",
        thread_id=thread_id,
        task=_read_task(task_path),
        task_path=_rel_path(task_path, root),
        state=_task_state(state_path),
        state_path=_rel_path(state_path, root),
        status=status,
        summary=summary,
        done=status == "done",
        exists=task_path.exists() or state_path.exists(),
    )


def _read_state_summary(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "unknown", ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown", ""
    status = "unknown"
    summary = ""
    for line in lines:
        if line.lower().startswith("status:"):
            status = line.split(":", 1)[1].strip().lower() or "unknown"
        elif line.lower().startswith("summary:"):
            summary = line.split(":", 1)[1].strip()
    return status, summary


def _thread_rows(root: Path, meta: dict[str, Any] | None) -> list[ThreadRow]:
    raw_rows = list_thread_rows(root)
    latest: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        thread_id = str(row.get("thread_id") or "")
        if not thread_id:
            continue
        previous = latest.get(thread_id)
        if previous is None or _sort_timestamp(row.get("updated_at")) > _sort_timestamp(previous.get("updated_at")):
            latest[thread_id] = row

    conflict_map: dict[str, dict[str, Any]] = {}
    concurrent = (meta or {}).get("concurrent_context")
    if isinstance(concurrent, dict):
        for conflict in concurrent.get("conflicts") or []:
            if isinstance(conflict, dict) and conflict.get("thread_id"):
                conflict_map[str(conflict["thread_id"])] = conflict

    rows: list[ThreadRow] = []
    for thread_id, row in sorted(latest.items(), key=lambda item: _sort_timestamp(item[1].get("updated_at")), reverse=True):
        selected_files = _string_list(row.get("selected_files"))
        dirty_files = _string_list(row.get("dirty_files"))
        conflict = conflict_map.get(thread_id, {})
        overlap = _string_list(conflict.get("overlap"))
        status = str(row.get("status") or "")
        rows.append(
            ThreadRow(
                thread_id=thread_id,
                task=str(row.get("task") or ""),
                status=status,
                summary=str(row.get("summary") or ""),
                branch=str(row.get("branch") or ""),
                updated_at=str(row.get("updated_at") or ""),
                worktree=str(row.get("worktree") or ""),
                selected_count=len(selected_files),
                dirty_count=len(dirty_files),
                conflicts=[str(conflict.get("task") or conflict.get("thread_id") or "")] if conflict else [],
                overlap_files=overlap,
                prune_eligible=status == "done",
            )
        )
    return rows[:50]


def _task_history_rows(root: Path, task_control: list[TaskControlRow], thread_rows: list[ThreadRow]) -> list[TaskHistoryRow]:
    rows: list[TaskHistoryRow] = []
    for item in task_control:
        if item.task:
            rows.append(
                TaskHistoryRow(
                    task=item.task,
                    source=f"{item.scope} task",
                    thread_id=item.thread_id or "",
                    cwd=str(root),
                    status=item.status,
                    summary=item.summary,
                )
            )
    for item in recent_task_start_snapshots(root, limit=40):
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        rows.append(
            TaskHistoryRow(
                task=task,
                source="task start",
                observed_at=str(item.get("started_at") or item.get("timestamp") or item.get("recorded_at") or ""),
                thread_id=str(item.get("thread") or ""),
                agent=str(item.get("agent") or ""),
                branch=str(item.get("branch") or ""),
                git_sha=str(item.get("git_sha") or "")[:12],
                cwd=str(provenance.get("cwd") or root),
                context_path=str(item.get("context_path") or ""),
                status=str(item.get("status") or ""),
                summary=str(item.get("visible_reason") or ""),
            )
        )
    for item in recent_task_memories(root, limit=40):
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        rows.append(
            TaskHistoryRow(
                task=task,
                source="task memory",
                observed_at=str(item.get("timestamp") or item.get("recorded_at") or ""),
                thread_id=str(item.get("thread") or ""),
                branch=str(item.get("branch") or ""),
                git_sha=str(item.get("git_sha") or "")[:12],
                cwd=str(provenance.get("cwd") or root),
                status=str(item.get("status") or ""),
                summary=str(item.get("summary") or ""),
            )
        )
    for item in thread_rows:
        if item.task:
            rows.append(
                TaskHistoryRow(
                    task=item.task,
                    source="thread index",
                    observed_at=item.updated_at,
                    thread_id=item.thread_id,
                    branch=item.branch,
                    cwd=item.worktree or str(root),
                    status=item.status,
                    summary=item.summary,
                )
            )
    deduped: dict[str, TaskHistoryRow] = {}
    for row in rows:
        key = _task_history_key(row)
        current = deduped.get(key)
        if current is None or row.observed_at >= current.observed_at:
            deduped[key] = row
    return sorted(deduped.values(), key=lambda item: (item.observed_at, item.source), reverse=True)[:30]


def _task_history_key(row: TaskHistoryRow) -> str:
    task = " ".join(row.task.lower().split())
    return "|".join([task, row.thread_id, row.cwd])


def _project_candidates(root: Path, thread_rows: list[ThreadRow], task_history: list[TaskHistoryRow]) -> list[ProjectCandidate]:
    candidates: list[tuple[Path, str, dict[str, Any]]] = [(root, "current", {})]
    candidates.extend((Path(str(row.get("path"))), "global index", row) for row in load_project_index() if row.get("path"))
    candidates.extend((Path(row.worktree), "thread", {}) for row in thread_rows if row.worktree)
    candidates.extend((Path(row.cwd), "task history", {}) for row in task_history if row.cwd)

    seen: set[str] = set()
    rows: list[ProjectCandidate] = []
    for path, source, index_row in candidates:
        candidate = path.expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        rows.append(_project_candidate(root, resolved, source, index_row))
    return rows[:20]


def _project_candidate(current_root: Path, path: Path, source: str, index_row: dict[str, Any] | None = None) -> ProjectCandidate:
    exists = path.exists() and path.is_dir()
    valid = exists and _valid_project_root(path)
    branch = git.current_branch(path) or "" if valid and git.is_git_repo(path) else ""
    sha = (git.current_sha(path) or "")[:12] if valid and git.is_git_repo(path) else ""
    context_status = _project_context_status(path) if valid else "missing"
    mcp_status = _project_mcp_status(path) if valid else "unknown"
    map_ready = valid and context_status in {"fresh", "available"}
    detail = "map-ready" if map_ready else "context missing" if valid else "missing directory" if not exists else "not a git or AgentPack project"
    row = index_row or {}
    return ProjectCandidate(
        name=path.name or str(path),
        path=str(path),
        branch=branch or str(row.get("branch") or ""),
        git_sha=sha or str(row.get("git_sha") or ""),
        source=source,
        current=path == current_root,
        exists=exists,
        valid=valid,
        detail=detail,
        context_status=context_status,
        mcp_status=mcp_status,
        map_ready=map_ready,
        last_seen_at=str(row.get("last_seen_at") or ""),
    )


def _project_context_status(path: Path) -> str:
    context_path = path / ".agentpack" / "context.md"
    config_path = path / ".agentpack" / "config.toml"
    if context_path.exists():
        try:
            text = context_path.read_text(encoding="utf-8", errors="replace")[:1200]
        except OSError:
            return "available"
        return "stale" if "refresh_required: true" in text else "fresh"
    return "available" if config_path.exists() else "missing"


def _project_mcp_status(path: Path) -> str:
    if any((path / candidate).exists() for candidate in (".claude/settings.json", ".codex/config.toml", ".cursor/rules/agentpack.mdc", ".vscode/tasks.json")):
        return "configured"
    return "unknown"


def _valid_project_root(path: Path) -> bool:
    return path.is_dir() and ((path / ".git").exists() or (path / ".agentpack" / "config.toml").exists())


def _sort_timestamp(value: Any) -> str:
    return str(value or "")


def _integration_files(root: Path, mcp_health: McpHealth) -> list[IntegrationFileRow]:
    home = Path.home()
    rows = [
        _integration_file("claude", "Claude project MCP", root / ".mcp.json", "agentpack repair --agent claude"),
        _integration_file("claude", "Claude project settings", root / ".claude" / "settings.json", "agentpack repair --agent claude"),
        _integration_file("claude", "Claude local settings", root / ".claude" / "settings.local.json", "agentpack repair --agent claude"),
        _integration_file("claude", "Claude global settings", home / ".claude" / "settings.json", "agentpack repair --agent claude --global"),
        _integration_file("codex", "Codex MCP config", Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml", "agentpack repair --agent codex"),
        _integration_file("codex", "Codex hooks", root / ".codex" / "hooks.json", "agentpack repair --agent codex"),
        _integration_file("cursor", "Cursor rule", root / ".cursor" / "rules" / "agentpack.mdc", "agentpack repair --agent cursor"),
        _integration_file("windsurf", "Windsurf rule", root / ".windsurfrules", "agentpack repair --agent windsurf"),
        _integration_file("vscode", "VS Code tasks", root / ".vscode" / "tasks.json", "agentpack repair --agent vscode"),
    ]
    rows.append(
        IntegrationFileRow(
            agent="mcp",
            label="MCP runtime",
            path="agentpack mcp",
            exists=mcp_health.runtime_ok,
            status=mcp_health.runtime_status or "unknown",
            detail=mcp_health.runtime_detail,
            repair_command="agentpack repair --agent all",
        )
    )
    return rows


def _integration_file(agent: str, label: str, path: Path, repair_command: str) -> IntegrationFileRow:
    exists = path.exists()
    status = "present" if exists else "missing"
    detail = "File present." if exists else "File not found."
    if exists and path.suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            status = "invalid"
            detail = str(exc)
    return IntegrationFileRow(
        agent=agent,
        label=label,
        path=str(path),
        exists=exists,
        status=status,
        detail=detail,
        repair_command=repair_command,
    )


def _command_catalog() -> list[CommandCatalogItem]:
    rows = [
        ("next", "Cockpit", "Run next", "agentpack next", "Ask AgentPack for the next local action.", "low", False, True),
        ("doctor_all", "Diagnostics", "Doctor all agents", "agentpack doctor --agent all", "Audit AgentPack setup and host integrations.", "low", False, True),
        ("guard_refresh", "Context", "Guard and refresh", "agentpack guard --agent codex --repair-stale --refresh-context --thread global", "Repair stale context for the current project.", "medium", True, True),
        ("pack_auto", "Context", "Pack context", "agentpack pack --task auto", "Rebuild the selected context pack.", "low", False, True),
        ("route_task", "Context", "Route task", 'agentpack route --task "describe the task" --json', "Preview task routing without writing context.", "low", False, False),
        ("status", "Tasks", "Show status", "agentpack status", "Read task, git, and context state.", "low", False, True),
        ("task_show", "Tasks", "Show task", "agentpack task show --json", "Read the current task file.", "low", False, False),
        ("state_show", "Tasks", "Show state", "agentpack state show --json", "Read task execution state.", "low", False, False),
        ("threads", "Threads", "List threads", "agentpack threads --json", "List known AgentPack threads.", "low", False, True),
        ("threads_active", "Threads", "Active threads", "agentpack threads --active --json", "List active AgentPack threads.", "low", False, False),
        ("work", "Workflow", "Work task", 'agentpack work "describe the task"', "Start or continue a task workflow.", "low", False, True),
        ("finish", "Workflow", "Finish task", 'agentpack finish --summary "completed"', "Mark workflow completion and write task memory.", "high", True, True),
        ("dev_check", "Workflow", "Dev check", "agentpack dev-check", "Run local readiness checks.", "low", False, True),
        ("review", "Workflow", "Review", "agentpack review", "Run AgentPack review workflow.", "medium", False, True),
        ("release_check", "Release", "Release check", "agentpack release-check --profile ci", "Run release validation profile.", "medium", False, False),
        ("skills_index", "Learning & Skills", "Refresh skills", "agentpack skills index", "Refresh and inspect skill inventory.", "low", False, True),
        ("retrieve", "Files", "Retrieve file context", "agentpack retrieve src/agentpack/dashboard/collectors.py", "Fetch focused context for a file.", "low", False, False),
        ("repair_all", "Integrations", "Repair all integrations", "agentpack repair --agent all", "Repair AgentPack host integrations.", "high", True, True),
        ("install", "Integrations", "Install integration", "agentpack install --agent auto", "Install the detected AgentPack host integration.", "high", True, False),
    ]
    return [
        CommandCatalogItem(
            id=command_id,
            group=group,
            label=label,
            command=command,
            description=description,
            risk=risk,
            confirm_required=confirm_required,
            primary=primary,
        )
        for command_id, group, label, command, description, risk, confirm_required, primary in rows
    ]


def _artifact_rows(root: Path) -> list[ArtifactRow]:
    artifacts = [
        ("Config", ".agentpack/config.toml", "config", "settings"),
        ("Global task", ".agentpack/task.md", "task", "tasks"),
        ("Global task state", ".agentpack/task_state.md", "state", "tasks"),
        ("Context", ".agentpack/context.md", "context", "context"),
        ("Claude context", ".agentpack/context.claude.md", "context", "context"),
        ("Pack metadata", ".agentpack/pack_metadata.json", "metadata", "raw"),
        ("Session", ".agentpack/session.json", "session", "raw"),
        ("Thread index", ".agentpack/thread_index.jsonl", "threads", "threads"),
        ("Observer events", ".agentpack/observer-events.jsonl", "learning", "learning"),
        ("Learning notes", ".agentpack/learning.md", "learning", "learning"),
        ("Loop state", ".agentpack/loop_state.json", "workflow", "workflow"),
        ("Loop diagnosis", ".agentpack/loop_diagnosis.md", "workflow", "workflow"),
    ]
    return [_artifact_row(root, label, relative, kind, destination) for label, relative, kind, destination in artifacts]


def _artifact_row(root: Path, label: str, relative: str, kind: str, destination: str) -> ArtifactRow:
    path = root / relative
    stat = None
    if path.exists():
        try:
            stat = path.stat()
        except OSError:
            stat = None
    return ArtifactRow(
        label=label,
        path=relative,
        exists=path.exists(),
        kind=kind,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else "",
        size=stat.st_size if stat else 0,
        destination=destination,
    )


def _rel_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _thread_summary(root: Path, meta: dict[str, Any] | None) -> ThreadSummary:
    rows = list_thread_rows(root, active_only=True)
    conflicts: list[dict[str, Any]] = []
    concurrent = (meta or {}).get("concurrent_context")
    if isinstance(concurrent, dict):
        raw_conflicts = concurrent.get("conflicts") or []
        if isinstance(raw_conflicts, list):
            conflicts = [item for item in raw_conflicts if isinstance(item, dict)]
    return ThreadSummary(active_count=len(rows), conflicts=conflicts)


def _mcp_health(root: Path) -> McpHealth:
    registrations = _mcp_registrations(root)
    registered = any(item.status == "present" for item in registrations)
    runtime = check_mcp_runtime(root=root)
    status = "healthy" if runtime.ok and registered else "warning" if runtime.ok or registered else "missing"
    remediation = list(runtime.remediation)
    if not registered:
        remediation.append("agentpack repair --agent all")
    return McpHealth(
        status=status,
        runtime_status=runtime.status,
        runtime_ok=runtime.ok,
        runtime_detail=runtime.detail,
        registered=registered,
        registrations=registrations,
        live_exposure="unknown",
        expected_tools=list(MCP_TOOL_NAMES),
        remediation=_dedupe(remediation),
    )


def _mcp_registrations(root: Path) -> list[McpRegistration]:
    return [
        _local_mcp_registration(root / ".mcp.json"),
        _claude_global_mcp_registration(Path.home() / ".claude" / "settings.json"),
        _codex_mcp_registration(Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"),
    ]


def _local_mcp_registration(path: Path) -> McpRegistration:
    if not path.exists():
        return McpRegistration(scope="Claude local", path=str(path), status="missing", detail="No .mcp.json found.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return McpRegistration(scope="Claude local", path=str(path), status="invalid", detail=str(exc))
    if "agentpack" in data.get("mcpServers", {}):
        return McpRegistration(scope="Claude local", path=str(path), status="present", detail="agentpack server registered.")
    return McpRegistration(scope="Claude local", path=str(path), status="missing", detail="agentpack missing from mcpServers.")


def _claude_global_mcp_registration(path: Path) -> McpRegistration:
    if not path.exists():
        return McpRegistration(scope="Claude global", path=str(path), status="missing", detail="No Claude settings found.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return McpRegistration(scope="Claude global", path=str(path), status="invalid", detail=str(exc))
    if "agentpack" in data.get("mcpServers", {}):
        return McpRegistration(scope="Claude global", path=str(path), status="present", detail="agentpack server registered.")
    return McpRegistration(scope="Claude global", path=str(path), status="missing", detail="agentpack missing from mcpServers.")


def _codex_mcp_registration(path: Path) -> McpRegistration:
    if not path.exists():
        return McpRegistration(scope="Codex", path=str(path), status="missing", detail="No Codex config found.")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return McpRegistration(scope="Codex", path=str(path), status="invalid", detail=str(exc))
    if "[mcp_servers.agentpack]" in text and 'command = "agentpack"' in text and 'args = ["mcp"]' in text:
        return McpRegistration(scope="Codex", path=str(path), status="present", detail="agentpack server registered.")
    return McpRegistration(scope="Codex", path=str(path), status="missing", detail="agentpack missing from mcp_servers.")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _loop_summary(root: Path) -> LoopSummary:
    state = load_loop_state(root)
    if state is None:
        return LoopSummary()
    metrics = _loop_metrics(root)
    return LoopSummary(
        exists=True,
        status=state.status,
        task=state.task,
        iteration=state.iteration,
        max_iterations=state.max_iterations,
        runner=state.runner,
        last_runner_status=_result_status(state.last_runner),
        last_verification_status=_result_status(state.last_verification),
        blocked_reason=state.blocked_reason,
        failure_class=state.failure_class,
        risk_level=state.risk_review.level,
        changed_files=state.last_diff.files_changed[:20],
        diagnosis_file=".agentpack/loop_diagnosis.md" if (root / ".agentpack" / "loop_diagnosis.md").exists() else "",
        handoff_file=state.handoff_file,
        acceptance_file=state.acceptance_file,
        rollback_patch=state.rollback_patch,
        runs=metrics["runs"],
        blocked_runs=metrics["blocked"],
        ready_runs=metrics["ready_to_finish"],
        avg_iterations=metrics["avg_iterations"],
        next_action=_loop_next_action(state.status, state.task, state.runner, bool(state.verification_commands)),
    )


def _loop_metrics(root: Path) -> dict[str, Any]:
    rows = _load_jsonl(root / ".agentpack" / "loop_metrics.jsonl")
    total_iterations = sum(_as_int(row.get("iterations"), 0) for row in rows)
    return {
        "runs": len(rows),
        "blocked": sum(1 for row in rows if row.get("outcome") == "blocked"),
        "ready_to_finish": sum(1 for row in rows if row.get("outcome") == "ready_to_finish"),
        "avg_iterations": round(total_iterations / len(rows), 2) if rows else 0.0,
    }


def _suggested_actions(
    agentpack_dir: Path,
    task_text: str,
    context: ContextHealth,
    learning: list[LearningArtifact],
    benchmarks: BenchmarkSummary,
    feedback_rows: list[dict[str, Any]],
) -> list[SuggestedAction]:
    actions: list[SuggestedAction] = []
    if not agentpack_dir.exists():
        actions.append(
            SuggestedAction(
                label="Initialize AgentPack",
                command="agentpack init --yes",
                reason="No .agentpack directory exists.",
            )
        )
    if not task_text:
        actions.append(
            SuggestedAction(
                label="Start a task",
                command='agentpack work "describe the task"',
                reason="No current task found.",
            )
        )
    if context.status in {"missing", "stale"}:
        actions.append(
            SuggestedAction(
                label="Refresh context",
                command="agentpack pack --task auto",
                reason=f"Context is {context.status}.",
            )
        )
    if not any(item.exists for item in learning):
        actions.append(
            SuggestedAction(
                label="Generate learning notes",
                command="agentpack learn",
                reason="No learning artifacts found.",
            )
        )
    if not benchmarks.averages:
        actions.append(
            SuggestedAction(
                label="Initialize benchmarks",
                command="agentpack benchmark --init",
                reason="No benchmark metrics found.",
            )
        )
    if not feedback_rows:
        actions.append(
            SuggestedAction(
                label="Record skill feedback",
                command='agentpack skills feedback --task "..." --recommended-skill skill-name --user-feedback helpful',
                reason="No skill feedback found.",
            )
        )
    return actions


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _result_status(result: Any) -> str:
    if result is None:
        return ""
    return "passed" if getattr(result, "returncode", 1) == 0 else "failed"


def _loop_next_action(status: str, task: str, runner: str, has_verification: bool) -> str:
    if not runner:
        return 'agentpack work "..." --run --runner "..."'
    if not has_verification:
        return f'agentpack work "{task}" --run --verify "pytest -q"'
    if status == "ready_to_finish":
        return "agentpack finish --since main"
    if status == "blocked":
        return "agentpack dashboard"
    return f'agentpack work "{task}" --run'
