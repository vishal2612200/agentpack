from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.commands.review_cmd import (
    _build_review_preflight,
    _findings_to_inline_comments,
    _load_review_template,
    _parse_review_target,
    _parse_commentable_right_lines,
    _review_output_paths,
    _validate_review_artifact,
)


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-m", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_foo.py").write_text("def test_foo():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True)

    subprocess.run(["git", "checkout", "-b", "feature/review"], cwd=tmp_path, check=True)
    (tmp_path / "src" / "foo.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/foo.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "change foo"], cwd=tmp_path, check=True)
    return tmp_path


def test_build_review_preflight_uses_pr_base_and_related_tests(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(
        "agentpack.commands.review_cmd._gh_pr_metadata",
        lambda _root, _target=None: {
            "number": 6,
            "title": "Review flow",
            "url": "https://example.com/pr/6",
            "base_ref": "main",
            "head_ref": "feature/review",
        },
    )
    monkeypatch.setattr("agentpack.commands.review_cmd._fetch_pr_refs", lambda _root, _number, _base: {"ok": True, "error": ""})
    monkeypatch.setattr("agentpack.commands.review_cmd._rev_parse", lambda _root, _ref: "pr-head-sha")
    monkeypatch.setattr("agentpack.commands.review_cmd._changed_paths", lambda _root, _range: ["src/foo.py"])

    target = {"raw": "6", "number": 6, "url": "", "source": "option"}
    outputs = _review_output_paths(repo, branch_prefix="pr-6")
    preflight = _build_review_preflight(repo, "focus on backward compatibility", outputs, target=target)

    assert preflight["review_context"] == "focus on backward compatibility"
    assert preflight["review"]["mode"] == "fresh"
    assert preflight["review"]["branch_prefix"] == "pr-6"
    assert preflight["review"]["target"] == {"raw": "6", "number": 6, "url": "https://example.com/pr/6", "source": "option"}
    assert preflight["execution_contract"] == {
        "structured_format": "JSON or TOON",
        "canonical_format": "TOON",
        "requires_write_to_file": True,
        "requires_read_file_between_stages": True,
        "forbid_inline_review": True,
        "blocked_without_stage_artifact": True,
        "stage_order": ["understanding", "judge"],
    }
    assert preflight["git"]["head_sha"] == "pr-head-sha"
    assert preflight["citation_source"]["mode"] == "git-head"
    assert preflight["review"]["scaffold"] == "light"
    assert preflight["diff"]["base_ref"] == "origin/main"
    assert preflight["diff"]["head_ref"] == "origin/pr/6"
    assert preflight["diff"]["range"] == "origin/main...origin/pr/6"
    assert preflight["diff"]["source"] == "pr-target"
    assert preflight["paths"]["run_dir"].startswith(".agentpack/reviews/pr-6/")
    assert preflight["paths"]["understanding_output"].startswith(".agentpack/reviews/pr-6/")
    assert preflight["paths"]["findings_output"].startswith(".agentpack/reviews/pr-6/")
    assert preflight["changed_files"] == [
        {
            "path": "src/foo.py",
            "head_blob_sha": "",
            "related_tests": ["tests/test_foo.py"],
        }
    ]
    assert preflight["warnings"] == []


def test_parse_review_target_from_url_and_preserves_lens() -> None:
    target, lens = _parse_review_target("", "https://github.com/acme/repo/pull/98 focus latency")

    assert target == {
        "raw": "https://github.com/acme/repo/pull/98",
        "number": 98,
        "url": "https://github.com/acme/repo/pull/98",
        "source": "argument",
    }
    assert lens == "focus latency"


def test_review_command_blocks_without_pr_or_explicit_local_fallback(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)

    result = CliRunner().invoke(app, ["review", "reviewer is worried about prompt latency"])

    assert result.exit_code == 1
    assert "Review preflight blocked" in result.output
    assert "--allow-local-fallback" in result.output


def test_review_command_explicit_pr_binds_diff_and_run_dir(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    seen_targets = []

    def fake_gh(_root, target=None):
        seen_targets.append(target)
        return {
            "number": 98,
            "title": "Load test",
            "url": "https://example.com/pr/98",
            "base_ref": "main",
            "head_ref": "feature/load-test",
        }

    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", fake_gh)
    monkeypatch.setattr("agentpack.commands.review_cmd._fetch_pr_refs", lambda _root, _number, _base: {"ok": True, "error": ""})
    monkeypatch.setattr("agentpack.commands.review_cmd._rev_parse", lambda _root, _ref: "abc123")
    monkeypatch.setattr("agentpack.commands.review_cmd._changed_paths", lambda _root, _range: ["src/foo.py"])
    monkeypatch.setattr(
        "agentpack.commands.review_cmd._build_review_context_pack",
        lambda _root, _review_context, _diff_info, _outputs, _warnings: {
            "path": "",
            "tokens": 0,
            "selected_files": 0,
            "broad_context": False,
        },
    )

    result = CliRunner().invoke(app, ["review", "--pr", "98", "focus latency"])

    assert result.exit_code == 0, result.output
    assert seen_targets[0]["number"] == 98
    preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))
    assert preflight["review_context"] == "focus latency"
    assert preflight["review"]["branch_prefix"] == "pr-98"
    assert preflight["review"]["target"]["number"] == 98
    assert preflight["diff"]["range"] == "origin/main...origin/pr/98"
    assert preflight["diff"]["source"] == "pr-target"
    assert preflight["paths"]["run_dir"].startswith(".agentpack/reviews/pr-98/")
    assert (repo / ".agentpack" / "review-state.json").exists()


def test_review_command_explicit_pr_fetch_failure_blocks_without_fallback(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "agentpack.commands.review_cmd._gh_pr_metadata",
        lambda _root, _target=None: {
            "number": 98,
            "title": "Load test",
            "url": "https://example.com/pr/98",
            "base_ref": "main",
            "head_ref": "feature/load-test",
        },
    )
    monkeypatch.setattr("agentpack.commands.review_cmd._fetch_pr_refs", lambda _root, _number, _base: {"ok": False, "error": "no network"})

    result = CliRunner().invoke(app, ["review", "--pr", "98", "focus latency"])

    assert result.exit_code == 1
    assert "could not fetch PR #98 refs" in result.output
    assert "--allow-local-fallback" in result.output


def test_review_command_supports_strict_and_light_scaffolds(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)
    monkeypatch.setattr(
        "agentpack.commands.review_cmd._build_review_context_pack",
        lambda _root, _review_context, _diff_info, _outputs, _warnings: {
            "path": "",
            "tokens": 0,
            "selected_files": 0,
            "broad_context": False,
        },
    )
    runner = CliRunner()

    strict = runner.invoke(app, ["review", "--allow-local-fallback", "--strict", "small docs review"])

    assert strict.exit_code == 0, strict.output
    strict_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))
    assert strict_preflight["review"]["scaffold"] == "strict"
    assert "Review scaffold: strict" in (repo / ".agentpack" / "review.prompt.md").read_text(encoding="utf-8")

    light = runner.invoke(app, ["review", "--allow-local-fallback", "--light", "security token review"])

    assert light.exit_code == 0, light.output
    light_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))
    assert light_preflight["review"]["scaffold"] == "light"

    conflict = runner.invoke(app, ["review", "--allow-local-fallback", "--strict", "--light", "conflict"])

    assert conflict.exit_code == 1
    assert "Use only one of --strict or --light" in conflict.output


