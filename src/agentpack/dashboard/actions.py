from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]
import tomli_w

from agentpack.core.config import DEFAULT_CONFIG, Config, config_path, load_config
from agentpack.dashboard.collectors import EDITABLE_CONFIG_FIELDS, _config_summary
from agentpack.dashboard.models import DashboardConfigSummary


VALID_TASK_STATES = {"planned", "in_progress", "blocked", "handed_off", "done"}
VALID_AGENTS = {"all", "auto", "claude", "codex", "cursor", "windsurf", "vscode", "gemini"}
INSTALL_AGENTS = {"auto", "claude", "codex", "cursor", "windsurf", "vscode", "gemini"}


class DashboardActionError(ValueError):
    pass


def build_dashboard_action_command(action_id: str, payload: dict[str, Any]) -> str:
    action = action_id.strip().lower().replace("-", "_")
    if action in {"next", "doctor", "doctor_all", "status", "dev_check", "review"}:
        return {
            "next": "agentpack next",
            "doctor": f"agentpack doctor --agent {_agent(payload.get('agent'), default='all')}",
            "doctor_all": "agentpack doctor --agent all",
            "status": "agentpack status",
            "dev_check": "agentpack dev-check",
            "review": "agentpack review",
        }[action]
    if action in {"refresh_context", "guard_refresh"}:
        command = [
            "agentpack",
            "guard",
            "--agent",
            _agent(payload.get("agent"), default="codex"),
            "--repair-stale",
            "--refresh-context",
            "--thread",
            _thread(payload.get("thread"), default="global"),
        ]
        mode = str(payload.get("mode") or "").strip()
        if mode:
            command.extend(["--mode", mode])
        budget = _positive_int(payload.get("budget"))
        if budget:
            command.extend(["--budget", str(budget)])
        return _join(command)
    if action in {"pack", "pack_auto"}:
        command = ["agentpack", "pack", "--task", str(payload.get("task") or "auto")]
        mode = str(payload.get("mode") or "").strip()
        agent = str(payload.get("agent") or "").strip()
        if mode:
            command.extend(["--mode", mode])
        if agent:
            command.extend(["--agent", _agent(agent, default="auto")])
        return _join(command)
    if action in {"route", "route_context", "route_task"}:
        task = _required_text(payload.get("task"), "task")
        return _join(["agentpack", "route", "--task", task, "--json"])
    if action in {"set_task", "task_set"}:
        task = _required_text(payload.get("task"), "task")
        command = ["agentpack", "task", "set", task, "--thread", _thread(payload.get("thread"), default="global")]
        if payload.get("refresh") or payload.get("guard"):
            command.append("--guard")
        mode = str(payload.get("mode") or "").strip()
        if mode:
            command.extend(["--mode", mode])
        return _join(command)
    if action in {"clear_task", "task_clear"}:
        return _join(["agentpack", "task", "clear", "--thread", _thread(payload.get("thread"), default="global")])
    if action in {"set_state", "state_set"}:
        status = str(payload.get("status") or "").strip().lower()
        if status not in VALID_TASK_STATES:
            raise DashboardActionError("invalid task state")
        command = ["agentpack", "state", "set", status, "--thread", _thread(payload.get("thread"), default="global")]
        summary = str(payload.get("summary") or "").strip()
        if summary:
            command.extend(["--summary", summary])
        return _join(command)
    if action in {"archive_thread", "thread_archive"}:
        thread_id = _required_text(payload.get("thread_id"), "thread_id")
        summary = str(payload.get("summary") or "Archived from dashboard.").strip()
        return _join(["agentpack", "threads", "archive", thread_id, "--summary", summary])
    if action in {"prune_threads", "thread_prune"}:
        return _join(["agentpack", "threads", "prune", "--older-than", str(payload.get("older_than") or "7d"), "--yes"])
    if action in {"repair_integration", "repair_all"}:
        agent = _agent(payload.get("agent"), default="all")
        command = ["agentpack", "repair", "--agent", agent]
        if payload.get("global"):
            command.append("--global")
        return _join(command)
    if action in {"install_integration", "install"}:
        agent = _agent(payload.get("agent"), default="auto", valid=INSTALL_AGENTS)
        command = ["agentpack", "install", "--agent", agent]
        if payload.get("global"):
            command.append("--global")
        return _join(command)
    if action == "retrieve":
        target = _required_text(payload.get("target"), "target")
        command = ["agentpack", "retrieve", target]
        mode = str(payload.get("mode") or "").strip()
        if mode:
            command.extend(["--mode", mode])
        return _join(command)
    if action == "work":
        task = _required_text(payload.get("task"), "task")
        command = ["agentpack", "work", task]
        agent = str(payload.get("agent") or "").strip()
        mode = str(payload.get("mode") or "").strip()
        if agent:
            command.extend(["--agent", _agent(agent, default="auto")])
        if mode:
            command.extend(["--mode", mode])
        return _join(command)
    if action == "finish":
        summary = str(payload.get("summary") or "Finished from dashboard.").strip()
        return _join(["agentpack", "finish", "--summary", summary])
    if action == "release_check":
        return "agentpack release-check --profile ci"
    if action == "skills_index":
        return "agentpack skills index"
    if action == "ignore_suggest":
        return "agentpack ignore suggest"
    if action == "threads":
        return "agentpack threads --json"
    if action == "threads_active":
        return "agentpack threads --active --json"
    raise DashboardActionError(f"unknown dashboard action: {action_id}")


