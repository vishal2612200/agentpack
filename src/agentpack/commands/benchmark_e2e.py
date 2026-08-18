from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import typer
from rich import box
from rich.table import Table

from agentpack import __version__
from agentpack.application.pack_service import PackRequest
from agentpack.commands._shared import console, _root
from agentpack.core import git
from agentpack.core.config import load_config
from agentpack.core.token_estimator import estimate_tokens

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class E2ECase:
    name: str
    repo: Path
    task: str
    test_command: str
    setup_command: str = ""
    protected_paths: list[str] = field(default_factory=list)
    expected_edit_paths: list[str] = field(default_factory=list)
    risk_category: str = "uncategorized"


@dataclass
class E2EResult:
    schema_version: int
    case: str
    strategy: str
    trial: int
    passed: bool
    duration_s: float
    input_tokens: int
    agent_output_tokens: int
    estimated_input_cost_usd: float
    estimated_output_cost_usd: float
    estimated_total_cost_usd: float
    agent_returncode: int
    test_returncode: int
    timed_out: bool
    agent_tool_calls: int
    time_to_first_expected_file_s: float | None
    changed_files: list[str]
    source_files_changed: list[str]
    test_files_changed: list[str]
    protected_files_changed: list[str]
    expected_files_touched: list[str]
    missing_expected_edits: list[str]
    unexpected_files_touched: list[str]
    agentpack_noise: list[str]
    agent_log_path: str
    test_log_path: str
    workdir: str
    risk_category: str = "uncategorized"
    agent_command: str = ""
    validation_command: str = ""
    model: str = ""
    run_id: str = ""
    tested_commit: str = ""
    case_fingerprint: str = ""
    setup_returncode: int = 0