def test_review_command_writes_run_scoped_bundle_and_active_aliases(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)

    result = CliRunner().invoke(app, ["review", "--allow-local-fallback", "reviewer is worried about prompt latency"])

    assert result.exit_code == 0, result.output
    preflight_path = repo / ".agentpack" / "review-preflight.json"
    runbook_path = repo / ".agentpack" / "review.prompt.md"
    understanding_prompt_path = repo / ".agentpack" / "review-understanding.prompt.md"
    judge_prompt_path = repo / ".agentpack" / "review-judge.prompt.md"
    assert preflight_path.exists()
    assert runbook_path.exists()
    assert understanding_prompt_path.exists()
    assert judge_prompt_path.exists()

    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    run_dir = repo / preflight["paths"]["run_dir"]
    assert preflight["review_context"] == "reviewer is worried about prompt latency"
    assert preflight["diff"]["range"] == "HEAD~1..HEAD"
    assert preflight["warnings"][0] == "gh PR metadata unavailable; review is using local git context only"
    assert run_dir.exists()
    assert (run_dir / "preflight.json").exists()
    assert (run_dir / "runbook.md").exists()
    assert (run_dir / "understanding.prompt.md").exists()
    assert (run_dir / "judge.prompt.md").exists()
    assert (run_dir / "understanding.template.toon").exists()
    assert (run_dir / "findings.template.toon").exists()
    assert (run_dir / "context.md").exists()
    assert (run_dir / "citations.json").exists()
    assert (repo / ".agentpack" / "review-understanding.template.toon").exists()
    assert (repo / ".agentpack" / "review-findings.template.toon").exists()
    assert preflight["context_pack"]["path"].startswith(".agentpack/reviews/feature-review/")
    assert not (repo / ".agentpack" / "context.md").exists()
    assert preflight["paths"]["understanding_output"].startswith(".agentpack/reviews/feature-review/")
    assert preflight["paths"]["findings_output"].startswith(".agentpack/reviews/feature-review/")
    assert preflight["paths"]["understanding_template"].startswith(".agentpack/reviews/feature-review/")
    assert preflight["paths"]["findings_template"].startswith(".agentpack/reviews/feature-review/")

    runbook = runbook_path.read_text(encoding="utf-8")
    assert "reviewer is worried about prompt latency" in runbook
    assert preflight["review"]["run_id"] in runbook
    assert preflight["paths"]["understanding_output"] in runbook
    assert preflight["paths"]["findings_output"] in runbook
    assert preflight["paths"]["understanding_template"] in runbook
    assert preflight["paths"]["findings_template"] in runbook
    assert "## Hard Gates" in runbook
    assert "Review scaffold:" in runbook
    assert "Citation source:" in runbook
    assert "AgentPack Context Preflight" in runbook
    assert "agentpack_pack_context" in runbook
    assert "Do not perform the review inline" in runbook
    assert "If you cannot write the Stage 1 output file" in runbook
    assert "run `agentpack review --check`; do not start Stage 2" in runbook
    assert "run `agentpack review --check --post-inline-comments` for PR-bound runs" in runbook
    assert "Do not produce a final summary unless Stage 2 validates" in runbook

    understanding_prompt = understanding_prompt_path.read_text(encoding="utf-8")
    template = _load_review_template("stage1-understanding.md")
    assert understanding_prompt.startswith(template)
    assert "## AgentPack Run Inputs" in understanding_prompt
    assert "AgentPack context" in understanding_prompt
    assert "agentpack_pack_context" in understanding_prompt
    assert "## Execution Gates" in understanding_prompt
    assert "Do not answer inline from this stage prompt." in understanding_prompt
    assert f"Copy-fill TOON template: {preflight['paths']['understanding_template']}" in understanding_prompt
    assert "Prefer valid JSON matching the schema" in understanding_prompt
    assert "Start from the copy-fill TOON template" in understanding_prompt
    assert "will canonicalize safe schema-matching output to TOON" in understanding_prompt
    assert f"Output path: {preflight['paths']['understanding_output']}" in understanding_prompt
    assert understanding_prompt.rstrip().endswith("reviewer is worried about prompt latency")
    assert '"change_units"' in understanding_prompt
    assert "@root review_understanding" in understanding_prompt

    judge_prompt = judge_prompt_path.read_text(encoding="utf-8")
    template = _load_review_template("stage2-judge.md")
    assert judge_prompt.startswith(template)
    assert "## Execution Gates" in judge_prompt
    assert "AgentPack context" in judge_prompt
    assert "Do not answer inline from this stage prompt." in judge_prompt
    assert f"Copy-fill TOON template: {preflight['paths']['findings_template']}" in judge_prompt
    assert "Do not continue until the declared input TOON exists and has been read from disk." in judge_prompt
    assert f"Input path: {preflight['paths']['understanding_output']}" in judge_prompt
    assert f"Output path: {preflight['paths']['findings_output']}" in judge_prompt
    assert judge_prompt.rstrip().endswith("reviewer is worried about prompt latency")
    assert '"findings"' in judge_prompt
    assert "@root review_findings" in judge_prompt


