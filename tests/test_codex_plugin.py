from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_JSON = ROOT / ".codex-plugin" / "plugin.json"
PACKAGED_PLUGIN_JSON = ROOT / "src" / "agentpack" / "data" / "codex_plugin" / ".codex-plugin" / "plugin.json"
SKILLS_DIR = ROOT / "skills"
PACKAGED_SKILLS_DIR = ROOT / "src" / "agentpack" / "data" / "codex_plugin" / "skills"
README_SYMBOL = ROOT / "docs" / "assets" / "agentpack-symbol.png"
PLUGIN_ICON = ROOT / "assets" / "icon.png"
PACKAGED_PLUGIN_ICON = ROOT / "src" / "agentpack" / "data" / "codex_plugin" / "assets" / "icon.png"
PLUGIN_SCREENSHOT = ROOT / "assets" / "route-demo.svg"
PACKAGED_PLUGIN_SCREENSHOT = ROOT / "src" / "agentpack" / "data" / "codex_plugin" / "assets" / "route-demo.svg"


def test_codex_plugin_manifest_points_to_skills() -> None:
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    packaged_manifest = json.loads(PACKAGED_PLUGIN_JSON.read_text(encoding="utf-8"))

    assert packaged_manifest == manifest

    assert manifest["name"] == "agentpack"
    assert manifest["skills"] == "./skills/"
    assert manifest["interface"]["displayName"] == "AgentPack"
    assert manifest["interface"]["composerIcon"] == "./assets/icon.png"
    assert manifest["interface"]["logo"] == "./assets/icon.png"
    assert manifest["interface"]["screenshots"] == ["./assets/route-demo.svg"]
    assert manifest["homepage"] == "https://vishal2612200.github.io/agentpack/codex-plugin/"
    assert manifest["interface"]["websiteURL"] == "https://vishal2612200.github.io/agentpack/"
    assert manifest["interface"]["privacyPolicyURL"] == "https://vishal2612200.github.io/agentpack/privacy/"
    assert manifest["interface"]["termsOfServiceURL"] == "https://vishal2612200.github.io/agentpack/terms/"
    description = " ".join(
        [
            manifest["description"],
            manifest["interface"]["shortDescription"],
            manifest["interface"]["longDescription"],
        ]
    ).lower()
    assert "local context engine" in description
    assert "not a coding agent" in description
    prompts = manifest["interface"]["defaultPrompt"]
    assert "@agentpack-learn retry handling in this repo" in prompts


def test_codex_plugin_has_distribution_icon() -> None:
    assert README_SYMBOL.read_bytes() == PLUGIN_ICON.read_bytes()
    assert PACKAGED_PLUGIN_ICON.read_bytes() == PLUGIN_ICON.read_bytes()
    assert PLUGIN_ICON.stat().st_size < 250_000
    assert PLUGIN_ICON.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_codex_plugin_has_distribution_screenshot() -> None:
    assert PACKAGED_PLUGIN_SCREENSHOT.read_text(encoding="utf-8") == PLUGIN_SCREENSHOT.read_text(
        encoding="utf-8"
    )
    text = PLUGIN_SCREENSHOT.read_text(encoding="utf-8")
    assert "<svg" in text
    assert "AgentPack route, skills, review, learn, and memory demo" in text
    assert "Skill recommendations" in text
    assert "agentpack review --check --dry-run-post" in text
    assert "agentpack learn" in text
    assert "agentpack memory --timeline" in text
    assert "Advisory signals" in text


def test_hol_plugin_scanner_workflow_exists() -> None:
    workflow = (ROOT / ".github" / "workflows" / "hol-plugin-scanner.yml").read_text(encoding="utf-8")

    assert "hashgraph-online/ai-plugin-scanner-action@b4ca70a7342b42e712df83f832b464110cb81f18" in workflow
    assert 'plugin_dir: "src/agentpack/data/codex_plugin"' in workflow
    assert "min_score: 80" in workflow
    assert "fail_on_severity: high" in workflow


