from __future__ import annotations

from agentpack.router.models import SkillArtifact
from agentpack.router.scoring import score_skills
from agentpack.router.service import _load_skill_success


def test_scoring_prefers_pytest_and_webhook_skills():
    skills = [
        SkillArtifact(
            name="django-pytest",
            path=".claude/skills/django-pytest/SKILL.md",
            source=".claude/skills",
            description="Use for pytest test debugging, fixtures, mocks, and flaky tests.",
            triggers=["django", "pytest", "test", "fixture", "mock", "flaky"],
            tools_required=["pytest"],
            side_effect_level="command",
            applies_to_paths=["tests/**"],
        ),
        SkillArtifact(
            name="webhook-idempotency",
            path=".agentpack/skills/webhook-idempotency/SKILL.md",
            source=".agentpack/skills",
            description="Use for payment webhook idempotency.",
            triggers=["payment", "webhook", "idempotency"],
            side_effect_level="file_write",
            applies_to_paths=["payments/**"],
        ),
        SkillArtifact(
            name="rca-writer",
            path=".claude/skills/rca-writer/SKILL.md",
            source=".claude/skills",
            description="Use for incident analysis.",
            triggers=["incident", "postmortem", "rca"],
            side_effect_level="none",
        ),
    ]

    selected, warnings, all_scores = score_skills(
        skills,
        task="fix flaky payment webhook test",
        selected_paths=["payments/webhooks.py", "tests/test_payment_webhooks.py"],
        max_selected=3,
        allow_external=False,
    )

    assert warnings == []
    assert [item.skill.name for item in selected[:2]] == ["django-pytest", "webhook-idempotency"]
    assert all_scores[0].score > all_scores[-1].score


def test_external_skill_warned_not_selected_by_default():
    skills = [
        SkillArtifact(
            name="production-deploy-checklist",
            path=".claude/skills/production-deploy-checklist/SKILL.md",
            source=".claude/skills",
            description="Deploy to cloud and migrate production.",
            triggers=["deploy", "cloud", "production"],
            side_effect_level="external",
        )
    ]

    selected, warnings, _all_scores = score_skills(
        skills,
        task="deploy production API",
        selected_paths=["infra/prod-api.conf"],
        max_selected=3,
        allow_external=False,
    )

    assert selected == []
    assert warnings
    assert "production-deploy-checklist" in warnings[0]


def test_unpinned_general_coding_guideline_does_not_clear_confidence_threshold():
    skills = [
        SkillArtifact(
            name="karpathy-guidelines",
            path="skills/karpathy-guidelines/SKILL.md",
            source="skills",
            description=(
                "Behavioral guidelines to reduce common LLM coding mistakes. "
                "Use when writing, reviewing, or refactoring code to avoid "
                "overcomplication, make surgical changes, surface assumptions, "
                "and define verifiable success criteria."
            ),
            triggers=[
                "behavioral",
                "coding",
                "guidelines",
                "mistakes",
                "overcomplication",
                "refactoring",
                "reviewing",
                "surgical",
                "verifiable",
                "writing",
            ],
            side_effect_level="none",
        )
    ]

    selected, warnings, all_scores = score_skills(
        skills,
        task="fix auth token expiry bug",
        selected_paths=["src/auth/session.py"],
        max_selected=3,
        allow_external=False,
    )

    assert warnings == []
    assert selected == []
    assert "general coding guidance match" in all_scores[0].reasons


def test_always_recommend_boosts_safe_skill_for_coding_tasks():
    skills = [
        SkillArtifact(
            name="team-quality-bar",
            path="skills/team-quality-bar/SKILL.md",
            source="skills",
            description="Internal behavior guide.",
            side_effect_level="none",
        ),
        SkillArtifact(
            name="rca-writer",
            path="skills/rca-writer/SKILL.md",
            source="skills",
            description="Use for incident postmortems.",
            triggers=["incident", "postmortem"],
            side_effect_level="none",
        ),
    ]

    selected, warnings, all_scores = score_skills(
        skills,
        task="fix auth token expiry bug",
        selected_paths=["src/auth/session.py"],
        max_selected=1,
        allow_external=False,
        always_recommend=["team-quality-bar"],
    )

    assert warnings == []
    assert [item.skill.name for item in selected] == ["team-quality-bar"]
    assert "always-recommend skill" in all_scores[0].reasons


