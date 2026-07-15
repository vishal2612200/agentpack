from __future__ import annotations

import json

from agentpack.router.discovery import INDEX_PATH
from agentpack.router.discovery import discover_inventory
from agentpack.router.skills_index import INDEX_GENERATOR_VERSION, ensure_inventory_index, load_inventory_index_document


def test_discovery_finds_repo_global_cursor_and_agent_rules(tmp_path, monkeypatch):
    repo_skill = tmp_path / ".agentpack" / "skills" / "webhook" / "SKILL.md"
    repo_skill.parent.mkdir(parents=True)
    repo_skill.write_text("# Webhook Idempotency\n\nUse for webhook handlers.\n", encoding="utf-8")

    home = tmp_path / "home"
    global_skill = home / ".codex" / "skills" / "django-pytest" / "SKILL.md"
    global_skill.parent.mkdir(parents=True)
    global_skill.write_text("# Django Pytest\n\nUse for pytest workflows.\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    cursor_rule = tmp_path / ".cursor" / "rules" / "tests.mdc"
    cursor_rule.parent.mkdir(parents=True)
    cursor_rule.write_text(
        "---\ndescription: Test rule\nglobs: tests/**\n---\n\nUse test rules.\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("# Repo Rules\n\nFollow repo instructions.\n", encoding="utf-8")

    inventory = discover_inventory(tmp_path)

    assert {skill.name for skill in inventory.skills} >= {"Webhook Idempotency", "Django Pytest"}
    assert {rule.path for rule in inventory.rules} >= {".cursor/rules/tests.mdc", "AGENTS.md"}


def test_discovery_ignores_missing_directories(tmp_path):
    inventory = discover_inventory(tmp_path, paths=[".missing", "~/.also-missing"])

    assert inventory.skills == []
    assert inventory.rules == []


def test_discovery_deduplicates_identical_skills_across_roots(tmp_path):
    first = tmp_path / "first" / "same" / "SKILL.md"
    second = tmp_path / "second" / "same" / "SKILL.md"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    content = "# Shared Skill\n\nUse this skill for the same task.\n"
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")

    inventory = discover_inventory(tmp_path, paths=["first", "second"])

    assert [skill.name for skill in inventory.skills] == ["Shared Skill"]
    assert set(inventory.skills[0].aliases) >= {"first/same/SKILL.md", "second/same/SKILL.md"}


def test_discovery_finds_claude_plugin_manifest_skills(tmp_path):
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    plugin_dir.joinpath("plugin.json").write_text(
        '{"name": "andrej-karpathy-skills", "skills": ["./skills/karpathy-guidelines"]}\n',
        encoding="utf-8",
    )
    skill = tmp_path / "skills" / "karpathy-guidelines" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: karpathy-guidelines\n"
        "description: Behavioral guidelines to reduce common LLM coding mistakes. Use when writing, reviewing, or refactoring code.\n"
        "---\n\n"
        "# Karpathy Guidelines\n",
        encoding="utf-8",
    )

    inventory = discover_inventory(tmp_path, paths=[".claude-plugin"])

    assert [item.name for item in inventory.skills] == ["karpathy-guidelines"]
    assert inventory.skills[0].source == "claude-plugin:andrej-karpathy-skills"


def test_ensure_inventory_index_rebuilds_when_skill_file_changes(tmp_path):
    skill = tmp_path / ".agentpack" / "skills" / "pytest-debugging" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: pytest-debugging\ntask_types: [testing]\nlanguages: [python]\n---\n\nUse for pytest failures.\n",
        encoding="utf-8",
    )

    first = ensure_inventory_index(tmp_path, paths=[".agentpack/skills"])

    assert first.refreshed is True
    assert [item.name for item in first.document.inventory.skills] == ["pytest-debugging"]

    skill.write_text(
        "---\n"
        "name: pytest-debugging\n"
        "task_types: [testing]\n"
        "languages: [python]\n"
        "frameworks: [pytest]\n"
        "---\n\n"
        "Use for pytest failures.\n",
        encoding="utf-8",
    )
    second = ensure_inventory_index(tmp_path, paths=[".agentpack/skills"])

    assert second.refreshed is True
    assert second.reason == "fingerprint_changed"
    assert second.document.inventory.skills[0].frameworks == ["pytest"]


def test_ensure_inventory_index_reuses_fresh_index(tmp_path):
    skill = tmp_path / ".agentpack" / "skills" / "docs" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Docs\n\nUse for documentation updates.\n", encoding="utf-8")

    first = ensure_inventory_index(tmp_path, paths=[".agentpack/skills"])
    second = ensure_inventory_index(tmp_path, paths=[".agentpack/skills"])

    assert first.refreshed is True
    assert second.refreshed is False
    assert second.reason == "fresh"


def test_ensure_inventory_index_rebuilds_when_generator_changes(tmp_path):
    skill = tmp_path / ".agentpack" / "skills" / "docs" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Docs\n\nUse for documentation updates.\n", encoding="utf-8")

    first = ensure_inventory_index(tmp_path, paths=[".agentpack/skills"])
    path = tmp_path / INDEX_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    data["generator_version"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")

    second = ensure_inventory_index(tmp_path, paths=[".agentpack/skills"])

    assert first.refreshed is True
    assert second.refreshed is True
    assert second.reason == "generator_changed"
    assert second.document.generator_version == INDEX_GENERATOR_VERSION


def test_load_inventory_index_document_accepts_old_inventory_shape(tmp_path):
    path = tmp_path / INDEX_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": [
                    {
                        "name": "legacy",
                        "source": ".agentpack/skills",
                        "path": ".agentpack/skills/legacy/SKILL.md",
                    }
                ],
                "rules": [],
            }
        ),
        encoding="utf-8",
    )

    document = load_inventory_index_document(tmp_path)

    assert document is not None
    assert document.inventory.skills[0].name == "legacy"
    assert document.sources == []
