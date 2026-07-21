from __future__ import annotations

import stat
from pathlib import Path

from agentpack.core import git
from agentpack.integrations.platform import cli_module_argv, shell_join

# Hooks that indicate the working tree changed and the pack may be stale.
_HOOK_EVENTS = ("post-commit", "post-merge", "post-checkout")

_AGENTPACK_MARKER = "# agentpack:auto-repack"


def _hook_script(agent: str) -> str:
    effective = agent if agent not in ("auto", "") else "auto"
    command = shell_join(cli_module_argv("hook", "--event", "GitAutoRepack", "--agent", effective))
    return f"{_AGENTPACK_MARKER}\n{command}\n"


def git_hooks_dir(root: Path) -> Path | None:
    resolved = git.git_path(root, "hooks")
    if resolved is not None:
        return resolved
    git_entry = root / ".git"
    return git_entry / "hooks" if git_entry.is_dir() else None


def install_git_hooks(root: Path, agent: str) -> dict[str, str]:
    """Install agentpack auto-repack lines into .git/hooks/*.

    Returns {hook_name: action} where action is created|updated|unchanged.
    Idempotent — safe to re-run. Appends to existing hooks rather than replacing.
    """
    hooks_dir = git_hooks_dir(root)
    if hooks_dir is None:
        return {}
    hooks_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, str] = {}
    snippet = _hook_script(agent)

    for event in _HOOK_EVENTS:
        hook_path = hooks_dir / event
        if hook_path.exists():
            content = hook_path.read_text()
            if _AGENTPACK_MARKER in content:
                # Already installed — update the agent name if it changed
                lines = content.splitlines(keepends=True)
                new_lines = []
                skip_next = False
                for line in lines:
                    if line.strip() == _AGENTPACK_MARKER:
                        new_lines.append(snippet)
                        skip_next = True
                        continue
                    if skip_next:
                        skip_next = False
                        continue
                    new_lines.append(line)
                new_content = "".join(new_lines)
                if new_content != content:
                    hook_path.write_text(new_content)
                    results[event] = "updated"
                else:
                    results[event] = "unchanged"
            else:
                # Append to existing hook
                sep = "" if content.endswith("\n") else "\n"
                hook_path.write_text(content + sep + snippet)
                results[event] = "appended"
        else:
            hook_path.write_text(f"#!/bin/sh\n{snippet}")
            results[event] = "created"

        # Ensure executable
        current = hook_path.stat().st_mode
        hook_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return results


def remove_git_hooks(root: Path) -> dict[str, str]:
    """Remove agentpack lines from .git/hooks/*. Returns {hook_name: action}."""
    hooks_dir = git_hooks_dir(root)
    if hooks_dir is None or not hooks_dir.exists():
        return {}

    results: dict[str, str] = {}
    for event in _HOOK_EVENTS:
        hook_path = hooks_dir / event
        if not hook_path.exists():
            continue
        content = hook_path.read_text()
        if _AGENTPACK_MARKER not in content:
            results[event] = "unchanged"
            continue
        lines = content.splitlines(keepends=True)
        new_lines = []
        skip_next = False
        for line in lines:
            if line.strip() == _AGENTPACK_MARKER:
                skip_next = True
                continue
            if skip_next:
                skip_next = False
                continue
            new_lines.append(line)
        new_content = "".join(new_lines)
        # Remove file if only shebang remains
        stripped = new_content.strip()
        if stripped in ("", "#!/bin/sh", "#!/bin/bash"):
            hook_path.unlink()
            results[event] = "removed"
        else:
            hook_path.write_text(new_content)
            results[event] = "cleaned"

    return results
