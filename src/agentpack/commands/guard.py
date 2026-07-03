from __future__ import annotations

import typer

from agentpack.commands._shared import console, _root, run_refresh
from agentpack.core.modes import MODE_HELP, invalid_mode_message, is_requested_mode
from agentpack.core.thread_context import resolve_session_thread_option
from agentpack.control_plane.snapshot import context_is_fresh
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
        thread: str = typer.Option("", "--thread", help="Use thread-scoped context state (auto by default in agent sessions; use 'global' for legacy global state)."),
    ) -> None:
        """Executable pre-edit gate for agents before they trust packed context."""
        if agent not in SUPPORTED_AGENTS:
            console.print(f"[yellow]Unknown agent: {agent}. Supported: {', '.join(SUPPORTED_AGENTS)}[/]")
            raise typer.Exit(1)
        if not is_requested_mode(mode):
            console.print(f"[red]{invalid_mode_message(mode)}[/]")
            raise typer.Exit(1)

        root = _root()
        resolved_thread_id = resolve_session_thread_option(thread)
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
        if not context_ok and refresh_context and not _missing_session_task(context_reason):
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
            repair_command = _guard_repair_command(resolved_thread_id)
            console.print(f"  Run: {repair_command}")
            _print_action(
                what_failed=context_reason,
                why_it_matters="selected files may describe old task text, old code, or a different repo snapshot",
                repair_command=repair_command,
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
    return context_is_fresh(root, thread_id=thread_id)


def _missing_session_task(reason: str) -> bool:
    return reason.startswith("missing task for AgentPack session")


def _guard_repair_command(thread_id: str | None) -> str:
    command = "agentpack guard --repair-stale --refresh-context"
    if thread_id:
        command += f" --thread {thread_id}"
    return command
