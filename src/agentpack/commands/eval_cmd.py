from __future__ import annotations

from pathlib import Path
import os
import time

import typer
from rich.table import Table
from rich import box

from agentpack.commands._shared import console, _root
from agentpack.application.pack_service import PackPlanner, PackRequest
from agentpack.core.evals import (
    FAILURE_CLASSES,
    append_captured_eval_case,
    append_eval_cases_from_episodes,
    compare_eval_variants,
    default_eval_cases_path,
    eval_results_path,
    eval_watch_fingerprint,
    load_eval_cases,
    load_eval_result_records,
    persist_eval_results,
    run_eval_suite,
    scaffold_eval_cases,
    write_eval_ci_template,
    write_eval_report,
)


def register(app: typer.Typer) -> None:
    @app.command(name="eval")
    def eval_command(
        init: bool = typer.Option(False, "--init", help="Scaffold .agentpack/evals.toml and exit."),
        cases: str = typer.Option("", "--cases", help="Path to eval TOML file (default: .agentpack/evals.toml)."),
        case: str = typer.Option("", "--case", help="Run one eval case by id."),
        prove_targets: bool = typer.Option(False, "--prove-targets", help="Exit non-zero when any eval case fails."),
        capture: str = typer.Option("", "--capture", help="Append a case from current git diff using this id."),
        from_episodes: bool = typer.Option(False, "--from-episodes", help="Append regression eval cases from failed episodic memory."),
        failure_class: str = typer.Option("context", "--failure-class", help=f"Failure class ({' | '.join(FAILURE_CLASSES)})."),
        failure_source: str = typer.Option("agent_failed", "--failure-source", help="Failure source for captured cases."),
        check: list[str] | None = typer.Option(None, "--check", help="Deterministic command check for --capture. Repeatable."),
        task: str = typer.Option("", "--task", help="Task text for --capture."),
        base_ref: str = typer.Option("HEAD", "--base-ref", help="Git base ref for diff checks."),
        report: bool = typer.Option(False, "--report", help="Write benchmarks/results/YYYY-MM-DD-eval.md."),
        ci_template: bool = typer.Option(False, "--ci-template", help="Scaffold .github/workflows/agentpack-eval.yml and exit."),
        variant: str = typer.Option("agentpack", "--variant", help="Result variant label, e.g. baseline or agentpack."),
        compare_variants: str = typer.Option("", "--compare-variants", help="Compare latest results as BASELINE:VARIANT."),
        memory_ab: bool = typer.Option(False, "--memory-ab", help="Compare context selection with memory feedback off vs auto."),
        memory_ab_checks: bool = typer.Option(False, "--memory-ab-checks", help="With --memory-ab, also run deterministic eval checks for both memory profiles."),
        replay: bool = typer.Option(False, "--replay", help="Run cases in isolated git worktrees using captured patch_file artifacts."),
        watch: bool = typer.Option(False, "--watch", help="Rerun evals when git diff state changes."),
        interval: float = typer.Option(2.0, "--interval", help="Watch polling interval in seconds."),
        max_runs: int = typer.Option(0, "--max-runs", help="Maximum watch runs (0 = unlimited)."),
        until_pass: bool = typer.Option(False, "--until-pass", help="Stop watch mode after all cases pass."),
        agent: str = typer.Option("", "--agent", help="Agent label to store with --capture metadata."),
        prompt_file: str = typer.Option("", "--prompt-file", help="Prompt artifact path to store with --capture."),
        context_file: str = typer.Option(".agentpack/context.md", "--context-file", help="Context artifact path to store with --capture."),
    ) -> None:
        """Run deterministic eval cases without using an LLM judge."""
        root = _root()
        cases_path = Path(cases) if cases else default_eval_cases_path(root)

        if compare_variants:
            _print_variant_comparison(root, compare_variants)
            return

        if ci_template:
            out = write_eval_ci_template(root)
            console.print(f"[green]✓[/] Created [bold]{out}[/]")
            return

        if init:
            out = scaffold_eval_cases(root)
            console.print(f"[green]✓[/] Created [bold]{out}[/]")
            console.print("  Edit it with real failures, then run [bold]agentpack eval[/].")
            return

        if capture:
            try:
                captured = append_captured_eval_case(
                    cases_path,
                    root=root,
                    case_id=capture,
                    failure_class=failure_class,
                    checks=check or [],
                    task=task,
                    failure_source=failure_source,
                    base_ref=base_ref,
                    agent=agent,
                    prompt_file=prompt_file,
                    context_file=context_file,
                )
            except ValueError as exc:
                console.print(f"[red]{exc}[/]")
                raise typer.Exit(1) from exc
            console.print(f"[green]✓[/] Captured eval case [bold]{captured.id}[/] in [bold]{cases_path}[/]")
            console.print(f"  Required changed files: {len(captured.required_changed_files)}")
            if captured.patch_redaction_warnings:
                console.print(f"  [yellow]Redacted {len(captured.patch_redaction_warnings)} secret(s) from patch artifact.[/]")
            return

        if from_episodes:
            count = append_eval_cases_from_episodes(cases_path, root=root)
            console.print(f"[green]✓[/] Added {count} eval case(s) from failed episodes in [bold]{cases_path}[/]")
            return

        if report and not cases_path.exists():
            records = load_eval_result_records(eval_results_path(root))
            out = write_eval_report(root, records)
            console.print(f"[green]✓[/] Wrote eval report: [bold]{out}[/]")
            return

        if not cases_path.exists():
            console.print(f"[yellow]No eval cases file found at {cases_path}[/]")
            console.print("  Run [bold]agentpack eval --init[/] to scaffold one.")
            raise typer.Exit(1)

        try:
            eval_cases = load_eval_cases(cases_path)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc

        if case:
            eval_cases = [item for item in eval_cases if item.id == case]
            if not eval_cases:
                console.print(f"[yellow]No eval case found with id: {case}[/]")
                raise typer.Exit(1)

        if not eval_cases:
            console.print("[yellow]No eval cases defined.[/]")
            raise typer.Exit(1)

        if memory_ab:
            comparison = _run_memory_ab(root, eval_cases, run_checks=memory_ab_checks, replay=replay)
            failure = _memory_ab_prove_target_failure(comparison) if prove_targets else ""
            if failure:
                console.print(f"[red]Memory A/B target failed:[/] {failure}")
                raise typer.Exit(2)
            return

        if watch:
            results = _watch_eval_cases(
                root,
                eval_cases,
                variant=variant,
                replay=replay,
                interval=interval,
                max_runs=max_runs,
                until_pass=until_pass,
                extra_paths=[cases_path],
            )
        else:
            results = _run_once(root, eval_cases, variant=variant, replay=replay)

        if report:
            records = load_eval_result_records(eval_results_path(root))
            out = write_eval_report(root, records)
            console.print(f"[green]✓[/] Wrote eval report: [bold]{out}[/]")

        if prove_targets and not all(result.passed for result in results):
            raise typer.Exit(2)


