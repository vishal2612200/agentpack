from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]
import tomli_w

from agentpack.core import git
from agentpack.core.config import Config, config_path, load_config
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.redactor import redact_secrets
from agentpack.core.task_freshness import task_freshness
from agentpack.dashboard.action_history import read_action_history
from agentpack.dashboard.models import (
    ProjectDecision,
    ProjectEvidence,
    ProjectHealthDimension,
    ProjectHealthSnapshot,
    ProjectInitiative,
    ProjectInitiativeSuggestion,
    ProjectMetrics,
    ProjectMilestoneState,
    ProjectOutcomeState,
    ProjectOverview,
    ProjectProfile,
    ProjectRisk,
    ProjectStatusBrief,
    ProjectTimelineEvent,
    ProjectWorkspace,
)
from agentpack.dashboard.project_state import load_dashboard_state
from agentpack.learning.sessions import derive_mastery_status, read_learning_sessions_with_errors
from agentpack.learning.task_memory import recent_task_memories
from agentpack.session.events import read_events, record_event
from agentpack.session.identity import project_id, workspace_id


MAX_WORKTREES = 20
MAX_PROJECT_EVENTS = 200
MAX_OUTCOMES = 50
MAX_MILESTONES = 100
MAX_LIST_VALUES = 20
MAX_LINKS = 20
MAX_TASK_RECORDS = 100
MAX_LEARNING_ROWS = 100
MAX_TIMELINE_ROWS = 200
MAX_COMMITS = 50
MAX_EVIDENCE = 8
TASK_WINDOW_DAYS = 30
DISMISSAL_DAYS = 30
PROJECT_EVENT_TYPES = {
    "project_outcome_status",
    "project_milestone_status",
    "project_risk_upsert",
    "project_decision_recorded",
    "project_initiative_confirmed",
    "project_initiative_dismissed",
    "project_profile_updated",
}
PROJECT_STAGES = {"idea", "planning", "active", "maintenance", "paused", "complete"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_LINK_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class _TaskObservation:
    task_id: str
    title: str
    updated_at: str
    workspace_id: str
    files: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)


class ProjectConfigConflict(ValueError):
    pass


class ProjectReadOnlyError(PermissionError):
    pass


class ProjectValidationError(ValueError):
    pass


