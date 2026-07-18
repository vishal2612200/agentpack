from __future__ import annotations

from agentpack.router.models import RouteResult


def build_agent_prompt(result: RouteResult) -> str:
    lines = [
        "Use Agentpack route result before editing.",
        "",
        f"Task: {result.task}",
        f"Recommended interaction mode: {result.recommended_interaction_mode}",
        f"Mode reason: {result.mode_reason}",
        f"Current agent: {result.current_agent}",
        f"Reviewer agent: {result.reviewer_agent}",
        f"Task mode: {result.task_mode} (confidence {result.task_mode_confidence:.2f})",
        "",
        "Read these files first:",
    ]
    if result.selected_files:
        lines.extend(f"- {item['path']}" for item in result.selected_files[:10])
    else:
        lines.append("- No files selected.")

    if result.selection_explanations:
        lines += ["", "Why these files:"]
        for item in result.selection_explanations[:6]:
            why = "; ".join(item.get("why_selected", [])[:3]) or "selected by route score"
            lines.append(f"- {item['path']}: {why}")

    if result.omitted_files:
        lines += ["", "Why not selected:"]
        for item in result.omitted_files[:5]:
            why = "; ".join(item.get("why_not_selected", [])[:2]) or "not selected"
            lines.append(f"- {item['path']}: {why}")

    if result.observer_notes:
        lines += ["", "Observer priors (advisory):"]
        for item in result.observer_notes[:5]:
            confidence = float(item.get("confidence") or 0.0)
            lines.append(f"- {item.get('path', '')}: {item.get('reason', '')} (confidence {confidence:.2f})")

    lines += [
        "",
        "Evidence contract:",
        "- Do not make repo-code claims without `path:line` evidence.",
        "- If a claim is not verified from source, put it under Open Questions.",
        "- Review findings need a concrete location and supporting evidence citation.",
        "- Prefer `.agentpack/citations.json` when present to inspect packed source provenance.",
    ]

    if result.applied_rules:
        lines += ["", "Apply these rules:"]
        lines.extend(f"- {item.rule.name} ({item.rule.path})" for item in result.applied_rules)

    if result.baseline_skills:
        lines += ["", "Baseline guidance:"]
        lines.extend(
            f"- Load `{item.skill.name}` ({item.skill.path}) for standing guidance."
            for item in result.baseline_skills
        )

    if result.selected_skills:
        lines += ["", "Skill Plan:"]
        for idx, item in enumerate(result.selected_skills, start=1):
            reason = "; ".join(item.reasons[:2]) if item.reasons else "recommended by route"
            lines.extend([
                f"{idx}. Use `{item.skill.name}` because {reason}.",
                f"   - Load: {item.skill.path}",
                "   - Apply before editing matching files.",
            ])

    if result.safety_warnings:
        lines += ["", "Safety warnings:"]
        lines.extend(f"- {warning}" for warning in result.safety_warnings)

    if result.prompt_quality_warnings:
        lines += ["", "Prompt quality warnings:"]
        lines.extend(f"- {warning}" for warning in result.prompt_quality_warnings)
        if result.recommended_prompt_template:
            lines += ["", "Better prompt template:"]
            lines.extend(f"- {item}" for item in result.recommended_prompt_template)

    if result.routing_notes:
        lines += ["", "Routing notes:"]
        lines.extend(f"- {note}" for note in result.routing_notes)

    if result.evidence_checklist:
        lines += ["", "Evidence checklist:"]
        lines.extend(f"- {item}" for item in result.evidence_checklist)

    if result.suggested_commands:
        lines += ["", "Consider these commands; do not run blindly:"]
        lines.extend(f"- {item.command}" for item in result.suggested_commands)

    return "\n".join(lines)


