from __future__ import annotations

from pathlib import Path
from typing import Any

from agentpack.application.pack_service import AdapterRegistry
from agentpack.core.config import load_config
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.ignore import load_spec
from agentpack.core.scanner import scan
from agentpack.core.snapshot import build_snapshot
from agentpack.core.task_freshness import task_freshness
from agentpack.core.thread_context import (
    detect_conflicts,
    list_thread_rows,
    read_task_status,
    task_is_done,
    thread_paths,
)
from agentpack.core.token_contract import token_contract_from_metadata
from agentpack.core.loop_protocol import load_loop_state
from agentpack.router.skills_index import ensure_inventory_index
from agentpack.session.state import TASK_FILE

from agentpack.control_plane.models import (
    ContextSnapshot,
    ControlPlaneSnapshot,
    LoopSnapshot,
    SetupSnapshot,
    TaskSnapshot,
    ThreadSnapshot,
    TokenSnapshot,
)


_PLACEHOLDER_TASK = "Write or update the current coding task here."


def build_control_plane_snapshot(
    root: Path,
    *,
    thread_id: str | None = None,
    check_files: bool = False,
    check_skills: bool = False,
) -> ControlPlaneSnapshot:
    scoped = thread_paths(root, thread_id)
    setup = SetupSnapshot(
        initialized=(root / ".agentpack" / "config.toml").exists(),
        config_path=_rel(root / ".agentpack" / "config.toml", root),
    )
    task_path = scoped.task if scoped else root / TASK_FILE
    current_task = _read_task(task_path)
    status = read_task_status(root, scoped.thread_id if scoped else None)
    task = TaskSnapshot(
        thread_id=scoped.thread_id if scoped else None,
        task_path=_rel(task_path, root),
        has_task=bool(current_task),
        task=current_task,
        status=status,
        done=task_is_done(root, scoped.thread_id if scoped else None),
    )
    metadata_path = scoped.metadata if scoped else root / ".agentpack" / "pack_metadata.json"
    metadata = load_pack_metadata(root, metadata_path)
    context = _context_snapshot(
        root,
        task=task,
        scoped_thread_id=scoped.thread_id if scoped else None,
        metadata=metadata,
        metadata_path=metadata_path,
        check_files=check_files,
    )
    skill_index_error = _skill_index_error(root) if check_skills else ""
    return ControlPlaneSnapshot(
        root=str(root),
        setup=setup,
        task=task,
        context=context,
        threads=_thread_snapshot(root),
        tokens=_token_snapshot(metadata),
        loop=_loop_snapshot(root),
        skill_index_error=skill_index_error,
    )


def context_is_fresh(root: Path, *, thread_id: str | None = None) -> tuple[bool, str]:
    snapshot = build_control_plane_snapshot(root, thread_id=thread_id, check_files=True)
    return snapshot.context.status == "fresh", snapshot.context.reason


