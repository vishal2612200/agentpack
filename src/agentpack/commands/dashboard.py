from __future__ import annotations

import json
import socket
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
        port: int | None = typer.Option(None, "--port", help="Local dashboard server port.", show_default=DEFAULT_DASHBOARD_PORT),
        output: str = typer.Option("", "--output", "-o", help="Deprecated. Static dashboard files are no longer written."),
        legacy: bool = typer.Option(False, "--legacy", help="Deprecated. The dashboard is serve-only."),
    ) -> None:
        """Serve the local AgentPack dashboard."""
        root = _root()
        if json_output:
            snapshot = build_project_dashboard_snapshot(root)
            typer.echo(json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True))
            return
        if output or legacy:
            console.print("[red]Static dashboard output is deprecated.[/] Run `agentpack dashboard` and open the served URL instead.")
            raise typer.Exit(2)
        requested_port = DEFAULT_DASHBOARD_PORT if port is None else port
        actual_port = requested_port
        if port is None and not _port_available(DEFAULT_DASHBOARD_HOST, requested_port):
            actual_port = _free_port(DEFAULT_DASHBOARD_HOST)
            console.print(
                f"[yellow]Port {requested_port} is already in use; using port {actual_port} instead.[/]"
            )
        url = f"http://{DEFAULT_DASHBOARD_HOST}:{actual_port}/"
        console.print(f"[green]✓[/] Serving AgentPack dashboard at [bold]{url}[/]")
        console.print("[dim]Press Ctrl+C to stop.[/]")
        try:
            serve_dashboard(root, host=DEFAULT_DASHBOARD_HOST, port=actual_port, open_browser=open_browser)
        except OSError as exc:
            console.print(f"[red]Dashboard server failed on {DEFAULT_DASHBOARD_HOST}:{actual_port}: {exc}[/]")
            console.print("[dim]Use `agentpack dashboard --port <port>` if this port is already in use.[/]")
            raise typer.Exit(1) from exc


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run(["cmd", "/c", "start", "", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
