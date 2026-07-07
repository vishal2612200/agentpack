from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack import __version__
from agentpack.core.config import load_config
from agentpack.core.changed_paths import clear_changed_paths, read_changed_paths
from agentpack.core.ignore import DEFAULT_AGENTIGNORE, load_spec
from agentpack.core.scanner import scan, scan_incremental
from agentpack.core.snapshot import build_snapshot, save_snapshot, load_snapshot
from agentpack.core.diff import diff_snapshots
from agentpack.core import git
from agentpack.core.command_surface import refresh_commands
from agentpack.core.context_pack import (
    compact_selected_file_payloads,
    enrich_call_site_scores,
    select_files,
    save_pack_metadata,
    load_pack_metadata,
)
from agentpack.core.citations import (
    citation_manifest_relpath,
    collect_pack_citations,
    write_citation_manifest,
)
from agentpack.core.execution_state import build_execution_state, compact_execution_state
from agentpack.core.pack_handoff import build_pack_handoff
from agentpack.core.models import (
    BroadContext,
    ContextPack,
    DependencyGraph,
    FileInfo,
    OmittedRelevantFile,
    Receipt,
    ScanResult,
    SelectedFile,
)
from agentpack.core.modes import normalize_mode
from agentpack.core.pack_registry import build_pack_registry, write_pack_registry
from agentpack.core.task_map import build_task_map, task_map_for_path
from agentpack.core.task_freshness import normalize_task_text, read_task_md, task_hash, task_metadata
from agentpack.core.thread_context import (
    append_thread_index,
    build_thread_index_row,
    detect_conflicts,
    read_task_status,
    resolve_thread_id,
    thread_paths,
)
from agentpack.core.token_contract import build_token_contract
from agentpack.core.token_estimator import estimate_tokens
from agentpack.learning.feedback import ranking_feedback_boosts
from agentpack.renderers.markdown import render_claude, render_generic
from agentpack.analysis.ranking import (
    build_keyword_plan,
    persist_keyword_plan_stats,
    score_files,
    enrich_keyword_weights_from_files,
    boost_paired_tests,
    boost_api_endpoint_pairs,
    boost_frontend_api_consumers,
    boost_cross_layer_related,
    boost_monorepo_workspaces,
    boost_recall_neighbors,
    boost_second_pass_expansion,
    generic_task_term_ratio,
)
from agentpack.analysis.broad_context import build_broad_context
from agentpack.analysis.context_intent import broad_context_enabled, infer_context_intent
from agentpack.analysis.repo_map import build_repo_map
from agentpack.analysis.monorepo import (
    detect_workspace_dependency_edges,
    detect_workspace_roots,
    normalize_workspace,
)
from agentpack.analysis.task_classifier import classify_task
from agentpack.analysis.tests import find_related_tests
from agentpack.analysis import dependency_graph as dep_graph_mod
from agentpack.summaries.base import build_all_summaries
from agentpack.session.events import record_event
from agentpack.session.references import collect_repo_issue_references


@dataclass
class PackRequest:
    root: Path
    agent: str
    task: str
    mode: str
    budget: int
    since: str | None
    refresh: bool
    task_source: str = "explicit"
    workspace: str | None = None
    thread_id: str | None = None
    output_path: Path | None = None
    write_canonical: bool = True


@dataclass
class PackResult:
    pack: ContextPack
    out_path: Path
    phase_times: dict[str, float]
    packed_tokens: int
    raw_tokens: int
    saving_pct: float
    changed_files: list[str]
    scan_result: ScanResult


@dataclass
class ChangeSet:
    """Result of change detection: snapshot diff combined with git diff."""
    all_changed: set[str]
    git_staged: set[str]
    recently_modified: list[str]
    source: str
    current_snap: dict[str, Any] = field(default_factory=dict)


@dataclass
class RankResult:
    """Result of keyword extraction and file scoring."""
    keywords: set[str]
    keyword_plan: Any
    generic_ratio: float
    task_class: str
    task_class_confidence: float
    task_class_signals: list[str]
    scored: list[tuple[Any, float, list[str]]]


@dataclass
class PackPlan:
    """Shared planning output used by both pack and explain."""
    task: str
    requested_mode: str
    mode: str
    budget: int
    scan_result: ScanResult
    summaries: dict[str, Any]
    dep_graph: DependencyGraph
    all_changed: set[str]
    git_staged: set[str]
    recently_modified: list[str]
    keywords: set[str]
    keyword_plan: Any
    generic_task_ratio: float
    task_class: str
    task_class_confidence: float
    task_class_signals: list[str]
    context_intent: str
    broad_context: BroadContext | None
    changed_files_source: str
    repo_map: str
    workspace_roots: list[str]
    workspace_dependency_edges: dict[str, set[str]]
    workspace: str | None
    scored: list[tuple[Any, float, list[str]]]
    selected: list[SelectedFile]
    receipts: list[Receipt]
    omitted_relevant_files: list[OmittedRelevantFile]
    phase_times: dict[str, float]
    mode_warning: str | None = None
    current_snap: dict[str, Any] = field(default_factory=dict)


class ChangeDetector:
    """Combines snapshot diff + git diff → ChangeSet of changed paths."""

    def detect(
        self,
        packable: list[FileInfo],
        root: Path,
        since: str | None,
        previous_snap: dict | None = None,
    ) -> ChangeSet:
        current_snap = build_snapshot(packable)
        if previous_snap is None:
            previous_snap = load_snapshot(root)
        if previous_snap is None:
            changed_from_snap: set[str] = set()
            deleted_from_snap: set[str] = set()
        else:
            snap_diff = diff_snapshots(previous_snap, current_snap)
            changed_from_snap = set(snap_diff.added + snap_diff.modified)
            deleted_from_snap = set(snap_diff.deleted)

        git_changed: set[str] = set()
        git_staged: set[str] = set()
        recently_modified: list[str] = []

        if git.is_git_repo(root):
            if since:
                git_changed = git.changed_files_since(root, since)
            else:
                git_changed = git.changed_files(root)
            git_staged = git_changed
            recently_modified = git.recently_modified_files(root)
        packable_paths = {fi.path for fi in packable}
        previous_paths = set((previous_snap or {}).get("files", {}))
        deleted_changed = deleted_from_snap | {path for path in git_changed if path in previous_paths and path not in packable_paths}
        all_changed = ((changed_from_snap | git_changed) & packable_paths) | deleted_changed
        git_staged = git_staged & packable_paths
        recently_modified = [path for path in recently_modified if path in packable_paths]

        return ChangeSet(
            all_changed=all_changed,
            git_staged=git_staged,
            recently_modified=recently_modified,
            source=_change_source(root, since, changed_from_snap & packable_paths, git_changed & packable_paths),
            current_snap=current_snap,
        )


class FileRanker:
    """Extracts keywords from the task and scores files against them."""

    def rank(
        self,
        packable: list[FileInfo],
        changes: ChangeSet,
        dep_graph: DependencyGraph,
        task: str,
        cfg: Any,
        summaries: dict | None = None,
        root: Path | None = None,
        workspace_roots: list[str] | None = None,
        workspace_dependency_edges: dict[str, set[str]] | None = None,
    ) -> RankResult:
        from agentpack.core import git as _git
        keyword_plan = build_keyword_plan(
            task,
            files=packable,
            summaries=summaries or {},
            root=root,
            workspace_roots=workspace_roots,
        )
        keyword_weights = dict(keyword_plan.weights)
        keyword_weights = enrich_keyword_weights_from_files(keyword_weights, changes.all_changed, packable)
        keyword_plan.weights = keyword_weights
        keywords = set(keyword_weights)
        generic_ratio = generic_task_term_ratio(task)
        task_classification = classify_task(task)
        all_paths = {f.path for f in packable}

        for fi in packable:
            tests = find_related_tests(fi.path, all_paths)
            dep_graph.nodes[fi.path].tests = tests

        churn_counts: dict[str, int] = {}
        co_changed_paths: dict[str, int] = {}
        if root is not None and _git.is_git_repo(root):
            churn_counts = _git.file_churn_counts(root)
            co_changed_paths = _filter_co_changed_paths(
                root,
                _git.co_changed_files(root, changes.all_changed),
            )

        scored = score_files(
            packable,
            changed_paths=changes.all_changed,
            staged_paths=changes.git_staged,
            recently_modified=changes.recently_modified,
            dep_graph=dep_graph,
            keywords=keyword_plan,
            include_tests=cfg.context.include_tests,
            include_configs=cfg.context.include_configs,
            weights=cfg.scoring,
            summaries=summaries,
            churn_counts=churn_counts,
            co_changed_paths=co_changed_paths,
        )
        scored = boost_monorepo_workspaces(
            scored,
            workspace_roots=workspace_roots or [],
            workspace_dependency_edges=workspace_dependency_edges or {},
            changed_paths=changes.all_changed,
            task=task,
            weights=cfg.scoring,
        )
        scored = boost_recall_neighbors(scored, dep_graph, changes.all_changed, weights=cfg.scoring)
        scored = boost_second_pass_expansion(scored, dep_graph, keyword_plan, weights=cfg.scoring)
        scored = boost_frontend_api_consumers(scored, summaries, keyword_plan, weights=cfg.scoring)
        scored = boost_api_endpoint_pairs(scored, keyword_plan, weights=cfg.scoring)
        scored = boost_cross_layer_related(scored, keyword_plan, weights=cfg.scoring)
        scored = boost_paired_tests(scored, weights=cfg.scoring)
        if root is not None:
            scored = _apply_history_penalties(
                root,
                scored,
                changes.all_changed,
                generic_ratio=generic_ratio,
            )
            scored = _apply_ranking_feedback_boosts(root, scored, task, changes.all_changed, cfg)
        return RankResult(
            keywords=keywords,
            keyword_plan=keyword_plan,
            generic_ratio=generic_ratio,
            task_class=task_classification.kind,
            task_class_confidence=task_classification.confidence,
            task_class_signals=task_classification.signals,
            scored=scored,
        )


