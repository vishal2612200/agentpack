from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich import box

from agentpack.core import git
from agentpack.core.ignore import SENSITIVE_PATTERNS
from agentpack.core.thread_context import resolve_session_thread_option, thread_paths
from agentpack.analysis.ranking import suggest_task_rewrite
from agentpack.application.pack_service import PackRequest, PackService, PackResult
from agentpack.commands._shared import console, _root, _file_hash, _now_iso
from agentpack.core.changed_paths import record_changed_paths
from agentpack.core.command_surface import refresh_commands
from agentpack.core.modes import MODE_HELP, invalid_mode_message, is_requested_mode
from agentpack.integrations.agents import check_agent_integration, install_agent_integration
from agentpack.session.state import TASK_FILE, load_session, save_session, log_activity


def register(app: typer.Typer) -> None:
    @app.command()
    def pack(
        agent: str = typer.Option("auto", "--agent", help="Target agent (auto|claude|cursor|windsurf|codex|antigravity|generic). 'auto' detects from environment."),
        task: str = typer.Option(
            "auto",
            "--task",
            help="Task text to pack, or 'auto' to read .agentpack/task.md / git context.",
        ),
        mode: str = typer.Option("balanced", "--mode", help=f"Budget mode ({MODE_HELP})."),
        budget: int = typer.Option(0, "--budget", help="Token budget (0 = use config default)."),
        workspace: str = typer.Option("", "--workspace", help="Restrict pack to a monorepo workspace, e.g. apps/web."),
        since: Optional[str] = typer.Option(None, "--since", help="Git ref to compare against (e.g. HEAD~1, main)."),
        refresh: bool = typer.Option(False, "--refresh", help="Rebuild summaries before packing."),
        watch: bool = typer.Option(False, "--watch", help="Watch for file changes and re-pack automatically."),
        session: bool = typer.Option(False, "--session", help="Keep re-packing on changes for the whole session (alias for --watch)."),
        thread: str = typer.Option("", "--thread", help="Use thread-scoped task/context state (auto by default in agent sessions; use 'global' for legacy global state)."),
    ) -> None:
        """Generate a context pack for an AI coding agent."""
        if not is_requested_mode(mode):
            console.print(f"[red]{invalid_mode_message(mode)}[/]")
            raise typer.Exit(1)

        resolved_agent = _resolve_agent(agent)
        resolved_thread_id = resolve_session_thread_option(thread)
        resolved_task, task_source = _resolve_task_with_source(task, thread_id=resolved_thread_id)

        if watch or session:
            _pack_watch(agent=resolved_agent, task=resolved_task, mode=mode, budget=budget,
                        since=since, workspace=workspace or None, thread_id=resolved_thread_id)
            return

        result = PackService().run(PackRequest(
            root=_root(),
            agent=resolved_agent,
            task=resolved_task,
            mode=mode,
            budget=budget,
            since=since,
            refresh=refresh,
            task_source=task_source,
            workspace=workspace or None,
            thread_id=resolved_thread_id,
        ))
        _mark_session_refreshed(_root(), result)
        _auto_repair_stale_agent_rules(result.pack.agent)
        _print_pack_summary(result)


def _resolve_agent(agent: str) -> str:
    if agent != "auto":
        return agent
    from agentpack.adapters.detect import detect_agent
    resolved = detect_agent(_root())
    console.print(f"[dim]Auto agent: {resolved}[/]")
    return resolved


def _resolve_task(task: str) -> str:
    resolved, _source = _resolve_task_with_source(task)
    return resolved


