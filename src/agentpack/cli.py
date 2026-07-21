from __future__ import annotations

import typer
from agentpack.commands import (
    architecture,
    benchmark,
    claude_cmd,
    ci_cmd,
    compress_output,
    dashboard,
    dev_check,
    diagnose_selection,
    diff,
    doctor,
    eval_cmd,
    explain,
    guard,
    handoff,
    hook_cmd,
    ignore_cmd,
    init,
    install,
    learn,
    memory,
    mcp_cmd,
    migrate,
    monitor,
    next_cmd,
    pack,
    perf,
    quickstart,
    review_cmd,
    release_cmd,
    release_check,
    resolve_cmd,
    skill_review_cmd,
    retrieve,
    repair,
    route,
    scan,
    skills,
    state_cmd,
    start_cmd,
    stats,
    status,
    summarize,
    task_cmd,
    threads,
    toon_validate,
    tune,
    upgrade,
    verify_wheel,
    watch,
    wrap,
    workflow_cmd,
)
from agentpack import __version__


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


app = typer.Typer(help="AgentPack — token-aware context packing for AI coding agents.")

_CORE_HELP_CALLBACKS = {"work", "learn", "finish", "doctor"}
_SETUP_HELP_CALLBACKS = {
    "quickstart",
    "start",
    "next_action",
    "init",
    "route_task",
    "pack",
}
_REVIEW_HELP_CALLBACKS = {"guard", "review", "resolve", "skill_review_command"}
_HELP_PANEL_ORDER = {
    "Core loop": 0,
    "Setup and orientation": 1,
    "Review and safety": 2,
    "Advanced, diagnostics, and release": 3,
}


@app.callback()
def _main(
    version: bool = typer.Option(False, "--version", "-v", callback=_version_callback, is_eager=True, help="Show version and exit."),
) -> None:
    pass


for mod in [
    architecture,
    quickstart,
    start_cmd,
    next_cmd,
    doctor,
    init,
    route,
    pack,
    guard,
    handoff,
    ignore_cmd,
    scan,
    diff,
    status,
    state_cmd,
    task_cmd,
    threads,
    toon_validate,
    stats,
    dashboard,
    summarize,
    compress_output,
    learn,
    memory,
    perf,
    install,
    repair,
    migrate,
    monitor,
    explain,
    diagnose_selection,
    eval_cmd,
    tune,
    upgrade,
    watch,
    claude_cmd,
    benchmark,
    ci_cmd,
    dev_check,
    verify_wheel,
    mcp_cmd,
    hook_cmd,
    review_cmd,
    skills,
    release_check,
    release_cmd,
    resolve_cmd,
    skill_review_cmd,
    retrieve,
    wrap,
    workflow_cmd,
]:
    mod.register(app)


def _configure_help_panels() -> None:
    """Keep the default help focused while retaining every command path."""
    original_order = {id(command): index for index, command in enumerate(app.registered_commands)}
    for command in app.registered_commands:
        callback_name = getattr(command.callback, "__name__", "")
        if callback_name in _CORE_HELP_CALLBACKS:
            command.rich_help_panel = "Core loop"
        elif callback_name in _SETUP_HELP_CALLBACKS:
            command.rich_help_panel = "Setup and orientation"
        elif callback_name in _REVIEW_HELP_CALLBACKS:
            command.rich_help_panel = "Review and safety"
        else:
            command.rich_help_panel = "Advanced, diagnostics, and release"
    core_order = {"work": 0, "learn": 1, "finish": 2, "doctor": 3}
    app.registered_commands.sort(
        key=lambda command: (
            _HELP_PANEL_ORDER[str(command.rich_help_panel)],
            core_order.get(getattr(command.callback, "__name__", ""), original_order[id(command)]),
        )
    )
    for group in app.registered_groups:
        group.rich_help_panel = "Advanced, diagnostics, and release"


_configure_help_panels()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
