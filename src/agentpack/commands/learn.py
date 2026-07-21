from __future__ import annotations

import json
import shlex
from datetime import datetime
from pathlib import Path

import typer

from agentpack.commands._shared import _atomic_write, _root, console
from agentpack.commands.dashboard import _open_file
from agentpack.core.config import load_config
from agentpack.core.project_index import register_project
from agentpack.learning.collector import collect_learning_inputs
from agentpack.learning.extractor import apply_learning_request, build_learning_report
from agentpack.learning.feedback import (
    apply_feedback_to_report,
    load_feedback_summary,
    record_direct_learning_feedback,
    record_learning_feedback,
    record_ranking_feedback,
)
from agentpack.learning.lesson_ranker import rank_agent_lessons
from agentpack.learning.provider import (
    LearningProviderError,
    run_concept_provider_command,
    run_provider_command,
)
from agentpack.learning.quality import score_learning_report
from agentpack.learning.recommender import (
    find_recommended_topic,
    learning_project_roots,
    recommend_learning_topics,
    record_recommendation_impressions,
)
from agentpack.learning.renderers import (
    learning_report_to_dict,
    render_dashboard_html,
    render_agent_lessons_markdown,
    render_drills_markdown,
    render_llm_prompt_markdown,
    render_learning_markdown,
    render_pr_comment_markdown,
    render_provider_preview_markdown,
    render_quality_markdown,
    render_recommendations_markdown,
    render_team_lessons_markdown,
)
from agentpack.learning.sessions import (
    append_learning_session,
    complete_learning_session,
    find_learning_session,
    record_learning_sessions,
    session_from_recommendation,
)
from agentpack.learning.skill_map import (
    apply_skill_feedback,
    recommend_practice_drills,
    render_skill_summary,
    update_skill_map,
)
from agentpack.learning.task_memory import (
    latest_task_memory,
    learning_inputs_from_memory,
)
from agentpack.observer.brief import write_observer_brief
from agentpack.observer.events import (
    record_learning_feedback_observation,
    record_learning_observation,
)
from agentpack.session.events import record_event


