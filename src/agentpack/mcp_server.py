"""AgentPack MCP server — exposes context packing as MCP tools.

Start with:
    agentpack mcp

Or register in Claude Code settings:
    {
      "mcpServers": {
        "agentpack": {
          "command": "agentpack",
          "args": ["mcp"]
        }
      }
    }

Tools exposed:
    readiness          — prove live MCP exposure and report server/CLI/tool status
    start_task          — write task.md and return a fresh context pack
    pack_context        — generate/refresh a context pack for a task
    route_task          — read-only route: files + rules + skills + commands
    get_skills          — read-only skill/rule inventory
    get_skill           — read one skill by name or path
    explain_route       — read-only route with skill score reasons
    get_context         — read latest context pack; auto-refreshes when task.md changed
    refresh             — refresh using the current task.md
    explain_file        — show score breakdown + symbols for a specific file
    get_related_files   — return import-graph neighbours of a file
    query_graph         — bounded semantic graph search
    get_graph_node      — one graph node and its evidence
    get_graph_neighbors — bounded semantic graph neighbours
    shortest_path       — shortest semantic relationship path
    explain_graph_edge  — relationship evidence receipt
    get_delta_context   — return selected-file delta since the previous pack
    validate_toon       — validate TOON syntax from content or a repo-relative path
    get_stats           — token/saving stats for the latest pack
    create_handoff      — package a structured report and complete Git-visible patch
    list_handoffs       — list pending or historical project handoffs
    get_handoff         — inspect one handoff by memorable name
    accept_handoff      — atomically claim, apply, and resume a handoff
    release_handoff     — release the current session's claim
    learning_recommendations — return the next competency-backed learning queue
    learning_start      — start a coached learning session
    learning_complete   — record structured proof for a learning session
"""
from __future__ import annotations

import json
import hashlib
import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, cast

from agentpack import __version__
from agentpack.core import git
from agentpack.core.command_surface import available_cli_commands, fallback_agent_guidance, refresh_commands
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.structured_format import StructuredFormat, to_llm
from agentpack.core.task_freshness import read_task_md, task_freshness, write_task_md
from agentpack.core.thread_context import resolve_session_thread_option, read_task_status, task_is_terminal, thread_paths
from agentpack.core.token_estimator import estimate_tokens, estimator_mode
from agentpack.control_plane import build_control_plane_snapshot, plan_next_actions
from agentpack.control_plane.renderer import token_hint


def _repo_root() -> Path:
    """Resolve configured workspace root, then walk up from cwd."""
    configured = os.environ.get("AGENTPACK_ROOT", "").strip()
    cwd = Path(configured).expanduser() if configured else Path.cwd()
    if configured and not cwd.exists():
        raise ValueError(f"AGENTPACK_ROOT does not exist: {configured}")
    for parent in [cwd, *cwd.parents]:
        if (parent / ".agentpack").exists():
            return parent.resolve()
    return cwd.resolve()


MCP_TOOL_NAMES = (
    "readiness",
    "start_task",
    "pack_context",
    "route_task",
    "get_skills",
    "get_skill",
    "explain_route",
    "get_context",
    "refresh",
    "explain_file",
    "get_related_files",
    "query_graph",
    "get_graph_node",
    "get_graph_neighbors",
    "shortest_path",
    "explain_graph_edge",
    "get_delta_context",
    "get_task_map",
    "retrieve_context",
    "compress_output",
    "validate_toon",
    "get_stats",
    "create_handoff",
    "list_handoffs",
    "get_handoff",
    "accept_handoff",
    "release_handoff",
    "learning_recommendations",
    "learning_start",
    "learning_complete",
    "get_pr_context",
)


@dataclass(frozen=True)
class _PlanCacheKey:
    root: str
    task: str
    mode: str
    budget: int
    thread_id: str
    task_source: str
    repo_fingerprint: tuple[Any, ...]


class _McpSession:
    """Process-local caches shared by MCP calls for one or more workspaces."""

    def __init__(self) -> None:
        from agentpack.router.service import RouteService

        self.route_service = RouteService()
        self._plans: OrderedDict[_PlanCacheKey, Any] = OrderedDict()
        self._lock = RLock()

    def plan(
        self,
        root: Path,
        *,
        task: str,
        mode: str = "balanced",
        budget: int = 0,
        thread_id: str = "",
        task_source: str = "mcp",
        timeout_s: float | None = None,
    ) -> Any:
        from agentpack.adapters.detect import detect_agent
        from agentpack.application.pack_service import PackPlanner, PackRequest

        key = _PlanCacheKey(
            root=str(root.resolve()),
            task=" ".join(task.split()),
            mode=mode,
            budget=budget,
            thread_id=thread_id,
            task_source=task_source,
            repo_fingerprint=_repo_fingerprint(root),
        )
        with self._lock:
            cached = self._plans.get(key)
            if cached is not None:
                self._plans.move_to_end(key)
                return cached

        plan = PackPlanner().plan(PackRequest(
            root=root,
            agent=detect_agent(root),
            task=task,
            mode=mode,
            budget=budget,
            since=None,
            refresh=False,
            task_source=task_source,
            thread_id=thread_id or None,
            deadline=(time.monotonic() + timeout_s) if timeout_s is not None else None,
        ))
        with self._lock:
            # Planning can create or refresh ignored AgentPack artifacts. Recompute
            # fingerprint so next identical request hits cache immediately.
            key = _PlanCacheKey(
                root=key.root,
                task=key.task,
                mode=key.mode,
                budget=key.budget,
                thread_id=key.thread_id,
                task_source=key.task_source,
                repo_fingerprint=_repo_fingerprint(root),
            )
            self._plans[key] = plan
            self._plans.move_to_end(key)
            while len(self._plans) > 8:
                self._plans.popitem(last=False)
        return plan


_MCP_SESSIONS: dict[str, _McpSession] = {}
_MCP_SESSIONS_LOCK = RLock()
_GRAPH_INDEX_CACHE: OrderedDict[tuple[str, str], Any] = OrderedDict()
_GRAPH_INDEX_LOCK = RLock()
MCP_DEFAULT_OUTPUT_TOKENS = 20_000
MCP_MAX_OUTPUT_TOKENS = 100_000
MCP_MAX_INPUT_TOKENS = 100_000


def _session(root: Path) -> _McpSession:
    key = str(root.resolve())
    with _MCP_SESSIONS_LOCK:
        session = _MCP_SESSIONS.get(key)
        if session is None:
            session = _McpSession()
            _MCP_SESSIONS[key] = session
        while len(_MCP_SESSIONS) > 4:
            _MCP_SESSIONS.pop(next(iter(_MCP_SESSIONS)))
        return session


def _repo_fingerprint(root: Path) -> tuple[Any, ...]:
    """Cheap cache invalidation for dirty files and workspace configuration."""
    sha = git.current_sha(root) if git.is_git_repo(root) else None
    paths = set(git.dirty_files(root)) if git.is_git_repo(root) else set()
    if git.is_git_repo(root):
        paths.update(git.untracked_files(root))
    paths.update({".agentignore", ".agentpack/config.toml", ".agentpack/skills_index.json"})
    stats: list[tuple[str, int, int]] = []
    for relative in sorted(paths):
        path = root / relative
        try:
            stat = path.stat()
        except OSError:
            stats.append((relative, 0, 0))
        else:
            stats.append((relative, stat.st_mtime_ns, stat.st_size))
    return (sha, tuple(stats))


def _cache_stats(root: Path) -> dict[str, int]:
    session = _session(root)
    with session._lock:
        return {"plans": len(session._plans), "workspaces": len(_MCP_SESSIONS)}


def _timeout_seconds(value: float) -> float:
    if value <= 0 or value > 300:
        raise ValueError("timeout_s must be greater than 0 and no more than 300")
    return value


