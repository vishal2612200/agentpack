from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.table import Table
from rich import box
from rich.panel import Panel

from agentpack.core import git
from agentpack.core.config import load_config
from agentpack.core.ignore import load_spec
from agentpack.core.scanner import scan
from agentpack.core.snapshot import build_snapshot
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.token_contract import token_contract_from_metadata
from agentpack.application.pack_service import AdapterRegistry
from agentpack.analysis.ranking import suggest_task_rewrite
from agentpack.commands._shared import console, _root
from agentpack.session.state import SessionState
from agentpack.session.events import read_events, summarize_events


def register(app: typer.Typer) -> None:
    @app.command()
    def stats() -> None:
        """Show token-saving statistics and session info."""
        root = _root()
        cfg = load_config(root)
        ignore_spec = load_spec(root / cfg.project.ignore_file)
        meta = load_pack_metadata(root)
        freshness = (meta or {}).get("freshness") or {}
        workspace = freshness.get("workspace") or (meta or {}).get("workspace")
        include_globs = [f"{workspace}/**"] if isinstance(workspace, str) and workspace else (cfg.project.include_globs or None)

        scan_result = scan(
            root,
            ignore_spec,
            cfg.context.max_file_tokens,
            include_globs=include_globs,
            exclude_globs=cfg.project.exclude_globs or None,
            always_skip_paths=AdapterRegistry.generated_output_paths(root, cfg),
        )

        raw = sum(f.estimated_tokens for f in scan_result.all_files)
        after_ignore = sum(f.estimated_tokens for f in scan_result.packable)
        packed = meta.get("token_estimate", 0) if meta else 0
        token_contract = token_contract_from_metadata(meta)
        saving = (1 - packed / raw) * 100 if raw > 0 else 0

        ignored_count = len(scan_result.ignored) + len(scan_result.binary)
        included_count = 0
        summarized_count = 0
        top_files: list[tuple[str, str, str]] = []

        if meta:
            selected_meta = meta.get("selected_files_meta") or []
            if isinstance(selected_meta, list):
                mode_counts: dict[str, int] = {}
                for item in selected_meta:
                    if isinstance(item, dict) and isinstance(item.get("mode"), str):
                        mode = item["mode"]
                        mode_counts[mode] = mode_counts.get(mode, 0) + 1
                included_count = mode_counts.get("full", 0)
                summarized_count = mode_counts.get("summary", 0)

        # --- Session info ---
        from agentpack.session.state import load_session
        session = load_session(root)

        if session:
            sess_tbl = Table(title="Session", box=box.SIMPLE, show_header=False, padding=(0, 2))
            sess_tbl.add_column(style="dim")
            sess_tbl.add_column(style="bold")
            sess_tbl.add_row("active", "[green]yes[/]" if session.active else "[red]no[/]")
            sess_tbl.add_row("agent", session.agent)
            if session.last_resolved_agent and session.last_resolved_agent != session.agent:
                sess_tbl.add_row("last pack agent", session.last_resolved_agent)
            sess_tbl.add_row("mode", session.mode)
            if session.started_at:
                sess_tbl.add_row("started", session.started_at[:19].replace("T", " "))
            if session.last_refresh_at:
                sess_tbl.add_row("last refresh", session.last_refresh_at[:19].replace("T", " "))
            sess_tbl.add_row("refreshes", str(session.refresh_count))
            console.print(sess_tbl)
            console.print()

        # --- Last context top files ---
        metrics_path = root / ".agentpack" / "metrics.jsonl"
        if metrics_path.exists():
            lines = [line.strip() for line in metrics_path.read_text().splitlines() if line.strip()]
            if lines:
                try:
                    json.loads(lines[-1])
                    # metrics don't store per-file data — use context file for top files
                except Exception:
                    pass

        context_path_obj = None
        if meta:
            context_path_obj = root / meta.get("context_path", "")
            top_files = _top_files_from_metadata(meta)
            if not top_files and context_path_obj.exists():
                top_files = _parse_top_files(context_path_obj)

        freshness_diagnostics = _freshness_diagnostics(
            root=root,
            meta=meta,
            session=session,
            current_root_hash=build_snapshot(scan_result.packable)["root_hash"],
            context_path=context_path_obj,
        )
        if freshness_diagnostics:
            console.print()
            console.print(_advice_panel("Freshness advice", freshness_diagnostics))

        token_by_path = {f.path: f.estimated_tokens for f in scan_result.packable}
        top_estimate = sum(token_by_path.get(path, 0) for path, _mode, _why in top_files[:20])
        if top_estimate <= 0:
            full_files = [f for f in scan_result.packable
                          if f.estimated_tokens <= cfg.context.max_file_tokens]
            top_estimate = sum(f.estimated_tokens for f in full_files[:20])
        top_estimate = min(after_ignore, top_estimate)
        vs_top_files = (1 - packed / top_estimate) * 100 if top_estimate > 0 else 0

        # --- Token table ---
        token_tbl = Table(title="Last Context", box=box.SIMPLE, show_header=False, padding=(0, 2))
        token_tbl.add_column(style="dim")
        token_tbl.add_column(justify="right", style="bold")
        token_tbl.add_row("raw repo tokens", f"{raw:,}")
        token_tbl.add_row("after ignore", f"{after_ignore:,}")
        token_tbl.add_row("packed tokens", f"{packed:,}")
        token_tbl.add_row("vs raw repo", f"[green]{saving:.1f}% smaller[/]")
        token_tbl.add_row("vs top-20 full files", f"[green]{vs_top_files:.1f}% smaller[/]")
        token_tbl.add_row("files ignored/binary", f"{ignored_count:,}")
        token_tbl.add_row("files packable", f"{len(scan_result.packable):,}")
        token_tbl.add_row("files full", f"{included_count:,}")
        token_tbl.add_row("files summary", f"{summarized_count:,}")
        if token_contract:
            token_tbl.add_row("contract usage", f"{float(token_contract.get('usage_ratio') or 0):.1%}")
        console.print(token_tbl)
        if token_contract and token_contract.get("recommended_next_context"):
            console.print(f"[dim]Token contract: {token_contract['recommended_next_context']}[/]")

        events_summary = summarize_events(read_events(root, output_path=cfg.runtime.session_events_output))
        if events_summary["events"]:
            console.print()
            events_tbl = Table(title="Runtime Events", box=box.SIMPLE, show_header=False, padding=(0, 2))
            events_tbl.add_column(style="dim")
            events_tbl.add_column(justify="right", style="bold")
            events_tbl.add_row("events", f"{events_summary['events']:,}")
            events_tbl.add_row("estimated saved tokens", f"{events_summary['estimated_saved_tokens']:,}")
            events_tbl.add_row("retrievals", f"{events_summary['retrievals']:,}")
            events_tbl.add_row("output compressions", f"{events_summary['output_compressions']:,}")
            console.print(events_tbl)

        workspace_rows = _workspace_rows(meta, top_files)
        if workspace_rows:
            console.print()
            workspace_tbl = Table(title="Workspaces", box=box.SIMPLE, show_header=True, padding=(0, 1))
            workspace_tbl.add_column("workspace", style="cyan")
            workspace_tbl.add_column("selected", justify="right")
            workspace_tbl.add_column("tokens", justify="right")
            for workspace_name, selected_count, token_count in workspace_rows[:10]:
                workspace_tbl.add_row(workspace_name, str(selected_count), f"{token_count:,}")
            console.print(workspace_tbl)

        if top_files:
            console.print()
            top_tbl = Table(title="Top Included", box=box.SIMPLE, show_header=True, padding=(0, 1))
            top_tbl.add_column("#", width=3, style="dim")
            top_tbl.add_column("file", style="cyan", max_width=55)
            top_tbl.add_column("mode", width=8)
            top_tbl.add_column("why", style="dim", max_width=35)
            for i, (path, mode, why) in enumerate(top_files[:10], 1):
                top_tbl.add_row(str(i), path, mode, why)
            console.print(top_tbl)

        # --- Selection accuracy (last 10 runs) ---
        accuracy_rows = _load_accuracy_rows(metrics_path, n=10)
        noise_diagnostics = _noise_diagnostics(top_files, accuracy_rows, task=(meta or {}).get("task"))
        if noise_diagnostics:
            console.print()
            console.print(_advice_panel("Pack quality advice", noise_diagnostics))

        if accuracy_rows:
            avg_recall = sum(r["selection_recall"] for r in accuracy_rows) / len(accuracy_rows)
            avg_precision = sum(r["selection_precision"] for r in accuracy_rows) / len(accuracy_rows)
            avg_f1 = sum(r["selection_f1"] for r in accuracy_rows) / len(accuracy_rows)
            context_rows = [r for r in accuracy_rows if "selection_context_precision" in r]
            avg_context_precision = (
                sum(r["selection_context_precision"] for r in context_rows) / len(context_rows)
                if context_rows else None
            )
            token_rows = [r for r in accuracy_rows if "selection_token_precision" in r]
            avg_token_precision = (
                sum(r["selection_token_precision"] for r in token_rows) / len(token_rows)
                if token_rows else None
            )
            token_context_rows = [r for r in accuracy_rows if "selection_token_context_precision" in r]
            avg_token_context_precision = (
                sum(r["selection_token_context_precision"] for r in token_context_rows) / len(token_context_rows)
                if token_context_rows else None
            )
            mode_token_precision: dict[str, float] = {}
            for mode in ("full", "symbols", "summary"):
                key = f"selection_token_precision_{mode}"
                rows = [r for r in accuracy_rows if key in r]
                if rows:
                    mode_token_precision[mode] = sum(r[key] for r in rows) / len(rows)
            console.print()
            acc_tbl = Table(title=f"Selection Accuracy (last {len(accuracy_rows)} runs)", box=box.SIMPLE, show_header=False, padding=(0, 2))
            acc_tbl.add_column(style="dim")
            acc_tbl.add_column(justify="right", style="bold")
            acc_tbl.add_row("avg recall", f"{avg_recall:.1%}")
            acc_tbl.add_row("avg precision", f"{avg_precision:.1%}")
            if avg_context_precision is not None:
                acc_tbl.add_row("avg context precision", f"{avg_context_precision:.1%}")
            if avg_token_precision is not None:
                acc_tbl.add_row("avg token precision", f"{avg_token_precision:.1%}")
                if avg_token_context_precision is not None:
                    acc_tbl.add_row("token context precision", f"{avg_token_context_precision:.1%}")
                for mode, value in mode_token_precision.items():
                    acc_tbl.add_row(f"{mode} token precision", f"{value:.1%}")
            acc_tbl.add_row("avg F1", f"{avg_f1:.1%}")
            console.print(acc_tbl)
            console.print("[dim]recall = how many changed files were in the previous pack[/]")
            if avg_context_precision is not None:
                console.print("[dim]context precision = edited hits plus obvious support files, such as paired tests[/]")
            if avg_token_precision is not None:
                console.print("[dim]token precision = share of previous pack tokens spent on files later changed[/]")

        proof = _benchmark_proof_status(root)
        if proof:
            console.print()
            proof_tbl = Table(title="Benchmark Proof", box=box.SIMPLE, show_header=False, padding=(0, 2))
            proof_tbl.add_column(style="dim")
            proof_tbl.add_column(justify="right", style="bold")
            proof_tbl.add_row("cases", str(int(proof["cases"])))
            proof_tbl.add_row("avg recall", f"{proof['avg_recall']:.1%}")
            proof_tbl.add_row("avg token precision", f"{proof['avg_token_precision']:.1%}")
            proof_tbl.add_row("target", "[green]passed[/]" if proof["passed"] else "[yellow]not met[/]")
            console.print(proof_tbl)

        console.print("[dim]'top-20 full files' = raw full contents for top included files, capped at 20[/]")


