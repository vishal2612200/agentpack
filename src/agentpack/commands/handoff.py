from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import typer
from pydantic import ValidationError

from agentpack.commands._shared import _root, console
from agentpack.core.handoff import (
    HandoffError,
    HandoffStore,
    accept_handoff,
    cancel_handoff,
    create_handoff,
    export_handoff,
    import_handoff,
    release_handoff,
    render_markdown,
    resolve_pending_name,
)
from agentpack.core.structured_format import StructuredFormat, to_llm


handoff_app = typer.Typer(help="Create, inspect, resume, and transfer agent handoffs.")


def register(app: typer.Typer) -> None:
    app.add_typer(handoff_app, name="handoff")


@handoff_app.command("create")
def create_command(
    input_path: Path | None = typer.Option(None, "--input", exists=True, dir_okay=False, readable=True, help="Structured handoff report JSON."),
    interactive: bool = typer.Option(False, "--interactive", help="Prompt for the handoff report."),
    name: str = typer.Option("", "--name", help="Memorable immutable handoff name."),
    target_provider: str = typer.Option("", "--target-provider", help="Restrict the destination provider."),
    target_session_id: str = typer.Option("", "--target-session-id", help="Restrict the destination session."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    if (input_path is None) == (not interactive):
        _fail("provide exactly one of --input or --interactive")
    try:
        payload = _interactive_report() if interactive else json.loads(input_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        record = create_handoff(
            _root(),
            payload,
            name=name,
            target_provider=target_provider,
            target_session_id=target_session_id,
        )
    except (HandoffError, ValidationError, OSError, json.JSONDecodeError) as exc:
        _fail(str(exc))
    if json_output:
        typer.echo(json.dumps(_public_record(record, full=True), indent=2, sort_keys=True))
    else:
        console.print(f"[green]Created handoff[/] [bold]{record.name}[/]")


@handoff_app.command("list")
def list_command(
    pending: bool = typer.Option(False, "--pending", help="Show only ready handoffs."),
    output_format: str = typer.Option("toon", "--format", help="toon|json"),
) -> None:
    store = HandoffStore(_root())
    records = store.list({"ready"} if pending else None)
    if output_format in {"toon", "json"}:
        typer.echo(to_llm(_root(), [_public_record(item) for item in records], requested=cast(StructuredFormat, output_format), root_name="handoffs"))
        return
    _fail("format must be toon or json")


@handoff_app.command("show")
def show_command(
    name: str = typer.Argument(...),
    output_format: str = typer.Option("toon", "--format", help="toon|json|markdown"),
) -> None:
    try:
        record = HandoffStore(_root()).load(name)
    except HandoffError as exc:
        _fail(str(exc))
    if output_format == "markdown":
        typer.echo(render_markdown(record), nl=False)
    elif output_format in {"toon", "json"}:
        typer.echo(to_llm(_root(), _public_record(record, full=True), requested=cast(StructuredFormat, output_format), root_name="handoff"))
    else:
        _fail("format must be toon, json, or markdown")


@handoff_app.command("resume")
def resume_command(
    name: str = typer.Argument(""),
    latest: bool = typer.Option(False, "--latest", help="Select the newest ready handoff."),
    max_tokens: int = typer.Option(20000, "--max-tokens", min=1, help="Maximum fresh context tokens."),
    output_format: str = typer.Option("toon", "--format", help="toon|json"),
) -> None:
    root = _root()
    try:
        resolved = name
        if not resolved and not latest:
            pending = HandoffStore(root).list({"ready"})
            if len(pending) > 1 and sys.stdin.isatty():
                for index, record in enumerate(pending, start=1):
                    console.print(f"{index}. [bold]{record.name}[/] - {record.report.task}")
                selection = typer.prompt("Select handoff", type=int)
                if selection < 1 or selection > len(pending):
                    raise HandoffError("invalid handoff selection")
                resolved = pending[selection - 1].name
            else:
                resolved = resolve_pending_name(HandoffStore(root), "", latest=False)
        record, warnings = accept_handoff(root, resolved, latest=latest)
        context = _fresh_context(root, record.report.task, max_tokens, record.claim.thread_id if record.claim else "")
    except (HandoffError, ValidationError) as exc:
        _fail(str(exc))
    payload = {"handoff": _public_record(record, full=True), "warnings": warnings, "context": context}
    if output_format in {"toon", "json"}:
        typer.echo(to_llm(root, payload, requested=cast(StructuredFormat, output_format), root_name="handoff_resume"))
    else:
        _fail("format must be toon or json")


@handoff_app.command("release")
def release_command(name: str = typer.Argument(...)) -> None:
    try:
        record = release_handoff(_root(), name)
    except HandoffError as exc:
        _fail(str(exc))
    console.print(f"[green]Released handoff[/] [bold]{record.name}[/]")


@handoff_app.command("cancel")
def cancel_command(name: str = typer.Argument(...)) -> None:
    try:
        record = cancel_handoff(_root(), name)
    except HandoffError as exc:
        _fail(str(exc))
    console.print(f"[green]Cancelled handoff[/] [bold]{record.name}[/]")


@handoff_app.command("export")
def export_command(
    name: str = typer.Argument(...),
    output: Path = typer.Option(..., "--output", dir_okay=False),
) -> None:
    try:
        path = export_handoff(_root(), name, output)
    except (HandoffError, OSError, zipfile.BadZipFile) as exc:
        _fail(str(exc))
    console.print(f"[green]Exported handoff[/] {path}")


@handoff_app.command("import")
def import_command(bundle: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True)) -> None:
    try:
        record = import_handoff(_root(), bundle)
    except (HandoffError, OSError) as exc:
        _fail(str(exc))
    console.print(f"[green]Imported handoff[/] [bold]{record.name}[/]")


def _interactive_report() -> dict[str, Any]:
    task = typer.prompt("Task")
    criteria = _split(typer.prompt("Acceptance criteria (semicolon separated)"))
    summary = typer.prompt("Summary")
    next_action = typer.prompt("Next action")
    completed = _split(typer.prompt("Completed work (semicolon separated)", default=""))
    remaining = _split(typer.prompt("Remaining work (semicolon separated)", default=""))
    command = typer.prompt("Validation command", default="not run")
    outcome = typer.prompt("Validation outcome", default="not_run")
    tested_sha = typer.prompt("Tested SHA", default="uncommitted")
    reason = typer.prompt("Reason not run", default="") if outcome == "not_run" else ""
    return {
        "task": task,
        "acceptance_criteria": criteria,
        "summary": summary,
        "next_action": next_action,
        "completed": completed,
        "remaining": remaining,
        "decisions": [],
        "blockers": [],
        "validation": [{"command": command, "outcome": outcome, "tested_sha": tested_sha, "timestamp": datetime.now(timezone.utc).isoformat(), "reason": reason}],
        "changed_files": [],
        "dirty_files": [],
    }


def _fresh_context(root: Path, task: str, max_tokens: int, thread_id: str) -> str:
    from agentpack.mcp_server import _pack_context_impl

    return _pack_context_impl(root, task=task, mode="balanced", budget=0, max_tokens=max_tokens, thread_id=thread_id)


def _public_record(record: Any, *, full: bool = False) -> dict[str, Any]:
    data = record.model_dump(mode="json")
    data.pop("handoff_id", None)
    if not full:
        data = {key: data[key] for key in ("name", "status", "created_at", "updated_at", "source", "target_provider", "report")}
        data["report"] = {"task": data["report"]["task"], "summary": data["report"]["summary"], "next_action": data["report"]["next_action"]}
    return data


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _fail(message: str) -> None:
    console.print(f"[red]{message}[/]")
    raise typer.Exit(1)
