from __future__ import annotations

import shlex
import sys
import time

from agentpack.dashboard.terminal import TerminalSessionManager, inspect_command


def test_inspect_command_allows_agentpack_command(tmp_path) -> None:
    inspection = inspect_command("agentpack doctor --agent codex", root=tmp_path)

    assert inspection.allowed is True
    assert inspection.argv == ["agentpack", "doctor", "--agent", "codex"]
    assert inspection.confirm_required is False
    assert inspection.cwd == str(tmp_path)


def test_inspect_command_requires_confirmation_for_risky_agentpack_command(tmp_path) -> None:
    inspection = inspect_command("agentpack repair --agent all", root=tmp_path)

    assert inspection.allowed is True
    assert inspection.risky is True
    assert inspection.confirm_required is True
    assert inspection.risk_reasons


def test_inspect_command_requires_confirmation_for_finish_and_yes(tmp_path) -> None:
    finish = inspect_command("agentpack finish --summary done", root=tmp_path)
    prune = inspect_command("agentpack threads prune --older-than 7d --yes", root=tmp_path)

    assert finish.allowed is True
    assert finish.confirm_required is True
    assert prune.allowed is True
    assert prune.confirm_required is True


def test_inspect_command_rejects_non_agentpack_command(tmp_path) -> None:
    inspection = inspect_command("pytest tests/test_dashboard_command.py", root=tmp_path)

    assert inspection.allowed is False
    assert "only AgentPack commands" in inspection.reason


def test_inspect_command_rejects_shell_operators(tmp_path) -> None:
    inspection = inspect_command("agentpack doctor && rm -rf .", root=tmp_path)

    assert inspection.allowed is False
    assert "shell operators" in inspection.reason


def test_inspect_command_rejects_cwd_outside_project(tmp_path) -> None:
    outside = tmp_path.parent
    inspection = inspect_command("agentpack doctor", root=tmp_path, cwd=str(outside))

    assert inspection.allowed is False
    assert "cwd must be inside" in inspection.reason


def test_terminal_manager_blocks_unconfirmed_risky_command(tmp_path) -> None:
    manager = TerminalSessionManager(tmp_path)

    try:
        manager.start("agentpack repair --agent all")
    except PermissionError as exc:
        assert "confirmation required" in str(exc)
    else:
        raise AssertionError("risky command should require confirmation")


def test_terminal_session_completes_after_process_exit(tmp_path) -> None:
    manager = TerminalSessionManager(tmp_path)
    command = f"{shlex.quote(sys.executable)} -m agentpack.cli --version"

    session = manager.start(command)
    deadline = time.time() + 10
    while session.status not in {"completed", "failed"} and time.time() < deadline:
        time.sleep(0.05)

    output = "".join(event.data for event in session.events_after(0) if event.data)
    assert session.status == "completed"
    assert session.returncode == 0
    assert output.strip()