def test_review_check_gates_stage_outputs(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)
    runner = CliRunner()

    first = runner.invoke(app, ["review", "--allow-local-fallback", "first pass"])
    assert first.exit_code == 0, first.output
    preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    missing = runner.invoke(app, ["review", "--check"])
    assert missing.exit_code == 1
    assert "Stage 1 artifact missing" in missing.output
    assert "What failed: Stage 1 understanding artifact is missing" in missing.output
    assert "Safe to continue: no; create the Stage 1 artifact first" in missing.output

    understanding = repo / preflight["paths"]["understanding_output"]
    understanding.parent.mkdir(parents=True, exist_ok=True)
    understanding.write_text(
        "@format toon\n"
        "@root review_understanding\n"
        "intent:\n"
        "  requirement: placeholder\n"
        "change_units[]:\n"
        "  []\n"
        "open_questions[]:\n"
        "  []\n",
        encoding="utf-8",
    )

    ready = runner.invoke(app, ["review", "--check"])
    assert ready.exit_code == 0, ready.output
    assert "Stage 1 valid" in ready.output
    state = json.loads((repo / ".agentpack" / "review-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "awaiting_findings"

    findings = repo / preflight["paths"]["findings_output"]
    findings.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  []\n"
        "coverage:\n"
        "  status: complete\n",
        encoding="utf-8",
    )

    complete = runner.invoke(app, ["review", "--check"])
    assert complete.exit_code == 0, complete.output
    assert "Stage 2 valid" in complete.output
    state = json.loads((repo / ".agentpack" / "review-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "complete"


def test_review_check_canonicalizes_json_and_fenced_outputs(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)
    runner = CliRunner()

    first = runner.invoke(app, ["review", "--allow-local-fallback", "older model compatibility"])
    assert first.exit_code == 0, first.output
    preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    understanding = repo / preflight["paths"]["understanding_output"]
    understanding.parent.mkdir(parents=True, exist_ok=True)
    understanding.write_text(
        json.dumps(
            {
                "intent": {"requirement": "placeholder"},
                "change_units": [],
                "open_questions": [],
            }
        ),
        encoding="utf-8",
    )

    ready = runner.invoke(app, ["review", "--check"])

    assert ready.exit_code == 0, ready.output
    assert "Stage 1 valid" in ready.output
    assert understanding.read_text(encoding="utf-8").startswith("@format toon\n@root review_understanding\n")

    findings = repo / preflight["paths"]["findings_output"]
    findings.write_text(
        "```json\n"
        + json.dumps(
            {
                "findings": [],
                "coverage": "complete",
            }
        )
        + "\n```\n",
        encoding="utf-8",
    )

    complete = runner.invoke(app, ["review", "--check"])

    assert complete.exit_code == 0, complete.output
    assert "Stage 2 valid" in complete.output
    assert findings.read_text(encoding="utf-8").startswith("@format toon\n@root review_findings\n")


def test_review_validation_uses_pr_head_citation_source_when_worktree_drifts(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "src" / "foo.py").write_text("def foo():\n    value = 0\n    return 2\n", encoding="utf-8")
    run_dir = repo / ".agentpack" / "reviews" / "pr-1" / "run"
    run_dir.mkdir(parents=True)
    preflight = {
        "paths": {"findings_output": ".agentpack/reviews/pr-1/run/findings.toon"},
        "git": {"head_sha": head_sha},
        "citation_source": {"mode": "git-head", "head_sha": head_sha},
    }
    (run_dir / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    findings = run_dir / "findings.toon"
    findings.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "f1",
                        "unit": "cu1",
                        "location": "src/foo.py:2",
                        "claim": "foo returns changed value",
                        "evidence": "src/foo.py:2 shows the returned value",
                        "severity": "should-fix",
                    }
                ],
                "coverage": "complete",
            }
        ),
        encoding="utf-8",
    )

    payload = _validate_review_artifact(findings, kind="findings")

    assert payload["findings"][0]["id"] == "f1"


