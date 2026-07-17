from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import typer
from rich.markup import escape

from agentpack.integrations.global_install import (
    _GIT_TEMPLATE_DIR,
    _AGENTPACK_MARKER,
    _SHELL_MARKER_START,
    _HOOK_SCRIPTS,
    _detect_rc_file,
)
from agentpack.commands._shared import console, _root
from agentpack.core.command_surface import available_cli_commands, installed_cli_status, refresh_commands
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.ignore import agentignore_sync_status, format_import_summary
from agentpack.core.mcp_runtime import McpRuntimeCheck, check_mcp_runtime
from agentpack.core.task_freshness import task_freshness
from agentpack.core.thread_context import detect_conflicts, list_thread_rows
from agentpack.integrations.agents import SUPPORTED_AGENTS, check_agent_integration, expand_agents, install_agent_integration


def register(app: typer.Typer) -> None:
    @app.command()
    def doctor(
        agent: str = typer.Option(
            "auto",
            "--agent",
            help=f"Agent integration to audit ({' | '.join(SUPPORTED_AGENTS)}). Use all for the full matrix.",
        ),
        fix: bool = typer.Option(False, "--fix", help="Apply safe AgentPack repairs before reporting."),
    ) -> None:
        """Diagnose agentpack installation state — global hooks, per-repo config, agent setup."""
        ok = True
        if agent not in SUPPORTED_AGENTS:
            console.print(f"[yellow]Unknown agent: {agent}. Supported: {', '.join(SUPPORTED_AGENTS)}[/]")
            raise typer.Exit(1)
        root = _root()
        if fix:
            _print_repair_boundary(agent)
            _safe_fix(root, agent)

        # --- CLI binary ---
        console.print("[bold]CLI[/]")
        cli_status = installed_cli_status()
        binary = cli_status.get("binary") or shutil.which("agentpack")
        console.print(f"  [green]✓[/] importable AgentPack version: {cli_status.get('agentpack_version')}")
        importable = cli_status.get("importable_commands") or []
        if importable:
            console.print(f"  [green]✓[/] importable commands: {', '.join(str(cmd) for cmd in importable)}")
        if binary:
            try:
                result = subprocess.run(["agentpack", "--version"], capture_output=True, text=True)
                ver = result.stdout.strip() or result.stderr.strip()
                console.print(f"  [green]✓[/] agentpack found at {binary} ({ver})")
            except Exception:
                console.print(f"  [green]✓[/] agentpack found at {binary}")
        else:
            console.print("  [red]✗[/] agentpack not on PATH — run: pipx install agentpack-cli")
            ok = False
        help_commands = cli_status.get("help_commands") or []
        if help_commands:
            console.print(f"  [green]✓[/] installed CLI commands: {', '.join(str(cmd) for cmd in help_commands)}")
        if binary and "guard" not in set(str(cmd) for cmd in help_commands) and "guard" in set(available_cli_commands()):
            console.print(
                "  [yellow]![/] installed CLI command surface may be stale; "
                f"exact repair: {cli_status.get('repair_command')}"
            )
            _print_action(
                indent="  ",
                what_failed="installed CLI command surface is stale",
                why_it_matters="agent instructions may suggest commands this installed binary cannot run",
                repair_command=str(cli_status.get("repair_command") or "pipx upgrade agentpack-cli"),
                safe_to_continue="yes for direct repo work; no for AgentPack-managed refresh until repaired",
            )
            ok = False

        try:
            root = _root()
            warning = _source_checkout_warning(root, Path(__file__), sys.executable, binary)
            if warning:
                console.print(f"  [yellow]![/] {warning}")
                ok = False
        except Exception:
            pass

        # --- Git template hooks ---
        console.print("\n[bold]Git template hooks (~/.git-templates/hooks/)[/]")
        hooks_dir = _GIT_TEMPLATE_DIR / "hooks"
        if not hooks_dir.exists():
            console.print("  [yellow]![/] ~/.git-templates/hooks/ does not exist — run: agentpack global-install")
            ok = False
        else:
            for name in _HOOK_SCRIPTS:
                hook_path = hooks_dir / name
                if hook_path.exists() and _AGENTPACK_MARKER in hook_path.read_text():
                    console.print(f"  [green]✓[/] {name}")
                else:
                    console.print(f"  [red]✗[/] {name} missing — run: agentpack global-install --no-shell-hook --no-pipx")
                    ok = False

        # --- git config init.templateDir ---
        console.print("\n[bold]git config init.templateDir[/]")
        try:
            result = subprocess.run(
                ["git", "config", "--global", "init.templateDir"],
                capture_output=True, text=True,
            )
            configured_dir = result.stdout.strip()
            if configured_dir == str(_GIT_TEMPLATE_DIR):
                console.print(f"  [green]✓[/] init.templateDir = {configured_dir}")
            elif configured_dir:
                console.print(f"  [yellow]![/] init.templateDir = {configured_dir} (not agentpack's dir)")
            else:
                console.print("  [red]✗[/] init.templateDir not set — run: agentpack global-install --no-shell-hook --no-pipx")
                ok = False
        except Exception:
            console.print("  [yellow]![/] Could not check git config")

        # --- Shell hook ---
        console.print("\n[bold]Shell cd hook[/]")
        rc_file = _detect_rc_file()
        if rc_file is None:
            console.print(f"  [yellow]![/] Unknown shell ({os.environ.get('SHELL', 'unset')}) — cannot check")
        elif not rc_file.exists():
            console.print(f"  [red]✗[/] {rc_file} does not exist — run: agentpack global-install --no-git-template --no-pipx")
            ok = False
        elif _SHELL_MARKER_START in rc_file.read_text():
            console.print(f"  [green]✓[/] Hook present in {rc_file}")
        else:
            console.print(f"  [red]✗[/] Hook missing from {rc_file} — run: agentpack global-install --no-git-template --no-pipx")
            ok = False

        # --- Per-repo state ---
        console.print("\n[bold]Per-repo state[/]")
        try:
            root = _root()
        except Exception:
            console.print("  [dim]Not in a git repository[/]")
            _print_summary(ok)
            return

        config_path = root / ".agentpack" / "config.toml"
        if not config_path.exists():
            console.print(f"  [yellow]![/] Not initialized in {root} — run: agentpack init")
        else:
            console.print("  [green]✓[/] .agentpack/config.toml present")
            for finding in _agentignore_sync_findings(root):
                if finding.startswith("synced:"):
                    console.print(f"  [green]✓[/] {finding.split(':', 1)[1].strip()}")
                else:
                    console.print(f"  [yellow]![/] {finding}")
                    ok = False
            context_path = _latest_context_path(root)
            if context_path.exists():
                import time
                age = time.time() - context_path.stat().st_mtime
                age_str = f"{int(age // 3600)}h {int((age % 3600) // 60)}m" if age > 3600 else f"{int(age // 60)}m"
                console.print(f"  [green]✓[/] context pack present (age: {age_str})")
                task_state = task_freshness(root, load_pack_metadata(root))
                if task_state.is_stale:
                    console.print(
                        "  [yellow]![/] task context stale — "
                        f"packed: {task_state.packed_task}; current: {task_state.current_task}. "
                        "MCP get_context auto-refreshes this, or run: agentpack pack --task auto"
                    )
                    ok = False
                meta = load_pack_metadata(root) or {}
                freshness = meta.get("freshness") or {}
                if freshness:
                    console.print(
                        "  [green]✓[/] context provenance: "
                        f"task_source={freshness.get('task_source', 'unknown')}, "
                        f"git_root={freshness.get('git_root', root)}, "
                        f"worktree={freshness.get('worktree_path', root)}, "
                        f"branch={freshness.get('git_branch', 'unknown')}, "
                        f"generated={freshness.get('generated_at', 'unknown')}"
                    )
            else:
                console.print(f"  [yellow]![/] No context pack yet — write .agentpack/task.md, then run: {refresh_commands(agent).context_missing}")
            for line, healthy in _memory_findings(root):
                console.print(("  [green]✓[/] " if healthy else "  [yellow]![/] ") + line)

        # --- Agent-specific config ---
        console.print("\n[bold]Agent config[/]")
        _check_agent_file(root, "CLAUDE.md", "claude")
        _check_agent_file(root, ".cursorrules", "cursor")
        _check_agent_file(root, ".windsurfrules", "windsurf")
        _check_agent_file(root, "AGENTS.md", "codex")

        claude_settings = root / ".claude" / "settings.json"
        global_claude_settings = Path.home() / ".claude" / "settings.json"
        import json as _json
        _local_has_hooks = False
        _global_has_hooks = False

        def _has_stale_hooks(hooks: dict) -> bool:
            """Detect old inline-Python or context-injection hooks that should be upgraded."""
            all_cmds = [
                h.get("command", "")
                for event_hooks in hooks.values()
                for entry in event_hooks
                for h in entry.get("hooks", [])
            ]
            return any(
                "context.claude.md" in cmd
                or ".context_injected" in cmd
                or (".mcp_reminded" in cmd and "python3" in cmd)
                or ("agentpack pack" in cmd and "GitAutoRepack" not in cmd)
                for cmd in all_cmds
            )

        def _has_current_hooks(hooks: dict) -> bool:
            return "agentpack hook" in str(hooks)

        if claude_settings.exists():
            try:
                data = _json.loads(claude_settings.read_text())
                hooks = data.get("hooks", {})
                if "UserPromptSubmit" in hooks or "SessionStart" in hooks:
                    if _has_stale_hooks(hooks):
                        console.print("  [yellow]![/] Claude hooks stale (local) — old injection hook detected. Run: agentpack install --agent claude")
                        ok = False
                    elif _has_current_hooks(hooks):
                        console.print(f"  [green]✓[/] Claude hooks present (local): {claude_settings}")
                    else:
                        console.print(f"  [green]✓[/] Claude hooks present (local): {claude_settings}")
                    _local_has_hooks = True
                else:
                    console.print("  [yellow]![/] Claude hooks missing (local) — run: agentpack install --agent claude")
                    ok = False
            except Exception:
                console.print(f"  [yellow]![/] Could not parse {claude_settings}")
        else:
            console.print("  [dim]-[/] .claude/settings.json not present (run: agentpack install --agent claude)")
        if global_claude_settings.exists():
            try:
                data = _json.loads(global_claude_settings.read_text())
                hooks = data.get("hooks", {})
                if "UserPromptSubmit" in hooks or "SessionStart" in hooks:
                    if _has_stale_hooks(hooks):
                        console.print("  [yellow]![/] Claude hooks stale (global) — old injection hook detected. Run: agentpack install --agent claude --global")
                    else:
                        console.print(f"  [green]✓[/] Claude hooks present (global): {global_claude_settings}")
                    _global_has_hooks = True
                else:
                    console.print("  [yellow]![/] Claude hooks missing (global) — run: agentpack install --agent claude --global")
            except Exception:
                console.print(f"  [yellow]![/] Could not parse {global_claude_settings}")
        else:
            console.print("  [dim]-[/] ~/.claude/settings.json has no agentpack hooks — run: agentpack install --agent claude --global")
        if _local_has_hooks and not _global_has_hooks:
            console.print("  [yellow]![/] Hooks local-only — context won't auto-inject in other repos. Run: agentpack install --agent claude --global")

        # --- MCP registration ---
        console.print("\n[bold]MCP registration[/]")
        mcp_json = root / ".mcp.json"
        global_claude_settings_for_mcp = Path.home() / ".claude" / "settings.json"
        codex_config_for_mcp = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"
        _local_has_mcp = False
        _global_has_mcp = False
        _codex_has_mcp = False
        if mcp_json.exists():
            try:
                mcp_data = _json.loads(mcp_json.read_text())
                if "agentpack" in mcp_data.get("mcpServers", {}):
                    console.print(f"  [green]✓[/] MCP server registered (local): {mcp_json}")
                    _local_has_mcp = True
                else:
                    console.print("  [yellow]![/] .mcp.json exists but agentpack missing — run: agentpack install --agent claude")
            except Exception:
                console.print(f"  [yellow]![/] Could not parse {mcp_json}")
        else:
            console.print("  [dim]-[/] .mcp.json not present (run: agentpack install --agent claude)")
        if global_claude_settings_for_mcp.exists():
            try:
                global_data = _json.loads(global_claude_settings_for_mcp.read_text())
                if "agentpack" in global_data.get("mcpServers", {}):
                    console.print(f"  [green]✓[/] MCP server registered (global): {global_claude_settings_for_mcp}")
                    _global_has_mcp = True
            except Exception:
                pass
        if codex_config_for_mcp.exists():
            try:
                codex_text = codex_config_for_mcp.read_text(encoding="utf-8")
                if (
                    "[mcp_servers.agentpack]" in codex_text
                    and 'command = "agentpack"' in codex_text
                    and 'args = ["mcp"]' in codex_text
                ):
                    console.print(f"  [green]✓[/] MCP server registered (Codex): {codex_config_for_mcp}")
                    _codex_has_mcp = True
            except Exception:
                pass
        if not _local_has_mcp and not _global_has_mcp and not _codex_has_mcp:
            console.print("  [yellow]![/] MCP server not registered — mcp__agentpack__* tools unavailable")
            console.print("  [yellow]![/] exact repair: agentpack install --agent codex or agentpack install --agent claude")
            _print_action(
                indent="  ",
                what_failed="MCP server is not registered for this repo or host",
                why_it_matters="agents cannot call AgentPack tools and may fall back to stale markdown context",
                repair_command="agentpack repair --agent all",
                safe_to_continue="yes with direct rg/git evidence; no if relying on MCP context",
            )
            ok = False
        else:
            console.print(
                "  [green]✓[/] expected MCP tools: "
                "readiness, route_task, pack_context, get_context, refresh, get_stats"
            )

        # --- MCP runtime ---
        console.print("\n[bold]MCP runtime[/]")
        mcp_runtime = check_mcp_runtime(root=root)
        _print_mcp_runtime_doctor(mcp_runtime)
        if not mcp_runtime.ok:
            ok = False

        # --- Live host exposure ---
        console.print("\n[bold]Live host exposure[/]")
        console.print("  [yellow]![/] cannot be proven from CLI")
        console.print("  Call `agentpack_readiness()` or `mcp__agentpack__readiness()` from the agent host.")
        if mcp_runtime.ok:
            console.print(
                "  If readiness is absent, MCP local server is runnable but this host session has not exposed tools; "
                "run `agentpack repair --agent <agent>` and restart the host."
            )

        # --- Agent integration matrix ---
        console.print("\n[bold]Agent integration audit[/]")
        agents = expand_agents(agent, root)
        if agent == "auto":
            console.print(f"  [dim]Auto-detected agent: {agents[0]}[/]")
        for selected in agents:
            console.print(f"  [bold]{selected}[/]")
            selected_ok = True
            for check in check_agent_integration(root, selected):
                if check.ok:
                    console.print(f"    [green]✓[/] {check.label}: {check.detail}")
                    continue
                fix = f" — run: {check.fix}" if check.fix else ""
                console.print(f"    [red]✗[/] {check.label}: {check.detail}{fix}")
                _print_action(
                    indent="    ",
                    what_failed=f"{selected} {check.label}: {check.detail}",
                    why_it_matters="agent host may miss current AgentPack instructions, hooks, or MCP setup",
                    repair_command=check.fix or f"agentpack repair --agent {selected}",
                    safe_to_continue="no for AgentPack-managed agent setup; direct repo inspection is still safe",
                )
                selected_ok = False
                ok = False
            if not selected_ok:
                console.print(
                    f"    [yellow]![/] exact repair: {refresh_commands(selected).repair}"
                )

        # --- Concurrent threads ---
        console.print("\n[bold]Concurrent threads[/]")
        thread_conflicts = _thread_conflict_findings(root)
        if thread_conflicts:
            for finding in thread_conflicts:
                console.print(f"  [yellow]![/] {finding}")
            ok = False
        else:
            console.print("  [green]✓[/] no active same-branch/worktree overlaps")

        # --- Release hygiene ---
        console.print("\n[bold]Release hygiene[/]")
        findings = _release_hygiene_findings(root)
        if findings:
            for finding in findings:
                console.print(f"  [yellow]![/] {finding}")
            console.print("  [dim]warning only; keep these out of commits/releases.[/]")
        else:
            console.print("  [green]✓[/] no generated release-noise files staged or untracked")

        # --- Publish secrets ---
        console.print("\n[bold]Publish secrets[/]")
        publish_findings = _publish_secret_findings(root)
        if publish_findings:
            for finding in publish_findings:
                console.print(f"  [yellow]![/] {finding}")
        else:
            console.print("  [green]✓[/] npm publish token available in environment")

        # --- Slash commands ---
        console.print("\n[bold]Slash commands (/agentpack, /agentpack-review, /agentpack-learn, /agentpack-handoff, /agentpack-resume)[/]")
        for filename in ("agentpack.md", "agentpack-review.md", "agentpack-learn.md", "agentpack-handoff.md", "agentpack-resume.md"):
            local_cmd = root / ".claude" / "commands" / filename
            global_cmd = Path.home() / ".claude" / "commands" / filename
            if local_cmd.exists():
                console.print(f"  [green]✓[/] Slash command installed (local): {local_cmd}")
            else:
                console.print(
                    f"  [dim]-[/] Slash command not installed locally: {local_cmd} — "
                    "run: agentpack install --agent claude"
                )
            if global_cmd.exists():
                console.print(f"  [green]✓[/] Slash command installed (global): {global_cmd}")
            else:
                console.print(
                    f"  [dim]-[/] Slash command not installed globally: {global_cmd} — "
                    "run: agentpack install --agent claude --global"
                )

        _print_summary(ok)


