from pathlib import Path
import subprocess

from agentpack.learning.collector import LearningInputs, collect_learning_inputs
from agentpack.learning.extractor import build_learning_report


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _make_git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    return tmp_path


def test_collect_learning_inputs_reads_task_and_changed_files(tmp_path):
    repo = _make_git_repo(tmp_path)
    (repo / ".agentpack").mkdir()
    (repo / ".agentpack" / "task.md").write_text("Add auth retry handling for GH-99\n", encoding="utf-8")
    (repo / "auth.py").write_text("def login():\n    return 'ok'\n", encoding="utf-8")
    _git(repo, "add", ".agentpack/task.md", "auth.py")
    _git(repo, "commit", "-m", "initial")

    (repo / "auth.py").write_text("def login():\n    return 'retry'\n", encoding="utf-8")

    collected = collect_learning_inputs(repo, since=None, max_changed_files=20, max_diff_chars_per_file=500)

    assert collected.task == "Add auth retry handling for GH-99"
    assert collected.issue_references == ["GH-99"]
    assert collected.changed_files["auth.py"] == "modified"
    assert "retry" in collected.diffs["auth.py"]


def test_build_learning_report_extracts_concepts_tests_and_quiz():
    inputs = LearningInputs(
        task="Add auth retry handling for #42",
        since="HEAD~1",
        issue_references=["#42"],
        issue_reference_details=[{"ref": "#42", "kind": "github_issue", "title": "Retry bug"}],
        changed_files={
            "src/app/auth.py": "modified",
            "tests/test_auth.py": "modified",
        },
        diffs={
            "src/app/auth.py": "+ retry_count = 3\n+ raise AuthError('expired token')\n",
            "tests/test_auth.py": "+ def test_retries_expired_token():\n+     assert login() == 'ok'\n",
        },
    )

    report = build_learning_report(inputs, max_cards=5, max_quiz_questions=5)

    assert report.task == "Add auth retry handling for #42"
    assert report.issue_references == ["#42"]
    assert report.issue_reference_details[0]["title"] == "Retry bug"
    assert "authentication" in report.concepts
    assert "retry logic" in report.concepts
    assert report.tests == ["Updated tests/test_auth.py for authentication/retry logic/testing behavior."]
    assert report.learning_cards
    assert report.quiz
    assert report.agent_lessons
    assert report.agent_lessons[0].evidence_files
    assert report.skill_evidence
    assert report.skill_evidence[0].task == "Add auth retry handling for #42"
    assert "retry" in report.next_practice.lower()


def test_build_learning_report_creates_copy_ready_rate_limit_redis_topic():
    inputs = LearningInputs(
        task="Implement Redis rate limiting",
        since="HEAD~1",
        changed_files={
            "src/rate_limit.py": "modified",
            "tests/test_rate_limit.py": "modified",
        },
        diffs={
            "src/rate_limit.py": "+ redis.incr(key)\n+ redis.expire(key, ttl)\n+ raise TooManyRequests(status_code=429)\n",
            "tests/test_rate_limit.py": "+ def test_rate_limit_returns_429():\n+     assert response.status_code == 429\n",
        },
    )

    report = build_learning_report(inputs, max_cards=5, max_quiz_questions=5)

    assert "rate limiting" in report.concepts
    assert "caching" in report.concepts
    topic = report.learning_topics[0]
    assert topic.title == "Implementing Rate Limits With Redis"
    assert "Redis-backed counters" in topic.why
    assert "src/rate_limit.py" in topic.files
    assert "Topic: Implementing Rate Limits With Redis" in topic.prompt
    assert topic.questions
    assert topic.questions[0].expected_points
    assert "Evidence files: src/rate_limit.py" in topic.prompt


def test_build_learning_report_stays_bounded():
    inputs = LearningInputs(
        task="Refactor cache config",
        since=None,
        changed_files={f"src/file_{i}.py": "modified" for i in range(20)},
        diffs={f"src/file_{i}.py": "+ cache timeout config\n" for i in range(20)},
    )

    report = build_learning_report(inputs, max_cards=3, max_quiz_questions=2)

    assert len(report.learning_cards) <= 3
    assert len(report.learning_topics) <= 3
    assert len(report.quiz) <= 2
    assert len(report.agent_lessons) <= 3
