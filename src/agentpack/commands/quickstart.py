from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from agentpack.commands._shared import console, _root
from agentpack.control_plane import build_control_plane_snapshot
from agentpack.core.command_surface import refresh_commands
from agentpack.core.modes import MODE_HELP, invalid_mode_message, is_requested_mode, normalize_mode
from agentpack.core.thread_context import resolve_session_thread_option, thread_paths
from agentpack.session.state import TASK_FILE


_PLACEHOLDER_TASK = "Write or update the current coding task here."


def register(app: typer.Typer) -> None:
    @app.command()
    def quickstart(
        task: str = typer.Option("", "--task", help="Optional task to show or write into the current AgentPack task file."),
        mode: str = typer.Option("balanced", "--mode", help=f"Suggested mode ({MODE_HELP})."),
        write: bool = typer.Option(False, "--write", help="Write --task into the current AgentPack task file."),
        thread: str = typer.Option("", "--thread", help="Use thread-scoped context state (auto by default in agent sessions; use 'global' for legacy global state)."),
    ) -> None:
        """Show one clear first-run path for this repo."""
        if not is_requested_mode(mode):
            console.print(f"[red]{invalid_mode_message(mode)}[/]")
            raise typer.Exit(1)
        mode = normalize_mode(mode)
        if write and not task.strip():
            console.print("[red]--write requires --task.[/]")
            raise typer.Exit(1)

        root = _root()
        thread_id = resolve_session_thread_option(thread)
        written = False
        if write:
            scoped = thread_paths(root, thread_id)
            task_path = scoped.task if scoped else root / TASK_FILE
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(task.strip() + "\n", encoding="utf-8")
            written = True

        state = _quickstart_state(root, task.strip(), mode, written=written, thread_id=thread_id)

        console.print("\n[bold]AgentPack quickstart[/]")
        console.print(state["summary"])
        console.print()

        table = Table(show_header=True)
        table.add_column("When", style="dim", width=12)
        table.add_column("Command")
        table.add_column("Why", style="dim")
        for label, cmd, why in state["steps"]:
            table.add_row(label, f"[bold]{cmd}[/]", why)
        console.print(table)

        if state["optional"]:
            console.print()
            console.print("[bold]Optional later[/]")
            for cmd, why in state["optional"]:
                console.print(f"  [bold]{cmd}[/] [dim]# {why}[/]")

        if state["notes"]:
            console.print()
            for note in state["notes"]:
                console.print(f"[dim]- {note}[/]")


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _task_status(root: Path, thread_id: str | None = None) -> tuple[bool, str]:
    scoped = thread_paths(root, thread_id)
    task_path = scoped.task if scoped else root / TASK_FILE
    if not task_path.exists():
        return False, ""
    text = task_path.read_text(encoding="utf-8").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return False, ""
    first = lines[0]
    if _PLACEHOLDER_TASK in first:
        return False, first
    return True, first


def _quickstart_state(
    root: Path,
    task: str,
    mode: str,
    *,
    written: bool = False,
    thread_id: str | None = None,
) -> dict[str, object]:
    snapshot = build_control_plane_snapshot(root, thread_id=thread_id, check_files=False)
    initialized = snapshot.setup.initialized
    has_task, current_task = _task_status(root, thread_id)
    has_context = snapshot.context.status == "fresh"
    thread_suffix = f" --thread {thread_id}" if thread_id else ""

    steps: list[tuple[str, str, str]] = []
    optional: list[tuple[str, str]] = []
    notes: list[str] = []

    if not initialized:
        steps.append(("first", f"agentpack init --yes --mode {mode}", "create config, cache dir, session, and task file"))
    else:
        notes.append(".agentpack/config.toml already exists.")

    if written:
        notes.append(f"Saved task: {task}")
        steps.append(("next", refresh_commands("auto").primary + thread_suffix, "refresh context for the saved task"))
    elif task:
        steps.append(("next", f"agentpack start {_shell_single_quote(task)}{thread_suffix}", "write task and refresh context in one command"))
    elif not has_task:
        steps.append(("next", f"agentpack start 'fix auth token expiry'{thread_suffix}", "replace example with one concrete task"))
    else:
        notes.append(f"Current task: {current_task}")
        steps.append(("next", f"agentpack next --fix{thread_suffix}", "check repo state and safely refresh stale context"))

    steps.append(("verify", "agentpack doctor --agent auto", "check installed CLI, MCP registration, hooks, and repo integration"))

    optional.append(("agentpack stats", "inspect packed tokens and selected-file precision"))
    optional.append(("agentpack watch", "auto-refresh while you edit"))
    optional.append(("agentpack benchmark --init", "measure selection quality on real tasks"))

    if has_context:
        notes.append("A context pack already exists; rerun pack after changing task text.")
    if thread_id:
        notes.append(f"Using AgentPack session: {thread_id}")
    if not task and not has_task:
        notes.append("Specific tasks beat vague ones: include subsystem, symptom, and file/module names when known.")

    summary = "One clear path: initialize, start one concrete task, then verify health."
    if initialized and (has_task or written):
        summary = "Repo has setup; refresh/verify before trusting packed context."

    return {"summary": summary, "steps": steps, "optional": optional, "notes": notes}
