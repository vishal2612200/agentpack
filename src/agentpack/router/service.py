from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from agentpack.application.pack_service import PackPlanner, PackRequest
from agentpack.core.config import load_config
from agentpack.router.discovery import discover_inventory, inventory_for_route
from agentpack.router.models import (
    AppliedRule,
    CommandSuggestion,
    RouteExplanation,
    RouteResult,
    SelectedSkill,
    SkillInventory,
)
from agentpack.router.prompt_builder import build_agent_prompt
from agentpack.router.scoring import _classify_task, _normalize_skill_key, score_skills

_TEST_TERMS = ("test", "tests", "pytest", "flaky", "fixture", "mock", "failing", "fail", "debug")


class RouteService:
    def inventory(self, root: Path, *, use_index: bool = True) -> SkillInventory:
        cfg = load_config(root)
        paths = cfg.skills.paths
        if use_index:
            return inventory_for_route(root, paths)
        return discover_inventory(root, paths)

    def route_task(self, root: Path, task: str) -> RouteResult:
        task = _normalize_task(task)
        cfg = load_config(root)
        plan = PackPlanner().plan(PackRequest(
            root=root,
            agent="generic",
            task=task,
            mode="balanced",
            budget=0,
            since=None,
            refresh=False,
            task_source="route",
        ))
        selected_files = [_selected_file_dict(item) for item in plan.selected[:20]]
        selected_paths = [item["path"] for item in selected_files]

        inventory = self.inventory(root)
        selected_skills, safety_warnings, _all_scores = score_skills(
            inventory.skills,
            task=task,
            selected_paths=selected_paths,
            selected_files=selected_files,
            max_selected=cfg.skills.max_selected,
            allow_external=cfg.skills.allow_external_side_effects,
            always_recommend=cfg.skills.always_recommend,
            historical_success=_load_skill_success(root),
        )
        selected_skills = _strip_skill_bodies(selected_skills)
        baseline_skills, selected_skills = _split_baseline_skills(selected_skills)
        applied_rules = _apply_rules(inventory, selected_paths)
        commands = _suggest_commands(task, selected_paths)

        result = RouteResult(
            task=task,
            selected_files=selected_files,
            selected_skills=selected_skills,
            baseline_skills=baseline_skills,
            applied_rules=applied_rules,
            suggested_commands=commands,
            safety_warnings=safety_warnings,
        )
        result.agent_prompt = build_agent_prompt(result)
        return result

    def explain_route(self, root: Path, task: str) -> RouteExplanation:
        task = _normalize_task(task)
        result = self.route_task(root, task)
        cfg = load_config(root)
        selected_paths = [item["path"] for item in result.selected_files]
        inventory = self.inventory(root)
        _selected, _warnings, all_scores = score_skills(
            inventory.skills,
            task=task,
            selected_paths=selected_paths,
            selected_files=result.selected_files,
            max_selected=max(len(inventory.skills), cfg.skills.max_selected),
            allow_external=True,
            always_recommend=cfg.skills.always_recommend,
            historical_success=_load_skill_success(root),
        )
        all_scores = _strip_skill_bodies(all_scores)
        return RouteExplanation(**result.model_dump(), skill_scores=all_scores)

    def get_skill(self, root: Path, name_or_path: str) -> str:
        needle = name_or_path.strip().lower().replace("\\", "/").rstrip("/")
        if not needle:
            raise ValueError("Skill name or path is required.")
        inventory = self.inventory(root)
        for skill in inventory.skills:
            keys = {
                skill.name.lower(),
                skill.path.lower().replace("\\", "/").rstrip("/"),
                str(Path(skill.path).parent).lower().replace("\\", "/").rstrip("/"),
            }
            if needle in keys:
                if skill.raw_text:
                    return skill.raw_text
                path = Path(skill.path).expanduser()
                if not path.is_absolute():
                    path = root / path
                if path.exists():
                    return path.read_text(encoding="utf-8")
                raise ValueError(f"Skill content not available: {skill.path}")
        raise ValueError(f"Skill not found: {name_or_path}")


def _normalize_task(task: str) -> str:
    normalized = " ".join(task.strip().split())
    if not normalized:
        raise ValueError("Task is required.")
    return normalized


def _selected_file_dict(item) -> dict:
    return {
        "path": item.path,
        "score": item.score,
        "include_mode": item.include_mode,
        "reasons": item.reasons,
    }


def _strip_skill_bodies(items: list[SelectedSkill]) -> list[SelectedSkill]:
    stripped: list[SelectedSkill] = []
    for item in items:
        skill = item.skill.model_copy(update={"raw_text": ""})
        stripped.append(item.model_copy(update={"skill": skill}))
    return stripped


def _split_baseline_skills(items: list[SelectedSkill]) -> tuple[list[SelectedSkill], list[SelectedSkill]]:
    baseline: list[SelectedSkill] = []
    task_specific: list[SelectedSkill] = []
    for item in items:
        if "always-recommend skill" in item.reasons:
            baseline.append(item)
        else:
            task_specific.append(item)
    return baseline, task_specific


