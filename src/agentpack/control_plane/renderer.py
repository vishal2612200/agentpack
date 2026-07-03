from __future__ import annotations

from rich.console import Console

from agentpack.control_plane.models import ControlPlaneSnapshot, Recommendation


def print_recommendations(console: Console, recommendations: list[Recommendation]) -> None:
    if not recommendations:
        console.print("[green]✓[/] No obvious AgentPack action required.")
        return
    console.print("[bold]AgentPack next action[/]")
    for item in recommendations:
        console.print(f"Run: [bold]{item.command}[/]")
        console.print(f"  What failed: [dim]{item.reason}[/]")
        console.print(f"  Why it matters: [dim]{item.why_it_matters}[/]")
        console.print(f"  Safe to continue: [dim]{item.safe_to_continue}[/]")


def token_hint(snapshot: ControlPlaneSnapshot) -> str:
    tokens = snapshot.tokens
    if not tokens.estimated_tokens:
        return "No packed token contract yet."
    budget = f"/{tokens.budget:,}" if tokens.budget else ""
    return f"{tokens.estimated_tokens:,}{budget} tokens. {tokens.recommended_next_context}"
