from __future__ import annotations

import json

from typer.testing import CliRunner

from agentpack.application.pack_service import PackPlanner, PackRequest
from agentpack.cli import app


def _write_route_fixture(root):
    (root / ".agentpack").mkdir(exist_ok=True)
    (root / ".agentpack" / "config.toml").write_text(
        "[skills]\npaths = [\".agentpack/skills\", \".cursor/rules\"]\n",
        encoding="utf-8",
    )
    (root / "payments").mkdir()
    (root / "payments" / "webhooks.py").write_text("def handle_webhook(event):\n    return event\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_payment_webhooks.py").write_text(
        "from payments.webhooks import handle_webhook\n\n"
        "def test_payment_webhook():\n"
        "    assert handle_webhook({'id': 'evt_1'})\n",
        encoding="utf-8",
    )
    skill = root / ".agentpack" / "skills" / "django-pytest" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "# django-pytest\n\nUse for pytest test debugging, fixtures, mocks, and flaky tests.\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Repo Rules\n\nVerify changes with tests.\n", encoding="utf-8")


def test_skills_scan_prints_counts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_route_fixture(tmp_path)

    result = CliRunner().invoke(app, ["skills", "scan"])

    assert result.exit_code == 0, result.output
    assert "Found 1 skills and 1 rules" in result.output
    assert "django-pytest" in result.output


def test_skills_index_writes_valid_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_route_fixture(tmp_path)

    result = CliRunner().invoke(app, ["skills", "index"])

    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / ".agentpack" / "skills_index.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["inventory"]["version"] == 1
    assert data["inventory"]["skills"][0]["name"] == "django-pytest"
    assert data["inventory"]["rules"][0]["path"] == "AGENTS.md"
    assert data["configured_paths"] == [".agentpack/skills", ".cursor/rules"]
    assert data["sources"]
    assert "raw_text" not in data["inventory"]["skills"][0]
    assert "raw_text" not in data["inventory"]["rules"][0]


def test_skills_recommend_explain_prints_skill_plan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_route_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        ["skills", "recommend", "--task", "fix flaky payment webhook test", "--explain"],
    )

    assert result.exit_code == 0, result.output
    assert "Recommended skills" in result.output
    assert "django-pytest" in result.output
    assert "confidence" in result.output
    assert "Skill Plan" in result.output