def test_review_validation_report_suggests_nearby_repair_line(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    invalid = repo / ".agentpack" / "findings-repair.toon"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  -\n"
        "    id: f1\n"
        "    unit: cu1\n"
        "    location: src/foo.py:1\n"
        "    claim: foo returns changed value\n"
        "    evidence: src/foo.py:1 shows the returned value\n"
        "    severity: should-fix\n"
        "coverage:\n"
        "  status: incomplete\n",
        encoding="utf-8",
    )

    try:
        _validate_review_artifact(invalid, kind="findings")
    except ValueError as exc:
        assert "suggested=src/foo.py:2" in str(exc)
    else:
        raise AssertionError("unsupported evidence should fail citation validation")
    report = json.loads((repo / ".agentpack" / "findings-validation-errors.json").read_text(encoding="utf-8"))
    assert report["repair_hints"][0]["suggested"] == "src/foo.py:2"


def test_review_check_writes_repair_guide_for_invalid_toon(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)
    runner = CliRunner()

    first = runner.invoke(app, ["review", "--allow-local-fallback", "bad toon"])
    assert first.exit_code == 0, first.output
    preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    understanding = repo / preflight["paths"]["understanding_output"]
    understanding.parent.mkdir(parents=True, exist_ok=True)
    understanding.write_text("@format toon\nbroken\n", encoding="utf-8")

    failed = runner.invoke(app, ["review", "--check"])

    assert failed.exit_code == 1
    assert "repair" in failed.output
    assert "guide" in failed.output
    repair = understanding.with_name("understanding-toon-repair.md")
    assert repair.exists()
    repair_text = repair.read_text(encoding="utf-8")
    assert "@root review_understanding" in repair_text
    assert "valid JSON matching the same schema" in repair_text


def test_review_commentable_right_lines_parse_diff_hunks() -> None:
    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def foo():\n"
        "-    return 1\n"
        "+    return 2\n"
    )

    assert _parse_commentable_right_lines(diff) == {"src/foo.py": {1, 2}}


def test_review_findings_to_inline_comments_require_right_side_lines() -> None:
    findings = [
        {
            "id": "f1",
            "location": "src/foo.py:2",
            "claim": "foo returns a changed value",
            "evidence": "src/foo.py:2 shows the returned value",
            "severity": "should-fix",
            "category": "defect",
            "confidence": "high",
            "direction": "Return the intended value or update the caller expectation.",
        },
        {
            "id": "f2",
            "location": "src/bar.py:4",
            "claim": "bar changed",
            "evidence": "src/bar.py:4 shows the change",
        },
    ]

    comments, skipped = _findings_to_inline_comments(findings, {"src/foo.py": {2}})

    assert comments == [
        {
            "path": "src/foo.py",
            "line": 2,
            "side": "RIGHT",
            "body": (
                "**AgentPack review: Should fix**\n\n"
                "**What I noticed**\n"
                "foo returns a changed value\n\n"
                "**Evidence**\n"
                "src/foo.py:2 shows the returned value\n\n"
                "**Suggested next step**\n"
                "Return the intended value or update the caller expectation.\n\n"
                "<sub>Finding `f1` | `should-fix` | category: `defect` | confidence: `high`</sub>"
            ),
        }
    ]
    assert skipped == ["finding 2: src/bar.py:4 is not in the PR diff as a right-side line"]