def test_always_recommend_does_not_boost_external_skills():
    skills = [
        SkillArtifact(
            name="prod-deploy",
            path="skills/prod-deploy/SKILL.md",
            source="skills",
            description="Deploy production and notify Slack.",
            side_effect_level="external",
        )
    ]

    selected, warnings, all_scores = score_skills(
        skills,
        task="fix auth token expiry bug",
        selected_paths=["src/auth/session.py"],
        max_selected=1,
        allow_external=False,
        always_recommend=["prod-deploy"],
    )

    assert selected == []
    assert warnings
    assert "always-recommend skill" not in all_scores[0].reasons


def test_rich_metadata_confidence_and_negative_matches():
    skills = [
        SkillArtifact(
            name="pytest-debugging",
            path="skills/pytest-debugging/SKILL.md",
            source="skills",
            description="Debug failing Python tests.",
            task_types=["debug", "test"],
            languages=["python"],
            frameworks=["pytest"],
            triggers=["assertion", "failure"],
            anti_triggers=["frontend"],
            side_effect_level="command",
            applies_to_paths=["tests/**"],
            priority=70,
            confidence_threshold=0.6,
        ),
        SkillArtifact(
            name="python-style-review",
            path="skills/python-style-review/SKILL.md",
            source="skills",
            description="Review Python style in source files.",
            languages=["python"],
            triggers=["python"],
            anti_paths=["tests/**"],
            side_effect_level="none",
            confidence_threshold=0.6,
        ),
    ]

    selected, warnings, all_scores = score_skills(
        skills,
        task="debug failing pytest assertion",
        selected_paths=["tests/test_auth.py", "src/auth.py"],
        selected_files=[
            {"path": "tests/test_auth.py", "score": 900},
            {"path": "src/auth.py", "score": 300},
        ],
        max_selected=2,
        allow_external=False,
    )

    assert warnings == []
    assert selected[0].skill.name == "pytest-debugging"
    assert selected[0].confidence >= 0.6
    assert "task type match: debug, test" in selected[0].reasons
    assert "language match: python" in selected[0].reasons
    assert all(item.skill.name != "python-style-review" for item in selected)
    assert any("anti-path match" in reason for reason in all_scores[-1].reasons)


def test_unrelated_safe_skill_is_not_selected_from_priority_alone():
    skills = [
        SkillArtifact(
            name="incident-rca",
            path="skills/incident-rca/SKILL.md",
            source="skills",
            description="Write incident postmortems.",
            triggers=["incident", "postmortem"],
            side_effect_level="none",
            priority=100,
        )
    ]

    selected, warnings, all_scores = score_skills(
        skills,
        task="fix auth token expiry bug",
        selected_paths=["src/auth/session.py"],
        max_selected=3,
        allow_external=False,
    )

    assert selected == []
    assert warnings == []
    assert all_scores == []


