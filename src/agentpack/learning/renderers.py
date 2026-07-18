from __future__ import annotations

import html

from agentpack.core.models import Citation
from agentpack.learning.models import LearningReport


def learning_report_to_dict(report: LearningReport) -> dict:
    return report.model_dump(mode="json")


def render_agent_lessons_markdown(report: LearningReport) -> str:
    if not report.agent_lessons:
        return "# Agent Lessons\n\nNo agent lessons captured yet.\n"
    lines = ["# Agent Lessons", "", "Use these repo-specific lessons in future AgentPack tasks.", ""]
    for lesson in report.agent_lessons:
        lines.append(f"- {lesson.rule}")
        if lesson.evidence_files:
            lines.append("  Evidence: " + ", ".join(f"`{path}`" for path in lesson.evidence_files))
        if lesson.reason:
            lines.append(f"  Reason: {lesson.reason}")
    lines.append("")
    return "\n".join(lines)


def render_llm_prompt_markdown(report: LearningReport) -> str:
    lines = [
        "# AgentPack Learning Prompt",
        "",
        "Create a source-backed learning summary for this coding task.",
        "Use only the changed-file evidence, concepts, risks, tests, and agent lessons below.",
        "Do not invent files, technologies, or decisions not present here.",
        "",
        render_learning_markdown(report),
    ]
    return "\n".join(lines)


def render_pr_comment_markdown(report: LearningReport) -> str:
    lines = ["## Learning Summary", ""]
    lines.extend(report.summary[:3])
    if report.concepts:
        lines.extend(["", "### Concepts"])
        lines.extend(f"- {concept}" for concept in report.concepts[:5])
    if report.risks:
        lines.extend(["", "### Review Risks"])
        lines.extend(f"- {risk}" for risk in report.risks[:3])
    if report.next_practice:
        lines.extend(["", "### Next Practice", report.next_practice])
    lines.append("")
    return "\n".join(lines)


def render_provider_preview_markdown(report: LearningReport) -> str:
    lines = [
        "# AgentPack Provider Preview",
        "",
        "This is the bounded, source-backed learning payload that can be sent to an optional provider.",
        "No provider call is made by this preview.",
        "",
        f"Task: {report.task}",
        f"Scope: {report.scope}",
    ]
    if report.issue_references:
        lines.append("Issue references: " + ", ".join(report.issue_references))
    lines.extend(["", "## Changed File Evidence"])
    for source in report.source_files:
        concepts = ", ".join(source.concepts) if source.concepts else "none"
        lines.append(f"- `{source.path}` ({source.change_kind}) concepts: {concepts}")
    lines.extend(["", "## Concepts"])
    lines.extend(f"- {concept}" for concept in report.concepts)
    lines.extend(["", "## Existing Agent Lessons"])
    if report.agent_lessons:
        lines.extend(f"- {lesson.rule}" for lesson in report.agent_lessons)
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def render_drills_markdown(drills: list[str]) -> str:
    lines = ["# AgentPack Practice Drills", ""]
    if not drills:
        lines.append("No skill evidence captured yet.")
    else:
        lines.extend(f"{idx}. {drill}" for idx, drill in enumerate(drills, start=1))
    lines.append("")
    return "\n".join(lines)


def render_quality_markdown(report: LearningReport, score: int, issues: list[str]) -> str:
    lines = [
        "# AgentPack Learning Quality",
        "",
        f"Score: {score}",
        f"Citation coverage: {report.citation_coverage:.3f}",
        f"Task: {report.task}",
        "",
        "## Issues",
    ]
    if issues:
        lines.extend(f"- {issue}" for issue in issues)
    else:
        lines.append("- none")
    if report.invalid_citations:
        lines.extend(["", "## Invalid Citations"])
        lines.extend(f"- {item}" for item in report.invalid_citations[:20])
    if report.uncited_claims:
        lines.extend(["", "## Uncited Claims"])
        lines.extend(f"- {item}" for item in report.uncited_claims[:20])
    lines.append("")
    return "\n".join(lines)