def test_review_check_posts_inline_comments_once(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    posted_requests: list[tuple[str, int, dict]] = []

    monkeypatch.setattr(
        "agentpack.commands.review_cmd._gh_pr_metadata",
        lambda _root, _target=None: {
            "number": 98,
            "title": "Load test",
            "url": "https://github.com/acme/repo/pull/98",
            "base_ref": "main",
            "head_ref": "feature/load-test",
        },
    )
    monkeypatch.setattr("agentpack.commands.review_cmd._fetch_pr_refs", lambda _root, _number, _base: {"ok": True, "error": ""})
    monkeypatch.setattr("agentpack.commands.review_cmd._rev_parse", lambda _root, _ref: "abc123")
    monkeypatch.setattr("agentpack.commands.review_cmd._changed_paths", lambda _root, _range: ["src/foo.py"])
    monkeypatch.setattr(
        "agentpack.commands.review_cmd._build_review_context_pack",
        lambda _root, _review_context, _diff_info, _outputs, _warnings: {
            "path": "",
            "tokens": 0,
            "selected_files": 0,
            "broad_context": False,
        },
    )
    monkeypatch.setattr("agentpack.commands.review_cmd._commentable_right_lines", lambda _root, _range: {"src/foo.py": {2}})

    def fake_post(_root, repo_slug, pr_number, payload):
        posted_requests.append((repo_slug, pr_number, payload))
        return {"html_url": "https://github.com/acme/repo/pull/98#pullrequestreview-1", "id": 1}

    monkeypatch.setattr("agentpack.commands.review_cmd._post_pull_request_review", fake_post)

    prepared = runner.invoke(app, ["review", "--pr", "98", "focus latency"])
    assert prepared.exit_code == 0, prepared.output
    preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    understanding = repo / preflight["paths"]["understanding_output"]
    understanding.parent.mkdir(parents=True, exist_ok=True)
    understanding.write_text(
        "@format toon\n"
        "@root review_understanding\n"
        "intent:\n"
        "  requirement: placeholder\n"
        "change_units[]:\n"
        "  []\n"
        "open_questions[]:\n"
        "  []\n",
        encoding="utf-8",
    )
    findings = repo / preflight["paths"]["findings_output"]
    findings.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  -\n"
        "    id: f1\n"
        "    unit: cu1\n"
        "    location: src/foo.py:2\n"
        "    claim: foo returns changed value\n"
        "    evidence: src/foo.py:2 shows the returned value\n"
        "    severity: should-fix\n"
        "coverage:\n"
        "  status: complete\n",
        encoding="utf-8",
    )

    posted = runner.invoke(app, ["review", "--check", "--post-inline-comments"])

    assert posted.exit_code == 0, posted.output
    assert "Posted inline review comments" in posted.output
    assert len(posted_requests) == 1
    repo_slug, pr_number, payload = posted_requests[0]
    assert repo_slug == "acme/repo"
    assert pr_number == 98
    assert payload["commit_id"] == "abc123"
    assert payload["event"] == "COMMENT"
    assert payload["body"] == (
        f"AgentPack found 1 evidence-backed finding and left it inline where it applies.\n\n"
        f"Run: `{preflight['review']['run_id']}`\n\n"
        "Head: `abc123`"
    )
    assert payload["comments"] == [
        {
            "path": "src/foo.py",
            "line": 2,
            "side": "RIGHT",
            "body": (
                "**AgentPack review: Should fix**\n\n"
                "**What I noticed**\n"
                "foo returns changed value\n\n"
                "**Evidence**\n"
                "src/foo.py:2 shows the returned value\n\n"
                "**Suggested next step**\n"
                "Fix this path, or leave a note explaining why the current behavior is intentional.\n\n"
                "<sub>Finding `f1` | `should-fix`</sub>"
            ),
        }
    ]
    posted_record = json.loads((repo / preflight["paths"]["run_dir"] / "posted-review.json").read_text(encoding="utf-8"))
    dry_run_record = json.loads((repo / preflight["paths"]["run_dir"] / "inline-review-dry-run.json").read_text(encoding="utf-8"))
    assert dry_run_record["status"] == "dry_run"
    assert dry_run_record["payload_sha256"] == posted_record["payload_sha256"]
    assert posted_record["status"] == "posted"
    assert posted_record["comments"] == 1
    state = json.loads((repo / ".agentpack" / "review-state.json").read_text(encoding="utf-8"))
    assert state["posted_review"]["status"] == "posted"

    again = runner.invoke(app, ["review", "--check", "--post-inline-comments"])

    assert again.exit_code == 0, again.output
    assert "Review comments already posted" in again.output
    assert len(posted_requests) == 1