def register(app: typer.Typer) -> None:
    @app.command()
    def learn(
        request: list[str] = typer.Argument(None, help="Optional learning request, or: feedback helpful|not-helpful."),
        task: str = typer.Option("auto", "--task", help="Task source. Only 'auto' is supported."),
        global_scope: bool = typer.Option(
            False,
            "--global",
            help="Recommend topics across registered AgentPack projects.",
        ),
        topic_id: str = typer.Option("", "--topic", help="Start one recommended topic by stable topic ID."),
        project_id: str = typer.Option("", "--project", help="Owning project ID for a global recommendation."),
        coach_mode: str = typer.Option("", "--mode", help="Override the recommended coaching mode."),
        complete_session: str = typer.Option("", "--complete", help="Complete a learning session by session ID."),
        score: int | None = typer.Option(None, "--score", help="Coach score from 0 to 100 for --complete."),
        self_assessment: str = typer.Option(
            "",
            "--self-assessment",
            help="Developer confirmation for --complete: mastered|needs-practice.",
        ),
        since: str | None = typer.Option(None, "--since", help="Git ref to compare against, e.g. HEAD~1 or main."),
        today: bool = typer.Option(False, "--today", help="Use today's work scope label for the report."),
        output: str = typer.Option("", "--output", "-o", help="Markdown output path."),
        json_output: bool = typer.Option(False, "--json", help="Print JSON to stdout instead of writing Markdown."),
        llm_prompt: bool = typer.Option(False, "--llm-prompt", help="Write an LLM-ready learning prompt artifact."),
        pr_comment: bool = typer.Option(
            False,
            "--pr-comment",
            help="Write a PR-comment-ready learning summary artifact.",
        ),
        provider_preview: bool = typer.Option(
            False,
            "--provider-preview",
            help="Print the bounded provider payload without making a network call.",
        ),
        provider_command: str = typer.Option(
            "",
            "--provider-command",
            help="Run a local JSON-in/JSON-out provider command to enrich the report.",
        ),
        concept_provider_command: str = typer.Option(
            "",
            "--concept-provider-command",
            help="Run a local JSON-in/JSON-out provider command to enrich detected learning concepts.",
        ),
        no_concept_provider: bool = typer.Option(
            False,
            "--no-concept-provider",
            help="Disable configured concept provider enrichment for this run.",
        ),
        dashboard: bool = typer.Option(
            False,
            "--dashboard",
            help="Write a static HTML learning dashboard artifact.",
        ),
        open_dashboard: bool = typer.Option(False, "--open", help="Open the generated learning dashboard in a browser."),
        team_export: bool = typer.Option(
            False,
            "--team-export",
            help="Write an opt-in team lesson export without personal skill history.",
        ),
        ci: bool = typer.Option(
            False,
            "--ci",
            help="Fail when learning quality is below the configured threshold.",
        ),
        skills: bool = typer.Option(False, "--skills", help="Print the local skill memory summary and exit."),
        drills: bool = typer.Option(
            False,
            "--drills",
            help="Print recommended practice drills from local skill memory and exit.",
        ),
        feedback: str = typer.Option(
            "",
            "--feedback",
            help="Record feedback for this learning output (helpful|not-helpful).",
        ),
        feedback_note: str = typer.Option(
            "",
            "--feedback-note",
            "--note",
            help="Optional note stored with --feedback.",
        ),
        feedback_target: str = typer.Option(
            "",
            "--feedback-target",
            "--target",
            help="Optional target such as skill:CLI design, lesson:retry, rename:old=>new, or merge:old=>new.",
        ),
        suppress_skill: str = typer.Option(
            "",
            "--suppress-skill",
            help="Suppress a noisy skill in future skill views and generation.",
        ),
        rename_skill: str = typer.Option("", "--rename-skill", help="Rename a skill using old=>new."),
        merge_skill: str = typer.Option("", "--merge-skill", help="Merge a skill using old=>new."),
    ) -> None:
        """Generate local learning artifacts from current task and git changes."""
        if task != "auto":
            console.print('[red]`agentpack learn --task "..."` is not supported. Write .agentpack/task.md and use --task auto.[/]')
            raise typer.Exit(2)

        root = _root()
        _register_project_safe(root)
        cfg = load_config(root)
        request_parts = list(request or [])
        if topic_id and complete_session:
            _usage_error("--topic and --complete cannot be used together")
        if complete_session:
            if request_parts or project_id or coach_mode or global_scope:
                _usage_error("--complete cannot be combined with a learning request, --project, --mode, or --global")
            if score is None or not self_assessment:
                _usage_error("--complete requires --score and --self-assessment")
            _complete_recommended_session(
                root,
                complete_session,
                score=score,
                self_assessment=self_assessment,
                note=feedback_note,
                json_output=json_output,
            )
            return
        if topic_id:
            if request_parts or score is not None or self_assessment:
                _usage_error("--topic cannot be combined with a learning request, --score, or --self-assessment")
            _start_recommended_session(
                root,
                topic_id,
                project_id=project_id,
                coach_mode=coach_mode,
                json_output=json_output,
            )
            return
        if project_id or coach_mode or score is not None or self_assessment:
            _usage_error("--project and --mode require --topic; --score and --self-assessment require --complete")
        learning_request = ""
        if request_parts and request_parts[0] == "feedback":
            if len(request_parts) < 2:
                console.print("[red]Feedback value required: helpful|not-helpful.[/]")
                raise typer.Exit(2)
            value = request_parts[1]
            try:
                payload = record_direct_learning_feedback(
                    root / cfg.learning.feedback_output,
                    value,
                    task=_task_text(root),
                    note=feedback_note,
                    target=feedback_target,
                )
            except ValueError as exc:
                console.print(f"[red]{exc}[/]")
                raise typer.Exit(2) from exc
            record_event(
                root,
                "learn_feedback",
                {"feedback": payload["feedback"], "target": payload["target"]},
                output_path=cfg.runtime.session_events_output,
            )
            try:
                task_text = _task_text(root)
                record_learning_feedback_observation(
                    root,
                    task=task_text,
                    feedback=str(payload["feedback"]),
                    target=str(payload["target"]),
                )
                write_observer_brief(root, task=task_text)
            except Exception:
                pass
            console.print(f"[green]✓[/] Recorded learning feedback in {cfg.learning.feedback_output}")
            return
        if request_parts:
            learning_request = " ".join(request_parts).strip()
        skill_map_path = root / cfg.learning.skill_map_output
        if skills:
            typer.echo(render_skill_summary(skill_map_path), nl=False)
            return
        if drills:
            typer.echo(
                render_drills_markdown(recommend_practice_drills(skill_map_path)),
                nl=False,
            )
            return
        if suppress_skill:
            apply_skill_feedback(
                skill_map_path,
                target=suppress_skill,
                action="suppress",
                note=feedback_note,
            )
            console.print(f"[green]✓[/] Suppressed skill {suppress_skill}")
            return
        if rename_skill:
            old, new = _split_mapping(rename_skill, "--rename-skill")
            apply_skill_feedback(
                skill_map_path,
                target=old,
                action="rename",
                replacement=new,
                note=feedback_note,
            )
            console.print(f"[green]✓[/] Renamed skill {old} -> {new}")
            return
        if merge_skill:
            old, new = _split_mapping(merge_skill, "--merge-skill")
            apply_skill_feedback(
                skill_map_path,
                target=old,
                action="merge",
                replacement=new,
                note=feedback_note,
            )
            console.print(f"[green]✓[/] Merged skill {old} -> {new}")
            return

        since_date = _today_start_iso() if today and not since else None
        memory = latest_task_memory(root) if learning_request else None
        use_memory = memory is not None and _request_prefers_memory(learning_request)
        if use_memory and memory is not None:
            inputs = learning_inputs_from_memory(memory)
        else:
            inputs = collect_learning_inputs(
                root,
                since=since,
                since_date=since_date,
                max_changed_files=cfg.learning.max_changed_files,
                max_diff_chars_per_file=cfg.learning.max_diff_chars_per_file,
            )
            if learning_request and not inputs.changed_files and memory is not None:
                inputs = learning_inputs_from_memory(memory)
        report = build_learning_report(
            inputs,
            max_cards=cfg.learning.max_cards,
            max_quiz_questions=cfg.learning.max_quiz_questions,
        )
        if use_memory:
            report = report.model_copy(update={"scope": "task-memory"})
        report = apply_learning_request(report, learning_request)
        feedback_summary = load_feedback_summary(root / cfg.learning.feedback_output)
        report = apply_feedback_to_report(report, feedback_summary)
        report.agent_lessons = rank_agent_lessons(report, feedback_summary, limit=cfg.learning.max_cards)
        ranking_feedback_count = record_ranking_feedback(
            root,
            report,
            output_path=cfg.learning.ranking_feedback_output,
        )
        if today:
            report.scope = "today"
            if since_date:
                report.since = f"today ({since_date})"

        if provider_preview:
            typer.echo(render_provider_preview_markdown(report), nl=False)
            return

        concept_command = concept_provider_command or ("" if no_concept_provider else cfg.learning.concept_provider_command)
        if concept_command:
            try:
                report = run_concept_provider_command(
                    concept_command,
                    inputs,
                    report,
                    timeout_seconds=cfg.learning.concept_provider_timeout_seconds,
                )
            except LearningProviderError as exc:
                if concept_provider_command or cfg.learning.concept_provider_required:
                    console.print(f"[red]Concept provider command failed:[/] {exc}")
                    raise typer.Exit(1) from exc
                console.print(f"[yellow]Concept provider skipped:[/] {exc}")

        command = provider_command or cfg.learning.provider_command
        if command:
            try:
                report = run_provider_command(
                    command,
                    report,
                    timeout_seconds=cfg.learning.provider_timeout_seconds,
                )
            except LearningProviderError as exc:
                console.print(f"[red]Provider command failed:[/] {exc}")
                raise typer.Exit(1) from exc

        recommendations = recommend_learning_topics(
            root,
            report=report,
            request=learning_request,
            global_scope=global_scope,
        )
        report = report.model_copy(update={"recommendations": recommendations})

        update_skill_map(skill_map_path, report.skill_evidence)
        agent_lessons_path = root / cfg.learning.agent_lessons_output
        agent_lessons_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(agent_lessons_path, render_agent_lessons_markdown(report))

        quality = score_learning_report(report, root=root)
        if quality.score < cfg.learning.min_groundedness_score:
            console.print(f"[yellow]Learning quality warning:[/] score {quality.score}; " + "; ".join(quality.issues))
        learning_session_count = record_learning_sessions(root, report)

        if llm_prompt:
            prompt_path = root / cfg.learning.llm_prompt_output
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(prompt_path, render_llm_prompt_markdown(report))
        if pr_comment:
            pr_path = root / cfg.learning.pr_comment_output
            pr_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(pr_path, render_pr_comment_markdown(report))
        if open_dashboard:
            dashboard = True
        if dashboard:
            dashboard_path = root / cfg.learning.dashboard_output
            dashboard_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(dashboard_path, render_dashboard_html(report))
            console.print(f"[green]✓[/] Wrote {dashboard_path.relative_to(root)}")
            if open_dashboard:
                _open_file(dashboard_path)
        if team_export:
            team_path = root / cfg.learning.team_lessons_output
            team_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(team_path, render_team_lessons_markdown(report))
        if feedback:
            if feedback not in {"helpful", "not-helpful"}:
                console.print("[red]--feedback must be helpful or not-helpful.[/]")
                raise typer.Exit(2)
            record_learning_feedback(
                root / cfg.learning.feedback_output,
                report,
                feedback,
                feedback_note,
                feedback_target,
            )
            record_event(
                root,
                "learn_feedback",
                {"feedback": feedback, "target": feedback_target},
                output_path=cfg.runtime.session_events_output,
            )
            try:
                record_learning_feedback_observation(
                    root,
                    task=report.task,
                    feedback=feedback,
                    target=feedback_target,
                )
            except Exception:
                pass
        if ci:
            typer.echo(render_quality_markdown(report, quality.score, quality.issues), nl=False)
            if quality.score < cfg.learning.min_groundedness_score:
                raise typer.Exit(1)

        if report.recommendations is not None and not ci:
            report = report.model_copy(update={"recommendations": record_recommendation_impressions(report.recommendations)})

        if json_output:
            typer.echo(json.dumps(learning_report_to_dict(report), indent=2, sort_keys=True))
            return

        default_output = cfg.learning.daily_output if today else cfg.learning.markdown_output
        out_path = root / (output or default_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(out_path, render_learning_markdown(report))
        record_event(
            root,
            "learn",
            {
                "task": report.task,
                "issue_references": report.issue_references,
                "issue_reference_details": report.issue_reference_details,
                "changed_files": len(report.source_files),
                "concepts": report.concepts,
                "selected_hits": len(report.selected_hits),
                "selected_misses": len(report.selected_misses),
                "ranking_feedback_paths": ranking_feedback_count,
                "learning_request": report.learning_request,
                "coach_mode": report.coach_mode,
                "learning_sessions": learning_session_count,
            },
            output_path=cfg.runtime.session_events_output,
        )
        try:
            record_learning_observation(
                root,
                task=report.task,
                concepts=list(report.concepts),
                selected_hits=len(report.selected_hits),
                selected_misses=len(report.selected_misses),
                learning_request=report.learning_request,
                learning_sessions=learning_session_count,
            )
            write_observer_brief(root, task=report.task)
        except Exception:
            pass
        if report.recommendations is not None:
            typer.echo(render_recommendations_markdown(report.recommendations), nl=False)
        console.print(f"[green]✓[/] Wrote {out_path.relative_to(root)}")


def _today_start_iso() -> str:
    now = datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _task_text(root) -> str:
    task_path = root / ".agentpack" / "task.md"
    if task_path.exists():
        lines = [line.strip() for line in task_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
        if lines:
            return lines[0]
    return "Current work"


def _request_prefers_memory(request: str) -> bool:
    lowered = request.lower()
    return any(
        term in lowered
        for term in (
            "last task",
            "previous task",
            "recent task",
            "what agent changed",
            "agent changed",
        )
    )


def _split_mapping(value: str, flag: str) -> tuple[str, str]:
    if "=>" not in value:
        console.print(f"[red]{flag} expects old=>new.[/]")
        raise typer.Exit(2)
    old, new = [part.strip() for part in value.split("=>", 1)]
    if not old or not new:
        console.print(f"[red]{flag} expects non-empty old=>new values.[/]")
        raise typer.Exit(2)
    return old, new


def _register_project_safe(root: Path) -> None:
    try:
        register_project(root)
    except OSError:
        return


def _start_recommended_session(
    root: Path,
    topic_id: str,
    *,
    project_id: str,
    coach_mode: str,
    json_output: bool,
) -> None:
    valid_modes = {"study", "quiz", "interview", "failure", "review", "system-design"}
    if coach_mode and coach_mode not in valid_modes:
        _usage_error("--mode must be study, quiz, interview, failure, review, or system-design")
    try:
        target, topic, recommendation_id = find_recommended_topic(
            root,
            topic_id,
            project_id_value=project_id,
        )
    except ValueError as exc:
        _usage_error(str(exc))
        return
    session = session_from_recommendation(topic, recommendation_id=recommendation_id, mode=coach_mode)
    if not append_learning_session(target, session):
        console.print(f"[red]Could not write learning session in {target}[/]")
        raise typer.Exit(1)
    _record_learning_event_safe(
        target,
        "learning_session_started",
        {
            "session_id": session.session_id,
            "topic_id": session.topic_id,
            "recommendation_id": recommendation_id,
            "project_id": topic.project.project_id,
            "mode": session.mode,
        },
    )
    if json_output:
        typer.echo(json.dumps(session.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    console.print(f"[green]✓[/] Started [bold]{session.topic}[/] in {topic.project.name}")
    console.print(f"Session: [bold]{session.session_id}[/]")
    console.print(session.question or topic.exercise)
    complete = [
        "agentpack",
        "learn",
        "--complete",
        session.session_id,
        "--score",
        "<0-100>",
        "--self-assessment",
        "<mastered|needs-practice>",
    ]
    console.print(f"Complete: [bold]{shlex.join(complete)}[/]")


def _complete_recommended_session(
    root: Path,
    session_id: str,
    *,
    score: int,
    self_assessment: str,
    note: str,
    json_output: bool,
) -> None:
    owner = next(
        (candidate for candidate in learning_project_roots(root) if find_learning_session(candidate, session_id)),
        None,
    )
    if owner is None:
        _usage_error(f"Learning session not found: {session_id}")
        return
    try:
        session = complete_learning_session(
            owner,
            session_id,
            score=score,
            self_assessment=self_assessment,
            note=note,
        )
    except ValueError as exc:
        _usage_error(str(exc))
        return
    _record_learning_event_safe(
        owner,
        "learning_session_completed",
        {
            "session_id": session.session_id,
            "topic_id": session.topic_id,
            "project_id": session.project_id,
            "score": session.score,
            "self_assessment": session.self_assessment,
            "mastery_status": session.mastery_status,
        },
    )
    if json_output:
        typer.echo(json.dumps(session.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    console.print(f"[green]✓[/] Recorded {session.topic}: score {session.score}, mastery [bold]{session.mastery_status}[/]")


def _record_learning_event_safe(root: Path, event_type: str, payload: dict) -> None:
    try:
        record_event(root, event_type, payload, source="learn")
    except OSError:
        return


def _usage_error(message: str) -> None:
    console.print(f"[red]{message}[/]")
    raise typer.Exit(2)
