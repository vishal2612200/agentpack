from __future__ import annotations

import json

import typer

from agentpack.commands._shared import _root, console
from agentpack.core.skill_review import create_skill_review_workspace


def register(app: typer.Typer) -> None:
    @app.command("skill-review")
    def skill_review_command(
        skill: str = typer.Option(..., "--skill", help="Path or discovered name of the SKILL.md to review."),
        output: str = typer.Option("", "--output", help="Workspace directory (default: .agentpack/skill-reviews/<skill>/iteration-1)."),
        eval_count: int = typer.Option(20, "--eval-count", help="Even number of candidate trigger/non-trigger evals (4-40)."),
        force: bool = typer.Option(False, "--force", help="Overwrite generated files in an existing workspace."),
        as_json: bool = typer.Option(False, "--json", help="Print a machine-readable result."),
    ) -> None:
        """Review a SKILL.md and generate a candidate eval set in one pass."""
        try:
            workspace = create_skill_review_workspace(
                _root(),
                skill,
                output=output,
                eval_count=eval_count,
                force=force,
            )
        except (OSError, ValueError) as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc

        payload = {
            "skill_path": str(workspace.skill_path),
            "output_dir": str(workspace.output_dir),
            "review": str(workspace.review_path),
            "evals": str(workspace.evals_path),
            "manifest": str(workspace.manifest_path),
            "findings": str(workspace.findings_path),
            "eval_count": workspace.eval_count,
        }
        if as_json:
            typer.echo(json.dumps(payload, indent=2))
            return
        console.print(f"[green]✓[/] Created skill review workspace for [bold]{workspace.skill_path}[/]")
        console.print(f"  Findings: [bold]{workspace.findings_path}[/]")
        console.print(f"  Eval set: [bold]{workspace.evals_path}[/]")
        console.print(f"  Runbook: [bold]{workspace.review_path}[/]")
        console.print("  Next: review the candidate evals, then run the host-agent workflow from review.md.")