def _load_accuracy_rows(metrics_path: Path, n: int = 10) -> list[dict]:
    """Return up to n most recent metrics records that have accuracy fields."""
    if not metrics_path.exists():
        return []
    try:
        lines = metrics_path.read_text(encoding="utf-8").splitlines()
        rows = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if "selection_recall" in rec:
                rows.append(rec)
                if len(rows) >= n:
                    break
        return rows
    except Exception:
        return []


def _task_md_body(root: Path) -> str | None:
    path = root / ".agentpack" / "task.md"
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    lines = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    body = lines[0].strip() if lines else ""
    if body and "Write or update the current coding task here." not in body:
        return body
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _freshness_diagnostics(
    *,
    root: Path,
    meta: dict | None,
    session: SessionState | None,
    current_root_hash: str,
    context_path: Path | None,
) -> list[str]:
    if not meta:
        return ["No pack metadata found; run `agentpack pack --task auto`."]

    diagnostics: list[str] = []
    for warning in meta.get("freshness_warnings") or []:
        diagnostics.append(str(warning))

    task_md = _task_md_body(root)
    if task_md and task_md != meta.get("task"):
        diagnostics.append(
            ".agentpack/task.md differs from the latest packed task "
            f"(packed: {meta.get('task')}; current: {task_md})."
        )

    if meta.get("snapshot_root_hash") and meta.get("snapshot_root_hash") != current_root_hash:
        diagnostics.append("Files changed since the latest pack; refresh before trusting top included files.")

    if git.is_git_repo(root):
        packed_sha = meta.get("git_sha") or (meta.get("freshness") or {}).get("git_sha")
        current_sha = git.current_sha(root)
        if packed_sha and current_sha and packed_sha != current_sha:
            diagnostics.append("Git HEAD changed since the latest pack.")

    if context_path is not None and not context_path.exists():
        diagnostics.append(f"Recorded context path is missing: {context_path.relative_to(root)}.")

    if session and session.active:
        meta_agent = meta.get("agent")
        resolved_agent = session.last_resolved_agent or meta_agent
        configured_agent = session.agent
        if configured_agent in {"auto", "generic"}:
            configured_agent = ""
        if configured_agent and resolved_agent and configured_agent != resolved_agent:
            diagnostics.append(
                f"Session agent is {session.agent}, but latest pack resolved {resolved_agent}; "
                "run `agentpack pack --agent auto --task auto` or restart the session."
            )
        packed_at = _parse_iso(meta.get("generated_at"))
        refreshed_at = _parse_iso(session.last_refresh_at)
        if not session.last_refresh_at:
            diagnostics.append("Session is active but has no last refresh timestamp.")
        elif packed_at and refreshed_at and refreshed_at < packed_at:
            diagnostics.append("Session last refresh timestamp is older than latest pack metadata.")

    return diagnostics[:5]


