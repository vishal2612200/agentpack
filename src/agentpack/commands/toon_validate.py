from __future__ import annotations

import json
from pathlib import Path

import typer

from agentpack.commands._shared import console
from agentpack.core.toon_validator import validate_toon_file


def register(app: typer.Typer) -> None:
    @app.command("toon-validate")
    def toon_validate(
        path: Path = typer.Argument(..., help="TOON file to validate."),
        output_format: str = typer.Option("plain", "--format", help="Output format: plain|json."),
        allow_missing_format: bool = typer.Option(False, "--allow-missing-format", help="Allow files without @format toon."),
    ) -> None:
        """Validate TOON syntax for agent-facing artifacts."""
        if output_format not in {"plain", "json"}:
            console.print("[red]Invalid format. Use plain|json.[/]")
            raise typer.Exit(1)

        result = validate_toon_file(path, require_format=not allow_missing_format)
        if output_format == "json":
            typer.echo(json.dumps(result.as_dict(), indent=2))
        elif result.ok:
            root = f" root={result.root}" if result.root else ""
            console.print(f"[green]✓[/] valid TOON: {path}{root}")
            for warning in result.warnings:
                console.print(f"[yellow]warning:[/] {warning}")
        else:
            console.print(f"[red]invalid TOON:[/] {path}: {result.error}")
            for warning in result.warnings:
                console.print(f"[yellow]warning:[/] {warning}")

        if not result.ok:
            raise typer.Exit(1)