def _context_snapshot(
    root: Path,
    *,
    task: TaskSnapshot,
    scoped_thread_id: str | None,
    metadata: dict[str, Any] | None,
    metadata_path: Path,
    check_files: bool,
) -> ContextSnapshot:
    base = {
        "checked_files": check_files,
        "metadata_path": _rel(metadata_path, root),
    }
    if task.done:
        label = f"AgentPack session {scoped_thread_id}" if scoped_thread_id else ".agentpack task"
        return ContextSnapshot(status="stale", reason=f"{label} is marked done", **base)
    if scoped_thread_id and not task.has_task:
        return ContextSnapshot(status="stale", reason=f"missing task for AgentPack session {scoped_thread_id}", **base)
    if not metadata:
        return ContextSnapshot(status="missing", reason="missing context pack metadata", **base)

    freshness = metadata.get("freshness") if isinstance(metadata.get("freshness"), dict) else {}
    context_path = str(metadata.get("context_path") or "")
    common = {
        **base,
        "context_path": context_path,
        "generated_at": str(metadata.get("generated_at") or freshness.get("generated_at") or ""),
        "packed_task": str(metadata.get("task") or ""),
        "owner_thread_id": str(freshness.get("thread_id") or metadata.get("owner_thread_id") or ""),
    }
    if scoped_thread_id:
        owner = common["owner_thread_id"]
        if owner and owner != scoped_thread_id:
            return ContextSnapshot(
                status="stale",
                reason=f"context belongs to AgentPack session {owner}, not {scoped_thread_id}",
                **common,
            )
        if task.task and task.task != metadata.get("task"):
            return ContextSnapshot(status="stale", reason=".agentpack thread task differs from packed task", **common)
    else:
        task_state = task_freshness(root, metadata)
        if task_state.is_stale:
            return ContextSnapshot(status="stale", reason=".agentpack/task.md differs from packed task", **common)

    if check_files:
        try:
            cfg = load_config(root)
            ignore_spec = load_spec(root / cfg.project.ignore_file)
            scan_result = scan(
                root,
                ignore_spec,
                cfg.context.max_file_tokens,
                always_skip_paths=AdapterRegistry.generated_output_paths(root, cfg),
            )
            current = build_snapshot(scan_result.packable)
        except Exception as exc:
            return ContextSnapshot(status="stale", reason=f"could not compute repo snapshot: {exc}", **common)
        if current["root_hash"] != metadata.get("snapshot_root_hash"):
            return ContextSnapshot(status="stale", reason="repo snapshot differs from packed snapshot", **common)
        return ContextSnapshot(status="fresh", reason="fresh", **common)

    return ContextSnapshot(status="fresh", reason="fresh; file scan skipped", **common)


def _thread_snapshot(root: Path) -> ThreadSnapshot:
    rows = list_thread_rows(root, active_only=True)
    conflicts: list[dict[str, Any]] = []
    for row in rows:
        conflicts.extend(detect_conflicts(root, row).get("conflicts") or [])
    unique: dict[str, dict[str, Any]] = {}
    for conflict in conflicts:
        key = f"{conflict.get('thread_id')}:{','.join(conflict.get('overlap') or [])}"
        unique[key] = conflict
    return ThreadSnapshot(active_count=len(rows), conflict_count=len(unique), conflicts=list(unique.values())[:8])


def _token_snapshot(metadata: dict[str, Any] | None) -> TokenSnapshot:
    contract = token_contract_from_metadata(metadata) or {}
    return TokenSnapshot(
        budget=int(contract.get("budget") or 0),
        estimated_tokens=int(contract.get("estimated_tokens") or 0),
        usage_ratio=float(contract.get("usage_ratio") or 0.0),
        selected_count=int(contract.get("selected_count") or 0),
        mode_counts=contract.get("mode_counts") if isinstance(contract.get("mode_counts"), dict) else {},
        largest_sections=contract.get("largest_sections") if isinstance(contract.get("largest_sections"), list) else [],
        trimmed_sections=contract.get("trimmed_sections") if isinstance(contract.get("trimmed_sections"), dict) else {},
        recommended_next_context=str(contract.get("recommended_next_context") or ""),
    )


def _loop_snapshot(root: Path) -> LoopSnapshot:
    try:
        cfg = load_config(root)
    except Exception:
        return LoopSnapshot()
    if not cfg.loop.enabled:
        return LoopSnapshot(enabled=False)
    state = load_loop_state(root)
    if state is None:
        return LoopSnapshot(enabled=True)
    return LoopSnapshot(
        enabled=True,
        status=state.status,
        task=state.task,
        runner=state.runner or "",
        blocked_reason=state.blocked_reason or "",
    )


def _skill_index_error(root: Path) -> str:
    try:
        cfg = load_config(root)
        ensure_inventory_index(root, cfg.skills.paths)
    except Exception as exc:
        return str(exc)
    return ""


def _read_task(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return ""
    first = lines[0]
    return "" if _PLACEHOLDER_TASK in first else first


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