def _run_once(root: Path, eval_cases, *, variant: str, replay: bool):
    console.print(f"\n[bold]Running {len(eval_cases)} deterministic eval case(s)...[/]\n")
    results = run_eval_suite(root, eval_cases, variant=variant, replay=replay)
    persist_eval_results(root, results)
    _print_results(results)
    return results


def _watch_eval_cases(
    root: Path,
    eval_cases,
    *,
    variant: str,
    replay: bool,
    interval: float,
    max_runs: int,
    until_pass: bool,
    extra_paths: list[Path],
):
    if interval <= 0:
        console.print("[red]--interval must be greater than 0[/]")
        raise typer.Exit(1)
    if max_runs < 0:
        console.print("[red]--max-runs must be 0 or greater[/]")
        raise typer.Exit(1)

    console.print("[bold]Watching deterministic evals.[/] Press Ctrl-C to stop.")
    last_fingerprint = ""
    last_results = []
    patch_paths = [root / case.patch_file for case in eval_cases if case.patch_file]
    golden_paths = [root / golden.expected for case in eval_cases for golden in case.golden_files]
    watched_paths = extra_paths + patch_paths + golden_paths
    runs = 0
    try:
        while True:
            fingerprint = eval_watch_fingerprint(root, eval_cases, extra_paths=watched_paths)
            if fingerprint != last_fingerprint:
                runs += 1
                last_fingerprint = fingerprint
                last_results = _run_once(root, eval_cases, variant=variant, replay=replay)
                if until_pass and all(result.passed for result in last_results):
                    console.print("[green]✓[/] All eval cases pass; watch stopped.")
                    break
                if max_runs and runs >= max_runs:
                    break
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Eval watch stopped.[/]")
    return last_results


