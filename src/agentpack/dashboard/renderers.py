from __future__ import annotations

import html
from collections.abc import Iterable

from agentpack.dashboard.models import DashboardSnapshot, SkillRow


MAX_RENDERED_FILES = 50
MAX_RENDERED_MISSES = 20


def render_dashboard_html(snapshot: DashboardSnapshot) -> str:
    """Render the legacy static dashboard fallback.

    New dashboard features should target the bundled React/Vite cockpit under
    ``frontend/dashboard`` and the JSON contracts in ``dashboard.graph``.
    """
    files = _selected_file_rows(snapshot)
    task_map = _task_map_rows(snapshot)
    skills = _skill_rows(snapshot.skills.task_specific, "task-specific") + _skill_rows(snapshot.skills.baseline, "baseline")
    if not skills:
        skills = '<tr><td colspan="7">No skill recommendations found.</td></tr>'
    quality_strip = _quality_strip(snapshot)
    integrations = _integrations_panel(snapshot)
    skills_inventory = _skills_inventory_panel(snapshot)
    learning = _learning_rows(snapshot)
    observer = _observer_rows(snapshot)
    benchmarks = _benchmark_rows(snapshot)
    misses = _miss_rows(snapshot)
    actions = _action_rows(snapshot)
    loop = _loop_panel(snapshot)
    status_class = _status_class(snapshot.context.status)
    task_text = snapshot.task.text or "No task found"
    generated_at = snapshot.generated_at or snapshot.context.generated_at or "unknown"
    git_label = " ".join(part for part in [snapshot.project.branch or "unknown", snapshot.project.git_sha] if part).strip()

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentPack Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --surface: #ffffff;
      --surface-muted: #f8fafc;
      --panel: #ffffff;
      --panel-soft: #f8fafc;
      --border: #d8dee8;
      --border-strong: #bdc7d5;
      --text: #131820;
      --muted: #526071;
      --subtle: #768293;
      --focus: #0f62fe;
      --good: #137047;
      --good-bg: rgba(222, 245, 231, 0.9);
      --warn: #8a6100;
      --warn-bg: rgba(255, 242, 202, 0.92);
      --bad: #b4232c;
      --bad-bg: rgba(255, 232, 232, 0.92);
      --accent: #2157bd;
      --accent-strong: #173f8c;
      --accent-bg: #e8f0ff;
      --code: #edf2f7;
      --shadow: 0 1px 2px rgba(19, 24, 32, 0.06), 0 10px 28px rgba(19, 24, 32, 0.06);
      --shadow-soft: 0 1px 1px rgba(19, 24, 32, 0.04), 0 8px 20px rgba(19, 24, 32, 0.045);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; background: linear-gradient(180deg, #ffffff 0, var(--bg) 260px); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 16px; line-height: 1.5; }}
    a:focus-visible, button:focus-visible, [tabindex]:focus-visible {{ outline: 3px solid rgba(15, 98, 254, 0.34); outline-offset: 3px; }}
    .skip-link {{ position: absolute; left: 16px; top: -48px; z-index: 4; padding: 10px 12px; border-radius: 8px; background: var(--text); color: #fff; text-decoration: none; }}
    .skip-link:focus {{ top: 12px; }}
    .topbar {{ position: sticky; top: 0; z-index: 2; background: rgba(255,255,255,0.92); border-bottom: 1px solid var(--border); box-shadow: 0 1px 0 rgba(111,126,148,0.08); backdrop-filter: blur(14px); }}
    .topbar-inner {{ max-width: 1240px; margin: 0 auto; padding: 10px 24px; display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    .brand {{ font-weight: 760; letter-spacing: 0; color: var(--accent-strong); }}
    nav {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    nav a {{ min-height: 36px; display: inline-flex; align-items: center; color: var(--muted); text-decoration: none; font-size: 13px; font-weight: 560; padding: 7px 11px; border: 1px solid transparent; border-radius: 999px; transition: background-color 160ms ease, border-color 160ms ease, color 160ms ease; }}
    nav a:hover, nav a:focus-visible {{ background: var(--surface-muted); border-color: var(--border); color: var(--text); }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 24px; }}
    header.hero {{ padding: 30px 0 18px; display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, 300px); gap: 24px; align-items: end; }}
    h1, h2, h3 {{ line-height: 1.2; margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 34px; font-weight: 780; }}
    h2 {{ font-size: 18px; font-weight: 720; }}
    h3 {{ font-size: 15px; font-weight: 680; }}
    section {{ margin: 22px 0; }}
    .section {{ padding: 6px 0 10px; }}
    .section-header {{ margin-bottom: 12px; display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }}
    .section-body {{ padding: 0; }}
    .section-body > h3 {{ margin: 18px 0 8px; color: var(--text); }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 760; text-transform: uppercase; letter-spacing: 0.04em; margin: 0 0 8px; }}
    .subtitle {{ color: var(--muted); margin: 10px 0 0; max-width: 760px; font-size: 15px; line-height: 1.6; }}
    .meta-stack {{ display: grid; gap: 8px; }}
    .meta {{ color: var(--muted); margin: 0; padding: 12px 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); box-shadow: var(--shadow-soft); }}
    .meta strong {{ color: var(--text); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 10px; }}
    .quality-strip {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 1px; overflow: hidden; margin: 16px 0 18px; border: 1px solid var(--border); border-radius: 8px; background: var(--border); box-shadow: var(--shadow-soft); }}
    .quality-item {{ min-width: 0; padding: 12px 14px; background: var(--surface); }}
    .quality-item strong {{ display: block; color: var(--subtle); font-size: 11px; font-weight: 740; text-transform: uppercase; letter-spacing: 0.04em; }}
    .quality-item span {{ display: block; margin-top: 5px; color: var(--text); font-size: 17px; font-weight: 720; overflow-wrap: anywhere; }}
    .metric {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px; min-height: 78px; box-shadow: var(--shadow-soft); }}
    .metric strong {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .metric span {{ display: block; margin-top: 6px; font-size: 22px; font-weight: 720; }}
    .metric.compact span {{ font-size: 17px; }}
    .callout {{ margin-top: 12px; padding: 12px 14px; border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 8px; background: var(--surface); color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); box-shadow: var(--shadow-soft); }}
    th, td {{ border-bottom: 1px solid rgba(137,151,172,0.24); padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ background: var(--surface-muted); font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}
    tr:last-child td {{ border-bottom: 0; }}
    tbody tr:hover {{ background: rgba(234,240,255,0.38); }}
    code {{ background: var(--code); padding: 1px 4px; border-radius: 4px; word-break: break-word; }}
    ul {{ padding-left: 18px; margin: 0; }}
    li {{ margin: 7px 0; }}
    small {{ color: var(--muted); }}
    .pill {{ display: inline-flex; align-items: center; border: 1px solid var(--border); border-radius: 999px; padding: 2px 8px; background: var(--surface); color: var(--muted); font-size: 12px; white-space: nowrap; }}
    .domain-list {{ display: flex; flex-wrap: wrap; gap: 8px; padding: 0; margin: 0; list-style: none; }}
    .domain-list li {{ margin: 0; }}
    .domain-item {{ display: inline-flex; align-items: center; gap: 8px; min-height: 32px; padding: 5px 10px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); color: var(--muted); }}
    .domain-item strong {{ color: var(--text); font-variant-numeric: tabular-nums; }}
    .inventory-list {{ display: grid; gap: 10px; }}
    .inventory-card {{ border: 1px solid var(--border); border-radius: 8px; background: var(--surface); box-shadow: var(--shadow-soft); overflow: hidden; }}
    .inventory-card-header {{ display: grid; grid-template-columns: minmax(180px, 0.8fr) minmax(220px, 1fr) auto; gap: 12px; align-items: start; padding: 14px 16px; border-bottom: 1px solid var(--border); background: var(--surface-muted); }}
    .inventory-title {{ display: grid; gap: 6px; min-width: 0; }}
    .inventory-title strong {{ font-size: 15px; overflow-wrap: anywhere; }}
    .inventory-source {{ display: grid; gap: 6px; min-width: 0; color: var(--muted); font-size: 13px; }}
    .inventory-path {{ display: block; width: 100%; padding: 6px 8px; line-height: 1.35; }}
    .inventory-tags {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
    .inventory-meta {{ display: grid; gap: 12px; padding: 14px 16px 16px; }}
    .inventory-description {{ margin: 0; max-width: 900px; color: var(--text); line-height: 1.55; overflow-wrap: anywhere; }}
    .metadata-grid {{ margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px 14px; }}
    .metadata-grid div {{ min-width: 0; }}
    .metadata-grid dt {{ margin: 0 0 2px; color: var(--subtle); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }}
    .metadata-grid dd {{ margin: 0; color: var(--muted); overflow-wrap: anywhere; }}
    .trigger-list {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 0; margin: 0; list-style: none; }}
    .trigger-list li {{ margin: 0; }}
    .trigger-chip {{ display: inline-flex; align-items: center; min-height: 26px; padding: 3px 8px; border-radius: 999px; border: 1px solid rgba(33,87,189,0.22); background: var(--accent-bg); color: var(--accent-strong); font-size: 12px; font-weight: 560; }}
    .metadata-empty {{ margin: 0; color: var(--muted); }}
    .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }}
    .info-card {{ min-width: 0; padding: 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); box-shadow: var(--shadow-soft); }}
    .info-card h3, .info-card strong {{ color: var(--text); }}
    .info-card p {{ margin: 6px 0 0; color: var(--muted); }}
    .info-card code {{ display: inline-block; max-width: 100%; overflow-wrap: anywhere; }}
    .learning-list {{ display: grid; gap: 10px; }}
    .learning-card {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: start; }}
    .benchmark-layout {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 0.45fr); gap: 18px; align-items: start; }}
    .benchmark-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    .benchmark-card strong {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; overflow-wrap: anywhere; }}
    .benchmark-card span {{ display: block; margin-top: 7px; font-size: 22px; font-weight: 720; font-variant-numeric: tabular-nums; }}
    .miss-list {{ display: grid; gap: 10px; }}
    .miss-card {{ display: grid; gap: 6px; }}
    .loop-card {{ display: grid; gap: 12px; }}
    .empty-state {{ margin: 0; padding: 14px; border: 1px dashed var(--border-strong); border-radius: 8px; background: var(--surface-muted); color: var(--muted); }}
    .fresh, .present, .used_helpful {{ color: var(--good); }}
    .stale, .ignored, .used_noisy {{ color: var(--warn); }}
    .missing, .bad_recommendation {{ color: var(--bad); }}
    .unknown, .recommended_only, .none {{ color: var(--muted); }}
    .pill.fresh, .pill.present, .pill.used_helpful {{ border-color: #b7e2c7; background: var(--good-bg); color: var(--good); }}
    .pill.stale, .pill.ignored, .pill.used_noisy {{ border-color: #f2d28c; background: var(--warn-bg); color: var(--warn); }}
    .pill.missing, .pill.bad_recommendation {{ border-color: #f0b7b7; background: var(--bad-bg); color: var(--bad); }}
    .path-col {{ min-width: 220px; }}
    .reason-col {{ min-width: 260px; }}
    .two-col {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 0.45fr); gap: 18px; align-items: start; }}
    .action-list {{ display: grid; gap: 10px; padding: 0; list-style: none; }}
    .action-list li {{ margin: 0; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); box-shadow: var(--shadow-soft); }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      nav a {{ transition: none; }}
    }}
    @supports not ((backdrop-filter: blur(1px))) {{
      .topbar, .meta, .metric, .callout, .table-wrap, .action-list li {{ background: #ffffff; }}
    }}
    @media (max-width: 760px) {{
      .topbar-inner {{ padding: 10px 16px; align-items: flex-start; flex-direction: column; }}
      main {{ padding: 16px; }}
      header.hero, .two-col, .benchmark-layout {{ grid-template-columns: 1fr; }}
      .quality-strip {{ grid-template-columns: 1fr 1fr; }}
      .inventory-card-header {{ grid-template-columns: 1fr; }}
      .learning-card {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
    }}
  </style>
</head>
<body>
<a class="skip-link" href="#main">Skip to dashboard</a>
<div class="topbar">
  <div class="topbar-inner">
    <div class="brand">AgentPack</div>
    <nav aria-label="Dashboard sections">
      <a href="#health">Health</a>
      <a href="#integrations">Integrations</a>
      <a href="#files">Files</a>
      <a href="#task-map">Task Map</a>
      <a href="#skills">Skills</a>
      <a href="#inventory">Inventory</a>
      <a href="#learning">Learning</a>
      <a href="#observer">Observer</a>
      <a href="#benchmarks">Benchmarks</a>
      <a href="#loop">Loop</a>
      <a href="#actions">Actions</a>
    </nav>
  </div>
</div>
<main id="main">
  <header class="hero">
    <div>
    <p class="eyebrow">Local control plane</p>
    <h1>AgentPack Dashboard</h1>
    <p class="subtitle">{_e(task_text)}</p>
    </div>
    <div class="meta-stack">
      <p class="meta"><strong>Project</strong><br>{_e(snapshot.project.name)}<br><small>{_e(snapshot.project.path)}</small></p>
      <p class="meta"><strong>Git</strong><br>{_e(git_label or "unknown")}</p>
      <p class="meta"><strong>Generated</strong><br>{_e(generated_at)}</p>
    </div>
  </header>
  {quality_strip}

  <section id="health" class="section">
    <div class="section-header"><h2>Context Health</h2><span class="pill {status_class}">{_e(snapshot.context.status)}</span></div>
    <div class="section-body">
      <div class="grid">
      <div class="metric"><strong>Mode</strong><span>{_e(snapshot.context.mode or "unknown")}</span></div>
      <div class="metric"><strong>Packed Tokens</strong><span>{snapshot.context.packed_tokens:,}</span></div>
      <div class="metric"><strong>Raw Tokens</strong><span>{snapshot.context.raw_tokens:,}</span></div>
      <div class="metric"><strong>Savings</strong><span>{snapshot.context.saving_pct:.1f}%</span></div>
      <div class="metric"><strong>Selected Files</strong><span>{snapshot.context.selected_files_count}</span></div>
      <div class="metric"><strong>Threads</strong><span>{snapshot.threads.active_count}</span></div>
    </div>
    {_stale_reason(snapshot)}
    </div>
  </section>

  {integrations}

  <section id="files" class="section">
    <div class="section-header"><h2>Selected Files</h2><small>Top {min(len(snapshot.selected_files), MAX_RENDERED_FILES)} files from the active context pack</small></div>
    <div class="section-body"><div class="table-wrap"><table><thead><tr><th class="path-col">Path</th><th>Mode</th><th>Score</th><th>Tokens</th><th class="reason-col">Reasons</th></tr></thead><tbody>{files}</tbody></table></div></div>
  </section>

  <section id="task-map" class="section">
    <div class="section-header"><h2>Task Map</h2><small>Risk, tests, impact, and retrieve refs from the active pack</small></div>
    <div class="section-body"><div class="table-wrap"><table><thead><tr><th class="path-col">Path</th><th>Kind</th><th>Risk</th><th class="reason-col">Why</th><th>Tests</th><th>May Break</th><th>Retrieve</th></tr></thead><tbody>{task_map}</tbody></table></div></div>
  </section>

  <section id="skills" class="section">
    <div class="section-header"><h2>Skills</h2><small>Task-specific and baseline recommendations</small></div>
    <div class="section-body"><div class="table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>Confidence</th><th>Score</th><th>Status</th><th>Side Effect</th><th class="reason-col">Reasons</th></tr></thead><tbody>{skills}</tbody></table></div></div>
  </section>

  {skills_inventory}

  <section id="learning" class="section">
    <div class="section-header"><h2>Learning</h2><small>Artifacts that can improve future routing and handoffs</small></div>
    <div class="section-body">{learning}</div>
  </section>

  <section id="observer" class="section">
    <div class="section-header"><h2>Observer</h2><small>Advisory relationships from route, review, learn, and task memory</small></div>
    <div class="section-body">{observer}</div>
  </section>

  <section id="benchmarks" class="section">
    <div class="section-header"><h2>Benchmarks</h2><small>Recent routing quality signals</small></div>
    <div class="section-body benchmark-layout">
      <div><h3>Metrics</h3>{benchmarks}</div>
      <div><h3>Recent Misses</h3>{misses}</div>
    </div>
  </section>

  {loop}

  <section id="actions" class="section">
    <div class="section-header"><h2>Suggested Actions</h2><small>Next commands based on local state</small></div>
    <div class="section-body"><ul class="action-list">{actions}</ul></div>
  </section>
</main>
</body>
</html>
"""


def _quality_strip(snapshot: DashboardSnapshot) -> str:
    benchmark_recall = snapshot.benchmarks.averages.get("selection_recall")
    skill_recall = snapshot.benchmarks.averages.get("skill_recall_at_3")
    learning_present = sum(1 for item in snapshot.learning if item.exists)
    learning_total = len(snapshot.learning)
    items = [
        ("Context", str(snapshot.context.status or "unknown")),
        ("Savings", f"{snapshot.context.saving_pct:.1f}%"),
        ("Skills", str(snapshot.skills_inventory.total_skills) if snapshot.skills_inventory.available else "not indexed"),
        ("File Recall", f"{benchmark_recall:.3f}" if benchmark_recall is not None else "no metric"),
        (
            "Skill Recall",
            f"{skill_recall:.3f}" if skill_recall is not None else f"{learning_present}/{learning_total} learning" if learning_total else "no metric",
        ),
    ]
    cells = "".join(
        f'<div class="quality-item"><strong>{_e(label)}</strong><span>{_e(value)}</span></div>'
        for label, value in items
    )
    return f'<section class="quality-strip" aria-label="Dashboard quality summary">{cells}</section>'


def _selected_file_rows(snapshot: DashboardSnapshot) -> str:
    rows = "".join(
        "<tr>"
        f"<td><code>{_e(item.path)}</code></td>"
        f"<td>{_e(item.include_mode)}</td>"
        f"<td>{item.score:.1f}</td>"
        f"<td>{item.tokens}</td>"
        f"<td>{_e(', '.join(item.reasons[:3]))}</td>"
        "</tr>"
        for item in snapshot.selected_files[:MAX_RENDERED_FILES]
    )
    return rows or '<tr><td colspan="5">No selected files found.</td></tr>'


def _integrations_panel(snapshot: DashboardSnapshot) -> str:
    health = snapshot.mcp_health
    registrations = "".join(
        "<tr>"
        f"<td>{_e(item.scope)}</td>"
        f"<td><span class=\"pill {_status_class(item.status)}\">{_e(item.status)}</span></td>"
        f"<td><code>{_e(item.path)}</code></td>"
        f"<td>{_e(item.detail)}</td>"
        "</tr>"
        for item in health.registrations
    ) or '<tr><td colspan="4">No MCP registration checks found.</td></tr>'
    tools = " ".join(f'<span class="pill">{_e(tool)}</span>' for tool in health.expected_tools[:24]) or "No tools reported."
    remediation = "".join(f"<li><code>{_e(command)}</code></li>" for command in health.remediation) or "<li>No repair action needed.</li>"
    return f"""
  <section id="integrations" class="section">
    <div class="section-header"><h2>Integrations</h2><span class="pill {_status_class(health.status)}">{_e(health.status)}</span></div>
    <div class="section-body">
      <div class="grid">
        <div class="metric"><strong>MCP Runtime</strong><span>{_e(health.runtime_status or "unknown")}</span></div>
        <div class="metric"><strong>Runtime OK</strong><span>{'yes' if health.runtime_ok else 'no'}</span></div>
        <div class="metric"><strong>Registered</strong><span>{'yes' if health.registered else 'no'}</span></div>
        <div class="metric"><strong>Live Exposure</strong><span>{_e(health.live_exposure)}</span></div>
      </div>
      <p class="callout"><small>{_e(health.runtime_detail or "No runtime detail captured.")} Live host exposure cannot be proven by static dashboard; call agentpack_readiness() from the host.</small></p>
      <h3>MCP Registrations</h3>
      <div class="table-wrap"><table><thead><tr><th>Scope</th><th>Status</th><th>Path</th><th>Detail</th></tr></thead><tbody>{registrations}</tbody></table></div>
      <h3>Expected Tools</h3>
      <p>{tools}</p>
      <h3>Repair Path</h3>
      <ul>{remediation}</ul>
    </div>
  </section>"""


def _task_map_rows(snapshot: DashboardSnapshot) -> str:
    if not snapshot.task_map:
        return '<tr><td colspan="7">No task map found. Run agentpack pack to generate one.</td></tr>'
    rows: list[str] = []
    for item in snapshot.task_map[:MAX_RENDERED_FILES]:
        why = "; ".join(item.why_selected[:3])
        tests = ", ".join(f"<code>{_e(test)}</code>" for test in item.tests_to_run[:3])
        may_break = "; ".join(_e(value) for value in item.may_break[:2])
        retrieve = f"<code>retrieve_context(block_id=&quot;{_e(item.retrieve_ref)}&quot;)</code>" if item.retrieve_ref else ""
        risk_class = _status_class(item.risk_level)
        rows.append(
            "<tr>"
            f"<td class=\"path-col\"><code>{_e(item.path)}</code></td>"
            f"<td>{_e(item.kind)}</td>"
            f"<td><span class=\"pill {risk_class}\">{_e(item.risk_level.upper())}</span></td>"
            f"<td>{_e(why)}</td>"
            f"<td>{tests}</td>"
            f"<td>{may_break}</td>"
            f"<td>{retrieve}</td>"
            "</tr>"
        )
    return "".join(rows)


def _skill_rows(items: Iterable[SkillRow], kind: str) -> str:
    return "".join(
        "<tr>"
        f"<td>{_e(item.name)}</td>"
        f"<td>{_e(kind)}</td>"
        f"<td>{item.confidence:.2f}</td>"
        f"<td>{item.score:.1f}</td>"
        f'<td><span class="pill {item.status}">{_e(item.status)}</span></td>'
        f"<td>{_e(item.side_effect_level or 'unknown')}</td>"
        f"<td>{_e(', '.join(item.reasons[:3]))}</td>"
        "</tr>"
        for item in items
    )


def _skills_inventory_panel(snapshot: DashboardSnapshot) -> str:
    inventory = snapshot.skills_inventory
    if not inventory.available:
        reason = inventory.index_error or "No skills index available."
        return f"""
  <section id="inventory" class="section">
    <div class="section-header"><h2>Skills Inventory</h2><small>Local skill discovery</small></div>
    <div class="section-body"><p>{_e(reason)}</p></div>
  </section>"""

    domains = "".join(
        f'<li><span class="domain-item">{_e(item.name)} <strong>{item.count}</strong></span></li>'
        for item in inventory.domains[:20]
    ) or '<li><span class="domain-item">No domains found.</span></li>'
    sources = "".join(
        "<tr>"
        f"<td><code>{_e(item.configured_path)}</code></td>"
        f"<td>{_e(item.resolved_path)}</td>"
        f"<td>{'yes' if item.exists else 'no'}</td>"
        f"<td>{item.file_count}</td>"
        "</tr>"
        for item in inventory.sources
    ) or '<tr><td colspan="4">No configured skill sources found.</td></tr>'
    rows = _inventory_rows(inventory.rows)
    duplicate_names = ", ".join(inventory.duplicate_names) or "none"
    return f"""
  <section id="inventory" class="section">
    <div class="section-header"><h2>Skills Inventory</h2><small>Directories, domains, and metadata quality</small></div>
    <div class="section-body">
      <div class="grid">
      <div class="metric"><strong>Skills</strong><span>{inventory.total_skills}</span></div>
      <div class="metric"><strong>Rules</strong><span>{inventory.total_rules}</span></div>
      <div class="metric"><strong>Uncategorized</strong><span>{inventory.uncategorized_count}</span></div>
      <div class="metric"><strong>Inferred Metadata</strong><span>{inventory.missing_metadata_count}</span></div>
    </div>
    <p class="callout"><small>Index: {_e(inventory.index_reason or "unknown")}; refreshed: {'yes' if inventory.index_refreshed else 'no'}; duplicate names: {_e(duplicate_names)}</small></p>
    <h3>Domains</h3>
    <ul class="domain-list">{domains}</ul>
    <h3>Directories</h3>
    <div class="table-wrap"><table><thead><tr><th>Configured</th><th>Resolved</th><th>Exists</th><th>Files</th></tr></thead><tbody>{sources}</tbody></table></div>
    <h3>Discovered Skills</h3>
    {rows}
    </div>
  </section>"""


def _inventory_rows(items: Iterable[object]) -> str:
    rows = []
    for item in items:
        domains = _chip_list(getattr(item, "domains", []), empty="uncategorized")
        side_effect = getattr(item, "side_effect_level", "") or "unknown"
        quality = getattr(item, "metadata_quality", "inferred")
        rows.append(
            '<article class="inventory-card">'
            '<div class="inventory-card-header">'
            f'<div class="inventory-title"><strong>{_e(getattr(item, "name", ""))}</strong><div class="inventory-tags">{domains}</div></div>'
            f'<div class="inventory-source"><span>{_e(getattr(item, "source", ""))}</span><code class="inventory-path">{_e(getattr(item, "path", ""))}</code></div>'
            f'<div class="inventory-tags"><span class="pill">{_e(side_effect)}</span><span class="pill">{_e(quality)}</span></div>'
            "</div>"
            f'<div class="inventory-meta">{_metadata_cell(quality, getattr(item, "metadata", []))}</div>'
            "</article>"
        )
    if not rows:
        return "<p>No skills discovered.</p>"
    return '<div class="inventory-list">' + "".join(rows) + "</div>"


def _metadata_cell(quality: str, metadata: list[str]) -> str:
    description = ""
    triggers: list[str] = []
    facts: list[tuple[str, str]] = []
    for item in metadata:
        label, _, value = item.partition(": ")
        if not value:
            facts.append(("Metadata", item))
            continue
        if label == "description":
            description = value
        elif label == "triggers":
            triggers = [part.strip() for part in value.split(",") if part.strip()]
        else:
            facts.append((label.replace("_", " ").title(), value))

    parts: list[str] = []
    if description:
        parts.append(f'<p class="inventory-description">{_e(description)}</p>')
    if facts:
        fact_items = "".join(f"<div><dt>{_e(label)}</dt><dd>{_e(value)}</dd></div>" for label, value in facts)
        parts.append(f'<dl class="metadata-grid">{fact_items}</dl>')
    if triggers:
        chips = "".join(f'<li><span class="trigger-chip">{_e(trigger)}</span></li>' for trigger in triggers)
        parts.append(f'<ul class="trigger-list" aria-label="Skill triggers">{chips}</ul>')
    return "".join(parts) if parts else '<p class="metadata-empty">No metadata detected.</p>'


def _chip_list(values: Iterable[str], *, empty: str) -> str:
    items = list(values)
    if not items:
        items = [empty]
    return "".join(f'<span class="pill">{_e(item)}</span>' for item in items)



def _learning_rows(snapshot: DashboardSnapshot) -> str:
    rows = []
    for item in snapshot.learning_weak_spots:
        files = ", ".join(item.evidence_files[:3]) or "no evidence files captured"
        rows.append(
            '<article class="info-card learning-card">'
            f'<div><strong>{_e(item.concept or "Weak spot")}</strong><br>'
            f'<small>{item.count} queued question(s) / {_e(item.mode or "mixed")}</small>'
            f'<p>{_e(item.latest_question or item.latest_task or "No question captured.")}</p>'
            f'<p><code>{_e(files)}</code></p></div>'
            '<span class="pill stale">weak spot</span>'
            "</article>"
        )
    for item in snapshot.learning_memories:
        concepts = _chip_list(item.concepts, empty="no concepts")
        files = ", ".join(item.changed_files[:3]) or "no changed files captured"
        rows.append(
            '<article class="info-card learning-card">'
            f'<div><strong>{_e(item.task or "Recent task")}</strong><br>'
            f'<small>{_e(item.stage or "task")} / {_e(item.status or "unknown")} / {_e(item.branch or "unknown")}</small>'
            f'<p>{concepts}</p><p><code>{_e(files)}</code></p></div>'
            '<span class="pill present">memory</span>'
            "</article>"
        )
    for item in snapshot.learning:
        state = "present" if item.exists else "missing"
        excerpt = f"<p>{_e(item.excerpt)}</p>" if item.excerpt else ""
        rows.append(
            '<article class="info-card learning-card">'
            f'<div><strong>{_e(item.label)}</strong><br><code>{_e(item.path)}</code>{excerpt}</div>'
            f'<span class="pill {state}">{state}</span>'
            "</article>"
        )
    if not rows:
        return '<p class="empty-state">No learning artifacts checked.</p>'
    return '<div class="learning-list">' + "".join(rows) + "</div>"


def _observer_rows(snapshot: DashboardSnapshot) -> str:
    stats = "".join(
        f'<span class="pill">{_e(kind)} {count}</span>'
        for kind, count in sorted(snapshot.observer.event_types.items())
    )
    rows = []
    for item in snapshot.observer.insights:
        files = ", ".join(item.related_files[:4]) or "no files"
        evidence = ", ".join(item.evidence[:3]) or "no evidence"
        rows.append(
            '<article class="info-card learning-card">'
            f'<div><strong>{_e(item.title)}</strong><br>'
            f'<small>{_e(item.kind)} / confidence {item.confidence:.2f}</small>'
            f'<p>{_e(item.detail)}</p>'
            f'<p><strong>Action:</strong> {_e(item.action)}</p>'
            f'<p><code>{_e(files)}</code></p>'
            f'<p><small>{_e(evidence)}</small></p></div>'
            '<span class="pill">advisory</span>'
            "</article>"
        )
    if not rows:
        rows.append('<p class="empty-state">No observer signals yet.</p>')
    return (
        '<div class="grid">'
        f'<div class="metric"><strong>Events</strong><span>{snapshot.observer.events}</span></div>'
        f'<div class="metric compact"><strong>Brief</strong><span>{_e(snapshot.observer.brief_path)}</span></div>'
        f'<div class="metric compact"><strong>Types</strong><span>{stats or "none"}</span></div>'
        "</div>"
        '<p class="callout"><small>Observer signals are hypotheses from local history. Verify source files and diffs before acting.</small></p>'
        '<div class="learning-list">'
        + "".join(rows)
        + "</div>"
    )


def _benchmark_rows(snapshot: DashboardSnapshot) -> str:
    rows = [
        '<article class="info-card benchmark-card">'
        f"<strong>{_e(key)}</strong>"
        f"<span>{value:.3f}</span>"
        "</article>"
        for key, value in sorted(snapshot.benchmarks.averages.items())
    ]
    if not rows:
        return '<p class="empty-state">No benchmark metrics found.</p>'
    return '<div class="benchmark-grid">' + "".join(rows) + "</div>"


def _miss_rows(snapshot: DashboardSnapshot) -> str:
    rows = []
    for miss in snapshot.benchmarks.misses[:MAX_RENDERED_MISSES]:
        path = miss.get("path") or miss.get("file") or miss.get("expected_file") or ""
        reason = miss.get("reason") or miss.get("status") or miss.get("remediation") or ""
        rows.append(
            '<article class="info-card miss-card">'
            f"<strong>{_e(path or 'Unknown file')}</strong>"
            f"<p>{_e(reason or 'No reason recorded.')}</p>"
            "</article>"
        )
    if not rows:
        return '<p class="empty-state">No recent benchmark misses.</p>'
    return '<div class="miss-list">' + "".join(rows) + "</div>"


def _action_rows(snapshot: DashboardSnapshot) -> str:
    rows = [
        f"<li><strong>{_e(item.label)}</strong><br><code>{_e(item.command)}</code><br><small>{_e(item.reason)}</small></li>"
        for item in snapshot.suggested_actions
    ]
    return "".join(rows) or "<li>No suggested actions.</li>"


def _loop_panel(snapshot: DashboardSnapshot) -> str:
    if not snapshot.loop.exists:
        return """
  <section id="loop" class="section">
    <div class="section-header"><h2>Guarded Loop</h2><small>Optional proof harness state</small></div>
    <div class="section-body"><p class="empty-state">No Ralph Loop state found.</p></div>
  </section>"""
    return f"""
  <section id="loop" class="section">
    <div class="section-header"><h2>Guarded Loop</h2><small>Optional proof harness state</small></div>
    <div class="section-body info-card loop-card">
      <div class="grid">
      <div class="metric"><strong>Status</strong><span>{_e(snapshot.loop.status)}</span></div>
      <div class="metric"><strong>Iteration</strong><span>{snapshot.loop.iteration}/{snapshot.loop.max_iterations}</span></div>
      <div class="metric"><strong>Runner</strong><span>{_e(snapshot.loop.last_runner_status or "not run")}</span></div>
      <div class="metric"><strong>Verification</strong><span>{_e(snapshot.loop.last_verification_status or "not run")}</span></div>
      <div class="metric"><strong>Risk</strong><span>{_e(snapshot.loop.risk_level or "unknown")}</span></div>
      <div class="metric"><strong>Failure</strong><span>{_e(snapshot.loop.failure_class or "none")}</span></div>
      <div class="metric"><strong>Runs</strong><span>{snapshot.loop.runs}</span></div>
      <div class="metric"><strong>Avg Iterations</strong><span>{snapshot.loop.avg_iterations}</span></div>
    </div>
    <p><strong>Task:</strong> {_e(snapshot.loop.task)}</p>
    <p><strong>Blocked reason:</strong> {_e(snapshot.loop.blocked_reason or "none")}</p>
    <p><strong>Changed files:</strong> {_e(", ".join(snapshot.loop.changed_files[:8]) or "none")}</p>
    <p><strong>Artifacts:</strong> {_e(", ".join(item for item in [snapshot.loop.diagnosis_file, snapshot.loop.handoff_file, snapshot.loop.acceptance_file, snapshot.loop.rollback_patch] if item) or "none")}</p>
    <p><strong>Next:</strong> <code>{_e(snapshot.loop.next_action)}</code></p>
    </div>
  </section>"""


def _stale_reason(snapshot: DashboardSnapshot) -> str:
    if not snapshot.context.stale_reason:
        return ""
    return f'<p class="callout"><small>{_e(snapshot.context.stale_reason)}</small></p>'


def _status_class(value: object) -> str:
    text = str(value)
    risk_map = {
        "low": "fresh",
        "medium": "stale",
        "high": "missing",
        "healthy": "fresh",
        "warning": "stale",
        "present": "fresh",
        "invalid": "stale",
    }
    if text in risk_map:
        return risk_map[text]
    return text if text in {"fresh", "stale", "missing", "unknown"} else "unknown"


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)