def _safe_fix(root: Path, agent: str) -> None:
    console.print("[bold]Safe fixes[/]")
    console.print(
        "  [dim]doctor --fix diagnoses first, then applies AgentPack-managed local repairs. "
        "Direct mutation path: agentpack repair --agent <agent>.[/]"
    )
    if agent == "all":
        console.print("  [dim]Repair scope: every supported local agent integration.[/]")
    status = agentignore_sync_status(root)
    if status.action != "unchanged":
        status.path.parent.mkdir(parents=True, exist_ok=True)
        status.path.write_text(status.desired_content, encoding="utf-8")
        console.print(f"  [green]✓[/] {status.action} .agentignore")
    for selected in expand_agents(agent, root):
        if selected == "generic":
            continue
        failing = [check for check in check_agent_integration(root, selected) if not check.ok]
        if failing:
            from agentpack.commands.install import _install_slash_command

            install_agent_integration(root, selected, install_slash_command=_install_slash_command)
            console.print(f"  [green]✓[/] repaired {selected} integration")


def _print_repair_boundary(agent: str) -> None:
    target = "every supported local agent" if agent == "all" else f"{agent} local agent"
    console.print("[bold]Doctor repair mode[/]")
    console.print(f"  What doctor does: diagnose, then run safe AgentPack-managed repairs for {target}.")
    console.print("  What repair does: apply the requested integration repair directly, including --global when requested.")