def render_plain(result: RouteResult) -> str:
    lines = [
        "Task:",
        result.task,
        "",
        "Relevant files:",
    ]
    if result.selected_files:
        for item in result.selected_files:
            why = ", ".join(item.get("reasons", [])[:2])
            suffix = f" — {why}" if why else ""
            lines.append(f"- {item['path']} ({item.get('include_mode', 'unknown')}){suffix}")
    else:
        lines.append("- none")

    lines += ["", "Applied rules:"]
    if result.applied_rules:
        for item in result.applied_rules:
            reason = "; ".join(item.reasons)
            suffix = f" — {reason}" if reason else ""
            lines.append(f"- {item.rule.name} ({item.rule.path}){suffix}")
    else:
        lines.append("- none")

    lines += ["", "Why selected:"]
    if result.selection_explanations:
        for item in result.selection_explanations[:8]:
            why = "; ".join(item.get("why_selected", [])[:3]) or "selected by route score"
            lines.append(f"- {item['path']}: {why}")
    else:
        lines.append("- none")

    lines += ["", "Why not selected:"]
    if result.omitted_files:
        for item in result.omitted_files[:8]:
            why = "; ".join(item.get("why_not_selected", [])[:3]) or "not selected"
            lines.append(f"- {item['path']}: {why}")
    else:
        lines.append("- none")

    lines += ["", "Observer priors:"]
    if result.observer_notes:
        for item in result.observer_notes[:8]:
            confidence = float(item.get("confidence") or 0.0)
            evidence = ", ".join(str(value) for value in (item.get("evidence") or [])[:2])
            suffix = f" — {evidence}" if evidence else ""
            lines.append(f"- {item.get('path', '')} ({confidence:.2f}) {item.get('reason', '')}{suffix}")
    else:
        lines.append("- none")

    lines += ["", "Recommended skills:"]
    if result.selected_skills:
        for item in result.selected_skills:
            reason = "; ".join(item.reasons[:2])
            suffix = f" — {reason}" if reason else ""
            lines.append(
                f"- {item.skill.name} ({item.skill.side_effect_level}, confidence {item.confidence:.2f}, score {item.score:.0f}){suffix}"
            )
    else:
        lines.append("- none")

    lines += ["", "Baseline skills:"]
    if result.baseline_skills:
        for item in result.baseline_skills:
            lines.append(
                f"- {item.skill.name} ({item.skill.side_effect_level}, confidence {item.confidence:.2f})"
            )
    else:
        lines.append("- none")

    lines += ["", "Suggested commands:"]
    if result.suggested_commands:
        for item in result.suggested_commands:
            lines.append(f"- {item.command} — {item.reason}")
    else:
        lines.append("- none")

    lines += ["", "Safety:"]
    if result.safety_warnings:
        lines.extend(f"- {warning}" for warning in result.safety_warnings)
    else:
        lines.append("- No external side-effect skills selected.")

    lines += ["", "Routing:"]
    lines.append(f"- recommended_interaction_mode: {result.recommended_interaction_mode}")
    lines.append(f"- mode_reason: {result.mode_reason}")
    lines.append(f"- current_agent: {result.current_agent}")
    lines.append(f"- reviewer_agent: {result.reviewer_agent}")
    lines.append(f"- task_mode: {result.task_mode} (confidence {result.task_mode_confidence:.2f})")
    if result.task_mode_signals:
        lines.append(f"- signals: {', '.join(result.task_mode_signals[:4])}")
    for note in result.routing_notes:
        lines.append(f"- {note}")
    if result.evidence_checklist:
        lines.append("- evidence checklist:")
        lines.extend(f"  - {item}" for item in result.evidence_checklist)
    lines.append("- evidence contract: repo-code claims require `path:line`; unverified claims stay as open questions")
    if result.prompt_quality_warnings:
        lines.append("- prompt quality warnings:")
        lines.extend(f"  - {warning}" for warning in result.prompt_quality_warnings)
    if result.recommended_prompt_template:
        lines.append("- prompt template:")
        lines.extend(f"  - {item}" for item in result.recommended_prompt_template)

    lines += ["", "Agent prompt:", result.agent_prompt]
    return "\n".join(lines)
