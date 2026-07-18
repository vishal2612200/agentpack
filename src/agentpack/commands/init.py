from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import typer

from agentpack.core.config import DEFAULT_CONFIG, CONFIG_TEMPLATE
from agentpack.core.modes import ACTIVE_MODES, MODE_HELP, invalid_mode_message, is_requested_mode, normalize_mode
from agentpack.core.ignore import (
    AgentIgnoreSyncStatus,
    agentignore_sync_status,
    format_import_summary,
)
from agentpack.commands._shared import console, _root
from agentpack.integrations.agents import check_agent_integration, install_agent_integration
from agentpack.session.state import load_session, create_session, SESSION_FILE, TASK_FILE

_GITIGNORE_START = "# agentpack:start"
_GITIGNORE_END = "# agentpack:end"
_INIT_MODES = ACTIVE_MODES
_INIT_AGENTS = ("auto", "claude", "cursor", "windsurf", "codex", "antigravity", "generic")
_AGENT_GITIGNORE_ENTRIES = {
    "cursor": (".vscode/tasks.json",),
    "windsurf": (".vscode/tasks.json",),
    "antigravity": (".vscode/tasks.json", "GEMINI.md"),
}


@dataclass
class InitResult:
    path: str
    action: str


@dataclass
class InitHealth:
    label: str
    ok: bool
    detail: str


def _repo_gitignore_entries(share_cache: bool = False, agent: str = "generic") -> list[str]:
    entries = [
        ".agentpack/*",
        "!.agentpack/config.toml",
    ]
    if share_cache:
        entries.extend(["!.agentpack/cache/", "!.agentpack/cache/**"])
    else:
        entries.append(".agentpack/cache/")
    entries.extend(
        [
            ".agentpack/snapshots/",
            ".agentpack/context*",
            ".agentpack/metrics.jsonl",
            ".agentpack/session-events.jsonl",
            ".agentpack/pack_metadata.json",
            ".agentpack/pack-registry.json",
            ".agentpack/learning.md",
            ".agentpack/daily-summary.md",
            ".agentpack/skills-progress.json",
            ".agentpack/agent-lessons.md",
            ".agentpack/learning.prompt.md",
            ".agentpack/pr-learning-comment.md",
            ".agentpack/learning-dashboard.html",
            ".agentpack/team-lessons.md",
            ".agentpack/learning-feedback.jsonl",
            ".agentpack/ranking-feedback.jsonl",
            ".agentpack/episodic-cases.jsonl",
            ".agentpack/learning-inputs.json",
            ".agentpack/activity.log",
            ".agentpack/.gitignore",
            ".agentpack/.mcp_reminded",
            ".agentpack/session.json",
            ".agentpack/task.md",
            ".agentpack/benchmark_results.jsonl",
            ".agentpack/loop_state.json",
            ".agentpack/progress.md",
            ".agentpack/loop_events.jsonl",
            ".agentpack/loop_failures.jsonl",
            ".agent/skills/agentpack/",
            ".agentignore",
        ]
    )
    entries.extend(_AGENT_GITIGNORE_ENTRIES.get(agent, ()))
    return entries


def _repo_gitignore_block(share_cache: bool = False, agent: str = "generic") -> str:
    return (
        f"{_GITIGNORE_START}\n"
        "# AgentPack generated context/cache (safe to ignore)\n"
        + "\n".join(_repo_gitignore_entries(share_cache, agent))
        + "\n"
        f"{_GITIGNORE_END}\n"
    )


def _agentpack_gitignore_content(share_cache: bool = False) -> str:
    entries = []
    if not share_cache:
        entries.append("cache/")
    entries.extend(
        [
            "snapshots/",
            "context.*",
            "metrics.jsonl",
            "session-events.jsonl",
            "pack_metadata.json",
            "pack-registry.json",
            "learning.md",
            "daily-summary.md",
            "skills-progress.json",
            "agent-lessons.md",
            "learning.prompt.md",
            "pr-learning-comment.md",
            "learning-dashboard.html",
            "team-lessons.md",
            "learning-feedback.jsonl",
            "ranking-feedback.jsonl",
            "episodic-cases.jsonl",
            "learning-inputs.json",
            "activity.log",
            ".mcp_reminded",
            "session.json",
            "task.md",
            "benchmark_results.jsonl",
            "loop_state.json",
            "progress.md",
            "loop_events.jsonl",
            "loop_failures.jsonl",
        ]
    )
    return "\n".join(entries) + "\n"


