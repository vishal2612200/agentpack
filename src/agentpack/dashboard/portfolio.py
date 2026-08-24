"""Bounded, local-first portfolio data for Engineering Atlas."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from agentpack.core.project_index import agentpack_home, load_project_index
from agentpack.core import git
from agentpack.dashboard.contracts import CachedProjectStatus
from agentpack.dashboard.models import (
    DashboardWorkspaceRecord,
    PortfolioActivity,
    PortfolioPayload,
    PortfolioProject,
    PortfolioRelation,
    ProjectEvidence,
    ProjectHealthSnapshot,
)
from agentpack.session.identity import project_id as canonical_project_id, workspace_id


MAX_PROJECTS = 200
MAX_RELATIONS = 500
MAX_ATTENTION = 200
MAX_ACTIVITY = 200
MAX_CACHE_AGE_SECONDS = 7 * 24 * 60 * 60


def status_cache_path(project: str) -> Path:
    return agentpack_home() / "dashboard" / "projects" / project / "status.json"


def github_cache_path(project: str) -> Path:
    return agentpack_home() / "dashboard" / "github" / f"{project}.json"


def write_status_cache(status: CachedProjectStatus) -> None:
    path = status_cache_path(status.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(status.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_status_cache(project: str) -> tuple[CachedProjectStatus | None, int | None, list[str]]:
    path = status_cache_path(project)
    if not path.exists():
        return None, None, ["status cache unavailable"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        status = CachedProjectStatus.model_validate(payload)
        age = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(status.generated_at.replace("Z", "+00:00"))).total_seconds()))
        return status, age, []
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, None, [f"status cache unreadable: {type(exc).__name__}"]


def read_github_cache(project: str) -> dict[str, Any] | None:
    try:
        value = json.loads(github_cache_path(project).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        fetched = str(value.get("fetched_at") or "")
        age = int((datetime.now(timezone.utc) - datetime.fromisoformat(fetched.replace("Z", "+00:00"))).total_seconds()) if fetched else None
        value["cache_age_seconds"] = max(0, age) if age is not None else None
        value["stale"] = age is None or age > 15 * 60
        return value
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def build_portfolio_payload(root: Path, *, include_inferred: bool = True, now: datetime | None = None) -> dict[str, Any]:
    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    rows = load_project_index()
    launch_id = canonical_project_id(root.resolve())
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        identity = str(row.get("project_id") or "")
        if identity:
            grouped.setdefault(identity, []).append({**row, "project_id": identity})
    if launch_id not in grouped and root.exists():
        grouped[launch_id] = [{
            "project_id": launch_id,
            "path": str(root.resolve()),
            "name": root.name,
            "branch": git.current_branch(root) or "",
            "git_sha": (git.current_sha(root) or "")[:12],
            "last_seen_at": generated_at,
        }]

    projects: list[PortfolioProject] = []
    attention: list[dict[str, Any]] = []
    activity: list[PortfolioActivity] = []
    warnings: list[str] = []
    for project, project_rows in list(grouped.items())[:MAX_PROJECTS]:
        cached, age, cache_warnings = read_status_cache(project)
        warnings.extend(f"{project}: {item}" for item in cache_warnings)
        paths = [str(item.get("path") or "") for item in project_rows]
        exists = any(Path(path).exists() for path in paths)
        workspace_rows = [
            DashboardWorkspaceRecord(
                workspace_id=workspace_id(Path(path), current_project_id=project) if path else "",
                project_id=project,
                path=path,
                branch=str(item.get("branch") or ""),
                git_sha=str(item.get("git_sha") or ""),
            )
            for item, path in zip(project_rows, paths)
            if path
        ]
        profile = cached.profile if cached else None
        name = (profile.display_name if profile else "") or str(project_rows[0].get("name") or Path(paths[0]).name or project)
        health = cached.health if cached else ProjectHealthSnapshot()
        stale = not cached or (age is not None and age > MAX_CACHE_AGE_SECONDS)
        project_item = PortfolioProject(
            project_id=project,
            key=profile.key if profile else str(project_rows[0].get("project_key") or ""),
            name=name if profile else str(project_rows[0].get("display_name") or name),
            purpose=profile.purpose if profile else str(project_rows[0].get("purpose") or ""),
            stage=profile.stage if profile else str(project_rows[0].get("stage") or ""),
            owners=profile.owners if profile else list(project_rows[0].get("owners") or [])[:20],
            capabilities=profile.capabilities if profile else list(project_rows[0].get("capabilities") or [])[:50],
            links=profile.links if profile else dict(project_rows[0].get("links") or {}),
            relations_config=(getattr(profile, "relations", None) or list(project_rows[0].get("relations") or [])[:50]),
            github=read_github_cache(project),
            workspaces=workspace_rows,
            branch=cached.branch if cached else str(project_rows[0].get("branch") or ""),
            git_sha=cached.git_sha if cached else str(project_rows[0].get("git_sha") or ""),
            health=health,
            focus=cached.focus if cached else None,
            task_count=cached.task_count if cached else 0,
            agent_count=cached.agent_count if cached else 0,
            risks=cached.risks if cached else [],
            decisions=cached.decisions if cached else [],
            last_activity=max((item.updated_at or "" for item in cached.recent_changes), default="") if cached else "",
            cache_age_seconds=age,
            stale=stale,
            unavailable=not exists,
            source="cache" if cached else "index",
            confidence=0.9 if cached else 0.4,
            generated_at=cached.generated_at if cached else str(project_rows[0].get("last_seen_at") or ""),
            warnings=[*cache_warnings, *(cached.warnings if cached else [])],
        )
        projects.append(project_item)
        if stale or not exists:
            attention.append({"project_id": project, "kind": "stale" if stale else "unavailable", "title": "Project status needs refresh", "summary": "Last known project metadata is stale or workspace is unavailable.", "occurred_at": project_item.generated_at, "source": "index"})
        for risk in project_item.risks:
            if risk.status not in {"resolved", "accepted"}:
                attention.append({"project_id": project, "kind": "risk", "title": risk.title, "summary": risk.description, "severity": risk.severity, "occurred_at": risk.updated_at, "source": "cache", "confidence": risk.confidence})
        for event in (cached.recent_changes if cached else [])[:MAX_ACTIVITY]:
            activity.append(PortfolioActivity(project_id=project, workspace_id=event.workspace_id, kind=event.kind, title=event.title, summary=event.summary, occurred_at=event.updated_at, source=event.source, confidence=event.confidence))

    relations = infer_relations(projects) if include_inferred else []
    relations.extend(declared_relations(projects))
    relations = _dedupe_relations(relations)[:MAX_RELATIONS]
    activity.sort(key=lambda item: item.occurred_at, reverse=True)
    attention.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
    return PortfolioPayload(
        generated_at=generated_at,
        partial=len(grouped) > MAX_PROJECTS or bool(warnings),
        warnings=list(dict.fromkeys(warnings))[:50],
        projects=projects,
        relations=relations,
        attention=attention[:MAX_ATTENTION],
        recent_activity=activity[:MAX_ACTIVITY],
    ).model_dump(mode="json")


def declared_relations(projects: list[PortfolioProject]) -> list[PortfolioRelation]:
    key_groups: dict[str, list[PortfolioProject]] = {}
    for item in projects:
        if item.key:
            key_groups.setdefault(item.key, []).append(item)
    by_key = {key: values[0] for key, values in key_groups.items() if len(values) == 1}
    duplicate_keys = {key for key, values in key_groups.items() if len(values) > 1}
    result: list[PortfolioRelation] = []
    for item in projects:
        for relation in item.relations_config:
            target = str(relation.get("target") or "")
            resolved = by_key.get(target)
            duplicate = target in duplicate_keys
            result.append(PortfolioRelation(
                relation_id=_relation_id(item.project_id, target, str(relation.get("type") or "")),
                source_project_id=item.project_id,
                target_project_id=resolved.project_id if resolved else "",
                target_key=target,
                type=str(relation.get("type") or ""),
                label=str(relation.get("label") or ""),
                declared=True,
                confidence=1.0,
                unresolved=resolved is None,
                evidence=[ProjectEvidence(kind="config", path=".agentpack/config.toml", summary=str(relation.get("evidence") or "Declared project relation."), occurred_at=item.generated_at)],
                warnings=[f"duplicate project key: {target}"] if duplicate else [],
            ))
    return result


def infer_relations(projects: list[PortfolioProject]) -> list[PortfolioRelation]:
    by_name: dict[str, PortfolioProject] = {}
    manifests: dict[str, list[tuple[str, str, str]]] = {}
    for project in projects:
        root = Path(project.workspaces[0].path) if project.workspaces else None
        if root is None or not root.exists():
            continue
        names = _manifest_names(root)
        for name in names:
            by_name.setdefault(name, project)
        manifests[project.project_id] = _manifest_dependencies(root)
    result: list[PortfolioRelation] = []
    for project in projects:
        for dependency, manifest_path, pointer in manifests.get(project.project_id, []):
            target = by_name.get(dependency)
            if not target or target.project_id == project.project_id:
                continue
            result.append(PortfolioRelation(
                relation_id=_relation_id(project.project_id, target.project_id, "depends_on"),
                source_project_id=project.project_id,
                target_project_id=target.project_id,
                type="depends_on",
                label=dependency,
                confidence=0.9,
                evidence=[ProjectEvidence(kind="manifest", path=manifest_path, ref=pointer, summary="Exact package dependency match.", occurred_at=project.generated_at)],
            ))
    return result


def _manifest_names(root: Path) -> list[str]:
    result: list[str] = []
    package = root / "package.json"
    try:
        if package.exists():
            value = json.loads(package.read_text(encoding="utf-8"))
            if isinstance(value.get("name"), str):
                result.append(value["name"])
    except (OSError, ValueError):
        pass
    for filename in ("pyproject.toml", "Cargo.toml"):
        try:
            value = tomllib.loads((root / filename).read_text(encoding="utf-8"))
            section = value.get("project", {}) if filename.startswith("py") else value.get("package", {})
            if isinstance(section, dict) and isinstance(section.get("name"), str):
                result.append(section["name"])
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            pass
    try:
        for line in (root / "go.mod").read_text(encoding="utf-8").splitlines():
            if line.startswith("module "):
                result.append(line.split(maxsplit=1)[1].strip())
                break
    except OSError:
        pass
    return result


def _manifest_dependencies(root: Path) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    package = root / "package.json"
    try:
        value = json.loads(package.read_text(encoding="utf-8"))
        for field in ("dependencies", "devDependencies", "peerDependencies"):
            for name, spec in (value.get(field) or {}).items():
                if isinstance(spec, str) and spec.startswith("file:"):
                    target_root = (root / spec.removeprefix("file:")).resolve()
                    result.extend((target, "package.json", f"/{field}/{name}") for target in _manifest_names(target_root))
                else:
                    result.append((str(name), "package.json", f"/{field}/{name}"))
    except (OSError, ValueError):
        pass
    try:
        value = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = value.get("project", {}).get("dependencies", [])
        for dependency in dependencies if isinstance(dependencies, list) else []:
            match = re.match(r"[A-Za-z0-9_.-]+", str(dependency))
            if match:
                result.append((match.group(0), "pyproject.toml", "/project/dependencies"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        pass
    try:
        lines = (root / "go.mod").read_text(encoding="utf-8").splitlines()
        in_require = False
        for line in lines:
            clean = line.strip()
            if clean == "require (":
                in_require = True
                continue
            if in_require and clean == ")":
                in_require = False
                continue
            if clean.startswith("require "):
                clean = clean.removeprefix("require ")
            if in_require or clean.startswith(("github.com/", "golang.org/", "gopkg.in/")):
                dependency = clean.split()[0] if clean else ""
                if dependency:
                    result.append((dependency, "go.mod", "/require"))
    except OSError:
        pass
    try:
        value = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
        dependencies = value.get("dependencies", {})
        if isinstance(dependencies, dict):
            for name, detail in dependencies.items():
                dependency = detail.get("package") if isinstance(detail, dict) else name
                if isinstance(dependency, str):
                    result.append((dependency, "Cargo.toml", f"/dependencies/{name}"))
                if isinstance(detail, dict) and isinstance(detail.get("path"), str):
                    target_root = (root / detail["path"]).resolve()
                    result.extend((target, "Cargo.toml", f"/dependencies/{name}/path") for target in _manifest_names(target_root))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        pass
    return result


def _relation_id(source: str, target: str, relation_type: str) -> str:
    return "relation-" + hashlib.sha256(f"{source}:{target}:{relation_type}".encode()).hexdigest()[:20]


def _dedupe_relations(relations: list[PortfolioRelation]) -> list[PortfolioRelation]:
    result: dict[tuple[str, str, str], PortfolioRelation] = {}
    for relation in relations:
        key = (relation.source_project_id, relation.target_key or relation.target_project_id, relation.type)
        previous = result.get(key)
        if previous is None or relation.declared:
            if previous and previous.evidence:
                relation.evidence = [*relation.evidence, *previous.evidence]
            result[key] = relation
        elif previous:
            previous.evidence.extend(relation.evidence[:5])
    return list(result.values())
