from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import typer

from agentpack.commands import review_cmd
from agentpack.commands._shared import _atomic_write, _now_iso, _root, console
from agentpack.core import git as git_core
from agentpack.core.citations import (
    extract_location_citations,
    parse_location,
    validate_citations,
    validate_claim_support,
)
from agentpack.core.toon_parser import ToonParseError, load_toon

_PREFLIGHT = Path(".agentpack/resolve-preflight.json")
_PROMPT = Path(".agentpack/resolve.prompt.md")
_COMMENTS = Path(".agentpack/resolve-comments.json")
_STATE = Path(".agentpack/resolve-state.json")
_RUNS = Path(".agentpack/resolutions")
class _ResolveError(Exception):
    pass


def register(app: typer.Typer) -> None:
    @app.command("resolve")
    def resolve(
        resolve_context: str = typer.Argument("", help="Optional prioritization context for this PR comment pass."),
        pr_target: str = typer.Option("", "--pr", help="PR number or URL to resolve comments for."),
        resume: str = typer.Option("", "--resume", help="Resume a resolution run by run id."),
        check: bool = typer.Option(False, "--check", help="Validate the active resolution plan/replies."),
        reply: bool = typer.Option(False, "--reply", help="Post validated replies after checking the PR head."),
        max_iterations: int = typer.Option(3, "--max-iterations", min=1, help="Maximum validate/fix/reply passes."),
        inline_only: bool = typer.Option(False, "--inline-only", help="Exclude top-level PR issue comments."),
    ) -> None:
        """Prepare and gate a cited PR comment resolution loop."""
        root = _root()
        if not git_core.is_git_repo(root):
            console.print("[red]agentpack resolve requires a git repository.[/]")
            raise typer.Exit(1)
        if check or reply:
            _check_active(root, reply=reply)
            return

        target, cleaned_context = review_cmd._parse_review_target(pr_target.strip(), resolve_context.strip())
        if resume.strip():
            preflight = _load_run(root, resume.strip())
            _activate_run(root, preflight)
            run_id = preflight["resolve"]["run_id"]
        else:
            try:
                preflight, comments = _build_preflight(
                    root,
                    target,
                    cleaned_context,
                    max_iterations=max_iterations,
                    inline_only=inline_only,
                )
            except _ResolveError as exc:
                console.print(f"[red]Resolution preflight blocked:[/] {exc}")
                raise typer.Exit(1) from exc
            run_id = preflight["resolve"]["run_id"]
            _write_run(root, preflight, comments)

        console.print(f"[green]✓[/] Resolution run id: [bold]{run_id}[/]")
        console.print(f"[green]✓[/] Comments snapshot: [bold]{_COMMENTS}[/]")
        console.print(f"[green]✓[/] Resolution prompt: [bold]{_PROMPT}[/]")
        console.print(f"[green]✓[/] Plan target: [bold]{preflight['paths']['plan']}[/]")
        console.print(f"[green]✓[/] Reply target: [bold]{preflight['paths']['replies']}[/]")
        console.print("Read the prompt, write the cited plan, run `agentpack resolve --check`, then fix, test, write cited replies, and run `agentpack resolve --reply`.")


