from __future__ import annotations

import importlib.resources
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import typer

from agentpack.analysis.tests import find_related_tests
from agentpack.application.pr_context import build_pr_context
from agentpack.application.pack_service import PackRequest, PackService
from agentpack.commands._shared import _atomic_write, _now_iso, _root, console
from agentpack.core import git as git_core
from agentpack.core.citations import (
    CitationValidation,
    extract_location_citations,
    parse_location,
    semantic_support_command_judge,
    validate_citations,
    validate_claim_support,
)
from agentpack.core.git_preflight import GitPreflight, run_git_preflight
from agentpack.core.models import Citation
from agentpack.core.toon_parser import ToonParseError, load_toon
from agentpack.core.toon_validator import canonicalize_to_toon_text, validate_toon_payload_schema
from agentpack.observer.brief import write_observer_brief
from agentpack.observer.events import record_review_observation
from agentpack.observer.priors import observer_notes_for_task

_PREFLIGHT_PATH = Path(".agentpack/review-preflight.json")
_RUNBOOK_PATH = Path(".agentpack/review.prompt.md")
_UNDERSTANDING_PROMPT_PATH = Path(".agentpack/review-understanding.prompt.md")
_JUDGE_PROMPT_PATH = Path(".agentpack/review-judge.prompt.md")
_CRITIC_PROMPT_PATH = Path(".agentpack/review-critic.prompt.md")
_UNDERSTANDING_TEMPLATE_PATH = Path(".agentpack/review-understanding.template.toon")
_FINDINGS_TEMPLATE_PATH = Path(".agentpack/review-findings.template.toon")
_CRITIQUE_TEMPLATE_PATH = Path(".agentpack/review-critique.template.toon")
_APPROVED_FINDINGS_PATH = Path(".agentpack/review-approved-findings.toon")
_STATE_PATH = Path(".agentpack/review-state.json")
_REVIEW_RUNS_DIR = Path(".agentpack/reviews")
_PR_URL_RE = re.compile(r"https?://\S+/pull/(?P<number>\d+)\b", re.IGNORECASE)
_PR_CONTEXT_RE = re.compile(
    r"(?:\b(?:pr|pull request)\s*#?\s*(?P<number>\d+)\b|\bgh\s+pr\s+(?:view|diff|checkout)\s+(?P<gh_number>\d+)\b)",
    re.IGNORECASE,
)
_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")
_DIFF_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_AGENTPACK_REVIEW_BADGE = (
    "[![AgentPack review]"
    "(https://raw.githubusercontent.com/vishal2612200/agentpack/main/docs/assets/agentpack-review-badge.png)]"
    "(https://github.com/vishal2612200/agentpack)"
)
_GITHUB_REVIEW_BODY_SAFE_LIMIT = 60_000


class _ReviewPreflightError(Exception):
    pass


@dataclass(frozen=True)
class _ReviewArtifactPaths:
    authoring: Path
    canonical: Path
    authoring_rel: str
    canonical_rel: str

    @property
    def source(self) -> Path | None:
        if self.authoring.exists():
            return self.authoring
        if self.canonical.exists():
            return self.canonical
        return None


def register(app: typer.Typer) -> None:
    @app.command("review")
    def review(
        review_context: str = typer.Argument("", help="Optional reviewer or developer context for this PR review."),
        resume: str = typer.Option("", "--resume", help="Resume a previous review run by run id."),
        list_runs: bool = typer.Option(False, "--list", help="List recent review runs for the current branch."),
        pr_target: str = typer.Option("", "--pr", help="PR number or URL to review. Binds diff/context to that PR."),
        allow_local_fallback: bool = typer.Option(
            False,
            "--allow-local-fallback",
            help="Allow explicit local diff fallback when GitHub PR metadata or fetch is unavailable.",
        ),
        base_ref: str = typer.Option(
            "",
            "--base",
            help="Explicit local review base ref used only with --allow-local-fallback.",
        ),
        check: bool = typer.Option(False, "--check", help="Validate active review stage artifacts and print the next gate."),
        post_inline_comments: bool = typer.Option(
            False,
            "--post-inline-comments",
            "--post",
            help="After Anchor, Judge, and Critic validate, post approved findings as inline GitHub PR review comments.",
        ),
        dry_run_post: bool = typer.Option(
            False,
            "--dry-run-post",
            help="Validate and write the inline GitHub review payload without calling GitHub.",
        ),
        dry_run_check: bool = typer.Option(
            False,
            "--dry-run-check",
            help="Alias for --dry-run-post: validate artifacts, citations, and commentability without posting.",
        ),
        strict: bool = typer.Option(False, "--strict", help="Force the full strict review scaffold even for small PRs."),
        light: bool = typer.Option(False, "--light", help="Force the lighter small-PR review scaffold."),
    ) -> None:
        """Prepare the Anchor, Judge, Critic, Actor PR review bundle for the current branch or PR."""
        root = _root()
        if not git_core.is_git_repo(root):
            console.print("[red]agentpack review requires a git repository.[/]")
            raise typer.Exit(1)

        if list_runs:
            _list_review_runs(root)
            return
        if check or post_inline_comments or dry_run_post or dry_run_check:
            _check_active_review(root, post_inline_comments=post_inline_comments, dry_run_post=dry_run_post or dry_run_check)
            return
        if strict and light:
            console.print("[red]Use only one of --strict or --light.[/]")
            raise typer.Exit(1)

        git_preflight = run_git_preflight(root, allow_ff_pull=False)
        _print_git_preflight(git_preflight)
        if resume.strip():
            preflight = _load_review_run(root, resume.strip())
            outputs = _review_output_paths(
                root,
                branch_prefix=preflight["review"]["branch_prefix"],
                run_id=preflight["review"]["run_id"],
            )
        else:
            target, cleaned_context = _parse_review_target(pr_target.strip(), review_context.strip())
            outputs = _review_output_paths(root, branch_prefix=_target_branch_prefix(target))
            console.print(f"[green]✓[/] Review run id: [bold]{outputs['run_id']}[/]")
            console.print(f"[green]✓[/] Review run dir: [bold]{_rel_to_root(outputs['run_dir'], root)}[/]")
            try:
                preflight = _build_review_preflight(
                    root,
                    cleaned_context,
                    outputs,
                    target=target,
                    allow_local_fallback=allow_local_fallback,
                    base_ref=base_ref.strip(),
                    review_mode_override="strict" if strict else "light" if light else "",
                    git_preflight=git_preflight,
                )
            except _ReviewPreflightError as exc:
                console.print(f"[red]Review preflight blocked:[/] {exc}")
                raise typer.Exit(1) from exc

        _ensure_review_pipeline_contract(preflight, outputs, root)
        runbook = _render_review_runbook(preflight)
        understanding_prompt = _render_stage_prompt(
            "stage1-understanding.md",
            preflight,
            output_path=outputs["understanding_authoring"],
            template_path=preflight["paths"]["understanding_template"],
            prior_paths=[],
        )
        judge_prompt = _render_stage_prompt(
            "stage2-judge.md",
            preflight,
            output_path=outputs["findings_authoring"],
            template_path=preflight["paths"]["findings_template"],
            prior_paths=[outputs["understanding"]],
        )
        critic_prompt = _render_stage_prompt(
            "stage3-critic.md",
            preflight,
            output_path=outputs["critique_authoring"],
            template_path=preflight["paths"]["critique_template"],
            prior_paths=[outputs["understanding"], outputs["findings"]],
        )

        artifacts = {
            outputs["preflight"]: json.dumps(preflight, indent=2) + "\n",
            outputs["runbook"]: runbook,
            outputs["understanding_prompt"]: understanding_prompt,
            outputs["judge_prompt"]: judge_prompt,
            outputs["critic_prompt"]: critic_prompt,
            outputs["understanding_template"]: _review_toon_template("understanding"),
            outputs["findings_template"]: _review_toon_template("findings"),
            outputs["critique_template"]: _review_toon_template("critique"),
            outputs["state"]: json.dumps(_review_state(root, preflight), indent=2) + "\n",
            _PREFLIGHT_PATH: json.dumps(preflight, indent=2) + "\n",
            _RUNBOOK_PATH: runbook,
            _UNDERSTANDING_PROMPT_PATH: understanding_prompt,
            _JUDGE_PROMPT_PATH: judge_prompt,
            _CRITIC_PROMPT_PATH: critic_prompt,
            _UNDERSTANDING_TEMPLATE_PATH: _review_toon_template("understanding"),
            _FINDINGS_TEMPLATE_PATH: _review_toon_template("findings"),
            _CRITIQUE_TEMPLATE_PATH: _review_toon_template("critique"),
            _STATE_PATH: json.dumps(_review_state(root, preflight), indent=2) + "\n",
        }
        for rel_path, content in artifacts.items():
            abs_path = root / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(abs_path, content)
        try:
            changed_file_paths = [
                str(item.get("path") or "")
                for item in preflight.get("changed_files", [])
                if isinstance(item, dict) and item.get("path")
            ]
            record_review_observation(
                root,
                task=str(preflight.get("review_context") or "review"),
                status="preflight",
                changed_files=changed_file_paths,
            )
            write_observer_brief(root, task=str(preflight.get("review_context") or "review"))
        except Exception:
            pass

        console.print(f"[green]✓[/] Review run id: [bold]{preflight['review']['run_id']}[/]")
        console.print(f"[green]✓[/] Review run dir: [bold]{preflight['paths']['run_dir']}[/]")
        console.print(f"[green]✓[/] Review preflight: [bold]{_PREFLIGHT_PATH}[/]")
        console.print(f"[green]✓[/] Review runbook: [bold]{_RUNBOOK_PATH}[/]")
        console.print(f"[green]✓[/] Anchor prompt: [bold]{_UNDERSTANDING_PROMPT_PATH}[/]")
        console.print(f"[green]✓[/] Judge prompt: [bold]{_JUDGE_PROMPT_PATH}[/]")
        console.print(f"[green]✓[/] Critic prompt: [bold]{_CRITIC_PROMPT_PATH}[/]")
        console.print(f"[green]✓[/] Anchor TOON template: [bold]{_UNDERSTANDING_TEMPLATE_PATH}[/]")
        console.print(f"[green]✓[/] Judge TOON template: [bold]{_FINDINGS_TEMPLATE_PATH}[/]")
        console.print(f"[green]✓[/] Critic TOON template: [bold]{_CRITIQUE_TEMPLATE_PATH}[/]")
        console.print(f"[green]✓[/] Anchor JSON target: [bold]{_rel_to_root(outputs['understanding_authoring'], root)}[/]")
        console.print(f"[green]✓[/] Anchor canonical TOON: [bold]{_rel_to_root(outputs['understanding'], root)}[/]")
        console.print(f"[green]✓[/] Judge JSON target: [bold]{_rel_to_root(outputs['findings_authoring'], root)}[/]")
        console.print(f"[green]✓[/] Judge canonical TOON: [bold]{_rel_to_root(outputs['findings'], root)}[/]")
        console.print(f"[green]✓[/] Critic JSON target: [bold]{_rel_to_root(outputs['critique_authoring'], root)}[/]")
        console.print(f"[green]✓[/] Critic canonical TOON: [bold]{_rel_to_root(outputs['critique'], root)}[/]")
        console.print(f"[green]✓[/] Actor approved findings: [bold]{_rel_to_root(outputs['approved_findings'], root)}[/]")
        console.print(f"[green]✓[/] Review stage state: [bold]{_STATE_PATH}[/]")
        if preflight["warnings"]:
            console.print("[yellow]Warnings:[/]")
            for warning in preflight["warnings"]:
                console.print(f"  - {warning}")
        console.print("Use the runbook from your agent host; run `agentpack review --check` after each stage before continuing.")


