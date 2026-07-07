from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer

from agentpack.commands._shared import _atomic_write, _root, console
from agentpack.dashboard.app_shell import write_dashboard_shell
from agentpack.dashboard.collectors import build_project_dashboard_snapshot
from agentpack.dashboard.graph import build_dashboard_graph
from agentpack.dashboard.renderers import render_dashboard_html


def register(app: typer.Typer) -> None:
    @app.command()
    def dashboard(
        json_output: bool = typer.Option(False, "--json", help="Print normalized dashboard snapshot JSON."),
        open_browser: bool = typer.Option(False, "--open", help="Open the generated HTML dashboard."),
        output: str = typer.Option("", "--output", "-o", help="Dashboard HTML output path."),
        legacy: bool = typer.Option(False, "--legacy", help="Write the legacy static HTML dashboard."),
    ) -> None:
        """Generate a local AgentPack dashboard."""
        root = _root()
        snapshot = build_project_dashboard_snapshot(root)
        graph = build_dashboard_graph(snapshot, root)
        if json_output:
            typer.echo(json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True))
            return

        out = root / (output or ".agentpack/index.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        data_out = out.parent / "dashboard-data.json"
        graph_out = out.parent / "dashboard-graph.json"
        _atomic_write(data_out, json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
        _atomic_write(graph_out, json.dumps(graph.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
        if legacy:
            _atomic_write(out, render_dashboard_html(snapshot))
            modern = False
        else:
            modern = write_dashboard_shell(out, snapshot, graph)
        label = "cockpit" if modern else "legacy dashboard"
        console.print(f"[green]✓[/] Wrote {label} [bold]{out}[/]")
        console.print(f"[green]✓[/] Wrote data [bold]{data_out}[/]")
        console.print(f"[green]✓[/] Wrote graph [bold]{graph_out}[/]")
        if open_browser:
            _open_file(out)


def _open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run(["cmd", "/c", "start", "", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