def _build_preflight(
    root: Path,
    target: dict[str, Any] | None,
    context: str,
    *,
    max_iterations: int,
    inline_only: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pr = review_cmd._gh_pr_metadata(root, target)
    if not pr or not pr.get("number") or not pr.get("url"):
        raise _ResolveError("GitHub PR metadata unavailable; pass --pr <number-or-url> and verify gh auth/network")
    repository, number, head_sha = _pr_snapshot(root, pr)
    threads = _fetch_review_threads(root, repository, number)
    comments = _fetch_comments(root, repository, number, threads, include_issue_comments=not inline_only)
    run_id = review_cmd._new_review_run_id()
    branch_prefix = f"pr-{number}"
    run_dir = root / _RUNS / branch_prefix / run_id
    paths = {
        "run_dir": str(run_dir.relative_to(root)),
        "preflight": str((run_dir / "preflight.json").relative_to(root)),
        "comments": str((run_dir / "comments.json").relative_to(root)),
        "prompt": str((run_dir / "prompt.md").relative_to(root)),
        "plan": str((run_dir / "plan.toon").relative_to(root)),
        "replies": str((run_dir / "replies.toon").relative_to(root)),
        "state": str((run_dir / "state.json").relative_to(root)),
    }
    actionable = [
        comment
        for comment in comments
        if comment.get("thread_state", "known") == "known"
        and not comment.get("resolved")
        and not comment.get("outdated")
    ]
    unknown_thread_states = any(
        comment.get("kind") == "inline" and comment.get("thread_state") == "unknown"
        for comment in comments
    )
    preflight = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "context": context,
        "resolve": {
            "mode": "fresh",
            "run_id": run_id,
            "branch_prefix": branch_prefix,
            "max_iterations": max_iterations,
            "target": {"number": number, "url": pr["url"], "repository": repository},
        },
        "pr": {
            **pr,
            "repository": repository,
            "head_sha": head_sha,
        },
        "comments": {
            "total": len(comments),
            "inline": sum(comment["kind"] == "inline" for comment in comments),
            "issue": sum(comment["kind"] == "issue" for comment in comments),
            "actionable_candidates": len(actionable),
            "thread_lookup": "partial" if unknown_thread_states else "complete" if threads else "unavailable",
        },
        "execution_contract": {
            "phases": ["bind", "snapshot", "validate", "plan", "fix", "verify", "reply", "repeat"],
            "requires_latest_head": True,
            "requires_citation": True,
            "reply_only_after_check": True,
            "all_comments_require_disposition": True,
        },
        "paths": paths,
    }
    preflight["prompt"] = _render_prompt(preflight, comments)
    return preflight, comments


def _write_run(root: Path, preflight: dict[str, Any], comments: list[dict[str, Any]]) -> None:
    run_dir = root / preflight["paths"]["run_dir"]
    comment_text = json.dumps(comments, indent=2, sort_keys=True) + "\n"
    preflight_text = json.dumps(preflight, indent=2, sort_keys=True) + "\n"
    prompt_text = preflight["prompt"]
    artifacts = {
        run_dir / "preflight.json": preflight_text,
        run_dir / "comments.json": comment_text,
        run_dir / "prompt.md": prompt_text,
        root / _PREFLIGHT: preflight_text,
        root / _COMMENTS: comment_text,
        root / _PROMPT: prompt_text,
        root / _STATE: json.dumps(_state(preflight, "awaiting_plan"), indent=2) + "\n",
    }
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, content)


