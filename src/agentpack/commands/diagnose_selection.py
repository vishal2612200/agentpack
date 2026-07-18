from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from agentpack.commands._shared import console, _root
from agentpack.commands.tune import _build_tuning_suggestions
from agentpack.core.context_pack import load_pack_metadata


def register(app: typer.Typer) -> None:
    @app.command("diagnose-selection")
    def diagnose_selection(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
        write: bool = typer.Option(False, "--write", help="Write .agentpack/selection_diagnosis.md."),
    ) -> None:
        """Diagnose noisy or low-recall context selection."""
        root = _root()
        diagnosis = build_selection_diagnosis(root)
        if write:
            out = root / ".agentpack" / "selection_diagnosis.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(_markdown_report(diagnosis), encoding="utf-8")
            diagnosis["written"] = str(out.relative_to(root))
        if json_output:
            typer.echo(json.dumps(diagnosis, indent=2, sort_keys=True))
            return
        _print_diagnosis(diagnosis)


def build_selection_diagnosis(root: Path) -> dict[str, Any]:
    meta = load_pack_metadata(root) or {}
    selected = [item for item in (meta.get("selected_files_meta") or []) if isinstance(item, dict)]
    largest = sorted(selected, key=lambda item: int(item.get("tokens") or 0), reverse=True)[:10]
    summary_count = sum(1 for item in selected if item.get("mode") == "summary")
    diagnostics: list[str] = []
    freshness = meta.get("freshness") or {}
    if freshness.get("generic_task_ratio", 0) and float(freshness.get("generic_task_ratio") or 0) >= 0.5:
        diagnostics.append("Task terms are broad; rewrite with concrete subsystem, file, route, or symptom words.")
    if selected and summary_count / len(selected) >= 0.7:
        diagnostics.append("Latest pack is mostly summaries; keep standard balanced mode and tighten task wording.")
    if freshness.get("mode_warning"):
        diagnostics.append(str(freshness["mode_warning"]))
    suggestions = [
        {"area": item.area, "finding": item.finding, "suggestion": item.suggestion}
        for item in _build_tuning_suggestions(root, include_benchmark=True)
    ]
    benchmark_misses = _recent_benchmark_misses(root)
    selection_explanations = _selection_explanations_from_metadata(selected)
    omission_summary = _omission_summary(meta)
    actions = list(diagnostics)
    actions.extend(item["suggestion"] for item in suggestions[:6])
    if not actions:
        actions.append("No obvious selection issue found. Add benchmark cases with expected_files for stronger diagnostics.")
    return {
        "task": meta.get("task", ""),
        "context_path": meta.get("context_path", ""),
        "selected_count": len(selected),
        "summary_count": summary_count,
        "largest_token_consumers": [
            {"path": item.get("path"), "mode": item.get("mode"), "tokens": item.get("tokens", 0)}
            for item in largest
        ],
        "selection_explanations": selection_explanations,
        "omission_summary": omission_summary,
        "diagnostics": diagnostics,
        "benchmark_misses": benchmark_misses,
        "suggestions": suggestions,
        "actions": actions[:10],
    }


def _recent_benchmark_misses(root: Path) -> list[dict[str, Any]]:
    path = root / ".agentpack" / "benchmark_results.jsonl"
    if not path.exists():
        return []
    misses: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-20:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        for miss in row.get("misses", []) or []:
            if isinstance(miss, dict):
                item = dict(miss)
                item["task"] = row.get("task")
                misses.append(item)
    return misses[-10:]


def _print_diagnosis(diagnosis: dict[str, Any]) -> None:
    console.print("[bold]Selection diagnosis[/]")
    if diagnosis.get("task"):
        console.print(f"Task: {diagnosis['task']}")
    table = Table(show_header=True)
    table.add_column("file")
    table.add_column("mode")
    table.add_column("tokens", justify="right")
    for item in diagnosis["largest_token_consumers"][:8]:
        table.add_row(str(item["path"]), str(item["mode"]), str(item["tokens"]))
    console.print(table)
    console.print("[bold]Why selected[/]")
    for item in diagnosis["selection_explanations"][:8]:
        why = "; ".join(item.get("why_selected", [])[:3]) or "selected by pack score"
        console.print(f"  - {item['path']}: {why}")
    console.print("[bold]Why not selected[/]")
    if diagnosis["omission_summary"]:
        for item in diagnosis["omission_summary"][:8]:
            console.print(f"  - {item['reason']} ({item['count']}): {item['why_not_selected']}")
    else:
        console.print("  - No omitted-file buckets recorded in latest pack metadata.")
    console.print("[bold]Actions[/]")
    for action in diagnosis["actions"]:
        console.print(f"  - {action}")
    if diagnosis.get("written"):
        console.print(f"[green]✓[/] Wrote {diagnosis['written']}")


