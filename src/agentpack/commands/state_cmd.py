from __future__ import annotations

import json
from pathlib import Path

import typer

from agentpack.commands._shared import console, _root
from agentpack.core.execution_state import build_execution_state
from agentpack.core.thread_context import resolve_session_thread_option, thread_paths

VALID_STATUSES = {"planned", "in_progress", "blocked", "done"}

state_app = typer.Typer(help="Read and update AgentPack task execution state.")


def register(app: typer.Typer) -> None:
    app.add_typer(state_app, name="state")


@state_app.command("show")
def show_state(
    thread: str = typer.Option("", "--thread", help="Use thread-scoped task state (auto by default in agent sessions; use 'global' for legacy global state)."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    root = _root()
    scoped = thread_paths(root, resolve_session_thread_option(thread))
    state = build_execution_state(root, scoped)
    if json_output:
        typer.echo(json.dumps(state, indent=2, sort_keys=True))
        return
    task = state.get("task") or {}
    checklist = task.get("checklist") or {}
    console.print(f"[bold]Status:[/] {task.get('status') or 'unknown'}")
    if task.get("summary"):
        console.print(f"[bold]Summary:[/] {task['summary']}")
    if task.get("state_file"):
        console.print(f"[bold]State file:[/] {task['state_file']}")
    console.print(
        "[bold]Checklist:[/] "
        f"{checklist.get('done', 0)} done, {checklist.get('open', 0)} open, {checklist.get('blocked', 0)} blocked"
    )
    git = state.get("git") or {}
    console.print(f"[bold]Git:[/] {git.get('branch') or 'unknown'} @ {str(git.get('sha') or '')[:12] or 'unknown'}")
    runtime = state.get("runtime") or {}
    console.print(f"[bold]Runtime:[/] docker={runtime.get('docker')}, compose={runtime.get('compose_file') or 'none'}")


@state_app.command("set")
def set_state(
    status: str = typer.Argument(..., help="planned|in_progress|blocked|done"),
    summary: str = typer.Option("", "--summary", help="Task state summary."),
    thread: str = typer.Option("", "--thread", help="Use thread-scoped task state (auto by default in agent sessions; use 'global' for legacy global state)."),
) -> None:
    _write_state(status, summary=summary, thread=thread)


@state_app.command("done")
def done_state(
    summary: str = typer.Option("", "--summary", help="Completion summary."),
    thread: str = typer.Option("", "--thread", help="Use thread-scoped task state (auto by default in agent sessions; use 'global' for legacy global state)."),
) -> None:
    _write_state("done", summary=summary, thread=thread)


def _state_path(root: Path, thread: str) -> Path:
    scoped = thread_paths(root, resolve_session_thread_option(thread))
    return scoped.task_state if scoped else root / ".agentpack" / "task_state.md"


def _write_state(status: str, *, summary: str = "", thread: str = "") -> None:
    normalized = status.strip().lower()
    if normalized not in VALID_STATUSES:
        console.print(f"[red]Invalid status: {status}. Use planned|in_progress|blocked|done.[/]")
        raise typer.Exit(1)
    root = _root()
    path = _state_path(root, thread)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = _replace_state_lines(existing, normalized, summary)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    console.print(f"[green]✓[/] Wrote {path.relative_to(root)}")


def _replace_state_lines(existing: list[str], status: str, summary: str) -> list[str]:
    remaining = [
        line
        for line in existing
        if not line.lower().startswith("status:") and not line.lower().startswith("summary:")
    ]
    header = [f"Status: {status}", f"Summary: {summary.strip()}"]
    if remaining and remaining[0].strip():
        header.append("")
    return header + remaining