def test_diversity_penalty_avoids_three_redundant_skills():
    skills = [
        SkillArtifact(
            name="pytest-a",
            path="skills/pytest-a/SKILL.md",
            source="skills",
            description="pytest failure debugging",
            task_types=["test"],
            languages=["python"],
            frameworks=["pytest"],
            triggers=["pytest", "failure"],
            side_effect_level="command",
        ),
        SkillArtifact(
            name="pytest-b",
            path="skills/pytest-b/SKILL.md",
            source="skills",
            description="pytest assertion debugging",
            task_types=["test"],
            languages=["python"],
            frameworks=["pytest"],
            triggers=["pytest", "assertion"],
            side_effect_level="command",
        ),
        SkillArtifact(
            name="pytest-c",
            path="skills/pytest-c/SKILL.md",
            source="skills",
            description="pytest fixture debugging",
            task_types=["test"],
            languages=["python"],
            frameworks=["pytest"],
            triggers=["pytest", "fixture"],
            side_effect_level="command",
        ),
        SkillArtifact(
            name="auth-review",
            path="skills/auth-review/SKILL.md",
            source="skills",
            description="Review auth token expiry.",
            task_types=["security"],
            triggers=["auth", "token"],
            side_effect_level="none",
        ),
    ]

    selected, _warnings, _all_scores = score_skills(
        skills,
        task="fix pytest auth token failure",
        selected_paths=["tests/test_auth.py", "src/auth.py"],
        max_selected=3,
        allow_external=False,
    )

    assert "auth-review" in [item.skill.name for item in selected]


def test_historical_success_boosts_used_skill():
    skills = [
        SkillArtifact(
            name="auth-review",
            path="skills/auth-review/SKILL.md",
            source="skills",
            description="Review auth token expiry.",
            triggers=["auth", "token"],
            side_effect_level="none",
        )
    ]

    selected, _warnings, _all_scores = score_skills(
        skills,
        task="fix auth token expiry bug",
        selected_paths=["src/auth/session.py"],
        max_selected=1,
        allow_external=False,
        historical_success={"auth-review": 1.0},
    )

    assert selected[0].skill.name == "auth-review"
    assert any("historical success boost" in reason for reason in selected[0].reasons)


def test_recommended_only_feedback_does_not_create_history(tmp_path):
    feedback = tmp_path / ".agentpack" / "skill_feedback.jsonl"
    feedback.parent.mkdir()
    feedback.write_text(
        '{"task":"fix auth bug","recommended_skills":["auth-review"],'
        '"changed_files":["src/auth/session.py"],"tests_passed":true,'
        '"user_feedback":"helpful"}\n',
        encoding="utf-8",
    )

    history = _load_skill_success(tmp_path)

    assert history == {}


def test_ignored_feedback_requires_repetition_and_is_scoped(tmp_path):
    feedback = tmp_path / ".agentpack" / "skill_feedback.jsonl"
    feedback.parent.mkdir()
    line = (
        '{"task":"fix auth bug","recommended_skills":["deployment-checklist"],'
        '"ignored_skills":["deployment-checklist"],'
        '"changed_files":["src/auth/session.py"],"user_feedback":"ignored"}\n'
    )
    feedback.write_text(line, encoding="utf-8")

    assert _load_skill_success(tmp_path) == {}

    feedback.write_text(line + line, encoding="utf-8")
    history = _load_skill_success(tmp_path)

    assert history["deployment-checklist|task_type:bugfix"] < 0
    assert "deployment-checklist" not in history


def test_historical_noise_penalizes_scoped_skill_match():
    skill = SkillArtifact(
        name="deployment-checklist",
        path="skills/deployment-checklist/SKILL.md",
        source="skills",
        description="Review auth deployment checklist.",
        triggers=["auth"],
        side_effect_level="command",
    )

    _selected_without_history, _warnings, scores_without_history = score_skills(
        [skill],
        task="fix auth bug",
        selected_paths=["src/auth/session.py"],
        max_selected=1,
        allow_external=False,
    )
    selected_with_history, _warnings, scores_with_history = score_skills(
        [skill],
        task="fix auth bug",
        selected_paths=["src/auth/session.py"],
        max_selected=1,
        allow_external=False,
        historical_success={"deployment-checklist|task_type:bugfix": -0.25},
    )

    assert scores_with_history[0].score < scores_without_history[0].score
    assert selected_with_history == []
    assert any("historical noise penalty" in reason for reason in scores_with_history[0].reasons)