def _build_review_preflight(
    root: Path,
    review_context: str,
    outputs: dict[str, Any],
    *,
    target: dict[str, Any] | None = None,
    allow_local_fallback: bool = False,
    base_ref: str = "",
    review_mode_override: str = "",
    git_preflight: GitPreflight | None = None,
) -> dict[str, Any]:
    branch = outputs["branch"]
    pr = _gh_pr_metadata(root, target)
    all_paths = _repo_paths(root)
    diff_info = _diff_base(root, pr, target=target, allow_local_fallback=allow_local_fallback, base_ref=base_ref)
    sha = diff_info.get("head_sha") or git_core.current_sha(root) or ""
    changed_paths = _changed_paths(root, diff_info["range"])
    changed_files = [
        {
            "path": path,
            "head_blob_sha": _blob_sha(root, diff_info.get("head_ref") or "HEAD", path),
            "related_tests": find_related_tests(path, all_paths),
        }
        for path in changed_paths
    ]
    warnings, info = _warnings(root, pr, diff_info, changed_paths)
    context_pack = _build_review_context_pack(root, review_context, diff_info, outputs, warnings)
    review_target = _preflight_target(target, pr)
    observer_notes = observer_notes_for_task(root, review_context)
    review_mode = _review_scaffold_mode(changed_files, review_context, override=review_mode_override)
    try:
        shared_pr_context = build_pr_context(
            root,
            base_ref=str(diff_info["base_ref"]),
            head_ref=str(diff_info.get("head_ref") or "HEAD"),
            source="github" if diff_info["source"] in {"pr-target", "current-pr"} else "local-fallback",
            pr_number=int(pr["number"]) if isinstance(pr, dict) and pr.get("number") else None,
            pr_url=str(pr.get("url") or "") if isinstance(pr, dict) else "",
            focus=review_context,
        )
        shared_pr_context_payload = shared_pr_context.model_dump(mode="json")
    except Exception as exc:
        # Preserve direct source review, but make missing architecture evidence
        # explicit so agents cannot accidentally present unsupported claims.
        degraded = f"architecture review context degraded: {type(exc).__name__}: {str(exc)[:240]}"
        warnings.append(degraded)
        shared_pr_context_payload = {
            "context_status": "degraded",
            "warnings": [degraded],
            "architecture_claims_allowed": False,
        }

    return {
        "generated_at": _now_iso(),
        "review_context": review_context,
        "review": {
            "mode": "fresh",
            "scaffold": review_mode,
            "run_id": outputs["run_id"],
            "branch": branch,
            "branch_prefix": outputs["branch_prefix"],
            "target": review_target,
        },
        "execution_contract": {
            "structured_format": "JSON authoring, canonical TOON handoff",
            "canonical_format": "TOON",
            "requires_write_to_file": True,
            "requires_read_file_between_stages": True,
            "forbid_inline_review": True,
            "blocked_without_stage_artifact": True,
            "stage_order": ["anchor", "judge", "critic", "actor"],
        },
        "git": {
            "branch": branch,
            "branch_prefix": outputs["branch_prefix"],
            "head_sha": sha,
            "head_ref": diff_info.get("head_ref", ""),
            "base_sha": _rev_parse(root, diff_info.get("base_ref") or "") if diff_info.get("base_ref") else "",
            "base_ref": diff_info.get("base_ref", ""),
            "dirty_files": sorted(git_core.dirty_files(root)),
            "preflight": (git_preflight or run_git_preflight(root, allow_ff_pull=False)).as_dict(),
        },
        "citation_source": {
            "mode": "git-head" if diff_info["source"] in {"pr-target", "current-pr"} and sha else "working-tree",
            "head_sha": sha,
            "head_ref": diff_info.get("head_ref", ""),
            "fallback": "working-tree" if diff_info["source"] == "local-fallback" else "",
        },
        "pr": pr,
        "pr_context": shared_pr_context_payload,
        "diff": {
            "range": diff_info["range"],
            "base_ref": diff_info["base_ref"],
            "head_ref": diff_info.get("head_ref", ""),
            "source": diff_info["source"],
            "changed_files_count": len(changed_files),
        },
        "paths": {
            "run_dir": _rel_to_root(outputs["run_dir"], root),
            "preflight": _rel_to_root(outputs["preflight"], root),
            "runbook": _rel_to_root(outputs["runbook"], root),
            "understanding_prompt": _rel_to_root(outputs["understanding_prompt"], root),
            "judge_prompt": _rel_to_root(outputs["judge_prompt"], root),
            "critic_prompt": _rel_to_root(outputs["critic_prompt"], root),
            "understanding_template": _rel_to_root(outputs["understanding_template"], root),
            "findings_template": _rel_to_root(outputs["findings_template"], root),
            "critique_template": _rel_to_root(outputs["critique_template"], root),
            "understanding_authoring_output": _rel_to_root(outputs["understanding_authoring"], root),
            "understanding_canonical_output": _rel_to_root(outputs["understanding"], root),
            "findings_authoring_output": _rel_to_root(outputs["findings_authoring"], root),
            "findings_canonical_output": _rel_to_root(outputs["findings"], root),
            "critique_authoring_output": _rel_to_root(outputs["critique_authoring"], root),
            "critique_canonical_output": _rel_to_root(outputs["critique"], root),
            "understanding_output": _rel_to_root(outputs["understanding"], root),
            "findings_output": _rel_to_root(outputs["findings"], root),
            "critique_output": _rel_to_root(outputs["critique"], root),
            "approved_findings_output": _rel_to_root(outputs["approved_findings"], root),
            "state": _rel_to_root(outputs["state"], root),
            "active_preflight": str(_PREFLIGHT_PATH),
            "active_runbook": str(_RUNBOOK_PATH),
            "active_understanding_prompt": str(_UNDERSTANDING_PROMPT_PATH),
            "active_judge_prompt": str(_JUDGE_PROMPT_PATH),
            "active_critic_prompt": str(_CRITIC_PROMPT_PATH),
            "active_understanding_template": str(_UNDERSTANDING_TEMPLATE_PATH),
            "active_findings_template": str(_FINDINGS_TEMPLATE_PATH),
            "active_critique_template": str(_CRITIQUE_TEMPLATE_PATH),
            "active_approved_findings": str(_APPROVED_FINDINGS_PATH),
            "active_state": str(_STATE_PATH),
        },
        "context_pack": context_pack,
        "observer": {
            "advisory": True,
            "brief": ".agentpack/observer-brief.md",
            "notes": observer_notes,
        },
        "changed_files": changed_files,
        "warnings": warnings,
        "info": info,
    }


