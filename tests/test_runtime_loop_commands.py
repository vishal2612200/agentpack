from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.models import ContextPack, FileInfo, SelectedFile
from agentpack.core.pack_registry import save_pack_registry
from agentpack.core.scanner import file_hash


runner = CliRunner()


def test_perf_command_reads_session_events(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "session-events.jsonl").write_text(
        json.dumps({"type": "pack", "raw_tokens": 100, "packed_tokens": 25}) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["perf"])

    assert result.exit_code == 0
    assert "estimated saved tokens" in result.stdout


def test_perf_command_shows_history(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "session-events.jsonl").write_text(
        "\n".join([
            json.dumps({
                "type": "pack",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "raw_tokens": 100,
                "packed_tokens": 25,
            }),
            json.dumps({"type": "retrieve", "timestamp": "2026-01-01T00:01:00+00:00", "path": "src.py"}),
        ]),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["perf", "--history", "2"])

    assert result.exit_code == 0
    assert "Recent Events" in result.stdout
    assert "src.py" in result.stdout


def test_perf_measure_pack_compares_broad_profiles(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\ndefault_budget = 12000\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task.md").write_text("share broad repo context for review\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

    result = runner.invoke(app, ["perf", "--measure-pack", "--repeat", "1"])

    assert result.exit_code == 0, result.output
    assert "AgentPack Pack Profile" in result.stdout
    assert "broad-off" in result.stdout
    assert "broad-on" in result.stdout


def test_compress_output_command_reads_file(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / "out.txt").write_text("noise\n" * 50 + "ERROR src/app.py:10 failed\n", encoding="utf-8")

    result = runner.invoke(app, ["compress-output", "out.txt", "--kind", "pytest"])

    assert result.exit_code == 0
    assert "ERROR src/app.py:10 failed" in result.stdout


def test_memory_command_outputs_json(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "session-events.jsonl").write_text(
        json.dumps({
            "type": "learn",
            "task": "fix auth #123",
            "concepts": ["cli"],
            "issue_references": ["#123"],
            "issue_reference_details": [{"ref": "#123", "kind": "github_issue", "title": "Auth bug", "state": "OPEN"}],
        }) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["memory", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["recent_tasks"] == ["fix auth #123"]
    assert payload["recent_issue_references"] == ["#123"]
    assert payload["issue_reference_details"][0]["title"] == "Auth bug"
    assert payload["top_issue_references"] == [["#123", 1]]


def test_memory_timeline_outputs_timestamped_graph_rows(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / "src.py").write_text("current\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task-starts.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": "task:abc",
                "started_at": "2026-01-01T00:00:00+00:00",
                "snapshot_version": "task:abc@2026-01-01T00:00:00+00:00",
                "visible_reason": "task-start map captured before agent edits",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".agentpack" / "episodic-cases.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "episode_id": "episode:abc",
                "task_id": "task:abc",
                "completed_at": "2026-01-01T00:01:00+00:00",
                "episode_version": "episode:abc@2026-01-01T00:01:00+00:00",
                "path_hashes": {"src.py": "old-hash"},
                "visible_reason": "completed task episode with source hashes",
                "confidence": 0.9,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".agentpack" / "procedures.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "procedure_id": "procedure:retry",
                "version": 2,
                "version_key": "procedure:retry@v2",
                "procedure_hash": "procedure-hash",
                "updated_at": "2026-01-01T00:02:00+00:00",
                "title": "Retry safely",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".agentpack" / "memory-edges.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "edge_version": "edge:abc",
                "edge_type": "episode_procedure",
                "from_id": "episode:abc",
                "to_id": "procedure:retry",
                "observed_at": "2026-01-01T00:03:00+00:00",
                "confidence": 0.8,
                "reason": "procedure linked by completed episode",
                "relationship_hash": "relationship-hash",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["memory", "--timeline", "--json", "--limit", "10"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    rows = payload["timeline"]
    assert [row["kind"] for row in rows] == ["task_start", "episode", "procedure", "memory_edge"]
    assert rows[1]["version"] == "episode:abc@2026-01-01T00:01:00+00:00"
    assert rows[1]["is_stale"] is True
    assert rows[1]["stale_paths"] == ["src.py"]
    assert rows[2]["record_hash"] == "procedure-hash"
    assert rows[3]["from_id"] == "episode:abc"
    assert rows[3]["to_id"] == "procedure:retry"
    assert rows[3]["record_hash"] == "relationship-hash"


def test_memory_prune_retains_configured_tail(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "session-events.jsonl").write_text(
        "\n".join(json.dumps({"type": "pack", "idx": idx}) for idx in range(5)) + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".agentpack" / "episodic-cases.jsonl").write_text(
        "\n".join(json.dumps({"task": str(idx)}) for idx in range(4)) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["memory", "--prune", "--max-events", "2", "--max-episodes", "1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["session_events"]["pruned"] == 3
    assert payload["episodic_cases"]["pruned"] == 3
    events = (tmp_path / ".agentpack" / "session-events.jsonl").read_text(encoding="utf-8")
    assert '"idx": 3' in events
    assert '"idx": 0' not in events


def test_wrap_dry_run_packs_without_launching(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()

    result = runner.invoke(app, ["wrap", "codex", "--task", "inspect empty repo", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Context ready" in result.output
    assert "AGENTPACK_CONTEXT=" in result.output
    assert "No codex setup file found" in result.output
    assert "Launch command:" in result.output


def test_retrieve_command_reads_pack_registry(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()
    source = tmp_path / "src.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    pack = ContextPack(
        task="test",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=10,
        raw_repo_tokens=100,
        after_ignore_tokens=100,
        estimated_savings_percent=90,
        changed_files=["src.py"],
        selected_files=[
            SelectedFile(
                path="src.py",
                score=100,
                include_mode="full",
                reasons=["modified"],
                content="def run():\n    return 1\n",
            )
        ],
        receipts=[],
        freshness={"snapshot_root_hash": "abc", "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    info = FileInfo(
        path="src.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=10,
        hash=file_hash(source),
    )
    save_pack_registry(tmp_path, pack, [info])

    result = runner.invoke(app, ["retrieve", "src.py"])

    assert result.exit_code == 0
    assert "def run()" in result.stdout


def test_learn_feedback_command_records_feedback(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentpack").mkdir()

    result = runner.invoke(app, ["learn", "feedback", "helpful", "--target", "card:1"])

    assert result.exit_code == 0
    record = json.loads(
        (tmp_path / ".agentpack" / "learning-feedback.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert record["feedback"] == "helpful"
    assert record["target"] == "card:1"