def deterministic_entity_id(project: str, kind: str, title: str, *, parent_id: str = "") -> str:
    subject = " ".join(title.strip().lower().split())
    digest = hashlib.sha256(f"{project}:{kind}:{parent_id}:{subject}".encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{digest}"


def project_config_revision(root: Path) -> str:
    path = config_path(root.resolve())
    try:
        content = path.read_bytes()
    except OSError:
        content = b""
    return hashlib.sha256(content).hexdigest()


def load_project_profile(root: Path, *, now: datetime | None = None) -> ProjectProfile:
    root = root.resolve()
    cfg = load_config(root).project
    updated_at = _config_updated_at(root)
    return ProjectProfile(
        project_id=project_id(root),
        config_revision=project_config_revision(root),
        display_name=cfg.display_name or root.name or str(root),
        purpose=cfg.purpose,
        audiences=cfg.audiences[:MAX_LIST_VALUES],
        owners=cfg.owners[:MAX_LIST_VALUES],
        stage=cfg.stage,
        links=dict(list(cfg.links.items())[:MAX_LINKS]),
        environments=cfg.environments[:MAX_LIST_VALUES],
        status_stale_days=cfg.status_stale_days,
        source="declared",
        confidence=1.0,
        updated_at=updated_at or (now or datetime.now(timezone.utc)).isoformat(),
        evidence=[
            ProjectEvidence(
                kind="config",
                ref=".agentpack/config.toml",
                path=".agentpack/config.toml",
                summary="Shared project profile and roadmap definitions.",
                workspace_id=workspace_id(root),
            )
        ] if config_path(root).exists() else [],
        workspace_id=workspace_id(root),
    )


def declared_outcomes(root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    project = project_id(root)
    outcomes: list[dict[str, Any]] = []
    for outcome in load_config(root).project.outcomes[:MAX_OUTCOMES]:
        outcome_id = outcome.id or deterministic_entity_id(project, "outcome", outcome.title)
        milestones: list[dict[str, Any]] = []
        for milestone in outcome.milestones[:MAX_MILESTONES]:
            milestone_id = milestone.id or deterministic_entity_id(
                project,
                "milestone",
                milestone.title,
                parent_id=outcome_id,
            )
            milestones.append(
                {
                    "id": milestone_id,
                    "title": milestone.title,
                    "owner": milestone.owner,
                    "due_date": milestone.due_date,
                }
            )
        outcomes.append(
            {
                "id": outcome_id,
                "title": outcome.title,
                "description": outcome.description,
                "owner": outcome.owner,
                "target_date": outcome.target_date,
                "milestones": milestones,
            }
        )
    return outcomes


def update_project_profile(
    root: Path,
    updates: dict[str, Any],
    *,
    expected_revision: str,
) -> ProjectProfile:
    root = root.resolve()
    if not project_storage_writable(root, config_write=True):
        raise ProjectReadOnlyError("project configuration is read-only")
    current_revision = project_config_revision(root)
    if expected_revision != current_revision:
        raise ProjectConfigConflict("project configuration changed; refresh and retry")
    if not isinstance(updates, dict) or not updates:
        raise ProjectValidationError("profile must be a non-empty object")

    raw = _load_raw_config(root)
    project_section = raw.setdefault("project", {})
    if not isinstance(project_section, dict):
        raise ProjectValidationError("project config section must be a table")
    clean = _validate_profile_updates(project_id(root), updates)
    project_section.update(clean)
    try:
        Config.model_validate(raw)
    except ValueError as exc:
        raise ProjectValidationError(str(exc)) from exc
    _write_raw_config(root, raw)
    return load_project_profile(root)


def discover_project_workspaces(
    root: Path,
    *,
    now: datetime | None = None,
) -> tuple[list[ProjectWorkspace], list[str]]:
    root = root.resolve()
    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    warnings: list[str] = []
    entries = _git_worktree_entries(root)
    if not entries:
        entries = [{"path": str(root), "branch": git.current_branch(root) or "", "sha": git.current_sha(root) or ""}]

    workspaces: list[ProjectWorkspace] = []
    seen: set[str] = set()
    expected_project_id = project_id(root)
    ordered = sorted(entries, key=lambda item: str(Path(str(item.get("path") or "")).resolve()) != str(root))
    for entry in ordered[:MAX_WORKTREES]:
        candidate = Path(str(entry.get("path") or "")).expanduser().resolve()
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if not candidate.is_dir():
            warnings.append(f"inaccessible_worktree: {candidate}")
            continue
        try:
            candidate_project_id = project_id(candidate)
        except OSError:
            warnings.append(f"inaccessible_worktree: {candidate}")
            continue
        if candidate_project_id != expected_project_id:
            warnings.append(f"unrelated_worktree_skipped: {candidate}")
            continue
        candidate_workspace_id = workspace_id(candidate, current_project_id=expected_project_id)
        sha = str(entry.get("sha") or git.current_sha(candidate) or "")[:12]
        branch = str(entry.get("branch") or git.current_branch(candidate) or "").removeprefix("refs/heads/")
        workspaces.append(
            ProjectWorkspace(
                workspace_id=candidate_workspace_id,
                path=candidate_key,
                branch=branch,
                git_sha=sha,
                is_current=candidate == root,
                read_only=not project_storage_writable(candidate),
                source="observed",
                confidence=1.0,
                updated_at=_latest_commit_time(candidate) or generated_at,
                evidence=[
                    ProjectEvidence(
                        kind="git_worktree",
                        ref=branch or sha,
                        summary="Repository worktree discovered through the common Git directory.",
                        workspace_id=candidate_workspace_id,
                    )
                ],
            )
        )
    if len(entries) > MAX_WORKTREES:
        warnings.append(f"partial_result: limited worktree discovery to {MAX_WORKTREES}")
    return workspaces, warnings


def select_project_workspaces(
    workspaces: list[ProjectWorkspace],
    selector: str,
) -> list[ProjectWorkspace]:
    value = (selector or "all").strip()
    if value == "all":
        return list(workspaces)
    if value == "current":
        return [workspace for workspace in workspaces if workspace.is_current]
    selected = [workspace for workspace in workspaces if workspace.workspace_id == value]
    if not selected:
        raise ProjectValidationError(f"unknown workspace: {value}")
    return selected


def read_project_events(
    workspaces: list[ProjectWorkspace],
    *,
    limit_per_workspace: int = MAX_PROJECT_EVENTS,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for workspace in workspaces:
        root = Path(workspace.path)
        try:
            events = read_events(root, limit=min(MAX_PROJECT_EVENTS, max(1, limit_per_workspace)))
        except OSError:
            warnings.append(f"malformed_or_inaccessible_events: {workspace.path}")
            continue
        for event in events:
            event_type = str(event.get("event_type") or "")
            if event_type not in PROJECT_EVENT_TYPES:
                continue
            event_id = str(event.get("event_id") or "")
            if event_id and event_id in seen:
                continue
            if event_id:
                seen.add(event_id)
            rows.append(event)
    rows.sort(key=lambda event: (str(event.get("occurred_at") or ""), str(event.get("event_id") or "")))
    return rows, warnings


def fold_project_events(
    events: list[dict[str, Any]],
    event_type: str,
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if str(event.get("event_type") or "") != event_type:
            continue
        entity_id = str(_event_value(event, "entity_id") or "")
        if entity_id:
            latest[entity_id] = event
    return latest


def find_project_mutation(root: Path, mutation_id: str) -> dict[str, Any] | None:
    for event in reversed(read_events(root.resolve(), limit=MAX_PROJECT_EVENTS)):
        if str(_event_value(event, "mutation_id") or "") == mutation_id:
            return event
    return None


def append_project_event(
    root: Path,
    event_type: str,
    *,
    mutation_id: str,
    entity_id: str,
    values: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], bool]:
    root = root.resolve()
    if event_type not in PROJECT_EVENT_TYPES - {"project_profile_updated"}:
        raise ProjectValidationError(f"unsupported project event type: {event_type}")
    _validate_identifier(mutation_id, "mutation_id")
    _validate_identifier(entity_id, "entity_id")
    duplicate = find_project_mutation(root, mutation_id)
    if duplicate is not None:
        return duplicate, True
    if not project_storage_writable(root):
        raise ProjectReadOnlyError("project status storage is read-only")
    safe_values = dict(values or {})
    safe_evidence = _validate_evidence(evidence or [])
    event = record_event(
        root,
        event_type,
        {
            **safe_values,
            "mutation_id": mutation_id,
            "entity_id": entity_id,
            "evidence": safe_evidence,
        },
        source="dashboard",
    )
    return event, False


def project_storage_writable(root: Path, *, config_write: bool = False) -> bool:
    root = root.resolve()
    target = config_path(root) if config_write else root / ".agentpack"
    candidate = target if target.exists() else target.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        mode = stat.S_IMODE(candidate.stat().st_mode)
    except OSError:
        return False
    return bool(mode & 0o222) and os.access(candidate, os.W_OK)


def event_value(event: dict[str, Any], key: str, default: Any = "") -> Any:
    return _event_value(event, key, default)


def build_project_overview(
    root: Path,
    *,
    workspace: str = "all",
    now: datetime | None = None,
) -> ProjectOverview:
    root = root.resolve()
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    profile = load_project_profile(root, now=generated)
    workspaces, warnings = discover_project_workspaces(root, now=generated)
    selected = select_project_workspaces(workspaces, workspace)
    project_events, event_warnings = read_project_events(selected)
    warnings.extend(event_warnings)
    outcomes = _outcome_states(root, project_events)
    risks = _risk_states(project_events)
    decisions = _decision_states(project_events)
    initiatives = _initiative_states(project_events)
    tasks, task_warnings = _task_observations(selected, now=generated)
    warnings.extend(task_warnings)
    suggestions = _initiative_suggestions(
        profile.project_id,
        tasks,
        project_events,
        now=generated,
    )
    timeline = _timeline_for_workspaces(selected, limit=MAX_TIMELINE_ROWS)
    health = _health_snapshot(
        selected,
        outcomes=outcomes,
        now=generated,
    )
    recent_changes = [
        event
        for event in timeline
        if event.kind in {"commit", "project", "task", "check", "review", "learning"}
    ][:20]
    metrics = _project_metrics(
        outcomes=outcomes,
        risks=risks,
        decisions=decisions,
        initiatives=initiatives,
        recent_changes=recent_changes,
        stale_days=profile.status_stale_days,
        now=generated,
    )
    if not outcomes:
        warnings.append("empty_roadmap: no declared project outcomes")
    if not timeline:
        warnings.append("empty_history: no project activity is available")
    warnings = _unique(warnings)
    updated_at = _latest_timestamp(
        [
            profile.updated_at,
            *[item.updated_at for item in outcomes],
            *[item.updated_at for item in risks],
            *[item.updated_at for item in decisions],
            *[item.updated_at for item in initiatives],
            *[item.updated_at for item in timeline],
        ]
    ) or profile.updated_at
    partial = any(
        warning.startswith(("partial_result", "inaccessible_", "malformed_"))
        for warning in warnings
    )
    return ProjectOverview(
        project_id=profile.project_id,
        generated_at=generated.isoformat(),
        selected_workspace=workspace or "all",
        profile=profile,
        workspaces=workspaces,
        metrics=metrics,
        outcomes=outcomes,
        initiatives=initiatives,
        initiative_suggestions=suggestions,
        risks=risks,
        decisions=decisions,
        health=health,
        recent_changes=recent_changes,
        partial=partial,
        read_only=not project_storage_writable(root),
        source="inferred",
        confidence=0.8 if partial else 1.0,
        updated_at=updated_at,
        evidence=[*profile.evidence, *health.evidence][:MAX_EVIDENCE],
        workspace_id=workspace or "all",
        warnings=warnings,
    )


def build_project_timeline(
    root: Path,
    *,
    workspace: str = "all",
    kind: str = "",
    limit: int = 50,
) -> list[ProjectTimelineEvent]:
    workspaces, _warnings = discover_project_workspaces(root.resolve())
    selected = select_project_workspaces(workspaces, workspace)
    rows = _timeline_for_workspaces(selected, limit=min(MAX_TIMELINE_ROWS, max(1, limit)))
    if kind:
        rows = [row for row in rows if row.kind == kind]
    return rows[: min(MAX_TIMELINE_ROWS, max(1, limit))]


def build_project_status_brief(
    root: Path,
    *,
    mode: str = "summary",
    now: datetime | None = None,
) -> ProjectStatusBrief:
    if mode not in {"summary", "engineering"}:
        raise ProjectValidationError("brief mode must be summary or engineering")
    overview = build_project_overview(root, workspace="all", now=now)
    lines = _summary_brief_lines(overview)
    if mode == "engineering":
        lines.extend(_engineering_brief_lines(overview))
    markdown = "\n".join(lines).rstrip() + "\n"
    markdown, redaction_warnings = redact_secrets(markdown, "project-status-brief.md")
    markdown = _truncate_utf8(markdown, 20 * 1024)
    warnings = _unique([*overview.warnings, *redaction_warnings])
    return ProjectStatusBrief(
        mode=mode,
        markdown=markdown,
        project_id=overview.project_id,
        source="inferred",
        confidence=overview.confidence,
        updated_at=overview.updated_at,
        evidence=overview.evidence[:MAX_EVIDENCE],
        workspace_id="all",
        warnings=warnings,
    )


def apply_project_profile_update(
    root: Path,
    *,
    mutation_id: str,
    expected_revision: str,
    updates: dict[str, Any],
) -> tuple[ProjectProfile, bool]:
    root = root.resolve()
    _validate_identifier(mutation_id, "mutation_id")
    duplicate = find_project_mutation(root, mutation_id)
    if duplicate is not None:
        result = _event_value(duplicate, "result")
        if isinstance(result, dict):
            try:
                return ProjectProfile.model_validate(result), True
            except ValueError:
                pass
        return load_project_profile(root), True
    profile = update_project_profile(root, updates, expected_revision=expected_revision)
    record_event(
        root,
        "project_profile_updated",
        {
            "mutation_id": mutation_id,
            "entity_id": profile.project_id,
            "config_revision": profile.config_revision,
            "result": profile.model_dump(mode="json"),
            "evidence": [
                {
                    "kind": "config",
                    "ref": profile.config_revision,
                    "path": ".agentpack/config.toml",
                    "summary": "Shared project definitions updated.",
                }
            ],
        },
        source="dashboard",
    )
    return profile, False


def record_project_status_event(
    root: Path,
    request: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    root = root.resolve()
    event_type = str(request.get("event_type") or "")
    mutation_id = str(request.get("mutation_id") or "")
    entity_id = str(request.get("entity_id") or "")
    duplicate = find_project_mutation(root, mutation_id)
    if duplicate is not None:
        return duplicate, True
    overview = build_project_overview(root)
    values = {
        key: value
        for key, value in request.items()
        if key not in {"event_type", "mutation_id", "entity_id", "evidence"} and value not in {"", None}
    }
    evidence = request.get("evidence") if isinstance(request.get("evidence"), list) else []

    if event_type == "project_outcome_status":
        if entity_id not in {item.outcome_id for item in overview.outcomes}:
            raise ProjectValidationError("outcome does not belong to the selected project")
        if values.get("status") not in {"planned", "on_track", "at_risk", "achieved", "paused"}:
            raise ProjectValidationError("invalid outcome status")
    elif event_type == "project_milestone_status":
        if entity_id not in {milestone.milestone_id for item in overview.outcomes for milestone in item.milestones}:
            raise ProjectValidationError("milestone does not belong to the selected project")
        if values.get("status") not in {"planned", "in_progress", "blocked", "done"}:
            raise ProjectValidationError("invalid milestone status")
    elif event_type == "project_risk_upsert":
        if not values.get("title"):
            raise ProjectValidationError("risk title is required")
        values["severity"] = values.get("severity") or "medium"
        values["status"] = values.get("status") or "open"
        if values["severity"] not in {"low", "medium", "high", "critical"}:
            raise ProjectValidationError("invalid risk severity")
        if values["status"] not in {"open", "mitigating", "accepted", "resolved"}:
            raise ProjectValidationError("invalid risk status")
    elif event_type == "project_decision_recorded":
        if not values.get("title"):
            raise ProjectValidationError("decision title is required")
        values["status"] = values.get("status") or "proposed"
        if values["status"] not in {"proposed", "accepted", "rejected", "superseded"}:
            raise ProjectValidationError("invalid decision status")
    elif event_type in {"project_initiative_confirmed", "project_initiative_dismissed"}:
        suggestion = next(
            (item for item in overview.initiative_suggestions if item.suggestion_id == entity_id),
            None,
        )
        if suggestion is None:
            raise ProjectValidationError("initiative suggestion is not available")
        values.update(
            {
                "suggestion_id": suggestion.suggestion_id,
                "title": values.get("title") or suggestion.title,
                "description": values.get("description") or suggestion.rationale,
                "evidence_task_ids": suggestion.task_ids,
            }
        )
        if not evidence:
            evidence = [item.model_dump(mode="json") for item in suggestion.evidence]
    else:
        raise ProjectValidationError(f"unsupported project event type: {event_type}")

    return append_project_event(
        root,
        event_type,
        mutation_id=mutation_id,
        entity_id=entity_id,
        values=values,
        evidence=evidence,
    )


def _outcome_states(root: Path, events: list[dict[str, Any]]) -> list[ProjectOutcomeState]:
    status_events = fold_project_events(events, "project_outcome_status")
    milestone_events = fold_project_events(events, "project_milestone_status")
    config_evidence = ProjectEvidence(
        kind="config",
        ref=".agentpack/config.toml",
        path=".agentpack/config.toml",
        summary="Declared project roadmap.",
        workspace_id=workspace_id(root),
    )
    config_time = _config_updated_at(root)
    rows: list[ProjectOutcomeState] = []
    for raw in declared_outcomes(root):
        outcome_id = str(raw["id"])
        status_event = status_events.get(outcome_id)
        status = str(_event_value(status_event or {}, "status") or "planned")
        warnings: list[str] = []
        if status not in {"planned", "on_track", "at_risk", "achieved", "paused"}:
            warnings.append(f"invalid outcome status ignored: {status}")
            status = "planned"
        milestones: list[ProjectMilestoneState] = []
        for milestone in raw.get("milestones", []):
            milestone_id = str(milestone["id"])
            milestone_event = milestone_events.get(milestone_id)
            milestone_status = str(_event_value(milestone_event or {}, "status") or "planned")
            milestone_warnings: list[str] = []
            if milestone_status not in {"planned", "in_progress", "blocked", "done"}:
                milestone_warnings.append(f"invalid milestone status ignored: {milestone_status}")
                milestone_status = "planned"
            event_evidence = _event_evidence(milestone_event) if milestone_event else []
            milestones.append(
                ProjectMilestoneState(
                    milestone_id=milestone_id,
                    outcome_id=outcome_id,
                    title=str(milestone.get("title") or ""),
                    owner=str(milestone.get("owner") or ""),
                    due_date=str(milestone.get("due_date") or ""),
                    status=milestone_status,
                    source="observed" if milestone_event else "declared",
                    confidence=1.0,
                    updated_at=_event_updated_at(milestone_event) or config_time,
                    evidence=[config_evidence, *event_evidence][:MAX_EVIDENCE],
                    workspace_id=str((milestone_event or {}).get("workspace_id") or workspace_id(root)),
                    warnings=milestone_warnings,
                )
            )
        done = sum(item.status == "done" for item in milestones)
        progress = round(done * 100 / len(milestones), 1) if milestones else None
        event_evidence = _event_evidence(status_event) if status_event else []
        rows.append(
            ProjectOutcomeState(
                outcome_id=outcome_id,
                title=str(raw.get("title") or ""),
                description=str(raw.get("description") or ""),
                owner=str(raw.get("owner") or ""),
                target_date=str(raw.get("target_date") or ""),
                status=status,
                progress_pct=progress,
                milestones=milestones,
                source="observed" if status_event else "declared",
                confidence=1.0,
                updated_at=_event_updated_at(status_event) or config_time,
                evidence=[config_evidence, *event_evidence][:MAX_EVIDENCE],
                workspace_id=str((status_event or {}).get("workspace_id") or workspace_id(root)),
                warnings=warnings,
            )
        )
    return rows


def _risk_states(events: list[dict[str, Any]]) -> list[ProjectRisk]:
    rows: list[ProjectRisk] = []
    for risk_id, event in fold_project_events(events, "project_risk_upsert").items():
        severity = str(_event_value(event, "severity") or "medium")
        status = str(_event_value(event, "status") or "open")
        warnings: list[str] = []
        if severity not in {"low", "medium", "high", "critical"}:
            warnings.append(f"invalid risk severity ignored: {severity}")
            severity = "medium"
        if status not in {"open", "mitigating", "accepted", "resolved"}:
            warnings.append(f"invalid risk status ignored: {status}")
            status = "open"
        rows.append(
            ProjectRisk(
                risk_id=risk_id,
                title=str(_event_value(event, "title") or risk_id),
                description=str(_event_value(event, "description") or "")[:2000],
                owner=str(_event_value(event, "owner") or "")[:120],
                severity=severity,
                status=status,
                mitigation=str(_event_value(event, "mitigation") or "")[:2000],
                source="observed",
                confidence=1.0,
                updated_at=_event_updated_at(event),
                evidence=_event_evidence(event),
                workspace_id=str(event.get("workspace_id") or ""),
                warnings=warnings,
            )
        )
    return sorted(rows, key=lambda item: (item.status == "resolved", -_risk_weight(item.severity), item.risk_id))


def _decision_states(events: list[dict[str, Any]]) -> list[ProjectDecision]:
    rows: list[ProjectDecision] = []
    for decision_id, event in fold_project_events(events, "project_decision_recorded").items():
        status = str(_event_value(event, "status") or "proposed")
        warnings: list[str] = []
        if status not in {"proposed", "accepted", "rejected", "superseded"}:
            warnings.append(f"invalid decision status ignored: {status}")
            status = "proposed"
        rows.append(
            ProjectDecision(
                decision_id=decision_id,
                title=str(_event_value(event, "title") or decision_id),
                context=str(_event_value(event, "context") or "")[:2000],
                decision=str(_event_value(event, "decision") or "")[:2000],
                owner=str(_event_value(event, "owner") or "")[:120],
                status=status,
                source="observed",
                confidence=1.0,
                updated_at=_event_updated_at(event),
                evidence=_event_evidence(event),
                workspace_id=str(event.get("workspace_id") or ""),
                warnings=warnings,
            )
        )
    return sorted(rows, key=lambda item: (item.status != "proposed", item.updated_at, item.decision_id), reverse=True)


def _initiative_states(events: list[dict[str, Any]]) -> list[ProjectInitiative]:
    rows: list[ProjectInitiative] = []
    for initiative_id, event in fold_project_events(events, "project_initiative_confirmed").items():
        rows.append(
            ProjectInitiative(
                initiative_id=initiative_id,
                suggestion_id=str(_event_value(event, "suggestion_id") or ""),
                title=str(_event_value(event, "title") or initiative_id),
                description=str(_event_value(event, "description") or "")[:2000],
                owner=str(_event_value(event, "owner") or "")[:120],
                outcome_id=str(_event_value(event, "outcome_id") or ""),
                source="observed",
                confidence=1.0,
                updated_at=_event_updated_at(event),
                evidence=_event_evidence(event),
                workspace_id=str(event.get("workspace_id") or ""),
            )
        )
    return sorted(rows, key=lambda item: (item.updated_at, item.initiative_id), reverse=True)


def _task_observations(
    workspaces: list[ProjectWorkspace],
    *,
    now: datetime,
) -> tuple[list[_TaskObservation], list[str]]:
    cutoff = now.timestamp() - TASK_WINDOW_DAYS * 86400
    rows: dict[str, _TaskObservation] = {}
    warnings: list[str] = []
    for workspace in workspaces:
        root = Path(workspace.path)
        try:
            _project, _workspace, tasks, _runs, _feedback = load_dashboard_state(root)
            memories = recent_task_memories(root, limit=MAX_TASK_RECORDS)
        except OSError:
            warnings.append(f"malformed_or_inaccessible_tasks: {workspace.path}")
            continue
        for task in tasks[-MAX_TASK_RECORDS:]:
            if not _within_window(task.updated_at, cutoff):
                continue
            rows[task.task_id] = _TaskObservation(
                task_id=task.task_id,
                title=task.title,
                updated_at=task.updated_at,
                workspace_id=workspace.workspace_id,
                files=_unique(task.source_paths)[:20],
            )
        for memory in memories[-MAX_TASK_RECORDS:]:
            observed_at = str(memory.get("occurred_at") or memory.get("timestamp") or "")
            if not _within_window(observed_at, cutoff):
                continue
            title = str(memory.get("task") or "").strip()
            if not title:
                continue
            task_id = str(memory.get("logical_task_id") or memory.get("task_id") or "")
            if not task_id:
                task_id = deterministic_entity_id(workspace.workspace_id, "task", title)
            files = _string_list(memory.get("changed_files")) + _string_list(memory.get("selected_files"))
            concepts = _string_list(memory.get("concepts"))
            existing = rows.get(task_id)
            rows[task_id] = _TaskObservation(
                task_id=task_id,
                title=title,
                updated_at=max(observed_at, existing.updated_at if existing else ""),
                workspace_id=workspace.workspace_id,
                files=_unique([*(existing.files if existing else []), *files])[:20],
                concepts=_unique([*(existing.concepts if existing else []), *concepts])[:10],
            )
    ordered = sorted(rows.values(), key=lambda item: (item.updated_at, item.task_id), reverse=True)
    return ordered[:MAX_TASK_RECORDS], warnings


def _initiative_suggestions(
    project: str,
    tasks: list[_TaskObservation],
    events: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[ProjectInitiativeSuggestion]:
    groups: dict[str, dict[str, _TaskObservation]] = defaultdict(dict)
    for task in tasks:
        subjects = [_normalized_subject(value) for value in task.concepts[:3]]
        subjects.extend(_path_subject(path) for path in task.files[:5])
        for subject in _unique(subjects):
            if subject:
                groups[subject][task.task_id] = task

    dismissed = fold_project_events(events, "project_initiative_dismissed")
    confirmed = {
        str(_event_value(event, "suggestion_id") or entity_id)
        for entity_id, event in fold_project_events(events, "project_initiative_confirmed").items()
    }
    suggestions: list[ProjectInitiativeSuggestion] = []
    for subject, task_map in groups.items():
        if len(task_map) < 2:
            continue
        task_rows = sorted(task_map.values(), key=lambda item: (item.updated_at, item.task_id), reverse=True)
        suggestion_id = deterministic_entity_id(project, "initiative-suggestion", subject)
        if suggestion_id in confirmed:
            continue
        dismissal = dismissed.get(suggestion_id)
        if dismissal and _dismissal_active(dismissal, task_rows, now=now):
            continue
        evidence = [
            ProjectEvidence(
                kind="task",
                ref=task.task_id,
                summary=task.title[:500],
                path=task.files[0] if task.files else "",
                occurred_at=task.updated_at,
                workspace_id=task.workspace_id,
            )
            for task in task_rows[:MAX_EVIDENCE]
        ]
        if not evidence:
            continue
        file_count = len({path for task in task_rows for path in task.files})
        score = min(100, 35 + len(task_rows) * 12 + min(20, file_count * 2))
        title_subject = subject.replace("_", " ").replace("-", " ").title()
        suggestions.append(
            ProjectInitiativeSuggestion(
                suggestion_id=suggestion_id,
                title=f"Strengthen {title_subject}",
                rationale=(
                    f"{len(task_rows)} recent tasks touched {title_subject}; confirming an initiative "
                    "would make the repeated work visible at project level."
                ),
                score=score,
                task_ids=[task.task_id for task in task_rows[:20]],
                source="inferred",
                confidence=min(0.95, 0.55 + len(task_rows) * 0.08),
                updated_at=task_rows[0].updated_at,
                evidence=evidence,
                workspace_id=task_rows[0].workspace_id,
            )
        )
    suggestions.sort(key=lambda item: (item.score, item.updated_at, item.suggestion_id), reverse=True)
    return suggestions[:5]


def _timeline_for_workspaces(
    workspaces: list[ProjectWorkspace],
    *,
    limit: int,
) -> list[ProjectTimelineEvent]:
    rows: dict[str, ProjectTimelineEvent] = {}
    for workspace in workspaces:
        root = Path(workspace.path)
        for event in read_events(root, limit=MAX_PROJECT_EVENTS):
            event_id = str(event.get("event_id") or "")
            if not event_id:
                continue
            event_type = str(event.get("event_type") or event.get("type") or "activity")
            kind = _timeline_kind(event_type, event)
            rows[f"event:{event_id}"] = ProjectTimelineEvent(
                event_id=event_id,
                kind=kind,
                title=_timeline_title(event_type),
                summary=str(_event_value(event, "summary") or _event_value(event, "status") or "")[:500],
                entity_id=str(_event_value(event, "entity_id") or event.get("task_id") or ""),
                actor=str(event.get("agent") or event.get("source") or "agentpack"),
                git_sha=str(_event_value(event, "git_sha") or "")[:12],
                branch=str(_event_value(event, "branch") or ""),
                source="observed",
                confidence=1.0,
                updated_at=_event_updated_at(event),
                evidence=_event_evidence(event),
                workspace_id=str(event.get("workspace_id") or workspace.workspace_id),
            )
        sessions, _errors = read_learning_sessions_with_errors(root, limit=MAX_LEARNING_ROWS)
        for session in sessions:
            event_id = f"learning:{session.session_id}"
            rows[event_id] = ProjectTimelineEvent(
                event_id=event_id,
                kind="learning",
                title="Learning session completed" if session.status == "completed" else "Learning session started",
                summary=session.topic or session.question,
                entity_id=session.topic_id,
                actor="developer",
                source="observed",
                confidence=1.0,
                updated_at=session.updated_at or session.created_at,
                evidence=[
                    ProjectEvidence(
                        kind="learning",
                        ref=session.session_id,
                        summary=session.question[:500],
                        path=path,
                        occurred_at=session.updated_at or session.created_at,
                        workspace_id=workspace.workspace_id,
                    )
                    for path in session.evidence_files[:MAX_EVIDENCE]
                ],
                workspace_id=workspace.workspace_id,
            )
        for action in read_action_history(root, limit=80):
            if action.source != "dashboard":
                continue
            event_id = f"dashboard:{action.action_id}"
            rows[event_id] = ProjectTimelineEvent(
                event_id=event_id,
                kind="dashboard",
                title=action.label or "Dashboard action",
                summary=action.output_summary or action.status,
                actor="dashboard",
                source="observed",
                confidence=1.0,
                updated_at=action.ended_at or action.started_at,
                evidence=[ProjectEvidence(kind="action", ref=action.action_id, summary=action.status)],
                workspace_id=workspace.workspace_id,
            )
        for commit in _git_commits(root, limit=MAX_COMMITS):
            sha = commit["sha"]
            event_id = f"commit:{sha}"
            if event_id in rows:
                continue
            rows[event_id] = ProjectTimelineEvent(
                event_id=event_id,
                kind="commit",
                title=commit["subject"] or "Git commit",
                summary=commit["subject"],
                entity_id=sha,
                git_sha=sha[:12],
                branch=workspace.branch,
                tags=commit["tags"],
                source="observed",
                confidence=1.0,
                updated_at=commit["occurred_at"],
                evidence=[ProjectEvidence(kind="commit", ref=sha[:12], summary=commit["subject"])],
                workspace_id=workspace.workspace_id,
            )
    ordered = sorted(rows.values(), key=lambda item: (item.updated_at, item.event_id), reverse=True)
    return ordered[: min(MAX_TIMELINE_ROWS, max(1, limit))]


def _health_snapshot(
    workspaces: list[ProjectWorkspace],
    *,
    outcomes: list[ProjectOutcomeState],
    now: datetime,
) -> ProjectHealthSnapshot:
    dimensions = [
        _delivery_health(outcomes, now=now),
        _check_health(workspaces, check_kind="validation"),
        _architecture_health(workspaces),
        _check_health(workspaces, check_kind="release"),
        _context_health(workspaces),
        _knowledge_health(workspaces),
    ]
    evidence = [item for dimension in dimensions for item in dimension.evidence][:MAX_EVIDENCE]
    return ProjectHealthSnapshot(
        dimensions=dimensions,
        source="inferred",
        confidence=min((item.confidence for item in dimensions), default=0.0),
        updated_at=_latest_timestamp([item.updated_at for item in dimensions]),
        evidence=evidence,
        workspace_id="all" if len(workspaces) != 1 else workspaces[0].workspace_id,
        warnings=_unique([warning for item in dimensions for warning in item.warnings]),
    )


def _delivery_health(outcomes: list[ProjectOutcomeState], *, now: datetime) -> ProjectHealthDimension:
    if not outcomes:
        return _health_dimension("delivery", "unknown", "No declared roadmap is available.")
    blocked = [milestone for outcome in outcomes for milestone in outcome.milestones if milestone.status == "blocked"]
    if blocked:
        return _health_dimension(
            "delivery",
            "blocked",
            f"{len(blocked)} milestone(s) are blocked.",
            evidence=[item for milestone in blocked for item in milestone.evidence][:MAX_EVIDENCE],
            updated_at=_latest_timestamp([item.updated_at for item in blocked]),
        )
    overdue = [
        milestone
        for outcome in outcomes
        for milestone in outcome.milestones
        if milestone.status != "done" and _date_is_overdue(milestone.due_date, now.date())
    ]
    at_risk = [outcome for outcome in outcomes if outcome.status == "at_risk"]
    if overdue or at_risk:
        return _health_dimension(
            "delivery",
            "attention",
            f"{len(overdue)} overdue milestone(s) and {len(at_risk)} at-risk outcome(s).",
            evidence=[
                *[item for milestone in overdue for item in milestone.evidence],
                *[item for outcome in at_risk for item in outcome.evidence],
            ][:MAX_EVIDENCE],
            updated_at=_latest_timestamp([*[item.updated_at for item in overdue], *[item.updated_at for item in at_risk]]),
        )
    active = [outcome for outcome in outcomes if outcome.status not in {"achieved", "paused"}]
    if active:
        return _health_dimension(
            "delivery",
            "healthy",
            f"{len(active)} active outcome(s) have no blocked or overdue milestones.",
            evidence=[item for outcome in active for item in outcome.evidence][:MAX_EVIDENCE],
            updated_at=_latest_timestamp([item.updated_at for item in active]),
        )
    return _health_dimension("delivery", "unknown", "No active outcomes require delivery assessment.")


def _check_health(workspaces: list[ProjectWorkspace], *, check_kind: str) -> ProjectHealthDimension:
    aliases = {"validation": {"", "development", "dev", "validation", "review"}, "release": {"release"}}
    events = [
        event
        for workspace in workspaces
        for event in read_events(Path(workspace.path), limit=MAX_PROJECT_EVENTS)
        if str(event.get("event_type") or "") == "check_completed"
        and str(_event_value(event, "check_kind") or _event_value(event, "kind") or "").lower() in aliases[check_kind]
    ]
    label = "validation" if check_kind == "validation" else "release"
    if not events:
        return _health_dimension(label, "unknown", f"No {label} check has been recorded.")
    latest = max(events, key=lambda event: (_event_updated_at(event), str(event.get("event_id") or "")))
    status = str(_event_value(latest, "status") or "").lower()
    if status in {"failed", "failure", "blocked", "error"} or _safe_int(_event_value(latest, "returncode", 0)) != 0:
        return _health_dimension(
            label,
            "blocked",
            f"The latest {label} check failed.",
            event=latest,
        )
    if _event_matches_current_sha(latest, workspaces):
        return _health_dimension(
            label,
            "healthy",
            f"The latest {label} check passed for the current commit.",
            event=latest,
        )
    return _health_dimension(
        label,
        "stale",
        f"The latest {label} check passed for older code.",
        event=latest,
    )


def _architecture_health(workspaces: list[ProjectWorkspace]) -> ProjectHealthDimension:
    events = [
        event
        for workspace in workspaces
        for event in read_events(Path(workspace.path), limit=MAX_PROJECT_EVENTS)
        if str(event.get("event_type") or "") == "check_completed"
        and str(_event_value(event, "check_kind") or _event_value(event, "kind") or "").lower() == "architecture"
    ]
    if events:
        latest = max(events, key=lambda event: (_event_updated_at(event), str(event.get("event_id") or "")))
        blocking = _safe_int(_event_value(latest, "blocking_violations", 0))
        advisory = _safe_int(_event_value(latest, "advisory_violations", 0))
        if blocking or str(_event_value(latest, "status") or "").lower() in {"failed", "blocked"}:
            return _health_dimension("architecture", "blocked", f"{max(1, blocking)} blocking architecture violation(s).", event=latest)
        if advisory:
            return _health_dimension("architecture", "attention", f"{advisory} advisory architecture violation(s).", event=latest)
        if not _event_matches_current_sha(latest, workspaces):
            return _health_dimension("architecture", "stale", "The architecture check covers older code.", event=latest)
        return _health_dimension("architecture", "healthy", "The current architecture check has no blocking violations.", event=latest)

    receipt = _latest_architecture_receipt(workspaces)
    if receipt is None:
        return _health_dimension("architecture", "unknown", "No architecture graph or receipt is available.")
    if receipt["blocking"]:
        return _health_dimension(
            "architecture",
            "blocked",
            f"{receipt['blocking']} blocking architecture violation(s).",
            evidence=[receipt["evidence"]],
            updated_at=receipt["updated_at"],
        )
    if receipt["advisory"]:
        return _health_dimension(
            "architecture",
            "attention",
            f"{receipt['advisory']} advisory architecture violation(s).",
            evidence=[receipt["evidence"]],
            updated_at=receipt["updated_at"],
        )
    if receipt["stale"]:
        return _health_dimension(
            "architecture",
            "stale",
            "The architecture receipt is older than the current code.",
            evidence=[receipt["evidence"]],
            updated_at=receipt["updated_at"],
        )
    return _health_dimension(
        "architecture",
        "healthy",
        "The current architecture receipt has no blocking violations.",
        evidence=[receipt["evidence"]],
        updated_at=receipt["updated_at"],
    )


def _context_health(workspaces: list[ProjectWorkspace]) -> ProjectHealthDimension:
    statuses: list[tuple[str, ProjectEvidence, str]] = []
    for workspace in workspaces:
        root = Path(workspace.path)
        metadata = load_pack_metadata(root)
        if not metadata:
            statuses.append(("missing", ProjectEvidence(kind="context", summary="Context metadata is missing.", workspace_id=workspace.workspace_id), ""))
            continue
        freshness = task_freshness(root, metadata)
        status = "stale" if freshness.is_stale else "fresh"
        statuses.append(
            (
                status,
                ProjectEvidence(
                    kind="context",
                    ref=".agentpack/pack_metadata.json",
                    path=".agentpack/pack_metadata.json",
                    summary=freshness.reason or "Packed context matches the active task.",
                    occurred_at=str(metadata.get("generated_at") or ""),
                    workspace_id=workspace.workspace_id,
                ),
                str(metadata.get("generated_at") or ""),
            )
        )
    evidence = [item[1] for item in statuses][:MAX_EVIDENCE]
    updated_at = _latest_timestamp([item[2] for item in statuses])
    if any(status == "stale" for status, _evidence, _time in statuses):
        return _health_dimension("context", "stale", "At least one workspace has stale AI context.", evidence=evidence, updated_at=updated_at)
    if statuses and all(status == "fresh" for status, _evidence, _time in statuses):
        return _health_dimension("context", "healthy", "AI context is fresh in every selected workspace.", evidence=evidence, updated_at=updated_at)
    return _health_dimension("context", "unknown", "AI context is missing in one or more selected workspaces.", evidence=evidence, updated_at=updated_at)


def _knowledge_health(workspaces: list[ProjectWorkspace]) -> ProjectHealthDimension:
    sessions = []
    errors = 0
    for workspace in workspaces:
        rows, malformed = read_learning_sessions_with_errors(Path(workspace.path), limit=MAX_LEARNING_ROWS)
        sessions.extend((workspace, row) for row in rows)
        errors += malformed
    if not sessions:
        warnings = [f"malformed_learning_rows: {errors}"] if errors else []
        return _health_dimension("knowledge", "unknown", "No assessed learning history is available.", warnings=warnings)
    statuses = [derive_mastery_status(session) for _workspace, session in sessions]
    evidence = [
        ProjectEvidence(
            kind="learning",
            ref=session.session_id,
            summary=session.topic or session.question,
            path=session.evidence_files[0] if session.evidence_files else "",
            occurred_at=session.updated_at or session.created_at,
            workspace_id=workspace.workspace_id,
        )
        for workspace, session in sessions[-MAX_EVIDENCE:]
    ]
    updated_at = _latest_timestamp([session.updated_at or session.created_at for _workspace, session in sessions])
    if "needs_practice" in statuses:
        return _health_dimension("knowledge", "attention", "At least one relevant topic needs practice.", evidence=evidence, updated_at=updated_at)
    if statuses and all(status == "mastered" for status in statuses):
        return _health_dimension("knowledge", "healthy", "All assessed relevant topics are mastered.", evidence=evidence, updated_at=updated_at)
    return _health_dimension("knowledge", "unknown", "Learning history is developing or unassessed.", evidence=evidence, updated_at=updated_at)


def _project_metrics(
    *,
    outcomes: list[ProjectOutcomeState],
    risks: list[ProjectRisk],
    decisions: list[ProjectDecision],
    initiatives: list[ProjectInitiative],
    recent_changes: list[ProjectTimelineEvent],
    stale_days: int,
    now: datetime,
) -> ProjectMetrics:
    milestones = [milestone for outcome in outcomes for milestone in outcome.milestones]
    completed = sum(milestone.status == "done" for milestone in milestones)
    active_entities: list[Any] = [outcome for outcome in outcomes if outcome.status not in {"achieved", "paused"}]
    active_entities.extend(milestone for milestone in milestones if milestone.status != "done")
    active_entities.extend(risk for risk in risks if risk.status != "resolved")
    active_entities.extend(decision for decision in decisions if decision.status == "proposed")
    active_entities.extend(initiatives)
    covered = sum(
        bool(getattr(entity, "owner", ""))
        and _is_recent(getattr(entity, "updated_at", ""), stale_days=stale_days, now=now)
        and bool(getattr(entity, "evidence", []))
        for entity in active_entities
    )
    coverage = round(covered * 100 / len(active_entities), 1) if active_entities else None
    evidence = [item for entity in active_entities for item in getattr(entity, "evidence", [])][:MAX_EVIDENCE]
    return ProjectMetrics(
        outcome_count=len(outcomes),
        active_outcomes=sum(outcome.status not in {"achieved", "paused"} for outcome in outcomes),
        milestone_count=len(milestones),
        completed_milestones=completed,
        milestone_completion_pct=round(completed * 100 / len(milestones), 1) if milestones else None,
        open_risks=sum(risk.status != "resolved" for risk in risks),
        pending_decisions=sum(decision.status == "proposed" for decision in decisions),
        confirmed_initiatives=len(initiatives),
        recent_changes=len(recent_changes),
        evidence_coverage=coverage,
        source="inferred",
        confidence=1.0,
        updated_at=_latest_timestamp([getattr(entity, "updated_at", "") for entity in active_entities]),
        evidence=evidence,
        workspace_id="all",
    )


def _summary_brief_lines(overview: ProjectOverview) -> list[str]:
    profile = overview.profile
    lines = [f"# {profile.display_name} Status", ""]
    if profile.purpose:
        lines.extend([profile.purpose, ""])
    lines.extend(
        [
            "## Project",
            "",
            f"- Stage: {profile.stage or 'Not declared'}",
            f"- Owners: {', '.join(profile.owners) or 'Not declared'}",
            f"- Milestones: {overview.metrics.completed_milestones}/{overview.metrics.milestone_count or 0} complete",
            f"- Open risks: {overview.metrics.open_risks}",
            f"- Pending decisions: {overview.metrics.pending_decisions}",
            f"- Evidence coverage: {_format_percentage(overview.metrics.evidence_coverage)}",
            "",
            "## Outcomes",
            "",
        ]
    )
    if overview.outcomes:
        for outcome in overview.outcomes:
            progress = _format_percentage(outcome.progress_pct)
            lines.append(f"- [{outcome.status}] {outcome.title} - {progress}")
    else:
        lines.append("- No outcomes declared.")
    lines.extend(["", "## Health", ""])
    for dimension in overview.health.dimensions:
        lines.append(f"- {dimension.dimension.title()}: {dimension.status} - {dimension.summary}")
    lines.extend(["", "## Risks And Decisions", ""])
    open_risks = [risk for risk in overview.risks if risk.status != "resolved"]
    pending = [decision for decision in overview.decisions if decision.status == "proposed"]
    if open_risks:
        lines.extend(f"- Risk [{risk.severity}]: {risk.title}" for risk in open_risks[:5])
    if pending:
        lines.extend(f"- Decision pending: {decision.title}" for decision in pending[:5])
    if not open_risks and not pending:
        lines.append("- No open risks or pending decisions recorded.")
    lines.extend(["", "## Next Actions", ""])
    next_actions = _brief_next_actions(overview)
    lines.extend(f"- {item}" for item in next_actions)
    return lines


def _engineering_brief_lines(overview: ProjectOverview) -> list[str]:
    lines = ["", "## Engineering Evidence", ""]
    for workspace in overview.workspaces:
        lines.append(f"- {workspace.branch or 'detached'} @ {workspace.git_sha or 'unknown'} ({workspace.workspace_id})")
    checks = [item for item in overview.recent_changes if item.kind in {"check", "review"}]
    if checks:
        lines.extend(["", "### Recent Checks", ""])
        for check in checks[:10]:
            detail = f" [{check.git_sha}]" if check.git_sha else ""
            lines.append(f"- {check.title}{detail}: {check.summary or 'recorded'}")
            for evidence in check.evidence[:3]:
                if evidence.path:
                    lines.append(f"  - {evidence.path}: {evidence.summary or evidence.ref}")
    lines.extend(["", "### Recent Commits", ""])
    commits = [item for item in overview.recent_changes if item.kind == "commit"]
    lines.extend(f"- {item.git_sha}: {item.title}" for item in commits[:10])
    if not commits:
        lines.append("- No recent commits were available.")
    return lines


def _brief_next_actions(overview: ProjectOverview) -> list[str]:
    actions: list[str] = []
    for dimension in overview.health.dimensions:
        if dimension.status in {"blocked", "attention", "stale"}:
            actions.append(f"Address {dimension.dimension}: {dimension.summary}")
    actions.extend(f"Resolve risk: {risk.title}" for risk in overview.risks if risk.status in {"open", "mitigating"})
    actions.extend(f"Decide: {decision.title}" for decision in overview.decisions if decision.status == "proposed")
    actions.extend(f"Review initiative suggestion: {item.title}" for item in overview.initiative_suggestions[:2])
    return _unique(actions)[:8] or ["No evidence-backed next action is currently available."]


def _health_dimension(
    dimension: str,
    status: str,
    summary: str,
    *,
    event: dict[str, Any] | None = None,
    evidence: list[ProjectEvidence] | None = None,
    updated_at: str = "",
    warnings: list[str] | None = None,
) -> ProjectHealthDimension:
    event_evidence = _event_evidence(event) if event else []
    return ProjectHealthDimension(
        dimension=dimension,
        status=status,
        summary=summary,
        source="inferred",
        confidence=1.0 if status != "unknown" else 0.0,
        updated_at=updated_at or (_event_updated_at(event) if event else ""),
        evidence=(evidence or event_evidence)[:MAX_EVIDENCE],
        workspace_id=str((event or {}).get("workspace_id") or "all"),
        warnings=warnings or [],
    )


def _event_evidence(event: dict[str, Any] | None) -> list[ProjectEvidence]:
    if not event:
        return []
    workspace = str(event.get("workspace_id") or "")
    occurred_at = _event_updated_at(event)
    evidence: list[ProjectEvidence] = []
    for item in event.get("evidence", [])[:MAX_EVIDENCE]:
        if not isinstance(item, dict):
            continue
        path = _relative_evidence_path(str(item.get("path") or ""))
        evidence.append(
            ProjectEvidence(
                kind=str(item.get("kind") or "event")[:64],
                ref=str(item.get("ref") or "")[:240],
                summary=str(item.get("summary") or item.get("note") or "")[:500],
                path=path,
                occurred_at=str(item.get("occurred_at") or occurred_at),
                workspace_id=workspace,
            )
        )
    if not evidence:
        evidence.append(
            ProjectEvidence(
                kind="event",
                ref=str(event.get("event_id") or ""),
                summary=str(_event_value(event, "summary") or event.get("event_type") or "")[:500],
                occurred_at=occurred_at,
                workspace_id=workspace,
            )
        )
    return evidence


def _latest_architecture_receipt(workspaces: list[ProjectWorkspace]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for workspace in workspaces:
        root = Path(workspace.path)
        paths = [
            root / ".agentpack" / "artifacts" / "architecture-receipt.json",
            root / ".agentpack" / "architecture" / "architecture-receipt.json",
        ]
        for path in paths:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            except (OSError, ValueError):
                continue
            blocking = _safe_int(payload.get("blocking_violations")) if isinstance(payload, dict) else 0
            advisory = _safe_int(payload.get("advisory_violations")) if isinstance(payload, dict) else 0
            markdown = path.with_name("architecture-diff.md")
            if markdown.exists() and not (blocking or advisory):
                try:
                    text = markdown.read_text(encoding="utf-8")
                except OSError:
                    text = ""
                blocking = _markdown_count(text, "Blocking invariant results")
                advisory = _markdown_count(text, "Advisory invariant results")
            latest_commit = _parse_time(workspace.updated_at)
            receipt_time = _parse_time(updated_at)
            candidates.append(
                {
                    "blocking": blocking,
                    "advisory": advisory,
                    "updated_at": updated_at,
                    "stale": bool(latest_commit and receipt_time and receipt_time < latest_commit),
                    "evidence": ProjectEvidence(
                        kind="architecture_receipt",
                        ref=path.name,
                        path=str(path.relative_to(root)),
                        summary="Sanitized architecture check receipt.",
                        occurred_at=updated_at,
                        workspace_id=workspace.workspace_id,
                    ),
                }
            )
    return max(candidates, key=lambda item: item["updated_at"]) if candidates else None


def _git_commits(root: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["git", "log", f"-{min(MAX_COMMITS, max(1, limit))}", "--format=%H%x1f%aI%x1f%s%x1f%D"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    commits: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f", 3)
        if len(parts) != 4:
            continue
        decorations = parts[3]
        tags = [item.strip().removeprefix("tag: ") for item in decorations.split(",") if item.strip().startswith("tag: ")]
        commits.append({"sha": parts[0], "occurred_at": parts[1], "subject": parts[2], "tags": tags[:20]})
    return commits


def _timeline_kind(event_type: str, event: dict[str, Any]) -> str:
    if event_type.startswith("project_"):
        return "project"
    if event_type == "check_completed":
        kind = str(_event_value(event, "check_kind") or _event_value(event, "kind") or "").lower()
        return "review" if kind == "review" else "check"
    if "review" in event_type:
        return "review"
    if event_type.startswith("task_"):
        return "task"
    if "learning" in event_type or event_type == "memory_recorded":
        return "learning"
    if "session" in event_type:
        return "session"
    return "activity"


def _timeline_title(event_type: str) -> str:
    labels = {
        "project_outcome_status": "Outcome status updated",
        "project_milestone_status": "Milestone status updated",
        "project_risk_upsert": "Project risk updated",
        "project_decision_recorded": "Project decision recorded",
        "project_initiative_confirmed": "Initiative confirmed",
        "project_initiative_dismissed": "Initiative suggestion dismissed",
        "check_completed": "Check completed",
        "task_started": "Task started",
        "task_completed": "Task completed",
        "context_prepared": "AI context prepared",
    }
    return labels.get(event_type, event_type.replace("_", " ").strip().capitalize() or "Project activity")


def _dismissal_active(event: dict[str, Any], tasks: list[_TaskObservation], *, now: datetime) -> bool:
    dismissed_at = _parse_time(_event_updated_at(event))
    if dismissed_at is None or (now - dismissed_at).days >= DISMISSAL_DAYS:
        return False
    previous_ids = set(_string_list(_event_value(event, "evidence_task_ids", [])))
    if any(task.task_id not in previous_ids for task in tasks):
        return False
    return not any((_parse_time(task.updated_at) or dismissed_at) > dismissed_at for task in tasks)


def _event_matches_current_sha(event: dict[str, Any], workspaces: list[ProjectWorkspace]) -> bool:
    sha = str(_event_value(event, "git_sha") or "")
    event_workspace = str(event.get("workspace_id") or "")
    candidates = [workspace for workspace in workspaces if not event_workspace or workspace.workspace_id == event_workspace]
    return bool(sha) and any(workspace.git_sha and workspace.git_sha.startswith(sha[:12]) for workspace in candidates)


def _path_subject(path: str) -> str:
    if path.replace("\\", "/").startswith(".agentpack/"):
        return ""
    parts = [part.lower() for part in PurePosixPath(path.replace("\\", "/")).parts if part not in {".", "src", "lib", "app", "tests", "test"}]
    if not parts:
        return ""
    candidate = parts[0] if len(parts) == 1 else parts[-2] if "." in parts[-1] else parts[-1]
    return _normalized_subject(candidate)


def _normalized_subject(value: str) -> str:
    subject = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return "" if subject in {"", "core", "utils", "common", "service", "services", "index", "main"} else subject[:80]


def _relative_evidence_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts:
        return ""
    return str(path)[:500]


def _event_updated_at(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    return str(event.get("occurred_at") or event.get("timestamp") or "")


def _within_window(value: str, cutoff_timestamp: float) -> bool:
    parsed = _parse_time(value)
    return bool(parsed and parsed.timestamp() >= cutoff_timestamp)


def _is_recent(value: str, *, stale_days: int, now: datetime) -> bool:
    parsed = _parse_time(value)
    return bool(parsed and (now - parsed).total_seconds() <= stale_days * 86400)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _latest_timestamp(values: list[str]) -> str:
    valid = [(parsed, value) for value in values if (parsed := _parse_time(value)) is not None]
    return max(valid, key=lambda item: item[0])[1] if valid else ""


def _date_is_overdue(value: str, today: date) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(value) < today
    except ValueError:
        return False


def _risk_weight(value: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(value, 0)


def _markdown_count(text: str, label: str) -> int:
    match = re.search(rf"{re.escape(label)}:\s*(\d+)", text)
    return int(match.group(1)) if match else 0


def _format_percentage(value: float | None) -> str:
    return "Not ready" if value is None else f"{value:g}%"


def _truncate_utf8(value: str, maximum: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    return encoded[: maximum - 1].decode("utf-8", errors="ignore").rstrip() + "\n"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _event_value(event: dict[str, Any], key: str, default: Any = "") -> Any:
    if key in event:
        return event.get(key, default)
    payload = event.get("payload")
    return payload.get(key, default) if isinstance(payload, dict) else default


def _load_raw_config(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise ProjectValidationError(f"could not parse {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectValidationError("project configuration must be a TOML table")
    return payload


def _write_raw_config(root: Path, payload: dict[str, Any]) -> None:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            tomli_w.dump(payload, handle)
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProjectReadOnlyError(f"could not update project configuration: {exc}") from exc


def _validate_profile_updates(project: str, updates: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "display_name",
        "purpose",
        "audiences",
        "owners",
        "stage",
        "links",
        "environments",
        "status_stale_days",
        "outcomes",
    }
    unknown = sorted(set(updates) - allowed)
    if unknown:
        raise ProjectValidationError(f"unknown profile fields: {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    if "display_name" in updates:
        clean["display_name"] = _bounded_text(updates["display_name"], "display_name", 160)
    if "purpose" in updates:
        clean["purpose"] = _bounded_text(updates["purpose"], "purpose", 2000)
    for key in ("audiences", "owners", "environments"):
        if key in updates:
            clean[key] = _bounded_string_list(updates[key], key)
    if "stage" in updates:
        stage = str(updates["stage"] or "").strip()
        if stage and stage not in PROJECT_STAGES:
            raise ProjectValidationError(f"stage must be one of: {', '.join(sorted(PROJECT_STAGES))}")
        clean["stage"] = stage
    if "links" in updates:
        clean["links"] = _safe_links(updates["links"])
    if "status_stale_days" in updates:
        value = updates["status_stale_days"]
        if isinstance(value, bool):
            raise ProjectValidationError("status_stale_days must be an integer")
        try:
            days = int(value)
        except (TypeError, ValueError) as exc:
            raise ProjectValidationError("status_stale_days must be an integer") from exc
        if not 1 <= days <= 3650:
            raise ProjectValidationError("status_stale_days must be between 1 and 3650")
        clean["status_stale_days"] = days
    if "outcomes" in updates:
        clean["outcomes"] = _validate_outcomes(project, updates["outcomes"])
    return clean


def _validate_outcomes(project: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_OUTCOMES:
        raise ProjectValidationError(f"outcomes must be a list with at most {MAX_OUTCOMES} items")
    outcomes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ProjectValidationError("each outcome must be an object")
        unknown = set(item) - {"id", "title", "description", "owner", "target_date", "milestones"}
        if unknown:
            raise ProjectValidationError(f"unknown outcome fields: {', '.join(sorted(unknown))}")
        title = _bounded_text(item.get("title"), "outcome title", 160, required=True)
        outcome_id = str(item.get("id") or deterministic_entity_id(project, "outcome", title))
        _validate_identifier(outcome_id, "outcome id")
        if outcome_id in seen:
            raise ProjectValidationError(f"duplicate outcome id: {outcome_id}")
        seen.add(outcome_id)
        target_date = _validated_date(item.get("target_date"), "target_date")
        milestones = _validate_milestones(project, outcome_id, item.get("milestones", []))
        outcomes.append(
            {
                "id": outcome_id,
                "title": title,
                "description": _bounded_text(item.get("description"), "outcome description", 2000),
                "owner": _bounded_text(item.get("owner"), "outcome owner", 120),
                "target_date": target_date,
                "milestones": milestones,
            }
        )
    return outcomes


def _validate_milestones(project: str, outcome_id: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_MILESTONES:
        raise ProjectValidationError(f"milestones must be a list with at most {MAX_MILESTONES} items")
    milestones: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ProjectValidationError("each milestone must be an object")
        unknown = set(item) - {"id", "title", "owner", "due_date"}
        if unknown:
            raise ProjectValidationError(f"unknown milestone fields: {', '.join(sorted(unknown))}")
        title = _bounded_text(item.get("title"), "milestone title", 160, required=True)
        milestone_id = str(item.get("id") or deterministic_entity_id(project, "milestone", title, parent_id=outcome_id))
        _validate_identifier(milestone_id, "milestone id")
        if milestone_id in seen:
            raise ProjectValidationError(f"duplicate milestone id: {milestone_id}")
        seen.add(milestone_id)
        milestones.append(
            {
                "id": milestone_id,
                "title": title,
                "owner": _bounded_text(item.get("owner"), "milestone owner", 120),
                "due_date": _validated_date(item.get("due_date"), "due_date"),
            }
        )
    return milestones


def _validate_evidence(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            raise ProjectValidationError("evidence entries must be objects")
        path = str(item.get("path") or "").strip()
        if path:
            parsed = PurePosixPath(path.replace("\\", "/"))
            if parsed.is_absolute() or ".." in parsed.parts:
                raise ProjectValidationError("evidence paths must be repository-relative")
        clean.append(
            {
                "kind": _bounded_text(item.get("kind"), "evidence kind", 64, required=True),
                "ref": _bounded_text(item.get("ref"), "evidence ref", 240),
                "summary": _bounded_text(item.get("summary"), "evidence summary", 500),
                "path": path[:500],
            }
        )
    return clean


def _validate_identifier(value: str, field: str) -> None:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ProjectValidationError(f"{field} must be 1-64 letters, numbers, dots, underscores, or hyphens")


def _bounded_text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) and value is not None:
        raise ProjectValidationError(f"{field} must be a string")
    text = str(value or "").strip()
    if required and not text:
        raise ProjectValidationError(f"{field} is required")
    if len(text) > maximum:
        raise ProjectValidationError(f"{field} must be at most {maximum} characters")
    return text


def _bounded_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST_VALUES:
        raise ProjectValidationError(f"{field} must be a list with at most {MAX_LIST_VALUES} items")
    return [_bounded_text(item, field, 120, required=True) for item in value]


def _safe_links(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_LINKS:
        raise ProjectValidationError(f"links must be an object with at most {MAX_LINKS} entries")
    links: dict[str, str] = {}
    for key, url in value.items():
        name = str(key or "").strip()
        _validate_identifier(name, "link name")
        safe_url = _bounded_text(url, f"link {name}", 500, required=True)
        if not _SAFE_LINK_RE.match(safe_url):
            raise ProjectValidationError(f"link {name} must use http or https")
        links[name] = safe_url
    return links


def _validated_date(value: Any, field: str) -> str:
    text = _bounded_text(value, field, 10)
    if not text:
        return ""
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ProjectValidationError(f"{field} must use YYYY-MM-DD") from exc
    return text


def _git_worktree_entries(root: Path) -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in [*result.stdout.splitlines(), ""]:
        if not line.strip():
            if current.get("path"):
                entries.append(current)
            current = {}
            continue
        key, _, raw_value = line.partition(" ")
        if key == "worktree":
            current["path"] = raw_value.strip()
        elif key == "HEAD":
            current["sha"] = raw_value.strip()
        elif key == "branch":
            current["branch"] = raw_value.strip()
    return entries


def _latest_commit_time(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _config_updated_at(root: Path) -> str:
    try:
        timestamp = config_path(root).stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
