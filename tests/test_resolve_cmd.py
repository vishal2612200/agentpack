from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.commands.resolve_cmd import (
    _ResolveError,
    _fetch_review_threads,
    _post_replies,
)
from agentpack.commands.review_cmd import _parse_review_target


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=tmp_path, check=True)
    return tmp_path


def _metadata() -> dict[str, object]:
    return {
        "number": 42,
        "title": "Resolve comments",
        "url": "https://github.com/example/project/pull/42",
        "base_ref": "main",
        "head_ref": "feature/comments",
    }


def _comments() -> list[dict[str, object]]:
    return [
        {
            "id": "101",
            "kind": "inline",
            "thread_id": "thread-1",
            "author": "reviewer",
            "body": "Please handle the changed return value.",
            "location": "src/foo.py:2",
            "path": "src/foo.py",
            "line": 2,
            "start_line": None,
            "side": "RIGHT",
            "commit_id": "head-sha",
            "resolved": False,
            "outdated": False,
            "created_at": "2026-07-18T00:00:00Z",
            "updated_at": "2026-07-18T00:00:00Z",
            "url": "https://github.com/example/project/pull/42#discussion_r101",
            "in_reply_to_id": "",
        }
    ]


def test_resolve_pr_argument_matches_review_slash_command_ux() -> None:
    target, context = _parse_review_target("", "pr 123 focus on compatibility")

    assert target is not None
    assert target["number"] == 123
    assert context == "focus on compatibility"