def benchmark_e2e_init(
    output: str = typer.Option("", "--output", help="Output TOML path. Default: .agentpack/e2e_cases.toml."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Scaffold a guarded E2E benchmark suite for real coding agents."""
    root = _root()
    out_path = Path(output) if output else root / ".agentpack" / "e2e_cases.toml"
    if not out_path.is_absolute():
        out_path = root / out_path
    if out_path.exists() and not force:
        console.print(f"[yellow]E2E cases file already exists:[/] {out_path}")
        console.print("  Pass [bold]--force[/] to overwrite.")
        raise typer.Exit(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_e2e_cases_template(root), encoding="utf-8")
    console.print(f"[green]✓[/] Created [bold]{out_path}[/]")
    console.print("  Fill in setup/test commands, then run [bold]agentpack benchmark e2e[/].")


def _e2e_cases_template(root: Path) -> str:
    repo = json.dumps(str(root))
    categories = [
        ("caller_signature_change", "Function signature or behavior change requiring caller updates."),
        ("api_service_model_contract", "API route, service, serializer, and model contract change."),
        ("config_env_runtime", "Bug depends on config/env/default behavior."),
        ("deleted_or_renamed_file", "Snapshot or reference logic must handle deleted/renamed files."),
        ("omitted_related_test", "Related test is not obvious from the primary source file."),
        ("cross_package_monorepo", "Change spans workspace/package boundaries."),
        ("side_effect_eventing", "Fix needs awareness of emitted events, analytics, or side effects."),
        ("schema_migration_contract", "Schema/model/migration contract impacts runtime behavior."),
        ("generated_file_noise", "Generated or ignored files should not steer the fix."),
        ("broad_task_precision", "Broad task wording where noisy context hurts precision."),
    ]
    lines = [
        "# AgentPack guarded E2E benchmark cases",
        "#",
        "# Each case should protect validation tests from edits and name expected source files.",
        "# Run at least 3 trials across no-context, grep, agentpack-lite, hybrid, and agentpack.",
        "# Example:",
        "#   agentpack benchmark e2e --cases .agentpack/e2e_cases.toml \\",
        "#     --agent-command 'bash -lc \"codex exec --dangerously-bypass-approvals-and-sandbox --cd {repo} --skip-git-repo-check \\\"$(cat {prompt})\\\"\"' \\",
        "#     --strategies no-context,grep,agentpack-lite,hybrid,agentpack --trials 3",
        "",
    ]
    for name, description in categories:
        lines.extend([
            "# [[cases]]",
            f"# name = \"{name}\"",
            f"# risk_category = \"{name}\"",
            f"# repo = {repo}",
            f"# task = \"{description}\"",
            f"# setup_command = \"python /absolute/path/to/setup_{name}.py\"",
            "# test_command = \"PYTHONPATH=src pytest -q tests/path/to_targeted_test.py\"",
            "# protected_paths = [\"tests/path/to_targeted_test.py\"]",
            "# expected_edit_paths = [\"src/path/to_expected_source.py\"]",
            "",
        ])
    return "\n".join(lines)


def benchmark_e2e(
    cases: str = typer.Option(..., "--cases", help="TOML file with [[cases]] entries."),
    agent_command: str = typer.Option(..., "--agent-command", help="Agent command. Use {prompt} and {repo} placeholders, or prompt path is appended."),
    strategies: str = typer.Option("no-context,grep,agentpack-lite,hybrid,agentpack", "--strategies", help="Comma-separated: no-context,grep,agentpack-lite,hybrid,agentpack."),
    trials: int = typer.Option(1, "--trials", help="Runs per case per strategy."),
    timeout: int = typer.Option(300, "--timeout", help="Agent command timeout seconds."),
    input_cost_per_mtok: float = typer.Option(0.0, "--input-cost-per-mtok", help="Optional input token price in USD per 1M tokens."),
    output_cost_per_mtok: float = typer.Option(0.0, "--output-cost-per-mtok", help="Optional output token price in USD per 1M tokens."),
    output: str = typer.Option("", "--output", help="JSONL output path. Default: .agentpack/e2e_results.jsonl"),
    keep_workdirs: bool = typer.Option(False, "--keep-workdirs", help="Keep temp workdirs for failed-result inspection."),
) -> None:
    """Run real coding-agent E2E evals and judge by test command pass/fail."""
    root = _root()
    parsed_cases = _load_e2e_cases(root, Path(cases))
    wanted_strategies = [item.strip() for item in strategies.split(",") if item.strip()]
    unknown = set(wanted_strategies) - {"no-context", "grep", "agentpack-lite", "hybrid", "agentpack"}
    if unknown:
        raise typer.BadParameter(f"Unknown strategy: {', '.join(sorted(unknown))}")
    if trials < 1:
        raise typer.BadParameter("--trials must be >= 1")

    out_path = Path(output) if output else root / ".agentpack" / "e2e_results.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = "e2e-" + uuid4().hex
    tested_commit = git.current_sha(root) or "unknown"
    results: list[E2EResult] = []

    for case in parsed_cases:
        for strategy in wanted_strategies:
            for trial in range(1, trials + 1):
                result = _run_e2e_case(
                    case,
                    strategy=strategy,
                    trial=trial,
                    agent_command=agent_command,
                    timeout=timeout,
                    input_cost_per_mtok=input_cost_per_mtok,
                    output_cost_per_mtok=output_cost_per_mtok,
                    keep_workdir=keep_workdirs,
                    run_id=run_id,
                    tested_commit=tested_commit,
                )
                results.append(result)

    manifest = {
        "record_type": "manifest",
        "schema_version": 1,
        "run_id": run_id,
        "tested_commit": tested_commit,
        "agentpack_version": __version__,
        "strategies": wanted_strategies,
        "trials": trials,
        "cases": sorted({result.case for result in results}),
        "risk_categories": sorted({result.risk_category for result in results}),
    }
    temporary = out_path.with_name(f".{out_path.name}.{run_id}.tmp")
    temporary.write_text(
        "\n".join([json.dumps(manifest, sort_keys=True), *(json.dumps(result.__dict__, sort_keys=True) for result in results)]) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, out_path)

    _print_e2e_summary(results, out_path)


def benchmark_e2e_report(
    results: str = typer.Option("", "--results", help="JSONL output from `agentpack benchmark e2e`. Default: .agentpack/e2e_results.jsonl."),
    baseline: str = typer.Option("no-context", "--baseline", help="Baseline strategy, usually no-context."),
    treatment: str = typer.Option("agentpack", "--treatment", help="Treatment strategy, usually agentpack."),
    markdown: bool = typer.Option(False, "--markdown", help="Print a Markdown report instead of a console table."),
    output: str = typer.Option("", "--output", help="Write Markdown report to this path."),
) -> None:
    """Compare AgentPack vs no-AgentPack E2E benchmark runs."""
    root = _root()
    path = Path(results) if results else root / ".agentpack" / "e2e_results.jsonl"
    if not path.is_absolute():
        path = root / path
    records = _load_e2e_result_records(path)
    if not records:
        console.print(f"[yellow]No E2E results found at {path}[/]")
        raise typer.Exit(1)
    from agentpack import __version__

    report = _e2e_ab_markdown(
        records,
        baseline=baseline,
        treatment=treatment,
        source=path,
        version=__version__,
        tested_commit=git.current_sha(root) or "unknown",
    )
    if output:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        console.print(f"[green]✓[/] Wrote E2E report: [bold]{output_path}[/]")
        return
    if markdown:
        console.print(report)
        return
    _print_e2e_ab_table(records, baseline=baseline, treatment=treatment, source=path)


def _load_e2e_result_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict) and row.get("record_type") == "manifest":
                continue
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def _e2e_strategy_metrics(records: list[dict[str, Any]], strategy: str) -> dict[str, float]:
    subset = [row for row in records if row.get("strategy") == strategy]
    if not subset:
        return {"runs": 0.0}

    def avg(key: str) -> float:
        return sum(float(row.get(key) or 0.0) for row in subset) / len(subset)

    first_file_values = [
        float(row["time_to_first_expected_file_s"])
        for row in subset
        if row.get("time_to_first_expected_file_s") is not None
    ]
    expected_rows = [
        row
        for row in subset
        if row.get("expected_files_touched") or row.get("missing_expected_edits")
    ]
    expected_touch_rate = (
        sum(1 for row in expected_rows if row.get("expected_files_touched")) / len(expected_rows)
        if expected_rows
        else 0.0
    )
    return {
        "runs": float(len(subset)),
        "success_rate": sum(1 for row in subset if row.get("passed")) / len(subset),
        "noise_rate": sum(1 for row in subset if row.get("agentpack_noise")) / len(subset),
        "expected_touch_rate": expected_touch_rate,
        "avg_input_tokens": avg("input_tokens"),
        "avg_output_tokens": avg("agent_output_tokens"),
        "avg_total_tokens": avg("input_tokens") + avg("agent_output_tokens"),
        "avg_total_cost_usd": avg("estimated_total_cost_usd"),
        "avg_duration_s": avg("duration_s"),
        "avg_tool_calls": avg("agent_tool_calls"),
        "avg_time_to_first_expected_file_s": (
            sum(first_file_values) / len(first_file_values)
            if first_file_values
            else 0.0
        ),
    }


def _e2e_ab_metrics(records: list[dict[str, Any]], *, baseline: str, treatment: str) -> dict[str, Any]:
    base = _e2e_strategy_metrics(records, baseline)
    treat = _e2e_strategy_metrics(records, treatment)
    if not base.get("runs") or not treat.get("runs"):
        return {"baseline": base, "treatment": treat, "deltas": {}}
    return {
        "baseline": base,
        "treatment": treat,
        "deltas": {
            "success_rate_pp": (treat["success_rate"] - base["success_rate"]) * 100,
            "task_success_saved": treat["success_rate"] - base["success_rate"],
            "tool_calls_saved": base["avg_tool_calls"] - treat["avg_tool_calls"],
            "token_cost_saved_usd": base["avg_total_cost_usd"] - treat["avg_total_cost_usd"],
            "tokens_saved": base["avg_total_tokens"] - treat["avg_total_tokens"],
            "time_to_first_correct_file_saved_s": (
                base["avg_time_to_first_expected_file_s"] - treat["avg_time_to_first_expected_file_s"]
            ),
            "duration_saved_s": base["avg_duration_s"] - treat["avg_duration_s"],
        },
    }


def _print_e2e_ab_table(
    records: list[dict[str, Any]],
    *,
    baseline: str,
    treatment: str,
    source: Path,
) -> None:
    metrics = _e2e_ab_metrics(records, baseline=baseline, treatment=treatment)
    table = Table(title=f"E2E A/B: {baseline} vs {treatment}", box=box.SIMPLE, show_header=True)
    table.add_column("metric")
    table.add_column(baseline, justify="right")
    table.add_column(treatment, justify="right")
    table.add_column("saved / lift", justify="right")
    base = metrics["baseline"]
    treat = metrics["treatment"]
    deltas = metrics["deltas"]
    if not deltas:
        console.print(f"[yellow]Need results for both {baseline} and {treatment} in {source}[/]")
        return
    table.add_row("runs", f"{base['runs']:.0f}", f"{treat['runs']:.0f}", "-")
    table.add_row("task success", f"{base['success_rate']:.0%}", f"{treat['success_rate']:.0%}", f"{deltas['success_rate_pp']:+.1f} pp")
    table.add_row("expected file touched", f"{base['expected_touch_rate']:.0%}", f"{treat['expected_touch_rate']:.0%}", "-")
    table.add_row("AgentPack noise cases", f"{base['noise_rate']:.0%}", f"{treat['noise_rate']:.0%}", "-")
    table.add_row("tool calls", f"{base['avg_tool_calls']:.1f}", f"{treat['avg_tool_calls']:.1f}", f"{deltas['tool_calls_saved']:+.1f}")
    table.add_row("tokens", f"{base['avg_total_tokens']:,.0f}", f"{treat['avg_total_tokens']:,.0f}", f"{deltas['tokens_saved']:+,.0f}")
    table.add_row("cost", f"${base['avg_total_cost_usd']:.4f}", f"${treat['avg_total_cost_usd']:.4f}", _fmt_signed_usd(deltas["token_cost_saved_usd"]))
    table.add_row("time to first correct file", f"{base['avg_time_to_first_expected_file_s']:.1f}s", f"{treat['avg_time_to_first_expected_file_s']:.1f}s", f"{deltas['time_to_first_correct_file_saved_s']:+.1f}s")
    table.add_row("duration", f"{base['avg_duration_s']:.1f}s", f"{treat['avg_duration_s']:.1f}s", f"{deltas['duration_saved_s']:+.1f}s")
    console.print(table)
    console.print(f"[dim]Source: {source}[/]")


def _e2e_ab_markdown(
    records: list[dict[str, Any]],
    *,
    baseline: str,
    treatment: str,
    source: Path,
    version: str = "",
    tested_commit: str = "",
) -> str:
    metrics = _e2e_ab_metrics(records, baseline=baseline, treatment=treatment)
    base = metrics["baseline"]
    treat = metrics["treatment"]
    deltas = metrics["deltas"]
    if not deltas:
        return f"Need results for both `{baseline}` and `{treatment}` in `{source}`.\n"
    base_records = [row for row in records if row.get("strategy") == baseline]
    treatment_records = [row for row in records if row.get("strategy") == treatment]
    case_names = {str(row.get("case")) for row in records if row.get("case")}
    categories = sorted({
        str(row.get("risk_category"))
        for row in records
        if row.get("risk_category") and row.get("risk_category") != "uncategorized"
    })
    agent_configs = sorted({str(row.get("agent_command")) for row in records if row.get("agent_command")})
    models = sorted({str(row.get("model")) for row in records if row.get("model")})
    run_ids = sorted({str(row.get("run_id")) for row in records if row.get("run_id")})
    record_commits = sorted({str(row.get("tested_commit")) for row in records if row.get("tested_commit")})
    reported_commit = record_commits[0] if len(record_commits) == 1 else tested_commit
    trials = min(len(base_records), len(treatment_records)) // max(len(case_names), 1)
    case_rows = [
        (case_name, next((str(row.get("risk_category")) for row in records if row.get("case") == case_name), "uncategorized"),
         sum(1 for row in base_records if row.get("case") == case_name),
         sum(1 for row in treatment_records if row.get("case") == case_name))
        for case_name in sorted(case_names)
    ]
    lines = [
        f"# AgentPack E2E A/B: {baseline} vs {treatment}",
        "",
        f"- agentpack version: {version or 'unspecified'}",
        f"- tested commit: {reported_commit or 'unspecified'}",
        f"- run id: {', '.join(run_ids) if run_ids else 'unspecified'}",
        f"- baseline: {baseline}",
        f"- treatment: {treatment}",
        f"- cases: {len(case_names)}",
        f"- risk categories: {', '.join(categories) if categories else 'unspecified'}",
        f"- trials per strategy: {trials}",
        f"- total runs: {len(records)}",
        f"- agent/model configuration: {', '.join(agent_configs) if agent_configs else 'unspecified'}; model={', '.join(models) if models else 'unspecified'}",
        f"- source: `{source}`",
        "",
        "<!-- agentpack-e2e-manifest: "
        + json.dumps(
            {
                "schema_version": 1,
                "run_ids": run_ids,
                "tested_commits": record_commits or ([tested_commit] if tested_commit else []),
                "baseline": baseline,
                "treatment": treatment,
                "cases": sorted(case_names),
                "risk_categories": categories,
                "trials_per_strategy": trials,
                "total_runs": len(records),
            },
            sort_keys=True,
        )
        + " -->",
        "",
        "| Metric | Baseline | AgentPack | Saved / lift |",
        "|---|---:|---:|---:|",
        f"| runs | {base['runs']:.0f} | {treat['runs']:.0f} | - |",
        f"| task success | {base['success_rate']:.0%} | {treat['success_rate']:.0%} | {deltas['success_rate_pp']:+.1f} pp |",
        f"| expected file touched | {base['expected_touch_rate']:.0%} | {treat['expected_touch_rate']:.0%} | - |",
        f"| AgentPack noise cases | {base['noise_rate']:.0%} | {treat['noise_rate']:.0%} | - |",
        f"| tool calls | {base['avg_tool_calls']:.1f} | {treat['avg_tool_calls']:.1f} | {deltas['tool_calls_saved']:+.1f} |",
        f"| tokens | {base['avg_total_tokens']:,.0f} | {treat['avg_total_tokens']:,.0f} | {deltas['tokens_saved']:+,.0f} |",
        f"| token cost | ${base['avg_total_cost_usd']:.4f} | ${treat['avg_total_cost_usd']:.4f} | {_fmt_signed_usd(deltas['token_cost_saved_usd'])} |",
        f"| time to first correct file | {base['avg_time_to_first_expected_file_s']:.1f}s | {treat['avg_time_to_first_expected_file_s']:.1f}s | {deltas['time_to_first_correct_file_saved_s']:+.1f}s |",
        f"| duration | {base['avg_duration_s']:.1f}s | {treat['avg_duration_s']:.1f}s | {deltas['duration_saved_s']:+.1f}s |",
        "",
        "## Trial matrix",
        "",
        "| Case | Risk category | Baseline trials | AgentPack trials |",
        "|---|---|---:|---:|",
    ]
    lines.extend(
        f"| `{case_name}` | {category} | {base_trials} | {treatment_trials} |"
        for case_name, category, base_trials, treatment_trials in case_rows
    )
    lines.append("")
    return "\n".join(lines)


def _fmt_signed_usd(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):.4f}"


def _load_e2e_cases(root: Path, path: Path) -> list[E2ECase]:
    case_path = path if path.is_absolute() else root / path
    data = tomllib.loads(case_path.read_text(encoding="utf-8"))
    cases: list[E2ECase] = []
    for raw in data.get("cases") or []:
        repo_value = raw.get("repo")
        if not repo_value:
            raise ValueError(f"Case {raw.get('name') or '<unnamed>'} missing repo")
        repo = Path(str(repo_value))
        if not repo.is_absolute():
            repo = (case_path.parent / repo).resolve()
        cases.append(
            E2ECase(
                name=str(raw.get("name") or repo.name),
                repo=repo,
                task=str(raw.get("task") or ""),
                test_command=str(raw.get("test_command") or ""),
                setup_command=str(raw.get("setup_command") or ""),
                protected_paths=[str(path) for path in raw.get("protected_paths", [])],
                expected_edit_paths=[str(path) for path in raw.get("expected_edit_paths", [])],
                risk_category=str(raw.get("risk_category") or "uncategorized"),
            )
        )
    if not cases:
        raise ValueError(f"No [[cases]] found in {case_path}")
    return cases


def _run_e2e_case(
    case: E2ECase,
    *,
    strategy: str,
    trial: int,
    agent_command: str,
    timeout: int,
    keep_workdir: bool,
    input_cost_per_mtok: float = 0.0,
    output_cost_per_mtok: float = 0.0,
    run_id: str = "",
    tested_commit: str = "",
) -> E2EResult:
    start = time.perf_counter()
    work_root = Path(tempfile.mkdtemp(prefix=f"agentpack-e2e-{case.name}-{strategy}-"))
    repo = work_root / "repo"
    shutil.copytree(case.repo, repo, ignore=shutil.ignore_patterns(".git", ".agentpack", "__pycache__", ".pytest_cache"))
    _init_e2e_git(repo)
    setup_returncode = 0
    setup_output = ""
    if case.setup_command:
        try:
            setup = subprocess.run(case.setup_command, cwd=repo, shell=True, capture_output=True, text=True, timeout=timeout)
            setup_returncode = setup.returncode
            setup_output = "\n".join((setup.stdout, setup.stderr)).strip()
        except subprocess.TimeoutExpired as exc:
            setup_returncode = 124
            setup_output = f"setup timed out after {exc.timeout} seconds"
    protected_hashes = _hash_protected_paths(repo, case.protected_paths)

    prompt = _e2e_prompt(case, strategy, repo)
    prompt_path = repo / ".agentpack_e2e_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    agent_args = _agent_args(agent_command, prompt_path, repo)
    timed_out = False
    agent_start_epoch = time.time()
    if setup_returncode:
        agent = subprocess.CompletedProcess(agent_args, setup_returncode, "", f"setup failed: {setup_output}")
    else:
        try:
            agent = subprocess.run(agent_args, cwd=repo, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            agent = _timeout_result(agent_args, exc)
    agent_log_path = work_root / "agent.log"
    test_log_path = work_root / "test.log"
    _write_e2e_process_log(agent_log_path, agent)
    if setup_returncode:
        test = subprocess.CompletedProcess(case.test_command, setup_returncode, "", "skipped because setup failed")
    else:
        try:
            test = subprocess.run(case.test_command, cwd=repo, shell=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            test = _timeout_result(case.test_command, exc)
    _write_e2e_process_log(test_log_path, test)
    input_tokens = estimate_tokens(prompt)
    agent_output_tokens = _process_output_tokens(agent)
    input_cost = _estimate_token_cost(input_tokens, input_cost_per_mtok)
    output_cost = _estimate_token_cost(agent_output_tokens, output_cost_per_mtok)
    changed = sorted(git.dirty_files(repo)) if git.is_git_repo(repo) else []
    public_changed = _public_changed_files(changed)
    source_changed = [path for path in public_changed if not _is_test_path(path)]
    test_changed = [path for path in public_changed if _is_test_path(path)]
    protected_changed = _changed_protected_paths(repo, protected_hashes)
    expected_touched = _expected_files_touched(public_changed, case.expected_edit_paths)
    missing_expected = sorted(set(case.expected_edit_paths) - set(expected_touched))
    unexpected_touched = _unexpected_files_touched(public_changed, case.expected_edit_paths)
    agentpack_noise = _agentpack_noise(strategy, unexpected_touched, missing_expected, public_changed)
    time_to_first_expected_file = _time_to_first_expected_file(repo, expected_touched, agent_start_epoch)
    tool_calls = _estimate_agent_tool_calls(agent)
    duration = time.perf_counter() - start
    passed = not setup_returncode and not timed_out and agent.returncode == 0 and test.returncode == 0 and not protected_changed

    if not keep_workdir and passed:
        shutil.rmtree(work_root, ignore_errors=True)

    return E2EResult(
        schema_version=2,
        case=case.name,
        strategy=strategy,
        trial=trial,
        passed=passed,
        duration_s=round(duration, 3),
        input_tokens=input_tokens,
        agent_output_tokens=agent_output_tokens,
        estimated_input_cost_usd=round(input_cost, 8),
        estimated_output_cost_usd=round(output_cost, 8),
        estimated_total_cost_usd=round(input_cost + output_cost, 8),
        agent_returncode=agent.returncode,
        test_returncode=test.returncode,
        timed_out=timed_out,
        agent_tool_calls=tool_calls,
        time_to_first_expected_file_s=round(time_to_first_expected_file, 3) if time_to_first_expected_file is not None else None,
        changed_files=changed,
        source_files_changed=source_changed,
        test_files_changed=test_changed,
        protected_files_changed=protected_changed,
        expected_files_touched=expected_touched,
        missing_expected_edits=missing_expected,
        unexpected_files_touched=unexpected_touched,
        agentpack_noise=agentpack_noise,
        agent_log_path=str(agent_log_path),
        test_log_path=str(test_log_path),
        workdir=str(work_root),
        risk_category=case.risk_category,
        agent_command=agent_command,
        validation_command=case.test_command,
        model=os.environ.get("AGENTPACK_E2E_MODEL", "unspecified"),
        run_id=run_id,
        tested_commit=tested_commit,
        case_fingerprint=_e2e_case_fingerprint(case),
        setup_returncode=setup_returncode,
    )


def _e2e_case_fingerprint(case: E2ECase) -> str:
    payload = json.dumps(
        {
            "name": case.name,
            "task": case.task,
            "test_command": case.test_command,
            "setup_command": case.setup_command,
            "protected_paths": sorted(case.protected_paths),
            "expected_edit_paths": sorted(case.expected_edit_paths),
            "risk_category": case.risk_category,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _public_changed_files(changed: list[str]) -> list[str]:
    internal = {".agentpack_e2e_prompt.txt"}
    return sorted(path for path in changed if path not in internal and not _is_generated_e2e_path(path))


def _is_generated_e2e_path(path: str) -> bool:
    return (
        path == ".agentpack"
        or path.startswith(".agentpack/")
        or path == ".agentpack/"
        or "__pycache__/" in path
        or path.endswith("__pycache__/")
        or path.endswith(".pyc")
    )


def _is_test_path(path: str) -> bool:
    name = Path(path).name
    parts = {part.lower() for part in Path(path.lower()).parts}
    return (
        path.startswith("tests/")
        or "test" in parts
        or "/tests/" in path
        or "__test__/" in path
        or "__tests__/" in path
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
    )


def _expected_files_touched(changed: list[str], expected_edit_paths: list[str]) -> list[str]:
    expected = set(expected_edit_paths)
    return sorted(path for path in changed if path in expected)


def _unexpected_files_touched(changed: list[str], expected_edit_paths: list[str]) -> list[str]:
    if not expected_edit_paths:
        return []
    expected = set(expected_edit_paths)
    return sorted(path for path in changed if path not in expected)


def _agentpack_noise(strategy: str, unexpected: list[str], missing_expected: list[str], changed: list[str]) -> list[str]:
    if "agentpack" not in strategy:
        return []
    noise: list[str] = []
    if unexpected:
        noise.append(f"unexpected edits: {', '.join(unexpected[:5])}")
    if missing_expected:
        noise.append(f"missed expected files: {', '.join(missing_expected[:5])}")
    generated = [path for path in changed if _is_generated_e2e_path(path)]
    if generated:
        noise.append(f"generated AgentPack files changed: {', '.join(generated[:5])}")
    return noise


def _process_output_tokens(result: subprocess.CompletedProcess[str]) -> int:
    return estimate_tokens("\n".join(part for part in (result.stdout, result.stderr) if part))


def _estimate_agent_tool_calls(result: subprocess.CompletedProcess[str]) -> int:
    text = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if not text:
        return 0
    patterns = [
        r"\btool[_ -]?call\b",
        r"\bexec_command\b",
        r"\bapply_patch\b",
        r"\bread_file\b",
        r"\bwrite_file\b",
        r"\blist_files\b",
        r"\bsearch\b",
        r"\brg\b",
    ]
    return sum(len(re.findall(pattern, text, flags=re.IGNORECASE)) for pattern in patterns)


def _time_to_first_expected_file(repo: Path, expected_touched: list[str], agent_start_epoch: float) -> float | None:
    deltas: list[float] = []
    for path in expected_touched:
        target = repo / path
        if not target.exists():
            continue
        try:
            delta = target.stat().st_mtime - agent_start_epoch
        except OSError:
            continue
        if delta >= 0:
            deltas.append(delta)
    return min(deltas) if deltas else None


def _estimate_token_cost(tokens: int, cost_per_mtok: float) -> float:
    if tokens <= 0 or cost_per_mtok <= 0:
        return 0.0
    return tokens / 1_000_000 * cost_per_mtok


def _hash_protected_paths(repo: Path, paths: list[str]) -> dict[str, str | None]:
    return {path: _file_sha256(repo / path) for path in paths}


def _changed_protected_paths(repo: Path, before: dict[str, str | None]) -> list[str]:
    return [
        path
        for path, expected in before.items()
        if _file_sha256(repo / path) != expected
    ]


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_e2e_process_log(path: Path, result: subprocess.CompletedProcess[str]) -> None:
    path.write_text(
        "\n".join([
            f"returncode={result.returncode}",
            "",
            "STDOUT:",
            result.stdout,
            "",
            "STDERR:",
            result.stderr,
        ]),
        encoding="utf-8",
    )


def _timeout_result(
    args: str | list[str],
    exc: subprocess.TimeoutExpired,
) -> subprocess.CompletedProcess[str]:
    stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
    stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
    return subprocess.CompletedProcess(
        args=args,
        returncode=124,
        stdout=stdout,
        stderr=stderr + f"\nTimed out after {exc.timeout} seconds.",
    )


def _e2e_prompt(case: E2ECase, strategy: str, repo: Path) -> str:
    from agentpack.commands import benchmark as benchmark_compat

    base = (
        f"Task: {case.task}\n\n"
        "Edit the repository to complete the task. Keep changes minimal. "
        f"After editing, the validation command should pass: `{case.test_command}`.\n"
    )
    if strategy == "no-context":
        return base
    if strategy == "grep":
        return base + "\nRelevant grep output:\n" + benchmark_compat._grep_context(case.task, repo)
    if strategy == "agentpack-lite":
        return base + "\nAgentPack lite context:\n" + benchmark_compat._agentpack_lite_context(case, repo)
    if strategy == "hybrid":
        return (
            base
            + "\nRelevant grep output:\n"
            + benchmark_compat._grep_context(case.task, repo)
            + "\n\nAgentPack lite context:\n"
            + benchmark_compat._agentpack_lite_context(case, repo)
        )
    if strategy == "agentpack":
        result = benchmark_compat.PackService().run(PackRequest(
            root=repo,
            agent="generic",
            task=case.task,
            mode="balanced",
            budget=40000,
            since=None,
            refresh=False,
            task_source="e2e",
        ))
        context = result.out_path.read_text(encoding="utf-8") if result.out_path.exists() else ""
        return base + "\nAgentPack context:\n" + context
    raise ValueError(f"unknown strategy: {strategy}")


def _agentpack_lite_context(case: E2ECase, repo: Path) -> str:
    from agentpack.commands import benchmark as benchmark_compat

    lite = load_config(repo).context_lite
    result = benchmark_compat.PackService().run(PackRequest(
        root=repo,
        agent="generic",
        task=case.task,
        mode="lite",
        budget=lite.budget,
        since=None,
        refresh=False,
        task_source="e2e-lite",
    ))
    pack = result.pack
    lines = [
        "Purpose: cheap repo situational awareness. Inspect files before editing; omitted paths are warnings, not evidence.",
        "",
        "## Selected File Map",
        "| File | Mode | Score | Why |",
        "|---|---|---:|---|",
    ]
    for selected in pack.selected_files[:lite.max_selected_files]:
        why = ", ".join(selected.reasons[:3]) or "-"
        lines.append(f"| `{selected.path}` | {selected.include_mode} | {selected.score:.0f} | {why} |")

    if pack.omitted_relevant_files:
        lines.extend([
            "",
            "## High-Risk Omitted Files",
            "| File | Risk | Score | Why |",
            "|---|---|---:|---|",
        ])
        for omitted in pack.omitted_relevant_files[:lite.max_omitted_files]:
            why = ", ".join(omitted.reasons[:3]) or omitted.omission_reason
            lines.append(f"| `{omitted.path}` | {omitted.risk.upper()} | {omitted.score:.0f} | {why} |")

    if pack.changed_files:
        lines.extend(["", "## Changed Files"])
        lines.extend(f"- `{path}`" for path in pack.changed_files[:15])

    stubs = _lite_file_stubs(pack.selected_files[:lite.max_stubs], summary_chars=lite.summary_chars)
    if stubs:
        lines.extend(["", "## File Stubs", *stubs])

    return "\n".join(lines)


def _lite_file_stubs(selected_files: list[Any], *, summary_chars: int = 500) -> list[str]:
    lines: list[str] = []
    for selected in selected_files:
        parts = [f"### `{selected.path}`"]
        if selected.summary:
            parts.append(_truncate_line(selected.summary, summary_chars))
        signatures = [
            symbol.signature or f"{symbol.kind} {symbol.name}"
            for symbol in selected.symbols[:8]
        ]
        if signatures:
            parts.append("Symbols: " + "; ".join(signatures))
        if len(parts) > 1:
            lines.extend(parts)
    return lines


def _truncate_line(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _grep_context(task: str, repo: Path, *, max_lines: int = 120) -> str:
    terms = [term for term in task.replace("_", " ").replace("-", " ").split() if len(term) >= 4]
    if not terms:
        return "(no grep terms)"
    outputs: list[str] = []
    for term in terms[:8]:
        try:
            result = subprocess.run(
                ["rg", "-n", "--glob", "!.git", "--glob", "!.agentpack", term],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.stdout:
            outputs.extend(result.stdout.splitlines())
        if len(outputs) >= max_lines:
            break
    return "\n".join(outputs[:max_lines]) or "(no grep matches)"


def _agent_args(command: str, prompt_path: Path, repo: Path) -> list[str]:
    rendered = command.replace("{prompt}", str(prompt_path)).replace("{repo}", str(repo))
    args = shlex.split(rendered)
    if "{prompt}" not in command:
        args.append(str(prompt_path))
    return args


def _init_e2e_git(repo: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "agentpack@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "AgentPack E2E"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=repo, check=True)


def _print_e2e_summary(results: list[E2EResult], out_path: Path) -> None:
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("strategy")
    table.add_column("runs", justify="right")
    table.add_column("pass rate", justify="right")
    table.add_column("timeouts", justify="right")
    table.add_column("expected touch", justify="right")
    table.add_column("avg tokens", justify="right")
    table.add_column("avg cost", justify="right")
    table.add_column("avg tools", justify="right")
    table.add_column("first file", justify="right")
    table.add_column("avg seconds", justify="right")
    table.add_column("pass/min", justify="right")
    table.add_column("pass/$", justify="right")
    for strategy in sorted({result.strategy for result in results}):
        subset = [result for result in results if result.strategy == strategy]
        pass_rate = sum(1 for result in subset if result.passed) / len(subset)
        timeout_rate = sum(1 for result in subset if result.timed_out) / len(subset)
        expected_cases = [result for result in subset if result.expected_files_touched or result.missing_expected_edits]
        expected_touch_rate = (
            sum(1 for result in expected_cases if result.expected_files_touched) / len(expected_cases)
            if expected_cases
            else None
        )
        avg_tokens = sum(result.input_tokens for result in subset) / len(subset)
        avg_cost = sum(result.estimated_total_cost_usd for result in subset) / len(subset)
        avg_tools = sum(result.agent_tool_calls for result in subset) / len(subset)
        first_file_times = [
            result.time_to_first_expected_file_s
            for result in subset
            if result.time_to_first_expected_file_s is not None
        ]
        avg_first_file = sum(first_file_times) / len(first_file_times) if first_file_times else None
        avg_seconds = sum(result.duration_s for result in subset) / len(subset)
        total_seconds = sum(result.duration_s for result in subset)
        total_cost = sum(result.estimated_total_cost_usd for result in subset)
        pass_per_minute = (sum(1 for result in subset if result.passed) / total_seconds * 60) if total_seconds else 0.0
        pass_per_dollar = (sum(1 for result in subset if result.passed) / total_cost) if total_cost else 0.0
        table.add_row(
            strategy,
            str(len(subset)),
            f"{pass_rate:.0%}",
            f"{timeout_rate:.0%}",
            f"{expected_touch_rate:.0%}" if expected_touch_rate is not None else "-",
            f"{avg_tokens:,.0f}",
            f"${avg_cost:.4f}" if avg_cost else "-",
            f"{avg_tools:.1f}",
            f"{avg_first_file:.1f}s" if avg_first_file is not None else "-",
            f"{avg_seconds:.1f}",
            f"{pass_per_minute:.2f}",
            f"{pass_per_dollar:.2f}" if total_cost else "-",
        )
    console.print(table)
    console.print(f"[dim]JSONL: {out_path}[/]")




def register_e2e_commands(benchmark_app: typer.Typer) -> None:
    benchmark_app.command("e2e-init")(benchmark_e2e_init)
    benchmark_app.command("e2e")(benchmark_e2e)
    benchmark_app.command("e2e-report")(benchmark_e2e_report)
