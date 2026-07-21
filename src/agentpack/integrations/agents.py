from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agentpack import __version__
from agentpack.core.command_surface import refresh_commands
from agentpack.integrations.git_hooks import git_hooks_dir
from agentpack.installers.antigravity import AntigravityInstaller
from agentpack.installers.claude import ClaudeInstaller
from agentpack.installers.codex import CodexInstaller
from agentpack.installers.cursor import CursorInstaller
from agentpack.installers.windsurf import WindsurfInstaller

SUPPORTED_AGENTS = ("auto", "all", "claude", "cursor", "windsurf", "codex", "antigravity", "generic")
CONCRETE_AGENTS = ("claude", "cursor", "windsurf", "codex", "antigravity", "generic")
GIT_REPACK_AGENTS = {"cursor", "windsurf", "codex", "antigravity"}
GIT_HOOK_EVENTS = ("post-commit", "post-merge", "post-checkout")


@dataclass(frozen=True)
class AgentCheck:
    agent: str
    label: str
    ok: bool
    detail: str
    fix: str | None = None


def resolve_agent(agent: str, root: Path) -> str:
    if agent != "auto":
        return agent
    from agentpack.adapters.detect import detect_agent

    return detect_agent(root)


def expand_agents(agent: str, root: Path) -> tuple[str, ...]:
    resolved = resolve_agent(agent, root)
    if resolved == "all":
        return CONCRETE_AGENTS
    return (resolved,)


def install_agent_integration(
    root: Path,
    agent: str,
    *,
    global_install: bool = False,
    slash_command: bool = True,
    install_slash_command=None,
) -> dict[str, str]:
    """Install or repair one agent integration and return file/action results."""
    if agent == "claude":
        installer = ClaudeInstaller()
        results = {
            "CLAUDE.md": installer.patch_claude_md(root),
            "~/.claude/settings.json" if global_install else ".claude/settings.json": installer.patch_claude_settings(
                root, global_install
            ),
            "~/.claude/settings.json:mcpServers.agentpack" if global_install else ".mcp.json:mcpServers.agentpack": installer.patch_mcp_server(
                root, global_install
            ),
        }
        if slash_command and install_slash_command is not None:
            slash_results = install_slash_command(root, global_install)
            if isinstance(slash_results, dict):
                results.update(slash_results)
            else:
                results["/agentpack"] = slash_results
        return results

    if agent == "cursor":
        installer = CursorInstaller()
        results = {
            ".cursorrules": installer.patch_cursor_rules(root),
            ".cursor/rules/agentpack.mdc": installer.patch_cursor_mdc(root),
        }
        if not global_install:
            results.update(installer.install_auto_repack(root))
        return results

    if agent == "windsurf":
        installer = WindsurfInstaller()
        results = {".windsurfrules": installer.patch_windsurfrules(root)}
        if not global_install:
            results.update(installer.install_auto_repack(root))
        return results

    if agent == "codex":
        installer = CodexInstaller()
        plugin_result = installer.install_codex_plugin()
        results = {
            "AGENTS.md": installer.patch_agents_md(root),
            "~/.codex/plugins/cache/local/agentpack": next(iter(plugin_result.values())),
            '~/.codex/config.toml:plugins."agentpack@local"': installer.patch_codex_plugin_config(),
            "~/.codex/config.toml:mcp_servers.agentpack": installer.patch_codex_mcp_config(),
        }
        if not global_install:
            results.update(installer.install_auto_repack(root))
        return results

    if agent == "antigravity":
        installer = AntigravityInstaller()
        results = {"GEMINI.md": installer.patch_gemini_md(root)}
        if not global_install:
            results.update(installer.install_auto_repack(root))
        return results

    if agent == "generic":
        return {}

    raise ValueError(f"Unknown agent: {agent}")


def check_agent_integration(root: Path, agent: str) -> list[AgentCheck]:
    if agent == "claude":
        return _check_claude(root)
    if agent == "cursor":
        return [
            _current_rule_file(root, agent, ".cursorrules", "agentpack repair --agent cursor"),
            _current_rule_file(root, agent, ".cursor/rules/agentpack.mdc", "agentpack repair --agent cursor"),
            *_check_git_hooks(root, agent),
            _current_vscode_tasks(root, agent, "agentpack repair --agent cursor"),
        ]
    if agent == "windsurf":
        return [
            _current_rule_file(root, agent, ".windsurfrules", "agentpack repair --agent windsurf"),
            *_check_git_hooks(root, agent),
            _current_vscode_tasks(root, agent, "agentpack repair --agent windsurf"),
        ]
    if agent == "codex":
        return [
            _current_rule_file(root, agent, "AGENTS.md", "agentpack repair --agent codex"),
            _codex_plugin_config(),
            _codex_hooks(root),
            _codex_mcp_config(),
            *_check_git_hooks(root, agent),
        ]
    if agent == "antigravity":
        return [
            _current_rule_file(root, agent, "GEMINI.md", "agentpack repair --agent antigravity"),
            *_check_git_hooks(root, agent),
            _current_vscode_tasks(root, agent, "agentpack repair --agent antigravity"),
        ]
    if agent == "generic":
        return [AgentCheck(agent, "generic", True, "no agent-specific files required")]
    raise ValueError(f"Unknown agent: {agent}")