def update_dashboard_config(root: Path, updates: dict[str, Any]) -> DashboardConfigSummary:
    if not isinstance(updates, dict) or not updates:
        raise DashboardActionError("updates must be a non-empty object")
    current = load_config(root).model_dump(mode="json")
    defaults = DEFAULT_CONFIG.model_dump(mode="json")
    raw = _load_existing_config(root)
    for field_id, value in updates.items():
        section, key = _split_config_field(field_id)
        expected = _expected_config_value(defaults, section, key)
        raw.setdefault(section, {})
        if not isinstance(raw[section], dict):
            raise DashboardActionError(f"invalid config section: {section}")
        raw[section][key] = _coerce_config_value(value, expected, field_id)
        current.setdefault(section, {})
        if isinstance(current[section], dict):
            current[section][key] = raw[section][key]

    Config.model_validate(current)
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        tomli_w.dump(raw, fh)
    return _config_summary(root)


def _load_existing_config(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return data if isinstance(data, dict) else {}


def _split_config_field(field_id: str) -> tuple[str, str]:
    if field_id not in EDITABLE_CONFIG_FIELDS:
        raise DashboardActionError(f"invalid config field: {field_id}")
    section, key = field_id.split(".", 1)
    return section, key


def _expected_config_value(defaults: dict[str, Any], section: str, key: str) -> Any:
    section_values = defaults.get(section)
    if not isinstance(section_values, dict) or key not in section_values:
        raise DashboardActionError(f"unknown config field: {section}.{key}")
    return section_values[key]


def _coerce_config_value(value: Any, expected: Any, field_id: str) -> Any:
    if isinstance(expected, bool):
        if not isinstance(value, bool):
            raise DashboardActionError(f"{field_id} must be a boolean")
        return value
    if isinstance(expected, int) and not isinstance(expected, bool):
        if isinstance(value, bool):
            raise DashboardActionError(f"{field_id} must be an integer")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise DashboardActionError(f"{field_id} must be an integer") from exc
    if isinstance(expected, list):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise DashboardActionError(f"{field_id} must be a list of strings")
        return value
    if not isinstance(value, str):
        raise DashboardActionError(f"{field_id} must be a string")
    return value


def _agent(value: Any, *, default: str, valid: set[str] | None = None) -> str:
    agent = str(value or default).strip().lower()
    allowed = valid or VALID_AGENTS
    if agent not in allowed:
        raise DashboardActionError("unknown agent")
    return agent


def _thread(value: Any, *, default: str) -> str:
    thread = str(value or default).strip()
    if not thread:
        return default
    return thread


def _positive_int(value: Any) -> int:
    if value in {None, ""}:
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DashboardActionError("budget must be an integer") from exc
    if parsed <= 0:
        raise DashboardActionError("budget must be positive")
    return parsed


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DashboardActionError(f"{field} is required")
    return text


def _join(argv: list[str]) -> str:
    return " ".join(shlex.quote(item) for item in argv)