def _markdown_report(diagnosis: dict[str, Any]) -> str:
    lines = ["# AgentPack Selection Diagnosis", ""]
    if diagnosis.get("task"):
        lines.append(f"- Task: {diagnosis['task']}")
    lines.append(f"- Context: {diagnosis.get('context_path') or 'unknown'}")
    lines.append(f"- Selected files: {diagnosis['selected_count']}")
    lines.append("")
    lines.append("## Actions")
    for action in diagnosis["actions"]:
        lines.append(f"- {action}")
    lines.append("")
    lines.append("## Why Selected")
    for item in diagnosis["selection_explanations"]:
        why = "; ".join(item.get("why_selected", [])[:3]) or "selected by pack score"
        lines.append(f"- `{item['path']}`: {why}")
    lines.append("")
    lines.append("## Why Not Selected")
    if diagnosis["omission_summary"]:
        for item in diagnosis["omission_summary"]:
            lines.append(f"- {item['reason']} ({item['count']}): {item['why_not_selected']}")
    else:
        lines.append("- No omitted-file buckets recorded in latest pack metadata.")
    lines.append("")
    lines.append("## Largest Token Consumers")
    for item in diagnosis["largest_token_consumers"]:
        lines.append(f"- `{item['path']}` ({item['mode']}, {item['tokens']} tokens)")
    return "\n".join(lines).rstrip() + "\n"


def _selection_explanations_from_metadata(selected: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    explanations: list[dict[str, Any]] = []
    for item in selected[:limit]:
        reasons = [str(reason) for reason in (item.get("reasons") or [])]
        explanations.append({
            "path": item.get("path"),
            "mode": item.get("mode"),
            "score": item.get("score", 0),
            "why_selected": _diagnosis_why_selected(reasons),
            "top_reasons": reasons[:5],
        })
    return explanations


def _diagnosis_why_selected(reasons: list[str]) -> list[str]:
    why: list[str] = []
    if any(reason.startswith("GitHub PR file") for reason in reasons):
        why.append("GitHub PR file")
    if any(reason in {"modified", "staged", "recently modified"} for reason in reasons):
        why.append("changed or recently modified")
    if any(reason.startswith("content keyword match") for reason in reasons):
        why.append("content keyword match")
    if any(reason.startswith(("filename keyword match", "multi-term path match")) for reason in reasons):
        why.append("path/name matched task terms")
    if any(reason.startswith(("matched define:", "matched call:", "matched entrypoint:")) for reason in reasons):
        why.append("symbol or entrypoint matched task terms")
    if any(reason in {"implementation role match", "direct dependency of changed file"} for reason in reasons):
        why.append("implementation/dependency evidence")
    if any(reason.startswith("test for high-scoring") or reason == "has related tests" for reason in reasons):
        why.append("test coverage link")
    if not why and reasons:
        why.append(reasons[0])
    return why[:4]


def _omission_summary(meta: dict[str, Any]) -> list[dict[str, Any]]:
    handoff = meta.get("pack_handoff") or {}
    rows: list[dict[str, Any]] = []
    for source_key, group in (
        ("omitted_relevant", handoff.get("omitted_relevant") or {}),
        ("skipped_uncertain", handoff.get("skipped_uncertain") or {}),
    ):
        counts = group.get("reason_counts") or group.get("excluded_reason_counts") or {}
        if not isinstance(counts, dict):
            continue
        for reason, count in counts.items():
            rows.append({
                "source": source_key,
                "reason": str(reason),
                "count": int(count or 0),
                "why_not_selected": _explain_omission_reason(str(reason)),
            })
    return sorted(rows, key=lambda item: (-item["count"], item["reason"]))[:8]


def _explain_omission_reason(reason: str) -> str:
    lower = reason.lower().replace("_", " ")
    if "budget" in lower:
        return "matched task signals but did not fit current token budget"
    if "compressed context cap" in lower:
        return "weak or compressed context bucket was already full"
    if "summary score below floor" in lower:
        return "score was below the guarded summary threshold"
    if "docs disabled" in lower:
        return "documentation context is disabled for this pack mode"
    if "score too low" in lower:
        return "ranking score was too low for the task"
    if "test file lacks direct task evidence" in lower:
        return "test file matched broadly but lacked direct task evidence"
    if "marginal" in lower:
        return "replaced by a stronger candidate"
    return "planner did not have enough evidence to keep it selected"