def _file_contains(root: Path, agent: str, rel: str, needle: str, fix: str) -> AgentCheck:
    path = root / rel
    if not path.exists():
        return AgentCheck(agent, rel, False, "missing", fix)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return AgentCheck(agent, rel, False, "unreadable", fix)
    if needle.lower() in content.lower():
        return AgentCheck(agent, rel, True, "configured")
    return AgentCheck(agent, rel, False, "present but AgentPack block missing", fix)


def _current_rule_file(root: Path, agent: str, rel: str, fix: str) -> AgentCheck:
    path = root / rel
    if not path.exists():
        return AgentCheck(agent, rel, False, "missing", fix)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return AgentCheck(agent, rel, False, "unreadable", fix)
    commands = refresh_commands(agent)
    required = (
        "agentpack",
        "MCP is the active path",
        "agentpack_get_context",
        "agentpack:freshness",
        "TOON",
        commands.primary,
    )
    missing = [needle for needle in required if needle.lower() not in content.lower()]
    if not missing:
        return AgentCheck(agent, rel, True, "current MCP-first AgentPack rule present")
    if "agentpack" in content.lower():
        return AgentCheck(
            agent,
            rel,
            False,
            "stale AgentPack rule; missing MCP-first guard/freshness contract",
            fix,
        )
    return AgentCheck(agent, rel, False, "present but AgentPack block missing", fix)


def _current_vscode_tasks(root: Path, agent: str, fix: str) -> AgentCheck:
    path = root / ".vscode" / "tasks.json"
    if not path.exists():
        return AgentCheck(agent, ".vscode/tasks.json", False, "missing", fix)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return AgentCheck(agent, ".vscode/tasks.json", False, "unreadable", fix)
    refresh_options = {refresh_commands(agent).primary, refresh_commands("auto").primary}
    has_refresh = any(command.lower() in content.lower() for command in refresh_options)
    missing = [needle for needle in ("agentpack",) if needle.lower() not in content.lower()]
    if not missing and has_refresh:
        return AgentCheck(agent, ".vscode/tasks.json", True, "current AgentPack refresh task present")
    if "agentpack" in content.lower():
        return AgentCheck(
            agent,
            ".vscode/tasks.json",
            False,
            "stale AgentPack tasks; missing executable refresh command",
            fix,
        )
    return AgentCheck(agent, ".vscode/tasks.json", False, "present but AgentPack tasks missing", fix)


def _check_git_hooks(root: Path, agent: str) -> list[AgentCheck]:
    checks: list[AgentCheck] = []
    hooks_dir = git_hooks_dir(root)
    for event in GIT_HOOK_EVENTS:
        path = hooks_dir / event if hooks_dir is not None else root / ".git" / "hooks" / event
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                content = ""
            if "agentpack:auto-repack" in content:
                checks.append(AgentCheck(agent, f".git/hooks/{event}", True, "auto-repack hook present"))
                continue
        checks.append(AgentCheck(agent, f".git/hooks/{event}", False, "missing auto-repack hook", f"agentpack repair --agent {agent}"))
    return checks