def _print_results(results) -> None:
    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("case", max_width=32)
    tbl.add_column("status", width=8)
    tbl.add_column("class", max_width=18)
    tbl.add_column("checks", justify="right")
    tbl.add_column("changed", justify="right")
    tbl.add_column("lines", justify="right")
    tbl.add_column("time", justify="right")

    for result in results:
        status = "[green]pass[/]" if result.passed else "[red]fail[/]"
        failed = len(result.failed_checks)
        checks = f"{len(result.checks) - failed}/{len(result.checks)}"
        tbl.add_row(
            result.case.id,
            status,
            result.case.failure_class,
            checks,
            str(len(result.changed_files)),
            str(result.changed_lines),
            f"{result.duration_s:.2f}s",
        )

    console.print(tbl)
    for result in results:
        for check in result.failed_checks:
            detail = f": {check.detail}" if check.detail else ""
            console.print(f"  [red]![/] {result.case.id} / {check.name}{detail}", soft_wrap=True)


def _print_variant_comparison(root: Path, compare_variants: str) -> None:
    try:
        baseline, variant = compare_variants.split(":", 1)
    except ValueError as exc:
        console.print("[red]--compare-variants must use BASELINE:VARIANT, e.g. baseline:agentpack[/]")
        raise typer.Exit(1) from exc
    records = load_eval_result_records(eval_results_path(root))
    comparison = compare_eval_variants(records, baseline, variant)

    tbl = Table(title=f"Eval Variant Comparison: {baseline} → {variant}", box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("case", max_width=36)
    tbl.add_column(baseline, justify="center")
    tbl.add_column(variant, justify="center")
    tbl.add_column("status", max_width=12)
    for row in comparison["rows"]:
        tbl.add_row(
            row["case_id"],
            _pass_label(row["baseline_passed"]),
            _pass_label(row["variant_passed"]),
            row["status"],
        )
    console.print(tbl)
    console.print(
        f"  improved [bold green]{comparison['improved']}[/]  "
        f"regressed [bold red]{comparison['regressed']}[/]  "
        f"unchanged [bold]{comparison['unchanged']}[/]  "
        f"incomplete [bold yellow]{comparison['incomplete']}[/]"
    )


def _pass_label(value) -> str:
    if value is True:
        return "[green]pass[/]"
    if value is False:
        return "[red]fail[/]"
    return "[yellow]-[/]"


def _run_memory_ab(root: Path, eval_cases, *, run_checks: bool = False, replay: bool = False) -> dict:
    rows = []
    regressed = 0
    memory_signal_selected_total = 0
    memory_signal_compacted_total = 0
    for case in eval_cases:
        baseline_details = _plan_with_memory_details(root, case, "off")
        memory_details = _plan_with_memory_details(root, case, "auto")
        baseline = baseline_details["paths"]
        memory = memory_details["paths"]
        baseline_passed = None
        memory_passed = None
        if run_checks:
            baseline_passed = _run_case_with_memory(root, case, "off", replay=replay)
            memory_passed = _run_case_with_memory(root, case, "auto", replay=replay)
        required = set(case.required_changed_files)
        base_selected = set(baseline)
        memory_selected = set(memory)
        base_hits = len(required & base_selected)
        memory_hits = len(required & memory_selected)
        base_noise = len(base_selected - required) if required else len(base_selected)
        memory_noise = len(memory_selected - required) if required else len(memory_selected)
        status = "same"
        row_regressed = False
        if memory_hits > base_hits:
            status = "improved"
        elif memory_hits < base_hits or memory_noise > base_noise + 5:
            status = "regressed"
            row_regressed = True
        if baseline_passed is True and memory_passed is False:
            status = "regressed"
            row_regressed = True
        memory_signal_selected = int(memory_details["memory_signal_selected_files"])
        memory_signal_compacted = int(memory_details["memory_signal_compacted_files"])
        memory_signal_selected_total += memory_signal_selected
        memory_signal_compacted_total += memory_signal_compacted
        if memory_signal_compacted > 0:
            status = "regressed"
            row_regressed = True
        if row_regressed:
            regressed += 1
        rows.append({
            "case": case.id,
            "required": len(required),
            "baseline_hits": base_hits,
            "memory_hits": memory_hits,
            "baseline_noise": base_noise,
            "memory_noise": memory_noise,
            "memory_signal_selected_files": memory_signal_selected,
            "memory_signal_compacted_files": memory_signal_compacted,
            "baseline_passed": baseline_passed,
            "memory_passed": memory_passed,
            "status": status,
        })

    tbl = Table(title="Memory A/B Context Selection", box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("case", max_width=32)
    tbl.add_column("required", justify="right")
    tbl.add_column("base hits", justify="right")
    tbl.add_column("mem hits", justify="right")
    tbl.add_column("base noise", justify="right")
    tbl.add_column("mem noise", justify="right")
    tbl.add_column("mem signal", justify="right")
    tbl.add_column("mem compacted", justify="right")
    if run_checks:
        tbl.add_column("base pass", justify="center")
        tbl.add_column("mem pass", justify="center")
    tbl.add_column("status")
    for row in rows:
        style = "[red]" if row["status"] == "regressed" else "[green]" if row["status"] == "improved" else ""
        end = "[/]" if style else ""
        cells = [
            row["case"],
            str(row["required"]),
            str(row["baseline_hits"]),
            str(row["memory_hits"]),
            str(row["baseline_noise"]),
            str(row["memory_noise"]),
            str(row["memory_signal_selected_files"]),
            str(row["memory_signal_compacted_files"]),
        ]
        if run_checks:
            cells.extend([_pass_label(row["baseline_passed"]), _pass_label(row["memory_passed"])])
        cells.append(f"{style}{row['status']}{end}")
        tbl.add_row(*cells)
    console.print(tbl)
    return {
        "rows": rows,
        "regressed": regressed,
        "memory_signal_selected_files": memory_signal_selected_total,
        "memory_signal_compacted_files": memory_signal_compacted_total,
        "memory_signals_tested": memory_signal_selected_total > 0,
    }


def _memory_ab_prove_target_failure(comparison: dict) -> str:
    if int(comparison.get("regressed") or 0) > 0:
        return "memory feedback regressed selection, checks, or memory-confirmed compaction"
    if not bool(comparison.get("memory_signals_tested")):
        return "no memory-confirmed selected files; seed episodic or ranking memory before using --prove-targets"
    return ""


def _plan_with_memory(root: Path, case, memory_feedback: str) -> list[str]:
    return list(_plan_with_memory_details(root, case, memory_feedback)["paths"])


def _plan_with_memory_details(root: Path, case, memory_feedback: str) -> dict[str, object]:
    previous = os.environ.get("AGENTPACK_MEMORY_FEEDBACK")
    os.environ["AGENTPACK_MEMORY_FEEDBACK"] = memory_feedback
    try:
        plan = PackPlanner().plan(PackRequest(
            root=root,
            agent="generic",
            task=case.task,
            mode="balanced",
            budget=0,
            since=case.base_ref,
            refresh=False,
            task_source="eval_memory_ab",
        ))
        memory_signal_selected = 0
        memory_signal_compacted = 0
        for item in plan.selected:
            reasons = [str(reason) for reason in item.reasons]
            has_memory_signal = _memory_signal_reason(reasons)
            if has_memory_signal:
                memory_signal_selected += 1
                if any(reason.startswith("source-aware compacted") for reason in reasons):
                    memory_signal_compacted += 1
        return {
            "paths": [item.path for item in plan.selected],
            "memory_signal_selected_files": memory_signal_selected,
            "memory_signal_compacted_files": memory_signal_compacted,
        }
    finally:
        if previous is None:
            os.environ.pop("AGENTPACK_MEMORY_FEEDBACK", None)
        else:
            os.environ["AGENTPACK_MEMORY_FEEDBACK"] = previous


def _memory_signal_reason(reasons: list[str]) -> bool:
    return any(
        "episodic memory similar task" in reason
        or "learning feedback miss" in reason
        or "procedure=" in reason
        for reason in reasons
    )


def _run_case_with_memory(root: Path, case, memory_feedback: str, *, replay: bool) -> bool:
    previous = os.environ.get("AGENTPACK_MEMORY_FEEDBACK")
    os.environ["AGENTPACK_MEMORY_FEEDBACK"] = memory_feedback
    try:
        result = run_eval_suite(root, [case], variant=f"memory-{memory_feedback}", replay=replay)[0]
        return result.passed
    finally:
        if previous is None:
            os.environ.pop("AGENTPACK_MEMORY_FEEDBACK", None)
        else:
            os.environ["AGENTPACK_MEMORY_FEEDBACK"] = previous