def _prepare(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr("agentpack.commands.resolve_cmd.review_cmd._gh_pr_metadata", lambda _root, _target: _metadata())
    monkeypatch.setattr(
        "agentpack.commands.resolve_cmd._pr_snapshot",
        lambda _root, _pr: ("example/project", 42, "head-sha"),
    )
    monkeypatch.setattr("agentpack.commands.resolve_cmd._fetch_review_threads", lambda *_args: {"101": {"resolved": False}})
    monkeypatch.setattr(
        "agentpack.commands.resolve_cmd._fetch_comments",
        lambda *_args, **_kwargs: _comments(),
    )
    monkeypatch.chdir(repo)


def test_resolve_writes_comment_snapshot_and_prompt(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    _prepare(repo, monkeypatch)

    result = CliRunner().invoke(app, ["resolve", "--pr", "42"])

    assert result.exit_code == 0, result.output
    preflight = json.loads((repo / ".agentpack" / "resolve-preflight.json").read_text(encoding="utf-8"))
    assert preflight["pr"]["head_sha"] == "head-sha"
    assert preflight["comments"]["actionable_candidates"] == 1
    assert "agentpack resolve --reply" in (repo / ".agentpack" / "resolve.prompt.md").read_text(encoding="utf-8")
    snapshot = json.loads((repo / ".agentpack" / "resolve-comments.json").read_text(encoding="utf-8"))
    assert snapshot[0]["id"] == "101"
    assert snapshot[0]["resolved"] is False
    assert snapshot[0]["outdated"] is False


def test_fetch_review_threads_paginates_threads_and_comments(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    responses = [
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"id": "thread-1", "isResolved": False, "isOutdated": False}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "threads-1"},
                        }
                    }
                }
            }
        },
        {"data": {"node": {"comments": {"nodes": [{"databaseId": 101}], "pageInfo": {"hasNextPage": True, "endCursor": "comments-1"}}}}},
        {"data": {"node": {"comments": {"nodes": [{"databaseId": 102}], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}},
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"id": "thread-2", "isResolved": True, "isOutdated": False}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        },
        {"data": {"node": {"comments": {"nodes": [{"databaseId": 201}], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}},
    ]
    calls: list[list[str]] = []

    def fake_gh(_root, args, *, allow_failure=False):
        calls.append(args)
        return responses.pop(0)

    monkeypatch.setattr("agentpack.commands.resolve_cmd._gh_json", fake_gh)

    threads = _fetch_review_threads(repo, "example/project", 42)

    assert set(threads) == {"101", "102", "201"}
    assert threads["101"]["thread_id"] == "thread-1"
    assert threads["201"]["resolved"] is True
    assert len(calls) == 5
    assert not responses


def test_post_replies_persists_partial_success_for_retry(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".agentpack").mkdir()
    (repo / ".agentpack" / "resolve-comments.json").write_text(
        json.dumps([
            {"id": "101", "kind": "inline"},
            {"id": "102", "kind": "inline"},
        ]),
        encoding="utf-8",
    )
    (repo / ".agentpack" / "resolve-state.json").write_text(
        json.dumps({"run_id": "run-1", "status": "awaiting_replies", "posted": {}}),
        encoding="utf-8",
    )
    preflight = {
        "resolve": {"run_id": "run-1"},
        "pr": {"repository": "example/project", "number": 42},
    }
    payload = {
        "replies": [
            {"comment_id": "101", "body": "Fixed. See src/foo.py:1."},
            {"comment_id": "102", "body": "Fixed. See src/foo.py:1."},
        ]
    }
    responses = [
        {"html_url": "https://github.com/example/project/pull/42#reply-101"},
        {},
    ]
    monkeypatch.setattr("agentpack.commands.resolve_cmd._gh_json", lambda *_args, **_kwargs: responses.pop(0))

    with pytest.raises(_ResolveError, match="comment 102"):
        _post_replies(repo, preflight, payload)

    state = json.loads((repo / ".agentpack" / "resolve-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "replying"
    assert state["posted"] == {"101": "https://github.com/example/project/pull/42#reply-101"}


def test_resolve_reply_requires_cited_plan_and_posts_after_head_check(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    _prepare(repo, monkeypatch)
    runner = CliRunner()
    prepared = runner.invoke(app, ["resolve", "--pr", "42"])
    assert prepared.exit_code == 0, prepared.output
    preflight = json.loads((repo / ".agentpack" / "resolve-preflight.json").read_text(encoding="utf-8"))
    plan_path = repo / preflight["paths"]["plan"]
    replies_path = repo / preflight["paths"]["replies"]
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        "@format toon\n@root resolution_plan\n"
        "comment_plans[]:\n"
        "  -\n"
        "    comment_id: 101\n"
        "    disposition: fix\n"
        "    location: src/foo.py:1\n"
        "    evidence: src/foo.py:1 defines foo\n"
        "    plan: preserve the return contract\n",
        encoding="utf-8",
    )
    replies_path.write_text(
        "@format toon\n@root resolution_replies\n"
        "replies[]:\n"
        "  -\n"
        "    comment_id: 101\n"
        "    status: ready_to_reply\n"
        "    body: '**Fixed** Updated src/foo.py:1. Validation: passed.'\n"
        "    citations[]:\n"
        "      - src/foo.py:1\n"
        "    validation: passed\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_gh(_root: Path, args: list[str], *, allow_failure: bool = False):
        calls.append(args)
        if args[0:2] == ["api", "repos/example/project/pulls/42"]:
            return {"head": {"sha": "head-sha"}}
        return {"html_url": "https://github.com/example/project/pull/42#issuecomment-1"}

    monkeypatch.setattr("agentpack.commands.resolve_cmd._gh_json", fake_gh)
    result = runner.invoke(app, ["resolve", "--reply"])

    assert result.exit_code == 0, result.output
    assert any("--method" in call for call in calls)
    state = json.loads((repo / ".agentpack" / "resolve-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "replied"


def test_resolve_reply_blocks_when_pr_head_changes(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    _prepare(repo, monkeypatch)
    runner = CliRunner()
    assert runner.invoke(app, ["resolve", "--pr", "42"]).exit_code == 0
    preflight = json.loads((repo / ".agentpack" / "resolve-preflight.json").read_text(encoding="utf-8"))
    plan_path = repo / preflight["paths"]["plan"]
    replies_path = repo / preflight["paths"]["replies"]
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        "@format toon\n@root resolution_plan\ncomment_plans[]:\n  -\n    comment_id: 101\n    disposition: no-action\n    location: src/foo.py:1\n    evidence: src/foo.py:1 defines foo\n    plan: no code change\n",
        encoding="utf-8",
    )
    replies_path.write_text(
        "@format toon\n@root resolution_replies\nreplies[]:\n  -\n    comment_id: 101\n    status: ready_to_reply\n    body: '**Acknowledged** See src/foo.py:1. Validation: passed.'\n    citations[]:\n      - src/foo.py:1\n    validation: passed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agentpack.commands.resolve_cmd._gh_json",
        lambda *_args, **_kwargs: {"head": {"sha": "new-head-sha"}},
    )

    result = runner.invoke(app, ["resolve", "--reply"])

    assert result.exit_code == 1
    assert "PR head changed" in result.output
