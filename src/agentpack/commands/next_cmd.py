from __future__ import annotations

import json
import subprocess

import typer

from agentpack.commands._shared import console, _root, run_refresh
from agentpack.commands.diagnose_selection import build_selection_diagnosis, _markdown_report
from agentpack.commands.guard import _context_is_fresh
from agentpack.core.command_surface import refresh_commands
from agentpack.core.config import load_config
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.loop_protocol import load_loop_state
from agentpack.core.thread_context import detect_conflicts, list_thread_rows
from agentpack.integrations.platform import cli_module_argv
from agentpack.router.skills_index import ensure_inventory_index
from agentpack.session.state import TASK_FILE


def register(app: typer.Typer) -> None:
    @app.command("next")
    def next_action(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
        fix: bool = typer.Option(False, "--fix", help="Refresh stale context when safe."),
        fix_all_safe: bool = typer.Option(False, "--fix-all-safe", help="Run all safe repairs AgentPack can do without deleting or applying ignore suggestions."),
    ) -> None:
        """Recommend the next AgentPack action for this repo."""
        root = _root()
        recommendations = _recommendations(root)
        fixes: list[dict[str, str | int]] = []
        if fix_all_safe:
            recommendations, fixes = _fix_all_safe(root, recommendations)
        if fix and any(item["kind"] == "stale_context" for item in recommendations):
            stats = run_refresh(root, "auto", "balanced", 0)
            if stats:
                recommendations = [_recommendation("fixed", "agentpack next", "refreshed stale context")]
                fixes.append({"kind": "stale_context", "command": refresh_commands("auto").repair, "returncode": 0})
        payload = {"recommendations": recommendations, "fixes": fixes, "ok": not recommendations}
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
            return
        if not recommendations:
            console.print("[green]✓[/] No obvious AgentPack action required.")
            return
        console.print("[bold]AgentPack next action[/]")
        for item in recommendations:
            console.print(f"Run: [bold]{item['command']}[/]")
            console.print(f"  What failed: [dim]{item['reason']}[/]")
            if item.get("why_it_matters"):
                console.print(f"  Why it matters: [dim]{item['why_it_matters']}[/]")
            console.print(f"  Safe to continue: [dim]{item.get('safe_to_continue', 'unknown')}[/]")
        for item in fixes:
            marker = "[green]✓[/]" if item.get("returncode") == 0 else "[red]✗[/]"
            console.print(f"{marker} fixed {item['kind']}: [dim]{item['command']}[/]")


def _recommendations(root) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not (root / ".agentpack" / "config.toml").exists():
        return [_recommendation("init", "agentpack init --yes", "repo is not initialized")]
    if not _has_task(root):
        items.append(_recommendation("missing_task", 'agentpack start "describe the task"', "no concrete task is set"))
    fresh, reason = _context_is_fresh(root)
    if not fresh:
        items.append(_recommendation("stale_context", refresh_commands("auto").repair, reason))
    if _has_thread_conflicts(root):
        items.append(_recommendation("thread_conflict", "agentpack threads --conflicts", "active threads overlap on this branch/worktree"))
    if _pack_looks_noisy(root):
        items.append(_recommendation("selection_noise", "agentpack diagnose-selection", "latest pack has broad/noisy selection signals"))
    items.extend(_skills_index_recommendations(root))
    items.extend(_loop_recommendations(root))
    return items


def _has_task(root) -> bool:
    path = root / TASK_FILE
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8").strip()
    return bool(text and "Write or update the current coding task here." not in text)


def _has_thread_conflicts(root) -> bool:
    rows = list_thread_rows(root, active_only=True)
    return any(detect_conflicts(root, row).get("conflicts") for row in rows)


def _pack_looks_noisy(root) -> bool:
    meta = load_pack_metadata(root) or {}
    freshness = meta.get("freshness") or {}
    selected = meta.get("selected_files_meta") or []
    if freshness.get("generic_task_ratio", 0) and float(freshness.get("generic_task_ratio") or 0) >= 0.5:
        return True
    if freshness.get("mode_warning"):
        return True
    if isinstance(selected, list) and selected:
        summary_count = sum(1 for item in selected if isinstance(item, dict) and item.get("mode") == "summary")
        return summary_count / len(selected) >= 0.7
    return False


