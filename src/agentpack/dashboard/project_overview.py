from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
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
from agentpack.dashboard.models import (
    ProjectEvidence,
    ProjectProfile,
    ProjectWorkspace,
)
from agentpack.session.events import read_events, record_event
from agentpack.session.identity import project_id, workspace_id


MAX_WORKTREES = 20
MAX_PROJECT_EVENTS = 200
MAX_OUTCOMES = 50
MAX_MILESTONES = 100
MAX_LIST_VALUES = 20
MAX_LINKS = 20
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
