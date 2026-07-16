from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import typer

from agentpack.commands._shared import console, _root, run_refresh
from agentpack.commands.guard import _context_is_fresh
from agentpack.core.command_surface import refresh_commands
from agentpack.core.config import load_config
from agentpack.core.loop_protocol import (
    LoopCommandResult,
    dry_run_plan,
    finish_blockers,
    initialize_loop,
    load_loop_state,
    mark_done,
    resolve_runner_adapter,
    run_loop,
)
from agentpack.core.thread_context import resolve_session_thread_option
from agentpack.integrations.platform import cli_module_argv
from agentpack.learning.task_memory import record_task_memory
from agentpack.session.events import record_event


def register(app: typer.Typer) -> None:
    @app.command("work")
    def work(
        task_text: str = typer.Argument(..., help="Task text to start."),
        thread: str = typer.Option("", "--thread", help="Use thread-scoped task/context state (auto by default in agent sessions; use 'global' for legacy global state)."),
        agent: str = typer.Option("auto", "--agent", help="Agent to initialize and refresh for."),
        mode: str = typer.Option("balanced", "--mode", help="Pack/guard mode."),
        budget: int = typer.Option(0, "--budget", help="Token budget (0 = config default)."),
        workspace: str = typer.Option("", "--workspace", help="Restrict pack to a monorepo workspace."),
        pack_only: bool = typer.Option(False, "--pack-only", help="Run pack directly instead of guard."),
        no_init: bool = typer.Option(False, "--no-init", help="Do not initialize the repo when .agentpack/config.toml is missing."),
        no_next: bool = typer.Option(False, "--no-next", help="Do not print next-step diagnostics after context refresh."),
        run_loop_requested: bool = typer.Option(False, "--run", help="Run the optional guarded loop after preparing context."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Plan guarded-loop execution without running the configured runner."),
        runner: str = typer.Option("", "--runner", help="Generic shell command for the optional guarded-loop runner."),
        runner_adapter: str = typer.Option("", "--runner-adapter", help="Resolve runner command from a known adapter: claude, codex, cursor."),
        max_iterations: int = typer.Option(0, "--max-iterations", help="Override [loop].max_iterations for this run."),
        verify: list[str] = typer.Option([], "--verify", help="Verification command for the guarded loop. Repeatable."),
        acceptance: list[str] = typer.Option([], "--acceptance", help="Semantic acceptance check for runner contract. Repeatable."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    ) -> None:
        """Initialize if needed, write a task, refresh context, and show next steps."""
        root = _root()
        thread_id = resolve_session_thread_option(thread)
        stages: list[dict[str, Any]] = []
        if not no_init and not (root / ".agentpack" / "config.toml").exists():
            stages.append(_run("init", cli_module_argv("init", "--yes", "--agent", agent), root))
            if stages[-1]["returncode"] != 0:
                _finish(stages, json_output)

        start_args = ["start", task_text, "--agent", agent, "--mode", mode]
        if thread_id:
            start_args.extend(["--thread", thread_id])
        if budget:
            start_args.extend(["--budget", str(budget)])
        if workspace:
            start_args.extend(["--workspace", workspace])
        if pack_only:
            start_args.append("--pack-only")
        stages.append(_run("start", cli_module_argv(*start_args), root))
        _record_task_memory_safe(
            root,
            task=task_text,
            stage="work_start",
            status="ready" if stages[-1]["returncode"] == 0 else "failed",
            thread=thread,
            summary=stages[-1].get("detail", ""),
        )
        if stages[-1]["returncode"] == 0 and not no_next and not run_loop_requested and not dry_run:
            stages.append(_run("next", cli_module_argv("next"), root))
        if stages[-1]["returncode"] != 0:
            _finish(stages, json_output)

        loop_plan = None
        loop_summary = None
        if run_loop_requested or dry_run:
            cfg = load_config(root)
            state = initialize_loop(
                root,
                task_text,
                cfg.loop,
                runner_override=runner or _resolve_runner_adapter(runner_adapter, root),
                max_iterations_override=max_iterations,
                verification_overrides=list(verify) if verify else None,
                acceptance_overrides=list(acceptance) if acceptance else None,
            )
            if runner_adapter:
                state.runner_adapter = runner_adapter
            if dry_run:
                loop_plan = dry_run_plan(root, state).model_dump(mode="json")
                _finish(stages, json_output, loop_plan=loop_plan)
                return
            if not state.runner:
                console.print("[red]Ralph Loop runner missing.[/] Set [loop].runner or pass --runner.")
                raise typer.Exit(1)
            loop_summary = run_loop(
                root,
                state,
                refresh=lambda: _refresh_loop_context(root, agent, mode, budget, thread_id),
            ).model_dump(mode="json")
            _record_task_memory_safe(
                root,
                task=task_text,
                stage="work_loop",
                status=str(loop_summary.get("status") or "unknown"),
                thread=thread,
                summary=str(loop_summary.get("reason") or loop_summary.get("next_command") or ""),
                loop_summary=loop_summary,
            )
            if loop_summary["status"] != "ready_to_finish":
                _finish(stages, json_output, loop_summary=loop_summary)
                raise typer.Exit(1)
        _finish(stages, json_output, loop_plan=loop_plan, loop_summary=loop_summary)

    @app.command("finish")
    def finish(
        since: str = typer.Option("", "--since", help="Git ref for benchmark capture, e.g. main or HEAD~1."),
        task: str = typer.Option("", "--task", help="Task text for benchmark capture and completion summary."),
        thread: str = typer.Option("", "--thread", help="Use thread-scoped state (auto by default in agent sessions; use 'global' for legacy global state)."),
        summary: str = typer.Option("Finished by agentpack finish.", "--summary", help="Completion summary."),
        skip_checks: bool = typer.Option(False, "--skip-checks", help="Skip agentpack dev-check."),
        skip_diagnosis: bool = typer.Option(False, "--skip-diagnosis", help="Skip selection diagnosis write."),
        skip_benchmark_capture: bool = typer.Option(False, "--skip-benchmark-capture", help="Skip benchmark case capture."),
        archive_thread: bool = typer.Option(False, "--archive-thread", help="Deprecated compatibility flag; session-scoped finish archives automatically."),
        allow_empty_capture: bool = typer.Option(False, "--allow-empty-capture", help="Allow benchmark capture with no expected files."),
        allow_high_risk: bool = typer.Option(False, "--allow-high-risk", help="Allow finish after inspecting a high-risk Ralph Loop diff."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    ) -> None:
        """Run finish checks, capture benchmark evidence, and mark work done."""
        root = _root()
        thread_id = resolve_session_thread_option(thread)
        stages: list[dict[str, Any]] = []
        loop_state = load_loop_state(root)
        cfg = load_config(root)
        finish_task = task or _read_task(root, thread_id or "") or (loop_state.task if loop_state else "")
        loop_applies = loop_state is not None and cfg.loop.enabled and (not finish_task or finish_task == loop_state.task)
        if loop_applies:
            blockers = _loop_finish_blockers(root, cfg.loop, loop_state, thread_id or "", allow_empty_diff=allow_empty_capture, allow_high_risk=allow_high_risk)
            if blockers:
                _finish_blocked(blockers, json_output)
                raise typer.Exit(1)

        if not skip_diagnosis:
            stages.append(_run("diagnose-selection", cli_module_argv("diagnose-selection", "--write"), root))
        if not skip_benchmark_capture and since:
            capture_task = task or _read_task(root, thread_id or "") or summary
            args = ["benchmark", "capture", "--since", since, "--task", capture_task]
            if allow_empty_capture:
                args.append("--allow-empty")
            stages.append(_run("benchmark-capture", cli_module_argv(*args), root))
        if stages and stages[-1]["returncode"] != 0:
            _finish(stages, json_output)
        if not skip_checks:
            stages.append(_run("dev-check", cli_module_argv("dev-check"), root))
            record_event(
                root,
                "check_completed",
                {
                    "task": finish_task,
                    "thread_id": thread_id or "",
                    "command": stages[-1]["command"],
                    "status": "passed" if stages[-1]["returncode"] == 0 else "failed",
                    "returncode": stages[-1]["returncode"],
                    "summary": stages[-1].get("detail", ""),
                },
                source="workflow",
            )
        if stages and stages[-1]["returncode"] != 0:
            _finish(stages, json_output)

        state_args = ["state", "done", "--summary", summary]
        if thread_id:
            state_args.extend(["--thread", thread_id])
        stages.append(_run("state-done", cli_module_argv(*state_args), root))
        if stages[-1]["returncode"] == 0 and loop_applies:
            mark_done(root, summary)
        if stages[-1]["returncode"] == 0:
            record_event(
                root,
                "task_completed",
                {"task": finish_task, "thread_id": thread_id or "", "summary": summary},
                source="workflow",
            )
        _record_task_memory_safe(
            root,
            task=finish_task,
            stage="finish",
            status="done" if stages[-1]["returncode"] == 0 else "failed",
            thread=thread_id or "",
            summary=summary,
        )
        if thread_id and stages[-1]["returncode"] == 0:
            stages.append(_run("threads-archive", cli_module_argv("threads", "archive", thread_id, "--summary", summary), root))
        _finish(stages, json_output)

    @app.command("loop-smoke")
    def loop_smoke(
        runner: str = typer.Option("", "--runner", help="Runner command to test against a tiny fixture repo."),
        runner_adapter: str = typer.Option("", "--runner-adapter", help="Resolve runner command from a known adapter."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    ) -> None:
        """Run an optional guarded-loop smoke test in a temporary fixture repo."""
        with tempfile.TemporaryDirectory(prefix="agentpack-loop-smoke-") as raw:
            root = Path(raw)
            _seed_loop_smoke_repo(root)
            resolved_runner = runner or _resolve_runner_adapter(runner_adapter, root) or _deterministic_smoke_runner(root)
            state = initialize_loop(
                root,
                "make the smoke test pass by changing app.py value to 2",
                load_config(root).loop,
                runner_override=resolved_runner,
                verification_overrides=["python -m pytest -q"],
                acceptance_overrides=["smoke test passes"],
                max_iterations_override=2,
            )
            summary = run_loop(root, state, refresh=lambda: LoopCommandResult(command="smoke-refresh", returncode=0, output_excerpt="ok"))
            payload = {
                "passed": summary.status == "ready_to_finish",
                "summary": summary.model_dump(mode="json"),
                "runner": resolved_runner,
            }
            latest = load_loop_state(root)
            if latest is not None and latest.last_runner is not None:
                payload["runner_output_excerpt"] = latest.last_runner.output_excerpt
            if latest is not None:
                payload["failure_class"] = latest.failure_class
            if json_output:
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
            else:
                marker = "[green]✓[/]" if payload["passed"] else "[red]✗[/]"
                console.print(f"{marker} loop smoke {summary.status}")
                console.print(f"runner: [bold]{resolved_runner}[/]")
            if summary.status != "ready_to_finish":
                raise typer.Exit(1)

    @app.command("loop-rollback")
    def loop_rollback(
        iteration: int = typer.Option(0, "--iteration", help="Rollback patch iteration (0 = latest recorded)."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    ) -> None:
        """Restore the worktree to the latest recorded guarded-loop rollback patch."""
        root = _root()
        state = load_loop_state(root)
        patch = _loop_rollback_patch(root, state, iteration)
        payload = {"patch": str(patch.relative_to(root)) if patch else "", "applied": False}
        current = subprocess.run(["git", "diff", "--binary"], cwd=root, capture_output=True, text=True)
        if current.stdout.strip():
            reverse = subprocess.run(["git", "apply", "-R", "-"], input=current.stdout, cwd=root, capture_output=True, text=True)
            if reverse.returncode != 0:
                payload["reason"] = reverse.stderr.strip() or "failed to reverse current diff"
                if json_output:
                    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                    return
                console.print(f"[red]Rollback failed:[/] {payload['reason']}")
                raise typer.Exit(1)
            payload["applied"] = True
        elif not patch:
            payload["reason"] = "no rollback patch found and worktree has no tracked diff"
            if json_output:
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                return
            console.print("[yellow]No rollback patch found and worktree has no tracked diff.[/]")
            return
        if not patch:
            if json_output:
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                return
            console.print("[green]✓[/] Reversed current tracked diff")
            return
        apply_result = subprocess.run(["git", "apply", str(patch)], cwd=root, capture_output=True, text=True)
        payload["applied"] = apply_result.returncode == 0
        if apply_result.returncode != 0:
            payload["reason"] = apply_result.stderr.strip() or "failed to apply rollback patch"
            if json_output:
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                return
            console.print(f"[red]Rollback failed:[/] {payload['reason']}")
            raise typer.Exit(1)
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
            return
        console.print(f"[green]✓[/] Applied rollback patch {payload['patch']}")

    @app.command("loop-metrics")
    def loop_metrics(json_output: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
        """Summarize guarded-loop outcomes over time."""
        root = _root()
        rows = _read_loop_metrics(root)
        summary = _summarize_loop_metrics(rows)
        if json_output:
            typer.echo(json.dumps(summary, indent=2, sort_keys=True))
            return
        console.print(f"Runs: [bold]{summary['runs']}[/]")
        console.print(f"Ready: [green]{summary['ready_to_finish']}[/]  Blocked: [yellow]{summary['blocked']}[/]  Done: [green]{summary['done']}[/]")


def _run(name: str, command: list[str], root: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    detail = ((result.stderr or result.stdout).strip().splitlines() or [""])[-1]
    return {
        "name": name,
        "command": " ".join(command),
        "returncode": result.returncode,
        "detail": detail,
    }


def _record_task_memory_safe(
    root: Path,
    *,
    task: str,
    stage: str,
    status: str,
    thread: str = "",
    summary: str = "",
    loop_summary: dict[str, Any] | None = None,
) -> None:
    try:
        record_task_memory(
            root,
            task=task,
            stage=stage,
            status=status,
            thread=thread,
            summary=summary,
            loop_summary=loop_summary,
        )
    except Exception:
        # Task memory is opportunistic and must never slow or fail the agent path.
        return


def _finish(
    stages: list[dict[str, Any]],
    json_output: bool,
    *,
    loop_plan: dict[str, Any] | None = None,
    loop_summary: dict[str, Any] | None = None,
) -> None:
    passed = all(stage["returncode"] == 0 for stage in stages)
    if json_output:
        payload: dict[str, Any] = {"passed": passed, "stages": stages}
        if loop_plan is not None:
            payload["loop_plan"] = loop_plan
        if loop_summary is not None:
            payload["loop_summary"] = loop_summary
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for stage in stages:
            marker = "[green]✓[/]" if stage["returncode"] == 0 else "[red]✗[/]"
            console.print(f"{marker} {stage['name']}")
            if stage["returncode"] != 0:
                console.print(f"  rerun: [bold]{stage['command']}[/]")
                if stage.get("detail"):
                    console.print(f"  [dim]{stage['detail']}[/]")
        if loop_plan is not None:
            console.print(f"[green]✓[/] Ralph Loop dry run: {loop_plan['next_action']}")
        if loop_summary is not None:
            marker = "[green]✓[/]" if loop_summary.get("status") == "ready_to_finish" else "[yellow]![/]"
            console.print(f"{marker} Ralph Loop {loop_summary.get('status')}: {loop_summary.get('reason') or loop_summary.get('next_command')}")
    if not passed:
        raise typer.Exit(1)


def _loop_finish_blockers(
    root: Path,
    loop_cfg,
    loop_state,
    thread: str,
    *,
    allow_empty_diff: bool = False,
    allow_high_risk: bool = False,
) -> list[dict[str, Any]]:
    blockers = [
        blocker.model_dump(mode="json")
        for blocker in finish_blockers(root, loop_cfg, loop_state, allow_empty_diff=allow_empty_diff, allow_high_risk=allow_high_risk)
    ]
    fresh, reason = _context_is_fresh(root, thread_id=resolve_session_thread_option(thread))
    if not fresh:
        blockers.append(
            {
                "kind": "stale_context",
                "message": f"Context is stale: {reason}",
                "command": refresh_commands("auto").repair,
            }
        )
    return blockers


def _resolve_runner_adapter(adapter: str, root: Path) -> str:
    if not adapter:
        return ""
    command = resolve_runner_adapter(adapter, root)
    if not command:
        console.print(f"[red]Runner adapter unavailable:[/] {adapter}")
        raise typer.Exit(1)
    return command


def _seed_loop_smoke_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    (root / ".agentpack").mkdir()
    (root / ".agentpack" / "config.toml").write_text("[context]\n[loop]\nrequire_clean_tree = false\n", encoding="utf-8")
    (root / ".agentpack" / "task.md").write_text("make smoke test pass\n", encoding="utf-8")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "test_app.py").write_text("from app import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py", "test_app.py"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test User", "commit", "-m", "init"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _deterministic_smoke_runner(root: Path) -> str:
    script = root / "smoke_runner.py"
    script.write_text(
        "from pathlib import Path\n"
        "Path('app.py').write_text('VALUE = 2\\n', encoding='utf-8')\n"
        "print('{\"status\":\"changed\",\"summary\":\"updated app.py\",\"files_changed\":[\"app.py\"],\"acceptance\":{\"smoke test passes\":\"pass\"}}')\n",
        encoding="utf-8",
    )
    return "python smoke_runner.py"


def _loop_rollback_patch(root: Path, state, iteration: int) -> Path | None:
    if iteration > 0:
        candidate = root / ".agentpack" / "loop_rollback" / f"iteration-{iteration}-before.patch"
        return candidate if candidate.exists() else None
    if state is not None and state.rollback_patch:
        candidate = root / state.rollback_patch
        if candidate.exists():
            return candidate
    patches = sorted((root / ".agentpack" / "loop_rollback").glob("iteration-*-before.patch"))
    return patches[-1] if patches else None


def _read_loop_metrics(root: Path) -> list[dict[str, Any]]:
    path = root / ".agentpack" / "loop_metrics.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows[-500:]


def _summarize_loop_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes: dict[str, int] = {}
    failure_classes: dict[str, int] = {}
    total_iterations = 0
    for row in rows:
        outcome = str(row.get("outcome") or "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        failure_class = str(row.get("failure_class") or "")
        if failure_class:
            failure_classes[failure_class] = failure_classes.get(failure_class, 0) + 1
        total_iterations += int(row.get("iterations") or 0)
    runs = len(rows)
    return {
        "runs": runs,
        "ready_to_finish": outcomes.get("ready_to_finish", 0),
        "blocked": outcomes.get("blocked", 0),
        "done": outcomes.get("done", 0),
        "outcomes": outcomes,
        "failure_classes": failure_classes,
        "avg_iterations": round(total_iterations / runs, 2) if runs else 0,
    }


def _finish_blocked(blockers: list[dict[str, Any]], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps({"passed": False, "stages": [], "loop_blockers": blockers}, indent=2, sort_keys=True))
        return
    console.print("[red]Ralph Loop completion blockers:[/]")
    for blocker in blockers:
        console.print(f"  [yellow]![/] {blocker['message']}")
        console.print(f"    Run: [bold]{blocker['command']}[/]")


def _read_task(root: Path, thread: str) -> str:
    thread_id = resolve_session_thread_option(thread)
    if thread_id:
        path = root / ".agentpack" / "threads" / thread_id / "task.md"
    else:
        path = root / ".agentpack" / "task.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()


def _refresh_loop_context(root: Path, agent: str, mode: str, budget: int, thread_id: str | None) -> LoopCommandResult:
    stats = run_refresh(root, agent, mode, budget, thread_id=thread_id)
    command = refresh_commands(agent).primary
    if stats is None:
        return LoopCommandResult(command=command, returncode=1, output_excerpt="context refresh failed")
    return LoopCommandResult(command=command, returncode=0, output_excerpt=json.dumps(stats, sort_keys=True))
