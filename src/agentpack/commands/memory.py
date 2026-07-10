from __future__ import annotations

import json
from collections import Counter

import typer
from rich.table import Table
from rich import box

from agentpack.commands._shared import _root, console
from agentpack.core.config import load_config
from agentpack.learning.memory_timeline import build_memory_timeline
from agentpack.session.references import merge_issue_reference_objects, merge_issue_references
from agentpack.session.events import read_events


def register(app: typer.Typer) -> None:
    @app.command()
    def memory(
        json_output: bool = typer.Option(False, "--json", help="Print JSON."),
        task: str = typer.Option("", "--task", help="Show the bounded retrieval chain for this task."),
        path: list[str] = typer.Option([], "--path", help="Live candidate path; repeat to bound memory retrieval."),
        timeline: bool = typer.Option(False, "--timeline", help="Show timestamped task, episode, procedure, and edge rows."),
        limit: int = typer.Option(50, "--limit", help="Maximum timeline rows to show."),
        prune: bool = typer.Option(False, "--prune", help="Prune local memory files to configured retention limits."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show prune counts without writing files."),
        max_events: int = typer.Option(0, "--max-events", help="Override retained session event rows for --prune."),
        max_episodes: int = typer.Option(0, "--max-episodes", help="Override retained episodic case rows for --prune."),
    ) -> None:
        """Show local cross-agent task memory from events and learning artifacts."""
        root = _root()
        cfg = load_config(root)
        if task.strip():
            from agentpack.learning.graph_memory import retrieve_memory_chain

            payload = retrieve_memory_chain(root, task=task, live_paths=path)
            if json_output:
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                return
            console.print_json(json.dumps(payload, indent=2, sort_keys=True))
            return
        if prune:
            result = {
                "session_events": _prune_jsonl(
                    root / cfg.runtime.session_events_output,
                    max_rows=max_events or cfg.runtime.max_session_events,
                    dry_run=dry_run,
                ),
                "episodic_cases": _prune_jsonl(
                    root / cfg.learning.episodic_cases_output,
                    max_rows=max_episodes or cfg.runtime.max_episodic_cases,
                    dry_run=dry_run,
                ),
            }
            if json_output:
                typer.echo(json.dumps(result, indent=2))
                return
            for label, payload in result.items():
                console.print(
                    f"[green]✓[/] {label}: kept {payload['kept']}, pruned {payload['pruned']}"
                    + (" (dry run)" if dry_run else "")
                )
            return
        if timeline:
            rows = build_memory_timeline(root, limit=limit)
            payload = {"timeline": rows, "count": len(rows)}
            if json_output:
                typer.echo(json.dumps(payload, indent=2))
                return
            table = Table(title="AgentPack Memory Timeline", box=box.SIMPLE, show_header=True, padding=(0, 1))
            table.add_column("time", style="dim")
            table.add_column("kind")
            table.add_column("id")
            table.add_column("relation")
            table.add_column("confidence")
            table.add_column("stale")
            table.add_column("reason")
            for row in rows:
                relation = ""
                if row.get("from_id") or row.get("to_id"):
                    relation = f"{_short(row.get('from_id'), 28)} -> {_short(row.get('to_id'), 28)}"
                table.add_row(
                    _short(row.get("timestamp"), 24),
                    str(row.get("kind") or ""),
                    _short(row.get("id") or row.get("version"), 32),
                    relation,
                    str(row.get("confidence") or ""),
                    "yes" if row.get("is_stale") else "",
                    _short(row.get("visible_reason"), 60),
                )
            console.print(table)
            return
        events = read_events(root, output_path=cfg.runtime.session_events_output, limit=500)
        tasks = [str(event.get("task")) for event in events if event.get("task")]
        concepts = Counter(
            concept
            for event in events
            for concept in (event.get("concepts") or [])
            if isinstance(concept, str)
        )
        issue_references = merge_issue_references(
            ref
            for event in events
            for ref in (event.get("issue_references") or [])
            if isinstance(ref, str)
        )
        top_issue_references = Counter(
            ref
            for event in events
            for ref in (event.get("issue_references") or [])
            if isinstance(ref, str)
        ).most_common(20)
        issue_reference_details = merge_issue_reference_objects(
            item
            for event in events
            for item in (event.get("issue_reference_details") or [])
            if isinstance(item, dict)
        )
        episodes = _read_jsonl(root / cfg.learning.episodic_cases_output, limit=200)
        episode_concepts = Counter(
            concept
            for episode in episodes
            for concept in (episode.get("concepts") or [])
            if isinstance(concept, str)
        )
        payload = {
            "recent_tasks": tasks[-20:],
            "recent_issue_references": issue_references[-20:],
            "issue_reference_details": [item.to_dict() for item in issue_reference_details[-20:]],
            "top_issue_references": top_issue_references,
            "top_concepts": concepts.most_common(20),
            "episode_count": len(episodes),
            "top_episode_concepts": episode_concepts.most_common(20),
            "event_count": len(events),
        }
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
            return
        table = Table(title="AgentPack Memory", box=box.SIMPLE, show_header=True, padding=(0, 1))
        table.add_column("kind", style="dim")
        table.add_column("value")
        table.add_row("events", str(len(events)))
        for task in tasks[-10:]:
            table.add_row("task", task)
        for ref in issue_references[-10:]:
            table.add_row("issue", ref)
        for item in issue_reference_details[-10:]:
            label = item.ref
            if item.title:
                label += f" — {item.title}"
            if item.state:
                label += f" ({item.state})"
            table.add_row("issue detail", label)
        for concept, count in concepts.most_common(10):
            table.add_row("concept", f"{concept} ({count})")
        for concept, count in episode_concepts.most_common(10):
            table.add_row("episode concept", f"{concept} ({count})")
        console.print(table)


def _read_jsonl(path, *, limit: int) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _prune_jsonl(path, *, max_rows: int, dry_run: bool) -> dict:
    if max_rows <= 0:
        return {"path": str(path), "kept": 0, "pruned": 0, "total": 0}
    if not path.exists():
        return {"path": str(path), "kept": 0, "pruned": 0, "total": 0}
    lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    kept_lines = lines[-max_rows:]
    pruned = max(0, len(lines) - len(kept_lines))
    if pruned and not dry_run:
        path.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    return {"path": str(path), "kept": len(kept_lines), "pruned": pruned, "total": len(lines)}


def _short(value, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"
