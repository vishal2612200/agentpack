import json
from pathlib import Path

from agentpack.core.evals import append_eval_cases_from_episodes, load_eval_cases
from agentpack.learning.episodes import episodic_memory_boosts, episodic_memory_matches, record_episode
from agentpack.learning.procedures import record_procedure


def test_episodic_memory_boosts_similar_passed_tasks(tmp_path: Path) -> None:
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "auth" / "otp.py").write_text("def check():\n    return True\n", encoding="utf-8")
    (tmp_path / "tests" / "test_otp.py").write_text("def test_check():\n    assert True\n", encoding="utf-8")
    record_episode(
        tmp_path,
        task="fix otp rate limit 429 behavior",
        selected_files=["src/auth/otp.py"],
        changed_files=["src/auth/otp.py", "tests/test_otp.py"],
        checks=[{"name": "tests", "passed": True}],
        passed=True,
        failure_class="context",
        failure_source="agent_failed",
    )

    boosts = episodic_memory_boosts(tmp_path, "repair otp rate limit retry-after")

    assert boosts["src/auth/otp.py"] > 0
    assert boosts["tests/test_otp.py"] > 0


def test_episodic_memory_records_nodes_edges_and_procedure_matches(tmp_path: Path) -> None:
    source = tmp_path / "src" / "auth" / "otp.py"
    source.parent.mkdir(parents=True)
    source.write_text("def send_otp():\n    return True\n", encoding="utf-8")
    record_procedure(
        tmp_path,
        procedure_id="otp-rate-limit-check",
        title="Verify OTP rate limit containment",
        triggers=["otp", "rate limit", "429"],
        steps=["check successful sends", "compare backend and provider counts"],
    )

    record_episode(
        tmp_path,
        task="fix otp rate limit 429 behavior",
        selected_files=["src/auth/otp.py"],
        changed_files=["src/auth/otp.py"],
        passed=True,
        task_id="task:otp",
        touched_nodes=[
            {
                "node_id": "node:send-otp",
                "path": "src/auth/otp.py",
                "symbol": "send_otp",
                "kind": "function",
                "source_hash": "hash",
            }
        ],
        procedure_ids=["otp-rate-limit-check"],
    )

    matches = episodic_memory_matches(tmp_path, "repair otp rate limit retry-after")
    edges_text = (tmp_path / ".agentpack" / "memory-edges.jsonl").read_text(encoding="utf-8")
    edges = [json.loads(line) for line in edges_text.splitlines()]

    assert matches[0]["node_ids"] == ["node:send-otp"]
    assert matches[0]["procedures"][0]["procedure_id"] == "otp-rate-limit-check"
    assert "confidence" in matches[0]
    assert "node:send-otp" in edges_text
    assert "otp-rate-limit-check" in edges_text
    assert all(edge["relationship_hash"] for edge in edges)
    assert all(edge["visible_reason"] for edge in edges)


def test_episodic_memory_skips_stale_hashes(tmp_path: Path) -> None:
    source = tmp_path / "src" / "auth" / "otp.py"
    source.parent.mkdir(parents=True)
    source.write_text("def check():\n    return True\n", encoding="utf-8")
    record_episode(
        tmp_path,
        task="fix otp rate limit 429 behavior",
        selected_files=["src/auth/otp.py"],
        changed_files=["src/auth/otp.py"],
        passed=True,
    )

    source.write_text("def check():\n    return False\n", encoding="utf-8")

    assert episodic_memory_boosts(tmp_path, "repair otp rate limit retry-after") == {}


def test_episodic_memory_skips_missing_paths(tmp_path: Path) -> None:
    record_episode(
        tmp_path,
        task="fix otp rate limit 429 behavior",
        selected_files=["src/auth/otp.py"],
        changed_files=["src/auth/otp.py"],
        passed=True,
    )

    assert episodic_memory_boosts(tmp_path, "repair otp rate limit retry-after") == {}


def test_episodic_memory_ignores_failed_tasks(tmp_path: Path) -> None:
    source = tmp_path / "src" / "billing.py"
    source.parent.mkdir(parents=True)
    source.write_text("def retry():\n    return False\n", encoding="utf-8")
    record_episode(
        tmp_path,
        task="fix billing webhook retry",
        selected_files=["src/billing.py"],
        changed_files=["src/billing.py"],
        passed=False,
    )

    matches = episodic_memory_matches(tmp_path, "billing webhook retry")

    assert episodic_memory_boosts(tmp_path, "billing webhook retry") == {}
    assert matches[0]["negative_guidance"] is True
    assert matches[0]["boost"] == 0


def test_failed_episode_can_be_promoted_to_eval_case(tmp_path: Path) -> None:
    record_episode(
        tmp_path,
        task="fix checkout retry ordering",
        selected_files=["src/payments.py"],
        changed_files=["src/payments.py", "tests/test_payments.py"],
        passed=False,
        failure_class="context",
        failure_source="agent_failed",
    )
    cases_path = tmp_path / ".agentpack" / "evals.toml"

    count = append_eval_cases_from_episodes(cases_path, root=tmp_path)
    cases = load_eval_cases(cases_path)

    assert count == 1
    assert cases[0].task == "fix checkout retry ordering"
    assert cases[0].required_changed_files == ["src/payments.py", "tests/test_payments.py"]
