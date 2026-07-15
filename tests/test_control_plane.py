from __future__ import annotations

import json
from pathlib import Path

from agentpack.control_plane import build_control_plane_snapshot, plan_next_actions
from agentpack.core.context_pack import save_pack_metadata
from agentpack.core.token_contract import build_token_contract


def test_control_plane_recommends_start_for_missing_thread_task(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")

    snapshot = build_control_plane_snapshot(tmp_path, thread_id="codex-local")
    actions = plan_next_actions(snapshot)

    assert snapshot.task.thread_id == "codex-local"
    assert snapshot.task.has_task is False
    assert [item.kind for item in actions][:2] == ["missing_task", "stale_context"]
    assert "--thread codex-local" in actions[0].command


def test_control_plane_does_not_duplicate_refresh_thread_flag(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")

    snapshot = build_control_plane_snapshot(tmp_path, thread_id="codex-local")
    stale = next(item for item in plan_next_actions(snapshot) if item.kind == "stale_context")

    assert stale.command.count("--thread") == 1
    assert stale.command.endswith("--thread codex-local")


def test_control_plane_uses_token_contract_without_repo_scan(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text("[context]\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "task.md").write_text("fix auth bug\n", encoding="utf-8")
    save_pack_metadata(
        tmp_path,
        context_path=".agentpack/context.md",
        snapshot_root_hash="old",
        task="fix auth bug",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=900,
        selected_files=[
            {"path": "src/auth.py", "mode": "full", "tokens": 800},
            {"path": "tests/test_auth.py", "mode": "summary", "tokens": 100},
        ],
        token_contract=build_token_contract(
            budget=1000,
            token_estimate=900,
            selected_files=[
                {"path": "src/auth.py", "mode": "full", "tokens": 800},
                {"path": "tests/test_auth.py", "mode": "summary", "tokens": 100},
            ],
            context_path=".agentpack/context.md",
            mode="balanced",
        ),
    )

    snapshot = build_control_plane_snapshot(tmp_path, check_files=False)

    assert snapshot.context.status == "fresh"
    assert snapshot.context.checked_files is False
    assert snapshot.tokens.estimated_tokens == 900
    assert snapshot.tokens.largest_sections[0]["path"] == "src/auth.py"
    assert "near the budget" in snapshot.tokens.recommended_next_context


def test_pack_metadata_persists_token_contract(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    contract = build_token_contract(
        budget=2000,
        token_estimate=500,
        selected_files=[{"path": "app.py", "mode": "full", "tokens": 500}],
        context_path=".agentpack/context.md",
        mode="balanced",
    )
    save_pack_metadata(
        tmp_path,
        context_path=".agentpack/context.md",
        snapshot_root_hash="hash",
        task="fix cache",
        agent="generic",
        mode="balanced",
        budget=2000,
        token_estimate=500,
        token_contract=contract,
    )

    payload = json.loads((tmp_path / ".agentpack" / "pack_metadata.json").read_text(encoding="utf-8"))
    assert payload["token_contract"]["estimated_tokens"] == 500
    assert payload["token_contract"]["recommended_next_context"]
