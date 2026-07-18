from __future__ import annotations

from pathlib import Path

import typer

from agentpack.commands._shared import _root, console
from agentpack.core.config import load_config
from agentpack.core.pack_registry import retrieve_from_registry
from agentpack.session.events import record_event


def register(app: typer.Typer) -> None:
    @app.command()
    def retrieve(
        target: str = typer.Argument("", help="Path from the latest pack registry."),
        block_id: str = typer.Option("", "--block-id", help="Exact registry block ID to retrieve."),
        mode: str = typer.Option("as_stored", "--mode", help="as_stored|full|skeleton|symbols|summary."),
        kind: str = typer.Option("any", "--kind", help="any|selected|omitted."),
        allow_stale: bool = typer.Option(False, "--allow-stale", help="Read current file contents when the registry hash is stale."),
        output: Path | None = typer.Option(None, "--output", "-o", help="Write retrieval output to a file."),
    ) -> None:
        """Retrieve file or symbol context from the latest pack registry."""
        root = _root()
        cfg = load_config(root)
        if not target and not block_id:
            console.print("[yellow]Provide a path argument or --block-id.[/]")
            raise typer.Exit(1)
        content = retrieve_from_registry(
            root,
            path=target,
            block_id=block_id,
            mode=mode,
            allow_stale=allow_stale,
            kind=kind if kind in {"any", "selected", "omitted"} else "any",
            max_chars=cfg.runtime.max_retrieve_chars,
            registry_file=root / cfg.runtime.pack_registry_output,
        )
        record_event(
            root,
            "retrieve",
            {
                "path": target,
                "block_id": block_id,
                "mode": mode,
                "kind": kind,
                "allow_stale": allow_stale,
            },
            output_path=cfg.runtime.session_events_output,
        )
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            console.print(f"[green]Retrieved context written:[/] {output}")
            return
        typer.echo(content)
