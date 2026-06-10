from __future__ import annotations

import json

from typer.testing import CliRunner

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
    assert data["version"] == 1
    assert data["skills"][0]["name"] == "django-pytest"
    assert data["rules"][0]["path"] == "AGENTS.md"
    assert "raw_text" not in data["skills"][0]
    assert "raw_text" not in data["rules"][0]


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
            "--recommended-skill",
            "auth-review",
            "--used-skill",
            "auth-review",
            "--ignored-skill",
            "deployment-checklist",
            "--bad-recommendation",
            "deployment-checklist",
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
    assert data["recommended_skills"] == ["auth-review"]
    assert data["used_skills"] == ["auth-review"]
    assert data["ignored_skills"] == ["deployment-checklist"]
    assert data["bad_recommendations"] == ["deployment-checklist"]
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
        "selected_files",
        "selected_skills",
        "applied_rules",
        "suggested_commands",
        "safety_warnings",
        "agent_prompt",
    }
    assert data["selected_files"]
    assert data["selected_skills"][0]["skill"]["name"] == "django-pytest"
    assert data["applied_rules"][0]["rule"]["path"] == "AGENTS.md"
    assert "pytest" in data["suggested_commands"][0]["command"]
    assert not (tmp_path / ".agentpack" / "context.md").exists()
    assert not (tmp_path / ".agentpack" / "context.claude.md").exists()