def _review_output_paths(
    root: Path,
    *,
    branch_prefix: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    branch = git_core.current_branch(root) or "HEAD"
    branch_prefix = branch_prefix or branch.replace("/", "-")
    run_id = run_id or _new_review_run_id()
    run_dir = root / _REVIEW_RUNS_DIR / branch_prefix / run_id
    return {
        "branch": branch,
        "branch_prefix": branch_prefix,
        "run_id": run_id,
        "run_dir": run_dir,
        "preflight": run_dir / "preflight.json",
        "runbook": run_dir / "runbook.md",
        "understanding_prompt": run_dir / "understanding.prompt.md",
        "judge_prompt": run_dir / "judge.prompt.md",
        "critic_prompt": run_dir / "critic.prompt.md",
        "understanding_template": run_dir / "understanding.template.toon",
        "findings_template": run_dir / "findings.template.toon",
        "critique_template": run_dir / "critique.template.toon",
        "understanding_authoring": run_dir / "understanding.json",
        "understanding": run_dir / "understanding.toon",
        "findings_authoring": run_dir / "findings.json",
        "findings": run_dir / "findings.toon",
        "critique_authoring": run_dir / "critique.json",
        "critique": run_dir / "critique.toon",
        "approved_findings": run_dir / "approved-findings.toon",
        "state": run_dir / "state.json",
    }


def _ensure_review_pipeline_contract(preflight: dict[str, Any], outputs: dict[str, Any], root: Path) -> None:
    """Add Critic and Actor paths when resuming a pre-Critic review run."""
    paths = preflight.setdefault("paths", {})
    path_defaults = {
        "critic_prompt": outputs["critic_prompt"],
        "critique_template": outputs["critique_template"],
        "critique_authoring_output": outputs["critique_authoring"],
        "critique_canonical_output": outputs["critique"],
        "critique_output": outputs["critique"],
        "approved_findings_output": outputs["approved_findings"],
    }
    for key, path in path_defaults.items():
        paths.setdefault(key, _rel_to_root(path, root))
    paths.setdefault("active_critic_prompt", str(_CRITIC_PROMPT_PATH))
    paths.setdefault("active_critique_template", str(_CRITIQUE_TEMPLATE_PATH))
    paths.setdefault("active_approved_findings", str(_APPROVED_FINDINGS_PATH))
    execution_contract = preflight.setdefault("execution_contract", {})
    execution_contract["stage_order"] = ["anchor", "judge", "critic", "actor"]


def _new_review_run_id() -> str:
    return f"{_now_iso().replace(':', '').replace('-', '').replace('.', '')}-{uuid4().hex[:8]}"


def _parse_review_target(raw_pr: str, review_context: str) -> tuple[dict[str, Any] | None, str]:
    if raw_pr:
        return _target_from_raw(raw_pr, source="option"), review_context
    url_match = _PR_URL_RE.search(review_context)
    if url_match:
        target = _target_from_raw(url_match.group(0), source="argument")
        cleaned = _clean_review_context(review_context[:url_match.start()] + review_context[url_match.end():])
        return target, cleaned
    match = _PR_CONTEXT_RE.search(review_context)
    if not match:
        return None, review_context
    number = match.group("number") or match.group("gh_number") or ""
    target = _target_from_raw(number, source="argument")
    cleaned = _clean_review_context(review_context[:match.start()] + review_context[match.end():])
    return target, cleaned


def _target_from_raw(raw: str, *, source: str) -> dict[str, Any]:
    value = raw.strip()
    url_match = _PR_URL_RE.search(value)
    number = url_match.group("number") if url_match else value.lstrip("#")
    if not number.isdigit():
        number_match = re.search(r"\b(\d+)\b", value)
        number = number_match.group(1) if number_match else ""
    return {
        "raw": value,
        "number": int(number) if number.isdigit() else None,
        "url": url_match.group(0) if url_match else "",
        "source": source,
    }


def _target_branch_prefix(target: dict[str, Any] | None) -> str | None:
    if not target or not target.get("number"):
        return None
    return f"pr-{target['number']}"


def _target_cli_arg(target: dict[str, Any] | None) -> str | None:
    if not target:
        return None
    if target.get("url"):
        return str(target["url"])
    if target.get("number"):
        return str(target["number"])
    raw = str(target.get("raw") or "").strip()
    return raw or None


def _artifact_paths(preflight: dict[str, Any], kind: str, *, root: Path) -> _ReviewArtifactPaths:
    paths = preflight.get("paths") if isinstance(preflight.get("paths"), dict) else {}
    if kind == "understanding":
        authoring_rel = str(paths.get("understanding_authoring_output") or paths.get("understanding_output") or "")
        canonical_rel = str(paths.get("understanding_canonical_output") or paths.get("understanding_output") or "")
    elif kind == "findings":
        authoring_rel = str(paths.get("findings_authoring_output") or paths.get("findings_output") or "")
        canonical_rel = str(paths.get("findings_canonical_output") or paths.get("findings_output") or "")
    elif kind == "critique":
        authoring_rel = str(paths.get("critique_authoring_output") or paths.get("critique_output") or "")
        canonical_rel = str(paths.get("critique_canonical_output") or paths.get("critique_output") or "")
        if not authoring_rel or not canonical_rel:
            run_dir = str(paths.get("run_dir") or "")
            if run_dir:
                authoring_rel = f"{run_dir.rstrip('/')}/critique.json"
                canonical_rel = f"{run_dir.rstrip('/')}/critique.toon"
    else:
        raise ValueError(f"unknown review artifact kind: {kind}")
    if not authoring_rel or not canonical_rel:
        raise ValueError(f"active review preflight is missing {kind} artifact paths")
    authoring_path = Path(authoring_rel)
    canonical_path = Path(canonical_rel)
    return _ReviewArtifactPaths(
        authoring=authoring_path if authoring_path.is_absolute() else root / authoring_path,
        canonical=canonical_path if canonical_path.is_absolute() else root / canonical_path,
        authoring_rel=authoring_rel,
        canonical_rel=canonical_rel,
    )


def _clean_review_context(value: str) -> str:
    return " ".join(value.replace("  ", " ").strip(" -:\t").split())


def _preflight_target(target: dict[str, Any] | None, pr: dict[str, Any] | None) -> dict[str, Any]:
    if target:
        return {
            "raw": target.get("raw", ""),
            "number": target.get("number") or (pr or {}).get("number"),
            "url": target.get("url") or (pr or {}).get("url", ""),
            "source": target.get("source", ""),
        }
    if pr:
        return {
            "raw": "",
            "number": pr.get("number"),
            "url": pr.get("url", ""),
            "source": "current-branch",
        }
    return {"raw": "", "number": None, "url": "", "source": "local-fallback"}


def _load_review_run(root: Path, run_id: str) -> dict[str, Any]:
    branch = git_core.current_branch(root) or "HEAD"
    branch_prefix = branch.replace("/", "-")
    if run_id == "latest":
        latest = _latest_review_run(root, branch_prefix)
        if latest is None:
            console.print(f"[red]Review run not found:[/] latest for {branch_prefix}")
            raise typer.Exit(1)
        preflight_path = latest
    else:
        preflight_path = root / _REVIEW_RUNS_DIR / branch_prefix / run_id / "preflight.json"
    if not preflight_path.exists():
        matches = sorted((root / _REVIEW_RUNS_DIR).glob(f"*/{run_id}/preflight.json"))
        if len(matches) == 1:
            preflight_path = matches[0]
        else:
            console.print(f"[red]Review run not found:[/] {preflight_path}")
            raise typer.Exit(1)
    try:
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        console.print(f"[red]Review run preflight is invalid JSON:[/] {preflight_path}")
        raise typer.Exit(1)
    understanding_paths = _artifact_paths(preflight, "understanding", root=root)
    findings_paths = _artifact_paths(preflight, "findings", root=root)
    critique_paths = _artifact_paths(preflight, "critique", root=root)
    try:
        if understanding_paths.source is not None:
            _validate_and_canonicalize_review_artifact(understanding_paths, kind="understanding", preflight=preflight)
        if findings_paths.source is not None:
            _validate_and_canonicalize_review_artifact(findings_paths, kind="findings", preflight=preflight)
        if critique_paths.source is not None:
            _validate_and_canonicalize_review_artifact(critique_paths, kind="critique", preflight=preflight)
    except ValueError as exc:
        console.print(f"[red]Review run artifact invalid:[/] {exc}")
        raise typer.Exit(1)
    preflight.setdefault("review", {})
    preflight["review"]["mode"] = "resume"
    return preflight


def _latest_review_run(root: Path, branch_prefix: str) -> Path | None:
    records = _review_run_records(root, branch_prefix=branch_prefix)
    if not records:
        records = _review_run_records(root)
    return Path(records[0]["preflight_path"]) if records else None


def _review_run_records(root: Path, *, branch_prefix: str | None = None) -> list[dict[str, Any]]:
    runs_dir = root / _REVIEW_RUNS_DIR
    if not runs_dir.exists():
        return []
    pattern = f"{branch_prefix}/*/preflight.json" if branch_prefix else "*/*/preflight.json"
    records: list[dict[str, Any]] = []
    for preflight_path in runs_dir.glob(pattern):
        try:
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        review = preflight.get("review") if isinstance(preflight.get("review"), dict) else {}
        diff = preflight.get("diff") if isinstance(preflight.get("diff"), dict) else {}
        records.append(
            {
                "preflight_path": str(preflight_path),
                "generated_at": str(preflight.get("generated_at") or ""),
                "run_id": str(review.get("run_id") or preflight_path.parent.name),
                "branch_prefix": str(review.get("branch_prefix") or preflight_path.parent.parent.name),
                "target": review.get("target") if isinstance(review.get("target"), dict) else {},
                "diff_source": str(diff.get("source") or ""),
            }
        )
    records.sort(key=lambda item: (item["generated_at"], item["run_id"]), reverse=True)
    return records


def _list_review_runs(root: Path) -> None:
    records = _review_run_records(root)
    if not records:
        console.print("No review runs found.")
        return
    console.print("Recent review runs:")
    for item in records[:20]:
        target = item["target"] if isinstance(item["target"], dict) else {}
        pr = f" PR #{target.get('number')}" if target.get("number") else ""
        console.print(
            f"- {item['run_id']} [{item['branch_prefix']}]"
            f"{pr} {item['diff_source']} {item['generated_at']}"
        )


def _render_review_runbook(preflight: dict[str, Any]) -> str:
    target = preflight["review"].get("target", {})
    scaffold = str(preflight["review"].get("scaffold") or "standard")
    citation_source = preflight.get("citation_source") if isinstance(preflight.get("citation_source"), dict) else {}
    return (
        "# AgentPack Review Workflow\n\n"
        "Run the Anchor, Judge, Critic, Actor review flow for the current PR or branch. Treat the source of truth as the latest PR head, "
        "`gh pr view`, `git diff`, and direct reads of exact changed code. The reviewer context below is a prioritization "
        "lens only; it must not replace code evidence.\n\n"
        "## Reviewer Context\n\n"
        f"{preflight['review_context'] or '(none)'}\n\n"
        "## AgentPack Context Preflight\n\n"
        "Before reading PR diff or code, refresh AgentPack context for this exact review task. "
        "Prefer MCP `agentpack_pack_context(task=\"review current PR ...\")`; if MCP is unavailable, "
        "use the current AgentPack CLI refresh command. If you bypass this, state the bypass reason.\n\n"
        "## Preflight\n\n"
        f"- Review mode: {preflight['review'].get('mode', 'fresh')}\n"
        f"- Review scaffold: {scaffold}\n"
        f"- Review run id: {preflight['review']['run_id']}\n"
        f"- Review run dir: {preflight['paths']['run_dir']}\n"
        f"- Branch: {preflight['git']['branch']}\n"
        f"- Branch prefix: {preflight['git']['branch_prefix']}\n"
        f"- Head SHA: {preflight['git']['head_sha']}\n"
        + _render_git_preflight_runbook(preflight)
        + f"- Citation source: {citation_source.get('mode', 'working-tree')} {citation_source.get('head_sha', '')}\n"
        f"- Diff range: {preflight['diff']['range']}\n"
        f"- Diff source: {preflight['diff']['source']}\n"
        + (f"- Review target: PR #{target['number']} ({target['source']})\n" if target.get("number") else "")
        + f"- Changed files: {preflight['diff']['changed_files_count']}\n"
        + (f"- AgentPack context: `{preflight['context_pack']['path']}` ({preflight['context_pack']['tokens']} tokens)\n" if preflight.get("context_pack", {}).get("path") else "")
        + (f"- PR: #{preflight['pr']['number']} — {preflight['pr']['title']}\n" if preflight.get("pr") else "")
        + (f"- PR URL: {preflight['pr']['url']}\n" if preflight.get("pr") and preflight["pr"].get("url") else "")
        + _render_review_observer_runbook(preflight)
        + "\n## Generated Artifacts\n\n"
        + f"- Preflight JSON: `{preflight['paths']['preflight']}`\n"
        f"- Anchor prompt: `{preflight['paths']['understanding_prompt']}`\n"
        f"- Judge prompt: `{preflight['paths']['judge_prompt']}`\n"
        f"- Critic prompt: `{preflight['paths']['critic_prompt']}`\n"
        f"- Anchor TOON fallback template: `{preflight['paths']['understanding_template']}`\n"
        f"- Judge TOON fallback template: `{preflight['paths']['findings_template']}`\n"
        f"- Critic TOON fallback template: `{preflight['paths']['critique_template']}`\n"
        f"- Anchor JSON authoring output: `{preflight['paths']['understanding_authoring_output']}`\n"
        f"- Anchor canonical TOON handoff: `{preflight['paths']['understanding_canonical_output']}`\n"
        f"- Judge JSON authoring output: `{preflight['paths']['findings_authoring_output']}`\n"
        f"- Judge canonical TOON handoff: `{preflight['paths']['findings_canonical_output']}`\n"
        f"- Critic JSON authoring output: `{preflight['paths']['critique_authoring_output']}`\n"
        f"- Critic canonical TOON handoff: `{preflight['paths']['critique_canonical_output']}`\n"
        f"- Actor approved findings: `{preflight['paths']['approved_findings_output']}`\n"
        f"- Stage state JSON: `{preflight['paths']['state']}`\n\n"
        "## Hard Gates\n\n"
        "1. Do not perform the review inline from these prompts or this runbook.\n"
        "2. If diff source is not `pr-target` or `current-pr`, stop and rerun `agentpack review --pr <number>`.\n"
        "3. If you cannot write the Anchor output file at the declared path, stop and report blocked.\n"
        "4. After Anchor, run `agentpack review --check`; do not start Judge until it validates Anchor.\n"
        "5. After Judge, run `agentpack review --check`; do not start Critic until it validates Judge.\n"
        "6. After Critic, run `agentpack review --check`; AgentPack generates approved findings. "
        "Only then may Actor run `agentpack review --check --post-inline-comments` for PR-bound runs. "
        "For local-only fallback reviews, run `agentpack review --check`. Do not produce a final summary unless Critic validates.\n\n"
        "## Workflow\n\n"
        "1. Read the Anchor prompt and produce JSON at the declared understanding authoring path.\n"
        "2. Run `agentpack review --check` and confirm AgentPack wrote canonical `understanding.toon`.\n"
        "3. Read canonical `understanding.toon`, then read the Judge prompt and produce JSON findings at the declared authoring path.\n"
        "4. Run `agentpack review --check`, read both canonical handoffs, then produce Critic JSON at the declared critique authoring path.\n"
        "5. Run `agentpack review --check` and confirm AgentPack wrote `approved-findings.toon`.\n"
        "6. Actor may publish only that approved artifact with `agentpack review --check --post-inline-comments`; it never edits or pushes the PR branch.\n"
        "7. In the final user-facing response, summarize approved findings and validation gaps without exposing internal stage names.\n"
    )


def _render_git_preflight_runbook(preflight: dict[str, Any]) -> str:
    git_info = preflight.get("git") if isinstance(preflight.get("git"), dict) else {}
    gate = git_info.get("preflight") if isinstance(git_info.get("preflight"), dict) else {}
    if not gate:
        return ""
    return (
        f"- Git preflight action: {gate.get('action', '')}\n"
        f"- Git preflight reason: {gate.get('reason', '')}\n"
    )


def _print_git_preflight(preflight: GitPreflight) -> None:
    color = "yellow" if preflight.action.startswith("blocked") or preflight.action == "fetch_failed" else "green"
    console.print(f"[{color}]Git preflight:[/] {preflight.action} — {preflight.reason}")
    if preflight.dirty_sample:
        console.print(f"[{color}]Dirty sample:[/] {', '.join(preflight.dirty_sample)}")


def _render_review_observer_runbook(preflight: dict[str, Any]) -> str:
    observer = preflight.get("observer") if isinstance(preflight.get("observer"), dict) else {}
    notes = observer.get("notes") if isinstance(observer.get("notes"), list) else []
    if not notes:
        return ""
    lines = ["\n## Observer Signals\n\n", "These are advisory priors only; do not cite them as review evidence.\n"]
    for item in notes[:5]:
        if not isinstance(item, dict):
            continue
        confidence = float(item.get("confidence") or 0.0)
        lines.append(f"- `{item.get('path', '')}`: {item.get('reason', '')} (confidence {confidence:.2f})\n")
    return "".join(lines)


def _render_stage_prompt(
    template_name: str,
    preflight: dict[str, Any],
    *,
    output_path: Path,
    template_path: str,
    prior_paths: list[Path],
) -> str:
    root = _root().resolve()
    abs_output = output_path.resolve()
    output_label = "JSON authoring path" if abs_output.suffix == ".json" else "Output path"
    lines = [_load_review_template(template_name)]
    lines.extend(
        [
            "",
            "## AgentPack Run Inputs",
            "",
            f"- Review run id: {preflight['review']['run_id']}",
            f"- Review mode: {preflight['review'].get('mode', 'fresh')}",
            f"- Review scaffold: {preflight['review'].get('scaffold', 'standard')}",
            f"- Preflight JSON: {preflight['paths']['preflight']}",
            f"- Head SHA: {preflight['git']['head_sha']}",
            f"- Diff range: {preflight['diff']['range']}",
            f"- Diff source: {preflight['diff']['source']}",
            f"- Review target: PR #{preflight['review'].get('target', {}).get('number')}"
            if preflight["review"].get("target", {}).get("number")
            else "- Review target: current branch/local fallback",
            f"- {output_label}: {_rel_to_root(abs_output, root)}",
            f"- Copy-fill TOON template: {template_path}",
            "- Structured output format: JSON preferred. TOON is accepted only for simple scalar fields; AgentPack canonicalizes valid JSON to TOON.",
        ]
    )
    context_pack = preflight.get("context_pack") if isinstance(preflight.get("context_pack"), dict) else {}
    if context_pack.get("path"):
        lines.append(f"- Broad AgentPack context: {context_pack['path']}")
    for prior_path in prior_paths:
        lines.append(f"- Canonical TOON input path: {_rel_to_root(prior_path.resolve(), root)}")
    lines.extend(
        [
            "",
            "## Execution Gates",
            "",
            "- Do not answer inline from this stage prompt.",
            "- Prefer valid JSON matching the schema. This is the default path.",
            "- Use TOON only if you can keep every scalar on one line. Do not use YAML block scalars (`>` or `|`) or YAML-style multiline values in TOON.",
            "- Start from the copy-fill TOON template only if TOON is reliable for this artifact.",
            "- Write the required JSON artifact to the declared authoring path and nothing else.",
            "- Run `agentpack review --check`; AgentPack will canonicalize schema-valid JSON to TOON for the next stage.",
            "- If you cannot write the file or validate that it exists, stop and report blocked.",
            "- Run `agentpack review --check` after writing this artifact before continuing.",
        ]
    )
    if prior_paths:
        lines.append("- Do not continue until the declared canonical TOON input exists and has been read from disk.")
    if preflight["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in preflight["warnings"])
    lines.extend(["", "Reviewer context:", preflight["review_context"] or "(none)"])
    return "\n".join(lines).rstrip() + "\n"


def _review_toon_template(kind: str) -> str:
    if kind == "understanding":
        return (
            "@format toon\n"
            "@root review_understanding\n"
            "intent:\n"
            "  issue_ref: null\n"
            "  requirement: Replace with factual restatement from PR or issue\n"
            "  author_decisions[]:\n"
            "    []\n"
            "change_units[]:\n"
            "  -\n"
            "    id: cu1\n"
            "    location: path/to/changed_file.py:10-24\n"
            "    kind: core\n"
            "    what_changed: Replace with factual description of the edit, no judgment\n"
            "    code: Replace with changed block read from the repository\n"
            "    referenced_symbols[]:\n"
            "      []\n"
            "    callers[]:\n"
            "      []\n"
            "    contracts_touched[]:\n"
            "      -\n"
            "        contract: symbol, schema, env var, or API touched\n"
            "        before: Previous contract, or none if new\n"
            "        after: New contract\n"
            "        evidence: path/to/changed_file.py:10\n"
            "    local_convention_refs[]:\n"
            "      []\n"
            "open_questions[]:\n"
            "  []\n"
        )
    if kind == "findings":
        return (
            "@format toon\n"
            "@root review_findings\n"
            "findings[]:\n"
            "  -\n"
            "    id: f1\n"
            "    unit: cu1\n"
            "    lens: unit\n"
            "    type: logic\n"
            "    location: path/to/changed_file.py:12\n"
            "    claim: Replace with a factual review finding\n"
            "    evidence: path/to/changed_file.py:12 shows the supporting code\n"
            "    severity: should-fix\n"
            "    category: defect\n"
            "    confidence: high\n"
            "    depends_on: null\n"
            "    direction: Replace with what would resolve it, or null\n"
            "coverage: Replace with units examined and any gaps\n"
        )
    if kind == "critique":
        return (
            "@format toon\n"
            "@root review_critique\n"
            "head_sha: Replace with the review preflight head SHA\n"
            "decisions[]:\n"
            "  -\n"
            "    finding_id: f1\n"
            "    verdict: accept\n"
            "    rationale: Replace with the evidence-based calibration rationale\n"
            "    severity: null\n"
        )
    raise ValueError(f"unknown review template kind: {kind}")


def _build_review_context_pack(
    root: Path,
    review_context: str,
    diff_info: dict[str, Any],
    outputs: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    target_label = diff_info.get("target_label") or "current PR"
    task = f"review {target_label} with broad repo context"
    if review_context:
        task = f"{task}: {review_context[:200]}"
    changed_paths = _changed_paths(root, str(diff_info.get("range") or "HEAD"))
    if changed_paths:
        task = f"{task}. Prioritize changed files: {', '.join(changed_paths[:80])}"
    try:
        result = PackService().run(PackRequest(
            root=root,
            agent="generic",
            task=task,
            mode="deep",
            budget=0,
            since=diff_info.get("base_ref") or None,
            refresh=False,
            task_source="review",
            output_path=outputs["run_dir"] / "context.md",
            write_canonical=False,
        ))
    except Exception as exc:
        warnings.append(f"Could not build broad AgentPack context for review: {exc}")
        if not changed_paths:
            return {"path": "", "tokens": 0, "selected_files": 0, "broad_context": False}
        result = None
    if result is not None and (result.pack.selected_files or not changed_paths):
        return {
            "path": _rel_to_root(result.out_path, root),
            "tokens": result.packed_tokens,
            "selected_files": len(result.pack.selected_files),
            "broad_context": result.pack.broad_context is not None,
        }
    warnings.append(
        "AgentPack selected no files for the review context; writing a bounded changed-file fallback."
    )
    fallback_path = outputs["run_dir"] / "context.md"
    fallback_text, fallback_file_count = _write_review_context_fallback(root, diff_info, changed_paths, fallback_path)
    return {
        "path": _rel_to_root(fallback_path, root),
        "tokens": len(fallback_text.split()),
        "selected_files": fallback_file_count,
        "broad_context": False,
        "fallback": "changed-files",
    }


def _write_review_context_fallback(
    root: Path,
    diff_info: dict[str, Any],
    changed_paths: list[str],
    output_path: Path,
    *,
    max_chars: int = 80_000,
) -> tuple[str, int]:
    """Keep review context useful when ranking cannot select a changed file."""
    head_ref = str(diff_info.get("head_ref") or diff_info.get("head_sha") or "HEAD")
    sections = [
        "# Review Context Fallback",
        "",
        "AgentPack could not select changed files within the review context budget.",
        "The following bounded snapshots are the PR changed files and should be treated as the primary review context.",
        "",
    ]
    remaining = max_chars
    included_files = 0
    for path in changed_paths:
        if remaining <= 0:
            break
        content = _git_show_file(root, head_ref, path)
        if content is None:
            try:
                content = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        if "\x00" in content:
            continue
        snippet = _review_file_window(root, diff_info, path, content, max_chars=min(6_000, remaining)).rstrip()
        truncated = "\n\n[truncated]" if len(snippet) < len(content) else ""
        section = f"## {path}\n\n```text\n{snippet}{truncated}\n```\n"
        sections.append(section)
        remaining -= len(section)
        included_files += 1
    text = "\n".join(sections).rstrip() + "\n"
    _atomic_write(output_path, text)
    return text, included_files


def _load_review_template(name: str) -> str:
    try:
        return (
            importlib.resources.files("agentpack")
            .joinpath("data", "review", name)
            .read_text(encoding="utf-8")
        )
    except Exception:
        return (
            Path(__file__).resolve().parents[1] / "data" / "review" / name
        ).read_text(encoding="utf-8")


def _gh_pr_metadata(root: Path, target: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not target:
        return None
    if shutil.which("gh") is None:
        return None
    target_arg = _target_cli_arg(target)
    args = ["gh", "pr", "view"]
    if target_arg:
        args.append(target_arg)
    args.extend([
        "--json",
        "number,title,url,baseRefName,headRefName",
    ])
    result = subprocess.run(
        args,
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return {
        "number": payload.get("number"),
        "title": payload.get("title", ""),
        "url": payload.get("url", ""),
        "base_ref": payload.get("baseRefName", ""),
        "head_ref": payload.get("headRefName", ""),
    }


def _repo_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _diff_base(
    root: Path,
    pr: dict[str, Any] | None,
    *,
    target: dict[str, Any] | None = None,
    allow_local_fallback: bool = False,
    base_ref: str = "",
) -> dict[str, str]:
    if pr:
        number = pr.get("number") or (target or {}).get("number")
        base_name = str(pr.get("base_ref") or "").strip()
        if number and base_name:
            fetched = _fetch_pr_refs(root, int(number), base_name)
            if fetched["ok"]:
                base_ref = f"refs/remotes/origin/{base_name}"
                head_ref = f"refs/remotes/origin/pr/{number}"
                return {
                    "base_ref": base_ref,
                    "head_ref": head_ref,
                    "head_sha": _rev_parse(root, head_ref),
                    "range": f"{base_ref}...{head_ref}",
                    "source": "pr-target" if target else "current-pr",
                    "target_label": f"PR #{number}",
                }
            if not allow_local_fallback:
                raise _ReviewPreflightError(
                    f"could not fetch PR #{number} refs ({fetched['error']}); "
                    "rerun with network/GitHub access or pass --allow-local-fallback explicitly"
                )
        elif not allow_local_fallback:
            raise _ReviewPreflightError("PR metadata missing number/base branch; pass --pr <number> or --allow-local-fallback")
        return _local_diff_base(root, pr, fallback_reason=f"PR ref fetch unavailable for #{number or '?'}", base_ref=base_ref)

    if not allow_local_fallback:
        target_hint = _target_cli_arg(target)
        if target_hint:
            raise _ReviewPreflightError(
                f"gh PR metadata unavailable for {target_hint}; review diff not trusted. "
                "Fix gh auth/network or pass --allow-local-fallback explicitly."
            )
        raise _ReviewPreflightError(
            "gh PR metadata unavailable; pass --pr <number-or-url> so review can bind to the requested PR, "
            "or pass --allow-local-fallback explicitly for local branch review"
        )
    return _local_diff_base(root, pr, fallback_reason="gh PR metadata unavailable", base_ref=base_ref)


def _local_diff_base(
    root: Path,
    pr: dict[str, Any] | None,
    *,
    fallback_reason: str,
    base_ref: str = "",
) -> dict[str, str]:
    head_sha = git_core.current_sha(root) or ""
    requested_base = base_ref or str((pr or {}).get("base_ref", ""))
    candidates = _base_candidates(requested_base)
    for candidate in candidates:
        if not _git_ref_exists(root, candidate):
            continue
        base_sha = _merge_base(root, candidate)
        if base_sha and (base_sha != head_sha or requested_base):
            return _local_diff_info(base_sha, head_sha, fallback_reason, candidate)

    # Fresh test repositories often have only a default branch and no remote.
    # Use its parent only in that explicit local-fallback mode; feature branches
    # without a resolvable default base remain blocked.
    branch = git_core.current_branch(root) or ""
    if not base_ref and branch in {"main", "master"} and _git_ref_exists(root, "HEAD^"):
        base_sha = _merge_base(root, "HEAD^")
        if base_sha:
            return _local_diff_info(base_sha, head_sha, fallback_reason, "HEAD^")
    raise _ReviewPreflightError(
        "local review fallback requires --base <ref> or a resolvable origin/main, origin/master, main, or master ref"
    )


def _local_diff_info(base_sha: str, head_sha: str, fallback_reason: str, base_label: str) -> dict[str, str]:
    return {
        "base_ref": base_sha,
        "head_ref": "HEAD",
        "head_sha": head_sha,
        "range": f"{base_sha}...HEAD",
        "source": "local-fallback",
        "fallback_reason": fallback_reason,
        "base_label": base_label,
        "target_label": "local branch",
    }


def _fetch_pr_refs(root: Path, number: int, base_name: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "git",
            "fetch",
            "--quiet",
            "origin",
            f"+refs/heads/{base_name}:refs/remotes/origin/{base_name}",
            f"+refs/pull/{number}/head:refs/remotes/origin/pr/{number}",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return {"ok": True, "error": ""}
    return {"ok": False, "error": (result.stderr or result.stdout or f"git fetch exited {result.returncode}").strip()}


def _rev_parse(root: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", _qualified_ref(ref)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _base_candidates(base_name: str) -> list[str]:
    candidates: list[str] = []
    if base_name:
        candidates.extend([f"refs/remotes/origin/{base_name}", base_name])
    candidates.extend([
        "refs/remotes/origin/main",
        "refs/remotes/origin/master",
        "main",
        "master",
    ])
    return candidates


def _merge_base(root: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "merge-base", "HEAD", ref],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _review_file_window(root: Path, diff_info: dict[str, Any], path: str, content: str, *, max_chars: int) -> str:
    diff_range = str(diff_info.get("range") or "")
    if not diff_range:
        return content[:max_chars]
    result = subprocess.run(
        ["git", "diff", "--unified=24", diff_range, "--", path],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return content[:max_chars]
    lines = content.splitlines()
    windows: list[tuple[int, int]] = []
    for match in re.finditer(_DIFF_HUNK_RE.pattern, result.stdout, flags=re.MULTILINE):
        start = max(1, int(match.group("new_start")) - 24)
        count = int(match.group("new_count") or "1")
        windows.append((start, min(len(lines), start + count + 48)))
    if not windows:
        return content[:max_chars]
    selected: list[str] = []
    for start, end in windows:
        selected.extend(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))
    return "\n".join(selected)[:max_chars]


def _qualified_ref(ref: str) -> str:
    if ref.startswith("origin/"):
        return f"refs/remotes/{ref}"
    return ref


def _git_ref_exists(root: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", _qualified_ref(ref)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _changed_paths(root: Path, diff_range: str) -> list[str]:
    if diff_range == "HEAD":
        return sorted(git_core.changed_files(root) | git_core.untracked_files(root))
    args = ["git", "diff", "--name-only", diff_range]
    result = subprocess.run(args, cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def _blob_sha(root: Path, ref: str, path: str) -> str:
    if not ref or not path:
        return ""
    result = subprocess.run(
        ["git", "ls-tree", _qualified_ref(ref), "--", path],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    parts = result.stdout.strip().split()
    return parts[2] if len(parts) >= 3 else ""


def _review_scaffold_mode(
    changed_files: list[dict[str, Any]],
    review_context: str,
    *,
    override: str = "",
) -> str:
    if override in {"light", "strict"}:
        return override
    risky_terms = {
        "auth",
        "billing",
        "payment",
        "security",
        "permission",
        "migration",
        "database",
        "token",
        "secret",
        "privacy",
    }
    haystack = " ".join([review_context, *[str(item.get("path") or "") for item in changed_files]]).lower()
    if any(term in haystack for term in risky_terms):
        return "strict"
    if len(changed_files) <= 5:
        return "light"
    return "standard"


def _warnings(
    root: Path,
    pr: dict[str, Any] | None,
    diff_info: dict[str, str],
    changed_paths: list[str],
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    info: list[str] = []
    dirty = sorted(git_core.dirty_files(root))
    dirty_overlap = sorted(set(dirty) & set(changed_paths))
    if dirty_overlap:
        warnings.append(
            f"dirty tree overlaps review diff ({len(dirty_overlap)} path(s)); review the fetched PR head, not local edits"
        )
    elif dirty:
        info.append(f"dirty tree has {len(dirty)} unrelated path(s); no overlap with review diff")
    warnings.extend(_incomplete_review_run_warnings(root))
    if not pr:
        warnings.append("gh PR metadata unavailable; review is using local git context only")
    if diff_info["source"] == "local-fallback":
        warnings.append(f"diff fell back to local range {diff_info['range']}: {diff_info.get('fallback_reason', 'unknown reason')}")
    if not changed_paths:
        warnings.append("no changed files detected for the selected diff range")
    generated = [path for path in changed_paths if path.startswith(".agentpack/")]
    if generated:
        warnings.append("generated AgentPack artifacts are in the diff; keep them low priority unless the change is about distribution or docs")
    return warnings, info


def _incomplete_review_run_warnings(root: Path) -> list[str]:
    branch = git_core.current_branch(root) or "HEAD"
    branch_prefix = branch.replace("/", "-")
    branch_dir = root / _REVIEW_RUNS_DIR / branch_prefix
    if not branch_dir.exists():
        return []
    warnings: list[str] = []
    for run_dir in sorted((path for path in branch_dir.iterdir() if path.is_dir()), reverse=True):
        preflight_path = run_dir / "preflight.json"
        preflight: dict[str, Any] | None = None
        if preflight_path.exists():
            try:
                loaded = json.loads(preflight_path.read_text(encoding="utf-8"))
                preflight = loaded if isinstance(loaded, dict) else None
            except (OSError, json.JSONDecodeError):
                preflight = None
        if preflight:
            understanding_paths = _artifact_paths(preflight, "understanding", root=root)
            findings_paths = _artifact_paths(preflight, "findings", root=root)
            critique_paths = _artifact_paths(preflight, "critique", root=root)
            understanding = understanding_paths.source
            findings = findings_paths.source
            critique = critique_paths.source
        else:
            understanding = run_dir / "understanding.toon"
            findings = run_dir / "findings.toon"
            critique = run_dir / "critique.toon"
            understanding_paths = _ReviewArtifactPaths(understanding, understanding, _rel_to_root(understanding, root), _rel_to_root(understanding, root))
            findings_paths = _ReviewArtifactPaths(findings, findings, _rel_to_root(findings, root), _rel_to_root(findings, root))
            critique_paths = _ReviewArtifactPaths(critique, critique, _rel_to_root(critique, root), _rel_to_root(critique, root))
        if understanding and understanding.exists():
            try:
                _validate_and_canonicalize_review_artifact(understanding_paths, kind="understanding", preflight=preflight)
            except ValueError as exc:
                warnings.append(f"invalid understanding artifact in {run_dir.name}: {exc}")
                break
        if findings and findings.exists():
            try:
                _validate_and_canonicalize_review_artifact(findings_paths, kind="findings", preflight=preflight)
            except ValueError as exc:
                warnings.append(f"invalid findings artifact in {run_dir.name}: {exc}")
                break
        if critique and critique.exists():
            try:
                critique_payload = _validate_and_canonicalize_review_artifact(critique_paths, kind="critique", preflight=preflight)
                if preflight and findings and findings.exists():
                    findings_payload = _validate_and_canonicalize_review_artifact(findings_paths, kind="findings", preflight=preflight)
                    _validate_critique_against_findings(critique_payload, findings_payload, preflight)
            except ValueError as exc:
                warnings.append(f"invalid critique artifact in {run_dir.name}: {exc}")
                break
        if understanding and understanding.exists() and (not findings or not critique):
            warnings.append(
                f"incomplete previous review run {run_dir.name}; start fresh by default or resume with `agentpack review --resume {run_dir.name}`"
            )
            break
    return warnings


def _review_state(root: Path, preflight: dict[str, Any]) -> dict[str, Any]:
    understanding = _artifact_paths(preflight, "understanding", root=root)
    findings = _artifact_paths(preflight, "findings", root=root)
    critique = _artifact_paths(preflight, "critique", root=root)
    status = "awaiting_anchor"
    try:
        if understanding.source is not None:
            _validate_and_canonicalize_review_artifact(understanding, kind="understanding", preflight=preflight)
            status = "awaiting_judge"
        if findings.source is not None:
            _validate_and_canonicalize_review_artifact(findings, kind="findings", preflight=preflight)
            status = "awaiting_critic"
        if critique.source is not None:
            critique_payload = _validate_and_canonicalize_review_artifact(critique, kind="critique", preflight=preflight)
            findings_payload = _validate_and_canonicalize_review_artifact(findings, kind="findings", preflight=preflight)
            _validate_critique_against_findings(critique_payload, findings_payload, preflight)
            status = "ready_to_publish"
            run_dir = root / str(preflight["paths"]["run_dir"])
            if (run_dir / "posted-review.json").exists() or (run_dir / "inline-review-dry-run.json").exists():
                status = "complete"
    except ValueError:
        status = "blocked_invalid_artifact"
    return {
        "generated_at": _now_iso(),
        "run_id": preflight["review"]["run_id"],
        "status": status,
        "preflight": preflight["paths"]["preflight"],
        "understanding_authoring_output": understanding.authoring_rel,
        "understanding_output": understanding.canonical_rel,
        "findings_authoring_output": findings.authoring_rel,
        "findings_output": findings.canonical_rel,
        "critique_authoring_output": critique.authoring_rel,
        "critique_output": critique.canonical_rel,
        "approved_findings_output": str(preflight["paths"].get("approved_findings_output") or ""),
        "check_command": "agentpack review --check",
    }


def _check_active_review(root: Path, *, post_inline_comments: bool = False, dry_run_post: bool = False) -> None:
    if not (root / _PREFLIGHT_PATH).exists():
        console.print("[red]No active review preflight found.[/] Run `agentpack review --pr <number>` first.")
        raise typer.Exit(1)
    try:
        preflight = json.loads((root / _PREFLIGHT_PATH).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Active review preflight is invalid JSON:[/] {exc}")
        raise typer.Exit(1) from exc

    stale_reason = _stale_active_review_reason(root, preflight)
    if stale_reason:
        console.print(f"[red]Active review preflight is stale:[/] {stale_reason}")
        _print_review_check_action(
            what_failed=stale_reason,
            why_it_matters="validating artifacts from another branch or PR can block the current review with unrelated failures",
            repair_command="run `agentpack review --pr <number>` for the current PR, or `agentpack review --allow-local-fallback` for a local review",
            safe_to_continue="no; start or resume the review that matches the current checkout",
        )
        raise typer.Exit(1)

    understanding = _artifact_paths(preflight, "understanding", root=root)
    findings = _artifact_paths(preflight, "findings", root=root)
    critique = _artifact_paths(preflight, "critique", root=root)
    state_path = root / preflight["paths"].get("state", _STATE_PATH)

    if understanding.source is None:
        state = _review_state(root, preflight)
        _write_review_state(root, preflight, state)
        console.print(f"[red]Anchor artifact missing:[/] {understanding.authoring_rel}")
        _print_review_check_action(
            what_failed="Anchor understanding artifact is missing",
            why_it_matters="Judge findings would be based on inline memory instead of a checked file artifact",
            repair_command=f"write {understanding.authoring_rel} then run `agentpack review --check`",
            safe_to_continue="no; create the Anchor artifact first",
        )
        raise typer.Exit(1)
    try:
        _validate_and_canonicalize_review_artifact(understanding, kind="understanding", preflight=preflight)
    except ValueError as exc:
        state = _review_state(root, preflight)
        _write_review_state(root, preflight, state)
        console.print(f"[red]Anchor artifact invalid:[/] {exc}")
        _print_review_check_action(
            what_failed=str(exc),
            why_it_matters="invalid Anchor artifact blocks evidence-backed Judge findings",
            repair_command=f"repair {understanding.authoring_rel} then run `agentpack review --check`",
            safe_to_continue="no; fix Anchor schema/citations first",
        )
        raise typer.Exit(1) from exc

    if findings.source is None:
        state = _review_state(root, preflight)
        _write_review_state(root, preflight, state)
        console.print(f"[green]✓[/] Anchor valid. Canonical TOON: [bold]{understanding.canonical_rel}[/]")
        console.print("[green]✓[/] Proceed to Judge prompt.")
        console.print(f"State: [bold]{_rel_to_root(state_path, root)}[/]")
        if post_inline_comments or dry_run_post:
            console.print("[red]Actor blocked:[/] Judge and Critic artifacts must validate before publishing.")
            raise typer.Exit(1)
        return
    try:
        findings_payload = _validate_and_canonicalize_review_artifact(findings, kind="findings", preflight=preflight)
    except ValueError as exc:
        state = _review_state(root, preflight)
        _write_review_state(root, preflight, state)
        console.print(f"[red]Judge artifact invalid:[/] {exc}")
        _print_review_check_action(
            what_failed=str(exc),
            why_it_matters="invalid candidate findings cannot be evaluated by Critic or published safely",
            repair_command=f"repair {findings.authoring_rel} then run `agentpack review --check`",
            safe_to_continue="no; fix Judge schema/citations first",
        )
        raise typer.Exit(1) from exc

    if critique.source is None:
        state = _review_state(root, preflight)
        _write_review_state(root, preflight, state)
        console.print(f"[green]✓[/] Judge valid. Canonical TOON: [bold]{findings.canonical_rel}[/]")
        console.print("[green]✓[/] Proceed to Critic prompt.")
        console.print(f"State: [bold]{_rel_to_root(state_path, root)}[/]")
        if post_inline_comments or dry_run_post:
            console.print("[red]Actor blocked:[/] Critic artifact must validate before publishing.")
            raise typer.Exit(1)
        return
    try:
        critique_payload = _validate_and_canonicalize_review_artifact(critique, kind="critique", preflight=preflight)
        _validate_critique_against_findings(critique_payload, findings_payload, preflight)
    except ValueError as exc:
        state = _review_state(root, preflight)
        _write_review_state(root, preflight, state)
        console.print(f"[red]Critic artifact invalid:[/] {exc}")
        _print_review_check_action(
            what_failed=str(exc),
            why_it_matters="Actor may only publish Critic-approved findings for this exact Judge artifact and PR head",
            repair_command=f"repair {critique.authoring_rel} then run `agentpack review --check`",
            safe_to_continue="no; fix the Critic decisions first",
        )
        raise typer.Exit(1) from exc

    _write_approved_findings(root, preflight, findings_payload, critique_payload)
    approved_payload = _read_approved_findings(root, preflight)
    posted: dict[str, Any] | None = None
    if post_inline_comments or dry_run_post:
        try:
            posted = _post_inline_review_comments(root, preflight, approved_payload, dry_run=dry_run_post)
        except _ReviewPreflightError as exc:
            state = _review_state(root, preflight)
            _write_review_state(root, preflight, state)
            action = "dry-run inline review payload" if dry_run_post else "post inline review comments"
            console.print(f"[red]Could not {action}:[/] {exc}")
            _print_review_check_action(
                what_failed=str(exc),
                why_it_matters="GitHub inline comments require a PR-bound, right-side-line payload",
                repair_command="agentpack review --check --dry-run-post",
                safe_to_continue="no for posting; yes for a local summary that states posting is blocked",
            )
            raise typer.Exit(1) from exc

    state = _review_state(root, preflight)
    if posted:
        state["posted_review"] = posted
    _write_review_state(root, preflight, state)
    try:
        raw_findings = approved_payload.get("findings")
        findings_count = len(raw_findings) if isinstance(raw_findings, list) else 0
        changed_file_paths = [
            str(item.get("path") or "")
            for item in preflight.get("changed_files", [])
            if isinstance(item, dict) and item.get("path")
        ]
        record_review_observation(
            root,
            task=str(preflight.get("review_context") or "review"),
            status="complete",
            changed_files=changed_file_paths,
            findings_count=findings_count,
            posted_status=str((posted or {}).get("status") or ""),
        )
        write_observer_brief(root, task=str(preflight.get("review_context") or "review"))
    except Exception:
        pass
    if posted:
        if posted.get("status") == "already_posted":
            console.print(f"[yellow]Review comments already posted:[/] {posted.get('url', '')}")
        elif posted.get("status") == "no_findings":
            console.print("[green]✓[/] Critic valid. No approved findings to post as inline comments.")
        elif posted.get("status") == "dry_run":
            console.print(f"[green]✓[/] Inline review payload valid: [bold]{posted.get('request_payload', posted.get('path', ''))}[/]")
        else:
            console.print(f"[green]✓[/] Posted inline review comments: [bold]{posted.get('url', '')}[/]")
    else:
        console.print("[green]✓[/] Critic valid. Approved findings are ready for the publish-only Actor.")
    console.print(f"State: [bold]{_rel_to_root(state_path, root)}[/]")


def _stale_active_review_reason(root: Path, preflight: dict[str, Any]) -> str:
    preflight_git = preflight.get("git") if isinstance(preflight.get("git"), dict) else {}
    expected_branch = str(preflight_git.get("branch") or "")
    current_branch = git_core.current_branch(root) or ""
    if expected_branch and current_branch and expected_branch != current_branch:
        return f"active review was prepared for branch {expected_branch}, but current branch is {current_branch}"
    paths = preflight.get("paths") if isinstance(preflight.get("paths"), dict) else {}
    run_dir = str(paths.get("run_dir") or "")
    if run_dir and not (root / run_dir).exists():
        return f"active review run directory is missing: {run_dir}"
    return ""


def _validate_critique_against_findings(
    critique_payload: dict[str, Any],
    findings_payload: dict[str, Any],
    preflight: dict[str, Any],
) -> None:
    expected_head = str(preflight.get("git", {}).get("head_sha") or "")
    critique_head = str(critique_payload.get("head_sha") or "")
    if not expected_head or critique_head != expected_head:
        raise ValueError("critique head_sha must exactly match the review preflight head SHA")
    findings = findings_payload.get("findings")
    decisions = critique_payload.get("decisions")
    if not isinstance(findings, list) or not isinstance(decisions, list):
        raise ValueError("Judge findings and Critic decisions must both be lists")
    finding_ids = [str(item.get("id") or "") for item in findings if isinstance(item, dict)]
    if len(finding_ids) != len(findings) or len(set(finding_ids)) != len(finding_ids):
        raise ValueError("Judge findings must have unique non-empty IDs before Critic review")
    decision_ids = [str(item.get("finding_id") or "") for item in decisions if isinstance(item, dict)]
    if len(decision_ids) != len(decisions) or len(set(decision_ids)) != len(decision_ids):
        raise ValueError("Critic decisions must contain exactly one decision per unique Judge finding ID")
    unknown = sorted(set(decision_ids) - set(finding_ids))
    missing = sorted(set(finding_ids) - set(decision_ids))
    if unknown:
        raise ValueError(f"Critic decisions reference unknown Judge finding IDs: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"Critic decisions are missing Judge finding IDs: {', '.join(missing)}")
    severity_rank = {"nit": 0, "should-fix": 1, "blocker": 2}
    findings_by_id = {str(item["id"]): item for item in findings if isinstance(item, dict)}
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        if str(decision.get("verdict") or "") != "downgrade":
            continue
        finding = findings_by_id[str(decision.get("finding_id") or "")]
        original = str(finding.get("severity") or "")
        replacement = str(decision.get("severity") or "")
        if original == "nit":
            raise ValueError(
                f"Critic downgrade for {finding['id']} is invalid because it is already nit; "
                "use accept or reject"
            )
        if severity_rank.get(replacement, -1) >= severity_rank.get(original, -1):
            raise ValueError(
                f"Critic downgrade for {finding['id']} must lower severity from {original} to a lower severity"
            )


def _write_approved_findings(
    root: Path,
    preflight: dict[str, Any],
    findings_payload: dict[str, Any],
    critique_payload: dict[str, Any],
) -> dict[str, Any]:
    decisions = {
        str(item["finding_id"]): item
        for item in critique_payload.get("decisions", [])
        if isinstance(item, dict)
    }
    approved_findings: list[dict[str, Any]] = []
    for finding in findings_payload.get("findings", []):
        if not isinstance(finding, dict):
            continue
        decision = decisions[str(finding["id"])]
        if decision["verdict"] == "reject":
            continue
        approved = dict(finding)
        if decision["verdict"] == "downgrade":
            approved["severity"] = decision["severity"]
        approved_findings.append(approved)
    payload = {"findings": approved_findings, "coverage": findings_payload["coverage"]}
    canonical = canonicalize_to_toon_text(
        json.dumps(payload),
        schema="review-findings",
        source="approved findings",
    )
    approved_rel = str(preflight["paths"].get("approved_findings_output") or "")
    if not approved_rel:
        raise ValueError("active review preflight is missing approved findings path")
    approved_path = Path(approved_rel)
    if not approved_path.is_absolute():
        approved_path = root / approved_path
    _write_canonical_artifact(approved_path, canonical.text)
    active_path = root / _APPROVED_FINDINGS_PATH
    _write_canonical_artifact(active_path, canonical.text)
    return payload


def _read_approved_findings(root: Path, preflight: dict[str, Any]) -> dict[str, Any]:
    approved_rel = str(preflight["paths"].get("approved_findings_output") or "")
    if not approved_rel:
        raise ValueError("active review preflight is missing approved findings path")
    approved_path = Path(approved_rel)
    if not approved_path.is_absolute():
        approved_path = root / approved_path
    return _load_review_artifact_payload(approved_path, kind="findings", canonical_path=approved_path)


def _print_review_check_action(
    *,
    what_failed: str,
    why_it_matters: str,
    repair_command: str,
    safe_to_continue: str,
) -> None:
    console.print(f"  What failed: {what_failed}")
    console.print(f"  Why it matters: {why_it_matters}")
    console.print(f"  Repair command: {repair_command}")
    console.print(f"  Safe to continue: {safe_to_continue}")


def _write_review_state(root: Path, preflight: dict[str, Any], state: dict[str, Any]) -> None:
    targets = [root / _STATE_PATH]
    if preflight.get("paths", {}).get("state"):
        targets.append(root / preflight["paths"]["state"])
    content = json.dumps(state, indent=2) + "\n"
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, content)


def _validate_and_canonicalize_review_artifact(
    paths: _ReviewArtifactPaths,
    *,
    kind: str,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = paths.source
    if source is None:
        raise ValueError(f"{kind} artifact missing: {paths.authoring_rel}")
    return _validate_review_artifact(
        source,
        kind=kind,
        preflight=preflight,
        canonical_path=paths.canonical,
    )


def _validate_review_artifact(
    path: Path,
    *,
    kind: str,
    preflight: dict[str, Any] | None = None,
    canonical_path: Path | None = None,
) -> dict[str, Any]:
    try:
        payload = _load_review_artifact_payload(path, kind=kind, canonical_path=canonical_path)
    except (OSError, ToonParseError, json.JSONDecodeError, ValueError) as exc:
        repair_path = _write_review_repair_guide(path, kind, str(exc))
        repair_note = f"; repair guide: {repair_path.name}" if repair_path else ""
        raise ValueError(f"{path.name} is not a valid review artifact: {exc}{repair_note}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must decode to an object")
    schema_errors = validate_toon_payload_schema(payload, _review_schema(kind))
    if schema_errors:
        details = "; ".join(schema_errors[:5])
        repair_path = _write_review_repair_guide(path, kind, details)
        repair_note = f"; repair guide: {repair_path.name}" if repair_path else ""
        raise ValueError(f"{path.name} schema invalid: {details}{repair_note}")
    root = _validation_root(path)
    preflight = preflight or _preflight_for_artifact(root, path)
    if kind == "critique":
        return payload
    content_resolver = _review_citation_content_resolver(root, preflight)
    citation_validation = (
        _validate_understanding_citations(root, payload, content_resolver=content_resolver)
        if kind == "understanding"
        else _validate_findings_citations(root, payload, content_resolver=content_resolver)
    )
    if citation_validation.invalid or citation_validation.missing:
        details = [*citation_validation.invalid[:5], *citation_validation.missing[:5]]
        suffix = "; ".join(details) if details else "missing citation"
        report_path = _write_review_validation_report(path, kind, citation_validation)
        report_note = f"; full report: {report_path.name}" if report_path else ""
        raise ValueError(f"{path.name} has invalid or missing citations: {suffix}{report_note}")
    return payload


def _load_review_artifact_payload(path: Path, *, kind: str, canonical_path: Path | None = None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        payload = load_toon(path)
        if not isinstance(payload, dict):
            return payload
        if text.lstrip().startswith("@format toon") and text.strip().startswith("@format toon"):
            if canonical_path is not None and canonical_path != path:
                _write_canonical_artifact(canonical_path, text)
            return payload
    except ToonParseError:
        pass

    canonical = canonicalize_to_toon_text(text, schema=_review_schema(kind), source=str(path))
    target = canonical_path or path
    _write_canonical_artifact(target, canonical.text, existing_text=text if target == path else None)
    if not isinstance(canonical.payload, dict):
        raise ValueError(f"{path.name} must decode to an object")
    return canonical.payload


def _write_canonical_artifact(path: Path, text: str, *, existing_text: str | None = None) -> None:
    if existing_text is None:
        try:
            existing_text = path.read_text(encoding="utf-8")
        except OSError:
            existing_text = None
    if existing_text != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, text)


def _review_schema(kind: str) -> str:
    schemas = {
        "understanding": "review-understanding",
        "findings": "review-findings",
        "critique": "review-critique",
    }
    try:
        return schemas[kind]
    except KeyError as exc:
        raise ValueError(f"unknown review artifact kind: {kind}") from exc


def _write_review_repair_guide(path: Path, kind: str, error: str) -> Path | None:
    try:
        guide_path = path.with_name(f"{kind}-toon-repair.md")
        template_kind = kind
        guide = (
            "# AgentPack Review TOON Repair\n\n"
            f"Artifact: `{path.name}`\n\n"
            f"Error: `{error}`\n\n"
            "Use one of these safe recovery paths:\n\n"
            "1. Replace the file with canonical TOON using this template and real repo `path:line` evidence.\n"
            "2. If the agent cannot emit TOON reliably, write valid JSON matching the same schema; "
            "`agentpack review --check` will canonicalize schema-valid JSON to TOON before continuing.\n\n"
            "```toon\n"
            f"{_review_toon_template(template_kind).rstrip()}\n"
            "```\n"
        )
        guide_path.write_text(guide, encoding="utf-8")
        return guide_path
    except OSError:
        return None


def _write_review_validation_report(path: Path, kind: str, validation: CitationValidation) -> Path | None:
    try:
        report_path = path.with_name(f"{kind}-validation-errors.json")
        payload = {
            "artifact": path.name,
            "kind": kind,
            "invalid": validation.invalid,
            "missing": validation.missing,
            "repair_hints": _citation_repair_hints(validation.invalid),
            "valid_count": len(validation.valid),
        }
        report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return report_path
    except OSError:
        return None


def _citation_repair_hints(invalid: list[str]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for item in invalid:
        match = re.search(r"(?P<path>[A-Za-z0-9_./@+~:-]+):(?P<line>\d+).*suggested=(?P<suggested>[A-Za-z0-9_./@+~:-]+:\d+)", item)
        if not match:
            continue
        hint: dict[str, Any] = {
            "failed": f"{match.group('path')}:{match.group('line')}",
            "suggested": match.group("suggested"),
            "reason": "cited line did not support claim text",
        }
        suggestions = re.search(r"; suggestions=(?P<suggestions>.+)$", item)
        if suggestions:
            hint["suggestions"] = suggestions.group("suggestions")
        hints.append(hint)
    return hints


def _preflight_for_artifact(root: Path, path: Path) -> dict[str, Any] | None:
    candidates = [path.parent / "preflight.json", root / _PREFLIGHT_PATH]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            preflight = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        paths = preflight.get("paths") if isinstance(preflight.get("paths"), dict) else {}
        known = {
            str(paths.get("understanding_authoring_output") or ""),
            str(paths.get("understanding_canonical_output") or ""),
            str(paths.get("understanding_output") or ""),
            str(paths.get("findings_authoring_output") or ""),
            str(paths.get("findings_canonical_output") or ""),
            str(paths.get("findings_output") or ""),
            str(paths.get("critique_authoring_output") or ""),
            str(paths.get("critique_canonical_output") or ""),
            str(paths.get("critique_output") or ""),
        }
        rel = _rel_to_root(path, root)
        if candidate == path.parent / "preflight.json" or rel in known:
            return preflight
    return None


def _review_citation_content_resolver(root: Path, preflight: dict[str, Any] | None):
    if not preflight:
        return None
    citation_source = preflight.get("citation_source") if isinstance(preflight.get("citation_source"), dict) else {}
    head_sha = str(citation_source.get("head_sha") or preflight.get("git", {}).get("head_sha") or "").strip()
    if citation_source.get("mode") != "git-head" or not head_sha:
        return None
    cache: dict[str, str | None] = {}

    def resolver(citation: Citation) -> str | None:
        path = citation.path
        if path not in cache:
            cache[path] = _git_show_file(root, head_sha, path)
        return cache[path]

    return resolver


def _git_show_file(root: Path, ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{_qualified_ref(ref)}:{path}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _validate_findings_citations(
    root: Path,
    payload: dict[str, Any],
    *,
    content_resolver=None,
) -> CitationValidation:
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return CitationValidation(valid=[], invalid=["findings must be a list"], missing=[])
    valid: list[Citation] = []
    invalid: list[str] = []
    missing: list[str] = []
    semantic_judge = _semantic_support_judge()
    for index, finding in enumerate(raw_findings, start=1):
        if not isinstance(finding, dict):
            invalid.append(f"finding {index}: not an object")
            continue
        location = parse_location(str(finding.get("location") or ""))
        if location is None:
            missing.append(f"finding {index}.location: missing valid location path:line")
        else:
            location.claim_id = f"finding:{index}:location"
            location_validation = validate_citations(root, [location], content_resolver=content_resolver)
            valid.extend(location_validation.valid)
            invalid.extend(f"finding {index}.location: {item}" for item in location_validation.invalid)
        evidence_citations = extract_location_citations(finding.get("evidence"))
        if not evidence_citations:
            missing.append(f"finding {index}.evidence: missing evidence path:line")
        for citation in evidence_citations:
            citation.claim_id = f"finding:{index}:evidence"
        evidence_validation = validate_citations(root, evidence_citations, content_resolver=content_resolver)
        valid.extend(evidence_validation.valid)
        invalid.extend(f"finding {index}.evidence: {item}" for item in evidence_validation.invalid)
        invalid.extend(
            validate_claim_support(
                root,
                finding.get("claim"),
                evidence_citations,
                label=f"finding {index}.evidence",
                ignored_claim_terms=Path(location.path).stem if location is not None else None,
                semantic_judge=semantic_judge,
                content_resolver=content_resolver,
            )
        )
    return CitationValidation(valid=valid, invalid=invalid, missing=missing)


def _validate_understanding_citations(
    root: Path,
    payload: dict[str, Any],
    *,
    content_resolver=None,
) -> CitationValidation:
    raw_units = payload.get("change_units")
    if not isinstance(raw_units, list):
        return CitationValidation(valid=[], invalid=["change_units must be a list"], missing=[])
    citations: list[Citation] = []
    invalid: list[str] = []
    missing: list[str] = []
    semantic_judge = _semantic_support_judge()
    for unit_index, unit in enumerate(raw_units, start=1):
        if not isinstance(unit, dict):
            invalid.append(f"change_unit {unit_index}: not an object")
            continue
        for field in ("code", "referenced_symbols", "callers", "contracts_touched", "local_convention_refs"):
            field_citations = extract_location_citations(unit.get(field))
            if field in {"referenced_symbols", "callers", "contracts_touched"} and unit.get(field) and not field_citations:
                missing.append(f"change_unit {unit_index}.{field}: missing path:line")
            for citation in field_citations:
                citation.claim_id = f"change_unit:{unit_index}:{field}"
                citations.append(citation)
            if field in {"referenced_symbols", "callers", "local_convention_refs"}:
                invalid.extend(
                    validate_claim_support(
                        root,
                        unit.get(field),
                        field_citations,
                        label=f"change_unit {unit_index}.{field}",
                        semantic_judge=semantic_judge,
                        content_resolver=content_resolver,
                    )
                )
    validation = validate_citations(root, citations, content_resolver=content_resolver)
    return CitationValidation(valid=validation.valid, invalid=[*invalid, *validation.invalid], missing=[*missing, *validation.missing])


def _post_inline_review_comments(
    root: Path,
    preflight: dict[str, Any],
    findings_payload: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_dir = root / preflight["paths"]["run_dir"]
    posted_path = run_dir / "posted-review.json"
    dry_run_path = run_dir / "inline-review-dry-run.json"
    payload_path = run_dir / "inline-review-payload.json"
    findings = findings_payload.get("findings")
    if posted_path.exists() and not dry_run:
        try:
            posted = json.loads(posted_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise _ReviewPreflightError(f"{_rel_to_root(posted_path, root)} is invalid JSON; remove it to retry") from exc
        if posted.get("status") == "posted":
            return {
                "status": "already_posted",
                "url": str(posted.get("url") or posted.get("html_url") or ""),
                "path": _rel_to_root(posted_path, root),
            }
        if posted.get("status") == "no_findings" and (not isinstance(findings, list) or not findings):
            return {**posted, "path": _rel_to_root(posted_path, root)}
        if posted.get("status") not in {"no_findings"}:
            raise _ReviewPreflightError(
                f"{_rel_to_root(posted_path, root)} has unsupported status; remove it to retry"
            )

    pr_number = _preflight_pr_number(preflight)
    if pr_number is None:
        raise _ReviewPreflightError("active review is not bound to a GitHub PR; rerun with `agentpack review --pr <number>`")
    repo_slug = _preflight_repo_slug(root, preflight)
    if not repo_slug:
        raise _ReviewPreflightError("could not determine GitHub repository for the active PR")
    head_sha = str(preflight.get("git", {}).get("head_sha") or "").strip()
    if not head_sha:
        raise _ReviewPreflightError("active review is missing the PR head SHA")

    if not isinstance(findings, list) or not findings:
        record = {
            "status": "dry_run" if dry_run else "no_findings",
            "run_id": preflight["review"]["run_id"],
            "checked_at": _now_iso() if dry_run else None,
            "posted_at": None if dry_run else _now_iso(),
            "repo": repo_slug,
            "pr": pr_number,
            "head_sha": head_sha,
            "comments": 0,
        }
        record = {key: value for key, value in record.items() if value is not None}
        output_path = dry_run_path if dry_run else posted_path
        _atomic_write(output_path, json.dumps(record, indent=2) + "\n")
        return {**record, "path": _rel_to_root(output_path, root)}

    commentable_lines = _commentable_right_lines(root, str(preflight.get("diff", {}).get("range") or ""))
    comments, skipped, non_inline_notes = _findings_to_inline_comments(findings, commentable_lines)
    has_review_content = bool(comments or non_inline_notes)
    if not has_review_content:
        detail = "; ".join(skipped[:5]) + ("; ..." if len(skipped) > 5 else "")
        raise _ReviewPreflightError(f"findings TOON produced no postable review content: {detail}")

    review_body = _review_body(preflight, len(comments), non_inline_notes)
    request = {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": review_body,
    }
    if comments:
        request["comments"] = comments
    request_hash = _stable_json_hash(request)
    payload_record = {
        "repo": repo_slug,
        "pr": pr_number,
        "endpoint": f"repos/{repo_slug}/pulls/{pr_number}/reviews",
        "payload_sha256": request_hash,
        "payload": request,
    }
    _atomic_write(payload_path, json.dumps(payload_record, indent=2) + "\n")
    dry_run_record = {
        "status": "dry_run",
        "run_id": preflight["review"]["run_id"],
        "checked_at": _now_iso(),
        "repo": repo_slug,
        "pr": pr_number,
        "head_sha": head_sha,
        "comments": len(comments),
        "non_inline_findings": len(non_inline_notes),
        "request_payload": _rel_to_root(payload_path, root),
        "payload_sha256": request_hash,
    }
    _atomic_write(dry_run_path, json.dumps(dry_run_record, indent=2) + "\n")
    if dry_run:
        return {**dry_run_record, "path": _rel_to_root(dry_run_path, root)}

    _require_matching_dry_run(root, dry_run_path, payload_path, request_hash)
    response = _post_pull_request_review(root, repo_slug, pr_number, request)
    posted = {
        "status": "posted",
        "run_id": preflight["review"]["run_id"],
        "posted_at": _now_iso(),
        "repo": repo_slug,
        "pr": pr_number,
        "head_sha": head_sha,
        "comments": len(comments),
        "non_inline_findings": len(non_inline_notes),
        "url": str(response.get("html_url") or response.get("url") or ""),
        "id": response.get("id"),
        "request_payload": _rel_to_root(payload_path, root),
        "payload_sha256": request_hash,
    }
    _atomic_write(posted_path, json.dumps(posted, indent=2) + "\n")
    return {**posted, "path": _rel_to_root(posted_path, root)}


def _stable_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_matching_dry_run(root: Path, dry_run_path: Path, payload_path: Path, request_hash: str) -> None:
    if not dry_run_path.exists():
        raise _ReviewPreflightError(
            "live inline review post requires a fresh dry run first; run `agentpack review --check --dry-run-post`"
        )
    try:
        dry_run = json.loads(dry_run_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _ReviewPreflightError(f"{_rel_to_root(dry_run_path, root)} is invalid JSON; rerun --dry-run-post") from exc
    if dry_run.get("status") != "dry_run":
        raise _ReviewPreflightError(f"{_rel_to_root(dry_run_path, root)} is not a dry-run record; rerun --dry-run-post")
    if dry_run.get("payload_sha256") != request_hash:
        raise _ReviewPreflightError("inline review payload changed since dry run; rerun `agentpack review --check --dry-run-post`")
    if dry_run.get("request_payload") != _rel_to_root(payload_path, root):
        raise _ReviewPreflightError("inline review dry run points at a different payload file; rerun --dry-run-post")


def _preflight_pr_number(preflight: dict[str, Any]) -> int | None:
    for source in (preflight.get("review", {}).get("target"), preflight.get("pr")):
        if not isinstance(source, dict):
            continue
        try:
            number = int(source.get("number") or 0)
        except (TypeError, ValueError):
            number = 0
        if number > 0:
            return number
    return None


def _preflight_repo_slug(root: Path, preflight: dict[str, Any]) -> str:
    for source in (preflight.get("pr"), preflight.get("review", {}).get("target")):
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "")
        match = re.search(r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?(?:/pull/\d+)?/?$", url)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    if shutil.which("gh") is None:
        return ""
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _commentable_right_lines(root: Path, diff_range: str) -> dict[str, set[int]]:
    if not diff_range:
        return {}
    result = subprocess.run(
        ["git", "diff", "--unified=0", diff_range],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    return _parse_commentable_right_lines(result.stdout)


def _parse_commentable_right_lines(diff_text: str) -> dict[str, set[int]]:
    commentable: dict[str, set[int]] = {}
    current_path = ""
    new_line: int | None = None
    for line in diff_text.splitlines():
        diff_match = _DIFF_GIT_RE.match(line)
        if diff_match:
            current_path = diff_match.group("new")
            new_line = None
            continue
        if line.startswith("+++ b/"):
            current_path = line.removeprefix("+++ b/")
            new_line = None
            continue
        if line.startswith("+++ /dev/null"):
            current_path = ""
            new_line = None
            continue
        hunk_match = _DIFF_HUNK_RE.match(line)
        if hunk_match:
            try:
                new_line = int(hunk_match.group("new_start"))
            except ValueError:
                new_line = None
            continue
        if not current_path or new_line is None or line.startswith("\\"):
            continue
        if line.startswith("-") and not line.startswith("---"):
            continue
        if line.startswith("+") and line.startswith("+++"):
            continue
        if new_line > 0:
            commentable.setdefault(current_path, set()).add(new_line)
        new_line += 1
    return commentable


def _findings_to_inline_comments(
    findings: list[Any],
    commentable_lines: dict[str, set[int]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    comments: list[dict[str, Any]] = []
    skipped: list[str] = []
    non_inline_notes: list[str] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            reason = f"finding {index}: not an object"
            skipped.append(reason)
            non_inline_notes.append(f"- {reason}")
            continue
        location = parse_location(str(finding.get("location") or ""))
        if location is None or location.start_line is None:
            reason = f"finding {index}: missing valid location path:line"
            skipped.append(reason)
            non_inline_notes.append(_non_inline_finding_note(finding, index, reason))
            continue
        path = location.path
        line = int(location.start_line)
        if line not in commentable_lines.get(path, set()):
            reason = f"finding {index}: {path}:{line} is not in the PR diff as a right-side line"
            skipped.append(reason)
            non_inline_notes.append(_non_inline_finding_note(finding, index, reason))
            continue
        comments.append(
            {
                "path": path,
                "line": line,
                "side": "RIGHT",
                "body": _inline_comment_body(finding, index),
            }
        )
    return comments, skipped, non_inline_notes


def _non_inline_finding_note(finding: dict[str, Any], index: int, reason: str) -> str:
    finding_id = _comment_field(finding, "id") or f"finding-{index}"
    location = _comment_field(finding, "location") or "unknown location"
    claim = _comment_field(finding, "claim") or "Review finding."
    evidence = _comment_field(finding, "evidence")
    direction = _comment_field(finding, "direction")
    parts = [f"- Finding {_inline_code(finding_id)} at {_inline_code(location)}: {claim}", f"Reason: {reason}"]
    if evidence:
        parts.append(f"Evidence: {evidence}")
    if direction:
        parts.append(f"Suggested next step: {direction}")
    return "\n  ".join(parts)


def _inline_comment_body(finding: dict[str, Any], index: int) -> str:
    severity = _comment_field(finding, "severity") or "finding"
    claim = _comment_field(finding, "claim") or "Review finding."
    evidence = _comment_field(finding, "evidence")
    parts = [
        _AGENTPACK_REVIEW_BADGE,
        f"**{_display_severity(severity)}**",
        claim,
    ]
    if evidence:
        parts.append(f"Evidence: {evidence}")
    next_step = _inline_comment_next_step(finding, severity)
    if next_step:
        parts.append(f"Suggested next step: {next_step}")
    parts.append(_inline_comment_metadata(finding, index, severity))
    return _clip_text("\n\n".join(parts), _GITHUB_REVIEW_BODY_SAFE_LIMIT)


def _comment_field(finding: dict[str, Any], field: str) -> str:
    value = finding.get(field)
    if value is None:
        return ""
    text = " ".join(str(value).split()).strip()
    return "" if text.lower() in {"null", "none", "n/a"} else text


def _display_severity(severity: str) -> str:
    return {
        "blocker": "Blocker",
        "should-fix": "Should fix",
        "nit": "Nit",
        "finding": "Finding",
    }.get(severity, severity.replace("-", " ").strip().title() or "Finding")


def _inline_comment_next_step(finding: dict[str, Any], severity: str) -> str:
    direction = _comment_field(finding, "direction")
    if direction:
        return direction
    if severity == "blocker":
        return "Resolve this before merge, or confirm the cited invariant already makes it safe."
    if severity == "should-fix":
        return "Fix this path, or leave a note explaining why the current behavior is intentional."
    if severity == "nit":
        return "Consider this if it improves clarity without expanding the change."
    return "Review the cited path and decide whether a code or test update is needed."


def _inline_comment_metadata(finding: dict[str, Any], index: int, severity: str) -> str:
    finding_id = _comment_field(finding, "id") or f"finding-{index}"
    metadata = [f"Finding {_inline_code(finding_id)}", f"severity: {_inline_code(severity)}"]
    for field in ("category", "confidence", "lens", "type"):
        value = _comment_field(finding, field)
        if value:
            metadata.append(f"{field}: {_inline_code(value)}")
    return "<details><summary>Review metadata</summary>\n\n" + " | ".join(metadata) + "\n\n</details>"


def _inline_code(value: str) -> str:
    safe = value.replace("`", "'")
    return f"`{safe}`"


def _review_body(preflight: dict[str, Any], comment_count: int, non_inline_notes: list[str] | None = None) -> str:
    run_id = preflight.get("review", {}).get("run_id", "")
    head_sha = str(preflight.get("git", {}).get("head_sha") or "").strip()
    non_inline_notes = non_inline_notes or []
    non_inline_count = len(non_inline_notes)
    total = comment_count + non_inline_count
    finding_word = "finding" if total == 1 else "findings"
    if comment_count and non_inline_count:
        parts = [
            f"AgentPack found {total} evidence-backed {finding_word}: {comment_count} inline and {non_inline_count} in this review body."
        ]
    elif non_inline_count:
        parts = [
            f"AgentPack found {non_inline_count} evidence-backed {finding_word} that could not be attached inline, so included them in this review body."
        ]
    else:
        pronoun = "it" if comment_count == 1 else "them"
        where = "it applies" if comment_count == 1 else "they apply"
        parts = [
            f"AgentPack found {comment_count} evidence-backed {finding_word} and left {pronoun} inline where {where}."
        ]
    if run_id:
        parts.append(f"Run: `{run_id}`")
    if head_sha:
        parts.append(f"Head: `{head_sha[:12]}`")
    if non_inline_notes:
        parts.append("## Non-inline findings\n\n" + "\n\n".join(non_inline_notes))
    return _clip_text("\n\n".join(parts), 60_000)


def _post_pull_request_review(root: Path, repo_slug: str, pr_number: int, payload: dict[str, Any]) -> dict[str, Any]:
    if shutil.which("gh") is None:
        raise _ReviewPreflightError("GitHub CLI `gh` is required to post inline review comments")
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo_slug}/pulls/{pr_number}/reviews",
            "--method",
            "POST",
            "--input",
            "-",
        ],
        cwd=root,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"gh api exited {result.returncode}").strip()
        raise _ReviewPreflightError(detail)
    try:
        response = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return response if isinstance(response, dict) else {}


def _clip_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _semantic_support_judge():
    command = os.environ.get("AGENTPACK_CITATION_SEMANTIC_COMMAND", "").strip()
    if not command:
        return None
    try:
        return semantic_support_command_judge(command)
    except ValueError:
        return lambda _payload: "semantic support command is empty"


def _validation_root(path: Path) -> Path:
    for candidate in (path.parent, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    for candidate in (path.parent, *path.parents):
        if candidate.name == ".agentpack":
            return candidate.parent
    return Path.cwd()


def _rel_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)