def test_review_check_dry_run_writes_inline_payload_without_posting(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    runner = CliRunner()

    monkeypatch.setattr(
        "agentpack.commands.review_cmd._gh_pr_metadata",
        lambda _root, _target=None: {
            "number": 98,
            "title": "Load test",
            "url": "https://github.com/acme/repo/pull/98",
            "base_ref": "main",
            "head_ref": "feature/load-test",
        },
    )
    monkeypatch.setattr("agentpack.commands.review_cmd._fetch_pr_refs", lambda _root, _number, _base: {"ok": True, "error": ""})
    monkeypatch.setattr("agentpack.commands.review_cmd._rev_parse", lambda _root, _ref: "abc123")
    monkeypatch.setattr("agentpack.commands.review_cmd._changed_paths", lambda _root, _range: ["src/foo.py"])
    monkeypatch.setattr(
        "agentpack.commands.review_cmd._build_review_context_pack",
        lambda _root, _review_context, _diff_info, _outputs, _warnings: {
            "path": "",
            "tokens": 0,
            "selected_files": 0,
            "broad_context": False,
        },
    )
    monkeypatch.setattr("agentpack.commands.review_cmd._commentable_right_lines", lambda _root, _range: {"src/foo.py": {2}})

    def fail_if_posted(_root, _repo_slug, _pr_number, _payload):
        raise AssertionError("dry-run should not call GitHub")

    monkeypatch.setattr("agentpack.commands.review_cmd._post_pull_request_review", fail_if_posted)

    prepared = runner.invoke(app, ["review", "--pr", "98", "focus latency"])
    assert prepared.exit_code == 0, prepared.output
    preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    understanding = repo / preflight["paths"]["understanding_output"]
    understanding.parent.mkdir(parents=True, exist_ok=True)
    understanding.write_text(
        "@format toon\n"
        "@root review_understanding\n"
        "intent:\n"
        "  requirement: placeholder\n"
        "change_units[]:\n"
        "  []\n"
        "open_questions[]:\n"
        "  []\n",
        encoding="utf-8",
    )
    findings = repo / preflight["paths"]["findings_output"]
    findings.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  -\n"
        "    id: f1\n"
        "    unit: cu1\n"
        "    location: src/foo.py:2\n"
        "    claim: foo returns changed value\n"
        "    evidence: src/foo.py:2 shows the returned value\n"
        "    severity: should-fix\n"
        "coverage:\n"
        "  status: complete\n",
        encoding="utf-8",
    )

    dry_run = runner.invoke(app, ["review", "--check", "--dry-run-post"])

    assert dry_run.exit_code == 0, dry_run.output
    assert "Inline review payload valid" in dry_run.output
    run_dir = repo / preflight["paths"]["run_dir"]
    assert not (run_dir / "posted-review.json").exists()
    dry_run_record = json.loads((run_dir / "inline-review-dry-run.json").read_text(encoding="utf-8"))
    assert dry_run_record["status"] == "dry_run"
    assert dry_run_record["comments"] == 1
    payload_record = json.loads((run_dir / "inline-review-payload.json").read_text(encoding="utf-8"))
    assert payload_record["endpoint"] == "repos/acme/repo/pulls/98/reviews"
    assert payload_record["payload_sha256"] == dry_run_record["payload_sha256"]
    assert payload_record["payload"]["commit_id"] == "abc123"
    assert payload_record["payload"]["comments"][0]["path"] == "src/foo.py"
    state = json.loads((repo / ".agentpack" / "review-state.json").read_text(encoding="utf-8"))
    assert state["posted_review"]["status"] == "dry_run"


def test_review_check_blocks_post_when_finding_is_not_commentable(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    runner = CliRunner()

    monkeypatch.setattr(
        "agentpack.commands.review_cmd._gh_pr_metadata",
        lambda _root, _target=None: {
            "number": 98,
            "title": "Load test",
            "url": "https://github.com/acme/repo/pull/98",
            "base_ref": "main",
            "head_ref": "feature/load-test",
        },
    )
    monkeypatch.setattr("agentpack.commands.review_cmd._fetch_pr_refs", lambda _root, _number, _base: {"ok": True, "error": ""})
    monkeypatch.setattr("agentpack.commands.review_cmd._rev_parse", lambda _root, _ref: "abc123")
    monkeypatch.setattr("agentpack.commands.review_cmd._changed_paths", lambda _root, _range: ["src/foo.py"])
    monkeypatch.setattr(
        "agentpack.commands.review_cmd._build_review_context_pack",
        lambda _root, _review_context, _diff_info, _outputs, _warnings: {
            "path": "",
            "tokens": 0,
            "selected_files": 0,
            "broad_context": False,
        },
    )
    monkeypatch.setattr("agentpack.commands.review_cmd._commentable_right_lines", lambda _root, _range: {})

    prepared = runner.invoke(app, ["review", "--pr", "98", "focus latency"])
    assert prepared.exit_code == 0, prepared.output
    preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    understanding = repo / preflight["paths"]["understanding_output"]
    understanding.parent.mkdir(parents=True, exist_ok=True)
    understanding.write_text(
        "@format toon\n"
        "@root review_understanding\n"
        "intent:\n"
        "  requirement: placeholder\n"
        "change_units[]:\n"
        "  []\n"
        "open_questions[]:\n"
        "  []\n",
        encoding="utf-8",
    )
    findings = repo / preflight["paths"]["findings_output"]
    findings.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  -\n"
        "    id: f1\n"
        "    unit: cu1\n"
        "    location: src/foo.py:2\n"
        "    claim: foo returns changed value\n"
        "    evidence: src/foo.py:2 shows the returned value\n"
        "    severity: should-fix\n"
        "coverage:\n"
        "  status: complete\n",
        encoding="utf-8",
    )

    blocked = runner.invoke(app, ["review", "--check", "--post-inline-comments"])

    assert blocked.exit_code == 1
    assert "Could not post inline review comments" in blocked.output
    assert "src/foo.py:2 is not in the PR diff as a right-side line" in blocked.output
    assert not (repo / preflight["paths"]["run_dir"] / "posted-review.json").exists()