def _scan_metadata(
    root: Path,
    cfg: Any,
    *,
    include_globs: list[str],
    exclude_globs: list[str],
    generated_paths: set[str],
    workspace: str | None,
) -> dict[str, Any]:
    ignore_path = root / cfg.project.ignore_file
    ignore_text = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else DEFAULT_AGENTIGNORE
    scan_config = {
        "ignore_file": cfg.project.ignore_file,
        "ignore_hash": _hash_text(ignore_text),
        "include_globs": include_globs,
        "exclude_globs": exclude_globs,
        "max_file_tokens": cfg.context.max_file_tokens,
        "workspace": workspace,
    }
    return {
        "scan_fingerprint": _hash_json(scan_config),
        "git_branch": git.current_branch(root) if git.is_git_repo(root) else None,
        "git_sha": git.current_sha(root) if git.is_git_repo(root) else None,
        "scan_config": scan_config,
    }


def _full_scan_reason(
    root: Path,
    cfg: Any,
    previous_snap: dict[str, Any] | None,
    scan_metadata: dict[str, Any],
    dirty_paths: set[str],
    ledger_paths: set[str],
    *,
    force_refresh: bool,
) -> str | None:
    if not cfg.context.incremental_scan:
        return "incremental scan disabled"
    if force_refresh:
        return "refresh requested"
    if previous_snap is None:
        return "no previous snapshot"
    previous_files = previous_snap.get("files") if isinstance(previous_snap.get("files"), dict) else {}
    if not previous_files:
        return "previous snapshot has no files"
    if not git.is_git_repo(root):
        return "git unavailable"

    previous_meta = previous_snap.get("metadata") if isinstance(previous_snap.get("metadata"), dict) else {}
    if previous_meta.get("scan_fingerprint") != scan_metadata.get("scan_fingerprint") and (
        _normalized_scan_config(previous_meta.get("scan_config"))
        != _normalized_scan_config(scan_metadata.get("scan_config"))
    ):
        return "scan config or ignore rules changed"
    if previous_meta.get("git_branch") != scan_metadata.get("git_branch"):
        return "git branch changed"
    if previous_meta.get("git_sha") != scan_metadata.get("git_sha") and not ledger_paths:
        return "git HEAD changed"
    if len(dirty_paths) > cfg.context.max_incremental_changed_files:
        return f"too many dirty paths ({len(dirty_paths)})"
    if _snapshot_age_seconds(previous_snap) > cfg.context.full_scan_interval_seconds:
        return "periodic full scan interval reached"
    return None


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_scan_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized = dict(value)
    normalized.pop("generated_paths", None)
    return normalized


def _hash_json(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return _hash_text(raw)


def _snapshot_age_seconds(snapshot: dict[str, Any]) -> float:
    created_at = snapshot.get("created_at")
    if not isinstance(created_at, str):
        return float("inf")
    try:
        if created_at.endswith("Z"):
            created_at = created_at[:-1] + "+00:00"
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())
    except ValueError:
        return float("inf")


def _read_agent_lessons(root: Path, cfg: Any, limit: int = 2000) -> str:
    if not getattr(cfg.learning, "inject_agent_lessons", True):
        return ""
    path = root / cfg.learning.agent_lessons_output
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text[:limit]