def test_skills_feedback_records_outcome(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()

    result = CliRunner().invoke(
        app,
        [
            "skills",
            "feedback",
            "--task",
            "fix auth",
            "--used-skill",
            "auth-review",
            "--changed-file",
            "src/auth.py",
            "--tests-passed",
            "--user-feedback",
            "helpful",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / ".agentpack" / "skill_feedback.jsonl").read_text(encoding="utf-8"))
    assert data["task"] == "fix auth"
    assert data["used_skills"] == ["auth-review"]
    assert data["changed_files"] == ["src/auth.py"]
    assert data["tests_passed"] is True


def test_route_json_returns_stable_keys_and_does_not_write_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_route_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        ["route", "--task", "fix flaky payment webhook test", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) >= {
        "task",
        "recommended_interaction_mode",
        "mode_reason",
        "current_agent",
        "reviewer_agent",
        "task_mode",
        "task_mode_confidence",
        "task_mode_signals",
        "selected_files",
        "selected_skills",
        "applied_rules",
        "suggested_commands",
        "evidence_checklist",
        "routing_notes",
        "prompt_quality_warnings",
        "recommended_prompt_template",
        "safety_warnings",
        "agent_prompt",
    }
    assert data["selected_files"]
    assert data["selected_skills"][0]["skill"]["name"] == "django-pytest"
    assert data["applied_rules"][0]["rule"]["path"] == "AGENTS.md"
    assert "pytest" in data["suggested_commands"][0]["command"]
    assert data["task_mode"] in {"small_direct_edit", "broad_feature"}
    assert data["task_mode_confidence"] > 0
    assert not (tmp_path / ".agentpack" / "context.md").exists()
    assert not (tmp_path / ".agentpack" / "context.claude.md").exists()


def test_route_json_flag_alias_returns_machine_readable_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_route_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        ["route", "--task", "fix flaky payment webhook test", "--json"],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["task"] == "fix flaky payment webhook test"
    assert data["selected_files"]
    assert data["agent_prompt"]


def test_route_invalid_format_exits_nonzero_and_mentions_json_alias(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_route_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        ["route", "--task", "fix flaky payment webhook test", "--format", "bad"],
    )

    assert result.exit_code != 0
    assert "--format plain" in result.output or "plain" in result.output
    assert "--format json" in result.output or "json" in result.output
    assert "--json" in result.output


def test_route_uses_recent_issue_references_as_hints(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_route_fixture(tmp_path)
    (tmp_path / ".agentpack" / "session-events.jsonl").write_text(
        json.dumps({"type": "learn", "task": "fix webhook #77", "issue_references": ["#77"]}) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["route", "--task", "continue webhook fix", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "Used recent issue references" in "\n".join(data["routing_notes"])


def test_route_short_simple_question_recommends_ask_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENAI_CODEX", "1")

    result = CliRunner().invoke(app, ["route", "--task", "what does this error mean?", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["recommended_interaction_mode"] == "ask"
    assert "short explanatory prompt" in data["mode_reason"]
    assert data["current_agent"] == "codex"
    assert data["reviewer_agent"] == "claude"
    assert any("Ask/Chat mode" in warning for warning in data["prompt_quality_warnings"])
    assert data["recommended_prompt_template"]


def test_route_vague_agent_prompt_recommends_spec_and_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")

    result = CliRunner().invoke(app, ["route", "--task", "can you fix these gaps..", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    warnings = "\n".join(data["prompt_quality_warnings"])
    assert data["recommended_interaction_mode"] == "agent"
    assert "repo work" in data["mode_reason"] or "actionable repo work" in data["mode_reason"]
    assert data["current_agent"] == "claude"
    assert data["reviewer_agent"] == "codex"
    assert "No file context detected" in warnings
    assert "acceptance criteria" in warnings
    assert "Short prompt has no output constraint" in warnings


def test_route_caps_frustration_warns_to_switch_strategy(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")

    result = CliRunner().invoke(app, ["route", "--task", "THIS IS STILL BROKEN!!!", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any("Frustration signal" in warning for warning in data["prompt_quality_warnings"])


def test_route_suppresses_weak_external_skill_warning_noise(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text(
        "[skills]\npaths = [\".agentpack/skills\"]\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "agentpack.py").write_text("def route(): return True\n", encoding="utf-8")

    for idx in range(8):
        skill = tmp_path / ".agentpack" / "skills" / f"cloud-gap-{idx}" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\n"
            "confidence_threshold: 0.95\n"
            "triggers: [gaps]\n"
            "---\n"
            f"# cloud-gap-{idx}\n\n"
            "Send Slack status during cloud deploys and migrations.\n",
            encoding="utf-8",
        )

    result = CliRunner().invoke(app, ["route", "--task", "can you fix these gaps..", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["safety_warnings"] == []
    assert "External side-effect skill not auto-selected" not in data["agent_prompt"]
    assert "more external side-effect skills not shown" not in data["agent_prompt"]


def test_route_uses_codex_env_over_multi_agent_repo_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_SHELL", "1")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "GEMINI.md").write_text("antigravity instructions\n", encoding="utf-8")
    (tmp_path / ".agent" / "skills").mkdir(parents=True)
    for path in (
        "src/agentpack/router/service.py",
        "src/agentpack/router/prompt_builder.py",
        "src/agentpack/mcp_server.py",
        "src/agentpack/commands/eval_cmd.py",
        "src/agentpack/commands/guard.py",
        "src/agentpack/adapters/detect.py",
        "src/agentpack/application/pack_service.py",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "route",
            "--task",
            "fix noisy routing, stale CLI/MCP mismatch, and wrong current agent identity",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    selected = [item["path"] for item in data["selected_files"][:6]]
    assert data["current_agent"] == "codex"
    assert data["reviewer_agent"] == "claude"
    assert "src/agentpack/commands/eval_cmd.py" not in selected
    assert "src/agentpack/router/service.py" in selected
    assert "src/agentpack/mcp_server.py" in selected
    assert "src/agentpack/adapters/detect.py" in selected


def test_route_refreshes_stale_skills_index(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_route_fixture(tmp_path)
    runner = CliRunner()

    index = runner.invoke(app, ["skills", "index"])
    assert index.exit_code == 0, index.output

    skill = tmp_path / ".agentpack" / "skills" / "django-pytest" / "SKILL.md"
    skill.write_text(
        "# django-pytest\n\nUse for pytest test debugging, fixtures, mocks, flaky tests, and regression tests.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["route", "--task", "fix pytest regression", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = [item["skill"]["name"] for item in payload["selected_skills"]]
    assert "django-pytest" in names


def test_route_small_direct_edit_recommends_targeted_search(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Button.tsx").write_text("export function Button(){ return null }\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["route", "--task", "small css fix Button.tsx", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["task_mode"] == "small_direct_edit"
    assert any("targeted `rg`" in note for note in data["routing_notes"])
    assert any("orientation only" in note for note in data["routing_notes"])
    assert data["evidence_checklist"]


def test_route_runtime_debugging_returns_evidence_checklist(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "customerio_events.py").write_text("def send_event(row): return row\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["route", "--task", "debug Customer.io event pipeline logs", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["task_mode"] == "runtime_debugging"
    assert "inspect runtime/tool evidence for the exact failing session" in data["evidence_checklist"]
    assert any("Do not route by repo ranking alone" in note for note in data["routing_notes"])


def test_route_pr_review_suppresses_noisy_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess = __import__("subprocess")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login(): return True\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".agentpack/\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "src" / "auth.py").write_text("def login(): return False\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".agentpack/\ndist/\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["route", "--task", "review PR auth diff", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    paths = [item["path"] for item in data["selected_files"]]
    assert data["task_mode"] == "pr_review"
    assert paths[0] == "src/auth.py"
    assert ".gitignore" not in paths


def test_route_pr_review_keeps_changed_workflow_diff_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess = __import__("subprocess")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "deploy.yml").write_text("name: deploy\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("# deployment workflow helper\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / ".github" / "workflows" / "deploy.yml").write_text(
        "name: deploy\non: push\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["route", "--task", "review PR deployment workflow", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    paths = [item["path"] for item in data["selected_files"]]
    assert data["task_mode"] == "pr_review"
    assert paths[0] == ".github/workflows/deploy.yml"


def test_route_pr_review_can_prioritize_github_pr_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "customerio_events.py").write_text("def send(): return True\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "noise.md").write_text("review diff notes\n", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = "backend/customerio_events.py\n"
        stderr = ""

    monkeypatch.setattr("agentpack.router.service.shutil.which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr("agentpack.router.service.subprocess.run", lambda *args, **kwargs: Result())

    result = CliRunner().invoke(app, ["route", "--task", "review PR #123 for Customer.io events", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    paths = [item["path"] for item in data["selected_files"]]
    assert data["task_mode"] == "pr_review"
    assert data["task_mode_confidence"] >= 0.68
    assert any("pr-review" in signal for signal in data["task_mode_signals"])
    assert paths[0] == "backend/customerio_events.py"
    assert "GitHub PR file" in data["selected_files"][0]["reasons"]


def test_route_pr_review_suppresses_secret_fixture_noise_when_pr_files_exist(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "customerio_events.py").write_text("def send(): return True\n", encoding="utf-8")
    (tmp_path / "tests" / "fixtures" / "secret_repo" / "src").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "secret_repo" / "src" / "leak.py").write_text(
        "TOKEN = 'sk-test-secret-fixture'\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_redactor.py").write_text(
        "SECRET = 'sk-test-redactor-fixture'\n",
        encoding="utf-8",
    )

    class Result:
        returncode = 0
        stdout = "backend/customerio_events.py\n"
        stderr = ""

    monkeypatch.setattr("agentpack.router.service.shutil.which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr("agentpack.router.service.subprocess.run", lambda *args, **kwargs: Result())
    monkeypatch.setattr("agentpack.application.pack_service.shutil.which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr("agentpack.application.pack_service.subprocess.run", lambda *args, **kwargs: Result())

    result = CliRunner().invoke(app, ["route", "--task", "review PR #123 for security and performance", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    paths = [item["path"] for item in data["selected_files"]]
    assert paths[0] == "backend/customerio_events.py"
    assert "tests/test_redactor.py" not in paths
    assert "tests/fixtures/secret_repo/src/leak.py" not in paths


def test_pack_planner_uses_github_pr_files_as_changed_context(tmp_path, monkeypatch) -> None:
    subprocess = __import__("subprocess")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "customerio_events.py").write_text("def send(): return True\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "noise.md").write_text("review diff notes\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    monkeypatch.setattr(
        "agentpack.application.pack_service._github_pr_paths",
        lambda root, task: {"backend/customerio_events.py"},
    )

    plan = PackPlanner().plan(PackRequest(
        root=tmp_path,
        agent="generic",
        task="review PR #123 for Customer.io events",
        mode="balanced",
        budget=4000,
        since=None,
        refresh=False,
    ))

    assert "backend/customerio_events.py" in plan.all_changed
    assert plan.changed_files_source == "GitHub PR files"
    selected = {item.path: item for item in plan.selected}
    assert "backend/customerio_events.py" in selected
    assert "GitHub PR file" in selected["backend/customerio_events.py"].reasons