def test_review_command_starts_fresh_and_warns_about_incomplete_previous_run(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)
    runner = CliRunner()

    first = runner.invoke(app, ["review", "--allow-local-fallback", "first pass"])
    assert first.exit_code == 0, first.output
    first_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))
    first_understanding = repo / first_preflight["paths"]["understanding_output"]
    first_understanding.parent.mkdir(parents=True, exist_ok=True)
    first_understanding.write_text("@format toon\n@root review_understanding\nintent:\n  requirement: placeholder\nchange_units[]:\n  []\nopen_questions[]:\n  []\n", encoding="utf-8")

    second = runner.invoke(app, ["review", "--allow-local-fallback", "second pass"])
    assert second.exit_code == 0, second.output
    second_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    assert second_preflight["review"]["run_id"] != first_preflight["review"]["run_id"]
    assert any("incomplete previous review run" in warning for warning in second_preflight["warnings"])
    assert second_preflight["paths"]["run_dir"] != first_preflight["paths"]["run_dir"]


def test_review_command_resume_reuses_existing_run(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)
    runner = CliRunner()

    first = runner.invoke(app, ["review", "--allow-local-fallback", "first pass"])
    assert first.exit_code == 0, first.output
    first_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))
    run_id = first_preflight["review"]["run_id"]

    resumed = runner.invoke(app, ["review", "--resume", run_id, "ignored context"])
    assert resumed.exit_code == 0, resumed.output
    resumed_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    assert resumed_preflight["review"]["run_id"] == run_id
    assert resumed_preflight["review"]["mode"] == "resume"
    assert resumed_preflight["review_context"] == "first pass"

def test_review_command_warns_on_invalid_understanding_toon(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)
    runner = CliRunner()

    first = runner.invoke(app, ["review", "--allow-local-fallback", "first pass"])
    assert first.exit_code == 0, first.output
    first_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))
    first_understanding = repo / first_preflight["paths"]["understanding_output"]
    first_understanding.parent.mkdir(parents=True, exist_ok=True)
    first_understanding.write_text("@format toon\nbroken\n", encoding="utf-8")

    second = runner.invoke(app, ["review", "--allow-local-fallback", "second pass"])
    assert second.exit_code == 0, second.output
    second_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))

    assert any("invalid understanding TOON" in warning for warning in second_preflight["warnings"])

def test_review_command_resume_fails_cleanly_on_invalid_understanding_toon(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("agentpack.commands.review_cmd._gh_pr_metadata", lambda _root, _target=None: None)
    runner = CliRunner()

    first = runner.invoke(app, ["review", "--allow-local-fallback", "first pass"])
    assert first.exit_code == 0, first.output
    first_preflight = json.loads((repo / ".agentpack" / "review-preflight.json").read_text(encoding="utf-8"))
    run_id = first_preflight["review"]["run_id"]
    first_understanding = repo / first_preflight["paths"]["understanding_output"]
    first_understanding.parent.mkdir(parents=True, exist_ok=True)
    first_understanding.write_text("@format toon\nbroken\n", encoding="utf-8")

    resumed = runner.invoke(app, ["review", "--resume", run_id])

    assert resumed.exit_code == 1
    assert "Review run artifact invalid" in resumed.output


def test_review_findings_validator_requires_claim_level_citations(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    valid = repo / ".agentpack" / "findings-valid.toon"
    valid.parent.mkdir(parents=True, exist_ok=True)
    valid.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  -\n"
        "    id: f1\n"
        "    unit: cu1\n"
        "    location: src/foo.py:1\n"
        "    claim: foo returns changed value\n"
        "    evidence: src/foo.py:2 shows the returned value\n"
        "    severity: should-fix\n"
        "coverage:\n"
        "  status: complete\n",
        encoding="utf-8",
    )
    invalid = repo / ".agentpack" / "findings-invalid.toon"
    invalid.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  -\n"
        "    id: f1\n"
        "    unit: cu1\n"
        "    location: src/foo.py\n"
        "    claim: foo returns changed value\n"
        "    evidence: code shows it\n"
        "    severity: should-fix\n"
        "coverage:\n"
        "  status: incomplete\n",
        encoding="utf-8",
    )

    _validate_review_artifact(valid, kind="findings")

    try:
        _validate_review_artifact(invalid, kind="findings")
    except ValueError as exc:
        assert "location must include path:line evidence" in str(exc)
        assert "evidence must include path:line evidence" in str(exc)
    else:
        raise AssertionError("invalid findings should fail citation validation")


