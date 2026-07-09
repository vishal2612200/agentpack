from __future__ import annotations

import typer
from agentpack.commands import (
    audit,
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


@app.callback()
def _main(
    version: bool = typer.Option(False, "--version", "-v", callback=_version_callback, is_eager=True, help="Show version and exit."),
) -> None:
    pass


for mod in [
    quickstart,
    start_cmd,
    next_cmd,
    doctor,
    init,
    route,
    pack,
    guard,
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
    audit,
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
    retrieve,
    wrap,
    workflow_cmd,
]:
    mod.register(app)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