def _print_action(
    *,
    indent: str,
    what_failed: str,
    why_it_matters: str,
    repair_command: str,
    safe_to_continue: str,
) -> None:
    console.print(f"{indent}What failed: {what_failed}")
    console.print(f"{indent}Why it matters: {why_it_matters}")
    console.print(f"{indent}Repair command: {repair_command}")
    console.print(f"{indent}Safe to continue: {safe_to_continue}")


def _print_mcp_runtime_doctor(check: McpRuntimeCheck) -> None:
    if check.status == "stdio_waiting":
        console.print("  [green]✓[/] agentpack mcp starts and waits for stdio")
        return
    if check.status == "ready":
        console.print(f"  [green]✓[/] {check.detail}")
        return
    if check.status == "missing_extra":
        console.print("  [yellow]![/] missing MCP extra")
    elif check.status == "command_missing":
        console.print("  [yellow]![/] agentpack command missing")
    else:
        console.print(f"  [yellow]![/] {check.detail}")
    for command in check.remediation:
        console.print(f"  Fix: {escape(command)}")


def _thread_conflict_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for row in list_thread_rows(root, active_only=True):
        context = detect_conflicts(root, row)
        for conflict in context.get("conflicts") or []:
            findings.append(
                f"{row.get('thread_id')} overlaps {conflict.get('thread_id')} "
                f"on {conflict.get('overlap_count', 0)} file(s); run `agentpack threads --conflicts`."
            )
    return sorted(set(findings))[:5]


