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
    get_delta_context   — return selected-file delta since the previous pack
    validate_toon       — validate TOON syntax from content or a repo-relative path
    get_stats           — token/saving stats for the latest pack
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from agentpack import __version__
from agentpack.core import git
from agentpack.core.command_surface import available_cli_commands, fallback_agent_guidance, refresh_commands
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.structured_format import StructuredFormat, to_llm
from agentpack.core.task_freshness import read_task_md, task_freshness, write_task_md
from agentpack.core.thread_context import resolve_session_thread_option, task_is_done, thread_paths
from agentpack.core.token_estimator import estimate_tokens
from agentpack.control_plane import build_control_plane_snapshot, plan_next_actions
from agentpack.control_plane.renderer import token_hint


def _repo_root() -> Path:
    """Walk up from cwd until .agentpack/ found; fall back to cwd."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".agentpack").exists():
            return parent
    return cwd


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
    "get_delta_context",
    "get_task_map",
    "retrieve_context",
    "compress_output",
    "validate_toon",
    "get_stats",
)


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
        "cli_commands": list(available_cli_commands()),
        "refresh_command": refresh_commands("auto").primary,
        "recommended_next_tool": recommended_next_tool,
        "reason": recommendations[0].reason if recommendations else "context is ready for the current task",
        "avoid": _mcp_avoid_list(snapshot, [item.kind for item in recommendations]),
        "token_hint": token_hint(snapshot),
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


def _truncate_to_budget(text: str, max_tokens: int = 20000) -> str:
    """Truncate packed context to fit within max_tokens (estimated via tiktoken, falls back to len//4)."""
    if estimate_tokens(text) <= max_tokens:
        return text

    split_marker = "\n## File Context"
    marker_pos = text.find(split_marker)
    if marker_pos == -1:
        budget_chars = max_tokens * 4
        truncated = text[:budget_chars]
        omit_files = max(1, (len(text) - budget_chars) // 2000)
        return truncated + f"\n\n> [Truncated: {omit_files} files omitted to fit context window. Use get_context() to read full pack or narrow the task.]"

    header = text[:marker_pos]
    file_section = text[marker_pos:]

    if estimate_tokens(header) >= max_tokens:
        return header + "\n\n> [Truncated: file context omitted to fit context window. Use get_context() to read full pack or narrow the task.]"

    blocks = file_section.split("\n### ")
    # blocks[0] is the "## File Context" heading; blocks[1:] are individual files
    accumulated = blocks[0]
    total_files = len(blocks) - 1
    kept_files = 0
    for block in blocks[1:]:
        candidate = accumulated + "\n### " + block
        if estimate_tokens(header + candidate) > max_tokens:
            break
        accumulated = candidate
        kept_files += 1

    omitted = total_files - kept_files
    if omitted > 0:
        return header + accumulated + f"\n\n> [Truncated: {omitted} files omitted to fit context window. Use get_context() to read full pack or narrow the task.]"
    return header + accumulated


def _get_context_impl(root: Path, thread_id: str | None = None) -> str:
    """Read the latest context pack, blocking to refresh when task.md changed."""
    scoped = thread_paths(root, thread_id)
    if task_is_done(root, scoped.thread_id if scoped else None):
        label = f"AgentPack session {scoped.thread_id}" if scoped else "AgentPack task"
        return (
            f"> {label} is marked done. Completed context will not be reused.\n\n"
            "Start a new task/session with `agentpack start \"describe the task\"` or MCP `start_task(...)`."
        )
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
                refreshed = _pack_context_impl(root, task="", max_tokens=20000, thread_id=scoped.thread_id)
            else:
                refreshed = _pack_context_impl(root, task="", max_tokens=20000)
            return f"> Context auto-refreshed because {auto_refresh_reason}.\n\n{refreshed}"
        except Exception as exc:
            content = pack_path.read_text(encoding="utf-8")
            return _stale_context_notice(root, metadata, auto_refresh_reason, refresh_failed=str(exc)) + content

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

    return header + content


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
) -> str:
    """Write task.md when task is provided, pack context, and return markdown."""
    from agentpack.application.pack_service import PackService, PackRequest
    from agentpack.adapters.detect import detect_agent
    from agentpack.renderers.markdown import render_claude
    from agentpack.learning.task_memory import record_task_start_snapshot

    provided_task = bool(task.strip())
    had_task_md = _task_md_body(root, thread_id or None) is not None
    dirty_files_before = sorted(git.dirty_files(root)) if provided_task and git.is_git_repo(root) else []
    resolved_task = _resolve_mcp_task(root, task, thread_id or None)
    agent = detect_agent(root)
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
    ))
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


