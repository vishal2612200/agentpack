from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from agentpack.cli import app


runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_agentpack_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENTPACK_HOME", str(tmp_path.parent / f"{tmp_path.name}-agentpack-home"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _git_with_env(repo: Path, *args: str, env: dict[str, str]) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "task.md").write_text("Add CLI learning summaries\n", encoding="utf-8")
    (tmp_path / "cli.py").write_text("import typer\n\napp = typer.Typer()\n", encoding="utf-8")
    _git(tmp_path, "add", ".agentpack/task.md", "cli.py")
    _git(tmp_path, "commit", "-m", "initial")
    (tmp_path / "cli.py").write_text(
        "import typer\n\napp = typer.Typer()\n@app.command()\ndef learn():\n    pass\n",
        encoding="utf-8",
    )
    return tmp_path


def test_learn_writes_markdown_file(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn"])

    assert result.exit_code == 0, result.output
    output = repo / ".agentpack" / "learning.md"
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "# AgentPack Learning Summary" in text
    assert "## Next Technical Topics" in text
    assert "Next Technical Topics" in result.output
    assert "`cli.py`" in text


def test_learn_json_outputs_json_without_writing_default_file(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["task"] == "Add CLI learning summaries"
    assert payload["source_files"][0]["path"] == "cli.py"
    assert payload["recommendations"]["scope"] == "local"
    assert payload["recommendations"]["topics"][0]["topic_id"].startswith("topic-")
    assert not (repo / ".agentpack" / "learning.md").exists()


def test_learn_starts_and_completes_recommended_topic(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    report_result = runner.invoke(app, ["learn", "--json"])
    assert report_result.exit_code == 0, report_result.output
    report = json.loads(report_result.stdout)
    topic = report["recommendations"]["topics"][0]

    start_result = runner.invoke(app, ["learn", "--topic", topic["topic_id"], "--json"])
    assert start_result.exit_code == 0, start_result.output
    session = json.loads(start_result.stdout)
    assert session["topic_id"] == topic["topic_id"]
    assert session["status"] == "queued"
    assert "Evaluate every expected point" in session["coaching_prompt"]

    proof = {
        "kind": "artifact",
        "answer": "The command is additive and the focused verification passes.",
        "rubric_results": [
            {"criterion": point, "rating": "met", "evidence": "Covered by the answer"}
            for point in session["expected_points"]
        ],
        "artifact_paths": ["cli.py"],
        "verification_evidence": [
            {"command": "python -m compileall cli.py", "exit_code": 0, "summary": "compiled", "executed_at": ""}
        ],
        "self_assessment": "mastered",
        "evaluator": "codex",
        "evaluated_at": "",
    }
    proof_path = repo / "proof.json"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")

    complete_result = runner.invoke(
        app,
        [
            "learn",
            "--complete",
            session["session_id"],
            "--proof-file",
            "proof.json",
            "--json",
        ],
    )
    assert complete_result.exit_code == 0, complete_result.output
    completed = json.loads(complete_result.stdout)
    assert completed["mastery_status"] == "developing"
    assert completed["score"] == 100
    assert completed["artifact_hashes"]["cli.py"]

    retry = runner.invoke(
        app,
        ["learn", "--complete", session["session_id"], "--proof-file", "-", "--json"],
        input=json.dumps(proof),
    )
    assert retry.exit_code == 0, retry.output
    assert json.loads(retry.stdout)["duplicate"] is True


def test_learn_rejects_incomplete_completion_request(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--complete", "session-missing", "--score", "90"])

    assert result.exit_code == 2
    assert "requires --proof-file" in result.output


def test_learn_global_session_is_written_and_completed_in_owning_repo(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _repo(first)
    _repo(second)
    (second / ".agentpack" / "session-events.jsonl").write_text(
        json.dumps(
            {
                "type": "task_memory",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": "task-retry",
                "task": "Bound worker retry attempts",
                "status": "done",
                "concepts": ["retry logic"],
                "changed_files": ["src/worker/retry.py"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(second)
    second_report = runner.invoke(app, ["learn", "--json"])
    assert second_report.exit_code == 0, second_report.output

    monkeypatch.chdir(first)
    global_report = runner.invoke(app, ["learn", "--global", "--json"])
    assert global_report.exit_code == 0, global_report.output
    recommendations = json.loads(global_report.stdout)["recommendations"]
    topic = next(item for item in recommendations["topics"] if item["project"]["root"] == str(second.resolve()))

    started = runner.invoke(
        app,
        ["learn", "--topic", topic["topic_id"], "--project", topic["project"]["project_id"], "--json"],
    )
    assert started.exit_code == 0, started.output
    session = json.loads(started.stdout)
    owner_rows = [
        json.loads(line)
        for line in (second / ".agentpack" / "learning-sessions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(row["session_id"] == session["session_id"] for row in owner_rows)

    completed = runner.invoke(
        app,
        [
            "learn",
            "--complete",
            session["session_id"],
            "--score",
            "84",
            "--self-assessment",
            "mastered",
            "--json",
        ],
    )
    assert completed.exit_code == 0, completed.output
    assert json.loads(completed.stdout)["mastery_status"] == "developing"
    assert "deprecated" in completed.stderr
    latest_owner_row = json.loads((second / ".agentpack" / "learning-sessions.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert latest_owner_row["session_id"] == session["session_id"]
    assert latest_owner_row["status"] == "completed"


def test_learn_profile_defaults_and_atomic_update(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    defaults = runner.invoke(app, ["learn", "--profile", "--json"])
    assert defaults.exit_code == 0, defaults.output
    assert json.loads(defaults.stdout)["profile"]["role"] == "general"

    updated = runner.invoke(
        app,
        ["learn", "--profile", "--role", "backend", "--level", "mid", "--json"],
    )
    assert updated.exit_code == 0, updated.output
    payload = json.loads(updated.stdout)
    assert payload["profile"]["role"] == "backend"
    assert payload["profile"]["target_level"] == "mid"


def test_learn_profile_rejects_invalid_role(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--profile", "--role", "database"])

    assert result.exit_code == 2
    assert "role must be" in result.output


def test_learn_json_accepts_on_demand_quiz_request(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "quiz", "me", "on", "this", "task", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["learning_request"] == "quiz me on this task"
    assert payload["coach_mode"] == "quiz"
    assert payload["learning_topics"][0]["questions"]
    assert payload["learning_topics"][0]["questions"][0]["mode"] == "quiz"
    sessions = [json.loads(line) for line in (repo / ".agentpack" / "learning-sessions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sessions[0]["request"] == "quiz me on this task"
    assert sessions[0]["mode"] == "quiz"
    assert sessions[0]["status"] == "queued"
    assert sessions[0]["question"]
    assert sessions[0]["evidence_files"] == ["cli.py"]


def test_learn_can_build_from_last_task_memory(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / ".agentpack" / "session-events.jsonl").write_text(
        json.dumps(
            {
                "type": "task_memory",
                "task": "Ship cache invalidation fix",
                "stage": "finish",
                "status": "done",
                "changed_files": ["src/cache.py"],
                "selected_files": ["src/cache.py"],
                "concepts": ["caching"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "cli.py").write_text("import typer\n\napp = typer.Typer()\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "interview me on last task", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["task"] == "Ship cache invalidation fix"
    assert payload["scope"] == "task-memory"
    assert payload["coach_mode"] == "interview"
    assert payload["source_files"][0]["path"] == "src/cache.py"
    assert "caching" in payload["concepts"]
    sessions = [json.loads(line) for line in (repo / ".agentpack" / "learning-sessions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sessions[0]["task"] == "Ship cache invalidation fix"
    assert sessions[0]["concepts"] == ["caching"]


def test_learn_custom_output_path(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--output", ".agentpack/custom.md"])

    assert result.exit_code == 0, result.output
    assert (repo / ".agentpack" / "custom.md").exists()


def test_learn_today_writes_daily_summary_path(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--today"])

    assert result.exit_code == 0, result.output
    output = repo / ".agentpack" / "daily-summary.md"
    assert output.exists()
    assert "**Scope:** today" in output.read_text(encoding="utf-8")


def test_learn_today_uses_calendar_day_commits(tmp_path, monkeypatch):
    repo = tmp_path
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / ".agentpack").mkdir()
    (repo / ".agentpack" / "task.md").write_text("Review today CLI work\n", encoding="utf-8")
    (repo / "old.py").write_text("print('old')\n", encoding="utf-8")
    _git(repo, "add", ".agentpack/task.md", "old.py")
    _git_with_env(
        repo,
        "commit",
        "-m",
        "old",
        env={
            "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+0000",
            "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+0000",
        },
    )
    (repo / "new.py").write_text("import typer\napp = typer.Typer()\n", encoding="utf-8")
    _git(repo, "add", "new.py")
    _git(repo, "commit", "-m", "new")
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--today"])

    assert result.exit_code == 0, result.output
    text = (repo / ".agentpack" / "daily-summary.md").read_text(encoding="utf-8")
    assert "`new.py`" in text
    assert "`old.py`" not in text


def test_learn_updates_skill_map(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn"])

    assert result.exit_code == 0, result.output
    payload = json.loads((repo / ".agentpack" / "skills-progress.json").read_text(encoding="utf-8"))
    assert payload["skills"]


def test_learn_writes_agent_lessons(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn"])

    assert result.exit_code == 0, result.output
    text = (repo / ".agentpack" / "agent-lessons.md").read_text(encoding="utf-8")
    assert "# Agent Lessons" in text
    assert "Evidence:" in text


def test_learn_writes_llm_prompt_and_pr_comment(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--llm-prompt", "--pr-comment"])

    assert result.exit_code == 0, result.output
    prompt = (repo / ".agentpack" / "learning.prompt.md").read_text(encoding="utf-8")
    comment = (repo / ".agentpack" / "pr-learning-comment.md").read_text(encoding="utf-8")
    assert "source-backed learning summary" in prompt
    assert "## Learning Summary" in comment


def test_learn_writes_dashboard_and_team_export(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--dashboard", "--team-export"])

    assert result.exit_code == 0, result.output
    dashboard = (repo / ".agentpack" / "learning-dashboard.html").read_text(encoding="utf-8")
    team = (repo / ".agentpack" / "team-lessons.md").read_text(encoding="utf-8")
    assert "AgentPack Learn Dashboard" in dashboard
    assert 'class="topbar"' in dashboard
    assert "Changed File Evidence" in dashboard
    assert "Coach Queue" in dashboard
    assert "Study prompt" in dashboard
    assert "Question" in dashboard
    assert "Learning Cards" in dashboard
    assert "<script" not in dashboard.lower()
    assert "https://" not in dashboard
    assert "http://" not in dashboard
    assert "# AgentPack Team Lessons" in team
    assert "personal skill history" in team


def test_learn_open_writes_and_opens_dashboard(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    opened: list[str] = []
    monkeypatch.setattr("agentpack.commands.learn._open_file", lambda path: opened.append(str(path)))

    result = runner.invoke(app, ["learn", "--open"])

    assert result.exit_code == 0, result.output
    dashboard = repo / ".agentpack" / "learning-dashboard.html"
    assert dashboard.exists()
    assert [str(Path(path).resolve()) for path in opened] == [str(dashboard)]


def test_learn_provider_command_enriches_report(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    provider = repo / "provider.py"
    provider.write_text(
        "import json, sys\npayload = json.load(sys.stdin)\nprint(json.dumps({'next_practice': 'Explain provider output for ' + payload['task']}))\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--provider-command", f"python {provider}"])

    assert result.exit_code == 0, result.output
    text = (repo / ".agentpack" / "learning.md").read_text(encoding="utf-8")
    assert "Explain provider output for Add CLI learning summaries" in text


def test_learn_json_validates_provider_citations_against_repo_root(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    provider = repo / "provider.py"
    provider.write_text(
        "import json\n"
        "print(json.dumps({\n"
        "  'claim_citations': {\n"
        "    'summary:1': [\n"
        "      {'path': 'missing.py', 'start_line': 1, 'end_line': 1, 'kind': 'summary'}\n"
        "    ]\n"
        "  }\n"
        "}))\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--json", "--provider-command", f"python {provider}"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["invalid_citations"] == ["missing.py:1: file missing"]
    assert payload["citation_coverage"] < 1.0


def test_learn_concept_provider_command_enriches_concepts_and_topics(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    provider = repo / "concept_provider.py"
    provider.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "assert payload['current_report']['task'] == 'Add CLI learning summaries'\n"
        "print(json.dumps({\n"
        "  'concepts': ['developer education'],\n"
        "  'source_file_concepts': {'cli.py': ['developer education']},\n"
        "  'learning_topics': [{\n"
        "    'title': 'Developer Education',\n"
        "    'why': 'Study how CLI output teaches workflow concepts.',\n"
        "    'prompt': 'Teach me developer education from this AgentPack task.',\n"
        "    'files': ['cli.py'],\n"
        "    'concepts': ['developer education']\n"
        "  }]\n"
        "}))\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--concept-provider-command", f"python {provider}"])

    assert result.exit_code == 0, result.output
    text = (repo / ".agentpack" / "learning.md").read_text(encoding="utf-8")
    assert "developer education" in text
    assert "Developer Education" in text
    assert "Teach me developer education" in text


def test_learn_provider_command_failure_exits_nonzero(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    provider = repo / "provider.py"
    provider.write_text("raise SystemExit('bad provider')\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--provider-command", f"python {provider}"])

    assert result.exit_code == 1
    assert "Provider command failed" in result.output


def test_learn_concept_provider_command_failure_exits_nonzero_when_explicit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    provider = repo / "concept_provider.py"
    provider.write_text("raise SystemExit('bad concept provider')\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--concept-provider-command", f"python {provider}"])

    assert result.exit_code == 1
    assert "Concept provider command failed" in result.output


def test_learn_records_feedback(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(
        app,
        [
            "learn",
            "--feedback",
            "helpful",
            "--feedback-note",
            "Useful cards",
            "--feedback-target",
            "skill:CLI design",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = (repo / ".agentpack" / "learning-feedback.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    assert payload["feedback"] == "helpful"
    assert payload["note"] == "Useful cards"
    assert payload["target"] == "skill:CLI design"


def test_learn_skill_views_and_drills(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["learn"])
    assert result.exit_code == 0, result.output

    skills = runner.invoke(app, ["learn", "--skills"])
    drills = runner.invoke(app, ["learn", "--drills"])

    assert skills.exit_code == 0, skills.output
    assert "# AgentPack Skill Memory" in skills.output
    assert "CLI design" in skills.output
    assert drills.exit_code == 0, drills.output
    assert "# AgentPack Practice Drills" in drills.output


def test_learn_provider_preview_does_not_write_default_file(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--provider-preview"])

    assert result.exit_code == 0, result.output
    assert "# AgentPack Provider Preview" in result.output
    assert "`cli.py`" in result.output
    assert not (repo / ".agentpack" / "learning.md").exists()


def test_learn_provider_preview_skips_configured_concept_provider(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    provider = repo / "concept_provider.py"
    provider.write_text("raise SystemExit('should not run')\n", encoding="utf-8")
    (repo / ".agentpack" / "config.toml").write_text(
        f'[learning]\nconcept_provider_command = "python {provider}"\nconcept_provider_required = true\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--provider-preview"])

    assert result.exit_code == 0, result.output
    assert "# AgentPack Provider Preview" in result.output


def test_learn_configured_concept_provider_failure_warns_by_default(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    provider = repo / "concept_provider.py"
    provider.write_text("raise SystemExit('temporary unavailable')\n", encoding="utf-8")
    (repo / ".agentpack" / "config.toml").write_text(
        f'[learning]\nconcept_provider_command = "python {provider}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn"])

    assert result.exit_code == 0, result.output
    assert "Concept provider skipped" in result.output
    assert (repo / ".agentpack" / "learning.md").exists()


def test_learn_ci_quality_prints_quality_report(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["learn", "--ci"])

    assert result.exit_code == 0, result.output
    assert "# AgentPack Learning Quality" in result.output
    assert "Score:" in result.output


def test_learn_suppresses_renames_and_merges_skills(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["learn"])
    assert result.exit_code == 0, result.output

    rename = runner.invoke(app, ["learn", "--rename-skill", "CLI design=>CLI workflow"])
    suppress = runner.invoke(app, ["learn", "--suppress-skill", "CLI workflow"])

    assert rename.exit_code == 0, rename.output
    assert suppress.exit_code == 0, suppress.output
    payload = json.loads((repo / ".agentpack" / "skills-progress.json").read_text(encoding="utf-8"))
    assert payload["skills"]["CLI workflow"]["suppressed"] is True