def _render_prompt(preflight: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    target = preflight["pr"]
    return (
        "# AgentPack PR Comment Resolution\n\n"
        "Resolve every actionable inline or PR review comment in one controlled pass. "
        "This is an evidence-first loop: never claim a fix without a source citation and validation result.\n\n"
        "## Binding\n\n"
        f"- PR: #{target['number']} — {target.get('title', '')}\n"
        f"- Repository: `{target['repository']}`\n"
        f"- Head SHA: `{target['head_sha']}`\n"
        f"- Comment snapshot: `{_COMMENTS}`\n"
        f"- Max iterations: {preflight['resolve']['max_iterations']}\n"
        f"- Context lens: {preflight['context'] or '(none)'}\n\n"
        "## Required Loop\n\n"
        "1. Read the complete comment snapshot. Group inline replies into threads and inspect the exact current PR head.\n"
        "2. Validate every comment: actionable, stale, duplicate, no-action, or blocked. Use direct source reads and tests; do not accept a comment as correct only because it sounds plausible.\n"
        "3. Write every comment disposition to the plan TOON. Actionable fixes need a concrete plan, `path:line` location, and evidence citations.\n"
        "4. Run `agentpack resolve --check`. Do not edit code until the plan passes.\n"
        "5. Apply all validated fixes in one pass. Keep unrelated work untouched.\n"
        "6. Run targeted tests, then the relevant project checks. Record exact commands and outcomes.\n"
        "7. Write one reply record per comment. Every reply must be concise, cite the changed source as `path:line`, and state validation as passed, failed, or not run.\n"
        "8. Run `agentpack resolve --reply`. It refuses stale PR heads, missing citations, invalid comments, and duplicate replies in the same run. Issue-kind replies are posted as new top-level PR comments because GitHub does not support threaded replies for issue comments.\n"
        "9. Refresh with a new `agentpack resolve --pr <number>` run and repeat until no actionable unresolved comment remains or the max iteration limit is reached.\n\n"
        "## Safety Rules\n\n"
        "- Do not post a reply before the plan, code changes, citations, and validation pass.\n"
        "- Do not mark a comment fixed when the requested behavior is not verified. Use `blocked` and explain the blocker.\n"
        "- For issue-kind comments, state in the reply that the response is a new top-level PR comment, not a threaded reply.\n"
        "- Do not resolve or hide a thread merely because a reply was posted. Marking GitHub threads resolved is a separate explicit action.\n"
        "- Do not invent line numbers. Use the current PR head and exact file evidence.\n\n"
        "## Plan TOON\n\n"
        f"Write `{preflight['paths']['plan']}` with root `resolution_plan` and `comment_plans[]`; each item needs `comment_id`, `disposition`, `location`, `evidence`, and `plan`.\n\n"
        "## Reply TOON\n\n"
        f"Write `{preflight['paths']['replies']}` with root `resolution_replies` and `replies[]`; each item needs `comment_id`, `status: ready_to_reply`, `body`, `citations[]`, and `validation`.\n\n"
        f"Snapshot contains {len(comments)} comment(s), including {sum(item['kind'] == 'inline' for item in comments)} inline comment(s).\n"
    )


def _pr_snapshot(root: Path, pr: dict[str, Any]) -> tuple[str, int, str]:
    parsed = urlparse(str(pr["url"]))
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[-2] != "pull" or not parts[-1].isdigit():
        raise _ResolveError(f"cannot derive repository from PR URL: {pr['url']}")
    repository = "/".join(parts[:2])
    number = int(parts[-1])
    payload = _gh_json(root, ["api", f"repos/{repository}/pulls/{number}"])
    if not isinstance(payload, dict) or not payload.get("head", {}).get("sha"):
        raise _ResolveError(f"could not read latest head for PR #{number}")
    return repository, number, str(payload["head"]["sha"])


def _fetch_comments(
    root: Path,
    repository: str,
    number: int,
    threads: dict[str, dict[str, Any]],
    *,
    include_issue_comments: bool,
) -> list[dict[str, Any]]:
    raw_review = _gh_paginated(root, ["api", f"repos/{repository}/pulls/{number}/comments", "--paginate"])
    comments = [_normalize_inline(item, threads) for item in raw_review if isinstance(item, dict)]
    if include_issue_comments:
        raw_issue = _gh_paginated(root, ["api", f"repos/{repository}/issues/{number}/comments", "--paginate"])
        comments.extend(_normalize_issue(item) for item in raw_issue if isinstance(item, dict))
    return sorted(comments, key=lambda item: (item.get("created_at", ""), str(item["id"])))


def _fetch_review_threads(root: Path, repository: str, number: int) -> dict[str, dict[str, Any]]:
    owner, repo = repository.split("/", 1)
    result: dict[str, dict[str, Any]] = {}
    threads_cursor: str | None = None
    while True:
        cursor_clause = ", $threadsCursor:String" if threads_cursor else ""
        after_clause = ", after:$threadsCursor" if threads_cursor else ""
        query = f"""
        query($owner:String!, $repo:String!, $number:Int!{cursor_clause}) {{
          repository(owner:$owner, name:$repo) {{
            pullRequest(number:$number) {{
              reviewThreads(first:100{after_clause}) {{
                nodes {{ id isResolved isOutdated }}
                pageInfo {{ hasNextPage endCursor }}
              }}
            }}
          }}
        }}
        """
        args = [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={repo}",
            "-F",
            f"number={number}",
        ]
        if threads_cursor:
            args.extend(["-f", f"threadsCursor={threads_cursor}"])
        payload = _gh_json(root, args, allow_failure=True)
        pull_request = (
            ((payload or {}).get("data") or {}).get("repository", {}).get("pullRequest")
            if isinstance(payload, dict)
            else None
        )
        connection = _graphql_connection(pull_request, "reviewThreads")
        if connection is None:
            break
        for thread in connection["nodes"]:
            if not isinstance(thread, dict) or not thread.get("id"):
                continue
            state = {
                "thread_id": thread["id"],
                "resolved": bool(thread.get("isResolved")),
                "outdated": bool(thread.get("isOutdated")),
            }
            comments_cursor: str | None = None
            while True:
                comments_page = _fetch_thread_comments(root, thread["id"], comments_cursor)
                if comments_page is None:
                    break
                comments, page_info = comments_page
                for comment in comments:
                    if isinstance(comment, dict) and comment.get("databaseId") is not None:
                        result[str(comment["databaseId"])] = state
                if not page_info.get("hasNextPage"):
                    break
                next_cursor = str(page_info.get("endCursor") or "")
                if not next_cursor or next_cursor == comments_cursor:
                    break
                comments_cursor = next_cursor
        page_info = connection["page_info"]
        if not page_info.get("hasNextPage"):
            break
        next_cursor = str(page_info.get("endCursor") or "")
        if not next_cursor or next_cursor == threads_cursor:
            break
        threads_cursor = next_cursor
    return result


def _fetch_thread_comments(
    root: Path,
    thread_id: str,
    cursor: str | None,
) -> tuple[list[Any], dict[str, Any]] | None:
    cursor_clause = ", $commentsCursor:String" if cursor else ""
    after_clause = ", after:$commentsCursor" if cursor else ""
    query = f"""
    query($threadId:ID!{cursor_clause}) {{
      node(id:$threadId) {{
        ... on PullRequestReviewThread {{
          comments(first:100{after_clause}) {{
            nodes {{ databaseId }}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }}
      }}
    }}
    """
    args = ["api", "graphql", "-f", f"query={query}", "-f", f"threadId={thread_id}"]
    if cursor:
        args.extend(["-f", f"commentsCursor={cursor}"])
    payload = _gh_json(root, args, allow_failure=True)
    if not isinstance(payload, dict):
        return None
    node = (payload.get("data") or {}).get("node")
    connection = _graphql_connection(node, "comments")
    if connection is None:
        return None
    return connection["nodes"], connection["page_info"]


def _graphql_connection(payload: Any, name: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    connection = payload.get(name)
    if not isinstance(connection, dict):
        return None
    nodes = connection.get("nodes")
    page_info = connection.get("pageInfo")
    if not isinstance(nodes, list) or not isinstance(page_info, dict):
        return None
    return {"nodes": nodes, "page_info": page_info}


def _normalize_inline(item: dict[str, Any], threads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    comment_id = str(item.get("id", ""))
    thread = threads.get(comment_id)
    thread_data = thread or {}
    line = item.get("line") or item.get("original_line")
    location = f"{item.get('path')}:{line}" if item.get("path") and line else str(item.get("path") or "")
    return {
        "id": comment_id,
        "kind": "inline",
        "thread_id": thread_data.get("thread_id", ""),
        "thread_state": "known" if thread is not None else "unknown",
        "in_reply_to_id": str(item.get("in_reply_to_id") or ""),
        "author": (item.get("user") or {}).get("login", ""),
        "body": str(item.get("body") or ""),
        "location": location,
        "path": str(item.get("path") or ""),
        "line": line,
        "start_line": item.get("start_line") or item.get("original_start_line"),
        "side": item.get("side") or item.get("original_side") or "",
        "commit_id": str(item.get("commit_id") or ""),
        "resolved": bool(thread_data.get("resolved", False)),
        "outdated": bool(thread_data.get("outdated", False)),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "url": str(item.get("html_url") or item.get("url") or ""),
    }


def _normalize_issue(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")),
        "kind": "issue",
        "thread_id": "",
        "in_reply_to_id": "",
        "author": (item.get("user") or {}).get("login", ""),
        "body": str(item.get("body") or ""),
        "location": "",
        "path": "",
        "line": None,
        "start_line": None,
        "side": "",
        "commit_id": "",
        "resolved": False,
        "outdated": False,
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "url": str(item.get("html_url") or item.get("url") or ""),
    }


def _gh_json(root: Path, args: list[str], *, allow_failure: bool = False) -> Any:
    if shutil.which("gh") is None:
        if allow_failure:
            return None
        raise _ResolveError("gh is not installed")
    result = subprocess.run(["gh", *args], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        if allow_failure:
            return None
        raise _ResolveError((result.stderr or result.stdout or "gh command failed").strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        if allow_failure:
            return None
        raise _ResolveError(f"gh returned invalid JSON: {exc}") from exc


def _gh_paginated(root: Path, args: list[str]) -> list[Any]:
    if shutil.which("gh") is None:
        raise _ResolveError("gh is not installed")
    result = subprocess.run(["gh", *args], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise _ResolveError((result.stderr or result.stdout or "gh command failed").strip())
    decoder = json.JSONDecoder()
    values: list[Any] = []
    position = 0
    while position < len(result.stdout):
        while position < len(result.stdout) and result.stdout[position].isspace():
            position += 1
        if position >= len(result.stdout):
            break
        value, position = decoder.raw_decode(result.stdout, position)
        values.extend(value if isinstance(value, list) else [value])
    return values


def _load_run(root: Path, run_id: str) -> dict[str, Any]:
    matches = sorted((root / _RUNS).glob(f"*/{run_id}/preflight.json"))
    if len(matches) != 1:
        console.print(f"[red]Resolution run not found:[/] {run_id}")
        raise typer.Exit(1)
    try:
        return json.loads(matches[0].read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Resolution preflight is invalid JSON:[/] {exc}")
        raise typer.Exit(1) from exc


def _activate_run(root: Path, preflight: dict[str, Any]) -> None:
    for key, active in (("preflight", _PREFLIGHT), ("comments", _COMMENTS), ("prompt", _PROMPT), ("state", _STATE)):
        source = root / preflight["paths"][key]
        if source.exists():
            _atomic_write(root / active, source.read_text(encoding="utf-8"))


def _check_active(root: Path, *, reply: bool) -> None:
    preflight_path = root / _PREFLIGHT
    if not preflight_path.exists():
        console.print("[red]No active resolution preflight found.[/] Run `agentpack resolve --pr <number>` first.")
        raise typer.Exit(1)
    preflight: dict[str, Any] = {}
    try:
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        plan = _validate_toon(root / preflight["paths"]["plan"], "plan")
    except (OSError, json.JSONDecodeError, ValueError, ToonParseError) as exc:
        _write_state(root, preflight, "blocked", str(exc))
        console.print(f"[red]Resolution check blocked:[/] {exc}")
        raise typer.Exit(1) from exc
    if not reply:
        _write_state(root, preflight, "awaiting_replies")
        console.print(f"[green]✓[/] Resolution plan valid for {len(plan['comment_plans'])} comment(s). Fix and verify before writing replies.")
        return
    try:
        replies = _validate_toon(root / preflight["paths"]["replies"], "replies")
        _assert_head_unchanged(root, preflight)
        results = _post_replies(root, preflight, replies)
    except (OSError, json.JSONDecodeError, ValueError, ToonParseError, _ResolveError) as exc:
        _write_state(root, preflight, "blocked", str(exc), _load_posted_state(root))
        console.print(f"[red]Resolution reply blocked:[/] {exc}")
        raise typer.Exit(1) from exc
    _write_state(root, preflight, "replied", "", results)
    console.print(f"[green]✓[/] Posted {len(results)} cited PR comment reply/replies.")
    console.print("Start a fresh `agentpack resolve --pr <number>` pass to resnapshot remaining comments.")


def _validate_toon(path: Path, kind: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"missing {kind} artifact: {path}")
    payload = load_toon(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must decode to an object")
    if kind == "plan":
        if not isinstance(payload.get("comment_plans"), list):
            raise ValueError("plan must contain comment_plans[]")
        _validate_plan_items(path, payload["comment_plans"], _snapshot_ids(path))
    else:
        if not isinstance(payload.get("replies"), list):
            raise ValueError("replies must contain replies[]")
        _validate_reply_items(path, payload["replies"], _snapshot_ids(path))
    return payload


def _snapshot_ids(path: Path) -> set[str]:
    snapshot = _validation_root(path) / _COMMENTS
    try:
        comments = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"comment snapshot is unavailable: {snapshot}") from exc
    ids = [str(comment.get("id") or "") for comment in comments if isinstance(comment, dict)]
    if any(not comment_id for comment_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("comment snapshot must contain unique comment ids")
    return set(ids)


def _validate_plan_items(path: Path, items: list[Any], expected_ids: set[str]) -> None:
    citations = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"comment plan {index} is not an object")
        comment_id = str(item.get("comment_id") or "").strip()
        if not comment_id:
            raise ValueError(f"comment plan {index} missing comment_id")
        if comment_id in seen_ids:
            raise ValueError(f"comment plan {index} duplicates comment_id {comment_id}")
        seen_ids.add(comment_id)
        if str(item.get("disposition") or "") not in {"fix", "no-action", "stale", "duplicate", "blocked"}:
            raise ValueError(f"comment plan {index} has invalid disposition")
        item_citations = [parse_location(str(item.get("location") or "")), *extract_location_citations(item.get("evidence"))]
        item_citations = [citation for citation in item_citations if citation]
        for citation in item_citations:
            if citation:
                citation.claim_id = f"comment_plan:{index}"
                citations.append(citation)
        if not item_citations:
            raise ValueError(f"comment plan {index} needs location/evidence citations")
        invalid = validate_claim_support(
            _validation_root(path),
            item.get("evidence"),
            item_citations,
            label=f"comment plan {index}.evidence",
        )
        if invalid:
            raise ValueError("; ".join(invalid))
    if seen_ids != expected_ids:
        raise ValueError(f"comment plan ids must cover snapshot exactly; missing={sorted(expected_ids - seen_ids)}, extra={sorted(seen_ids - expected_ids)}")
    validation = validate_citations(_validation_root(path), citations)
    if validation.invalid or validation.missing:
        raise ValueError("; ".join([*validation.invalid, *validation.missing]))


def _validate_reply_items(path: Path, items: list[Any], expected_ids: set[str]) -> None:
    citations = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"reply {index} is not an object")
        comment_id = str(item.get("comment_id") or "").strip()
        if not comment_id or item.get("status") != "ready_to_reply":
            raise ValueError(f"reply {index} needs comment_id and status: ready_to_reply")
        if comment_id in seen_ids:
            raise ValueError(f"reply {index} duplicates comment_id {comment_id}")
        seen_ids.add(comment_id)
        body = str(item.get("body") or "").strip()
        if not body:
            raise ValueError(f"reply {index} has empty body")
        item_citations = extract_location_citations(item.get("citations")) or extract_location_citations(body)
        if not item_citations:
            raise ValueError(f"reply {index} needs a path:line citation")
        for citation in item_citations:
            citation.claim_id = f"reply:{index}"
            citations.append(citation)
    if seen_ids != expected_ids:
        raise ValueError(f"reply ids must cover snapshot exactly; missing={sorted(expected_ids - seen_ids)}, extra={sorted(seen_ids - expected_ids)}")
    validation = validate_citations(_validation_root(path), citations)
    if validation.invalid or validation.missing:
        raise ValueError("; ".join([*validation.invalid, *validation.missing]))


def _assert_head_unchanged(root: Path, preflight: dict[str, Any]) -> None:
    repository = preflight["pr"]["repository"]
    number = preflight["pr"]["number"]
    payload = _gh_json(root, ["api", f"repos/{repository}/pulls/{number}"])
    current = str((payload or {}).get("head", {}).get("sha") or "")
    if not current or current != preflight["pr"]["head_sha"]:
        raise _ResolveError("PR head changed since the plan; start a fresh resolve run")


def _post_replies(root: Path, preflight: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
    comments = {comment["id"]: comment for comment in json.loads((root / _COMMENTS).read_text(encoding="utf-8"))}
    state_path = root / _STATE
    try:
        posted: dict[str, str] = json.loads(state_path.read_text(encoding="utf-8")).get("posted", {})
    except (OSError, json.JSONDecodeError):
        posted = {}
    run_id = preflight["resolve"]["run_id"]
    for reply in payload["replies"]:
        comment_id = str(reply["comment_id"])
        if comment_id in posted:
            continue
        comment = comments.get(comment_id)
        if not comment:
            raise _ResolveError(f"reply references comment not in snapshot: {comment_id}")
        marker = f"<!-- agentpack-resolve:{run_id}:{comment_id} -->"
        body = f"{str(reply['body']).rstrip()}\n\n{marker}"
        if comment["kind"] == "inline":
            endpoint = f"repos/{preflight['pr']['repository']}/pulls/{preflight['pr']['number']}/comments/{comment_id}/replies"
        else:
            endpoint = f"repos/{preflight['pr']['repository']}/issues/{preflight['pr']['number']}/comments"
            body = (
                f"{body}\n\n"
                "_This is a new top-level PR comment because GitHub does not support threaded replies for issue comments._"
            )
        response = _gh_json(root, ["api", endpoint, "--method", "POST", "--field", f"body={body}"])
        if not isinstance(response, dict) or not response.get("html_url"):
            raise _ResolveError(f"GitHub did not return a reply URL for comment {comment_id}")
        posted[comment_id] = str(response["html_url"])
        _write_state(root, preflight, "replying", f"posted reply for comment {comment_id}; continuing", posted)
    return posted


def _load_posted_state(root: Path) -> dict[str, str]:
    try:
        payload = json.loads((root / _STATE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    posted = payload.get("posted", {})
    return {str(key): str(value) for key, value in posted.items()} if isinstance(posted, dict) else {}


def _state(preflight: dict[str, Any], status: str, detail: str = "", posted: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "run_id": preflight.get("resolve", {}).get("run_id", ""),
        "status": status,
        "detail": detail,
        "posted": posted or {},
        "updated_at": _now_iso(),
    }


def _write_state(root: Path, preflight: dict[str, Any], status: str, detail: str = "", posted: dict[str, str] | None = None) -> None:
    content = json.dumps(_state(preflight, status, detail, posted), indent=2) + "\n"
    targets = [root / _STATE]
    if preflight.get("paths", {}).get("state"):
        targets.append(root / preflight["paths"]["state"])
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, content)


def _validation_root(path: Path) -> Path:
    for candidate in (path.parent, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return Path.cwd()