def _explain_file_impl(root: Path, path: str, task: str = "", thread_id: str | None = None) -> str:
    """Testable core of the explain_file MCP tool."""
    from agentpack.application.pack_service import PackPlanner, PackRequest, _sf_tokens
    from agentpack.adapters.detect import detect_agent

    resolved_task = task
    if not resolved_task:
        resolved_task = _task_md_body(root, thread_id) or "general"

    plan = PackPlanner().plan(PackRequest(
        root=root,
        agent=detect_agent(root),
        task=resolved_task,
        mode="balanced",
        budget=0,
        since=None,
        refresh=False,
    ))

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


def _get_related_files_impl(root: Path, path: str, depth: int = 1, thread_id: str | None = None) -> str:
    """Testable core of the get_related_files MCP tool."""
    from agentpack.application.pack_service import PackPlanner, PackRequest
    from agentpack.adapters.detect import detect_agent

    depth = max(1, min(depth, 2))
    task = _task_md_body(root, thread_id) or "general"

    plan = PackPlanner().plan(PackRequest(
        root=root,
        agent=detect_agent(root),
        task=task,
        mode="balanced",
        budget=0,
        since=None,
        refresh=False,
    ))

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


def _get_stats_impl(root: Path) -> str:
    """Testable core of the get_stats MCP tool."""
    metadata_path = root / ".agentpack" / "pack_metadata.json"

    if not metadata_path.exists():
        return "No pack metadata found. Run pack_context() first."

    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Failed to read pack metadata: {exc}"

    lines = [
        "## AgentPack Stats",
        "",
        f"- **task**: {meta.get('task', 'unknown')}",
        f"- **generated_at**: {meta.get('generated_at', 'unknown')}",
        f"- **mode**: {meta.get('mode', 'unknown')}",
        f"- **budget**: {meta.get('budget', 0):,} tokens",
        f"- **packed_tokens**: {meta.get('token_estimate', 0):,}",
        f"- **agent**: {meta.get('agent', 'unknown')}",
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
) -> str:
    from agentpack.core.config import load_config
    from agentpack.core.pack_registry import retrieve_from_registry
    from agentpack.session.events import record_event

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
    return result


def _compress_output_impl(root: Path, content: str, kind: str = "auto") -> str:
    from agentpack.core.config import load_config
    from agentpack.output_compression import compress_output
    from agentpack.session.events import record_event

    cfg = load_config(root)
    result = compress_output(content, kind=kind, max_items=cfg.runtime.max_output_summary_items)
    record_event(
        root,
        "compress_output",
        {"kind": kind, "input_chars": len(content), "output_chars": len(result)},
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
) -> str:
    """Return a compact read-only route payload; full details remain opt-in."""
    from agentpack.router.service import RouteService

    result = RouteService().route_task(root, task)
    if detail == "full":
        payload = result.model_dump(mode="json")
    elif detail == "compact":
        payload = _compact_route_payload(result)
    else:
        raise ValueError("detail must be 'compact' or 'full'")
    return to_llm(root, payload, requested=output_format, root_name="agentpack_route")


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


def _get_skills_impl(root: Path, output_format: StructuredFormat = "toon") -> str:
    """Return discovered skill/rule inventory payload."""
    from agentpack.router.service import RouteService

    inventory = RouteService().inventory(root)
    return to_llm(root, inventory.model_dump(mode="json"), requested=output_format, root_name="agentpack_skills")


def _get_skill_impl(root: Path, name_or_path: str) -> str:
    """Return one skill's raw SKILL.md content by name or path."""
    from agentpack.router.service import RouteService

    return RouteService().get_skill(root, name_or_path)