def test_review_findings_validator_rejects_unsupported_evidence_line(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    invalid = repo / ".agentpack" / "findings-unsupported.toon"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  -\n"
        "    id: f1\n"
        "    unit: cu1\n"
        "    location: src/foo.py:1\n"
        "    claim: foo returns changed value\n"
        "    evidence: src/foo.py:1 shows the returned value\n"
        "    severity: should-fix\n"
        "coverage:\n"
        "  status: incomplete\n",
        encoding="utf-8",
    )

    try:
        _validate_review_artifact(invalid, kind="findings")
    except ValueError as exc:
        assert "finding 1.evidence: src/foo.py:1 does not support claim text" in str(exc)
    else:
        raise AssertionError("unsupported finding evidence should fail citation validation")


def test_review_findings_validator_can_use_semantic_support_command(tmp_path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    judge = repo / "judge.py"
    judge.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "assert payload['cited_text'].strip() == 'return 2'\n"
        "print(json.dumps({'supported': False, 'reason': 'semantic mismatch'}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTPACK_CITATION_SEMANTIC_COMMAND", f"python {judge}")
    finding = repo / ".agentpack" / "findings-semantic.toon"
    finding.parent.mkdir(parents=True, exist_ok=True)
    finding.write_text(
        "@format toon\n"
        "@root review_findings\n"
        "findings[]:\n"
        "  -\n"
        "    id: f1\n"
        "    unit: cu1\n"
        "    location: src/foo.py:1\n"
        "    claim: foo returns changed value\n"
        "    evidence: src/foo.py:2 shows the returned value\n"
        "    severity: should-fix\n"
        "coverage:\n"
        "  status: incomplete\n",
        encoding="utf-8",
    )

    try:
        _validate_review_artifact(finding, kind="findings")
    except ValueError as exc:
        assert "semantic support rejected (semantic mismatch)" in str(exc)
    else:
        raise AssertionError("semantic support command rejection should fail validation")


def test_review_understanding_validator_rejects_unsupported_symbol_line(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    valid = repo / ".agentpack" / "understanding-valid.toon"
    valid.parent.mkdir(parents=True, exist_ok=True)
    valid.write_text(
        "@format toon\n"
        "@root review_understanding\n"
        "intent:\n"
        "  requirement: placeholder\n"
        "change_units[]:\n"
        "  -\n"
        "    id: cu1\n"
        "    location: src/foo.py:1-2\n"
        "    kind: core\n"
        "    what_changed: foo return changed\n"
        "    code: src/foo.py:2 return 2\n"
        "    referenced_symbols[]:\n"
        "      -\n"
        "        name: returned value\n"
        "        defined_at: src/foo.py:2\n"
        "        code: return 2\n"
        "        confidence: high\n"
        "    callers[]:\n"
        "      []\n"
        "    contracts_touched[]:\n"
        "      []\n"
        "    local_convention_refs[]:\n"
        "      []\n"
        "open_questions[]:\n"
        "  []\n",
        encoding="utf-8",
    )
    invalid = repo / ".agentpack" / "understanding-invalid.toon"
    invalid.write_text(
        "@format toon\n"
        "@root review_understanding\n"
        "intent:\n"
        "  requirement: placeholder\n"
        "change_units[]:\n"
        "  -\n"
        "    id: cu1\n"
        "    location: src/foo.py:1-2\n"
        "    kind: core\n"
        "    what_changed: foo return changed\n"
        "    code: src/foo.py:2 return 2\n"
        "    referenced_symbols[]:\n"
        "      -\n"
        "        name: returned value\n"
        "        defined_at: src/foo.py:1\n"
        "        code: return 2\n"
        "        confidence: high\n"
        "    callers[]:\n"
        "      []\n"
        "    contracts_touched[]:\n"
        "      []\n"
        "    local_convention_refs[]:\n"
        "      []\n"
        "open_questions[]:\n"
        "  []\n",
        encoding="utf-8",
    )

    _validate_review_artifact(valid, kind="understanding")

    try:
        _validate_review_artifact(invalid, kind="understanding")
    except ValueError as exc:
        assert "change_unit 1.referenced_symbols: src/foo.py:1 does not support claim text" in str(exc)
    else:
        raise AssertionError("unsupported understanding citation should fail validation")
