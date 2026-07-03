from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer

from agentpack.commands._shared import console, _root
from agentpack.core.command_surface import refresh_command_args
from agentpack.core.thread_context import resolve_session_thread_option, thread_paths
from agentpack.integrations.platform import cli_module_argv
from agentpack.session.state import TASK_FILE

task_app = typer.Typer(help="Show, set, or clear the current AgentPack task.")


def register(app: typer.Typer) -> None:
    app.add_typer(task_app, name="task")


@task_app.command("show")
def show_task(
    thread: str = typer.Option("", "--thread", help="Use thread-scoped task state (auto by default in agent sessions; use 'global' for legacy global state)."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    root = _root()
    path = _task_path(root, thread)
    value = _read_task(path)
    payload = {
        "task": value,
        "path": _rel(path, root),
        "thread_id": _thread_id(root, thread),
        "exists": path.exists(),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    if value:
        console.print(f"[bold]Task:[/] {value}")
    else:
        console.print("[yellow]No task set.[/]")
    console.print(f"[dim]{payload['path']}[/]")


@task_app.command("set")
def set_task(
    task_text: str = typer.Argument(..., help="Task text to write."),
    thread: str = typer.Option("", "--thread", help="Use thread-scoped task state (auto by default in agent sessions; use 'global' for legacy global state)."),
    pack: bool = typer.Option(False, "--pack", help="Run agentpack pack after writing the task."),
    guard: bool = typer.Option(False, "--guard", help="Run the installed refresh/repair command after writing."),
    agent: str = typer.Option("auto", "--agent", help="Agent to pass to pack/guard."),
    mode: str = typer.Option("balanced", "--mode", help="Pack/guard mode."),
) -> None:
    root = _root()
    path = _task_path(root, thread)
    task = task_text.strip()
    if not task:
        console.print("[red]Task text cannot be empty.[/]")
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(task + "\n", encoding="utf-8")
    console.print(f"[green]✓[/] Wrote {_rel(path, root)}")

    thread_id = _thread_id(root, thread)
    if guard:
        _run_cli(refresh_command_args(agent, mode), thread_id=thread_id)
    elif pack:
        _run_cli(["pack", "--agent", agent, "--task", "auto", "--mode", mode], thread_id=thread_id)


@task_app.command("clear")
def clear_task(
    thread: str = typer.Option("", "--thread", help="Use thread-scoped task state (auto by default in agent sessions; use 'global' for legacy global state)."),
) -> None:
    root = _root()
    path = _task_path(root, thread)
    if path.exists():
        path.unlink()
        console.print(f"[green]✓[/] Removed {_rel(path, root)}")
    else:
        console.print(f"[yellow]No task file at {_rel(path, root)}[/]")


def _task_path(root: Path, thread: str) -> Path:
    thread_id = resolve_session_thread_option(thread)
    scoped = thread_paths(root, thread_id)
    return scoped.task if scoped else root / TASK_FILE


def _thread_id(root: Path, thread: str) -> str | None:
    scoped = thread_paths(root, resolve_session_thread_option(thread))
    return scoped.thread_id if scoped else None


def _read_task(path: Path) -> str:
    if not path.exists():
        return ""
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return lines[0] if lines else ""


def _run_cli(args: list[str], *, thread_id: str | None = None) -> None:
    argv = cli_module_argv(*args)
    if thread_id:
        argv.extend(["--thread", thread_id])
    result = subprocess.run(argv, cwd=_root())
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)
