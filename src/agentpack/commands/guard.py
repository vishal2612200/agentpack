from __future__ import annotations

import typer

from agentpack.commands._shared import console, _root, run_refresh
from agentpack.core.config import load_config
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.ignore import load_spec
from agentpack.core.modes import MODE_HELP, invalid_mode_message, is_requested_mode
from agentpack.core.scanner import scan
from agentpack.core.snapshot import build_snapshot
from agentpack.core.task_freshness import task_freshness
from agentpack.core.thread_context import resolve_thread_option, thread_paths
from agentpack.application.pack_service import AdapterRegistry
from agentpack.integrations.agents import (
    SUPPORTED_AGENTS,
    check_agent_integration,
    expand_agents,
    install_agent_integration,
)


def register(app: typer.Typer) -> None:
    @app.command()
    def guard(
        agent: str = typer.Option(
            "auto",
            "--agent",
            help=f"Agent integration to guard ({' | '.join(SUPPORTED_AGENTS)}).",
        ),
        repair_stale: bool = typer.Option(
            False,
            "--repair-stale",
            help="Repair stale/missing AgentPack rule and hook files before returning.",
        ),
        refresh_context: bool = typer.Option(
            False,
            "--refresh-context",
            help="Refresh the context pack when it is missing or stale.",
        ),
        mode: str = typer.Option("balanced", "--mode", help=f"Refresh mode ({MODE_HELP})."),
        budget: int = typer.Option(0, "--budget", help="Refresh token budget (0 = config default)."),
        thread: str = typer.Option("", "--thread", help="Use thread-scoped context state."),
    ) -> None:
        """Executable pre-edit gate for agents before they trust packed context."""
        if agent not in SUPPORTED_AGENTS:
            console.print(f"[yellow]Unknown agent: {agent}. Supported: {', '.join(SUPPORTED_AGENTS)}[/]")
            raise typer.Exit(1)
        if not is_requested_mode(mode):
            console.print(f"[red]{invalid_mode_message(mode)}[/]")
            raise typer.Exit(1)

        root = _root()
        resolved_thread_id = resolve_thread_option(thread)
        agents = expand_agents(agent, root)
        ok = True

        for selected in agents:
            checks = check_agent_integration(root, selected)
            failing = [check for check in checks if not check.ok]
            if failing and repair_stale and selected != "generic":
                _repair_agent(root, selected)
                checks = check_agent_integration(root, selected)
                failing = [check for check in checks if not check.ok]

            if failing:
                ok = False
                console.print(f"[yellow]Agent integration needs repair: {selected}[/]")
                for check in failing:
                    fix = f" Run: {check.fix}" if check.fix else ""
                    console.print(f"  [yellow]![/] {check.label}: {check.detail}.{fix}")
                    _print_action(
                        what_failed=f"{selected} {check.label}: {check.detail}",
                        why_it_matters="agent host may miss current AgentPack instructions, hooks, or MCP setup",
                        repair_command=check.fix or f"agentpack repair --agent {selected}",
                        safe_to_continue="no; repair before trusting AgentPack integration",
                    )
            else:
                console.print(f"[green]✓[/] Agent integration current: {selected}")

        context_ok, context_reason = _context_is_fresh(root, thread_id=resolved_thread_id)
        if not context_ok and refresh_context:
            selected_agent = agents[0] if agents else "generic"
            console.print(f"[yellow]Refreshing context: {context_reason}[/]")
            stats = run_refresh(root, selected_agent, mode, budget, thread_id=resolved_thread_id)
            if stats is None:
                ok = False
            else:
                context_ok, context_reason = _context_is_fresh(root, thread_id=resolved_thread_id)

        if context_ok:
            console.print("[green]✓[/] Context pack fresh")
        else:
            ok = False
            console.print(f"[yellow]Context pack unsafe: {context_reason}[/]")
            console.print("  Run: agentpack guard --repair-stale --refresh-context")
            _print_action(
                what_failed=context_reason,
                why_it_matters="selected files may describe old task text, old code, or a different repo snapshot",
                repair_command="agentpack guard --repair-stale --refresh-context",
                safe_to_continue="no; refresh or use direct rg/git evidence as source of truth",
            )

        if not ok:
            raise typer.Exit(1)


def _repair_agent(root, agent: str) -> None:
    from agentpack.commands.install import _install_slash_command

    console.print(f"[yellow]Repairing AgentPack integration: {agent}[/]")
    install_agent_integration(
        root,
        agent,
        global_install=False,
        install_slash_command=_install_slash_command,
    )


def _print_action(*, what_failed: str, why_it_matters: str, repair_command: str, safe_to_continue: str) -> None:
    console.print(f"    What failed: {what_failed}")
    console.print(f"    Why it matters: {why_it_matters}")
    console.print(f"    Repair command: {repair_command}")
    console.print(f"    Safe to continue: {safe_to_continue}")


def _context_is_fresh(root, thread_id: str | None = None) -> tuple[bool, str]:
    scoped = thread_paths(root, thread_id)
    meta = load_pack_metadata(root, scoped.metadata if scoped else None)
    if not meta:
        return False, "missing context pack metadata"

    if scoped:
        if scoped.task.exists():
            current_task = scoped.task.read_text(encoding="utf-8").strip()
            if current_task and current_task != meta.get("task"):
                return False, ".agentpack thread task differs from packed task"
    else:
        task_state = task_freshness(root, meta)
        if task_state.is_stale:
            return False, ".agentpack/task.md differs from packed task"

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
        return False, f"could not compute repo snapshot: {exc}"

    if current["root_hash"] != meta.get("snapshot_root_hash"):
        return False, "repo snapshot differs from packed snapshot"

    return True, "fresh"
