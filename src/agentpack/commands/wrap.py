from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer

from agentpack.adapters.detect import detect_agent
from agentpack.application.pack_service import PackRequest, PackService
from agentpack.commands._shared import _root, console
from agentpack.core import git
from agentpack.core.task_freshness import write_task_md
from agentpack.session.events import record_event


_KNOWN_BINARIES = {
    "claude": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "windsurf": "windsurf",
}


def register(app: typer.Typer) -> None:
    @app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    def wrap(
        ctx: typer.Context,
        agent_name: str = typer.Argument("auto", help="Agent binary to launch: auto|claude|codex|cursor|windsurf."),
        task: str = typer.Option("", "--task", help="Task text to write before packing."),
        mode: str = typer.Option("balanced", "--mode", help="Pack mode."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print launch command without executing it."),
        check_setup: bool = typer.Option(True, "--check-setup/--no-check-setup", help="Warn when local agent setup files are missing."),
        print_env: bool = typer.Option(False, "--print-env", help="Print context environment variables passed to the agent."),
    ) -> None:
        """Pack fresh context, then launch a coding agent."""
        root = _root()
        resolved_agent = detect_agent(root) if agent_name == "auto" else agent_name
        if task.strip():
            write_task_md(root, task.strip())
        resolved_task = task.strip() or _resolve_task(root)
        result = PackService().run(PackRequest(
            root=root,
            agent=resolved_agent,
            task=resolved_task,
            mode=mode,
            budget=0,
            since=None,
            refresh=False,
        ))
        binary = _KNOWN_BINARIES.get(resolved_agent, resolved_agent)
        executable = shutil.which(binary)
        command = [executable or binary, *ctx.args]
        launch_env = _agent_env(root, result.out_path, resolved_task)
        record_event(
            root,
            "wrap",
            {"agent": resolved_agent, "task": resolved_task, "context_path": str(result.out_path.relative_to(root))},
        )
        console.print(f"[green]Context ready:[/] {result.out_path.relative_to(root)}")
        if check_setup:
            for warning in _setup_warnings(root, resolved_agent):
                console.print(f"[yellow]![/] {warning}")
        if print_env or dry_run:
            for key, value in launch_env.items():
                console.print(f"{key}={value}")
        console.print("Launch command: " + " ".join(command))
        if dry_run:
            return
        if executable is None:
            console.print(f"[red]Agent binary not found on PATH:[/] {binary}")
            raise typer.Exit(1)
        record_event(
            root,
            "agent_session_started",
            {
                "agent": resolved_agent,
                "task": resolved_task,
                "context_path": str(result.out_path.relative_to(root)),
            },
            source="agent-launch",
        )
        env = {**os.environ, **launch_env}
        raise typer.Exit(subprocess.call(command, cwd=root, env=env))


def _resolve_task(root: Path) -> str:
    task_path = root / ".agentpack" / "task.md"
    if task_path.exists():
        task = task_path.read_text(encoding="utf-8").strip()
        if task:
            return task
    if git.is_git_repo(root):
        inferred, _source = git.infer_task_with_source(root)
        return inferred
    return "general"


def _agent_env(root: Path, context_path: Path, task: str) -> dict[str, str]:
    return {
        "AGENTPACK_ROOT": str(root),
        "AGENTPACK_CONTEXT": str(context_path),
        "AGENTPACK_TASK": task,
    }


def _setup_warnings(root: Path, agent: str) -> list[str]:
    checks = {
        "claude": [".mcp.json", ".claude/settings.json"],
        "codex": ["AGENTS.md", ".codex/config.toml"],
        "cursor": [".cursor/rules/agentpack.mdc"],
        "windsurf": [".windsurfrules"],
    }
    expected = checks.get(agent)
    if not expected:
        return []
    if any((root / path).exists() for path in expected):
        return []
    joined = " or ".join(expected)
    return [f"No {agent} setup file found ({joined}). Run `agentpack install --agent {agent}` if context hooks are not configured."]
