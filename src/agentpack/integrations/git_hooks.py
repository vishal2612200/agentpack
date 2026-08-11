from __future__ import annotations

import stat
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentpack.core import git
from agentpack.integrations.platform import cli_module_argv, shell_join

# Hooks that indicate the working tree changed and the pack may be stale.
_HOOK_EVENTS = ("post-commit", "post-merge", "post-checkout")

_AGENTPACK_MARKER = "# agentpack:auto-repack:start"
_AGENTPACK_END_MARKER = "# agentpack:auto-repack:end"
_LEGACY_MARKER = "# agentpack:auto-repack"


@dataclass(frozen=True)
class GitHookStatus:
    state: Literal["missing", "valid", "malformed", "duplicate", "stale"]
    detail: str


def _hook_script(agent: str) -> str:
    effective = agent if agent not in ("auto", "") else "auto"
    command = shell_join(cli_module_argv("hook", "--event", "GitAutoRepack", "--agent", effective))
    return f"{_AGENTPACK_MARKER}\n{command}\n{_AGENTPACK_END_MARKER}\n"


def _hook_command(agent: str) -> str:
    effective = agent if agent not in ("auto", "") else "auto"
    return shell_join(cli_module_argv("hook", "--event", "GitAutoRepack", "--agent", effective))


def inspect_git_hook(content: str, agent: str) -> GitHookStatus:
    lines = content.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == _AGENTPACK_MARKER]
    ends = [index for index, line in enumerate(lines) if line.strip() == _AGENTPACK_END_MARKER]
    legacy = any(line.strip() == _LEGACY_MARKER for line in lines if line.strip() != _AGENTPACK_MARKER)
    if not starts and not ends and not legacy:
        return GitHookStatus("missing", "missing auto-repack hook")
    if legacy:
        return GitHookStatus("malformed", "legacy auto-repack marker")
    if len(starts) > 1 or len(ends) > 1:
        return GitHookStatus("duplicate", "duplicate auto-repack blocks")
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        return GitHookStatus("malformed", "incomplete auto-repack block")
    block = "\n".join(lines[starts[0] : ends[0] + 1])
    expected_commands = {_hook_command(agent), _hook_command("auto")}
    commands = [line.strip() for line in lines[starts[0] + 1 : ends[0]] if line.strip()]
    if len(commands) != 1 or commands[0] not in expected_commands:
        return GitHookStatus("stale", "auto-repack command does not match selected agent")
    if not any(expected in block for expected in expected_commands):
        return GitHookStatus("stale", "auto-repack command missing")
    return GitHookStatus("valid", "current auto-repack hook present")


def _looks_agentpack_command(line: str) -> bool:
    normalized = line.strip().lower()
    return bool(normalized) and (
        "agentpack" in normalized
        or "gitautorepack" in normalized
        or normalized == "ent auto"
    )


def _legacy_only(lines: list[str]) -> bool:
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#!") or stripped.startswith("#"):
            continue
        if stripped in {_LEGACY_MARKER, _AGENTPACK_MARKER, _AGENTPACK_END_MARKER} or _looks_agentpack_command(stripped):
            continue
        return False
    return True


def _replace_managed_content(content: str, snippet: str) -> str:
    lines = content.splitlines(keepends=True)
    if _legacy_only(lines) and any(
        line.strip() in {_LEGACY_MARKER, _AGENTPACK_MARKER, _AGENTPACK_END_MARKER} for line in lines
    ):
        prefix = [line for line in lines if line.lstrip().startswith("#!")]
        return "".join(prefix) + snippet

    output: list[str] = []
    inserted = False
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == _AGENTPACK_MARKER:
            end = next((i for i in range(index + 1, len(lines)) if lines[i].strip() == _AGENTPACK_END_MARKER), None)
            if not inserted:
                output.append(snippet)
                inserted = True
            if end is not None:
                index = end + 1
            else:
                index += 1
                while index < len(lines) and _looks_agentpack_command(lines[index]):
                    index += 1
            continue
        if stripped == _LEGACY_MARKER:
            if not inserted:
                output.append(snippet)
                inserted = True
            index += 1
            if index < len(lines) and _looks_agentpack_command(lines[index]):
                index += 1
            continue
        if stripped == _AGENTPACK_END_MARKER:
            index += 1
            continue
        output.append(lines[index])
        index += 1
    if not inserted:
        separator = "" if not output or output[-1].endswith("\n") else "\n"
        output.extend([separator, snippet] if separator else [snippet])
    return "".join(output)


def _atomic_write_hook(path: Path, content: str) -> None:
    mode = path.stat().st_mode if path.exists() else stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


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
            if _AGENTPACK_MARKER in content or _LEGACY_MARKER in content:
                new_content = _replace_managed_content(content, snippet)
                if new_content != content:
                    _atomic_write_hook(hook_path, new_content)
                    results[event] = "updated"
                else:
                    results[event] = "unchanged"
            else:
                # Append to existing hook
                sep = "" if content.endswith("\n") else "\n"
                _atomic_write_hook(hook_path, content + sep + snippet)
                results[event] = "appended"
        else:
            _atomic_write_hook(hook_path, f"#!/bin/sh\n{snippet}")
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
        if _AGENTPACK_MARKER not in content and _LEGACY_MARKER not in content:
            results[event] = "unchanged"
            continue
        new_content = _replace_managed_content(content, "")
        # Remove file if only shebang remains
        stripped = new_content.strip()
        if stripped in ("", "#!/bin/sh", "#!/bin/bash"):
            hook_path.unlink()
            results[event] = "removed"
        else:
            _atomic_write_hook(hook_path, new_content)
            results[event] = "cleaned"

    return results