def _snapshot_cache_key(snapshot: Any) -> str:
    payload = {
        "schema_version": getattr(snapshot, "schema_version", None),
        "ref": getattr(snapshot, "ref", None),
        "commit_sha": getattr(snapshot, "commit_sha", None),
        "file_hashes": getattr(snapshot, "file_hashes", {}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _readiness_impl(root: Path, output_format: StructuredFormat = "auto") -> str:
    metadata = load_pack_metadata(root) or {}
    freshness = metadata.get("freshness") or {}
    thread_id = resolve_session_thread_option("")
    snapshot = build_control_plane_snapshot(root, thread_id=thread_id, check_files=False)
    recommendations = plan_next_actions(snapshot)
    recommended_next_tool = _recommended_mcp_tool(snapshot, [item.kind for item in recommendations])
    payload = {
        "ok": True,
        "proof": "This response proves the current host can call AgentPack MCP tools.",
        "agentpack_version": __version__,
        "repo_root": str(root),
        "git_branch": git.current_branch(root) if git.is_git_repo(root) else None,
        "git_sha": git.current_sha(root) if git.is_git_repo(root) else None,
        "mcp_server": "agentpack",
        "mcp_tools": list(MCP_TOOL_NAMES),
        "tool_manifest": {
            "count": len(MCP_TOOL_NAMES),
            "sha256": hashlib.sha256("\n".join(MCP_TOOL_NAMES).encode()).hexdigest(),
            "module": str(Path(__file__).resolve()),
        },
        "workspace_source": "AGENTPACK_ROOT" if os.environ.get("AGENTPACK_ROOT") else "process cwd",
        "cli_commands": list(available_cli_commands()),
        "refresh_command": refresh_commands("auto").primary,
        "recommended_next_tool": recommended_next_tool,
        "reason": recommendations[0].reason if recommendations else "context is ready for the current task",
        "avoid": _mcp_avoid_list(snapshot, [item.kind for item in recommendations]),
        "token_hint": token_hint(snapshot),
        "token_estimator": {
            "mode": estimator_mode(),
            "fallback": estimator_mode() != "tiktoken",
        },
        "control_plane": {
            "thread_id": snapshot.task.thread_id,
            "context_status": snapshot.context.status,
            "context_reason": snapshot.context.reason,
            "token_contract": snapshot.tokens.model_dump(mode="json"),
            "next_actions": [item.model_dump(mode="json") for item in recommendations[:5]],
        },
        "latest_context": {
            "task": metadata.get("task"),
            "generated_at": metadata.get("generated_at"),
            "agentpack_version": freshness.get("agentpack_version"),
            "source_command": freshness.get("source_command"),
            "worktree_path": freshness.get("worktree_path"),
        },
    }
    requested = "toon" if output_format == "auto" else output_format
    return to_llm(root, payload, requested=requested, root_name="agentpack_readiness")


def _recommended_mcp_tool(snapshot, kinds: list[str]) -> str:
    if "init" in kinds:
        return "route_task"
    if "missing_task" in kinds or "done_task" in kinds:
        return "start_task"
    if "stale_context" in kinds or snapshot.context.status != "fresh":
        return "get_context"
    if snapshot.tokens.estimated_tokens and snapshot.tokens.budget and snapshot.tokens.usage_ratio >= 0.85:
        return "get_delta_context"
    return "get_context"


def _mcp_avoid_list(snapshot, kinds: list[str]) -> list[str]:
    avoid: list[str] = []
    if snapshot.context.status == "fresh":
        avoid.append("pack_context unless task text changed")
    if "missing_task" in kinds or "done_task" in kinds:
        avoid.append("get_context until a concrete active task is set")
    if snapshot.tokens.estimated_tokens and snapshot.tokens.budget and snapshot.tokens.usage_ratio >= 0.85:
        avoid.append("full pack_context for small follow-up reads")
    return avoid


def _metadata_provenance(root: Path, metadata: dict | None) -> dict[str, object]:
    freshness = (metadata or {}).get("freshness") or {}
    return {
        "task": (metadata or {}).get("task"),
        "generated_at": (metadata or {}).get("generated_at") or freshness.get("generated_at"),
        "agentpack_version": freshness.get("agentpack_version") or __version__,
        "cwd": freshness.get("cwd"),
        "git_root": freshness.get("git_root") or str(root),
        "worktree_path": freshness.get("worktree_path"),
        "git_branch": freshness.get("git_branch") or (git.current_branch(root) if git.is_git_repo(root) else None),
        "git_sha": freshness.get("git_sha") or (git.current_sha(root) if git.is_git_repo(root) else None),
        "source_command": freshness.get("source_command"),
        "available_cli_commands": list(available_cli_commands()),
        "refresh_command": refresh_commands("auto").primary,
    }


def _stale_context_notice(
    root: Path,
    metadata: dict | None,
    reason: str,
    *,
    refresh_failed: str = "",
) -> str:
    lines = [
        "> **STALE AgentPack context. Do not trust selected files until refreshed.**",
        f"> Reason: {reason}",
    ]
    if refresh_failed:
        lines.append(f"> Auto-refresh failed: {refresh_failed}")
    lines.extend(["", "## Stale Context Provenance", ""])
    for label, value in _metadata_provenance(root, metadata).items():
        if value:
            lines.append(f"- **{label}:** {value}")
    lines.extend([
        "",
        "## Fallback",
        "",
        f"- Run `pack_context()` to retry or `{refresh_commands('auto').primary}` from the CLI.",
        f"- {fallback_agent_guidance()}",
        "",
    ])
    return "\n".join(lines)


def _validate_token_limit(value: int, name: str, *, maximum: int = MCP_MAX_OUTPUT_TOKENS) -> int:
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    if value > maximum:
        raise ValueError(f"{name} must be no more than {maximum}")
    return value


def _hard_prefix(text: str, max_tokens: int) -> str:
    """Return longest prefix whose estimate stays within max_tokens."""
    if not text or max_tokens < 1:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def _fit_prefix_with_suffix(prefix: str, suffix: str, max_tokens: int) -> str:
    if estimate_tokens(suffix) > max_tokens:
        return _hard_prefix(suffix, max_tokens)
    low, high = 0, len(prefix)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(prefix[:mid] + suffix) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    return prefix[:low] + suffix


def _truncate_to_budget(
    text: str,
    max_tokens: int = MCP_DEFAULT_OUTPUT_TOKENS,
    *,
    marker: str | None = None,
) -> str:
    """Hard-cap markdown output using active token estimator."""
    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    if estimate_tokens(text) <= max_tokens:
        return text
    marker = marker or (
        "> [Truncated by AgentPack to fit output budget. "
        "Use get_context() to read full pack or narrow the task.]"
    )
    suffix = "\n\n" + marker
    split_marker = "\n## File Context"
    marker_pos = text.find(split_marker)
    if marker_pos >= 0:
        header = text[:marker_pos]
        blocks = text[marker_pos:].split("\n### ")
        kept = blocks[0]
        kept_files = 0
        for block in blocks[1:]:
            candidate = kept + "\n### " + block
            if estimate_tokens(header + candidate + suffix) > max_tokens:
                break
            kept = candidate
            kept_files += 1
        omitted = max(0, len(blocks) - 1 - kept_files)
        if omitted:
            suffix = "\n\n" + marker.replace("output budget", f"{omitted} files omitted; output budget")
        return _fit_prefix_with_suffix(header + kept, suffix, max_tokens)
    omitted = max(1, len(text) // 2000)
    suffix = "\n\n" + marker.replace("output budget", f"{omitted} sections omitted; output budget")
    return _fit_prefix_with_suffix(text, suffix, max_tokens)


def _get_context_impl(
    root: Path,
    thread_id: str | None = None,
    max_tokens: int = MCP_DEFAULT_OUTPUT_TOKENS,
) -> str:
    """Read the latest context pack, blocking to refresh when task.md changed."""
    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    scoped = thread_paths(root, thread_id)
    if task_is_terminal(root, scoped.thread_id if scoped else None):
        label = f"AgentPack session {scoped.thread_id}" if scoped else "AgentPack task"
        status = read_task_status(root, scoped.thread_id if scoped else None)
        terminal_description = "Completed context" if status == "done" else "Handed-off context"
        return _truncate_to_budget((
            f"> {label} is marked {status}. {terminal_description} will not be reused.\n\n"
            "Start a new task/session with `agentpack start \"describe the task\"` or MCP `start_task(...)`."
        ), max_tokens)
    pack_path = None
    candidates = (
        (scoped.context_claude, scoped.context) if scoped else (root / ".agentpack" / "context.claude.md", root / ".agentpack" / "context.md")
    )
    for candidate in candidates:
        if candidate.exists():
            pack_path = candidate
            break
    if pack_path is None:
        return ""

    snapshot_path = root / ".agentpack" / "snapshots" / "latest.json"

    snapshot = None
    if snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            snapshot = None

    metadata = load_pack_metadata(root, scoped.metadata if scoped else None)
    freshness = task_freshness(root, metadata) if scoped is None else None
    auto_refresh_reason = ""
    scoped_task = _task_md_body(root, scoped.thread_id if scoped else None)
    if scoped and metadata and scoped_task and scoped_task != metadata.get("task"):
        auto_refresh_reason = ".agentpack thread task differs from packed task"
    elif freshness and freshness.is_stale and freshness.current_task:
        auto_refresh_reason = (
            f"{freshness.reason} (packed: {freshness.packed_task}; current: {freshness.current_task})"
        )
    elif metadata is None:
        auto_refresh_reason = "pack metadata missing"
    elif snapshot is None:
        auto_refresh_reason = "repo snapshot missing"
    elif metadata and snapshot and metadata.get("snapshot_root_hash") != snapshot.get("root_hash"):
        auto_refresh_reason = "repo snapshot changed"

    if auto_refresh_reason:
        try:
            if scoped:
                refreshed = _pack_context_impl(root, task="", max_tokens=max_tokens, thread_id=scoped.thread_id)
            else:
                refreshed = _pack_context_impl(root, task="", max_tokens=max_tokens)
            return _truncate_to_budget(
                f"> Context auto-refreshed because {auto_refresh_reason}.\n\n{refreshed}",
                max_tokens,
            )
        except Exception as exc:
            content = pack_path.read_text(encoding="utf-8")
            return _truncate_to_budget(
                _stale_context_notice(root, metadata, auto_refresh_reason, refresh_failed=str(exc)) + content,
                max_tokens,
            )

    content = pack_path.read_text(encoding="utf-8")

    generated_at = metadata.get("generated_at", "unknown") if metadata else "unknown"
    token_estimate = metadata.get("token_estimate", 0) if metadata else 0
    stale_reasons: list[str] = []

    if metadata is None or snapshot is None or metadata.get("snapshot_root_hash") != snapshot.get("root_hash"):
        stale_reasons.append("repo snapshot changed")
    if metadata:
        saved_sha = metadata.get("git_sha") or (metadata.get("freshness") or {}).get("git_sha")
        current_sha = git.current_sha(root) if git.is_git_repo(root) else None
        if saved_sha and current_sha and saved_sha != current_sha:
            stale_reasons.append("git HEAD changed")
        task_md = _task_md_body(root, scoped.thread_id if scoped else None)
        if task_md and task_md != metadata.get("task"):
            stale_reasons.append(".agentpack task differs")

    if stale_reasons:
        reason_text = ", ".join(stale_reasons)
        header = _stale_context_notice(root, metadata, f"{reason_text} since last pack (generated: {generated_at})")
    else:
        header = f"> Context is fresh (generated: {generated_at}, {token_estimate:,} tokens).\n\n"

    return _truncate_to_budget(header + content, max_tokens)


def _task_md_body(root: Path, thread_id: str | None = None) -> str | None:
    scoped = thread_paths(root, thread_id)
    if scoped:
        if not scoped.task.exists():
            return None
        raw = scoped.task.read_text(encoding="utf-8").strip()
        return raw or None
    return read_task_md(root)


def _write_task_md(root: Path, task: str, thread_id: str | None = None) -> None:
    scoped = thread_paths(root, thread_id)
    if scoped:
        scoped.task.parent.mkdir(parents=True, exist_ok=True)
        scoped.task.write_text(task.rstrip() + "\n", encoding="utf-8")
        return
    write_task_md(root, task)


def _resolve_mcp_task(root: Path, task: str = "", thread_id: str | None = None) -> str:
    task = " ".join(task.strip().split())
    if task:
        _write_task_md(root, task, thread_id)
        return task
    task_md = _task_md_body(root, thread_id)
    if task_md:
        return task_md
    if thread_id:
        raise ValueError(
            f"No task is set for AgentPack session {thread_id}. "
            "Call start_task(task=...) or pack_context(task=...) before requesting context."
        )
    inferred, _source = git.infer_task_with_source(root) if git.is_git_repo(root) else ("general", "fallback")
    return inferred


def _pack_context_impl(
    root: Path,
    *,
    task: str = "",
    mode: str = "balanced",
    budget: int = 0,
    max_tokens: int = 20000,
    thread_id: str = "",
    timeout_s: float = 60.0,
) -> str:
    """Write task.md when task is provided, pack context, and return markdown."""
    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    from agentpack.application.pack_service import PackService, PackRequest
    from agentpack.adapters.detect import detect_agent
    from agentpack.renderers.markdown import render_claude
    from agentpack.learning.task_memory import record_task_start_snapshot

    provided_task = bool(task.strip())
    had_task_md = _task_md_body(root, thread_id or None) is not None
    dirty_files_before = sorted(git.dirty_files(root)) if provided_task and git.is_git_repo(root) else []
    resolved_task = _resolve_mcp_task(root, task, thread_id or None)
    agent = detect_agent(root)
    from agentpack.application.pack_service import PackTimeoutError

    try:
        result = PackService().run(PackRequest(
            root=root,
            agent=agent,
            task=resolved_task,
            mode=mode,
            budget=budget,
            since=None,
            refresh=False,
            task_source="mcp" if provided_task else ("task.md" if had_task_md else "git"),
            thread_id=thread_id or None,
            deadline=time.monotonic() + _timeout_seconds(timeout_s),
        ))
    except PackTimeoutError as exc:
        return f"> AgentPack request timed out: {exc}. Retry with a narrower task or larger timeout_s."
    if provided_task:
        try:
            record_task_start_snapshot(
                root,
                task=resolved_task,
                thread=thread_id or "",
                agent=agent,
                context_path=result.out_path,
                dirty_files_before=dirty_files_before,
            )
        except Exception:
            pass
    return _truncate_to_budget(render_claude(result.pack), max_tokens)


def _explain_file_impl(
    root: Path,
    path: str,
    task: str = "",
    thread_id: str | None = None,
    *,
    session: _McpSession | None = None,
    timeout_s: float = 60.0,
) -> str:
    """Testable core of the explain_file MCP tool."""
    from agentpack.application.pack_service import PackTimeoutError, _sf_tokens

    resolved_task = task
    if not resolved_task:
        resolved_task = _task_md_body(root, thread_id) or "general"

    try:
        plan = (session or _session(root)).plan(
            root,
            task=resolved_task,
            mode="balanced",
            budget=0,
            thread_id=thread_id or "",
            task_source="mcp",
            timeout_s=_timeout_seconds(timeout_s),
        )
    except PackTimeoutError as exc:
        return f"MCP request timed out: {exc}. Retry with a narrower task or larger timeout_s."

    score_map = {fi.path: (score, reasons) for fi, score, reasons in plan.scored}
    if path not in score_map:
        return f"File not found in scoring data: {path}"

    score_val, reasons = score_map[path]
    selected_file = next((sf for sf in plan.selected if sf.path == path), None)
    is_selected = selected_file is not None
    include_mode = selected_file.include_mode if selected_file else "excluded"

    token_count = 0
    if selected_file:
        token_count = _sf_tokens(selected_file)
    else:
        for fi in plan.scan_result.packable:
            if fi.path == path:
                token_count = fi.estimated_tokens
                break

    summary_data = plan.summaries.get(path, {})
    raw_symbols = summary_data.get("symbols", []) if isinstance(summary_data, dict) else []
    symbol_names = [s["name"] if isinstance(s, dict) else s.name for s in raw_symbols]

    lines = [
        f"## {path}",
        "",
        f"- **selected**: {'yes' if is_selected else 'no'}",
        f"- **include mode**: {include_mode}",
        f"- **score**: {score_val:.0f}",
        f"- **tokens**: {token_count:,}",
        f"- **task**: {resolved_task}",
        "",
        "### Score signals",
        "",
    ]
    if reasons:
        for reason in reasons:
            lines.append(f"- {reason}")
    else:
        lines.append("_(none)_")

    if symbol_names:
        lines += ["", "### Symbols", ""]
        lines += [f"- `{s}`" for s in symbol_names]

    dep_node = plan.dep_graph.get(path)
    if dep_node.imports:
        lines += ["", "### Imports", ""]
        lines += [f"- `{imp}`" for imp in dep_node.imports[:10]]
    if dep_node.imported_by:
        lines += ["", "### Imported by", ""]
        lines += [f"- `{imp}`" for imp in dep_node.imported_by[:10]]

    return "\n".join(lines)


def _get_related_files_impl(
    root: Path,
    path: str,
    depth: int = 1,
    thread_id: str | None = None,
    *,
    session: _McpSession | None = None,
    timeout_s: float = 60.0,
) -> str:
    """Testable core of the get_related_files MCP tool."""
    from agentpack.application.pack_service import PackTimeoutError

    depth = max(1, min(depth, 2))
    task = _task_md_body(root, thread_id) or "general"

    try:
        plan = (session or _session(root)).plan(
            root,
            task=task,
            mode="balanced",
            budget=0,
            thread_id=thread_id or "",
            task_source="mcp",
            timeout_s=_timeout_seconds(timeout_s),
        )
    except PackTimeoutError as exc:
        return f"MCP request timed out: {exc}. Retry with a narrower task or larger timeout_s."

    graph = plan.dep_graph

    def _neighbours(p: str) -> dict[str, str]:
        node = graph.get(p)
        result: dict[str, str] = {}
        for imp in node.imports:
            result[imp] = "imports"
        for rev in node.imported_by:
            result[rev] = "imported_by"
        for test in node.tests:
            result[test] = "test"
        return result

    seen: dict[str, str] = {}
    frontier = {path}
    for hop in range(depth):
        next_frontier: set[str] = set()
        for p in frontier:
            for rel_path, rel_type in _neighbours(p).items():
                if rel_path != path and rel_path not in seen:
                    label = rel_type if hop == 0 else f"{rel_type} (hop {hop + 1})"
                    seen[rel_path] = label
                    next_frontier.add(rel_path)
        frontier = next_frontier

    if not seen:
        return f"No related files found for `{path}` at depth {depth}."

    lines = [f"## Related files for `{path}`", ""]
    for rel_path, rel_type in sorted(seen.items(), key=lambda x: x[1]):
        lines.append(f"- `{rel_path}` — {rel_type}")
    return "\n".join(lines)


def _graph_index(root: Path, timeout_s: float = 60.0):
    from agentpack.application.pack_service import PackTimeoutError
    from agentpack.architecture.index import SemanticGraphIndex
    from agentpack.architecture.service import build_snapshot_for_ref

    deadline = time.monotonic() + _timeout_seconds(timeout_s)
    snapshot = build_snapshot_for_ref(root, cache_validation="manifest")
    if time.monotonic() >= deadline:
        raise PackTimeoutError("AgentPack graph request timed out during snapshot validation")
    key = (str(root.resolve()), _snapshot_cache_key(snapshot))
    with _GRAPH_INDEX_LOCK:
        cached = _GRAPH_INDEX_CACHE.get(key)
        if cached is not None:
            _GRAPH_INDEX_CACHE.move_to_end(key)
            return cached
    index = SemanticGraphIndex(snapshot)
    if time.monotonic() >= deadline:
        raise PackTimeoutError("AgentPack graph request timed out during index construction")
    with _GRAPH_INDEX_LOCK:
        _GRAPH_INDEX_CACHE[key] = index
        _GRAPH_INDEX_CACHE.move_to_end(key)
        while len(_GRAPH_INDEX_CACHE) > 4:
            _GRAPH_INDEX_CACHE.popitem(last=False)
    return index


def _graph_output(root: Path, payload: dict, output_format: str = "toon") -> str:
    requested = "toon" if output_format == "auto" else output_format
    return to_llm(root, payload, requested=requested, root_name="agentpack_graph")


def _graph_detail(detail: str) -> str:
    if detail not in {"compact", "full"}:
        raise ValueError("detail must be 'compact' or 'full'")
    return detail


_GRAPH_RELATIONSHIPS = {
    "contains", "imports", "calls", "references", "inherits", "implements",
    "tested_by", "documents", "configures", "reads_from", "writes_to",
    "publishes", "consumes", "declared_dependency",
}


def _graph_limit(value: int, maximum: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return min(value, maximum)


def _graph_relationship(value: str) -> str:
    if value and value not in _GRAPH_RELATIONSHIPS:
        raise ValueError(f"unsupported graph relationship: {value}")
    return value


def _compact_graph_node(node: dict) -> dict:
    locator = node.get("locator") or {}
    evidence_refs = []
    for evidence in (node.get("evidence") or [])[:2]:
        evidence_refs.append({
            "path": evidence.get("path"),
            "start_line": evidence.get("start_line"),
            "end_line": evidence.get("end_line"),
            "source": evidence.get("source"),
            "source_hash": evidence.get("source_hash"),
        })
    return {
        "entity_key": node.get("entity_key"),
        "type": node.get("entity_type"),
        "name": node.get("qualified_name") or node.get("display_name"),
        "path": locator.get("path"),
        "line": locator.get("start_line"),
        "language": node.get("language"),
        "confidence_tier": node.get("confidence_tier"),
        "evidence_refs": evidence_refs,
    }


def _compact_graph_edge(row: dict) -> dict:
    node = row.get("node") or {}
    return {
        "edge_key": row.get("edge_key"),
        "relationship": row.get("relationship"),
        "confidence_tier": row.get("confidence_tier"),
        "node": _compact_graph_node(node),
        "evidence": (row.get("evidence") or [])[:1],
    }


def _query_graph_impl(root: Path, text: str, relationship: str = "", limit: int = 20, detail: str = "compact", output_format: str = "toon", timeout_s: float = 60.0) -> str:
    detail = _graph_detail(detail)
    if not text.strip():
        raise ValueError("text must not be empty")
    relationship = _graph_relationship(relationship)
    limit = _graph_limit(limit, 100, "limit")
    index = _graph_index(root, timeout_s)
    hits = index.query(text, limit=limit)
    if relationship:
        related_keys = {
            key
            for edge in index.snapshot.edges
            if edge.edge_type == relationship
            for key in (edge.source_entity_key, edge.target_entity_key)
        }
        hits = [hit for hit in hits if hit.entity.entity_key in related_keys]
    rows = []
    for hit in hits:
        entity = hit.entity.model_dump(mode="json")
        rows.append({"score": hit.score, "entity": entity if detail == "full" else _compact_graph_node(entity)})
    return _graph_output(root, {"query": text, "relationship": relationship, "results": rows, "snapshot": {"schema_version": index.snapshot.schema_version, "commit_sha": index.snapshot.commit_sha, "unresolved_entities": sum(1 for entity in index.snapshot.entities if entity.entity_type in {"external", "unresolved"})}}, output_format)


def _get_graph_node_impl(root: Path, name: str, detail: str = "compact", output_format: str = "toon", timeout_s: float = 60.0) -> str:
    detail = _graph_detail(detail)
    if not name.strip():
        raise ValueError("name must not be empty")
    index = _graph_index(root, timeout_s)
    rows = []
    for entity in index.resolve(name)[:20]:
        payload = entity.model_dump(mode="json")
        if detail != "full":
            payload = {"entity_key": payload["entity_key"], "type": payload["entity_type"], "qualified_name": payload["qualified_name"], "path": payload["locator"]["path"], "line": payload["locator"]["start_line"], "confidence_tier": payload["confidence_tier"], "evidence": payload["evidence"][:2]}
        rows.append(payload)
    return _graph_output(root, {"name": name, "nodes": rows}, output_format)


def _get_graph_neighbors_impl(root: Path, name: str, relationship: str = "", direction: str = "both", limit: int = 50, detail: str = "compact", output_format: str = "toon", timeout_s: float = 60.0) -> str:
    detail = _graph_detail(detail)
    if not name.strip():
        raise ValueError("name must not be empty")
    relationship = _graph_relationship(relationship)
    limit = _graph_limit(limit, 100, "limit")
    index = _graph_index(root, timeout_s)
    rows = index.neighbors(name, relationship=relationship, direction=direction, limit=limit)
    if detail != "full":
        rows = [
            {
                "edge_key": row["edge_key"],
                "relationship": row["relationship"],
                "confidence_tier": row["confidence_tier"],
                "node": _compact_graph_node(row["node"]),
                "evidence": row["evidence"][:1],
            }
            for row in rows
        ]
    return _graph_output(root, {"name": name, "neighbors": rows}, output_format)


def _shortest_path_impl(root: Path, source: str, target: str, max_hops: int = 8, detail: str = "compact", output_format: str = "toon", timeout_s: float = 60.0) -> str:
    detail = _graph_detail(detail)
    if not source.strip() or not target.strip():
        raise ValueError("source and target must not be empty")
    max_hops = _graph_limit(max_hops, 32, "max_hops")
    index = _graph_index(root, timeout_s)
    rows = index.shortest_path(source, target, max_hops=max_hops)
    if detail != "full":
        rows = [_compact_graph_edge(row) for row in rows]
    return _graph_output(root, {"source": source, "target": target, "path": rows}, output_format)


def _explain_graph_edge_impl(root: Path, edge_key: str, detail: str = "compact", output_format: str = "toon", timeout_s: float = 60.0) -> str:
    detail = _graph_detail(detail)
    if not edge_key.strip():
        raise ValueError("edge_key must not be empty")
    index = _graph_index(root, timeout_s)
    explanation = index.explain_edge(edge_key)
    if detail != "full" and explanation is not None:
        explanation = {
            "edge": _compact_graph_edge(explanation["edge"]),
            "source": _compact_graph_node(explanation["source"]),
            "target": _compact_graph_node(explanation["target"]),
            "evidence": explanation["evidence"][:1],
        }
    return _graph_output(root, {"edge_key": edge_key, "explanation": explanation}, output_format)


def _get_stats_impl(root: Path) -> str:
    """Testable core of the get_stats MCP tool."""
    metadata_path = root / ".agentpack" / "pack_metadata.json"

    if not metadata_path.exists():
        return "No pack metadata found. Run pack_context() first."

    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Failed to read pack metadata: {exc}"

    cache_stats = _cache_stats(root)
    lines = [
        "## AgentPack Stats",
        "",
        f"- **task**: {meta.get('task', 'unknown')}",
        f"- **generated_at**: {meta.get('generated_at', 'unknown')}",
        f"- **mode**: {meta.get('mode', 'unknown')}",
        f"- **budget**: {meta.get('budget', 0):,} tokens",
        f"- **packed_tokens**: {meta.get('token_estimate', 0):,}",
        f"- **agent**: {meta.get('agent', 'unknown')}",
        f"- **mcp_plan_cache_entries**: {cache_stats['plans']}",
        f"- **mcp_workspace_sessions**: {cache_stats['workspaces']}",
    ]

    metrics_path = root / ".agentpack" / "metrics.jsonl"
    if metrics_path.exists():
        try:
            lines_raw = metrics_path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines_raw):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                lines += [
                    "",
                    "### Last pack run",
                    "",
                    f"- **raw_tokens**: {rec.get('raw_tokens', 0):,}",
                    f"- **saving**: {rec.get('saving_pct', 0):.1f}%",
                    f"- **selected_files**: {rec.get('selected_files', 0)}",
                    f"- **changed_files**: {rec.get('changed_files', 0)}",
                    f"- **excluded_files**: {rec.get('excluded_files', 0)} (score too low)",
                    f"- **total_time**: {rec.get('total_s', 0):.2f}s",
                ]
                if rec.get("selection_f1"):
                    lines.append(f"- **selection_f1**: {rec['selection_f1']:.3f}")
                excluded_paths = rec.get("excluded_paths", [])
                if excluded_paths:
                    lines += ["", "### Below-threshold files (top 10)", ""]
                    for p in excluded_paths:
                        lines.append(f"- `{p}`")
                break
        except Exception:
            pass

    return "\n".join(lines)


def _get_pr_context_impl(
    root: Path,
    *,
    pr: str = "",
    focus: str = "",
    output_format: StructuredFormat = "toon",
    allow_local_fallback: bool = False,
) -> str:
    """Testable implementation for the MCP PR-context contract."""
    from agentpack.application.pr_context import PRContextError, resolve_pr_context

    try:
        context = resolve_pr_context(
            root,
            pr=pr or None,
            focus=focus,
            allow_local_fallback=allow_local_fallback,
        )
    except PRContextError as exc:
        payload = {"ok": False, "error": str(exc), "allow_local_fallback": allow_local_fallback}
        return to_llm(root, payload, requested=output_format, root_name="agentpack_pr_context")
    return to_llm(
        root,
        {"ok": True, "pr_context": context.model_dump(mode="json")},
        requested=output_format,
        root_name="agentpack_pr_context",
    )


def _get_delta_context_impl(root: Path, max_files: int = 12) -> str:
    """Return the latest saved delta summary and selected-file changes."""
    metadata_path = root / ".agentpack" / "pack_metadata.json"
    if not metadata_path.exists():
        return "No pack metadata found. Run pack_context() first."
    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Failed to read pack metadata: {exc}"

    freshness = meta.get("freshness") or {}
    delta = freshness.get("delta_summary") or "No selected-file delta recorded for the latest pack."
    selected = meta.get("selected_files_meta") or []
    lines = ["## AgentPack Delta", "", delta, ""]
    if selected:
        lines += ["### Current top selected files", ""]
        for item in selected[:max(1, max_files)]:
            if not isinstance(item, dict):
                continue
            path = item.get("path", "")
            mode = item.get("mode", "")
            why = item.get("why", "")
            suffix = f" — {why}" if why else ""
            lines.append(f"- `{path}` ({mode}){suffix}")
    return "\n".join(lines)


def _get_task_map_impl(root: Path, output_format: StructuredFormat = "auto", max_files: int = 50) -> str:
    metadata = load_pack_metadata(root) or {}
    task_map = metadata.get("task_map") if isinstance(metadata, dict) else {}
    if not isinstance(task_map, dict) or not task_map:
        return "No task map found. Run `agentpack pack` or MCP `pack_context()` first."
    files = task_map.get("files")
    if isinstance(files, list) and max_files > 0:
        task_map = {**task_map, "files": files[:max_files]}
    requested = "toon" if output_format == "auto" else output_format
    return to_llm(root, task_map, requested=requested, root_name="agentpack_task_map")


def _retrieve_context_impl(
    root: Path,
    path: str = "",
    block_id: str = "",
    mode: str = "as_stored",
    allow_stale: bool = False,
    *,
    targets: list[str] | None = None,
    kind: str = "any",
    max_tokens: int = 12_000,
) -> str:
    from agentpack.core.config import load_config
    from agentpack.core.pack_registry import retrieve_from_registry
    from agentpack.session.events import record_event

    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    cfg = load_config(root)
    clean_kind = kind if kind in {"any", "selected", "omitted"} else "any"
    target_paths = [item for item in (targets or []) if item]
    if target_paths:
        results = [
            retrieve_from_registry(
                root,
                path=target,
                mode=mode,
                allow_stale=allow_stale,
                kind=clean_kind,  # type: ignore[arg-type]
                max_chars=cfg.runtime.max_retrieve_chars,
                registry_file=root / cfg.runtime.pack_registry_output,
            )
            for target in target_paths[:12]
        ]
        result = "\n\n---\n\n".join(results)
        omitted_targets = max(0, len(target_paths) - 12)
        if omitted_targets:
            result += f"\n\n> Retrieval omitted {omitted_targets} targets beyond 12-target safety cap."
    else:
        result = retrieve_from_registry(
            root,
            path=path,
            block_id=block_id,
            mode=mode,
            allow_stale=allow_stale,
            kind=clean_kind,  # type: ignore[arg-type]
            max_chars=cfg.runtime.max_retrieve_chars,
            registry_file=root / cfg.runtime.pack_registry_output,
        )
    record_event(
        root,
        "retrieve",
        {
            "path": path,
            "block_id": block_id,
            "targets": target_paths,
            "mode": mode,
            "kind": clean_kind,
            "allow_stale": allow_stale,
        },
        output_path=cfg.runtime.session_events_output,
    )
    return _truncate_to_budget(
        result,
        max_tokens,
        marker=(
            "> [Retrieval truncated by AgentPack to fit output budget. "
            "Narrow targets or request one file at a time.]"
        ),
    )


def _compress_output_impl(
    root: Path,
    content: str,
    kind: str = "auto",
    max_input_chars: int = 250_000,
    max_input_tokens: int | None = 60_000,
) -> str:
    from agentpack.core.config import load_config
    from agentpack.output_compression import compress_output
    from agentpack.session.events import record_event

    if max_input_chars < 1:
        raise ValueError("max_input_chars must be at least 1")
    if max_input_chars > MCP_MAX_INPUT_TOKENS * 4:
        raise ValueError(f"max_input_chars must be no more than {MCP_MAX_INPUT_TOKENS * 4}")
    if max_input_tokens is not None:
        max_input_tokens = _validate_token_limit(max_input_tokens, "max_input_tokens", maximum=MCP_MAX_INPUT_TOKENS)
    original_chars = len(content)
    truncated_input = original_chars > max_input_chars
    if len(content) > max_input_chars:
        content = content[:max_input_chars]
    if max_input_tokens is not None and estimate_tokens(content) > max_input_tokens:
        truncated_input = True
        content = _hard_prefix(content, max_input_tokens)
    if truncated_input:
        marker = "\n... input truncated by AgentPack ..."
        if max_input_tokens is not None:
            content = _fit_prefix_with_suffix(content, marker, max_input_tokens)
        elif "input truncated by AgentPack" not in content:
            content = content.rstrip() + marker
    cfg = load_config(root)
    result = compress_output(content, kind=kind, max_items=cfg.runtime.max_output_summary_items)
    record_event(
        root,
        "compress_output",
        {
            "kind": kind,
            "input_chars": original_chars,
            "output_chars": len(result),
            "input_truncated": truncated_input,
            "input_tokens": estimate_tokens(content),
            "input_token_limit": max_input_tokens,
        },
        output_path=cfg.runtime.session_events_output,
    )
    return result


def _validate_toon_impl(
    root: Path,
    *,
    content: str = "",
    path: str = "",
    require_format: bool = True,
    schema: str = "",
    allow_json: bool = False,
    return_canonical: bool = False,
    output_format: StructuredFormat = "toon",
) -> str:
    from agentpack.core.toon_validator import canonicalize_to_toon_text, validate_toon_file, validate_toon_text

    raw_text = ""
    if bool(content) == bool(path):
        payload = {
            "ok": False,
            "source": path or "<string>",
            "root": None,
            "parsed_type": None,
            "error": "provide exactly one of content or path",
            "warnings": [],
            "schema": schema,
            "input_format": "",
            "repair_hint": "",
            "canonical_available": False,
        }
    elif path:
        target = (root / path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            payload = {
                "ok": False,
                "source": path,
                "root": None,
                "parsed_type": None,
                "error": "path must stay inside repo root",
                "warnings": [],
                "schema": schema,
                "input_format": "",
                "repair_hint": "",
                "canonical_available": False,
            }
        else:
            result = validate_toon_file(
                target,
                require_format=require_format,
                schema=schema,
                allow_json=allow_json,
            )
            payload = result.as_dict()
            if result.ok and return_canonical:
                raw_text = target.read_text(encoding="utf-8")
    else:
        result = validate_toon_text(
            content,
            source="<content>",
            require_format=require_format,
            schema=schema,
            allow_json=allow_json,
        )
        payload = result.as_dict()
        if result.ok and return_canonical:
            raw_text = content
    if return_canonical and payload.get("ok") and raw_text:
        try:
            canonical = canonicalize_to_toon_text(
                raw_text,
                schema=schema,
                source=str(payload.get("source") or path or "<content>"),
                allow_json=allow_json,
            )
        except ValueError as exc:
            payload["ok"] = False
            payload["error"] = f"unable to render canonical TOON: {exc}"
        else:
            payload["canonical_toon"] = canonical.text
            payload["canonical_root"] = canonical.root
            payload["canonical_input_format"] = canonical.input_format
    return to_llm(root, payload, requested=output_format, root_name="agentpack_toon_validation")


def _route_task_impl(
    root: Path,
    task: str,
    output_format: StructuredFormat = "toon",
    detail: str = "compact",
    thread_id: str = "",
    timeout_s: float = 60.0,
    max_tokens: int = MCP_DEFAULT_OUTPUT_TOKENS,
) -> str:
    """Return a compact read-only route payload; full details remain opt-in."""
    from agentpack.application.pack_service import PackTimeoutError

    try:
        result = _session(root).route_service.route_task(
            root,
            task,
            thread_id=thread_id,
            timeout_s=_timeout_seconds(timeout_s),
        )
    except PackTimeoutError as exc:
        return to_llm(
            root,
            {"ok": False, "error": str(exc), "retry_hint": "narrow task or increase timeout_s"},
            requested=output_format,
            root_name="agentpack_route",
        )
    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    if detail == "full":
        payload = result.model_dump(mode="json")
    elif detail == "compact":
        payload = _compact_route_payload(result)
    else:
        raise ValueError("detail must be 'compact' or 'full'")
    return _bounded_structured(root, payload, output_format, "agentpack_route", max_tokens)


def _compact_route_payload(result) -> dict:
    """Keep routing useful to an agent without duplicating the generated prompt."""
    return {
        "task": result.task,
        "recommended_interaction_mode": result.recommended_interaction_mode,
        "mode_reason": result.mode_reason,
        "current_agent": result.current_agent,
        "reviewer_agent": result.reviewer_agent,
        "task_mode": result.task_mode,
        "task_mode_confidence": result.task_mode_confidence,
        "task_mode_signals": result.task_mode_signals,
        "selected_files": result.selected_files[:12],
        "selected_skills": [
            {
                "name": item.skill.name,
                "path": item.skill.path,
                "score": item.score,
                "confidence": item.confidence,
                "reasons": item.reasons[:3],
            }
            for item in result.selected_skills[:8]
        ],
        "baseline_skills": [
            {"name": item.skill.name, "path": item.skill.path}
            for item in result.baseline_skills[:8]
        ],
        "applied_rules": [
            {"name": item.rule.name, "path": item.rule.path, "reasons": item.reasons[:3]}
            for item in result.applied_rules[:8]
        ],
        "suggested_commands": [item.model_dump(mode="json") for item in result.suggested_commands[:8]],
        "evidence_checklist": result.evidence_checklist[:8],
        "routing_notes": result.routing_notes[:8],
        "prompt_quality_warnings": result.prompt_quality_warnings[:8],
        "safety_warnings": result.safety_warnings[:12],
    }


def _bounded_structured(
    root: Path,
    payload: dict[str, Any],
    output_format: StructuredFormat,
    root_name: str,
    max_tokens: int,
    *,
    list_fields: tuple[str, ...] = (),
) -> str:
    """Render structured payload without allowing inventory-style fan-out."""
    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    rendered = to_llm(root, payload, requested=output_format, root_name=root_name)
    if estimate_tokens(rendered) <= max_tokens:
        return rendered

    candidate = dict(payload)
    for _ in range(8):
        changed = False
        for field in list_fields:
            values = candidate.get(field)
            if isinstance(values, list) and values:
                shortened = values[: max(1, len(values) // 2)]
                if len(shortened) < len(values):
                    candidate[field] = shortened
                    changed = True
        candidate["truncated"] = True
        rendered = to_llm(root, candidate, requested=output_format, root_name=root_name)
        if estimate_tokens(rendered) <= max_tokens:
            return rendered
        if not changed:
            break

    minimal = {
        "truncated": True,
        "message": "Response exceeded MCP token budget; narrow request or increase max_tokens.",
    }
    if "body_fetch" in payload:
        minimal["body_fetch"] = payload["body_fetch"]
    rendered = to_llm(root, minimal, requested=output_format, root_name=root_name)
    if estimate_tokens(rendered) <= max_tokens:
        return rendered
    # Structured callers must never receive arbitrary character slices. `null`
    # is valid JSON and fits even the smallest accepted token budget.
    if output_format in {"auto", "json"}:
        return "null"
    raise ValueError("max_tokens is too small for a valid structured response")


def _skill_metadata(skill: Any) -> dict[str, Any]:
    data = skill.model_dump(mode="json") if hasattr(skill, "model_dump") else dict(skill)
    data.pop("raw_text", None)
    return data


def _get_skills_impl(
    root: Path,
    output_format: StructuredFormat = "toon",
    max_items: int = 50,
    max_tokens: int = 4_000,
) -> str:
    """Return discovered skill/rule inventory payload."""
    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    max_items = min(max_items, 500)
    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    inventory = _session(root).route_service.inventory(root)
    skills = [_skill_metadata(item) for item in inventory.skills]
    rules = [_skill_metadata(item) for item in inventory.rules]
    payload = {
        "version": inventory.version,
        "skills": skills[:max_items],
        "rules": rules[:max_items],
        "total_skills": len(skills),
        "total_rules": len(rules),
        "returned_skills": min(len(skills), max_items),
        "returned_rules": min(len(rules), max_items),
        "body_fetch": "Use get_skill(name_or_path) for raw skill content.",
    }
    return _bounded_structured(
        root,
        payload,
        output_format,
        "agentpack_skills",
        max_tokens,
        list_fields=("skills", "rules"),
    )


def _get_skill_impl(
    root: Path,
    name_or_path: str,
    max_chars: int = 20000,
    max_tokens: int = 4_000,
) -> str:
    """Return one skill's raw SKILL.md content by name or path."""
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    if max_chars > MCP_MAX_INPUT_TOKENS * 4:
        raise ValueError(f"max_chars must be no more than {MCP_MAX_INPUT_TOKENS * 4}")
    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    content = _session(root).route_service.get_skill(root, name_or_path)
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + "\n\n... skill output truncated by AgentPack ..."
    return _truncate_to_budget(
        content,
        max_tokens,
        marker="... skill output truncated by AgentPack; request narrower skill content ...",
    )


def _explain_route_impl(
    root: Path,
    task: str,
    output_format: StructuredFormat = "toon",
    thread_id: str = "",
    timeout_s: float = 60.0,
    max_items: int = 40,
    max_tokens: int = 8_000,
) -> str:
    """Return task route payload including all positive skill scores."""
    from agentpack.application.pack_service import PackTimeoutError

    try:
        result = _session(root).route_service.explain_route(
            root,
            task,
            thread_id=thread_id,
            timeout_s=_timeout_seconds(timeout_s),
        )
    except PackTimeoutError as exc:
        return to_llm(
            root,
            {"ok": False, "error": str(exc), "retry_hint": "narrow task or increase timeout_s"},
            requested=output_format,
            root_name="agentpack_route_explanation",
        )
    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    max_items = min(max_items, 500)
    max_tokens = _validate_token_limit(max_tokens, "max_tokens")
    payload = result.model_dump(mode="json")
    scores = payload.get("skill_scores") or []
    payload["skill_scores_total"] = len(scores)
    payload["skill_scores"] = scores[:max_items]
    payload["skill_scores_truncated"] = len(scores) > max_items
    # Full route already exposes selected evidence; avoid duplicating generated prompt.
    payload.pop("agent_prompt", None)
    return _bounded_structured(
        root,
        payload,
        output_format,
        "agentpack_route_explanation",
        max_tokens,
        list_fields=("skill_scores", "selected_files", "selection_explanations", "omitted_files"),
    )


def _learning_recommendations_impl(root: Path, request: str = "", scope: str = "local") -> str:
    from agentpack.learning.recommender import record_recommendation_impressions
    from agentpack.learning.service import get_learning_recommendations

    recommendations = get_learning_recommendations(root, request=request, scope=scope)
    recommendations = record_recommendation_impressions(recommendations)
    return to_llm(
        root,
        recommendations.model_dump(mode="json"),
        requested="toon",
        root_name="learning_recommendations",
    )


def _learning_start_impl(root: Path, topic_id: str, project_id: str = "", mode: str = "") -> str:
    from agentpack.learning.service import coaching_prompt, start_learning_session

    session, duplicate = start_learning_session(
        root,
        topic_id,
        project_id_value=project_id,
        mode=mode,
    )
    return to_llm(
        root,
        {
            "session": session.model_dump(mode="json"),
            "coaching_prompt": coaching_prompt(session),
            "duplicate": duplicate,
        },
        requested="toon",
        root_name="learning_session",
    )


def _learning_complete_impl(root: Path, session_id: str, proof: dict[str, Any]) -> str:
    from agentpack.learning.service import complete_learning_session_with_proof

    session, duplicate = complete_learning_session_with_proof(root, session_id, proof)
    return to_llm(
        root,
        {"session": session.model_dump(mode="json"), "duplicate": duplicate},
        requested="toon",
        root_name="learning_completion",
    )


def serve() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "mcp package required for MCP server. "
            "Install: pipx inject agentpack-cli 'agentpack-cli[mcp]'",
            file=sys.stderr,
        )
        sys.exit(1)

    mcp = FastMCP("agentpack")

    @mcp.tool()
    def readiness(format: str = "toon") -> str:
        """Prove this host exposes AgentPack MCP tools and report server/CLI status.

        If an agent can call this tool and read the response, live MCP exposure is confirmed.
        CLI doctor can only verify registration/config; this tool verifies the active host path.
        """
        return _readiness_impl(_repo_root(), format)

    @mcp.tool()
    def learning_recommendations(request: str = "", scope: str = "local") -> str:
        """Return up to three competency-backed topics from local or global project evidence."""
        return _learning_recommendations_impl(_repo_root(), request=request, scope=scope)

    @mcp.tool()
    def learning_start(topic_id: str, project_id: str = "", mode: str = "") -> str:
        """Start one coached topic and return its question, rubric, evidence, and coaching prompt."""
        return _learning_start_impl(_repo_root(), topic_id, project_id=project_id, mode=mode)

    @mcp.tool()
    def learning_complete(session_id: str, proof: dict[str, Any]) -> str:
        """Validate and record host-evaluated structured proof for a learning session."""
        return _learning_complete_impl(_repo_root(), session_id, proof)

    @mcp.tool()
    def start_task(task: str, mode: str = "balanced", budget: int = 0, max_tokens: int = 20000, thread_id: str = "", timeout_s: float = 60.0) -> str:
        """Start a new coding task: write session task.md, pack context, and return it.

        This is the recommended MCP-first entry point at the start of a task.
        """
        root = _repo_root()
        resolved_thread = resolve_session_thread_option(thread_id) or ""
        from agentpack.adapters.detect import detect_agent
        from agentpack.session.events import record_event

        record_event(
            root,
            "task_started",
            {"task": task, "thread_id": resolved_thread, "agent": detect_agent(root)},
            source="mcp",
        )
        return _pack_context_impl(
            root,
            task=task,
            mode=mode,
            budget=budget,
            max_tokens=max_tokens,
            thread_id=resolved_thread,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    def pack_context(task: str = "", mode: str = "balanced", budget: int = 0, max_tokens: int = 20000, thread_id: str = "", timeout_s: float = 60.0) -> str:
        """Generate a ranked context pack.

        Args:
            task: Optional task text. If provided, AgentPack writes it to the current session task.md.
                  If omitted, scoped sessions require an existing session task; legacy global mode may infer from git.
            mode: lite | balanced (default) | deep.
            budget: Token budget, 0 = config default (usually 40000).
            max_tokens: Maximum tokens to return (default 20000). Increase for deep context.

        Returns the packed context as a markdown string.
        """
        return _pack_context_impl(
            _repo_root(),
            task=task,
            mode=mode,
            budget=budget,
            max_tokens=max_tokens,
            thread_id=resolve_session_thread_option(thread_id) or "",
            timeout_s=timeout_s,
        )

    @mcp.tool()
    def create_handoff(report: dict[str, Any], name: str = "", target_provider: str = "", target_session_id: str = "") -> str:
        """Create a single-consumer handoff with the complete Git-visible worktree patch."""
        from agentpack.core.handoff import create_handoff as create

        record = create(
            _repo_root(),
            report,
            name=name,
            target_provider=target_provider,
            target_session_id=target_session_id,
        )
        payload = record.model_dump(mode="json")
        payload.pop("handoff_id", None)
        return to_llm(_repo_root(), payload, requested="toon", root_name="handoff")

    @mcp.tool()
    def list_handoffs(status: str = "ready", format: str = "toon") -> str:
        """List project handoffs without exposing internal UUIDs."""
        from agentpack.core.handoff import HandoffStore

        if format not in {"toon", "json"}:
            raise ValueError("format must be toon or json")
        statuses = None if status in {"", "all"} else {status}
        records = []
        for record in HandoffStore(_repo_root()).list(statuses):
            records.append({
                "name": record.name,
                "status": record.status,
                "created_at": record.created_at.isoformat(),
                "source_provider": record.source.provider,
                "task": record.report.task,
                "summary": record.report.summary,
                "next_action": record.report.next_action,
            })
        return to_llm(_repo_root(), records, requested=cast(StructuredFormat, format), root_name="handoffs")

    @mcp.tool()
    def get_handoff(name: str, format: str = "toon") -> str:
        """Return one handoff by its memorable project-scoped name."""
        from agentpack.core.handoff import HandoffStore, render_markdown

        record = HandoffStore(_repo_root()).load(name)
        if format == "markdown":
            return render_markdown(record)
        if format not in {"toon", "json"}:
            raise ValueError("format must be toon, json, or markdown")
        payload = record.model_dump(mode="json")
        payload.pop("handoff_id", None)
        return to_llm(_repo_root(), payload, requested=cast(StructuredFormat, format), root_name="handoff")

    @mcp.tool()
    def accept_handoff(name: str = "", latest: bool = False, max_tokens: int = 20000) -> str:
        """Atomically claim a handoff, apply its patch, and return bounded fresh context."""
        from agentpack.core.handoff import accept_handoff as accept

        record, warnings = accept(_repo_root(), name, latest=latest)
        context = _pack_context_impl(
            _repo_root(),
            task=record.report.task,
            max_tokens=max_tokens,
            thread_id=record.claim.thread_id if record.claim else "",
        )
        payload = record.model_dump(mode="json")
        payload.pop("handoff_id", None)
        return to_llm(
            _repo_root(),
            {"handoff": payload, "warnings": warnings, "context": context},
            requested="toon",
            root_name="handoff_resume",
        )

    @mcp.tool()
    def release_handoff(name: str) -> str:
        """Release the current session's claim so another session can resume it."""
        from agentpack.core.handoff import release_handoff as release

        record = release(_repo_root(), name)
        return to_llm(
            _repo_root(),
            {"name": record.name, "status": record.status},
            requested="toon",
            root_name="handoff",
        )

    @mcp.tool()
    def route_task(
        task: str,
        format: str = "toon",
        detail: str = "compact",
        thread_id: str = "",
        timeout_s: float = 60.0,
        max_tokens: int = MCP_DEFAULT_OUTPUT_TOKENS,
    ) -> str:
        """Route a task to files, rules, skills, command suggestions, and safety warnings.

        Read-only: does not write task.md or context files. The default compact response
        contains top files, reasons, actions, and warnings. Use detail='full' or
        explain_route when full routing evidence is needed.
        """
        return _route_task_impl(
            _repo_root(), task, format, detail, resolve_session_thread_option(thread_id) or "", timeout_s, max_tokens
        )

    @mcp.tool()
    def get_skills(format: str = "toon", max_items: int = 50, max_tokens: int = 4_000) -> str:
        """Return bounded skill/rule metadata; use get_skill for one raw body."""
        return _get_skills_impl(_repo_root(), format, max_items=max_items, max_tokens=max_tokens)

    @mcp.tool()
    def get_skill(name_or_path: str, max_chars: int = 20000, max_tokens: int = 4_000) -> str:
        """Return one AgentPack skill by name or path.

        Use after route_task/explain_route recommends a skill and before applying it.
        """
        return _get_skill_impl(_repo_root(), name_or_path, max_chars=max_chars, max_tokens=max_tokens)

    @mcp.tool()
    def explain_route(
        task: str,
        format: str = "toon",
        thread_id: str = "",
        timeout_s: float = 60.0,
        max_items: int = 40,
        max_tokens: int = 8_000,
    ) -> str:
        """Return bounded route payload with top skill scoring reasons."""
        return _explain_route_impl(
            _repo_root(), task, format, resolve_session_thread_option(thread_id) or "", timeout_s, max_items, max_tokens
        )

    @mcp.tool()
    def get_context(thread_id: str = "", max_tokens: int = MCP_DEFAULT_OUTPUT_TOKENS) -> str:
        """Return the latest session context pack, auto-refreshing when task.md changed.

        Fast for fresh packs. Blocks for one refresh if the current task differs from the packed task.
        Returns empty string if no pack exists yet.

        Args:
            max_tokens: Hard output budget. Default 20,000.
        """
        return _get_context_impl(_repo_root(), resolve_session_thread_option(thread_id), max_tokens)

    @mcp.tool()
    def refresh() -> str:
        """Refresh context using the current ambient session task file.

        Equivalent to running `agentpack session refresh`.
        Returns summary of what was packed.
        """
        from agentpack.commands._shared import run_refresh
        from agentpack.session.state import load_session
        from agentpack.adapters.detect import detect_agent

        root = _repo_root()
        state = load_session(root)
        agent = state.agent if state else detect_agent(root)
        mode = state.mode if state else "balanced"

        thread = resolve_session_thread_option("")
        result = run_refresh(root, agent, mode, 0, thread_id=thread)
        if result is None:
            return "Refresh failed."
        return (
            f"Refreshed: {result['files']} files, "
            f"{result['tokens']:,} tokens, "
            f"{result['saving']:.1f}% saving"
        )

    @mcp.tool()
    def explain_file(path: str, task: str = "", thread_id: str = "", timeout_s: float = 60.0) -> str:
        """Return score breakdown and symbol list for a specific file.

        Args:
            path: Repo-relative file path (e.g. "src/auth/session.py").
            task: Optional task description to score against. Defaults to current session task.md.

        Returns a markdown string with score signals, include mode, token count, and symbols.
        """
        return _explain_file_impl(
            _repo_root(), path, task, resolve_session_thread_option(thread_id), timeout_s=timeout_s
        )

    @mcp.tool()
    def get_related_files(path: str, depth: int = 1, thread_id: str = "", timeout_s: float = 60.0) -> str:
        """Return import-graph neighbours of a file (files it imports + files that import it).

        Args:
            path: Repo-relative file path (e.g. "src/auth/session.py").
            depth: Graph traversal depth (1 = direct neighbours, 2 = two hops). Max 2.

        Returns a markdown list of related files with their relationship type.
        """
        return _get_related_files_impl(
            _repo_root(), path, depth, resolve_session_thread_option(thread_id), timeout_s=timeout_s
        )

    @mcp.tool()
    def get_delta_context(max_files: int = 12) -> str:
        """Return selected-file delta and top current files from the latest pack.

        Args:
            max_files: Number of selected files to include. Default 12.

        Returns a compact markdown delta suitable for hooks and agent refresh checks.
        """
        return _get_delta_context_impl(_repo_root(), max_files)

    @mcp.tool()
    def get_task_map(format: str = "toon", max_files: int = 50) -> str:
        """Return risk-aware task map for the latest pack without loading full context.

        Args:
            format: auto | toon | json.
            max_files: Maximum task-map rows to include. Default 50.
        """
        return _get_task_map_impl(_repo_root(), output_format=format, max_files=max_files)

    @mcp.tool()
    def retrieve_context(
        path: str = "",
        block_id: str = "",
        mode: str = "as_stored",
        allow_stale: bool = False,
        targets: list[str] | None = None,
        kind: str = "any",
        max_tokens: int = 12_000,
    ) -> str:
        """Retrieve full or stored content for a selected/omitted pack registry record.

        Args:
            path: Repo-relative path to retrieve.
            block_id: Stable block id from the pack registry. Optional if path is set.
            mode: as_stored | full | skeleton | symbols | summary.
            allow_stale: If false, refuse retrieval when file changed since the latest pack.
            targets: Optional list of repo-relative paths to retrieve in one call.
            kind: any | selected | omitted.
            max_tokens: Aggregate hard output budget. Default 12,000.
        """
        return _retrieve_context_impl(
            _repo_root(),
            path=path,
            block_id=block_id,
            mode=mode,
            allow_stale=allow_stale,
            targets=targets,
            kind=kind,
            max_tokens=max_tokens,
        )

    @mcp.tool()
    def compress_output(
        content: str,
        kind: str = "auto",
        max_input_chars: int = 250000,
        max_input_tokens: int | None = 60_000,
    ) -> str:
        """Summarize noisy command output with bounded token input."""
        return _compress_output_impl(
            _repo_root(), content=content, kind=kind, max_input_chars=max_input_chars,
            max_input_tokens=max_input_tokens,
        )

    @mcp.tool()
    def validate_toon(
        content: str = "",
        path: str = "",
        require_format: bool = True,
        schema: str = "",
        allow_json: bool = False,
        return_canonical: bool = False,
        format: str = "toon",
    ) -> str:
        """Validate TOON syntax from inline content or a repo-relative file path.

        Args:
            content: Inline TOON content. Mutually exclusive with path.
            path: Repo-relative TOON file path. Mutually exclusive with content.
            require_format: Require the first non-empty line to be @format toon.
            schema: Optional schema: review-understanding | review-findings.
            allow_json: Accept JSON fallback when schema is provided.
            return_canonical: Include canonical_toon in the response when validation succeeds.
            format: auto | toon | json.
        """
        return _validate_toon_impl(
            _repo_root(),
            content=content,
            path=path,
            require_format=require_format,
            schema=schema,
            allow_json=allow_json,
            return_canonical=return_canonical,
            output_format=format,
        )

    @mcp.tool()
    def query_graph(text: str, relationship: str = "", limit: int = 20, detail: str = "compact", format: str = "toon", timeout_s: float = 60.0) -> str:
        """Search canonical semantic graph entities with bounded output."""
        return _query_graph_impl(_repo_root(), text, relationship, limit, detail, format, timeout_s)

    @mcp.tool()
    def get_graph_node(name: str, detail: str = "compact", format: str = "toon", timeout_s: float = 60.0) -> str:
        """Return a graph node and source evidence receipt."""
        return _get_graph_node_impl(_repo_root(), name, detail, format, timeout_s)

    @mcp.tool()
    def get_graph_neighbors(name: str, relationship: str = "", direction: str = "both", limit: int = 50, detail: str = "compact", format: str = "toon", timeout_s: float = 60.0) -> str:
        """Return bounded graph neighbours and edge evidence."""
        return _get_graph_neighbors_impl(_repo_root(), name, relationship, direction, limit, detail, format, timeout_s)

    @mcp.tool()
    def shortest_path(source: str, target: str, max_hops: int = 8, detail: str = "compact", format: str = "toon", timeout_s: float = 60.0) -> str:
        """Return a bounded shortest path between graph entities."""
        return _shortest_path_impl(_repo_root(), source, target, max_hops, detail, format, timeout_s)

    @mcp.tool()
    def explain_graph_edge(edge_key: str, detail: str = "compact", format: str = "toon", timeout_s: float = 60.0) -> str:
        """Return the source-line evidence for one graph edge."""
        return _explain_graph_edge_impl(_repo_root(), edge_key, detail, format, timeout_s)

    @mcp.tool()
    def get_stats() -> str:
        """Return token/saving stats for the latest context pack.

        Returns a markdown summary: packed tokens, raw tokens, saving %, selected files, task, generated_at.
        """
        return _get_stats_impl(_repo_root())

    @mcp.tool()
    def get_pr_context(
        pr: str = "",
        focus: str = "",
        format: str = "toon",
        allow_local_fallback: bool = False,
    ) -> str:
        """Return immutable PR evidence shared by local review entry points.

        GitHub PR refs are fetched and verified against GitHub's base/head SHAs.
        Local commits are used only when allow_local_fallback is explicitly true.
        """
        return _get_pr_context_impl(
            _repo_root(),
            pr=pr,
            focus=focus,
            output_format=format,
            allow_local_fallback=allow_local_fallback,
        )

    mcp.run()