def _load_skill_success(root: Path) -> dict[str, float]:
    path = root / ".agentpack" / "skill_feedback.jsonl"
    if not path.exists():
        return {}
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    ignored_totals: dict[str, float] = {}
    ignored_counts: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines[-500:]:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        used = _string_list(record.get("used_skills"))
        ignored = _string_list(record.get("ignored_skills"))
        bad_recommendations = _string_list(record.get("bad_recommendations"))
        context_keys = _feedback_context_keys(record)

        value = _feedback_value(record)
        for skill in used:
            _add_feedback_value(totals, counts, skill, value, context_keys=context_keys)

        used_keys = {_normalize_skill_key(skill) for skill in used}
        for skill in ignored:
            if _normalize_skill_key(skill) in used_keys:
                continue
            _add_feedback_value(
                ignored_totals,
                ignored_counts,
                skill,
                _ignored_feedback_value(record),
                context_keys=context_keys,
                include_global=False,
            )
        for skill in bad_recommendations:
            if _normalize_skill_key(skill) in used_keys:
                continue
            _add_feedback_value(
                totals,
                counts,
                skill,
                -0.6,
                context_keys=context_keys,
                include_global=False,
            )

    for key, count in ignored_counts.items():
        if count < 2:
            continue
        totals[key] = totals.get(key, 0.0) + ignored_totals[key]
        counts[key] = counts.get(key, 0) + count

    history: dict[str, float] = {}
    for key, count in counts.items():
        if count <= 0:
            continue
        value = totals[key] / count
        if value == 0:
            continue
        history[key] = max(-1.0, min(1.0, value))
    return history


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _add_feedback_value(
    totals: dict[str, float],
    counts: dict[str, int],
    skill: str,
    value: float,
    *,
    context_keys: list[str],
    include_global: bool = True,
) -> None:
    key = _normalize_skill_key(skill)
    if not key or value == 0:
        return
    keys = [key] if include_global and value > 0 else []
    keys.extend(f"{key}|{context}" for context in context_keys)
    if not keys and not context_keys:
        keys.append(key)
    for item in keys:
        totals[item] = totals.get(item, 0.0) + value
        counts[item] = counts.get(item, 0) + 1


def _feedback_context_keys(record: dict) -> list[str]:
    task = str(record.get("task") or "")
    changed_files = _string_list(record.get("changed_files"))
    taxonomy = _classify_task(task, changed_files)
    keys: list[str] = []
    for task_type in sorted(taxonomy["task_types"]):
        keys.append(f"task_type:{task_type}")
    for language in sorted(taxonomy["languages"]):
        keys.append(f"language:{language}")
    for framework in sorted(taxonomy["frameworks"]):
        keys.append(f"framework:{framework}")
    for prefix in _path_prefixes(changed_files):
        keys.append(f"path:{prefix}")
    return keys


def _path_prefixes(paths: list[str]) -> list[str]:
    prefixes: list[str] = []
    seen: set[str] = set()
    for path in paths:
        parts = [part for part in path.replace("\\", "/").strip("/").split("/") if part]
        for size in (1, 2):
            if len(parts) < size:
                continue
            prefix = "/".join(parts[:size]).lower()
            if prefix in seen:
                continue
            seen.add(prefix)
            prefixes.append(prefix)
    return prefixes


def _feedback_value(record: dict) -> float:
    feedback = str(record.get("user_feedback") or "").strip().lower()
    tests_passed = record.get("tests_passed")
    value = 0.0
    if tests_passed is True:
        value += 0.6
    elif tests_passed is False:
        value -= 0.4
    if feedback in {"helpful", "good", "used", "success"}:
        value += 0.4
    elif feedback in {"noisy", "ignored", "bad", "unhelpful"}:
        value -= 0.4
    return value


def _ignored_feedback_value(record: dict) -> float:
    feedback = str(record.get("user_feedback") or "").strip().lower()
    if feedback in {"bad", "noisy", "unhelpful"}:
        return -0.25
    return -0.15


def _apply_rules(inventory: SkillInventory, selected_paths: list[str]) -> list[AppliedRule]:
    applied: list[AppliedRule] = []
    for rule in sorted(inventory.rules, key=lambda item: (-item.priority, item.path)):
        reasons = _rule_reasons(rule.scope_paths, selected_paths)
        if reasons:
            applied.append(AppliedRule(rule=rule, reasons=reasons))
    return applied


def _rule_reasons(scope_paths: list[str], selected_paths: list[str]) -> list[str]:
    if not scope_paths:
        return ["repo-level rule"]
    if any(pattern in {"*", "**", "**/*"} for pattern in scope_paths):
        return ["always apply rule"]
    matched = [
        pattern for pattern in scope_paths
        if any(_path_matches(path, pattern) for path in selected_paths)
    ]
    return [f"matched scope: {', '.join(matched[:3])}"] if matched else []


def _suggest_commands(task: str, selected_paths: list[str]) -> list[CommandSuggestion]:
    lower = task.lower()
    test_paths = [
        path for path in selected_paths
        if path.startswith("tests/") or "/tests/" in path or path.endswith("_test.py") or path.endswith("_spec.py")
    ]
    has_test_intent = any(term in lower for term in _TEST_TERMS)
    if not has_test_intent and not test_paths:
        return []

    target = " ".join(test_paths[:3]) if test_paths else ""
    pytest_target = f" {target}" if target else ""
    commands = [
        CommandSuggestion(
            command=f"pytest{pytest_target} -q",
            reason="task or selected files indicate test work",
            source="agentpack-router",
        )
    ]
    if any(term in lower for term in ("flaky", "debug", "fail", "failing")):
        commands.append(CommandSuggestion(
            command=f"pytest{pytest_target} --maxfail=1 -vv",
            reason="task indicates failing/flaky test debugging",
            source="agentpack-router",
        ))
    return commands


def _path_matches(path: str, pattern: str) -> bool:
    normalized_path = path.strip("/")
    normalized_pattern = pattern.strip("/")
    return (
        fnmatch.fnmatch(normalized_path, normalized_pattern)
        or fnmatch.fnmatch(normalized_path, f"{normalized_pattern}/**")
        or normalized_path.startswith(normalized_pattern.rstrip("/") + "/")
    )