def _backup_file(path: Path) -> Path:
    backup = path.with_name(f"{path.name}.bak")
    index = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak.{index}")
        index += 1
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def _write_text(root: Path, path: Path, content: str, *, force: bool = False, backups: list[InitResult] | None = None) -> str:
    existed = path.exists()
    if existed and path.read_text(encoding="utf-8") == content:
        return "unchanged"
    if force and existed and backups is not None:
        backup = _backup_file(path)
        backups.append(InitResult(str(backup.relative_to(root)), "created"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "updated" if existed else "created"


def _patch_repo_gitignore(root: Path, share_cache: bool = False, agent: str = "generic", *, force: bool = False, backups: list[InitResult] | None = None) -> str:
    gitignore = root / ".gitignore"
    block = _repo_gitignore_block(share_cache, agent)
    if not gitignore.exists():
        gitignore.write_text(block, encoding="utf-8")
        return "created"

    content = gitignore.read_text(encoding="utf-8")
    start = content.find(_GITIGNORE_START)
    end = content.find(_GITIGNORE_END)
    if start != -1 and end != -1 and end >= start:
        end += len(_GITIGNORE_END)
        replacement = block.rstrip()
        updated = content[:start].rstrip() + "\n\n" + replacement + "\n" + content[end:].lstrip("\n")
        if updated == content:
            return "unchanged"
        if force and backups is not None:
            backup = _backup_file(gitignore)
            backups.append(InitResult(str(backup.relative_to(root)), "created"))
        gitignore.write_text(updated, encoding="utf-8")
        return "updated"

    prefix = content.rstrip() + "\n\n" if content.strip() else ""
    if force and backups is not None:
        backup = _backup_file(gitignore)
        backups.append(InitResult(str(backup.relative_to(root)), "created"))
    gitignore.write_text(prefix + block, encoding="utf-8")
    return "updated"


def _patch_agentignore(
    root: Path,
    *,
    force: bool = False,
    backups: list[InitResult] | None = None,
) -> tuple[str, AgentIgnoreSyncStatus]:
    status = agentignore_sync_status(root)
    if force and status.path.exists() and backups is not None:
        backup = _backup_file(status.path)
        backups.append(InitResult(str(backup.relative_to(root)), "created"))
    if status.action == "unchanged":
        return "unchanged", status
    status.path.parent.mkdir(parents=True, exist_ok=True)
    status.path.write_text(status.desired_content, encoding="utf-8")
    action = "created" if status.action == "create" else "updated"
    return action, agentignore_sync_status(root)


def _install_agent_integration(root, agent: str) -> dict[str, str]:
    """Install repo-local agent integration files after `agentpack init`."""
    if agent == "claude":
        from agentpack.commands.install import _install_slash_command

        return install_agent_integration(root, agent, install_slash_command=_install_slash_command)
    return install_agent_integration(root, agent)


def _print_agent_integration_results(results: dict[str, str]) -> None:
    for key, action in results.items():
        if action == "unchanged":
            continue
        if key.startswith("git:"):
            console.print(f"[green].git/hooks/{key[4:]} {action}[/]")
        elif key == "vscode:tasks":
            console.print(f"[green].vscode/tasks.json {action}[/]")
        else:
            console.print(f"[green]{key} {action}[/]")


def _validate_init_options(agent: str, mode: str | None, budget: int) -> None:
    if agent not in _INIT_AGENTS:
        console.print(f"[yellow]Unknown agent: {agent}. Supported: {', '.join(_INIT_AGENTS)}[/]")
        raise typer.Exit(1)
    if mode is not None and not is_requested_mode(mode):
        console.print(f"[yellow]{invalid_mode_message(mode)}[/]")
        raise typer.Exit(1)
    if budget < 0:
        console.print("[yellow]Budget must be 0 or greater.[/]")
        raise typer.Exit(1)


def _resolve_init_agent(root: Path, agent: str, *, force: bool = False) -> str:
    if agent != "auto":
        return agent
    existing_session = load_session(root)
    if existing_session is not None and not force:
        return existing_session.agent
    from agentpack.adapters.detect import detect_agent

    return detect_agent(root)


def _agent_integration_paths(agent: str) -> tuple[str, ...]:
    if agent == "claude":
        return (
            "CLAUDE.md",
            ".claude/settings.json",
            ".mcp.json",
            ".claude/commands/agentpack.md",
            ".claude/commands/agentpack-review.md",
            ".claude/commands/agentpack-resolve.md",
            ".claude/commands/agentpack-skill-review.md",
            ".claude/commands/agentpack-learn.md",
        )
    if agent == "cursor":
        return (".cursorrules", ".cursor/rules/agentpack.mdc", ".vscode/tasks.json")
    if agent == "windsurf":
        return (".windsurfrules", ".vscode/tasks.json")
    if agent == "codex":
        return ("AGENTS.md", ".codex/hooks.json")
    if agent == "antigravity":
        return ("GEMINI.md", ".vscode/tasks.json")
    return ()


def _backup_existing_paths(root: Path, paths: tuple[str, ...], backups: list[InitResult]) -> None:
    for rel in paths:
        path = root / rel
        if path.exists() and path.is_file():
            backup = _backup_file(path)
            backups.append(InitResult(str(backup.relative_to(root)), "created"))


def _planned_action(path: Path, expected: str | None = None) -> str:
    if not path.exists():
        return "create"
    if expected is None:
        return "update"
    try:
        return "unchanged" if path.read_text(encoding="utf-8") == expected else "update"
    except OSError:
        return "update"


def _print_dry_run(root: Path, agent: str, share_cache: bool, mode: str | None, budget: int) -> None:
    console.print("[bold yellow]Dry run — no files will be changed.[/]\n")
    ignore_status = agentignore_sync_status(root)
    items = [
        InitResult(".agentpack/", "ensure"),
        InitResult(".agentpack/snapshots/", "ensure"),
        InitResult(".agentpack/cache/", "ensure"),
        InitResult(".agentpack/.gitignore", _planned_action(root / ".agentpack" / ".gitignore")),
        InitResult(".gitignore", _planned_action(root / ".gitignore")),
        InitResult(".agentpack/config.toml", _planned_action(root / ".agentpack" / "config.toml")),
        InitResult(".agentignore", ignore_status.action),
        InitResult(SESSION_FILE, _planned_action(root / SESSION_FILE)),
        InitResult(TASK_FILE, _planned_action(root / TASK_FILE)),
    ]
    for rel in _agent_integration_paths(agent):
        items.append(InitResult(rel, _planned_action(root / rel)))
    _print_init_summary("Dry Run", items)
    console.print(f"  Agent: {agent}")
    console.print(f"  Mode: {normalize_mode(mode) if mode else DEFAULT_CONFIG.context.default_mode}")
    console.print(f"  Budget: {budget or DEFAULT_CONFIG.context.default_budget:,}")
    console.print(f"  Share cache: {'yes' if share_cache else 'no'}")
    if ignore_status.imported_rules:
        console.print(f"  {format_import_summary(ignore_status)}")


def _print_init_summary(title: str, results: list[InitResult]) -> None:
    console.print(f"\n[bold]{title}[/]")
    grouped: dict[str, list[str]] = {}
    for result in results:
        grouped.setdefault(result.action, []).append(result.path)
    for action in ("created", "updated", "unchanged", "skipped", "ensure", "create", "update"):
        paths = grouped.get(action)
        if not paths:
            continue
        console.print(f"  {action}: {', '.join(paths)}")


def _init_health(root: Path, agent: str) -> list[InitHealth]:
    checks = [
        InitHealth(".gitignore", (root / ".gitignore").exists(), "repo ignore file present"),
        InitHealth(".agentpack/config.toml", (root / ".agentpack" / "config.toml").exists(), "config file present"),
        InitHealth(".agentignore", (root / ".agentignore").exists(), "agent ignore file present"),
        InitHealth(SESSION_FILE, (root / SESSION_FILE).exists(), "session file present"),
        InitHealth(TASK_FILE, (root / TASK_FILE).exists(), "task file present"),
    ]
    try:
        from agentpack.core.config import load_config

        load_config(root)
        checks.append(InitHealth("config", True, "loads successfully"))
    except Exception as exc:
        checks.append(InitHealth("config", False, f"failed to load: {exc}"))
    for check in check_agent_integration(root, agent):
        checks.append(InitHealth(check.label, check.ok, check.detail))
    return checks


def _print_health(root: Path, agent: str) -> None:
    console.print("\n[bold]Init health[/]")
    for check in _init_health(root, agent):
        marker = "[green]✓[/]" if check.ok else "[yellow]![/]"
        console.print(f"  {marker} {check.label}: {check.detail}")


def register(app: typer.Typer) -> None:
    @app.command()
    def init(
        force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
        mode: Optional[str] = typer.Option(None, "--mode", help=f"Default pack mode ({MODE_HELP})."),
        budget: int = typer.Option(0, "--budget", help="Default token budget (0 = keep default 40000)."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive prompts, use defaults."),
        silent: bool = typer.Option(False, "--silent", help="Suppress all output (for use in hooks/scripts)."),
        share_cache: bool = typer.Option(False, "--share-cache", help="Commit summary cache to git (recommended for teams)."),
        agent: str = typer.Option("auto", "--agent", help="Target agent (auto|claude|cursor|windsurf|codex|antigravity|generic)."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change without writing files."),
        health_check: bool = typer.Option(True, "--health-check/--no-health-check", help="Verify init outputs after setup."),
    ) -> None:
        """Initialize AgentPack in the current directory.

        One-time setup. After this, just run `agentpack watch` — no other commands needed.
        """
        if silent:
            yes = True
            console.quiet = True
        _validate_init_options(agent, mode, budget)
        root = _root()
        resolved_agent = _resolve_init_agent(root, agent, force=force)
        if dry_run:
            _print_dry_run(root, resolved_agent, share_cache, mode, budget)
            return

        results: list[InitResult] = []
        backups: list[InitResult] = []
        agentpack_dir = root / ".agentpack"
        agentpack_existed = agentpack_dir.exists()
        snapshots_existed = (agentpack_dir / "snapshots").exists()
        cache_existed = (agentpack_dir / "cache").exists()
        agentpack_dir.mkdir(exist_ok=True)
        (agentpack_dir / "snapshots").mkdir(exist_ok=True)
        (agentpack_dir / "cache").mkdir(exist_ok=True)
        results.extend(
            [
                InitResult(".agentpack/", "unchanged" if agentpack_existed else "created"),
                InitResult(".agentpack/snapshots/", "unchanged" if snapshots_existed else "created"),
                InitResult(".agentpack/cache/", "unchanged" if cache_existed else "created"),
            ]
        )

        gitignore = agentpack_dir / ".gitignore"
        if not gitignore.exists() or force:
            action = _write_text(root, gitignore, _agentpack_gitignore_content(share_cache), force=force, backups=backups)
            results.append(InitResult(".agentpack/.gitignore", action))
        else:
            results.append(InitResult(".agentpack/.gitignore", "unchanged"))

        gitignore_action = _patch_repo_gitignore(root, share_cache=share_cache, agent=resolved_agent, force=force, backups=backups)
        results.append(InitResult(".gitignore", gitignore_action))

        config_path_file = agentpack_dir / "config.toml"
        if not config_path_file.exists() or force:
            cfg = DEFAULT_CONFIG.model_copy(deep=True)

            # Interactive mode selection
            if not yes and mode is None and sys.stdin.isatty():
                console.print("\n[bold]Choose default pack mode:[/]")
                console.print("  [cyan]1[/] lite     — cheap ranked map, omitted warnings, tight budget")
                console.print("  [cyan]2[/] balanced — deps, tests, summaries [bold](recommended)[/]")
                console.print("  [cyan]3[/] deep     — docs, more full files (most context)")
                choice = typer.prompt("Mode", default="2")
                mode_map = {
                    "1": "lite",
                    "2": "balanced",
                    "3": "deep",
                    "lite": "lite",
                    "balanced": "balanced",
                    "deep": "deep",
                }
                cfg.context.default_mode = normalize_mode(mode_map.get(choice.strip(), "balanced"))
            elif mode and is_requested_mode(mode):
                cfg.context.default_mode = normalize_mode(mode)

            if budget > 0:
                cfg.context.default_budget = budget

            config_toml = CONFIG_TEMPLATE.replace(
                'default_mode = "balanced"',
                f'default_mode = "{cfg.context.default_mode}"',
            )
            if budget > 0:
                config_toml = config_toml.replace(
                    "default_budget = 40000",
                    f"default_budget = {cfg.context.default_budget}",
                )
            action = _write_text(root, config_path_file, config_toml, force=force, backups=backups)
            results.append(InitResult(".agentpack/config.toml", action))
        else:
            results.append(InitResult(".agentpack/config.toml", "unchanged"))

        ignore_action, ignore_status = _patch_agentignore(root, force=force, backups=backups)
        results.append(InitResult(".agentignore", ignore_action))

        # Bootstrap session so `agentpack watch` works immediately — no separate `session start` needed
        from agentpack.core.config import load_config
        resolved_mode = load_config(root).context.default_mode
        existing_session = load_session(root)
        if existing_session is None or force:
            create_session(root, agent=resolved_agent, mode=resolved_mode)
            results.append(InitResult(SESSION_FILE, "created" if existing_session is None else "updated"))
            results.append(InitResult(TASK_FILE, "created" if existing_session is None else "updated"))
        else:
            results.append(InitResult(SESSION_FILE, "unchanged"))
            results.append(InitResult(TASK_FILE, "unchanged"))

        if force:
            _backup_existing_paths(root, _agent_integration_paths(resolved_agent), backups)
        integration_results = _install_agent_integration(root, resolved_agent)
        for key, action in integration_results.items():
            if key.startswith("git:"):
                results.append(InitResult(f".git/hooks/{key[4:]}", action))
            elif key == "vscode:tasks":
                results.append(InitResult(".vscode/tasks.json", action))
            else:
                results.append(InitResult(key, action))

        if backups:
            _print_init_summary("Backups", backups)
        _print_init_summary("Init Summary", results)
        if ignore_status.imported_rules:
            console.print(f"  [dim]{format_import_summary(ignore_status)}[/]")
        if health_check:
            _print_health(root, resolved_agent)
        console.print(f"\n[bold green]AgentPack initialized.[/] [dim]agent={resolved_agent} mode={resolved_mode}[/]")
        console.print("Run [bold]agentpack watch[/] to start auto-refreshing context.")
