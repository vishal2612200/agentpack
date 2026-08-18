from __future__ import annotations

import json
import sys
import types

import pytest

from agentpack.core.token_estimator import estimate_tokens
from agentpack.mcp_server import MCP_TOOL_NAMES, _explain_route_impl, _get_skill_impl, _get_skills_impl, _readiness_impl, _route_task_impl, serve
from agentpack.router.service import RouteService


def _write_route_fixture(root):
    (root / ".agentpack").mkdir(exist_ok=True)
    (root / ".agentpack" / "config.toml").write_text(
        "[skills]\npaths = [\".agentpack/skills\", \".cursor/rules\"]\n",
        encoding="utf-8",
    )
    (root / "payments").mkdir()
    (root / "payments" / "webhooks.py").write_text("def handle_webhook(event):\n    return event\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_payment_webhooks.py").write_text(
        "from payments.webhooks import handle_webhook\n\n"
        "def test_payment_webhook():\n"
        "    assert handle_webhook({'id': 'evt_1'})\n",
        encoding="utf-8",
    )
    skill = root / ".agentpack" / "skills" / "django-pytest" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "# django-pytest\n\nUse for pytest test debugging, fixtures, mocks, and flaky tests.\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Repo Rules\n\nVerify changes with tests.\n", encoding="utf-8")


def test_mcp_route_task_returns_json_and_does_not_write_context(tmp_path, monkeypatch):
    for name in ("CODEX_CI", "CODEX_ENVIRONMENT", "CODEX_SHELL", "CODEX_THREAD_ID", "OPENAI_CODEX"):
        monkeypatch.delenv(name, raising=False)
    _write_route_fixture(tmp_path)

    data = json.loads(_route_task_impl(tmp_path, "fix flaky payment webhook test", "json"))

    assert data["selected_files"]
    assert data["current_agent"] == "claude"
    assert data["reviewer_agent"] == "codex"
    assert data["mode_reason"]
    assert data["selected_skills"][0]["name"] == "django-pytest"
    assert data["selected_skills"][0]["path"].endswith("SKILL.md")
    assert data["applied_rules"][0]["path"] == "AGENTS.md"
    assert "agent_prompt" not in data
    assert len(json.dumps(data)) < 8_000

    full = json.loads(_route_task_impl(tmp_path, "fix flaky payment webhook test", "json", "full"))
    assert "agent_prompt" in full
    assert not (tmp_path / ".agentpack" / "task.md").exists()
    assert not (tmp_path / ".agentpack" / "context.md").exists()


def test_mcp_get_skills_returns_inventory_json(tmp_path):
    _write_route_fixture(tmp_path)

    data = json.loads(_get_skills_impl(tmp_path, "json"))

    assert data["skills"][0]["name"] == "django-pytest"
    assert data["rules"][0]["path"] == "AGENTS.md"


def test_mcp_get_skill_returns_raw_skill_content(tmp_path):
    _write_route_fixture(tmp_path)

    content = _get_skill_impl(tmp_path, "django-pytest")

    assert "# django-pytest" in content
    assert "pytest test debugging" in content


def test_mcp_explain_route_includes_skill_scores(tmp_path):
    _write_route_fixture(tmp_path)

    data = json.loads(_explain_route_impl(tmp_path, "fix flaky payment webhook test", "json"))

    assert data["skill_scores"]
    assert data["skill_scores"][0]["reasons"]
    assert estimate_tokens(json.dumps(data)) <= 8_000


def test_mcp_readiness_proves_live_tool_exposure(tmp_path):
    (tmp_path / ".agentpack").mkdir()

    data = json.loads(_readiness_impl(tmp_path, "json"))

    assert data["ok"] is True
    assert "proves" in data["proof"]
    assert data["mcp_server"] == "agentpack"
    assert "readiness" in data["mcp_tools"]
    assert "route_task" in data["mcp_tools"]
    assert "pack" in data["cli_commands"]
    assert data["tool_manifest"]["count"] == len(MCP_TOOL_NAMES)
    assert data["tool_manifest"]["sha256"]
    assert data["token_estimator"]["mode"] in {"tiktoken", "chars_per_4_fallback"}


def test_route_service_separates_always_recommend_baseline_skill(tmp_path):
    (tmp_path / ".agentpack").mkdir(exist_ok=True)
    (tmp_path / ".agentpack" / "config.toml").write_text(
        "[skills]\npaths = [\".agentpack/skills\"]\nalways_recommend = [\"team-quality-bar\"]\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def validate():\n    return True\n", encoding="utf-8")
    skill = tmp_path / ".agentpack" / "skills" / "team-quality-bar" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "# team-quality-bar\n\nInternal behavior guide.\n",
        encoding="utf-8",
    )

    result = RouteService().route_task(tmp_path, "fix auth bug")

    assert [item.skill.name for item in result.baseline_skills] == ["team-quality-bar"]
    assert result.selected_skills == []
    assert "Baseline guidance" in result.agent_prompt


def test_mcp_server_registers_router_tools(monkeypatch):
    class FakeMCP:
        instances = []

        def __init__(self, name):
            self.name = name
            self.tools = {}
            FakeMCP.instances.append(self)

        def tool(self):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn
            return decorator

        def run(self):
            raise RuntimeError("stop after registration")

    fake_module = types.ModuleType("mcp.server.fastmcp")
    fake_module.FastMCP = FakeMCP
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_module)

    with pytest.raises(RuntimeError, match="stop after registration"):
        serve()

    tool_names = set(FakeMCP.instances[0].tools)
    assert tool_names == set(MCP_TOOL_NAMES)
    assert {
        "readiness",
        "route_task",
        "get_skills",
        "get_skill",
        "explain_route",
        "validate_toon",
        "create_handoff",
        "list_handoffs",
        "get_handoff",
        "accept_handoff",
        "release_handoff",
    } <= tool_names
