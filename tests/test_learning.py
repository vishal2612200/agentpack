import subprocess
from pathlib import Path

from agentpack.application.pack_service import _apply_ranking_feedback_boosts
from agentpack.core.models import FileInfo
from agentpack.learning.collector import LearningInputs
from agentpack.learning.extractor import build_learning_report
from agentpack.learning.feedback import ranking_feedback_boosts, record_ranking_feedback
from agentpack.learning.episodes import record_episode
from agentpack.learning.procedures import record_procedure
from agentpack.learning.quality import score_learning_report
from agentpack.learning.renderers import render_agent_lessons_markdown, render_learning_markdown
from agentpack.learning.task_memory import build_task_memory_payload, build_task_start_snapshot


def test_learning_report_detects_selected_misses_and_concepts():
    inputs = LearningInputs(
        task="add mcp retrieval command",
        since="main",
        changed_files={"src/agentpack/mcp_server.py": "modified", "tests/test_mcp_server.py": "modified"},
        diffs={"src/agentpack/mcp_server.py": "+ def retrieve_context(): pass"},
        selected_files=["src/agentpack/mcp_server.py"],
    )

    report = build_learning_report(inputs)

    assert "mcp" in report.concepts
    assert report.selected_hits == ["src/agentpack/mcp_server.py"]
    assert report.selected_misses == ["tests/test_mcp_server.py"]
    assert report.learning_cards
    assert report.agent_lessons
    assert report.claim_citations["summary:1"]
    assert report.claim_citations["decision:1"]
    assert report.claim_citations["risk:1"]


def test_learning_renderers_are_grounded():
    inputs = LearningInputs(
        task="update cli config",
        changed_files={"src/agentpack/core/config.py": "modified"},
        selected_files=["src/agentpack/core/config.py"],
    )
    report = build_learning_report(inputs)

    markdown = render_learning_markdown(report)
    lessons = render_agent_lessons_markdown(report)
    quality = score_learning_report(report)

    assert "src/agentpack/core/config.py" in markdown
    assert "## Claim Citations" in markdown
    assert "src/agentpack/core/config.py" in lessons
    assert quality.score >= 70


def test_learning_records_ranking_feedback_for_selected_misses(tmp_path):
    report = build_learning_report(
        LearningInputs(
            task="add mcp retrieval command",
            changed_files={"src/agentpack/mcp_server.py": "modified", "tests/test_mcp_server.py": "modified"},
            selected_files=["src/agentpack/mcp_server.py"],
        )
    )

    count = record_ranking_feedback(tmp_path, report)
    boosts = ranking_feedback_boosts(tmp_path, "fix mcp retrieval followup")

    assert count == 1
    assert boosts["tests/test_mcp_server.py"] > 0


def test_ranking_feedback_boosts_scored_missed_paths(tmp_path):
    report = build_learning_report(
            LearningInputs(
                task="add mcp retrieval command",
                changed_files={"src/agentpack/mcp_server.py": "modified", "tests/test_mcp_server.py": "modified"},
                selected_files=["src/agentpack/mcp_server.py"],
            )
        )
    record_ranking_feedback(tmp_path, report)
    file_info = FileInfo(
        path="tests/test_mcp_server.py",
        abs_path=Path("/tmp/tests/test_mcp_server.py"),
        size_bytes=10,
        estimated_tokens=5,
    )

    scored = _apply_ranking_feedback_boosts(
        tmp_path,
        [(file_info, 10.0, ["filename keyword match"])],
        "fix mcp retrieval followup",
        set(),
    )

    assert scored[0][1] > 10.0
    assert any("learning feedback miss boost" in reason for reason in scored[0][2])


def test_episodic_procedure_boost_has_visible_reason(tmp_path):
    source = tmp_path / "src" / "auth" / "otp.py"
    source.parent.mkdir(parents=True)
    source.write_text("def send_otp():\n    return True\n", encoding="utf-8")
    record_procedure(
        tmp_path,
        procedure_id="otp-rate-limit-check",
        title="Verify OTP rate limit containment",
        triggers=["otp", "rate limit"],
        steps=["check successful sends"],
    )
    record_episode(
        tmp_path,
        task="fix otp rate limit behavior",
        selected_files=["src/auth/otp.py"],
        changed_files=["src/auth/otp.py"],
        passed=True,
        procedure_ids=["otp-rate-limit-check"],
    )
    file_info = FileInfo(
        path="src/auth/otp.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=5,
    )

    scored = _apply_ranking_feedback_boosts(
        tmp_path,
        [(file_info, 10.0, ["filename keyword match"])],
        "repair otp rate limit",
        set(),
    )

    assert scored[0][1] > 10.0
    assert any("confidence=" in reason and "procedure=Verify OTP rate limit containment" in reason for reason in scored[0][2])


def test_task_memory_uses_thread_scoped_pack_metadata(tmp_path):
    agentpack = tmp_path / ".agentpack"
    scoped = agentpack / "threads" / "claude-local"
    scoped.mkdir(parents=True)
    agentpack.mkdir(exist_ok=True)
    (agentpack / "pack_metadata.json").write_text('{"selected_files": ["src/global.py"]}', encoding="utf-8")
    (scoped / "pack_metadata.json").write_text('{"selected_files": ["src/scoped.py"]}', encoding="utf-8")

    payload = build_task_memory_payload(
        tmp_path,
        task="fix scoped session",
        stage="finish",
        status="done",
        thread="claude-local",
    )

    assert payload["selected_files"] == ["src/scoped.py"]


def test_task_start_snapshot_respects_empty_dirty_baseline(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "task.md").write_text("new task\n", encoding="utf-8")

    snapshot = build_task_start_snapshot(tmp_path, task="clean start", dirty_files_before=[])

    assert snapshot["dirty_files_before"] == []
