"""Explicit, read-only GitHub evidence refresh through installed ``gh`` CLI."""

from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any

from agentpack.dashboard.portfolio import github_cache_path


MAX_PROJECTS = 20
CONCURRENCY = 4
TIMEOUT_SECONDS = 10


def refresh_github_evidence(projects: list[dict[str, Any]], *, selected_project_id: str = "") -> dict[str, Any]:
    candidates = [item for item in projects if not selected_project_id or item.get("project_id") == selected_project_id][:MAX_PROJECTS]
    results: list[dict[str, Any]] = []
    stop = Event()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {executor.submit(_refresh_one, item, stop): item for item in candidates}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if result.get("status") == "rate_limited":
                stop.set()
    results.sort(key=lambda item: str(item.get("project_id") or ""))
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "partial": len(results) < len(candidates) or any(item.get("status") not in {"ok", "unknown"} for item in results), "projects": results}


def _refresh_one(project: dict[str, Any], stop: Event) -> dict[str, Any]:
    project_id = str(project.get("project_id") or "")
    if stop.is_set():
        return {"project_id": project_id, "status": "partial", "remediation": "Refresh stopped after GitHub rate limit."}
    root = Path(str((project.get("workspaces") or [{}])[0].get("path") or ""))
    repository = _repository(project.get("links"), root)
    generated_at = datetime.now(timezone.utc).isoformat()
    if not repository:
        return _write_result(project_id, {"status": "unknown", "remediation": "Declare project.links.github or configure origin remote.", "generated_at": generated_at})
    try:
        prs = _gh_json(["pr", "list", "--repo", repository, "--state", "open", "--limit", "50", "--json", "number,title,isDraft,reviewDecision,mergeStateStatus,headRefOid,updatedAt"], timeout=TIMEOUT_SECONDS)
        issues = _gh_json(["issue", "list", "--repo", repository, "--state", "open", "--limit", "10", "--json", "number,title,updatedAt,labels"], timeout=TIMEOUT_SECONDS)
        releases = _gh_json(["release", "list", "--repo", repository, "--limit", "1", "--json", "tagName,publishedAt"], timeout=TIMEOUT_SECONDS)
        checks: list[dict[str, Any]] = []
        for pr in prs if isinstance(prs, list) else []:
            sha = str(pr.get("headRefOid") or "")
            if not sha or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                continue
            check_payload = _gh_json(["api", f"repos/{repository}/commits/{sha}/check-runs", "--jq", ".check_runs"], timeout=TIMEOUT_SECONDS)
            if isinstance(check_payload, list):
                checks.extend({"name": str(item.get("name") or ""), "conclusion": str(item.get("conclusion") or ""), "sha": sha, "pr_number": pr.get("number")} for item in check_payload[:50])
        result = {
            "status": "ok", "repository": repository, "fetched_at": generated_at,
            "source": "gh", "immutable_reference": str(project.get("git_sha") or ""),
            "pull_requests": [{key: item.get(key) for key in ("number", "title", "isDraft", "reviewDecision", "mergeStateStatus", "headRefOid", "updatedAt")} for item in prs[:50]],
            "checks": checks[:100], "open_issue_count": len(issues) if isinstance(issues, list) else 0,
            "attention_issues": [{key: item.get(key) for key in ("number", "title", "updatedAt", "labels")} for item in issues[:10]],
            "latest_release": ({"tag": releases[0].get("tagName"), "published_at": releases[0].get("publishedAt")} if isinstance(releases, list) and releases else None),
        }
    except _GhError as exc:
        result = {"status": exc.kind, "repository": repository, "fetched_at": generated_at, "remediation": exc.remediation}
    return _write_result(project_id, result)


class _GhError(RuntimeError):
    def __init__(self, kind: str, remediation: str) -> None:
        self.kind, self.remediation = kind, remediation


def _gh_json(arguments: list[str], *, timeout: int) -> Any:
    try:
        result = subprocess.run(["gh", *arguments], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise _GhError("unknown", "Install gh CLI and authenticate with gh auth login.") from exc
    except subprocess.TimeoutExpired as exc:
        raise _GhError("timeout", "GitHub request timed out; retry refresh.") from exc
    if result.returncode != 0:
        text = result.stderr.lower()
        if "rate limit" in text:
            raise _GhError("rate_limited", "GitHub rate limit reached; retry later.")
        if "auth" in text or "login" in text:
            raise _GhError("unknown", "Authenticate gh with gh auth login.")
        raise _GhError("unknown", "GitHub repository was denied or returned an invalid response.")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise _GhError("unknown", "GitHub returned malformed evidence.") from exc


def _repository(links: Any, root: Path) -> str:
    if isinstance(links, dict) and links.get("github"):
        return _normalize_repository(str(links["github"]))
    try:
        result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, capture_output=True, text=True, timeout=3)
        return _normalize_repository(result.stdout.strip()) if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _normalize_repository(value: str) -> str:
    value = value.strip().removesuffix(".git")
    value = re.sub(r"^https?://github\.com/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^git@github\.com:", "", value, flags=re.IGNORECASE)
    return value.strip("/") if re.fullmatch(r"[^/\s]+/[^/\s]+", value.strip("/")) else ""


def _write_result(project_id: str, result: dict[str, Any]) -> dict[str, Any]:
    path = github_cache_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)
    return {"project_id": project_id, **result}