def _skills_index_recommendations(root) -> list[dict[str, str]]:
    cfg = load_config(root)
    try:
        ensure_inventory_index(root, cfg.skills.paths)
    except Exception as exc:
        return [
            _recommendation("skills_index_failed", "agentpack skills index", f"automatic skills index refresh failed: {exc}")
        ]
    return []


def _loop_recommendations(root) -> list[dict[str, str]]:
    cfg = load_config(root)
    if not cfg.loop.enabled:
        return []
    state = load_loop_state(root)
    if state is None:
        return []
    if not state.runner:
        return [_recommendation("loop_runner_missing", 'agentpack work "..." --run --runner "..."', "Ralph Loop state exists but no runner is configured")]
    if state.status == "ready_to_finish":
        return [_recommendation("loop_ready_to_finish", "agentpack finish --since main", "Ralph Loop verification passed")]
    if state.status == "blocked":
        return [_recommendation("loop_blocked", "agentpack dashboard", f"Ralph Loop blocked: {state.blocked_reason or 'inspect loop failures'}")]
    return [_recommendation("loop_continue", f'agentpack work "{state.task}" --run', f"Ralph Loop is {state.status}")]


def _recommendation(kind: str, command: str, reason: str) -> dict[str, str]:
    why = {
        "init": "AgentPack cannot create reliable task/context state until repo files exist.",
        "missing_task": "Generic or placeholder tasks produce noisy file selection.",
        "stale_context": "Packed selected files may describe old code, old task text, or a different snapshot.",
        "thread_conflict": "Multiple active threads may edit overlapping files without coordination.",
        "selection_noise": "Treat current pack as orientation only until noisy selection is diagnosed.",
        "skills_index_failed": "Skill/rule routing may miss local guidance until the index refreshes.",
        "loop_runner_missing": "Automated loop cannot run without an explicit runner command.",
        "loop_ready_to_finish": "Verification passed; finish captures evidence and closes the loop state.",
        "loop_blocked": "Loop state is blocked; inspect evidence before another run.",
        "loop_continue": "Loop state has pending work; continuing keeps state and rollback evidence aligned.",
        "fixed": "Safe refresh completed; rerun next to confirm remaining state.",
    }.get(kind, "Run the command before trusting AgentPack output.")
    safe = {
        "init": "no; initialize first",
        "missing_task": "no; set a concrete task first",
        "stale_context": "no; refresh or use direct rg/git evidence",
        "thread_conflict": "coordinate first",
        "selection_noise": "yes, but use direct rg/git evidence as truth",
        "skills_index_failed": "yes, but skill routing may be incomplete",
        "loop_runner_missing": "no for loop automation",
        "loop_ready_to_finish": "yes",
        "loop_blocked": "no; inspect dashboard first",
        "loop_continue": "yes for the loop runner",
        "fixed": "yes",
    }.get(kind, "unknown")
    return {"kind": kind, "command": command, "reason": reason, "why_it_matters": why, "safe_to_continue": safe}


def _fix_all_safe(root, recommendations: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str | int]]]:
    fixes: list[dict[str, str | int]] = []
    if any(item["kind"] == "init" for item in recommendations):
        result = subprocess.run(cli_module_argv("init", "--yes"), cwd=root, capture_output=True, text=True)
        fixes.append({"kind": "init", "command": "agentpack init --yes", "returncode": result.returncode})
        if result.returncode != 0:
            return recommendations, fixes
        recommendations = _recommendations(root)
    if any(item["kind"] == "stale_context" for item in recommendations):
        stats = run_refresh(root, "auto", "balanced", 0)
        fixes.append({
            "kind": "stale_context",
            "command": refresh_commands("auto").repair,
            "returncode": 0 if stats else 1,
        })
        recommendations = _recommendations(root)
    if any(item["kind"] == "selection_noise" for item in recommendations):
        diagnosis = build_selection_diagnosis(root)
        out = root / ".agentpack" / "selection_diagnosis.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_markdown_report(diagnosis), encoding="utf-8")
        fixes.append({"kind": "selection_noise", "command": "agentpack diagnose-selection --write", "returncode": 0})
        recommendations = [item for item in recommendations if item["kind"] != "selection_noise"]
    return recommendations, fixes