def _explain_route_impl(root: Path, task: str, output_format: StructuredFormat = "toon") -> str:
    """Return task route payload including all positive skill scores."""
    from agentpack.router.service import RouteService

    result = RouteService().explain_route(root, task)
    return to_llm(
        root,
        result.model_dump(mode="json"),
        requested=output_format,
        root_name="agentpack_route_explanation",
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
    def start_task(task: str, mode: str = "balanced", budget: int = 0, max_tokens: int = 20000, thread_id: str = "") -> str:
        """Start a new coding task: write session task.md, pack context, and return it.

        This is the recommended MCP-first entry point at the start of a task.
        """
        return _pack_context_impl(
            _repo_root(),
            task=task,
            mode=mode,
            budget=budget,
            max_tokens=max_tokens,
            thread_id=resolve_session_thread_option(thread_id) or "",
        )

    @mcp.tool()
    def pack_context(task: str = "", mode: str = "balanced", budget: int = 0, max_tokens: int = 20000, thread_id: str = "") -> str:
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
        )

    @mcp.tool()
    def route_task(task: str, format: str = "toon", detail: str = "compact") -> str:
        """Route a task to files, rules, skills, command suggestions, and safety warnings.

        Read-only: does not write task.md or context files. The default compact response
        contains top files, reasons, actions, and warnings. Use detail='full' or
        explain_route when full routing evidence is needed.
        """
        return _route_task_impl(_repo_root(), task, format, detail)

    @mcp.tool()
    def get_skills(format: str = "toon") -> str:
        """Return the discovered Agentpack skill/rule inventory as TOON or JSON."""
        return _get_skills_impl(_repo_root(), format)

    @mcp.tool()
    def get_skill(name_or_path: str) -> str:
        """Return one AgentPack skill by name or path.

        Use after route_task/explain_route recommends a skill and before applying it.
        """
        return _get_skill_impl(_repo_root(), name_or_path)

    @mcp.tool()
    def explain_route(task: str, format: str = "toon") -> str:
        """Return a route_task-style payload with skill scoring reasons."""
        return _explain_route_impl(_repo_root(), task, format)

    @mcp.tool()
    def get_context(thread_id: str = "") -> str:
        """Return the latest session context pack, auto-refreshing when task.md changed.

        Fast for fresh packs. Blocks for one refresh if the current task differs from the packed task.
        Returns empty string if no pack exists yet.
        """
        return _get_context_impl(_repo_root(), resolve_session_thread_option(thread_id))

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
    def explain_file(path: str, task: str = "", thread_id: str = "") -> str:
        """Return score breakdown and symbol list for a specific file.

        Args:
            path: Repo-relative file path (e.g. "src/auth/session.py").
            task: Optional task description to score against. Defaults to current session task.md.

        Returns a markdown string with score signals, include mode, token count, and symbols.
        """
        return _explain_file_impl(_repo_root(), path, task, resolve_session_thread_option(thread_id))

    @mcp.tool()
    def get_related_files(path: str, depth: int = 1, thread_id: str = "") -> str:
        """Return import-graph neighbours of a file (files it imports + files that import it).

        Args:
            path: Repo-relative file path (e.g. "src/auth/session.py").
            depth: Graph traversal depth (1 = direct neighbours, 2 = two hops). Max 2.

        Returns a markdown list of related files with their relationship type.
        """
        return _get_related_files_impl(_repo_root(), path, depth, resolve_session_thread_option(thread_id))

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
    ) -> str:
        """Retrieve full or stored content for a selected/omitted pack registry record.

        Args:
            path: Repo-relative path to retrieve.
            block_id: Stable block id from the pack registry. Optional if path is set.
            mode: as_stored | full | skeleton | symbols | summary.
            allow_stale: If false, refuse retrieval when file changed since the latest pack.
            targets: Optional list of repo-relative paths to retrieve in one call.
            kind: any | selected | omitted.
        """
        return _retrieve_context_impl(
            _repo_root(),
            path=path,
            block_id=block_id,
            mode=mode,
            allow_stale=allow_stale,
            targets=targets,
            kind=kind,
        )

    @mcp.tool()
    def compress_output(content: str, kind: str = "auto") -> str:
        """Summarize noisy command output while preserving errors, failures, paths, and diffs."""
        return _compress_output_impl(_repo_root(), content=content, kind=kind)

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
    def get_stats() -> str:
        """Return token/saving stats for the latest context pack.

        Returns a markdown summary: packed tokens, raw tokens, saving %, selected files, task, generated_at.
        """
        return _get_stats_impl(_repo_root())

    mcp.run()