def _check_claude(root: Path) -> list[AgentCheck]:
    checks = [_current_claude_md(root)]
    settings = root / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            checks.append(AgentCheck("claude", ".claude/settings.json", False, "invalid JSON", "agentpack repair --agent claude"))
        else:
            hooks_text = json.dumps(data.get("hooks", {}))
            ok = (
                "agentpack hook --event SessionStart" in hooks_text
                and "agentpack hook --event UserPromptSubmit" in hooks_text
            )
            checks.append(
                AgentCheck(
                    "claude",
                    ".claude/settings.json",
                    ok,
                    "current hooks present" if ok else "missing current lifecycle hooks",
                    None if ok else "agentpack repair --agent claude",
                )
            )
    else:
        checks.append(AgentCheck("claude", ".claude/settings.json", False, "missing", "agentpack repair --agent claude"))

    mcp = root / ".mcp.json"
    if mcp.exists():
        try:
            data = json.loads(mcp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            checks.append(AgentCheck("claude", ".mcp.json", False, "invalid JSON", "agentpack repair --agent claude"))
        else:
            ok = data.get("mcpServers", {}).get("agentpack") == {"command": "agentpack", "args": ["mcp"]}
            checks.append(
                AgentCheck(
                    "claude",
                    ".mcp.json",
                    ok,
                    "MCP server registered" if ok else "agentpack MCP server missing",
                    None if ok else "agentpack repair --agent claude",
                )
            )
    else:
        checks.append(AgentCheck("claude", ".mcp.json", False, "missing", "agentpack repair --agent claude"))
    return checks


def _current_claude_md(root: Path) -> AgentCheck:
    path = root / "CLAUDE.md"
    if not path.exists():
        return AgentCheck("claude", "CLAUDE.md", False, "missing", "agentpack repair --agent claude")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return AgentCheck("claude", "CLAUDE.md", False, "unreadable", "agentpack repair --agent claude")
    required = ("agentpack", "Prefer MCP", "mcp__agentpack__readiness", "mcp__agentpack__get_context", "TOON", "agentpack pack")
    missing = [needle for needle in required if needle.lower() not in content.lower()]
    if not missing:
        return AgentCheck("claude", "CLAUDE.md", True, "current MCP-first AgentPack block present")
    if "agentpack" in content.lower():
        return AgentCheck(
            "claude",
            "CLAUDE.md",
            False,
            "stale AgentPack block; missing MCP-first readiness guidance",
            "agentpack repair --agent claude",
        )
    return AgentCheck("claude", "CLAUDE.md", False, "present but AgentPack block missing", "agentpack repair --agent claude")


def _codex_hooks(root: Path) -> AgentCheck:
    path = root / ".codex" / "hooks.json"
    if not path.exists():
        return AgentCheck("codex", ".codex/hooks.json", False, "missing Codex app lifecycle hooks", "agentpack repair --agent codex")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return AgentCheck("codex", ".codex/hooks.json", False, "invalid JSON", "agentpack repair --agent codex")
    text = json.dumps(data.get("hooks", {}))
    ok = (
        "agentpack hook --event SessionStart" in text
        and "agentpack hook --event UserPromptSubmit" in text
    )
    return AgentCheck(
        "codex",
        ".codex/hooks.json",
        ok,
        "Codex app lifecycle hooks present" if ok else "missing SessionStart/UserPromptSubmit hooks",
        None if ok else "agentpack repair --agent codex",
    )


_PLUGIN_TABLE_RE = re.compile(
    r'(?ms)^\[plugins\."(?P<key>agentpack@[^"]+)"\]\n(?P<body>.*?)(?=^\[[^\n]+\]\n|\Z)',
)

def _codex_plugin_config() -> AgentCheck:
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    path = codex_home / "config.toml"
    if not path.exists():
        return AgentCheck("codex", "~/.codex/config.toml plugin", False, "missing Codex plugin config", "agentpack repair --agent codex")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return AgentCheck("codex", "~/.codex/config.toml plugin", False, "unreadable", "agentpack repair --agent codex")

    local_enabled = _plugin_enabled(content, "agentpack@local")
    stale_enabled = sorted(
        plugin_key
        for plugin_key in _agentpack_plugin_keys(content)
        if plugin_key != "agentpack@local" and _plugin_enabled(content, plugin_key)
    )
    local_bundle = codex_home / "plugins" / "cache" / "local" / "agentpack" / __version__

    if local_enabled and not stale_enabled and local_bundle.exists():
        return AgentCheck(
            "codex",
            "~/.codex/config.toml plugin",
            True,
            f"local Codex plugin enabled (agentpack@local, cache {__version__})",
        )
    if stale_enabled:
        return AgentCheck(
            "codex",
            "~/.codex/config.toml plugin",
            False,
            f"stale AgentPack plugin source enabled: {', '.join(stale_enabled)}",
            "agentpack repair --agent codex",
        )
    if not local_enabled:
        return AgentCheck(
            "codex",
            "~/.codex/config.toml plugin",
            False,
            "local AgentPack Codex plugin not enabled",
            "agentpack repair --agent codex",
        )
    return AgentCheck(
        "codex",
        "~/.codex/config.toml plugin",
        False,
        f"local AgentPack Codex plugin cache missing for version {__version__}",
        "agentpack repair --agent codex",
    )

def _agentpack_plugin_keys(content: str) -> set[str]:
    return {match.group("key") for match in _PLUGIN_TABLE_RE.finditer(content)}

def _plugin_enabled(content: str, plugin_key: str) -> bool:
    for match in _PLUGIN_TABLE_RE.finditer(content):
        if match.group("key") != plugin_key:
            continue
        for line in match.group("body").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == "enabled":
                return value.strip().lower() == "true"
        return False
    return False

def _codex_mcp_config() -> AgentCheck:
    path = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"
    if not path.exists():
        return AgentCheck("codex", "~/.codex/config.toml", False, "missing Codex MCP config", "agentpack repair --agent codex")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return AgentCheck("codex", "~/.codex/config.toml", False, "unreadable", "agentpack repair --agent codex")
    ok = (
        "[mcp_servers.agentpack]" in content
        and 'command = "agentpack"' in content
        and 'args = ["mcp"]' in content
    )
    return AgentCheck(
        "codex",
        "~/.codex/config.toml",
        ok,
        "Codex MCP server registered" if ok else "agentpack MCP server missing",
        None if ok else "agentpack repair --agent codex",
    )
