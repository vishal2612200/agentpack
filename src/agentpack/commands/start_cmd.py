from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from agentpack.commands._shared import console, _root
from agentpack.core.command_surface import refresh_command_args
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core import git
from agentpack.core.project_index import register_project
from agentpack.core.thread_context import resolve_session_thread_option, thread_paths
from agentpack.integrations.platform import cli_module_argv
from agentpack.learning.task_memory import record_task_start_snapshot
from agentpack.session.state import TASK_FILE


def register(app: typer.Typer) -> None:
    @app.command("start")
    def start(
        task_text: str = typer.Argument(..., help="Task text to write before refreshing context."),
        pack_only: bool = typer.Option(False, "--pack-only", help="Run pack directly instead of guard."),
        thread: str = typer.Option("", "--thread", help="Use thread-scoped task/context state (auto by default in agent sessions; use 'global' for legacy global state)."),
        agent: str = typer.Option("auto", "--agent", help="Agent to pass to pack/guard."),
        mode: str = typer.Option("balanced", "--mode", help="Pack/guard mode."),
        budget: int = typer.Option(0, "--budget", help="Token budget (0 = config default)."),
        workspace: str = typer.Option("", "--workspace", help="Restrict pack to a monorepo workspace."),
    ) -> None:
        """Write a task and immediately prepare usable context."""
        task = task_text.strip()
        if not task:
            console.print("[red]Task text cannot be empty.[/]")
            raise typer.Exit(1)
        root = _root()
        try:
            register_project(root)
        except OSError:
            pass
        thread_id = resolve_session_thread_option(thread)
        dirty_files_before = sorted(git.dirty_files(root)) if git.is_git_repo(root) else []
        task_path = _task_path(root, thread_id)
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(task + "\n", encoding="utf-8")
        console.print(f"[green]✓[/] Wrote {_rel(task_path, root)}")

        if pack_only or workspace:
            argv = cli_module_argv("pack", "--agent", agent, "--task", "auto", "--mode", mode)
            if budget:
                argv.extend(["--budget", str(budget)])
            if workspace:
                argv.extend(["--workspace", workspace])
        else:
            argv = cli_module_argv(*refresh_command_args(agent, mode, budget))
        if thread_id:
            argv.extend(["--thread", thread_id])
        result = subprocess.run(argv, cwd=root)
        if result.returncode != 0:
            raise typer.Exit(result.returncode)
        context_path = _context_path(root, thread_id, agent)
        try:
            record_task_start_snapshot(
                root,
                task=task,
                thread=thread_id or "",
                agent=agent,
                context_path=context_path,
                dirty_files_before=dirty_files_before,
            )
        except Exception:
            pass
        console.print(f"[green]✓[/] Context ready: [bold]{_rel(context_path, root)}[/]")


def _task_path(root: Path, thread_id: str | None) -> Path:
    scoped = thread_paths(root, thread_id)
    return scoped.task if scoped else root / TASK_FILE


def _context_path(root: Path, thread_id: str | None, agent: str) -> Path:
    scoped = thread_paths(root, thread_id)
    meta = load_pack_metadata(root, scoped.metadata if scoped else None)
    if meta and meta.get("context_path"):
        return root / str(meta["context_path"])
    if scoped:
        return scoped.context_claude if agent in {"auto", "claude"} else scoped.context
    return root / (".agentpack/context.claude.md" if agent == "claude" else ".agentpack/context.md")


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)
