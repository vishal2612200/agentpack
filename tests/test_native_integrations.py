from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "native-integrations" / "status.json"


def test_native_integration_status_is_machine_readable() -> None:
    data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))

    assert data["schema_version"] == 1
    assert data["native_enforcement_contract"]["fallback_command"]
    assert "mandatory_pre_edit_or_pre_tool_hook" in data["native_enforcement_contract"]["required_capabilities"]
    assert {host["id"] for host in data["hosts"]} == {"claude", "codex", "cursor", "windsurf"}


def test_native_integration_entries_have_real_paths_and_blockers() -> None:
    data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    required = set(data["native_enforcement_contract"]["required_capabilities"])

    for host in data["hosts"]:
        path = ROOT / host["path"]
        assert path.exists(), host
        assert host["status"] in {"skeleton", "blocked_stub"}
        assert host["enforcement_level"] == "advisory"
        assert set(host["capabilities"]) == required
        assert not all(host["capabilities"].values())
        assert host["blocked_on"], host
        assert all("Host API" in blocker or "plugin API" in blocker for blocker in host["blocked_on"])
        assert (path / "README.md").exists(), host


def test_native_integration_docs_include_capability_matrix() -> None:
    readme = (ROOT / "native-integrations" / "README.md").read_text(encoding="utf-8")

    assert "## Capability Matrix" in readme
    assert "| Host | Workspace root | Command/MCP access | Prompt/task access | Mandatory pre-edit/pre-tool hook | Can block failed readiness |" in readme
    assert "Current entries remain `advisory`." in readme


def test_native_integration_docs_show_advisory_and_enforcement_examples() -> None:
    readme = (ROOT / "native-integrations" / "README.md").read_text(encoding="utf-8")

    assert "Advisory example" in readme
    assert "Enforcement example" in readme
    # honesty boundary: enforcement still requires a blocking host API
    assert "block" in readme.lower()


def test_extension_skeletons_default_to_agent_specific_readiness_commands() -> None:
    cursor_package = json.loads(
        (ROOT / "native-integrations" / "cursor-extension" / "package.json").read_text(encoding="utf-8")
    )
    windsurf_package = json.loads(
        (ROOT / "native-integrations" / "windsurf-extension" / "package.json").read_text(encoding="utf-8")
    )

    cursor_default = cursor_package["contributes"]["configuration"]["properties"]["agentpack.guardCommand"]["default"]
    windsurf_default = windsurf_package["contributes"]["configuration"]["properties"]["agentpack.guardCommand"]["default"]
    assert cursor_default == "agentpack doctor --agent cursor"
    assert windsurf_default == "agentpack doctor --agent windsurf"


def test_native_stub_readmes_state_not_production_enforced() -> None:
    for rel in (
        "native-integrations/cursor-extension/README.md",
        "native-integrations/windsurf-extension/README.md",
        "native-integrations/claude-native/README.md",
        "native-integrations/codex-native/README.md",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "Current enforcement level: `advisory`." in text
        assert "mandatory" in text.lower()
