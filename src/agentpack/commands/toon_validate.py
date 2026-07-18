from __future__ import annotations

import json
from pathlib import Path

import typer

from agentpack.commands._shared import console
from agentpack.core.toon_validator import REVIEW_TOON_SCHEMAS, canonicalize_to_toon_text, validate_toon_file


def register(app: typer.Typer) -> None:
    @app.command("toon-validate")
    def toon_validate(
        path: Path = typer.Argument(..., help="TOON file to validate."),
        output_format: str = typer.Option("plain", "--format", help="Output format: plain|json."),
        allow_missing_format: bool = typer.Option(False, "--allow-missing-format", help="Allow files without @format toon."),
        schema: str = typer.Option(
            "",
            "--schema",
            help="Optional schema: review-understanding|review-findings.",
        ),
        allow_json: bool = typer.Option(
            False,
            "--allow-json",
            help="Accept JSON that matches the selected schema and can be canonicalized to TOON.",
        ),
        write_canonical: bool = typer.Option(
            False,
            "--write-canonical",
            help="Rewrite valid JSON/fenced/missing-format input as canonical TOON.",
        ),
    ) -> None:
        """Validate TOON syntax for agent-facing artifacts."""
        if output_format not in {"plain", "json"}:
            console.print("[red]Invalid format. Use plain|json.[/]")
            raise typer.Exit(1)
        if schema and schema not in REVIEW_TOON_SCHEMAS:
            console.print("[red]Invalid schema. Use review-understanding|review-findings.[/]")
            raise typer.Exit(1)
        if allow_json and not schema:
            console.print("[red]--allow-json requires --schema so JSON can be checked before canonicalization.[/]")
            raise typer.Exit(1)

        result = validate_toon_file(path, require_format=not allow_missing_format, schema=schema, allow_json=allow_json)
        if result.ok and write_canonical:
            try:
                canonical = canonicalize_to_toon_text(path.read_text(encoding="utf-8"), schema=schema, source=str(path))
                path.write_text(canonical.text, encoding="utf-8")
            except (OSError, ValueError) as exc:
                console.print(f"[red]unable to write canonical TOON:[/] {exc}")
                raise typer.Exit(1) from exc
        if output_format == "json":
            typer.echo(json.dumps(result.as_dict(), indent=2))
        elif result.ok:
            root = f" root={result.root}" if result.root else ""
            console.print(f"[green]✓[/] valid TOON: {path}{root}")
            if result.input_format == "json":
                console.print("[green]✓[/] JSON fallback can be canonicalized to TOON.")
            if write_canonical:
                console.print(f"[green]✓[/] wrote canonical TOON: {path}")
            for warning in result.warnings:
                console.print(f"[yellow]warning:[/] {warning}")
        else:
            console.print(f"[red]invalid TOON:[/] {path}: {result.error}")
            if result.repair_hint:
                console.print(f"[yellow]repair:[/] {result.repair_hint}")
            for warning in result.warnings:
                console.print(f"[yellow]warning:[/] {warning}")

        if not result.ok:
            raise typer.Exit(1)