def _check_agent_file(root: Path, filename: str, agent: str) -> None:
    path = root / filename
    if path.exists():
        content = path.read_text()
        if "agentpack" in content.lower():
            console.print(f"  [green]✓[/] {filename} (agentpack configured)")
        else:
            console.print(f"  [dim]-[/] {filename} exists but agentpack not configured — run: agentpack install --agent {agent}")
    else:
        console.print(f"  [dim]-[/] {filename} not present (optional)")


def _latest_context_path(root: Path) -> Path:
    meta = load_pack_metadata(root)
    if meta and meta.get("context_path"):
        candidate = root / str(meta["context_path"])
        if candidate.exists():
            return candidate
    for rel in (
        ".agentpack/context.md",
        ".agentpack/context.claude.md",
        ".agent/skills/agentpack/SKILL.md",
    ):
        candidate = root / rel
        if candidate.exists():
            return candidate
    return root / ".agentpack" / "context.md"


def _source_checkout_warning(
    root: Path,
    package_file: Path,
    executable: str,
    binary: str | None,
) -> str | None:
    source_pkg = root / "src" / "agentpack"
    if not source_pkg.exists():
        return None
    try:
        package_path = package_file.resolve()
        source_path = source_pkg.resolve()
    except OSError:
        return None
    if package_path.is_relative_to(source_path):
        return None
    binary_text = f" via {binary}" if binary else ""
    return (
        "source checkout detected, but CLI imports installed package "
        f"at {package_path}{binary_text}. Use `PYTHONPATH=src python -m agentpack.cli ...` "
        "or install editable with `pip install -e .`."
    )