def render_dashboard_html(report: LearningReport) -> str:
    concepts = "".join(f'<span class="chip">{html.escape(concept)}</span>' for concept in report.concepts) or '<span class="muted">None detected</span>'
    coach_request = html.escape(report.learning_request or "Ask for a lesson, quiz, interview, or failure drill with agentpack learn \"...\"")
    coach_mode = html.escape(report.coach_mode.replace("-", " ").title())
    topics = "".join(
        '<article class="topic-row">'
        '<div class="topic-main">'
        f"<h3>{html.escape(topic.title)}</h3>"
        f"<p>{html.escape(topic.why)}</p>"
        f'<div class="chips">{_file_chips(topic.concepts)}</div>'
        "</div>"
        '<div class="coach-panel">'
        f"{_questions_html(topic.questions)}"
        f'<details><summary>Study prompt</summary><pre class="copy-prompt">{html.escape(topic.prompt)}</pre></details>'
        "</div>"
        "</article>"
        for topic in report.learning_topics
    ) or '<p class="muted">No learning topics generated.</p>'
    cards = "".join(
        '<article class="learning-card">'
        f"<h3>{html.escape(card.title)}</h3>"
        f"<p>{html.escape(card.body)}</p>"
        f'<p class="evidence"><strong>Evidence</strong><br>{_file_chips(card.files)}</p>'
        "</article>"
        for card in report.learning_cards
    ) or '<p class="muted">No learning cards generated.</p>'
    lessons = "".join(
        "<li>"
        f"<strong>{html.escape(lesson.rule)}</strong>"
        f'<br><small>{html.escape(", ".join(lesson.evidence_files) or "no evidence")}</small>'
        "</li>"
        for lesson in report.agent_lessons
    ) or "<li>No agent lessons generated.</li>"
    source_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(source.path)}</code></td>"
        f"<td>{html.escape(source.change_kind)}</td>"
        f"<td>{html.escape(source.why)}</td>"
        f"<td>{_file_chips(source.concepts)}</td>"
        "</tr>"
        for source in report.source_files
    ) or '<tr><td colspan="4">No changed file evidence found.</td></tr>'
    risks = "".join(f"<li>{html.escape(risk)}</li>" for risk in report.risks) or "<li>No risks captured.</li>"
    tests = "".join(f"<li>{html.escape(test)}</li>" for test in report.tests) or "<li>No tests captured.</li>"
    drills = html.escape(report.next_practice or "No next practice generated.")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentPack Learn Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --glass: rgba(255, 255, 255, 0.82);
      --glass-strong: rgba(255, 255, 255, 0.94);
      --panel: #ffffff;
      --panel-soft: #f8fafc;
      --border: #d8dee8;
      --text: #131820;
      --muted: #526071;
      --focus: #0f62fe;
      --accent: #2157bd;
      --accent-strong: #173f8c;
      --accent-bg: rgba(225, 235, 255, 0.9);
      --code: rgba(231, 237, 244, 0.92);
      --shadow: 0 1px 2px rgba(19, 24, 32, 0.06), 0 12px 32px rgba(19, 24, 32, 0.07);
      --shadow-soft: 0 1px 1px rgba(19, 24, 32, 0.04), 0 10px 24px rgba(19, 24, 32, 0.055);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; background: linear-gradient(180deg, #ffffff 0, var(--bg) 280px); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 16px; line-height: 1.5; }}
    a:focus-visible, button:focus-visible, [tabindex]:focus-visible {{ outline: 3px solid rgba(15, 98, 254, 0.34); outline-offset: 3px; }}
    .skip-link {{ position: absolute; left: 16px; top: -48px; z-index: 4; padding: 10px 12px; border-radius: 8px; background: var(--text); color: #fff; text-decoration: none; }}
    .skip-link:focus {{ top: 12px; }}
    .topbar {{ position: sticky; top: 0; z-index: 2; background: rgba(255,255,255,0.76); border-bottom: 1px solid rgba(137,151,172,0.24); box-shadow: 0 1px 0 rgba(111,126,148,0.12); backdrop-filter: blur(18px) saturate(150%); }}
    .topbar-inner {{ max-width: 1120px; margin: 0 auto; padding: 10px 24px; display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    .brand {{ font-weight: 760; color: var(--accent-strong); }}
    nav {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    nav a {{ min-height: 36px; display: inline-flex; align-items: center; color: var(--muted); text-decoration: none; font-size: 13px; font-weight: 560; padding: 7px 11px; border: 1px solid transparent; border-radius: 999px; transition: background-color 160ms ease, border-color 160ms ease, color 160ms ease; }}
    nav a:hover, nav a:focus-visible {{ background: rgba(255,255,255,0.82); border-color: var(--border); color: var(--text); }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    header.hero {{ padding: 30px 0 24px; display: grid; grid-template-columns: minmax(0, 1fr) minmax(240px, 260px); gap: 24px; align-items: end; border-bottom: 1px solid rgba(137,151,172,0.24); }}
    h1, h2, h3 {{ line-height: 1.2; margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 34px; font-weight: 780; }}
    h2 {{ font-size: 18px; font-weight: 720; }}
    h3 {{ font-size: 15px; font-weight: 680; }}
    section {{ margin: 22px 0; }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 760; text-transform: uppercase; letter-spacing: 0.04em; margin: 0 0 8px; }}
    .subtitle {{ color: var(--muted); margin: 10px 0 0; max-width: 760px; font-size: 15px; line-height: 1.6; }}
    .meta-stack {{ display: grid; gap: 8px; }}
    .meta {{ color: var(--muted); margin: 0; padding: 12px 14px; border: 1px solid rgba(255,255,255,0.72); border-radius: 8px; background: var(--glass); box-shadow: var(--shadow-soft); backdrop-filter: blur(12px) saturate(130%); }}
    .meta strong {{ color: var(--text); }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }}
    .metric {{ background: var(--panel); border: 1px solid rgba(255,255,255,0.76); border-radius: 8px; padding: 14px; min-height: 78px; box-shadow: var(--shadow-soft); backdrop-filter: blur(14px) saturate(135%); }}
    .metric strong {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .metric span {{ display: block; margin-top: 6px; font-size: 22px; font-weight: 720; }}
    .section {{ padding: 6px 0 10px; }}
    .section-header {{ margin-bottom: 12px; display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }}
    .section-body {{ padding: 0; }}
    .coach-hero {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, 340px); gap: 14px; margin: 18px 0; }}
    .coach-card {{ padding: 16px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); box-shadow: var(--shadow-soft); }}
    .coach-card strong {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .coach-card span {{ display: block; margin-top: 8px; font-size: 20px; font-weight: 720; }}
    .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
    .learning-card {{ border: 1px solid rgba(255,255,255,0.76); border-radius: 8px; padding: 14px; background: var(--panel); box-shadow: var(--shadow-soft); backdrop-filter: blur(14px) saturate(135%); }}
    .learning-card p {{ margin: 10px 0 0; }}
    .topic-stack {{ display: grid; gap: 12px; }}
    .topic-row {{ display: grid; grid-template-columns: minmax(220px, 0.42fr) minmax(0, 1fr); gap: 14px; padding: 16px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); box-shadow: var(--shadow-soft); }}
    .topic-main p {{ margin: 8px 0 12px; color: var(--muted); }}
    .coach-panel {{ display: grid; gap: 10px; }}
    .question-card {{ padding: 12px; border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 8px; background: var(--panel-soft); }}
    .question-card p {{ margin: 0 0 8px; }}
    .expected {{ margin: 0; color: var(--muted); font-size: 13px; }}
    details {{ border: 1px solid var(--border); border-radius: 8px; background: var(--panel-soft); }}
    summary {{ cursor: pointer; padding: 10px 12px; color: var(--muted); font-size: 13px; font-weight: 680; }}
    .copy-label {{ color: var(--muted); font-size: 12px; font-weight: 680; text-transform: uppercase; letter-spacing: 0.04em; }}
    .copy-prompt {{ margin: 0; max-height: 200px; overflow: auto; white-space: pre-wrap; border-top: 1px solid var(--border); padding: 12px; background: #fff; color: var(--text); font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; user-select: all; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .chip {{ display: inline-flex; align-items: center; border: 1px solid rgba(148,163,184,0.48); border-radius: 999px; padding: 2px 8px; background: var(--accent-bg); color: var(--accent); font-size: 12px; margin: 2px 4px 2px 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid rgba(255,255,255,0.76); border-radius: 8px; background: var(--glass-strong); box-shadow: var(--shadow-soft); backdrop-filter: blur(14px) saturate(135%); }}
    th, td {{ border-bottom: 1px solid rgba(137,151,172,0.24); padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ background: rgba(248,250,252,0.94); font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}
    tr:last-child td {{ border-bottom: 0; }}
    code {{ background: var(--code); padding: 1px 4px; border-radius: 4px; word-break: break-word; }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin: 7px 0; }}
    small, .muted {{ color: var(--muted); }}
    .practice {{ margin: 0; padding: 12px 14px; border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 8px; background: var(--glass); color: var(--muted); backdrop-filter: blur(12px) saturate(125%); }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      nav a {{ transition: none; }}
    }}
    @supports not ((backdrop-filter: blur(1px))) {{
      .topbar, .meta, .metric, .learning-card, .practice, .table-wrap {{ background: #ffffff; }}
    }}
    @media (max-width: 760px) {{
      .topbar-inner {{ padding: 10px 16px; align-items: flex-start; flex-direction: column; }}
      main {{ padding: 16px; }}
      header.hero, .coach-hero, .topic-row {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
    }}
  </style>
</head>
<body>
<a class="skip-link" href="#main">Skip to task coach</a>
<div class="topbar">
  <div class="topbar-inner">
    <div class="brand">AgentPack</div>
    <nav aria-label="Learning dashboard sections">
      <a href="#concepts">Concepts</a>
      <a href="#files">Files</a>
      <a href="#topics">Topics</a>
      <a href="#cards">Cards</a>
      <a href="#lessons">Lessons</a>
      <a href="#practice">Practice</a>
    </nav>
  </div>
</div>
<main id="main">
  <header class="hero">
    <div>
    <p class="eyebrow">Task coach</p>
    <h1>Learn while agent works</h1>
    <p class="subtitle">{html.escape(report.task)}</p>
    </div>
    <div class="meta-stack">
      <p class="meta"><strong>Scope</strong><br><span class="muted">{html.escape(report.scope)}</span></p>
      <p class="meta"><strong>Since</strong><br><span class="muted">{html.escape(report.since or "not specified")}</span></p>
    </div>
  </header>
  <div class="coach-hero">
    <section class="coach-card"><strong>Current request</strong><span>{coach_request}</span></section>
    <section class="coach-card"><strong>Coach mode</strong><span>{coach_mode}</span></section>
  </div>
  <div class="metric-grid">
    <section class="metric"><strong>Evidence Files</strong><span>{len(report.source_files)}</span></section>
    <section class="metric"><strong>Concepts</strong><span>{len(report.concepts)}</span></section>
    <section class="metric"><strong>Topics</strong><span>{len(report.learning_topics)}</span></section>
    <section class="metric"><strong>Questions</strong><span>{sum(len(topic.questions) for topic in report.learning_topics)}</span></section>
  </div>
  <section id="concepts" class="section"><div class="section-header"><h2>Concepts</h2><small>Detected from changed-file evidence</small></div><div class="section-body chips">{concepts}</div></section>
  <section id="files" class="section"><div class="section-header"><h2>Changed File Evidence</h2><small>Source-backed learning inputs</small></div><div class="section-body"><div class="table-wrap"><table><thead><tr><th>Path</th><th>Change</th><th>Why</th><th>Concepts</th></tr></thead><tbody>{source_rows}</tbody></table></div></div></section>
  <section id="topics" class="section"><div class="section-header"><h2>Coach Queue</h2><small>Answer first, then compare against expected points</small></div><div class="section-body topic-stack">{topics}</div></section>
  <section id="cards" class="section"><div class="section-header"><h2>Learning Cards</h2><small>Review-ready summaries</small></div><div class="section-body card-grid">{cards}</div></section>
  <section class="section"><div class="section-header"><h2>Risks and Tests</h2><small>What to review next</small></div><div class="section-body card-grid"><article class="learning-card"><h3>Risks</h3><ul>{risks}</ul></article><article class="learning-card"><h3>Tests</h3><ul>{tests}</ul></article></div></section>
  <section id="lessons" class="section"><div class="section-header"><h2>Agent Lessons</h2><small>Rules captured for future runs</small></div><div class="section-body"><ul>{lessons}</ul></div></section>
  <section id="practice" class="section"><div class="section-header"><h2>Next Practice</h2><small>One practical follow-up</small></div><div class="section-body"><p class="practice">{drills}</p></div></section>
</main>
</body>
</html>
"""


def _file_chips(values: list[str]) -> str:
    return "".join(f'<span class="chip">{html.escape(value)}</span>' for value in values) or '<span class="muted">none</span>'


def _questions_html(questions) -> str:
    if not questions:
        return '<p class="muted">No coach questions generated.</p>'
    parts: list[str] = []
    for question in questions[:3]:
        expected = ", ".join(question.expected_points) if question.expected_points else "task-specific answer"
        evidence = _file_chips(question.evidence_files)
        parts.append(
            '<div class="question-card">'
            f"<p><strong>{html.escape(question.mode.title())}</strong> {html.escape(question.question)}</p>"
            f'<p class="expected">Expected: {html.escape(expected)}</p>'
            f'<div class="chips">{evidence}</div>'
            "</div>"
        )
    return "".join(parts)


def render_team_lessons_markdown(report: LearningReport) -> str:
    lines = [
        "# AgentPack Team Lessons",
        "",
        "Opt-in repo lessons derived from changed-file evidence. This export omits personal skill history.",
        "",
        "## Concepts",
    ]
    if report.concepts:
        lines.extend(f"- {concept}" for concept in report.concepts)
    else:
        lines.append("- none")
    lines.extend(["", "## Agent Lessons"])
    if report.agent_lessons:
        for lesson in report.agent_lessons:
            lines.append(f"- {lesson.rule}")
            if lesson.evidence_files:
                lines.append("  Evidence: " + ", ".join(f"`{path}`" for path in lesson.evidence_files))
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def render_learning_markdown(report: LearningReport) -> str:
    lines: list[str] = [
        "# AgentPack Learning Summary",
        "",
        f"**Task:** {report.task}",
        f"**Scope:** {report.scope}",
    ]
    if report.learning_request:
        lines.append(f"**Learning request:** {report.learning_request}")
    if report.coach_mode:
        lines.append(f"**Coach mode:** {report.coach_mode}")
    if report.since:
        lines.append(f"**Since:** `{report.since}`")
    if report.issue_references:
        lines.append("**Issue references:** " + ", ".join(report.issue_references))
    lines.extend(["", "## Summary"])
    lines.extend(f"- {item}" for item in report.summary)
    lines.extend(["", "## Changed Files"])
    for source in report.source_files:
        concepts = ", ".join(source.concepts) if source.concepts else "none detected"
        lines.append(f"- `{source.path}` ({source.change_kind}) - {source.why} Concepts: {concepts}.")
    lines.extend(["", "## Concepts"])
    lines.extend(f"- {concept}" for concept in report.concepts)
    lines.extend(["", "## Decisions"])
    lines.extend(f"- {decision}" for decision in report.decisions)
    lines.extend(["", "## Risks"])
    lines.extend(f"- {risk}" for risk in report.risks)
    lines.extend(["", "## Tests"])
    lines.extend(f"- {test}" for test in report.tests)
    if report.claim_citations:
        lines.extend(["", "## Claim Citations"])
        for claim_id in sorted(report.claim_citations):
            refs = _citation_refs(report.claim_citations[claim_id])
            if refs:
                lines.append(f"- `{claim_id}`: {refs}")
    lines.extend(["", "## Learning Topics"])
    for topic in report.learning_topics:
        lines.append(f"### {topic.title}")
        lines.append(topic.why)
        if topic.files:
            lines.append("Evidence: " + ", ".join(f"`{path}`" for path in topic.files))
        if topic.concepts:
            lines.append("Concepts: " + ", ".join(topic.concepts))
        if topic.questions:
            lines.append("")
            lines.append("Coach questions:")
            for question in topic.questions:
                points = ", ".join(question.expected_points) if question.expected_points else "task-specific answer"
                files = ", ".join(f"`{path}`" for path in question.evidence_files) if question.evidence_files else "no evidence files"
                lines.append(f"- [{question.mode}] {question.question}")
                lines.append(f"  Expected points: {points}")
                lines.append(f"  Evidence: {files}")
        lines.extend(["", "Copy-ready study prompt:", "```text", topic.prompt, "```", ""])
    lines.extend(["", "## Skill Evidence"])
    for item in report.skill_evidence:
        files = ", ".join(f"`{path}`" for path in item.evidence_files) if item.evidence_files else "no changed file evidence"
        lines.append(f"- {item.skill}: confidence {item.confidence}; files: {files}")
    lines.extend(["", "## Learning Cards"])
    for card in report.learning_cards:
        lines.append(f"### {card.title}")
        lines.append(card.body)
        if card.files:
            lines.append("Files: " + ", ".join(f"`{path}`" for path in card.files))
        lines.append("")
    lines.extend(["## Agent Lessons"])
    for lesson in report.agent_lessons:
        lines.append(f"- {lesson.rule}")
        if lesson.evidence_files:
            lines.append("  Evidence: " + ", ".join(f"`{path}`" for path in lesson.evidence_files))
        if lesson.reason:
            lines.append(f"  Reason: {lesson.reason}")
    lines.append("")
    lines.extend(["## Quiz"])
    for idx, item in enumerate(report.quiz, start=1):
        lines.append(f"{idx}. {item.question}")
        lines.append(f"   - Answer: {item.answer}")
    lines.extend(["", "## Next Practice", report.next_practice, ""])
    return "\n".join(lines)


def _citation_refs(citations: list[Citation]) -> str:
    refs: list[str] = []
    for citation in citations:
        if citation.kind == "external":
            if citation.url:
                refs.append(citation.url)
            continue
        if not citation.path:
            continue
        if citation.start_line is None:
            refs.append(f"`{citation.path}`")
            continue
        suffix = f"{citation.start_line}"
        if citation.end_line and citation.end_line != citation.start_line:
            suffix += f"-{citation.end_line}"
        refs.append(f"`{citation.path}:{suffix}`")
    return ", ".join(refs)
