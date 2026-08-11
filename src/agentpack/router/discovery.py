from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from agentpack.router.models import SkillInventory
from agentpack.router.parser import parse_rule_file, parse_skill_file
from agentpack.router.skills_index import (
    INDEX_PATH,
    inventory_for_route,
    load_inventory_index,
    write_inventory_index,
)

__all__ = [
    "DEFAULT_SKILL_PATHS",
    "INDEX_PATH",
    "ROOT_RULE_FILES",
    "discover_inventory",
    "inventory_for_route",
    "load_inventory_index",
    "write_inventory_index",
]

DEFAULT_SKILL_PATHS = [
    "skills",
    ".claude-plugin",
    ".claude/skills",
    "~/.claude/skills",
    "~/.codex/skills",
    "~/.agents/skills",
    ".agentpack/skills",
    ".cursor/rules",
]
ROOT_RULE_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")


def discover_inventory(root: Path, paths: list[str] | None = None) -> SkillInventory:
    inventory = SkillInventory()
    for configured_path in paths or DEFAULT_SKILL_PATHS:
        path = _resolve_source_path(root, configured_path)
        if not path.exists():
            continue
        plugin_skills = _discover_claude_plugin_skills(path, root)
        if plugin_skills:
            inventory.skills.extend(plugin_skills)
            continue
        if configured_path.endswith(".cursor/rules") or path.name == "rules" and path.parent.name == ".cursor":
            inventory.rules.extend(_discover_cursor_rules(path, root))
            continue
        inventory.skills.extend(_discover_skills(path, root, configured_path))

    for filename in ROOT_RULE_FILES:
        path = root / filename
        if path.exists():
            inventory.rules.append(parse_rule_file(path, root=root, source=filename, priority=50))

    inventory.skills = _dedupe_skills(inventory.skills)
    inventory.rules = _dedupe_by_path(inventory.rules)
    return inventory


def _discover_skills(path: Path, root: Path, source: str) -> list:
    candidates: list[Path] = []
    for child in sorted(path.iterdir()):
        if child.is_dir():
            skill_md = child / "SKILL.md"
            if skill_md.exists():
                candidates.append(skill_md)
        elif child.suffix.lower() == ".md":
            candidates.append(child)
    return [parse_skill_file(candidate, root=root, source=source) for candidate in candidates]


def _discover_claude_plugin_skills(path: Path, root: Path) -> list:
    manifest = _plugin_manifest_path(path)
    if manifest is None:
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    skill_refs = data.get("skills")
    if not isinstance(skill_refs, list):
        return []

    plugin_name = str(data.get("name") or manifest.parent.parent.name or manifest.parent.name)
    candidates: list[Path] = []
    for value in skill_refs:
        if not isinstance(value, str) or not value.strip():
            continue
        ref = value.strip()
        for base in _plugin_skill_bases(manifest):
            candidate = (base / ref).resolve()
            if candidate.is_dir():
                candidate = candidate / "SKILL.md"
            if candidate.exists() and candidate.name == "SKILL.md" and candidate not in candidates:
                candidates.append(candidate)
                break
    return [
        parse_skill_file(candidate, root=root, source=f"claude-plugin:{plugin_name}")
        for candidate in candidates
    ]


def _plugin_manifest_path(path: Path) -> Path | None:
    if path.is_file() and path.name == "plugin.json":
        return path
    if path.is_dir():
        direct = path / "plugin.json"
        if direct.exists():
            return direct
        nested = path / ".claude-plugin" / "plugin.json"
        if nested.exists():
            return nested
    return None


def _plugin_skill_bases(manifest: Path) -> list[Path]:
    bases = [manifest.parent]
    if manifest.parent.name == ".claude-plugin":
        bases.insert(0, manifest.parent.parent)
    return bases


def _discover_cursor_rules(path: Path, root: Path) -> list:
    return [
        parse_rule_file(candidate, root=root, source=".cursor/rules", priority=60)
        for candidate in sorted(path.glob("*.mdc"))
    ]


def _resolve_source_path(root: Path, configured_path: str) -> Path:
    expanded = Path(configured_path).expanduser()
    if expanded.is_absolute():
        return expanded
    return root / expanded


def _dedupe_by_path(items: list) -> list:
    seen: set[str] = set()
    result: list = []
    for item in items:
        if item.path in seen:
            continue
        seen.add(item.path)
        result.append(item)
    return result


def _dedupe_skills(items: list) -> list:
    """Collapse identical installed skills while retaining alternate paths."""
    seen: dict[tuple[str, str], Any] = {}
    result: list = []
    for item in items:
        content_hash = hashlib.sha256(item.raw_text.encode("utf-8")).hexdigest()
        identity = (item.name.casefold().strip(), content_hash)
        existing = seen.get(identity)
        if existing is None:
            seen[identity] = item
            result.append(item)
            continue
        aliases = set(existing.aliases)
        aliases.update({existing.path, item.path, existing.source, item.source})
        existing.aliases = sorted(alias for alias in aliases if alias)
    return result
