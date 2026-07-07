from __future__ import annotations

from datetime import datetime, timezone

from agentpack.core.models import ContextPack, SelectedFile
from agentpack.core.task_map import task_map_for_path


def _format_file_entry(sf: SelectedFile, pack: ContextPack) -> str:
    """Format a single selected file entry for the compact format."""
    lines: list[str] = [sf.path]
    lines.append(f"score: {int(sf.score)}")
    lines.append(f"include: {sf.include_mode}")
    task_item = task_map_for_path(pack.task_map, sf.path, "selected")
    if task_item.get("risk_level"):
        lines.append(f"risk: {str(task_item['risk_level']).upper()}")
    tests = task_item.get("tests_to_run") or []
    if isinstance(tests, list) and tests:
        lines.append(f"tests: {', '.join(str(test) for test in tests[:3])}")
    ref = str(task_item.get("retrieve_ref") or "")
    if ref:
        lines.append(f"retrieve: retrieve_context(block_id=\"{ref}\")")
    if sf.reasons:
        lines.append(f"why: {', '.join(sf.reasons)}")
    if sf.symbols:
        symbol_names = ", ".join(s.name for s in sf.symbols)
        lines.append(f"symbols: {symbol_names}")
    return "\n".join(lines)


def render_compact(pack: ContextPack) -> str:
    """Render a ContextPack into a structured compact format."""
    selected: list[SelectedFile] = []
    deps: list[SelectedFile] = []

    for sf in pack.selected_files:
        if sf.include_mode in ("full", "diff", "symbols", "skeleton"):
            selected.append(sf)
        else:
            deps.append(sf)

    now = datetime.now(timezone.utc).isoformat()
    sections: list[str] = []

    sections.append("# AgentPack Context")
    sections.append("")
    sections.append("<!-- agentpack:stable-prefix:start -->")
    sections.append("")
    sections.append("## instructions")
    sections.append("")
    sections.append("- Prefer selected files first.")
    sections.append("- Include modes: full, diff, symbols, skeleton, summary.")
    sections.append("- If task changes significantly, update `.agentpack/task.md`.")
    sections.append("- Run `agentpack session refresh` if context seems stale.")
    sections.append("")
    sections.append("<!-- agentpack:stable-prefix:end -->")
    sections.append("")
    sections.append(f"task: {pack.task}")
    sections.append(f"mode: {pack.mode}")
    sections.append(f"task_class: {pack.task_class}")
    sections.append(f"budget: {pack.token_estimate}/{pack.budget}")
    sections.append(f"generated: {now}")
    sections.append("")

    if pack.delta_summary:
        sections.append("## delta")
        sections.append("")
        sections.append(pack.delta_summary)
        sections.append("")

    if pack.repo_map:
        sections.append("## repo_map")
        sections.append("")
        sections.append(pack.repo_map)
        sections.append("")

    sections.append("## selected")
    sections.append("")
    if selected:
        for sf in selected:
            sections.append(_format_file_entry(sf, pack))
            sections.append("")
    else:
        sections.append("(none)")
        sections.append("")

    sections.append("## deps")
    sections.append("")
    if deps:
        for sf in deps:
            lines: list[str] = [sf.path]
            lines.append(f"score: {int(sf.score)}")
            lines.append("include: summary")
            task_item = task_map_for_path(pack.task_map, sf.path, "selected")
            if task_item.get("risk_level"):
                lines.append(f"risk: {str(task_item['risk_level']).upper()}")
            ref = str(task_item.get("retrieve_ref") or "")
            if ref:
                lines.append(f"retrieve: retrieve_context(block_id=\"{ref}\")")
            if sf.reasons:
                lines.append(f"why: {sf.reasons[0]}")
            sections.append("\n".join(lines))
            sections.append("")
    else:
        sections.append("(none)")
        sections.append("")

    files = pack.task_map.get("files") if isinstance(pack.task_map, dict) else None
    omitted = [item for item in files or [] if isinstance(item, dict) and item.get("kind") == "omitted"][:8]
    if omitted:
        sections.append("## omitted_refs")
        sections.append("")
        for item in omitted:
            lines = [str(item.get("path") or "")]
            lines.append(f"risk: {str(item.get('risk_level') or '').upper()}")
            ref = str(item.get("retrieve_ref") or "")
            if ref:
                lines.append(f"retrieve: retrieve_context(block_id=\"{ref}\")")
            why = item.get("why_selected") or []
            if isinstance(why, list) and why:
                lines.append(f"why: {why[0]}")
            sections.append("\n".join(lines))
            sections.append("")

    return "\n".join(sections)
