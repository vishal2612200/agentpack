from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer

from agentpack.commands._shared import _root, console
from agentpack.dashboard.collectors import build_project_dashboard_snapshot
from agentpack.dashboard.server import DEFAULT_DASHBOARD_HOST, DEFAULT_DASHBOARD_PORT, serve_dashboard


def register(app: typer.Typer) -> None:
    @app.command()
    def dashboard(
        json_output: bool = typer.Option(False, "--json", help="Print normalized dashboard snapshot JSON."),
        open_browser: bool = typer.Option(False, "--open", help="Open the served dashboard in a browser."),
        port: int = typer.Option(DEFAULT_DASHBOARD_PORT, "--port", help="Local dashboard server port."),
        output: str = typer.Option("", "--output", "-o", help="Deprecated. Static dashboard files are no longer written."),
        legacy: bool = typer.Option(False, "--legacy", help="Deprecated. The dashboard is serve-only."),
    ) -> None:
        """Serve the local AgentPack dashboard."""
        root = _root()
        snapshot = build_project_dashboard_snapshot(root)
        if json_output:
            typer.echo(json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True))
            return
        if output or legacy:
            console.print("[red]Static dashboard output is deprecated.[/] Run `agentpack dashboard` and open the served URL instead.")
            raise typer.Exit(2)
        url = f"http://{DEFAULT_DASHBOARD_HOST}:{port}/"
        console.print(f"[green]✓[/] Serving AgentPack dashboard at [bold]{url}[/]")
        console.print("[dim]Press Ctrl+C to stop.[/]")
        try:
            serve_dashboard(root, host=DEFAULT_DASHBOARD_HOST, port=port, open_browser=open_browser)
        except OSError as exc:
            console.print(f"[red]Dashboard server failed on {DEFAULT_DASHBOARD_HOST}:{port}: {exc}[/]")
            console.print("[dim]Use `agentpack dashboard --port <port>` if this port is already in use.[/]")
            raise typer.Exit(1) from exc


def _open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run(["cmd", "/c", "start", "", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