def _resolve_task_with_source(task: str, thread_id: str | None = None) -> tuple[str, str]:
    if task != "auto":
        task_text = " ".join(task.strip().split())
        if not task_text:
            console.print("[red]Task text cannot be empty.[/]")
            raise typer.Exit(1)
        root = _root()
        scoped = thread_paths(root, thread_id)
        task_path = scoped.task if scoped else root / ".agentpack" / "task.md"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(task_text + "\n", encoding="utf-8")
        source = f"thread:{scoped.thread_id}:--task" if scoped else "--task"
        console.print(f"[dim]Task from --task: {task_text}[/]")
        console.print(f"[dim]Wrote {task_path.relative_to(root)}[/]")
        return task_text, source
    root = _root()
    scoped = thread_paths(root, thread_id)
    if scoped and scoped.task.exists():
        content = scoped.task.read_text(encoding="utf-8").strip()
        lines = [ln for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
        body = lines[0].strip() if lines else ""
        if body:
            console.print(f"[dim]Auto task (thread {scoped.thread_id}): {body}[/]")
            return body, "thread_task.md"
    if scoped:
        console.print(
            f"[red]No task is set for AgentPack session {scoped.thread_id}.[/] "
            f"Run [bold]agentpack start \"describe the task\" --thread {scoped.thread_id}[/] "
            "or pass [bold]--task[/] explicitly."
        )
        raise typer.Exit(1)
    # task.md takes priority over all git heuristics
    task_md_path = root / ".agentpack" / "task.md"
    if task_md_path.exists():
        content = task_md_path.read_text(encoding="utf-8").strip()
        lines = [ln for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
        body = lines[0].strip() if lines else ""
        _PLACEHOLDER = "Write or update the current coding task here."
        if body and _PLACEHOLDER not in body:
            console.print(f"[dim]Auto task (task.md): {body}[/]")
            return body, "task.md"
    inferred, source = git.infer_task_with_source(root)
    console.print(f"[dim]Auto task ({source}): {inferred}[/]")
    return inferred, source


def _print_pack_summary(result: PackResult) -> None:
    out_path = result.out_path
    selected = result.pack.selected_files
    packed_tokens = result.packed_tokens
    raw_tokens = result.raw_tokens
    saving_pct = result.saving_pct
    changed_files = result.changed_files
    task = result.pack.task
    # since is not stored in PackResult; shown via changed_files

    stats = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    stats.add_column(style="dim")
    stats.add_column(justify="right", style="bold")
    stats.add_row("packed tokens", f"{packed_tokens:,}")
    stats.add_row("raw tokens", f"{raw_tokens:,}")
    stats.add_row("saving", f"[green]{saving_pct:.1f}%[/]")

    MODE_STYLE = {
        "full": "green",
        "diff": "cyan",
        "symbols": "yellow",
        "skeleton": "blue",
        "summary": "dim",
    }
    files_tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    files_tbl.add_column("file", style="dim", no_wrap=False, max_width=55)
    files_tbl.add_column("mode", justify="center", width=8)
    files_tbl.add_column("why", style="dim", max_width=30)

    changed_set = set(changed_files)
    for sf in selected[:20]:
        style = MODE_STYLE.get(sf.include_mode, "")
        mode_text = f"[{style}]{sf.include_mode}[/]" if style else sf.include_mode
        changed_marker = " [red]●[/]" if sf.path in changed_set else ""
        files_tbl.add_row(
            f"{sf.path}{changed_marker}",
            mode_text,
            ", ".join(sf.reasons) if sf.reasons else "",
        )
    if len(selected) > 20:
        files_tbl.add_row(f"[dim]... {len(selected) - 20} more[/]", "", "")

    if changed_files:
        changed_lines = "\n".join(f"  [red]●[/] {f}" for f in changed_files[:10])
        if len(changed_files) > 10:
            changed_lines += f"\n  [dim]... {len(changed_files) - 10} more[/]"
    else:
        changed_lines = "  [dim]none detected[/]"

    console.print()
    console.print(Panel(
        f"[bold cyan]{task}[/]",
        title="[bold green]✓ Context Pack Ready[/]",
        subtitle=f"[dim]{out_path}[/]",
        border_style="green",
        padding=(0, 1),
    ))
    console.print()
    console.print(Columns([stats, files_tbl], equal=False, expand=False))

    diagnostics = _pack_diagnostics(result)
    integration_warnings = _agent_integration_warnings(result)
    diagnostics.extend(integration_warnings)
    if diagnostics:
        diag_text = "\n".join(f"  [yellow]![/] {line}" for line in diagnostics)
        console.print(Panel(
            diag_text,
            title="[bold yellow]Pack diagnostics[/]",
            border_style="yellow",
            padding=(0, 1),
        ))

    _print_concurrent_context_warning(result)

    if changed_files:
        console.print(f"\n[bold]Changed files[/] ({len(changed_files)}):")
        console.print(changed_lines)

    redaction_warnings = result.pack.redaction_warnings
    if redaction_warnings:
        console.print(f"\n[bold yellow]⚠ Secrets redacted ({len(redaction_warnings)}):[/]")
        for w in redaction_warnings[:10]:
            console.print(f"  [yellow]{w}[/]")
        if len(redaction_warnings) > 10:
            console.print(f"  [dim]... {len(redaction_warnings) - 10} more[/]")

    sensitive_excluded = [
        fi.path for fi in result.scan_result.ignored
        if SENSITIVE_PATTERNS.match_file(fi.path)
    ]
    if sensitive_excluded:
        console.print(f"\n[bold green]✓ Sensitive files excluded ({len(sensitive_excluded)}):[/]")
        for p in sensitive_excluded[:10]:
            console.print(f"  [dim]{p}[/]")
        if len(sensitive_excluded) > 10:
            console.print(f"  [dim]... {len(sensitive_excluded) - 10} more[/]")

    console.print("\n[bold]Next step:[/]")
    console.print(f"  [bold white]claude < {out_path}[/]")
    console.print()


def _print_concurrent_context_warning(result: PackResult) -> None:
    conflicts = result.pack.concurrent_context.get("conflicts") or []
    if not conflicts:
        return
    lines: list[str] = []
    for conflict in conflicts[:5]:
        files = ", ".join(conflict.get("overlap") or [])
        if conflict.get("overlap_count", 0) > len(conflict.get("overlap") or []):
            files += f", +{conflict['overlap_count'] - len(conflict.get('overlap') or [])} more"
        lines.append(
            f"  [yellow]![/] {conflict.get('thread_id')} ({conflict.get('status') or 'unknown'}): "
            f"{conflict.get('task') or '(no task)'}"
        )
        lines.append(f"      overlaps {conflict.get('overlap_count', 0)} file(s): {files or '(none)'}")
    console.print(Panel(
        "\n".join(lines),
        title="[bold yellow]Concurrent context warning[/]",
        border_style="yellow",
        padding=(0, 1),
    ))


def _mark_session_refreshed(root: Path, result: PackResult) -> None:
    state = load_session(root)
    if state is None or not state.active:
        return
    freshness = result.pack.freshness or {}
    state.last_refresh_at = freshness.get("generated_at") or _now_iso()
    state.refresh_count += 1
    state.last_task_hash = _file_hash(root / TASK_FILE)
    state.last_git_hash = freshness.get("snapshot_root_hash", "")
    state.last_resolved_agent = getattr(result.pack, "agent", state.last_resolved_agent)
    save_session(root, state)
    log_activity(root, f"pack refresh — {len(result.pack.selected_files)} files, {result.packed_tokens:,} tokens")


def _pack_diagnostics(result: PackResult) -> list[str]:
    selected = result.pack.selected_files
    receipts = result.pack.receipts
    diagnostics: list[str] = []
    summary_count = sum(1 for sf in selected if sf.include_mode == "summary")
    filename_matches = sum(1 for sf in selected if "filename keyword match" in sf.reasons)
    symbol_matches = sum(1 for sf in selected if "symbol keyword match" in sf.reasons)
    score_floor_excluded = sum(1 for r in receipts if r.reason == "summary score below floor")
    summary_cap_excluded = sum(1 for r in receipts if r.reason in {"summary cap reached", "compressed context cap reached"})
    changed_set = set(result.changed_files)
    top_changed = sum(1 for sf in selected[:10] if sf.path in changed_set)
    strong_live_signal = bool(changed_set) and top_changed >= min(len(changed_set), 5)

    task_words = [
        part for part in result.pack.task.replace("_", " ").replace("-", " ").split()
        if len(part) >= 3
    ]
    generic_ratio = float((result.pack.freshness or {}).get("generic_task_ratio") or 0.0)
    mode_warning = (result.pack.freshness or {}).get("mode_warning")
    if len(task_words) <= 3:
        diagnostics.append("Task is very short; add subsystem, file, or symptom words for better precision.")
    if generic_ratio >= 0.5:
        diagnostics.append("Task terms are broad/generic; name concrete file, route, service, or symptom words.")
        diagnostics.append(f"Rewrite example: `{suggest_task_rewrite(result.pack.task)}`.")
    if mode_warning:
        diagnostics.append(str(mode_warning))
    if not result.changed_files:
        diagnostics.append("No changed files detected; pack relies mostly on task keywords and cached summaries.")
    if selected and not strong_live_signal and filename_matches / len(selected) >= 0.6:
        diagnostics.append("Most selected files matched by filename; task terms may be broad.")
    if selected and not strong_live_signal and summary_count / len(selected) >= 0.7:
        diagnostics.append("Pack is mostly summaries; keep balanced mode and use a more specific task for edit work.")
    if symbol_matches > 25:
        diagnostics.append(f"Many symbol matches selected ({symbol_matches}); inspect repeated task terms with explain.")
    if score_floor_excluded:
        diagnostics.append(f"{score_floor_excluded} weak summaries excluded by score floor.")
    if summary_cap_excluded:
        diagnostics.append(f"{summary_cap_excluded} summaries excluded by mode cap.")
    if result.scan_result.scan_mode == "incremental":
        diagnostics.append(
            f"Incremental scan reused {result.scan_result.reused_count:,} file(s), "
            f"rehashed {result.scan_result.rehashed_count:,}."
        )
    elif result.scan_result.full_scan_reason:
        diagnostics.append(f"Full scan: {result.scan_result.full_scan_reason}.")
    return diagnostics[:5]


def _agent_integration_warnings(result: PackResult) -> list[str]:
    agent = result.pack.agent
    if agent == "generic":
        return []
    try:
        failing = [check for check in check_agent_integration(_root(), agent) if not check.ok]
    except Exception:
        return []
    if not failing:
        return []
    return [
        f"Agent integration needs repair ({agent}); run `{refresh_commands(agent).repair}`."
    ]


def _auto_repair_stale_agent_rules(agent: str) -> None:
    if agent == "generic":
        return
    root = _root()
    try:
        checks = check_agent_integration(root, agent)
    except Exception:
        return
    stale = [check for check in checks if not check.ok and check.detail.startswith("stale AgentPack")]
    if not stale:
        return
    try:
        from agentpack.commands.install import _install_slash_command

        install_agent_integration(root, agent, install_slash_command=_install_slash_command)
        console.print(f"[yellow]Auto-repaired stale AgentPack integration for {agent}.[/]")
    except Exception as exc:
        console.print(
            f"[yellow]Stale AgentPack integration detected for {agent}; "
            f"run `{refresh_commands(agent).repair}`. ({exc})[/]"
        )


def _pack_watch(
    agent: str,
    task: str,
    mode: str,
    budget: int,
    since: str | None,
    workspace: str | None = None,
    thread_id: str | None = None,
) -> None:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        console.print("[red]watchdog is required for --watch mode.[/]")
        console.print("Install it in this environment: [bold]python -m pip install watchdog[/]")
        raise typer.Exit(1)

    root = _root()
    console.print("[bold]Watch mode active.[/] Repacking on file changes... (Ctrl+C to stop)")
    console.print(f"  Task: {task}")
    if workspace:
        console.print(f"  Workspace: {workspace}")

    def _run_pack() -> None:
        result = PackService().run(PackRequest(
            root=root, agent=agent, task=task, mode=mode, budget=budget,
            since=since, refresh=False, task_source="watch", workspace=workspace, thread_id=thread_id,
        ))
        _mark_session_refreshed(root, result)
        _print_pack_summary(result)

    _run_pack()

    _last_pack = [time.time()]
    _DEBOUNCE = 2.0

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            path = str(event.src_path)
            if ".agentpack" in path:
                return
            try:
                rel = str(Path(path).relative_to(root)).replace("\\", "/")
                record_changed_paths(root, [rel], source="watch")
            except ValueError:
                pass
            now = time.time()
            if now - _last_pack[0] < _DEBOUNCE:
                return
            _last_pack[0] = now
            console.print(f"\n[dim]Change detected: {event.src_path}[/]")
            try:
                _run_pack()
            except Exception as e:
                console.print(f"[red]Pack error: {e}[/]")

    observer = Observer()
    observer.schedule(Handler(), str(root), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        console.print("\n[dim]Watch mode stopped.[/]")
    observer.join()
