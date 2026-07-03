from __future__ import annotations

import typer
import shutil

from agentpack.core.command_surface import refresh_commands
from agentpack.core.task_freshness import read_task_md
from agentpack.core.thread_context import resolve_session_thread_option
from agentpack.control_plane import build_control_plane_snapshot
from agentpack.control_plane.renderer import token_hint
from agentpack.commands._shared import console, _root
from agentpack.integrations.agents import check_agent_integration, resolve_agent


def _task_md_body(root) -> str:
    return read_task_md(root) or ""


def register(app: typer.Typer) -> None:
    @app.command()
    def status(
        deep: bool = typer.Option(False, "--deep", help="Also show CLI, repo, task, and agent integration health."),
        thread: str = typer.Option("", "--thread", help="Use thread-scoped context state (auto by default in agent sessions; use 'global' for legacy global state)."),
    ) -> None:
        """Check if the latest context pack is stale."""
        root = _root()
        resolved_thread_id = resolve_session_thread_option(thread)
        snapshot = build_control_plane_snapshot(root, thread_id=resolved_thread_id, check_files=deep)
        if snapshot.context.status == "missing":
            console.print("[yellow]No context pack found. Run agentpack pack to generate one.[/]")
            if deep:
                _print_deep_health(root, None)
            raise typer.Exit(1)

        if snapshot.context.status == "fresh":
            label = "up to date" if snapshot.context.checked_files else "usable; file scan skipped"
            console.print(f"[green]Context pack is {label}.[/]")
            console.print(f"  Task: {snapshot.context.packed_task}")
            console.print(f"  Generated: {snapshot.context.generated_at}")
            if snapshot.task.thread_id:
                console.print(f"  Thread: {snapshot.task.thread_id}")
            if snapshot.tokens.estimated_tokens:
                console.print(f"  Token contract: {token_hint(snapshot)}")
            if deep:
                _print_deep_health(root, {"task": snapshot.context.packed_task})
        else:
            reason = snapshot.context.reason
            if reason == ".agentpack/task.md differs from packed task":
                reason = ".agentpack/task.md changed since last pack"
            console.print(f"[yellow]Context pack is STALE.[/] {reason}.")
            if snapshot.context.packed_task and snapshot.task.task and snapshot.context.packed_task != snapshot.task.task:
                console.print(f"  Packed task: {snapshot.context.packed_task}")
                console.print(f"  Current task: {snapshot.task.task}")
            if "task" in snapshot.context.reason:
                console.print("  AgentPack MCP `get_context()` will auto-refresh this mismatch.")
            console.print(f"  Last generated: {snapshot.context.generated_at}")
            console.print(f"  Run [bold]{refresh_commands('auto').repair}[/] to refresh.")
            if deep:
                _print_deep_health(root, {"task": snapshot.context.packed_task})
            raise typer.Exit(1)


def _print_deep_health(root, meta: dict | None) -> None:
    console.print("\n[bold]Deep health[/]")
    binary = shutil.which("agentpack") or "(not on PATH)"
    console.print(f"  CLI: {binary}")
    console.print(f"  Repo: {root}")
    task = _task_md_body(root) or (meta or {}).get("task") or "(none)"
    console.print(f"  Task: {task}")
    try:
        agent = resolve_agent("auto", root)
    except Exception:
        agent = "generic"
    console.print(f"  Active agent: {agent}")
    failing = []
    for check in check_agent_integration(root, agent):
        marker = "[green]✓[/]" if check.ok else "[yellow]![/]"
        fix = f" — {check.fix}" if check.fix and not check.ok else ""
        console.print(f"  {marker} {check.label}: {check.detail}{fix}")
        if not check.ok:
            failing.append(check)
    if failing:
        console.print(f"  [yellow]![/] Repair: {refresh_commands(agent).repair}")