class PackPlanner:
    """Runs scan → summarize → graph → rank → select; shared by pack and explain."""

    def plan(self, request: PackRequest) -> PackPlan:
        root = request.root
        cfg = load_config(root)
        requested_mode = request.mode or cfg.context.default_mode
        normalized_mode = normalize_mode(requested_mode)
        mode_warning = (
            "Legacy mode 'minimal' was mapped to 'balanced'."
            if requested_mode.strip().lower() != normalized_mode
            else None
        )
        request = replace(request, mode=normalized_mode)
        effective_budget = _resolve_effective_budget(request, cfg)
        ignore_spec = load_spec(root / cfg.project.ignore_file)
        phase_times: dict[str, float] = {}

        t0 = time.perf_counter()
        previous_snap = load_snapshot(root)
        workspace_roots = detect_workspace_roots(root)
        workspace = _resolve_workspace(root, request.workspace, workspace_roots)
        workspace_dependency_edges = detect_workspace_dependency_edges(root, workspace_roots)
        include_globs = _workspace_include_globs(workspace, cfg.project.include_globs or [])
        generated_paths = AdapterRegistry.generated_output_paths(root, cfg)
        scan_metadata = _scan_metadata(
            root,
            cfg,
            include_globs=include_globs or [],
            exclude_globs=cfg.project.exclude_globs or [],
            generated_paths=generated_paths,
            workspace=workspace,
        )
        ledger_paths = read_changed_paths(root)
        dirty_paths = (git.dirty_files(root) if git.is_git_repo(root) else set()) | ledger_paths
        full_scan_reason = _full_scan_reason(
            root,
            cfg,
            previous_snap,
            scan_metadata,
            dirty_paths,
            ledger_paths,
            force_refresh=request.refresh,
        )
        if full_scan_reason:
            scan_result = scan(
                root, ignore_spec, cfg.context.max_file_tokens,
                previous_snapshot=previous_snap,
                include_globs=include_globs or None,
                exclude_globs=cfg.project.exclude_globs or None,
                always_skip_paths=generated_paths,
            )
            scan_result.full_scan_reason = full_scan_reason
        else:
            scan_result = scan_incremental(
                root,
                ignore_spec,
                changed_paths=dirty_paths,
                max_file_tokens=cfg.context.max_file_tokens,
                previous_snapshot=previous_snap,
                include_globs=include_globs or None,
                exclude_globs=cfg.project.exclude_globs or None,
                always_skip_paths=generated_paths,
            )
        phase_times["scan"] = time.perf_counter() - t0

        packable = scan_result.packable

        t0 = time.perf_counter()
        summaries_objs = build_all_summaries(packable, root)
        summaries = {p: s.model_dump() for p, s in summaries_objs.items()}
        phase_times["summarize"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        dep_graph = dep_graph_mod.build(packable, root, summaries=summaries)
        phase_times["deps"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        changes = ChangeDetector().detect(packable, root, request.since, previous_snap=previous_snap)
        pr_paths = _github_pr_paths(root, request.task) if _is_pr_review_task(request.task) else set()
        changes = _apply_github_pr_changed_paths(changes, pr_paths, packable)
        changes.current_snap["metadata"] = {
            **scan_metadata,
            "scan_mode": scan_result.scan_mode,
            "rehashed_count": scan_result.rehashed_count,
            "reused_count": scan_result.reused_count,
            "full_scan_reason": scan_result.full_scan_reason,
        }
        phase_times["changes"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        rank_result = FileRanker().rank(
            packable,
            changes,
            dep_graph,
            request.task,
            cfg,
            summaries=summaries,
            root=root,
            workspace_roots=workspace_roots,
            workspace_dependency_edges=workspace_dependency_edges,
        )
        if pr_paths:
            rank_result.scored = _boost_github_pr_paths(rank_result.scored, pr_paths)
        rank_result.scored = _apply_scope_penalties(
            rank_result.scored,
            request.task,
            changes.all_changed,
            generic_ratio=rank_result.generic_ratio,
        )
        if not changes.all_changed:
            rank_result.scored = _apply_no_live_precision_guard(
                rank_result.scored,
                rank_result.generic_ratio,
                mode=request.mode,
            )
            rank_result.scored = _apply_scope_penalties(
                rank_result.scored,
                request.task,
                changes.all_changed,
                generic_ratio=rank_result.generic_ratio,
                no_live_changes=True,
            )
        effective_mode, resolved_mode_warning = _resolve_effective_mode(
            root,
            request.mode,
            rank_result.generic_ratio,
            no_live_changes=not changes.all_changed,
        )
        mode_warning = mode_warning or resolved_mode_warning
        phase_times["rank"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        repo_map_budget = _repo_map_budget_for_mode(effective_mode, effective_budget)
        repo_map = build_repo_map(
            files=packable,
            scored=rank_result.scored,
            summaries=summaries,
            dep_graph=dep_graph,
            changed_paths=changes.all_changed,
            budget_tokens=repo_map_budget,
        )
        phase_times["repo_map"] = time.perf_counter() - t0
        selection_budget = max(0, effective_budget - estimate_tokens(repo_map))

        context_intent = infer_context_intent(request.task, task_mode=rank_result.keyword_plan.task_kind)
        broad_context = None
        broad_setting = os.environ.get("AGENTPACK_BROAD_CONTEXT") or getattr(cfg.context, "broad_context", "auto")
        if broad_context_enabled(broad_setting, context_intent):
            t0 = time.perf_counter()
            broad_budget = max(500, int(effective_budget * max(0, cfg.context.broad_context_budget_pct) / 100))
            broad_context = build_broad_context(
                files=packable,
                summaries=summaries,
                scored=rank_result.scored,
                intent=context_intent,
                max_module_summaries=max(0, cfg.context.max_module_summaries),
                max_inventory_files=max(0, cfg.context.max_inventory_files),
                budget_tokens=broad_budget,
            )
            selection_budget = max(0, selection_budget - estimate_tokens(broad_context.model_dump_json()))
            phase_times["broad_context"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        omitted_relevant_files: list[OmittedRelevantFile] = []
        selected, receipts = select_files(
            files=packable,
            scored=rank_result.scored,
            changed_paths=changes.all_changed,
            summaries=summaries,
            mode=effective_mode,  # type: ignore[arg-type]
            budget=selection_budget,
            max_file_tokens=cfg.context.max_file_tokens,
            keywords=rank_result.keywords,
            min_summary_score=_guarded_summary_score_floor(
                root, cfg, effective_mode, rank_result.generic_ratio, no_live_changes=not changes.all_changed
            ),
            max_summary_files=_guarded_summary_cap(
                root,
                cfg,
                effective_mode,
                rank_result.generic_ratio,
                no_live_changes=not changes.all_changed,
                effective_budget=effective_budget,
                task_kind=rank_result.keyword_plan.task_kind,
                has_literal_phrases=bool(rank_result.keyword_plan.literal_phrases),
            ),
            max_weak_signal_files=_guarded_weak_signal_cap(
                root,
                effective_mode,
                rank_result.generic_ratio,
                no_live_changes=not changes.all_changed,
                effective_budget=effective_budget,
            ),
            strict_summary_selection=_strict_summary_selection(
                root,
                no_live_changes=not changes.all_changed,
                mode=effective_mode,
            ),
            omitted_relevant_files=omitted_relevant_files,
        )
        expanded_scored, call_site_count = enrich_call_site_scores(
            rank_result.scored,
            selected,
            summaries,
            changes.all_changed,
        )
        if call_site_count:
            omitted_relevant_files = []
            selected, receipts = select_files(
                files=packable,
                scored=expanded_scored,
                changed_paths=changes.all_changed,
                summaries=summaries,
                mode=effective_mode,  # type: ignore[arg-type]
                budget=selection_budget,
                max_file_tokens=cfg.context.max_file_tokens,
                keywords=rank_result.keywords,
                min_summary_score=_guarded_summary_score_floor(
                    root, cfg, effective_mode, rank_result.generic_ratio, no_live_changes=not changes.all_changed
                ),
                max_summary_files=_guarded_summary_cap(
                    root,
                    cfg,
                    effective_mode,
                    rank_result.generic_ratio,
                    no_live_changes=not changes.all_changed,
                    effective_budget=effective_budget,
                    task_kind=rank_result.keyword_plan.task_kind,
                    has_literal_phrases=bool(rank_result.keyword_plan.literal_phrases),
                ),
                max_weak_signal_files=_guarded_weak_signal_cap(
                    root,
                    effective_mode,
                    rank_result.generic_ratio,
                    no_live_changes=not changes.all_changed,
                    effective_budget=effective_budget,
                ),
                strict_summary_selection=_strict_summary_selection(
                    root,
                    no_live_changes=not changes.all_changed,
                    mode=effective_mode,
                ),
                omitted_relevant_files=omitted_relevant_files,
            )
            rank_result.scored = expanded_scored
        selected = compact_selected_file_payloads(
            selected,
            files=packable,
            summaries=summaries,
            scored=rank_result.scored,
            task=request.task,
            changed_paths=changes.all_changed,
        )
        phase_times["select"] = time.perf_counter() - t0

        return PackPlan(
            task=request.task,
            requested_mode=requested_mode,
            mode=effective_mode,
            budget=effective_budget,
            scan_result=scan_result,
            summaries=summaries,
            dep_graph=dep_graph,
            all_changed=changes.all_changed,
            git_staged=changes.git_staged,
            recently_modified=changes.recently_modified,
            keywords=rank_result.keywords,
            keyword_plan=rank_result.keyword_plan,
            generic_task_ratio=rank_result.generic_ratio,
            task_class=rank_result.task_class,
            task_class_confidence=rank_result.task_class_confidence,
            task_class_signals=rank_result.task_class_signals,
            context_intent=context_intent,
            broad_context=broad_context,
            changed_files_source=changes.source,
            repo_map=repo_map,
            workspace_roots=workspace_roots,
            workspace_dependency_edges=workspace_dependency_edges,
            workspace=workspace,
            scored=rank_result.scored,
            selected=selected,
            receipts=receipts,
            omitted_relevant_files=omitted_relevant_files,
            phase_times=phase_times,
            mode_warning=mode_warning,
            current_snap=changes.current_snap,
        )


class AdapterRegistry:
    """Maps agent names to adapter instances; extensible without touching PackService."""

    @staticmethod
    def _factories(cfg: Any) -> dict[str, Any]:
        from agentpack.adapters.antigravity import AntigravityAdapter
        from agentpack.adapters.claude import ClaudeAdapter
        from agentpack.adapters.codex import CodexAdapter
        from agentpack.adapters.cursor import CursorAdapter
        from agentpack.adapters.windsurf import WindsurfAdapter
        from agentpack.adapters.generic import GenericAdapter

        return {
            "antigravity": lambda: AntigravityAdapter(),
            "claude": lambda: ClaudeAdapter(cfg.agents.claude.output),
            "cursor": lambda: CursorAdapter(cfg.agents.generic.output),
            "windsurf": lambda: WindsurfAdapter(cfg.agents.generic.output),
            "codex": lambda: CodexAdapter(cfg.agents.generic.output),
            "generic": lambda: GenericAdapter(cfg.agents.generic.output),
        }

    @staticmethod
    def get(agent: str, cfg: Any) -> Any:
        from agentpack.adapters.generic import GenericAdapter

        adapters = AdapterRegistry._factories(cfg)
        return adapters.get(agent, lambda: GenericAdapter(cfg.agents.generic.output))()

    @staticmethod
    def generated_output_paths(root: Path, cfg: Any) -> set[str]:
        paths: set[str] = set()
        for factory in AdapterRegistry._factories(cfg).values():
            try:
                out_path = factory().output_path(root)
                paths.add(str(out_path.relative_to(root)).replace("\\", "/"))
            except (OSError, ValueError):
                continue
        paths.update(
            {
                cfg.learning.markdown_output,
                cfg.learning.daily_output,
                cfg.learning.skill_map_output,
                cfg.learning.agent_lessons_output,
                cfg.learning.llm_prompt_output,
                cfg.learning.pr_comment_output,
                cfg.learning.dashboard_output,
                cfg.learning.team_lessons_output,
                cfg.learning.feedback_output,
                cfg.learning.ranking_feedback_output,
                cfg.learning.episodic_cases_output,
                cfg.runtime.pack_registry_output,
                cfg.runtime.session_events_output,
                ".agent/skills/agentpack/citations.json",
            }
        )
        return paths


class PackService:
    """Materializes a plan from PackPlanner into a written context file."""

    def run(self, request: PackRequest) -> PackResult:
        root = request.root
        cfg = load_config(root)
        resolved_thread_id = resolve_thread_id(request.thread_id, env={})
        scoped_paths = thread_paths(root, resolved_thread_id)

        plan = PackPlanner().plan(request)

        packable = plan.scan_result.packable
        all_tokens = sum(f.estimated_tokens for f in plan.scan_result.all_files)
        raw_tokens = sum(f.estimated_tokens for f in packable)
        previous_metadata = load_pack_metadata(root, scoped_paths.metadata if scoped_paths else None)
        delta_summary = _compute_delta_summary(previous_metadata, plan.selected, plan.all_changed)
        packed_tokens = (
            sum(_sf_tokens(sf) for sf in plan.selected)
            + estimate_tokens(plan.repo_map)
            + estimate_tokens(delta_summary)
        )
        saving_pct = max(0.0, (1 - packed_tokens / all_tokens) * 100) if all_tokens > 0 else 0.0

        all_redaction_warnings = [w for sf in plan.selected for w in sf.redaction_warnings]
        freshness = _build_freshness_metadata(
            root,
            request=request,
            plan=plan,
            snapshot_root_hash=plan.current_snap["root_hash"],
        )
        if delta_summary:
            freshness["delta_summary"] = delta_summary
        freshness_warnings = _freshness_warnings(root, request, freshness)
        execution_state = build_execution_state(root, scoped_paths)
        if scoped_paths:
            freshness["thread_id"] = scoped_paths.thread_id
            freshness["owner_thread_id"] = scoped_paths.thread_id
            freshness["task_status"] = read_task_status(root, scoped_paths.thread_id) or "active"
            freshness["thread_paths"] = scoped_paths.as_relative_dict(root)
        thread_row = None
        concurrent_context: dict[str, Any] = {}
        if scoped_paths:
            thread_row = build_thread_index_row(
                root=root,
                thread_id=scoped_paths.thread_id,
                task=request.task,
                branch=str((execution_state.get("git") or {}).get("branch") or ""),
                selected_files=[sf.path for sf in plan.selected],
                dirty_files=sorted(git.dirty_files(root)) if git.is_git_repo(root) else [],
                status=str((execution_state.get("task") or {}).get("status") or "unknown"),
            )
            concurrent_context = detect_conflicts(root, thread_row)

        pack_obj = ContextPack(
            task=request.task,
            agent=request.agent,
            mode=plan.mode,  # type: ignore[arg-type]
            task_class=plan.task_class,
            budget=plan.budget,
            token_estimate=packed_tokens,
            raw_repo_tokens=all_tokens,
            after_ignore_tokens=raw_tokens,
            estimated_savings_percent=saving_pct,
            repo_map=plan.repo_map,
            broad_context=plan.broad_context,
            delta_summary=delta_summary,
            agent_lessons=_read_agent_lessons(root, cfg),
            changed_files=sorted(plan.all_changed),
            selected_files=plan.selected,
            receipts=plan.receipts if cfg.context.include_receipts else [],
            omitted_relevant_files=plan.omitted_relevant_files,
            pack_handoff_omitted_relevant_files=plan.omitted_relevant_files,
            redaction_warnings=all_redaction_warnings,
            stale=False,
            freshness=freshness,
            freshness_warnings=freshness_warnings,
            execution_state=execution_state,
            concurrent_context=concurrent_context,
        )
        registry = build_pack_registry(
            pack_obj,
            packable,
            max_records=cfg.runtime.max_registry_records,
        )
        pack_obj.task_map = build_task_map(pack_obj, plan.dep_graph, registry).model_dump(mode="json")

        adapter = AdapterRegistry.get(request.agent, cfg)
        packed_tokens = _fit_rendered_budget(pack_obj, adapter)
        saving_pct = max(0.0, (1 - packed_tokens / all_tokens) * 100) if all_tokens > 0 else 0.0
        pack_obj.estimated_savings_percent = saving_pct
        registry = build_pack_registry(
            pack_obj,
            packable,
            max_records=cfg.runtime.max_registry_records,
        )
        pack_obj.task_map = build_task_map(pack_obj, plan.dep_graph, registry).model_dump(mode="json")
        packed_tokens = _settle_rendered_token_estimate(pack_obj, adapter)
        pack_obj.estimated_savings_percent = max(0.0, (1 - packed_tokens / all_tokens) * 100) if all_tokens > 0 else 0.0
        saving_pct = pack_obj.estimated_savings_percent

        t0 = time.perf_counter()
        if scoped_paths:
            planned_out_path = scoped_paths.context if request.agent == "generic" else scoped_paths.context_claude
        elif request.output_path is not None:
            planned_out_path = request.output_path
        elif plan.workspace:
            safe_workspace = plan.workspace.replace("/", "__").replace("\\", "__")
            planned_out_path = root / ".agentpack" / "workspaces" / safe_workspace / "context.md"
        else:
            planned_out_path = adapter.output_path(root)
        citation_manifest_path = citation_manifest_relpath(root, planned_out_path)
        pack_obj.freshness["citation_manifest_path"] = citation_manifest_path
        pack_obj.citations = collect_pack_citations(pack_obj)
        if scoped_paths:
            out_path = _write_thread_context(pack_obj, root, scoped_paths, request.agent)
        elif request.output_path is not None:
            out_path = request.output_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(render_generic(pack_obj), encoding="utf-8")
        else:
            out_path = adapter.write(pack_obj, root)
            if request.write_canonical:
                _write_canonical_context(pack_obj, root, out_path)
        if plan.workspace and not scoped_paths:
            out_path = _write_workspace_context(pack_obj, root, plan.workspace)
        manifest_path = write_citation_manifest(pack_obj, root, out_path)
        citation_manifest_path = str(manifest_path.relative_to(root)).replace("\\", "/")
        pack_obj.freshness["citation_manifest_path"] = citation_manifest_path
        citation_summary = {
            "citation_count": len(pack_obj.citations),
            "selected_files_with_citations": sum(1 for sf in pack_obj.selected_files if sf.citations),
            "manifest_path": citation_manifest_path,
        }
        selected_files_meta = _selected_file_metadata(pack_obj.selected_files, pack_obj.task_map)
        token_contract = build_token_contract(
            budget=plan.budget,
            token_estimate=packed_tokens,
            raw_repo_tokens=all_tokens,
            after_ignore_tokens=raw_tokens,
            selected_files=selected_files_meta,
            context_path=str(out_path.relative_to(root)),
            mode=plan.mode,
        )
        plan.phase_times["render"] = time.perf_counter() - t0
        persist_keyword_plan_stats(root, request.task, plan.keyword_plan)

        save_snapshot(plan.current_snap, root)
        clear_changed_paths(root)
        save_pack_metadata(
            root,
            context_path=str(out_path.relative_to(root)),
            snapshot_root_hash=plan.current_snap["root_hash"],
            task=request.task,
            agent=request.agent,
            mode=plan.mode,
            requested_mode=plan.requested_mode,
            budget=plan.budget,
            token_estimate=packed_tokens,
            freshness=pack_obj.freshness,
            freshness_warnings=pack_obj.freshness_warnings,
            selected_files=selected_files_meta,
            pack_handoff=build_pack_handoff(pack_obj),
            execution_state=pack_obj.execution_state,
            concurrent_context=pack_obj.concurrent_context,
            citation_manifest_path=citation_manifest_path,
            citation_summary=citation_summary,
            token_contract=token_contract,
            task_map=pack_obj.task_map,
            metadata_path=scoped_paths.metadata if scoped_paths else None,
        )
        write_pack_registry(
            root,
            registry,
            output_path=cfg.runtime.pack_registry_output,
        )
        issue_reference_details = collect_repo_issue_references(root, request.task)
        record_event(
            root,
            "pack",
            {
                "task": request.task,
                "issue_references": [item.ref for item in issue_reference_details],
                "issue_reference_details": [item.to_dict() for item in issue_reference_details],
                "agent": request.agent,
                "mode": plan.mode,
                "packed_tokens": packed_tokens,
                "raw_tokens": all_tokens,
                "selected_files": len(pack_obj.selected_files),
                "omitted_files": len(pack_obj.omitted_relevant_files),
                "changed_files": len(pack_obj.changed_files),
                "context_path": str(out_path.relative_to(root)),
                "citation_manifest_path": citation_manifest_path,
                "citation_count": len(pack_obj.citations),
            },
            output_path=cfg.runtime.session_events_output,
        )
        if thread_row:
            append_thread_index(root, thread_row)
        excluded_receipts = [r for r in pack_obj.receipts if r.action == "excluded"]
        # Budget-cut: files that scored OK but didn't fit — more useful signal than "score too low"
        budget_cut = [r.path for r in pack_obj.receipts if r.reason == "budget exhausted"][:10]
        _record_metrics(
            root,
            task=request.task,
            mode=plan.mode,
            phase_times=plan.phase_times,
            packed_tokens=packed_tokens,
            raw_tokens=all_tokens,
            saving_pct=saving_pct,
            selected_count=len(pack_obj.selected_files),
            changed_count=len(plan.all_changed),
            selected_paths=[sf.path for sf in pack_obj.selected_files],
            selected_tokens={sf.path: _sf_tokens(sf) for sf in pack_obj.selected_files},
            selected_modes={sf.path: sf.include_mode for sf in pack_obj.selected_files},
            selected_hints=[{"path": sf.path, "why": sf.reasons[0] if sf.reasons else ""} for sf in pack_obj.selected_files[:8]],
            current_changed=plan.all_changed,
            task_class=plan.task_class,
            workspace=plan.workspace,
            workspace_roots=plan.workspace_roots,
            excluded_count=len(excluded_receipts),
            excluded_paths=budget_cut,
            scan_mode=plan.scan_result.scan_mode,
            scan_rehashed_count=plan.scan_result.rehashed_count,
            scan_reused_count=plan.scan_result.reused_count,
            full_scan_reason=plan.scan_result.full_scan_reason,
        )

        return PackResult(
            pack=pack_obj,
            out_path=out_path,
            phase_times=plan.phase_times,
            packed_tokens=packed_tokens,
            raw_tokens=all_tokens,
            saving_pct=saving_pct,
            changed_files=sorted(plan.all_changed),
            scan_result=plan.scan_result,
        )


def _write_canonical_context(pack: ContextPack, root: Path, out_path: Path) -> None:
    """Keep .agentpack/context.md fresh even when the target agent writes elsewhere."""
    canonical_path = root / ".agentpack" / "context.md"
    try:
        if out_path.resolve() == canonical_path.resolve():
            return
    except OSError:
        if out_path == canonical_path:
            return
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.write_text(render_generic(pack), encoding="utf-8")


def _write_thread_context(pack: ContextPack, root: Path, paths: Any, agent: str) -> Path:
    paths.base.mkdir(parents=True, exist_ok=True)
    paths.context.write_text(render_generic(pack), encoding="utf-8")
    paths.context_claude.write_text(render_claude(pack), encoding="utf-8")
    return paths.context if agent == "generic" else paths.context_claude


def _write_workspace_context(pack: ContextPack, root: Path, workspace: str) -> Path:
    safe = workspace.replace("/", "__").replace("\\", "__")
    workspace_path = root / ".agentpack" / "workspaces" / safe / "context.md"
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.write_text(render_generic(pack), encoding="utf-8")
    return workspace_path


def _resolve_workspace(root: Path, workspace: str | None, workspace_roots: list[str]) -> str | None:
    value = normalize_workspace(workspace)
    if value is None:
        return None
    if value in workspace_roots or (root / value).is_dir():
        return value
    known = ", ".join(workspace_roots[:8]) if workspace_roots else "none detected"
    raise ValueError(f"Unknown workspace '{value}'. Known workspaces: {known}")


def _workspace_include_globs(workspace: str | None, configured: list[str]) -> list[str]:
    if workspace is None:
        return configured
    return [f"{workspace}/**"]


def _selected_file_metadata(selected: list[SelectedFile], task_map: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "path": sf.path,
            "mode": sf.include_mode,
            "score": round(sf.score, 1),
            "why": sf.reasons[0] if sf.reasons else "",
            "reasons": sf.reasons,
            "tokens": _sf_tokens(sf),
            "symbols": _selected_symbol_metadata(sf),
            **_selected_task_map_metadata(task_map or {}, sf.path),
        }
        for sf in selected
    ]


def _selected_symbol_metadata(sf: SelectedFile) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sym in sf.symbols[:20]:
        rows.append(
            {
                "name": sym.name,
                "kind": sym.kind,
                "start_line": sym.start_line,
                "end_line": sym.end_line,
                "signature": sym.signature or "",
                "summary": sym.summary or "",
                "node_id": sym.node_id,
                "signature_hash": sym.signature_hash,
                "source_hash": sym.source_hash or sf.source_hash or "",
            }
        )
    return rows


def _selected_task_map_metadata(task_map: dict[str, Any], path: str) -> dict[str, Any]:
    item = task_map_for_path(task_map, path, "selected")
    if not item:
        return {}
    return {
        "risk_level": item.get("risk_level", ""),
        "risk_reasons": item.get("risk_reasons", []),
        "tests_to_run": item.get("tests_to_run", []),
        "may_break": item.get("may_break", []),
        "retrieve_ref": item.get("retrieve_ref", ""),
    }


def _sf_tokens(sf: SelectedFile) -> int:
    if sf.content:
        return estimate_tokens(sf.content)
    if sf.include_mode == "summary":
        return estimate_tokens(sf.summary) if sf.summary else 50
    parts: list[str] = []
    if sf.summary:
        parts.append(sf.summary)
    for sym in sf.symbols:
        if sym.signature:
            parts.append(sym.signature)
    return estimate_tokens("\n".join(parts)) if parts else 50


def _settle_rendered_token_estimate(pack: ContextPack, adapter: Any, passes: int = 3) -> int:
    """Measure final markdown, including tables, receipts, and renderer overhead."""
    token_estimate = pack.token_estimate
    for _ in range(passes):
        rendered_tokens = estimate_tokens(adapter.render(pack))
        if rendered_tokens == token_estimate:
            break
        pack.token_estimate = rendered_tokens
        token_estimate = rendered_tokens
    return token_estimate


def _fit_rendered_budget(pack: ContextPack, adapter: Any) -> int:
    token_estimate = _settle_rendered_token_estimate(pack, adapter)
    if token_estimate <= pack.budget:
        return token_estimate

    if pack.receipts:
        pack.receipts = []
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    if pack.broad_context:
        pack.broad_context = _compact_broad_context(pack.broad_context)
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    if pack.broad_context:
        pack.broad_context = None
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    if pack.repo_map:
        pack.repo_map = ""
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    if pack.delta_summary:
        pack.delta_summary = ""
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    if pack.execution_state:
        pack.execution_state = compact_execution_state(pack.execution_state)
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    if (pack.concurrent_context.get("conflicts") or []):
        pack.concurrent_context = {
            "thread_id": pack.concurrent_context.get("thread_id"),
            "active_threads": pack.concurrent_context.get("active_threads", 0),
            "conflict_count": len(pack.concurrent_context.get("conflicts") or []),
            "warning": True,
        }
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    if len(pack.omitted_relevant_files) > 5:
        pack.omitted_relevant_files = _rank_omitted_relevant_files(pack.omitted_relevant_files)[:5]
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    if pack.omitted_relevant_files:
        pack.omitted_relevant_files = []
        token_estimate = _settle_rendered_token_estimate(pack, adapter)
        if token_estimate <= pack.budget:
            return token_estimate

    while pack.selected_files and token_estimate > pack.budget:
        pack.selected_files.pop()
        token_estimate = _settle_rendered_token_estimate(pack, adapter)

    if token_estimate > pack.budget and (pack.freshness or pack.freshness_warnings):
        pack.freshness = {}
        pack.freshness_warnings = []
        token_estimate = _settle_rendered_token_estimate(pack, adapter)

    return token_estimate


def _rank_omitted_relevant_files(files: list[OmittedRelevantFile]) -> list[OmittedRelevantFile]:
    risk_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(files, key=lambda item: (risk_rank.get(item.risk, 2), -item.score, item.path))


def _repo_map_budget_for_mode(mode: str, effective_budget: int) -> int:
    caps = {"lite": 150, "balanced": 600, "deep": 900}
    return min(caps.get(mode, 500), max(0, effective_budget // 20))


def _resolve_effective_budget(request: PackRequest, cfg: Any) -> int:
    if request.budget > 0:
        return request.budget
    if request.mode == "lite":
        return cfg.context_lite.budget
    return cfg.context.default_budget


_FRONTEND_TASK_TERMS = {
    "component", "components", "frontend", "landing", "layout", "page", "pages",
    "preview", "previews", "public", "seo", "signup", "tool", "tools", "ui", "web",
}
_BACKEND_TASK_TERMS = {
    "api", "backend", "controller", "controllers", "cron", "database", "db", "job",
    "jobs", "queue", "schema", "schemas", "service", "services", "worker", "workers",
}
_FRONTEND_PATH_PREFIXES = (
    "src/app/", "src/components/", "src/pages/", "src/data/", "frontend/", "apps/web/", "web/",
)
_BACKEND_PATH_PREFIXES = (
    "backend/", "server/", "apps/api/",
)
_SCOPE_SUPPORT_PREFIXES = (
    "modified",
    "staged",
    "direct dependency of changed file",
    "reverse dependency",
    "recall neighbor",
    "historically co-changed",
    "has related tests",
    "test for",
    "workspace match",
)
_SCOPE_WEAK_ONLY_PREFIXES = (
    "filename keyword match",
    "symbol keyword match",
    "matched role keyword",
    "matched ranking keyword",
    "matched define",
    "matched call",
    "matched naming keyword",
)


def _task_tokens(task: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-zA-Z0-9]+", task.lower())
        if len(token) >= 3
    }


def _path_scope(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith(_FRONTEND_PATH_PREFIXES):
        return "frontend"
    if normalized.startswith(_BACKEND_PATH_PREFIXES):
        return "backend"
    return "other"


def _infer_task_scope(task: str, changed_paths: set[str]) -> str:
    frontend_changed = sum(1 for path in changed_paths if _path_scope(path) == "frontend")
    backend_changed = sum(1 for path in changed_paths if _path_scope(path) == "backend")
    if frontend_changed and not backend_changed:
        return "frontend_only"
    if backend_changed and not frontend_changed:
        return "backend_only"
    tokens = _task_tokens(task)
    frontend_terms = len(tokens & _FRONTEND_TASK_TERMS)
    backend_terms = len(tokens & _BACKEND_TASK_TERMS)
    if frontend_terms and not backend_terms:
        return "frontend_only"
    if backend_terms and not frontend_terms:
        return "backend_only"
    return "mixed"


def _has_reason_prefix(reasons: list[str], prefixes: tuple[str, ...]) -> bool:
    return any(reason.startswith(prefixes) for reason in reasons)


def _apply_scope_penalties(
    scored: list[tuple[Any, float, list[str]]],
    task: str,
    changed_paths: set[str],
    *,
    generic_ratio: float,
    no_live_changes: bool = False,
) -> list[tuple[Any, float, list[str]]]:
    scope = _infer_task_scope(task, changed_paths)
    if scope == "mixed":
        return scored

    adjusted: list[tuple[Any, float, list[str]]] = []
    for fi, score, reasons in scored:
        if fi.path in changed_paths:
            adjusted.append((fi, score, reasons))
            continue
        path_scope = _path_scope(fi.path)
        if scope == "frontend_only" and path_scope == "backend":
            has_support = _has_reason_prefix(reasons, _SCOPE_SUPPORT_PREFIXES)
            weak_only = not has_support and not any(reason.startswith("content keyword match") for reason in reasons)
            if weak_only and (no_live_changes or generic_ratio >= 0.35):
                adjusted.append((fi, 0.0, [*reasons, "frontend-scope backend suppression"]))
                continue
            penalty = max(25.0, score * (0.45 if has_support else 0.6))
            adjusted.append((fi, max(0.0, score - penalty), [*reasons, "frontend-scope backend dampening"]))
            continue
        if scope == "backend_only" and path_scope == "frontend":
            has_support = _has_reason_prefix(reasons, _SCOPE_SUPPORT_PREFIXES)
            weak_only = not has_support and not any(reason.startswith("content keyword match") for reason in reasons)
            if weak_only and (no_live_changes or generic_ratio >= 0.35):
                adjusted.append((fi, 0.0, [*reasons, "backend-scope frontend suppression"]))
                continue
            penalty = max(25.0, score * (0.45 if has_support else 0.6))
            adjusted.append((fi, max(0.0, score - penalty), [*reasons, "backend-scope frontend dampening"]))
            continue
        adjusted.append((fi, score, reasons))
    return adjusted


def _apply_history_penalties(
    root: Path,
    scored: list[tuple[Any, float, list[str]]],
    changed_paths: set[str],
    *,
    generic_ratio: float = 0.0,
    window: int = 20,
) -> list[tuple[Any, float, list[str]]]:
    """Downrank paths that recent packs proved noisy for later edits."""
    counts = _history_noise_counts(root, window=window)
    if not counts:
        return scored

    adjusted: list[tuple[Any, float, list[str]]] = []
    for fi, score, reasons in scored:
        if fi.path in changed_paths:
            adjusted.append((fi, score, reasons))
            continue
        count = counts.get(fi.path, 0)
        if count <= 0:
            adjusted.append((fi, score, reasons))
            continue
        has_strong = any(reason.startswith(_NO_LIVE_STRONG_SIGNALS) for reason in reasons)
        has_only_weak = not has_strong and _has_reason_prefix(reasons, _SCOPE_WEAK_ONLY_PREFIXES)
        if generic_ratio >= 0.35 and count >= 4 and not has_strong:
            adjusted.append((fi, 0.0, [*reasons, "repeat noise path suppressed"]))
            continue
        if count >= 6 and has_only_weak:
            adjusted.append((fi, 0.0, [*reasons, "repeat weak-noise path suppressed"]))
            continue
        penalty = min(45.0, count * 6.0 + max(0, count - 2) * 4.0)
        adjusted.append((fi, max(0.0, score - penalty), [*reasons, f"history noise penalty -{penalty:.0f}"]))
    return adjusted


def _apply_ranking_feedback_boosts(
    root: Path,
    scored: list[tuple[Any, float, list[str]]],
    task: str,
    changed_paths: set[str],
    cfg: Any | None = None,
) -> list[tuple[Any, float, list[str]]]:
    cfg = cfg or load_config(root)
    memory_setting = os.environ.get("AGENTPACK_MEMORY_FEEDBACK") or getattr(cfg.context, "memory_feedback", "auto")
    if str(memory_setting).strip().lower() == "off":
        return scored
    boosts = ranking_feedback_boosts(root, task)
    episodic_reasons: dict[str, str] = {}
    try:
        from agentpack.learning.episodes import episodic_memory_matches

        episodic_matches = episodic_memory_matches(
            root,
            task,
            output_path=cfg.learning.episodic_cases_output,
            procedures_path=getattr(cfg.learning, "procedures_output", ".agentpack/procedures.jsonl"),
            max_boost=float(getattr(cfg.context, "memory_boost_weight", 12.0)),
        )
    except Exception:
        episodic_matches = []
    episodic_boosts: dict[str, float] = {}
    for match in episodic_matches:
        path = str(match.get("path") or "")
        boost = float(match.get("boost") or 0)
        if not path or boost <= 0:
            continue
        episodic_boosts[path] = max(episodic_boosts.get(path, 0.0), boost)
        procedure_titles = [
            str(item.get("title") or item.get("procedure_id") or "")
            for item in match.get("procedures") or []
            if isinstance(item, dict)
        ]
        reason = str(match.get("visible_reason") or "episodic memory similar task")
        if procedure_titles:
            reason += f"; procedure={procedure_titles[0]}"
        episodic_reasons[path] = f"{reason}; confidence={float(match.get('confidence') or 0):.2f}"
    for path, boost in episodic_boosts.items():
        boosts[path] = max(boosts.get(path, 0.0), boost)
    if not boosts:
        return scored
    adjusted: list[tuple[Any, float, list[str]]] = []
    for fi, score, reasons in scored:
        boost = boosts.get(fi.path, 0.0)
        if boost <= 0 or fi.path in changed_paths:
            adjusted.append((fi, score, reasons))
            continue
        label = episodic_reasons.get(fi.path) or (
            "episodic memory similar task" if fi.path in episodic_boosts else "learning feedback miss"
        )
        adjusted.append((fi, score + boost, [*reasons, f"{label} boost +{boost:.0f}"]))
    return adjusted


def _compact_broad_context(context: BroadContext) -> BroadContext:
    clone = context.model_copy(deep=True)
    clone.inventory = clone.inventory[:40]
    clone.module_summaries = clone.module_summaries[:8]
    clone.entrypoints = clone.entrypoints[:10]
    clone.configs = clone.configs[:10]
    clone.docs = clone.docs[:10]
    clone.tests = clone.tests[:10]
    clone.semantic_clusters = clone.semantic_clusters[:8]
    clone.omitted_by_budget = clone.omitted_by_budget[:20]
    return clone


def _history_noise_counts(root: Path, *, window: int = 20) -> dict[str, int]:
    metrics_path = root / ".agentpack" / "metrics.jsonl"
    if not metrics_path.exists():
        return {}
    counts: dict[str, int] = {}
    try:
        lines = metrics_path.read_text(encoding="utf-8").splitlines()[-window:]
    except OSError:
        return {}
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for path in rec.get("selection_noise_paths", []) or []:
            if isinstance(path, str):
                counts[path] = counts.get(path, 0) + 1
    return counts


def _filter_co_changed_paths(
    root: Path,
    co_changed_paths: dict[str, int],
    *,
    min_count: int = 2,
    max_noise_count: int = 4,
) -> dict[str, int]:
    """Keep only repeated, not-recently-noisy co-change hints.

    Single co-change commits are often incidental. Repeated noisy files should
    not get revived by the recall boost after metrics already proved them bad.
    """
    if not co_changed_paths:
        return {}
    noise_counts = _history_noise_counts(root)
    return {
        path: count
        for path, count in co_changed_paths.items()
        if count >= min_count and noise_counts.get(path, 0) <= max_noise_count
    }


_NO_LIVE_STRONG_SIGNALS = (
    "symbol keyword match",
    "content keyword match",
    "matched entrypoint",
    "matched external system",
    "matched domain",
    "matched env read",
    "matched side effect",
    "direct dependency",
    "reverse dependency",
    "has related tests",
    "test for",
    "config file",
    "release/version metadata",
    "knowledge/architecture doc",
    "historically co-changed",
)
_NO_LIVE_META_ONLY_SIGNALS = (
    "matched role keyword",
    "matched ranking keyword",
    "matched define",
)

def _apply_no_live_precision_guard(
    scored: list[tuple[Any, float, list[str]]],
    generic_ratio: float,
    *,
    mode: str = "",
) -> list[tuple[Any, float, list[str]]]:
    """Tighten ranking when task keywords are the only signal.

    No-live-change packs are useful as orientation, but they are also where
    filename and summary noise dominate. Keep corroborated files, damp weak
    filename-only hits, and avoid letting broad task words fan out across repo.
    """
    adjusted: list[tuple[Any, float, list[str]]] = []
    broad_task = generic_ratio >= 0.3
    for fi, score, reasons in scored:
        has_filename = any(reason.startswith("filename keyword match") for reason in reasons)
        has_strong = any(reason.startswith(_NO_LIVE_STRONG_SIGNALS) for reason in reasons)
        has_meta_only = any(reason.startswith(_NO_LIVE_META_ONLY_SIGNALS) for reason in reasons)
        if broad_task and has_filename and not has_strong:
            damped = min(score, max(0.0, score * 0.2))
            adjusted.append((fi, damped, [*reasons, "broad-task weak-signal dampening"]))
            continue
        if has_filename and not has_strong:
            damped = min(score, max(0.0, score * 0.35))
            adjusted.append((fi, damped, [*reasons, "no-live filename-only dampening"]))
            continue
        if broad_task and has_meta_only and not has_strong:
            damped = max(0.0, score * 0.45 - 15)
            adjusted.append((fi, damped, [*reasons, "broad-task meta-summary dampening"]))
            continue
        adjusted.append((fi, score, reasons))
    return adjusted


def _compute_delta_summary(
    previous_metadata: dict[str, Any] | None,
    selected: list[SelectedFile],
    changed_paths: set[str],
) -> str:
    if not previous_metadata:
        return ""
    previous = previous_metadata.get("selected_files_meta") or []
    if not isinstance(previous, list):
        return ""
    prev_modes = {
        item.get("path"): item.get("mode")
        for item in previous
        if isinstance(item, dict) and item.get("path")
    }
    current_modes = {sf.path: sf.include_mode for sf in selected}
    added = sorted(set(current_modes) - set(prev_modes))
    removed = sorted(set(prev_modes) - set(current_modes))
    mode_changed = sorted(
        path for path, mode in current_modes.items()
        if path in prev_modes and prev_modes[path] != mode
    )
    if not (added or removed or mode_changed or changed_paths):
        return "No selected-file delta since last pack."
    lines = [
        f"Selected delta: +{len(added)} new, -{len(removed)} removed, {len(mode_changed)} mode changed; "
        f"{len(changed_paths)} live changed files."
    ]
    if added:
        lines.append("New: " + ", ".join(added[:6]))
    if removed:
        lines.append("Removed: " + ", ".join(removed[:6]))
    if mode_changed:
        lines.append("Mode changed: " + ", ".join(mode_changed[:6]))
    return "\n".join(lines)


def _summary_score_floor(cfg: Any, generic_ratio: float) -> float:
    floor = cfg.context.min_summary_score
    if generic_ratio >= 0.5:
        return floor + 15
    if generic_ratio >= 0.35:
        return floor + 8
    return floor


def _summary_cap_for_mode(cfg: Any, mode: str, generic_ratio: float = 0.0) -> int:
    if mode == "lite":
        lite_cap = cfg.context.max_summary_files_lite
        cap = min(lite_cap, cfg.context_lite.max_selected_files) if lite_cap > 0 else cfg.context_lite.max_selected_files
    elif mode == "balanced":
        cap = cfg.context.max_summary_files_balanced
    elif mode == "deep":
        cap = cfg.context.max_summary_files_deep
    else:
        cap = 0
    if cap > 0 and generic_ratio >= 0.5:
        return max(8, cap // 2)
    if cap > 0 and generic_ratio >= 0.35:
        return max(12, int(cap * 0.75))
    return cap


def _guarded_summary_score_floor(
    root: Path,
    cfg: Any,
    mode: str,
    generic_ratio: float,
    *,
    no_live_changes: bool = False,
) -> float:
    floor = _summary_score_floor(cfg, generic_ratio)
    avg_summary_precision, rows = _recent_summary_token_precision(root)
    if rows < 3:
        if no_live_changes and mode == "balanced":
            return floor + 60
        return floor + (15 if no_live_changes else 0)
    if avg_summary_precision <= 0.05:
        return floor + (140 if no_live_changes else 80)
    if avg_summary_precision <= 0.15:
        return floor + (80 if no_live_changes else 40)
    if no_live_changes:
        return floor + 35
    return floor


def _guarded_summary_cap(
    root: Path,
    cfg: Any,
    mode: str,
    generic_ratio: float = 0.0,
    *,
    no_live_changes: bool = False,
    effective_budget: int = 0,
    task_kind: str = "",
    has_literal_phrases: bool = False,
) -> int:
    cap = _summary_cap_for_mode(cfg, mode, generic_ratio)
    if no_live_changes and effective_budget and effective_budget <= 2500 and cap > 0:
        cap = min(cap, 2 if mode == "lite" else 3 if mode == "balanced" else 6)
    avg_summary_precision, rows = _recent_summary_token_precision(root)
    if rows < 3:
        if no_live_changes and cap > 0:
            if mode == "balanced":
                if task_kind == "chore" and has_literal_phrases:
                    return min(cap, 2)
                if task_kind == "test":
                    return min(cap, 4)
                return min(cap, 3)
            if effective_budget and effective_budget <= 2500:
                return min(cap, 2 if mode == "lite" else 6)
            if effective_budget and effective_budget <= 6000:
                return min(cap, 12 if mode == "lite" else 16)
            return min(cap, 16)
        return cap
    if avg_summary_precision <= 0.05:
        if no_live_changes:
            return -1
        strict_cap = 2 if mode == "lite" else 5 if mode == "balanced" else 10
    elif avg_summary_precision <= 0.15:
        strict_cap = 2 if mode == "lite" else 3 if no_live_changes else 12 if mode == "balanced" else 20
    else:
        if no_live_changes and cap > 0:
            return min(cap, 8)
        return cap
    if cap <= 0:
        return strict_cap
    return min(cap, strict_cap)


def _strict_summary_selection(
    root: Path,
    *,
    no_live_changes: bool = False,
    mode: str = "",
) -> bool:
    avg_summary_precision, rows = _recent_summary_token_precision(root)
    if rows < 3:
        return no_live_changes
    if avg_summary_precision <= 0.05:
        return True
    return no_live_changes and avg_summary_precision <= 0.15


def _recent_token_precision(root: Path, window: int = 10) -> tuple[float, int]:
    metrics_path = root / ".agentpack" / "metrics.jsonl"
    if not metrics_path.exists():
        return 1.0, 0
    values: list[float] = []
    try:
        lines = metrics_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 1.0, 0
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = rec.get("selection_token_precision")
        if isinstance(value, int | float):
            values.append(float(value))
            if len(values) >= window:
                break
    if not values:
        return 1.0, 0
    return sum(values) / len(values), len(values)


def _guarded_weak_signal_cap(
    root: Path,
    mode: str,
    generic_ratio: float,
    *,
    no_live_changes: bool = False,
    effective_budget: int = 0,
) -> int:
    if not no_live_changes:
        return 0
    if generic_ratio >= 0.5:
        base = {"lite": 0, "balanced": 1, "deep": 2}.get(mode, 1)
    elif generic_ratio >= 0.35:
        base = {"lite": 0, "balanced": 2, "deep": 3}.get(mode, 2)
    else:
        base = {"lite": 1, "balanced": 4, "deep": 6}.get(mode, 3)
    avg_precision, rows = _recent_token_precision(root)
    if rows >= 3:
        if avg_precision <= 0.1:
            base = min(base, 0 if mode == "lite" else 1)
        elif avg_precision <= 0.2:
            base = min(base, 1 if mode != "deep" else 2)
    if effective_budget and effective_budget <= 2500:
        base = min(base, 1)
    return max(0, base)


def _resolve_effective_mode(
    root: Path,
    requested_mode: str,
    generic_ratio: float,
    *,
    no_live_changes: bool = False,
) -> tuple[str, str | None]:
    return requested_mode, None


def _recent_summary_token_precision(root: Path, window: int = 10) -> tuple[float, int]:
    metrics_path = root / ".agentpack" / "metrics.jsonl"
    if not metrics_path.exists():
        return 1.0, 0
    values: list[float] = []
    try:
        lines = metrics_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 1.0, 0
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = rec.get("selection_token_precision_summary")
        if isinstance(value, int | float):
            values.append(float(value))
            if len(values) >= window:
                break
    if not values:
        return 1.0, 0
    return sum(values) / len(values), len(values)


def _change_source(root: Path, since: str | None, snapshot_changed: set[str], git_changed: set[str]) -> str:
    if not git.is_git_repo(root):
        return "snapshot diff"
    if since:
        return f"git diff since {since} + snapshot diff"
    if git_changed and snapshot_changed:
        return "git working tree + snapshot diff"
    if git_changed:
        return "git working tree"
    if snapshot_changed:
        return "snapshot diff"
    return "no live changes; ranking used task keywords and history"


def _is_pr_review_task(task: str) -> bool:
    lower = task.lower()
    return any(term in lower for term in ("pr ", "pull request", "review", "diff", "review comment"))


def _github_pr_paths(root: Path, task: str) -> set[str]:
    if shutil.which("gh") is None:
        return set()
    pr_number = _pr_number(task)
    cmd = ["gh", "pr", "view"]
    if pr_number:
        cmd.append(pr_number)
    cmd += ["--json", "files", "--jq", ".files[].path"]
    try:
        result = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _pr_number(task: str) -> str | None:
    match = re.search(r"(?:pr|pull request)\s*#?\s*(\d+)", task, re.IGNORECASE)
    return match.group(1) if match else None


def _apply_github_pr_changed_paths(
    changes: ChangeSet,
    pr_paths: set[str],
    packable: list[FileInfo],
) -> ChangeSet:
    packable_paths = {fi.path for fi in packable}
    pr_packable = pr_paths & packable_paths
    if not pr_packable:
        return changes
    source = "GitHub PR files" if changes.source.startswith("no live changes") else f"{changes.source} + GitHub PR files"
    return replace(
        changes,
        all_changed=changes.all_changed | pr_packable,
        source=source,
    )


def _boost_github_pr_paths(
    scored: list[tuple[Any, float, list[str]]],
    pr_paths: set[str],
) -> list[tuple[Any, float, list[str]]]:
    adjusted: list[tuple[Any, float, list[str]]] = []
    for fi, score, reasons in scored:
        if fi.path in pr_paths:
            boosted_reasons = reasons if "GitHub PR file" in reasons else ["GitHub PR file", *reasons]
            adjusted.append((fi, max(score, 1000.0), boosted_reasons))
            continue
        adjusted.append((fi, score, reasons))
    return adjusted


def _task_md_body(root: Path, thread_id: str | None = None) -> str | None:
    scoped = thread_paths(root, thread_id)
    if scoped:
        try:
            text = scoped.task.read_text(encoding="utf-8")
        except OSError:
            return None
        return normalize_task_text(text) or None
    return read_task_md(root)


def _build_freshness_metadata(
    root: Path,
    *,
    request: PackRequest,
    plan: PackPlan,
    snapshot_root_hash: str,
) -> dict[str, Any]:
    dirty = git.dirty_files(root) if git.is_git_repo(root) else set()
    metadata: dict[str, Any] = {
        "agentpack_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(Path.cwd()),
        "git_root": str(root),
        "worktree_path": str(root),
        "source_command": refresh_commands(request.agent).primary,
        "task_source": request.task_source,
        "changed_files_source": plan.changed_files_source,
        "snapshot_root_hash": snapshot_root_hash,
        "generic_task_ratio": round(plan.generic_task_ratio, 3),
        "task_class": plan.task_class,
        "task_class_confidence": plan.task_class_confidence,
        "task_class_signals": plan.task_class_signals,
        "context_intent": plan.context_intent,
        "broad_context": plan.broad_context is not None,
        "dirty_files_count": len(dirty),
        "requested_mode": plan.requested_mode,
        "effective_mode": plan.mode,
        "scan_mode": plan.scan_result.scan_mode,
        "scan_rehashed_count": plan.scan_result.rehashed_count,
        "scan_reused_count": plan.scan_result.reused_count,
    }
    if plan.scan_result.full_scan_reason:
        metadata["full_scan_reason"] = plan.scan_result.full_scan_reason
    if plan.mode_warning:
        metadata["mode_warning"] = plan.mode_warning
    task_md = _task_md_body(root, request.thread_id)
    if request.thread_id:
        metadata["packed_task_hash"] = task_hash(request.task)
        if task_md:
            metadata["task_md"] = task_md
            metadata["task_md_hash"] = task_hash(task_md)
            metadata["task_matches_task_md"] = task_md == request.task
    else:
        metadata.update(task_metadata(root, request.task))
    if plan.workspace:
        metadata["workspace"] = plan.workspace
    if plan.workspace_roots:
        metadata["workspace_roots"] = plan.workspace_roots
    if plan.workspace_dependency_edges:
        metadata["workspace_dependency_edges"] = {
            key: sorted(value) for key, value in plan.workspace_dependency_edges.items() if value
        }
    if git.is_git_repo(root):
        metadata["git_sha"] = git.current_sha(root)
        metadata["git_branch"] = git.current_branch(root)
    if dirty:
        metadata["dirty_files_sample"] = sorted(dirty)[:8]
    return metadata


def _freshness_warnings(root: Path, request: PackRequest, freshness: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    task_md = freshness.get("task_md")
    if task_md and task_md != request.task:
        warnings.append(
            ".agentpack/task.md differs from the packed task; AgentPack-controlled context reads should auto-refresh, or run `agentpack pack --task auto`."
        )
    if freshness.get("changed_files_source") == "no live changes; ranking used task keywords and history":
        warnings.append("No live changed files were detected; treat selected files as keyword-based hints.")
    if freshness.get("generic_task_ratio", 0) >= 0.5:
        warnings.append("Task terms are broad/generic; pack tightened weak-summary selection.")
    if freshness.get("mode_warning"):
        warnings.append(str(freshness["mode_warning"]))
    saved_sha = freshness.get("git_sha")
    current_sha = git.current_sha(root) if git.is_git_repo(root) else None
    if saved_sha and current_sha and saved_sha != current_sha:
        warnings.append("Git HEAD changed since this pack was generated.")
    return warnings


def _load_last_record(metrics_path: Path) -> dict[str, Any] | None:
    """Return the most recent metrics record that has selected_paths."""
    if not metrics_path.exists():
        return None
    try:
        lines = metrics_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("selected_paths"):
                return rec
    except Exception:
        pass
    return None


def _compute_selection_accuracy(
    root: Path,
    metrics_path: Path,
    current_selected: list[str],
    current_changed: set[str],
) -> dict[str, Any]:
    """Compare previous pack's selected_paths vs files actually changed since then.

    recall    = |predicted ∩ actual_changed| / |actual_changed|
    precision = |predicted ∩ actual_changed| / |predicted|
    """
    prev = _load_last_record(metrics_path)
    if prev is None:
        return {}

    prev_selected: set[str] = set(prev["selected_paths"])
    # actual_changed = files changed since the previous pack (current git diff)
    actual_changed: set[str] = current_changed
    if not actual_changed or not prev_selected:
        return {}

    hits = prev_selected & actual_changed
    support = {
        path for path in prev_selected - hits
        if _support_matches_changed(path, actual_changed)
    }
    recall = len(hits) / len(actual_changed)
    precision = len(hits) / len(prev_selected)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    context_precision = (len(hits) + len(support)) / len(prev_selected)
    result = {
        "selection_recall": round(recall, 3),
        "selection_precision": round(precision, 3),
        "selection_f1": round(f1, 3),
        "selection_hit_paths": sorted(hits),
        "selection_support_paths": sorted(support),
        "selection_context_precision": round(context_precision, 3),
        "selection_noise_paths": sorted(prev_selected - hits - support),
    }
    token_map = prev.get("selected_tokens") or {}
    if isinstance(token_map, dict):
        total_tokens = sum(v for v in token_map.values() if isinstance(v, int | float))
        hit_tokens = sum(
            token_map.get(path, 0)
            for path in hits
            if isinstance(token_map.get(path, 0), int | float)
        )
        if total_tokens > 0:
            token_precision = hit_tokens / total_tokens
            support_tokens = sum(
                token_map.get(path, 0)
                for path in support
                if isinstance(token_map.get(path, 0), int | float)
            )
            result["selection_token_precision"] = round(token_precision, 3)
            result["selection_token_context_precision"] = round((hit_tokens + support_tokens) / total_tokens, 3)
            result["selection_noise_pct"] = round((1 - token_precision) * 100, 1)
        mode_map = prev.get("selected_modes") or {}
        if isinstance(mode_map, dict):
            for mode in ("full", "diff", "symbols", "skeleton", "summary"):
                mode_paths = {path for path, value in mode_map.items() if value == mode}
                mode_total = sum(
                    token_map.get(path, 0)
                    for path in mode_paths
                    if isinstance(token_map.get(path, 0), int | float)
                )
                if mode_total <= 0:
                    continue
                mode_hit_tokens = sum(
                    token_map.get(path, 0)
                    for path in mode_paths & hits
                    if isinstance(token_map.get(path, 0), int | float)
                )
                result[f"selection_token_precision_{mode}"] = round(mode_hit_tokens / mode_total, 3)
    return result


def _support_matches_changed(path: str, changed_paths: set[str]) -> bool:
    """Heuristic for read-only support context that was plausibly useful.

    Edit precision only rewards files later changed. This helper gives partial
    credit to obvious support files such as paired tests or files sharing a
    meaningful stem/domain with a changed file.
    """
    from agentpack.analysis.ranking import _is_test_file, _test_matches_source

    p = Path(path)
    path_stem = p.stem.removeprefix("test_").removesuffix("_test")
    path_parts = {part.lower() for part in p.parts if len(part) >= 3}
    for changed in changed_paths:
        c = Path(changed)
        changed_stem = c.stem.removeprefix("test_").removesuffix("_test")
        if _is_test_file(path) and _test_matches_source(path, changed):
            return True
        if _is_test_file(changed) and _test_matches_source(changed, path):
            return True
        if path_stem and path_stem == changed_stem:
            return True
        changed_parts = {part.lower() for part in c.parts if len(part) >= 3}
        shared = (path_parts & changed_parts) - {"src", "app", "test", "tests", "lib", "core"}
        if len(shared) >= 2:
            return True
    return False


def _record_metrics(
    root: Path,
    *,
    task: str,
    mode: str,
    phase_times: dict[str, float],
    packed_tokens: int,
    raw_tokens: int,
    saving_pct: float,
    selected_count: int,
    changed_count: int,
    selected_paths: list[str],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    current_changed: set[str],
    task_class: str = "general",
    workspace: str | None = None,
    workspace_roots: list[str] | None = None,
    selected_hints: list[dict] | None = None,
    excluded_count: int = 0,
    excluded_paths: list[str] | None = None,
    scan_mode: str = "full",
    scan_rehashed_count: int = 0,
    scan_reused_count: int = 0,
    full_scan_reason: str | None = None,
) -> None:
    metrics_path = root / ".agentpack" / "metrics.jsonl"
    accuracy = _compute_selection_accuracy(root, metrics_path, selected_paths, current_changed)
    mode_counts: dict[str, int] = {}
    mode_tokens: dict[str, int] = {}
    for path, include_mode in selected_modes.items():
        mode_counts[include_mode] = mode_counts.get(include_mode, 0) + 1
        mode_tokens[include_mode] = mode_tokens.get(include_mode, 0) + selected_tokens.get(path, 0)
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "task_class": task_class,
        "workspace": workspace,
        "workspace_roots": workspace_roots or [],
        "mode": mode,
        "packed_tokens": packed_tokens,
        "raw_tokens": raw_tokens,
        "saving_pct": round(saving_pct, 1),
        "compression_ratio": round(packed_tokens / raw_tokens, 4) if raw_tokens > 0 else 0,
        "selected_files": selected_count,
        "changed_files": changed_count,
        "current_changed_paths": sorted(current_changed),
        "excluded_files": excluded_count,
        "excluded_paths": excluded_paths or [],
        "scan_mode": scan_mode,
        "scan_rehashed_count": scan_rehashed_count,
        "scan_reused_count": scan_reused_count,
        "full_scan_reason": full_scan_reason,
        "selected_paths": selected_paths,
        "selected_tokens": selected_tokens,
        "selected_modes": selected_modes,
        "mode_counts": mode_counts,
        "mode_tokens": mode_tokens,
        "selected_hints": selected_hints or [],
        "phases": {k: round(v, 3) for k, v in phase_times.items()},
        "total_s": round(sum(phase_times.values()), 3),
    }
    record.update(accuracy)
    try:
        with metrics_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass
