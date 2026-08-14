from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pathspec


DEFAULT_AGENTIGNORE = """\
# dependencies
node_modules/
.venv/
venv/
__pycache__/

# builds
dist/
build/
.next/
coverage/
out/
target/

# caches
.pytest_cache/
.mypy_cache/
.ruff_cache/
.cache/
.turbo/
.parcel-cache/

# generated/noisy
generated/
.cxx/
.gradle/
intermediates/
outputs/
.serverless/
tmp/
temp/
*.generated.*
*.min.js
*.map
*.snap
*.lock
*.log

# secrets
.env
.env.*
*.pem
*.key
id_rsa
id_dsa
id_ecdsa
id_ed25519
*.p12
*.pfx
*.jks
.npmrc
.pypirc
.netrc
*.tfvars
terraform.tfvars

# lock files
package-lock.json
yarn.lock
pnpm-lock.yaml
Pipfile.lock
poetry.lock
Cargo.lock
composer.lock
Gemfile.lock

# large data
*.csv
*.jsonl
*.parquet

# claude code internals
.claude/worktrees/
"""


AGENTIGNORE_IMPORT_START = "# agentpack:imported-gitignore:start"
AGENTIGNORE_IMPORT_END = "# agentpack:imported-gitignore:end"

_NESTED_GITIGNORE_SKIP_DIRS = {
    ".agentpack",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
_SOURCE_TOKENS = {
    "app", "apps", "doc", "docs", "example", "examples", "lib", "libs",
    "public", "spec", "specs", "src", "story", "stories", "test", "tests",
}
_NOISE_TOKENS = {
    "build", "cache", "coverage", "dist", "generated", "log", "logs", "next",
    "nuxt", "out", "parcel", "release", "reports", "results", "serverless",
    "snap", "target", "temp", "tmp", "turbo", "vendor",
}
_SAFE_GLOB_SUFFIXES = (
    ".cache",
    ".log",
    ".min.js",
    ".snap",
    ".tmp",
    ".tsbuildinfo",
)


SENSITIVE_PATTERNS = pathspec.PathSpec.from_lines("gitignore", [
    ".env", ".env.*", "*.pem", "*.key",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "*.p12", "*.pfx", "*.jks",
    ".npmrc", ".pypirc", ".netrc",
    "*.tfvars", "terraform.tfvars",
])


@dataclass(frozen=True)
class AgentIgnoreImportSource:
    path: str
    rules: list[str]


@dataclass(frozen=True)
class AgentIgnoreSyncStatus:
    path: Path
    current_content: str | None
    desired_content: str
    imported_rules: list[str]
    imported_sources: list[AgentIgnoreImportSource]
    action: str

    @property
    def is_stale(self) -> bool:
        return self.action == "update"


def load_spec(ignore_path: Path) -> pathspec.PathSpec:
    if ignore_path.exists():
        lines = ignore_path.read_text().splitlines()
    else:
        lines = DEFAULT_AGENTIGNORE.splitlines()
    return pathspec.PathSpec.from_lines("gitignore", lines)


def is_ignored(spec: pathspec.PathSpec, path: str) -> bool:
    return spec.match_file(path)


def decode_ignore_rule(rule: str) -> str:
    chars: list[str] = []
    i = 0
    while i < len(rule):
        ch = rule[i]
        if ch != "\\":
            chars.append(ch)
            i += 1
            continue
        if i + 1 >= len(rule):
            chars.append("/")
            break
        nxt = rule[i + 1]
        if nxt in {" ", "#", "!"}:
            chars.append(nxt)
            i += 2
            continue
        else:
            chars.append("/")
            i += 1
            continue
    return "".join(chars)


def normalize_ignore_rule(rule: str) -> str:
    normalized = decode_ignore_rule(rule.strip())
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.replace("//", "/")


def strip_marked_block(content: str, start_marker: str, end_marker: str) -> str:
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start == -1 or end == -1 or end < start:
        return content
    end += len(end_marker)
    before = content[:start].rstrip()
    after = content[end:].lstrip("\n")
    if before and after:
        return before + "\n\n" + after
    return before or after


def agentignore_sync_status(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> AgentIgnoreSyncStatus:
    path = root / ".agentignore"
    current_content = path.read_text(encoding="utf-8") if path.exists() else None
    imported_sources = _collect_imported_sources(root, env=env)
    imported_rules = [rule for source in imported_sources for rule in source.rules]
    desired_content = _desired_agentignore_content(root, current_content, imported_rules)
    if current_content is None:
        action = "create"
    elif current_content == desired_content:
        action = "unchanged"
    else:
        action = "update"
    return AgentIgnoreSyncStatus(
        path=path,
        current_content=current_content,
        desired_content=desired_content,
        imported_rules=imported_rules,
        imported_sources=imported_sources,
        action=action,
    )


def format_import_summary(status: AgentIgnoreSyncStatus) -> str:
    source_count = len(status.imported_sources)
    rule_count = len(status.imported_rules)
    if rule_count == 0:
        return "Imported 0 generated/noisy rules."
    source_label = "source" if source_count == 1 else "sources"
    sample = ", ".join(status.imported_rules[:5])
    extra = f", ... {rule_count - 5} more" if rule_count > 5 else ""
    return (
        f"Imported {rule_count} generated/noisy rules from {source_count} ignore {source_label}: "
        f"{sample}{extra}"
    )


def _desired_agentignore_content(
    root: Path,
    current_content: str | None,
    imported_rules: list[str],
) -> str:
    if current_content is None:
        base = DEFAULT_AGENTIGNORE.rstrip()
        if not imported_rules:
            return base + "\n"
        return base + "\n\n" + _import_block(imported_rules)

    import_block = _import_block(imported_rules)
    base = strip_marked_block(current_content, AGENTIGNORE_IMPORT_START, AGENTIGNORE_IMPORT_END).rstrip()
    if not import_block:
        updated = strip_marked_block(current_content, AGENTIGNORE_IMPORT_START, AGENTIGNORE_IMPORT_END)
        if updated != current_content and updated and not updated.endswith("\n"):
            updated += "\n"
        return updated
    updated = (base + "\n\n" if base else "") + import_block.rstrip()
    if updated and not updated.endswith("\n"):
        updated += "\n"
    return updated


def _import_block(rules: list[str]) -> str:
    if not rules:
        return ""
    return (
        f"{AGENTIGNORE_IMPORT_START}\n"
        "# Imported from git ignore sources because these look like generated/noisy paths\n"
        + "\n".join(rules)
        + "\n"
        f"{AGENTIGNORE_IMPORT_END}\n"
    )


def _collect_imported_sources(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> list[AgentIgnoreImportSource]:
    default_rules = {
        line.strip()
        for line in DEFAULT_AGENTIGNORE.splitlines()
        if line.strip() and not line.startswith("#")
    }
    sources: list[AgentIgnoreImportSource] = []
    seen_rules: set[str] = set()
    for display_path, ignore_path, base_dir in _iter_ignore_sources(root, env=env):
        rules = _extract_import_rules(
            root,
            ignore_path,
            base_dir,
            default_rules=default_rules,
            seen_rules=seen_rules,
        )
        if rules:
            sources.append(AgentIgnoreImportSource(path=display_path, rules=rules))
    return sources


def _iter_ignore_sources(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> list[tuple[str, Path, Path]]:
    env = env or os.environ
    sources: list[tuple[str, Path, Path]] = []
    root_gitignore = root / ".gitignore"
    if root_gitignore.exists():
        sources.append((".gitignore", root_gitignore, root))

    for path in sorted(root.rglob(".gitignore")):
        if path == root_gitignore:
            continue
        rel_parts = path.relative_to(root).parts[:-1]
        if any(part in _NESTED_GITIGNORE_SKIP_DIRS for part in rel_parts):
            continue
        sources.append((str(path.relative_to(root)), path, path.parent))

    info_exclude = root / ".git" / "info" / "exclude"
    if info_exclude.exists():
        sources.append((".git/info/exclude", info_exclude, root))

    global_ignore = _global_gitignore_path(env)
    if global_ignore and global_ignore.exists():
        sources.append((_display_global_path(global_ignore, env), global_ignore, root))
    return sources


def _global_gitignore_path(env: Mapping[str, str]) -> Path | None:
    configured = _git_config_excludesfile()
    if configured:
        return _expanduser_path(configured, env)
    home = Path(env.get("HOME", str(Path.home())))
    xdg_home = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
    candidate = xdg_home / "git" / "ignore"
    return candidate if candidate.exists() else None


def _git_config_excludesfile() -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get", "core.excludesfile"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value or None


def _expanduser_path(raw_path: str, env: Mapping[str, str]) -> Path:
    if raw_path.startswith("~"):
        home = Path(env.get("HOME", str(Path.home())))
        return home / raw_path[2:] if raw_path.startswith("~/") else home
    return Path(raw_path)


def _display_global_path(path: Path, env: Mapping[str, str]) -> str:
    home = Path(env.get("HOME", str(Path.home())))
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _extract_import_rules(
    root: Path,
    ignore_path: Path,
    base_dir: Path,
    *,
    default_rules: set[str],
    seen_rules: set[str],
) -> list[str]:
    try:
        lines = ignore_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    imported: list[str] = []
    in_agentpack_block = False
    for raw in lines:
        line = raw.strip()
        if line == "# agentpack:start":
            in_agentpack_block = True
            continue
        if line == "# agentpack:end":
            in_agentpack_block = False
            continue
        if in_agentpack_block or not line:
            continue
        if line.startswith("#"):
            continue
        normalized = _normalize_import_rule(line, root=root, base_dir=base_dir)
        if not normalized or normalized in default_rules or normalized in seen_rules:
            continue
        if not _should_import_gitignore_rule(normalized):
            continue
        imported.append(normalized)
        seen_rules.add(normalized)
    return imported


def _normalize_import_rule(rule: str, *, root: Path, base_dir: Path) -> str:
    normalized = normalize_ignore_rule(rule)
    if not normalized or normalized.startswith(("!", "#")):
        return ""
    anchored_to_base = normalized.startswith("/")
    if anchored_to_base:
        normalized = normalized[1:]
    if not normalized:
        return ""
    if base_dir == root:
        return normalized
    try:
        prefix = str(base_dir.relative_to(root)).replace("\\", "/").strip("/")
    except ValueError:
        prefix = ""
    if not prefix:
        return normalized
    if normalized.startswith("*."):
        return normalized
    return f"{prefix}/{normalized}".replace("//", "/")


def _ignore_rule_tokens(rule: str) -> set[str]:
    token_chars = []
    for ch in rule.lower():
        token_chars.append(ch if ch.isalnum() else " ")
    return {token for token in "".join(token_chars).split() if token}


def _should_import_gitignore_rule(rule: str) -> bool:
    if rule.startswith(".agentpack/") or rule == ".agentignore":
        return False
    if rule.startswith("*.") or rule.startswith("**/*."):
        return rule.endswith(_SAFE_GLOB_SUFFIXES)

    tokens = _ignore_rule_tokens(rule)
    if not tokens:
        return False
    if tokens & _NOISE_TOKENS:
        return True
    return not (tokens & _SOURCE_TOKENS)