def test_hol_repository_scan_ignores_generated_agentpack_state() -> None:
    scanner_config = (ROOT / ".plugin-scanner.toml").read_text(encoding="utf-8")

    assert "[scanner]" in scanner_config
    assert '".agentpack/**"' in scanner_config


def test_codexignore_keeps_plugin_scan_focused() -> None:
    ignore = (ROOT / ".codexignore").read_text(encoding="utf-8")

    assert ".agentpack/" in ignore
    assert "tests/" in ignore
    assert ".github/workflows/ci.yml" in ignore
    assert ".github/workflows/hol-plugin-scanner.yml" not in ignore
    assert "uv.lock" not in ignore
    assert (ROOT / "uv.lock").exists()
    assert (ROOT / "npm" / "package-lock.json").exists()


def test_packaged_codex_plugin_is_self_contained_for_distribution() -> None:
    bundle = ROOT / "src" / "agentpack" / "data" / "codex_plugin"

    for rel in (
        "README.md",
        "LICENSE",
        "SECURITY.md",
        ".codexignore",
        ".github/dependabot.yml",
        ".github/workflows/hol-plugin-scanner.yml",
        "uv.lock",
        "package.json",
        "package-lock.json",
    ):
        assert (bundle / rel).exists()


def test_packaged_codex_plugin_lockfiles_match_manifest_version() -> None:
    bundle = ROOT / "src" / "agentpack" / "data" / "codex_plugin"
    manifest = json.loads(PACKAGED_PLUGIN_JSON.read_text(encoding="utf-8"))
    package = json.loads((bundle / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((bundle / "package-lock.json").read_text(encoding="utf-8"))
    dependabot = (bundle / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert package["private"] is True
    assert package["version"] == manifest["version"]
    assert package_lock["version"] == manifest["version"]
    assert package_lock["packages"][""]["version"] == manifest["version"]
    assert 'package-ecosystem: "npm"' in dependabot


def test_codex_plugin_skills_delegate_to_existing_cli() -> None:
    expected = {
        "agentpack.md",
        "agentpack-route.md",
        "agentpack-pack.md",
        "agentpack-refresh.md",
        "agentpack-review.md",
        "agentpack-learn.md",
        "agentpack-handoff.md",
        "agentpack-resume.md",
    }

    assert {path.name for path in SKILLS_DIR.glob("*.md")} == expected
    assert {path.name for path in PACKAGED_SKILLS_DIR.glob("*.md")} == expected

    for skill_name in expected:
        assert (SKILLS_DIR / skill_name).read_text(encoding="utf-8") == (
            PACKAGED_SKILLS_DIR / skill_name
        ).read_text(encoding="utf-8")

    combined = "\n".join(path.read_text(encoding="utf-8") for path in SKILLS_DIR.glob("*.md"))
    assert "agentpack route --task" in combined
    assert 'agentpack review "$ARGUMENTS"' in combined
    assert "agentpack task set" in combined
    assert "agentpack pack --task auto" in combined
    assert "agentpack guard --agent codex --repair-stale --refresh-context" not in combined
    assert "agentpack-learn" in combined
    assert "agentpack handoff create" in combined
    assert "agentpack handoff resume" in combined
    assert "current local AgentPack session context" in combined
    assert "agentpack status" in combined
    assert ".agentpack/learning.md" in combined
    assert ".agentpack/review.prompt.md" in combined
    assert "understanding toon" in combined.lower()
    assert "findings toon" in combined.lower()
    assert "do not perform the review inline" in combined.lower()
    assert "stop and report blocked" in combined.lower()
    assert "read that understanding toon from disk" in combined.lower()
    assert "Reveal answer only after at least two tries" in combined
    assert "not a coding agent" in combined.lower()
    assert "map, not proof" in combined.lower()


def test_codex_plugin_docs_keep_local_first_boundary() -> None:
    docs = (ROOT / "docs" / "codex-plugin.md").read_text(encoding="utf-8").lower()

    assert "local context engine, not a coding agent" in docs
    assert "does not upload code" in docs
    assert "does not reimplement ranking, scanning, packing, mcp, or benchmarking" in docs
    assert "@agentpack-route" in docs
    assert "@agentpack-pack" in docs
    assert "@agentpack-review" in docs
    assert "@agentpack-learn" in docs
    assert "@agentpack-handoff" in docs
    assert "@agentpack-resume" in docs
    assert "_understanding.toon" in docs
    assert "_findings.toon" in docs


def test_agentpack_learn_slash_command_keeps_user_statement_last() -> None:
    command = (ROOT / "src" / "agentpack" / "data" / "agentpack-learn.md").read_text(encoding="utf-8")
    local = (ROOT / ".claude" / "commands" / "agentpack-learn.md").read_text(encoding="utf-8")
    codex_skill = (ROOT / "skills" / "agentpack-learn.md").read_text(encoding="utf-8")
    packaged_codex_skill = (
        ROOT / "src" / "agentpack" / "data" / "codex_plugin" / "skills" / "agentpack-learn.md"
    ).read_text(encoding="utf-8")

    assert command == local
    assert codex_skill == packaged_codex_skill
    for text in (command, local):
        lines = [line for line in text.splitlines() if line.strip()]
        assert lines[-1] == "User learning statement: $ARGUMENTS"
        assert text.count("$ARGUMENTS") == 1
        assert "Learning Curve Destroyer" in text
        assert "Real Error Simulator" in text
        assert "Confusion Breaker" in text
        assert "Personal Learning Path" in text
        assert "Forced Feynman Method" in text
        assert "agentpack status" in text
        assert ".agentpack/agent-lessons.md" in text

    for text in (codex_skill, packaged_codex_skill):
        assert "@agentpack-learn <statement>" in text
        assert "/agentpack-learn <statement>" in text
        assert "Learning Curve Destroyer" in text
        assert "Reveal answer only after at least two tries" in text
        assert ".agentpack/session-events.jsonl" in text


def test_agentpack_review_slash_command_matches_tracked_copy() -> None:
    command = (ROOT / "src" / "agentpack" / "data" / "agentpack-review.md").read_text(encoding="utf-8")
    local = (ROOT / ".claude" / "commands" / "agentpack-review.md").read_text(encoding="utf-8")

    assert command == local
    assert "/agentpack-review" in command
    assert 'agentpack review "$ARGUMENTS"' in command
    assert ".agentpack/review.prompt.md" in command
    assert "understanding toon" in command.lower()
    assert "findings toon" in command.lower()
    assert "do not perform the review inline" in command.lower()
    assert "stop and report blocked" in command.lower()
    assert "read that understanding toon from disk" in command.lower()


def test_agent_plugin_distribution_docs_cover_supported_hosts() -> None:
    docs = (ROOT / "docs" / "agent-plugins.md").read_text(encoding="utf-8").lower()

    for host in (
        "codex",
        "claude code",
        "cursor",
        "windsurf",
        "github copilot",
        "cline",
        "kiro",
        "opencode",
        "antigravity",
        "generic",
    ):
        assert host in docs

    assert "does not reimplement ranking, scanning, packing, mcp, or benchmarking" in docs
    assert "local context engine, not a coding agent" in docs
    assert "review, and learning" in docs
    assert "agentpack doctor --agent <agent>" in docs
    assert "native-integrations/cursor-extension/" in docs
    assert "native-integrations/windsurf-extension/" in docs


def test_portable_agent_rules_exist_for_common_hosts() -> None:
    paths = [
        "agent-rules/agentpack.md",
        ".cursorrules",
        ".cursor/rules/agentpack.mdc",
        ".windsurf/rules/agentpack.md",
        ".github/copilot-instructions.md",
        ".clinerules/agentpack.md",
        ".kiro/steering/agentpack.md",
        ".opencode/agentpack.md",
    ]

    for rel in paths:
        text = (ROOT / rel).read_text(encoding="utf-8")
        lower = text.lower()
        assert "agentpack" in lower
        assert ".agentpack/context.md" in text or "agentpack route --task" in text
        assert "agentpack pack --task auto" in text or "agentpack guard --agent" in text
        assert "TOON" in text
        assert "starting map, not proof" in lower or "starting points" in lower
