from __future__ import annotations

from agentpack.core.command_surface import refresh_command, refresh_commands

from agentpack.control_plane.models import ControlPlaneSnapshot, Recommendation


def plan_next_actions(snapshot: ControlPlaneSnapshot) -> list[Recommendation]:
    items: list[Recommendation] = []
    if not snapshot.setup.initialized:
        return [_recommendation("init", "agentpack init --yes", "repo is not initialized")]

    thread_suffix = f" --thread {snapshot.task.thread_id}" if snapshot.task.thread_id else ""
    if not snapshot.task.has_task:
        items.append(
            _recommendation(
                "missing_task",
                f'agentpack start "describe the task"{thread_suffix}',
                _missing_task_reason(snapshot.task.thread_id),
            )
        )
    if snapshot.task.done:
        items.append(
            _recommendation(
                "done_task",
                f'agentpack start "describe the next task"{thread_suffix}',
                _done_task_reason(snapshot.task.thread_id),
            )
        )

    if snapshot.context.status != "fresh":
        command = refresh_command("auto", snapshot.task.thread_id or "global")
        items.append(_recommendation("stale_context", command, snapshot.context.reason))

    if snapshot.threads.conflict_count:
        items.append(_recommendation("thread_conflict", "agentpack threads --conflicts", "active threads overlap on this branch/worktree"))

    if _pack_looks_noisy(snapshot):
        items.append(_recommendation("selection_noise", "agentpack diagnose-selection", "latest pack has broad/noisy selection signals"))

    if snapshot.skill_index_error:
        items.append(
            _recommendation(
                "skills_index_failed",
                "agentpack skills index",
                f"automatic skills index refresh failed: {snapshot.skill_index_error}",
            )
        )

    if snapshot.loop.enabled and snapshot.loop.status:
        if not snapshot.loop.runner:
            items.append(_recommendation("loop_runner_missing", 'agentpack work "..." --run --runner "..."', "Ralph Loop state exists but no runner is configured"))
        elif snapshot.loop.status == "ready_to_finish":
            items.append(_recommendation("loop_ready_to_finish", "agentpack finish --since main", "Ralph Loop verification passed"))
        elif snapshot.loop.status == "blocked":
            reason = snapshot.loop.blocked_reason or "inspect loop failures"
            items.append(_recommendation("loop_blocked", "agentpack dashboard", f"Ralph Loop blocked: {reason}"))
        else:
            items.append(_recommendation("loop_continue", f'agentpack work "{snapshot.loop.task}" --run', f"Ralph Loop is {snapshot.loop.status}"))
    return items


def recommendation_dicts(recommendations: list[Recommendation]) -> list[dict[str, str]]:
    return [item.model_dump(mode="json") for item in recommendations]


def _pack_looks_noisy(snapshot: ControlPlaneSnapshot) -> bool:
    selected = snapshot.tokens.selected_count
    if selected <= 0:
        return False
    summary_count = snapshot.tokens.mode_counts.get("summary", 0)
    return summary_count / selected >= 0.7


def _recommendation(kind: str, command: str, reason: str) -> Recommendation:
    why = {
        "init": "AgentPack cannot create reliable task/context state until repo files exist.",
        "missing_task": "Generic or placeholder tasks produce noisy file selection.",
        "stale_context": "Packed selected files may describe old code, old task text, or a different snapshot.",
        "thread_conflict": "Multiple active threads may edit overlapping files without coordination.",
        "done_task": "Completed task context must not be reused for a new agent session.",
        "selection_noise": "Treat current pack as orientation only until noisy selection is diagnosed.",
        "skills_index_failed": "Skill/rule routing may miss local guidance until the index refreshes.",
        "loop_runner_missing": "Automated loop cannot run without an explicit runner command.",
        "loop_ready_to_finish": "Verification passed; finish captures evidence and closes the loop state.",
        "loop_blocked": "Loop state is blocked; inspect evidence before another run.",
        "loop_continue": "Loop state has pending work; continuing keeps state and rollback evidence aligned.",
        "fixed": "Safe refresh completed; rerun next to confirm remaining state.",
    }.get(kind, "Run the command before trusting AgentPack output.")
    safe = {
        "init": "no; initialize first",
        "missing_task": "no; set a concrete task first",
        "stale_context": "no; refresh or use direct rg/git evidence",
        "thread_conflict": "coordinate first",
        "done_task": "no; start a new task/session first",
        "selection_noise": "yes, but use direct rg/git evidence as truth",
        "skills_index_failed": "yes, but skill routing may be incomplete",
        "loop_runner_missing": "no for loop automation",
        "loop_ready_to_finish": "yes",
        "loop_blocked": "no; inspect dashboard first",
        "loop_continue": "yes for the loop runner",
        "fixed": "yes",
    }.get(kind, "unknown")
    return Recommendation(kind=kind, command=command, reason=reason, why_it_matters=why, safe_to_continue=safe)


def fixed_recommendation(reason: str = "refreshed stale context") -> Recommendation:
    return _recommendation("fixed", "agentpack next", reason)


def _missing_task_reason(thread_id: str | None) -> str:
    return f"no concrete task is set for AgentPack session {thread_id}" if thread_id else "no concrete task is set"


def _done_task_reason(thread_id: str | None) -> str:
    return f"AgentPack session {thread_id} is marked done" if thread_id else "current AgentPack task is marked done"
