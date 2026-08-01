from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import typer
from rich.table import Table

from agentpack.architecture.index import SemanticGraphIndex
from agentpack.architecture.budgets import snapshot_metrics
from agentpack.architecture.service import build_diff, build_snapshot_for_ref, run_check
from agentpack.commands._shared import _root, console

architecture_app = typer.Typer(help="Build deterministic architecture snapshots, diffs, and checks.")


def register(app: typer.Typer) -> None:
    app.add_typer(architecture_app, name="architecture")


@architecture_app.command("snapshot")
def snapshot(
    ref: str = typer.Option("", "--ref", help="Git ref or SHA to snapshot. Omit to snapshot the current worktree."),
    cold: bool = typer.Option(False, "--cold", help="Ignore the materialized graph and force a cold build."),
    verify_incremental: bool = typer.Option(False, "--verify-incremental", help="Compare the incremental result with a cold rebuild."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    root = _root()
    result = build_snapshot_for_ref(root, ref or None, cold=cold, verify_incremental=verify_incremental)
    if json_output:
        payload = result.model_dump(mode="json")
        payload["build_stats"] = result.cache_stats
        payload["metrics"] = snapshot_metrics(result)
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    table = Table(title="Architecture Snapshot", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Ref", result.ref)
    table.add_row("Commit", result.commit_sha)
    table.add_row("Entities", str(len(result.entities)))
    table.add_row("Edges", str(len(result.edges)))
    table.add_row("Profile", result.extractor_profile_hash)
    if result.cache_stats:
        table.add_row("Build", str(result.cache_stats.get("build_mode", "unknown")))
        table.add_row("Parsed / reused", f"{result.cache_stats.get('parsed_files', 0)} / {result.cache_stats.get('reused_records', result.cache_stats.get('reused_files', 0))}")
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
    output_format: Literal["table", "json"] = typer.Option("table", "--format", help="Output format."),
) -> None:
    root = _root()
    result = run_check(root, base, head)
    if json_output or output_format == "json":
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


@architecture_app.command("baseline")
def baseline(
    ref: str = typer.Option("main", "--ref", help="Accepted git ref or SHA."),
    output: Path = typer.Option(Path(".agentpack/architecture-baseline.json"), "--output"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Record source-free architecture quality metrics for an accepted ref."""
    root = _root()
    snapshot = build_snapshot_for_ref(root, ref)
    payload = {
        "schema_version": 1,
        "ref": snapshot.ref,
        "commit_sha": snapshot.commit_sha,
        "metrics": snapshot_metrics(snapshot),
    }
    destination = output if output.is_absolute() else root / output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        console.print(f"[green]✓[/] Architecture baseline written: {destination}")


@architecture_app.command("query")
def query(
    text: str = typer.Argument(..., help="Entity, path, or relationship question."),
    ref: str = typer.Option("", "--ref", help="Git ref or SHA. Omit for the current worktree."),
    entity_type: str = typer.Option("", "--type", help="Limit results to an entity type."),
    limit: int = typer.Option(20, "--limit", min=1, max=200),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    index = SemanticGraphIndex(build_snapshot_for_ref(_root(), ref or None))
    rows = [{"score": hit.score, "entity": hit.entity.model_dump(mode="json")} for hit in index.query(text, limit=limit, entity_type=entity_type)]
    if json_output:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    for row in rows:
        entity = row["entity"]
        console.print(f"{entity['entity_type']} {entity['qualified_name']} ({entity['locator']['path']})")
    if not rows:
        console.print("No graph entities matched.")


@architecture_app.command("path")
def path(
    source: str = typer.Argument(..., help="Source entity name or path."),
    target: str = typer.Argument(..., help="Target entity name or path."),
    ref: str = typer.Option("", "--ref"),
    max_hops: int = typer.Option(8, "--max-hops", min=1, max=32),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    index = SemanticGraphIndex(build_snapshot_for_ref(_root(), ref or None))
    result = index.shortest_path(source, target, max_hops=max_hops)
    if json_output:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    for row in result:
        console.print(f"{row['relationship']} -> {row['node']['qualified_name']}")
    if not result:
        console.print("No graph path found.")


@architecture_app.command("explain")
def explain(
    name: str = typer.Argument(..., help="Entity name/path or edge key."),
    ref: str = typer.Option("", "--ref"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    index = SemanticGraphIndex(build_snapshot_for_ref(_root(), ref or None))
    result = index.explain_edge(name)
    if result is None:
        entities = [entity.model_dump(mode="json") for entity in index.resolve(name)]
        result = {"entities": entities, "neighbors": index.neighbors(name, limit=20)}
    if json_output:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    console.print_json(json.dumps(result, sort_keys=True))


@architecture_app.command("artifacts")
def artifacts(
    diff: Path = typer.Option(..., "--diff", exists=True, readable=True, help="Raw architecture diff JSON."),
    check: Path = typer.Option(..., "--check", exists=True, readable=True, help="Raw architecture check JSON."),
    output_dir: Path = typer.Option(Path(".agentpack/artifacts"), "--output-dir", help="Directory for sanitized artifacts."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Write source-free architecture CI artifacts and a receipt."""
    from agentpack.architecture.ci import write_ci_artifacts

    root = _root()
    result = write_ci_artifacts(
        diff_path=diff if diff.is_absolute() else root / diff,
        check_path=check if check.is_absolute() else root / check,
        output_dir=output_dir if output_dir.is_absolute() else root / output_dir,
    )
    if json_output:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    for label, path in result.items():
        console.print(f"[green]✓[/] {label}: {path}")
