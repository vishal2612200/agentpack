"""Immutable, evidence-backed PR context shared by review entry points."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agentpack.analysis.tests import find_related_tests
from agentpack.architecture.models import ArchitectureCheckResult, ArchitectureDiff
from agentpack.architecture.service import build_diff, build_snapshot_for_ref, run_check
from agentpack.learning.graph_memory import retrieve_memory_chain


class PRChangedFile(BaseModel):
    path: str
    related_tests: list[str] = Field(default_factory=list)


class PRContext(BaseModel):
    """Stable PR inputs plus derived architecture evidence for one review run."""

    source: Literal["github", "local-fallback"]
    context_status: Literal["complete", "degraded", "unavailable"] = "complete"
    pr_number: int | None = None
    pr_url: str = ""
    focus: str = ""
    base_sha: str
    head_sha: str
    changed_files: list[PRChangedFile]
    architecture_diff: ArchitectureDiff
    invariant_results: ArchitectureCheckResult
    affected_entity_keys: list[str] = Field(default_factory=list)
    affected_edge_keys: list[str] = Field(default_factory=list)
    relevant_tests: list[str] = Field(default_factory=list)
    context_references: list[str] = Field(default_factory=list)
    memory_retrieval_chain: dict[str, object] = Field(default_factory=dict)
    ci_artifact: dict[str, object] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PRContextError(ValueError):
    pass


def resolve_pr_context(
    root: Path,
    *,
    pr: str | int | None = None,
    focus: str = "",
    allow_local_fallback: bool = False,
) -> PRContext:
    """Resolve a GitHub PR to immutable local commits, or use explicit local fallback."""
    if pr in (None, "") and not allow_local_fallback:
        raise PRContextError("An explicit PR number or URL is required; pass allow_local_fallback=True for local commits.")
    metadata = _github_pr_metadata(root, pr)
    if metadata is None:
        if not allow_local_fallback:
            raise PRContextError("Could not resolve a GitHub PR; pass allow_local_fallback=True for local commits.")
        return _local_fallback_context(root, focus=focus)

    number = int(metadata["number"])
    base_name = str(metadata["baseRefName"])
    base_sha = str(metadata["baseRefOid"])
    head_sha = str(metadata["headRefOid"])
    head_ref = f"refs/remotes/origin/agentpack-pr-{number}"
    base_ref = f"refs/remotes/origin/agentpack-base-{number}"
    _fetch_pr_refs(root, number=number, base_name=base_name, head_ref=head_ref, base_ref=base_ref)
    _verify_ref(root, base_ref, base_sha, label="PR base")
    _verify_ref(root, head_ref, head_sha, label="PR head")
    return build_pr_context(
        root,
        base_ref=base_sha,
        head_ref=head_sha,
        source="github",
        pr_number=number,
        pr_url=str(metadata.get("url") or ""),
        focus=focus,
    )


def build_pr_context(
    root: Path,
    *,
    base_ref: str,
    head_ref: str,
    source: Literal["github", "local-fallback"],
    pr_number: int | None = None,
    pr_url: str = "",
    focus: str = "",
) -> PRContext:
    """Build the deterministic evidence payload consumed by review, MCP, and CI."""
    base_sha = _resolve_commit(root, base_ref)
    head_sha = _resolve_commit(root, head_ref)
    changed_paths = _changed_paths(root, base_sha, head_sha)
    all_paths = _tracked_paths(root, head_sha)
    changed_files = [
        PRChangedFile(path=path, related_tests=find_related_tests(path, all_paths))
        for path in changed_paths
    ]
    architecture_diff = build_diff(root, base_sha, head_sha)
    invariant_results = run_check(root, base_sha, head_sha)
    affected_entity_keys = sorted(
        {
            *(entity.entity_key for entity in architecture_diff.added_entities),
            *(entity.entity_key for entity in architecture_diff.removed_entities),
            *(change.entity_key for change in architecture_diff.changed_entities),
        }
    )
    affected_edge_keys = sorted(
        {
            *(edge.edge_key for edge in architecture_diff.added_edges),
            *(edge.edge_key for edge in architecture_diff.removed_edges),
            *(change.edge_key for change in architecture_diff.changed_edges),
        }
    )
    relevant_tests = sorted({test for item in changed_files for test in item.related_tests})
    base_snapshot = build_snapshot_for_ref(root, base_sha)
    head_snapshot = build_snapshot_for_ref(root, head_sha)
    base_entities = {entity.entity_key: entity for entity in base_snapshot.entities}
    head_entities = {entity.entity_key: entity for entity in head_snapshot.entities}
    entity_node_keys = {
        entity.entity_key: str(entity.metadata.get("node_key") or "")
        for entity in head_snapshot.entities
        if entity.metadata.get("node_key")
    }
    node_aliases: dict[str, dict[str, str]] = {}
    for alias in architecture_diff.aliased_entities:
        if alias.status != "confirmed" or alias.confidence < 0.9:
            continue
        before = base_entities.get(alias.before_entity_key)
        after = head_entities.get(alias.after_entity_key)
        before_node = str(before.metadata.get("node_key") or "") if before else ""
        after_node = str(after.metadata.get("node_key") or "") if after else ""
        if not before_node or not after_node:
            continue
        entity_node_keys[alias.before_entity_key] = before_node
        node_aliases[before_node] = {
            "node_key": after_node,
            "path": after.locator.path,
        }
    memory_retrieval_chain = retrieve_memory_chain(
        root,
        task=focus,
        live_paths=[*(item.path for item in changed_files), *relevant_tests],
        live_entity_keys=affected_entity_keys,
        architecture_edges=head_snapshot.edges,
        entity_node_keys=entity_node_keys,
        node_aliases=node_aliases,
        current_source_hashes=head_snapshot.file_hashes,
    )
    ci_artifact: dict[str, object] = {}
    artifact_warnings: list[str] = []
    try:
        from agentpack.architecture.ci import load_verified_ci_artifact

        loaded_artifact = load_verified_ci_artifact(root / ".agentpack" / "artifacts", head_sha=head_sha)
        if loaded_artifact is not None:
            ci_artifact = loaded_artifact
        elif (root / ".agentpack" / "artifacts" / "architecture-receipt.json").exists():
            artifact_warnings.append("local architecture artifact ignored: receipt does not match PR head")
    except (OSError, ValueError):
        artifact_warnings.append("local architecture artifact unavailable; rebuilt from immutable refs")
    warnings = list(invariant_results.warnings)
    warnings.extend(artifact_warnings)
    unavailable = sorted(
        language
        for language, tier in _snapshot_capabilities(root, head_sha).items()
        if tier == "unavailable"
    )
    if unavailable:
        warnings.append("parser capability unavailable: " + ", ".join(unavailable))
    context_references = [
        "architecture_diff",
        "architecture_invariant_results",
        "changed_files",
        "relevant_tests",
        "memory_retrieval_chain",
    ]
    if ci_artifact:
        context_references.append("verified_ci_artifact")
    return PRContext(
        source=source,
        pr_number=pr_number,
        pr_url=pr_url,
        focus=" ".join(focus.split()),
        base_sha=base_sha,
        head_sha=head_sha,
        changed_files=changed_files,
        architecture_diff=architecture_diff,
        invariant_results=invariant_results,
        affected_entity_keys=affected_entity_keys,
        affected_edge_keys=affected_edge_keys,
        relevant_tests=relevant_tests,
        context_references=context_references,
        memory_retrieval_chain=memory_retrieval_chain,
        ci_artifact=ci_artifact,
        warnings=warnings,
    )


def _local_fallback_context(root: Path, *, focus: str) -> PRContext:
    head_sha = _resolve_commit(root, "HEAD")
    base_ref = _local_base_ref(root)
    base_sha = _git(root, ["git", "merge-base", "HEAD", base_ref])
    if not base_sha:
        raise PRContextError(f"Could not find a merge-base between HEAD and local base {base_ref}.")
    return build_pr_context(
        root,
        base_ref=base_sha,
        head_ref=head_sha,
        source="local-fallback",
        focus=focus,
    )


def _github_pr_metadata(root: Path, pr: str | int | None) -> dict[str, object] | None:
    if pr in (None, ""):
        return None
    args = [
        "gh",
        "pr",
        "view",
        "--json",
        "number,url,baseRefName,baseRefOid,headRefName,headRefOid",
    ]
    if pr not in (None, ""):
        args.insert(3, str(pr))
    output = _git(root, args)
    if not output:
        return None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    required = ("number", "baseRefName", "baseRefOid", "headRefOid")
    return payload if isinstance(payload, dict) and all(payload.get(key) for key in required) else None


def _local_base_ref(root: Path) -> str:
    for candidate in ("refs/remotes/origin/main", "refs/remotes/origin/master", "main", "master"):
        if _git(root, ["git", "rev-parse", "--verify", f"{candidate}^{{commit}}"]):
            branch = _git(root, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
            if candidate.rsplit("/", 1)[-1] == (branch or "").strip() and candidate in {"main", "master"}:
                parent = _git(root, ["git", "rev-parse", "--verify", "HEAD^"])
                if parent:
                    return parent
            return candidate
    raise PRContextError(
        "Local fallback requires an explicit PR target or a local main/master ref; "
        "HEAD~1 is not a trusted review base."
    )


def _fetch_pr_refs(root: Path, *, number: int, base_name: str, head_ref: str, base_ref: str) -> None:
    result = subprocess.run(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            f"+refs/pull/{number}/head:{head_ref}",
            f"+refs/heads/{base_name}:{base_ref}",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PRContextError(f"Could not fetch PR #{number} refs: {detail or 'git fetch failed'}")


def _verify_ref(root: Path, ref: str, expected_sha: str, *, label: str) -> None:
    actual = _resolve_commit(root, ref)
    if actual != expected_sha:
        raise PRContextError(f"Untrusted {label}: fetched {actual}, GitHub reported {expected_sha}.")


def _resolve_commit(root: Path, ref: str) -> str:
    sha = _git(root, ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"])
    if not sha:
        raise PRContextError(f"Could not resolve immutable commit for {ref}.")
    return sha


def _changed_paths(root: Path, base_sha: str, head_sha: str) -> list[str]:
    output = _git(root, ["git", "diff", "--name-only", base_sha, head_sha])
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def _tracked_paths(root: Path, ref: str) -> set[str]:
    output = _git(root, ["git", "ls-tree", "-r", "--name-only", ref])
    return {line.strip() for line in output.splitlines() if line.strip()}


def _snapshot_capabilities(root: Path, ref: str) -> dict[str, str]:
    from agentpack.architecture.service import build_snapshot_for_ref

    return build_snapshot_for_ref(root, ref).capabilities


def _git(root: Path, args: list[str]) -> str:
    result = subprocess.run(args, cwd=root, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""
