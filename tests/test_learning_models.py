from agentpack.core.config import DEFAULT_CONFIG, Config
from agentpack.learning.models import (
    AgentLesson,
    LearningCard,
    LearningOptions,
    LearningReport,
    LearningSourceFile,
    QuizQuestion,
    SkillEvidence,
    SkillProgress,
)


def test_learning_config_defaults():
    cfg = DEFAULT_CONFIG

    assert cfg.learning.markdown_output == ".agentpack/learning.md"
    assert cfg.learning.daily_output == ".agentpack/daily-summary.md"
    assert cfg.learning.max_changed_files == 20
    assert cfg.learning.max_diff_chars_per_file == 1200
    assert cfg.learning.max_cards == 5
    assert cfg.learning.max_quiz_questions == 5
    assert cfg.learning.skill_map_output == ".agentpack/skills-progress.json"
    assert cfg.learning.agent_lessons_output == ".agentpack/agent-lessons.md"
    assert cfg.learning.llm_prompt_output == ".agentpack/learning.prompt.md"
    assert cfg.learning.pr_comment_output == ".agentpack/pr-learning-comment.md"
    assert cfg.learning.dashboard_output == ".agentpack/learning-dashboard.html"
    assert cfg.learning.team_lessons_output == ".agentpack/team-lessons.md"
    assert cfg.learning.feedback_output == ".agentpack/learning-feedback.jsonl"
    assert cfg.learning.provider_command == ""
    assert cfg.learning.provider_timeout_seconds == 60
    assert cfg.learning.concept_provider_command == ""
    assert cfg.learning.concept_provider_timeout_seconds == 30
    assert cfg.learning.concept_provider_required is False
    assert cfg.learning.inject_agent_lessons is True
    assert cfg.learning.min_groundedness_score == 70
    assert cfg.runtime.observer_events_output == ".agentpack/observer-events.jsonl"
    assert cfg.runtime.observer_brief_output == ".agentpack/observer-brief.md"


def test_learning_config_model_accepts_overrides():
    cfg = Config.model_validate({
        "learning": {
            "markdown_output": ".agentpack/custom-learning.md",
            "daily_output": ".agentpack/custom-daily.md",
            "max_changed_files": 7,
            "max_diff_chars_per_file": 400,
            "max_cards": 3,
            "max_quiz_questions": 2,
            "skill_map_output": ".agentpack/custom-skills.json",
            "agent_lessons_output": ".agentpack/custom-agent-lessons.md",
            "llm_prompt_output": ".agentpack/custom-prompt.md",
            "pr_comment_output": ".agentpack/custom-pr.md",
            "dashboard_output": ".agentpack/custom-dashboard.html",
            "team_lessons_output": ".agentpack/custom-team.md",
            "feedback_output": ".agentpack/custom-feedback.jsonl",
            "provider_command": "learn-provider",
            "provider_timeout_seconds": 12,
            "concept_provider_command": "concept-provider",
            "concept_provider_timeout_seconds": 8,
            "concept_provider_required": True,
            "inject_agent_lessons": False,
            "min_groundedness_score": 80,
        }
    })

    assert cfg.learning.markdown_output == ".agentpack/custom-learning.md"
    assert cfg.learning.daily_output == ".agentpack/custom-daily.md"
    assert cfg.learning.max_changed_files == 7
    assert cfg.learning.max_diff_chars_per_file == 400
    assert cfg.learning.max_cards == 3
    assert cfg.learning.max_quiz_questions == 2
    assert cfg.learning.skill_map_output == ".agentpack/custom-skills.json"
    assert cfg.learning.agent_lessons_output == ".agentpack/custom-agent-lessons.md"
    assert cfg.learning.llm_prompt_output == ".agentpack/custom-prompt.md"
    assert cfg.learning.pr_comment_output == ".agentpack/custom-pr.md"
    assert cfg.learning.dashboard_output == ".agentpack/custom-dashboard.html"
    assert cfg.learning.team_lessons_output == ".agentpack/custom-team.md"
    assert cfg.learning.feedback_output == ".agentpack/custom-feedback.jsonl"
    assert cfg.learning.provider_command == "learn-provider"
    assert cfg.learning.provider_timeout_seconds == 12
    assert cfg.learning.concept_provider_command == "concept-provider"
    assert cfg.learning.concept_provider_timeout_seconds == 8
    assert cfg.learning.concept_provider_required is True
    assert cfg.learning.inject_agent_lessons is False
    assert cfg.learning.min_groundedness_score == 80


def test_learning_report_serializes_to_json_safe_dict():
    report = LearningReport(
        task="Add auth retry handling",
        scope="task",
        since="HEAD~1",
        issue_references=["#123"],
        issue_reference_details=[{"ref": "#123", "kind": "github_issue", "title": "Auth retry bug"}],
        source_files=[
            LearningSourceFile(
                path="src/app/auth.py",
                change_kind="modified",
                why="Changed token refresh behavior",
                concepts=["auth", "retry"],
            )
        ],
        summary=["Added retry handling for expired auth tokens."],
        concepts=["authentication", "retry logic"],
        decisions=["Keep retry local to auth client."],
        risks=["Retry loops can hide permanent auth failures."],
        tests=["Covered expired token retry."],
        learning_cards=[
            LearningCard(
                title="Retry Boundaries",
                body="Retries need a clear max attempt count and failure path.",
                files=["src/app/auth.py"],
            )
        ],
        quiz=[
            QuizQuestion(
                question="Why should auth retries have a max attempt count?",
                answer="To avoid infinite loops and surface permanent failures.",
            )
        ],
        agent_lessons=[
            AgentLesson(
                rule="When changing auth retry behavior, verify max attempts and final failure path.",
                evidence_files=["src/app/auth.py"],
                reason="Retry changes can otherwise hide permanent authentication failures.",
            )
        ],
        skill_evidence=[
            SkillEvidence(
                skill="retry logic",
                task="Add auth retry handling",
                evidence_files=["src/app/auth.py"],
                confidence=80,
            )
        ],
        next_practice="Add one regression test for max retry attempts.",
    )

    payload = report.model_dump(mode="json")

    assert payload["task"] == "Add auth retry handling"
    assert payload["issue_references"] == ["#123"]
    assert payload["issue_reference_details"][0]["title"] == "Auth retry bug"
    assert payload["source_files"][0]["path"] == "src/app/auth.py"
    assert payload["learning_cards"][0]["title"] == "Retry Boundaries"
    assert payload["agent_lessons"][0]["rule"].startswith("When changing auth retry")
    assert payload["skill_evidence"][0]["skill"] == "retry logic"


def test_learning_options_defaults():
    options = LearningOptions()

    assert options.scope == "task"
    assert options.since is None
    assert options.today is False
    assert options.json_output is False


def test_skill_progress_tracks_evidence_without_productivity_metrics():
    progress = SkillProgress(
        skill="CLI design",
        task_count=2,
        last_task="Add AgentPack Learn",
        evidence=[
            SkillEvidence(
                skill="CLI design",
                task="Add AgentPack Learn",
                evidence_files=["src/agentpack/commands/learn.py"],
                confidence=75,
            )
        ],
    )

    payload = progress.model_dump(mode="json")

    assert payload["skill"] == "CLI design"
    assert "commits_per_day" not in payload
    assert payload["evidence"][0]["evidence_files"] == ["src/agentpack/commands/learn.py"]
