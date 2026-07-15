from __future__ import annotations

import re
from pathlib import Path

import typer.main
from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.command_surface import (
    _commands_from_help,
    available_cli_commands,
    missing_commands,
    prompt_quality_guidance,
    refresh_command_args,
    refresh_commands,
)
from agentpack.installers.antigravity import _gemini_block
from agentpack.installers.claude import _agentpack_block as claude_block
from agentpack.installers.codex import _agentpack_block as codex_block
from agentpack.installers.cursor import _cursor_rule
from agentpack.installers.windsurf import _windsurf_rule


GENERATED_INSTRUCTION_FILES = [
    "src/agentpack/data/codex_plugin/skills/agentpack.md",
    "src/agentpack/data/codex_plugin/skills/agentpack-pack.md",
    "src/agentpack/data/codex_plugin/skills/agentpack-refresh.md",
    "src/agentpack/data/codex_plugin/skills/agentpack-review.md",
    "src/agentpack/data/codex_plugin/skills/agentpack-route.md",
    "src/agentpack/data/codex_plugin/skills/agentpack-learn.md",
    "agent-rules/agentpack.md",
]
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_available_cli_commands_match_typer_registry() -> None:
    click_cmd = typer.main.get_command(app)

    assert set(available_cli_commands()) == set(click_cmd.commands)
    assert "pack" in available_cli_commands()


def test_root_help_puts_first_run_commands_first() -> None:
    result = CliRunner().invoke(app, ["--help"])
    click_cmd = typer.main.get_command(app)
    commands = list(click_cmd.commands)

    assert result.exit_code == 0, result.output
    assert commands[:7] == ["quickstart", "start", "next", "doctor", "init", "route", "pack"]
    assert commands.index("quickstart") < commands.index("benchmark")


def test_root_help_groups_advanced_commands_without_hiding_them() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "Core loop" in result.output
    assert "Advanced, diagnostics, and release" in result.output
    assert "release-check" in result.output
    assert "Usage: root release-check" in CliRunner().invoke(app, ["release-check", "--help"]).output


def test_commands_from_help_handles_rich_table_rows() -> None:
    help_text = """
│ global-install       Install global shell/git automation                    │
│ global-repair-hooks  Repair global git template hooks                       │
│ guard                Executable pre-edit gate                               │
"""

    commands = _commands_from_help(help_text)

    assert "global-install" in commands
    assert "global-repair-hooks" in commands
    assert "guard" in commands


def test_generated_agent_rules_reference_available_commands() -> None:
    blocks = [
        codex_block(),
        claude_block(),
        _cursor_rule(),
        _windsurf_rule(),
        _gemini_block(),
    ]
    blocks.extend((REPO_ROOT / path).read_text(encoding="utf-8") for path in GENERATED_INSTRUCTION_FILES)
    referenced = set()
    for block in blocks:
        referenced.update(re.findall(r"\bagentpack ([a-z][a-z0-9-]*)", block))

    assert missing_commands(referenced) == []


def test_refresh_commands_fall_back_when_guard_missing(monkeypatch) -> None:
    monkeypatch.setattr("agentpack.core.command_surface.available_cli_commands", lambda: ("pack", "repair"))

    commands = refresh_commands("codex")

    assert commands.used_guard is False
    assert commands.primary == "agentpack pack --agent codex --task auto"
    assert refresh_command_args("codex", "balanced", 1200) == [
        "pack",
        "--agent",
        "codex",
        "--task",
        "auto",
        "--mode",
        "balanced",
        "--budget",
        "1200",
    ]
    for block in [codex_block(), claude_block(), _cursor_rule(), _windsurf_rule(), _gemini_block()]:
        assert "agentpack guard" not in block
        assert "--repair-stale" not in block


def test_refresh_commands_make_global_guard_explicit(monkeypatch) -> None:
    monkeypatch.setattr("agentpack.core.command_surface.available_cli_commands", lambda: ("guard",))

    commands = refresh_commands("codex")

    assert commands.primary == "agentpack guard --agent codex --repair-stale --refresh-context --thread global"
    assert commands.repair == commands.primary
    assert commands.thread_auto == (
        "AGENTPACK_THREAD_ID=<stable-id> "
        "agentpack guard --agent codex --repair-stale --refresh-context --thread auto"
    )


def test_static_refresh_guidance_uses_portable_commands() -> None:
    static_files = [
        REPO_ROOT / "skills" / "agentpack-refresh.md",
        REPO_ROOT / "src" / "agentpack" / "data" / "agentpack.md",
    ]

    for path in static_files:
        content = path.read_text(encoding="utf-8")
        assert "agentpack guard" not in content
        assert '"$AGENTPACK_BIN" guard' not in content


def test_generated_agent_rules_include_prompt_quality_guidance() -> None:
    guidance = prompt_quality_guidance()

    for block in [codex_block(), claude_block(), _cursor_rule(), _windsurf_rule(), _gemini_block()]:
        assert guidance in block
        assert "Acceptance criteria" in block
        assert "Ask/Chat mode" in block