def _memory_findings(root: Path) -> list[tuple[str, bool]]:
    from agentpack.core.config import load_config

    cfg = load_config(root)
    rows: list[tuple[str, bool]] = []
    for label, rel_path, limit in (
        ("session events", cfg.runtime.session_events_output, cfg.runtime.max_session_events),
        ("episodic cases", cfg.learning.episodic_cases_output, cfg.runtime.max_episodic_cases),
    ):
        count = _jsonl_line_count(root / rel_path)
        rows.append((f"{label}: {count} row(s), retention {limit}", count <= limit))
    return rows


def _jsonl_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())


_RELEASE_NOISE_PREFIXES = (
    ".agent/",
    ".agentpack/",
    ".claude/worktrees/",
    ".codex/",
    ".cursor/",
    ".vscode/",
)
_RELEASE_NOISE_FILES = {".coverage"}


def _release_hygiene_findings(root: Path) -> list[str]:
    """Flag local generated artifacts that should not be staged or released."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["could not inspect git status for release hygiene"]
    if result.returncode != 0:
        return []

    noisy: list[str] = []
    for raw in result.stdout.splitlines():
        if not raw.strip():
            continue
        status = raw[:2].strip() or "modified"
        path = raw[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        norm = path.replace("\\", "/")
        if norm in _RELEASE_NOISE_FILES or any(norm.startswith(prefix) for prefix in _RELEASE_NOISE_PREFIXES):
            noisy.append(f"{status} {norm}")

    if not noisy:
        return []
    sample = ", ".join(noisy[:8])
    extra = f", ... {len(noisy) - 8} more" if len(noisy) > 8 else ""
    return [f"generated/local artifacts present: {sample}{extra}"]


def _publish_secret_findings(root: Path, env: Mapping[str, str] | None = None) -> list[str]:
    """Warn when local/release env is missing publish secrets."""
    env = env or os.environ
    findings: list[str] = []
    if (root / "npm" / "package.json").exists() and not (env.get("NPM_TOKEN") or env.get("NODE_AUTH_TOKEN")):
        findings.append(
            "npm package present but NPM_TOKEN/NODE_AUTH_TOKEN is not set; "
            "npm publish will fail until GitHub secret NPM_TOKEN exists."
        )
    return findings


def _agentignore_sync_findings(root: Path) -> list[str]:
    status = agentignore_sync_status(root)
    if status.action == "create":
        return ["missing .agentignore; run `agentpack init` or `agentpack ignore sync`."]
    if status.is_stale:
        return ["imported .agentignore rules are stale; run `agentpack ignore sync`."]
    if status.imported_rules:
        return [f"synced: {format_import_summary(status)}"]
    return ["synced: .agentignore present; no imported generated/noisy rules detected."]


def _print_summary(ok: bool) -> None:
    console.print("")
    if ok:
        console.print("[bold green]All checks passed.[/]")
    else:
        console.print("[bold yellow]Some checks failed. Run the suggested commands above to fix.[/]")
