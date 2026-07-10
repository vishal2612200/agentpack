from __future__ import annotations

import json

import typer
from rich.table import Table

from agentpack.architecture.service import build_diff, build_snapshot_for_ref, run_check
from agentpack.commands._shared import _root, console

architecture_app = typer.Typer(help="Build deterministic architecture snapshots, diffs, and checks.")


def register(app: typer.Typer) -> None:
    app.add_typer(architecture_app, name="architecture")


@architecture_app.command("snapshot")
def snapshot(
    ref: str = typer.Option("", "--ref", help="Git ref or SHA to snapshot. Omit to snapshot the current worktree."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    root = _root()
    result = build_snapshot_for_ref(root, ref or None)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    table = Table(title="Architecture Snapshot", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Ref", result.ref)
    table.add_row("Commit", result.commit_sha)
    table.add_row("Entities", str(len(result.entities)))
    table.add_row("Edges", str(len(result.edges)))
    table.add_row("Profile", result.extractor_profile_hash)
    console.print(table)


@architecture_app.command("diff")
def diff(
    base: str = typer.Option(..., "--base", help="Base git ref or SHA."),
    head: str = typer.Option(..., "--head", help="Head git ref or SHA."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    root = _root()
    result = build_diff(root, base, head)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    table = Table(title="Architecture Diff", show_header=True)
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("Added entities", str(len(result.added_entities)))
    table.add_row("Removed entities", str(len(result.removed_entities)))
    table.add_row("Changed entities", str(len(result.changed_entities)))
    table.add_row("Added edges", str(len(result.added_edges)))
    table.add_row("Removed edges", str(len(result.removed_edges)))
    table.add_row("Changed edges", str(len(result.changed_edges)))
    console.print(table)


@architecture_app.command("check")
def check(
    base: str = typer.Option(..., "--base", help="Base git ref or SHA."),
    head: str = typer.Option(..., "--head", help="Head git ref or SHA."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    root = _root()
    result = run_check(root, base, head)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        table = Table(title="Architecture Check", show_header=True)
        table.add_column("Category", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Violations", str(len(result.violations)))
        table.add_row("Blocking", str(sum(1 for violation in result.violations if violation.blocking)))
        table.add_row("Warnings", str(len(result.warnings)))
        console.print(table)
        for violation in result.violations:
            style = "red" if violation.blocking else "yellow"
            console.print(f"[{style}]{violation.invariant_id}[/{style}] {violation.message}")
        for warning in result.warnings:
            console.print(f"[yellow]{warning}[/]")
    if any(violation.blocking for violation in result.violations):
        raise typer.Exit(1)