def _noise_diagnostics(
    top_files: list[tuple[str, str, str]],
    accuracy_rows: list[dict],
    task: str | None = None,
) -> list[str]:
    diagnostics: list[str] = []
    if top_files:
        visible_top = top_files[:10]
        summary_count = sum(1 for _path, mode, _why in visible_top if mode == "summary")
        filename_matches = sum(
            1
            for _path, _mode, why in visible_top
            if "filename keyword match" in why and "modified" not in why and "staged" not in why
        )
        if summary_count / len(visible_top) >= 0.7:
            diagnostics.append("Latest pack is mostly summaries; keep balanced mode and use a narrower task for edit work.")
        if filename_matches / len(visible_top) >= 0.6:
            diagnostics.append("Top files mostly matched by filename; task terms may be broad.")
            diagnostics.append("Rewrite `.agentpack/task.md` with concrete file, route, service, or symptom words.")
            if task:
                diagnostics.append(f"Rewrite example: `{suggest_task_rewrite(task)}`.")

    if accuracy_rows:
        avg_precision = sum(r["selection_precision"] for r in accuracy_rows) / len(accuracy_rows)
        token_rows = [r for r in accuracy_rows if "selection_token_precision" in r]
        avg_token_precision = (
            sum(r["selection_token_precision"] for r in token_rows) / len(token_rows)
            if token_rows else None
        )
        summary_rows = [r for r in accuracy_rows if "selection_token_precision_summary" in r]
        avg_summary_precision = (
            sum(r["selection_token_precision_summary"] for r in summary_rows) / len(summary_rows)
            if summary_rows else None
        )
        if avg_precision < 0.05:
            diagnostics.append("Selection file precision is very low; many selected files were not later changed.")
        if avg_token_precision is not None and avg_token_precision < 0.2:
            diagnostics.append("Token precision is low; most packed tokens became noise in recent runs.")
            diagnostics.append("Keep standard balanced mode and rewrite the task with concrete file, route, service, or symptom words.")
        if avg_summary_precision == 0:
            diagnostics.append("Summary token precision is 0%; summaries will be suppressed in no-live-change packs.")
        noisy_counts: dict[str, int] = {}
        for row in accuracy_rows:
            for path in row.get("selection_noise_paths", []) or []:
                if isinstance(path, str):
                    noisy_counts[path] = noisy_counts.get(path, 0) + 1
        if noisy_counts:
            noisy = sorted(noisy_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
            diagnostics.append(
                "Repeated noisy paths: "
                + ", ".join(f"{path} ({count}x)" for path, count in noisy)
            )
            first_path = noisy[0][0]
            diagnostics.append(
                f"Inspect top noisy path: `agentpack explain --file {first_path} --task auto`; "
                "add generated/vendor paths to `.agentignore`, run `agentpack ignore sync`, or tighten task wording if it is not useful."
            )
    return diagnostics[:10]


def _top_files_from_metadata(meta: dict) -> list[tuple[str, str, str]]:
    files = meta.get("selected_files_meta") or []
    if not isinstance(files, list):
        return []
    result: list[tuple[str, str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        mode = item.get("mode")
        why = item.get("why") or ""
        if isinstance(path, str) and isinstance(mode, str):
            result.append((path, mode, str(why)))
    return result


def _workspace_rows(meta: dict | None, top_files: list[tuple[str, str, str]]) -> list[tuple[str, int, int]]:
    if not meta:
        return []
    freshness = meta.get("freshness") or {}
    roots = freshness.get("workspace_roots") or meta.get("workspace_roots") or []
    if not isinstance(roots, list) or not roots:
        return []
    from agentpack.analysis.monorepo import workspace_for_path

    selected_meta = meta.get("selected_files_meta") or []
    rows: dict[str, tuple[int, int]] = {}
    if isinstance(selected_meta, list):
        for item in selected_meta:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            workspace = workspace_for_path(item["path"], [str(root) for root in roots]) or "(root)"
            count, tokens = rows.get(workspace, (0, 0))
            token_count = item.get("tokens", 0)
            rows[workspace] = (count + 1, tokens + (token_count if isinstance(token_count, int | float) else 0))
    if not rows and top_files:
        for path, _mode, _why in top_files:
            workspace = workspace_for_path(path, [str(root) for root in roots]) or "(root)"
            count, tokens = rows.get(workspace, (0, 0))
            rows[workspace] = (count + 1, tokens)
    active = freshness.get("workspace") or meta.get("workspace")
    if isinstance(active, str) and active and active not in rows:
        rows[active] = (0, 0)
    return sorted(
        ((workspace, count, int(tokens)) for workspace, (count, tokens) in rows.items()),
        key=lambda item: (-item[1], item[0]),
    )


def _benchmark_proof_status(root: Path, *, n: int = 20) -> dict[str, float | bool] | None:
    path = root / ".agentpack" / "benchmark_results.jsonl"
    if not path.exists():
        return None
    rows: list[dict] = []
    try:
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            if len(rows) >= n:
                break
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row.get("recall"), int | float) and isinstance(row.get("token_precision"), int | float):
                rows.append(row)
    except OSError:
        return None
    if not rows:
        return None
    avg_recall = sum(float(row["recall"]) for row in rows) / len(rows)
    avg_token_precision = sum(float(row["token_precision"]) for row in rows) / len(rows)
    return {
        "cases": float(len(rows)),
        "avg_recall": avg_recall,
        "avg_token_precision": avg_token_precision,
        "passed": avg_recall >= 0.60 and avg_token_precision >= 0.50,
    }


def _advice_panel(title: str, diagnostics: list[str]) -> Panel:
    body = "\n".join(f"  [cyan]i[/] {line}" for line in diagnostics)
    return Panel(body, title=f"[bold cyan]{title}[/]", border_style="cyan", padding=(0, 1))


def _parse_top_files(context_path: Path) -> list[tuple[str, str, str]]:
    """Parse top selected files from context.md. Returns list of (path, mode, why)."""
    results: list[tuple[str, str, str]] = []
    try:
        content = context_path.read_text(encoding="utf-8")
        # Parse the Selected Files table: | `path` | mode | score | why |
        in_table = False
        for line in content.splitlines():
            if line.startswith("| File") or line.startswith("|---|"):
                in_table = True
                continue
            if in_table:
                if not line.startswith("|"):
                    break
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 3:
                    path = parts[0].strip("`")
                    mode = parts[1]
                    why = parts[3] if len(parts) > 3 else ""
                    results.append((path, mode, why))
    except Exception:
        pass
    return results
