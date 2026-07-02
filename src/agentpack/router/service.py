from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentpack.core import git
from agentpack.adapters.detect import detect_agent
from agentpack.application.pack_service import PackPlanner, PackRequest
from agentpack.core.config import load_config
from agentpack.session.events import read_events
from agentpack.session.references import extract_issue_references, merge_issue_references
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
from agentpack.router.scoring import score_skills

_TEST_TERMS = ("test", "tests", "pytest", "flaky", "fixture", "mock", "failing", "fail", "debug")
_NOISY_PATH_PREFIXES = (".agentpack/", ".agent/", ".codex/", ".cursor/", ".vscode/", ".github/workflows/")
_NOISY_PATHS = {".gitignore", ".agentignore", "AGENTS.md", "CLAUDE.md", "GEMINI.md"}


@dataclass(frozen=True)
class TaskModeDecision:
    mode: str
    confidence: float
    signals: list[str]


@dataclass(frozen=True)
class PromptQualityDecision:
    recommended_interaction_mode: str
    mode_reason: str
    warnings: list[str]
    template: list[str]


class RouteService:
    def inventory(self, root: Path, *, use_index: bool = True) -> SkillInventory:
        cfg = load_config(root)
        paths = cfg.skills.paths
        if use_index:
            return inventory_for_route(root, paths)
        return discover_inventory(root, paths)

    def route_task(self, root: Path, task: str) -> RouteResult:
        task = _normalize_task(task)
        routing_task = _task_with_recent_issue_context(root, task)
        mode_decision = classify_task_mode(task)
        task_mode = mode_decision.mode
        prompt_quality = assess_prompt_quality(task, task_mode)
        current_agent = detect_agent(root)
        reviewer_agent = _recommended_reviewer_agent(current_agent)
        cfg = load_config(root)
        plan = PackPlanner().plan(PackRequest(
            root=root,
            agent="generic",
            task=routing_task,
            mode="balanced",
            budget=0,
            since=None,
            refresh=False,
            task_source="route",
        ))
        pr_paths = _github_pr_paths(root, task) if task_mode == "pr_review" else set()
        selected_files = _route_selected_files(
            root,
            task_mode,
            task,
            [_selected_file_dict(item) for item in plan.selected],
            plan.all_changed,
            pr_paths,
        )
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
        checklist = _evidence_checklist(task_mode)
        notes = _routing_notes(task_mode, pr_paths)
        if routing_task != task:
            notes.append("Used recent issue references from AgentPack memory as routing hints.")

        result = RouteResult(
            task=task,
            recommended_interaction_mode=prompt_quality.recommended_interaction_mode,
            mode_reason=prompt_quality.mode_reason,
            current_agent=current_agent,
            reviewer_agent=reviewer_agent,
            task_mode=task_mode,
            task_mode_confidence=mode_decision.confidence,
            task_mode_signals=mode_decision.signals,
            selected_files=selected_files,
            selected_skills=selected_skills,
            baseline_skills=baseline_skills,
            applied_rules=applied_rules,
            suggested_commands=commands,
            evidence_checklist=checklist,
            routing_notes=notes,
            prompt_quality_warnings=prompt_quality.warnings,
            recommended_prompt_template=prompt_quality.template,
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


def _task_with_recent_issue_context(root: Path, task: str) -> str:
    if extract_issue_references(task):
        return task
    cfg = load_config(root)
    events = read_events(root, output_path=cfg.runtime.session_events_output, limit=50)
    refs = merge_issue_references(
        ref
        for event in events
        for ref in (event.get("issue_references") or [])
        if isinstance(ref, str)
    )
    if not refs:
        return task
    return f"{task} issue references {' '.join(refs[-5:])}"


def _selected_file_dict(item) -> dict:
    return {
        "path": item.path,
        "score": item.score,
        "include_mode": item.include_mode,
        "reasons": item.reasons,
    }


def detect_task_mode(task: str) -> str:
    return classify_task_mode(task).mode


def classify_task_mode(task: str) -> TaskModeDecision:
    lower = task.lower()
    signals: list[str] = []
    if _looks_agentpack_tooling_task(lower, signals):
        return TaskModeDecision("integration_readiness", _confidence(signals), signals)
    if _has_any(lower, ("pr ", "pull request", "review", "diff", "review comment"), signals, "pr-review"):
        return TaskModeDecision("pr_review", _confidence(signals), signals)
    if _has_any(lower, ("log", "cloudwatch", "queue", "sqs", "db row", "postgres", "dashboard", "customer.io", "event pipeline", "runtime", "prod", "production"), signals, "runtime"):
        return TaskModeDecision("runtime_debugging", _confidence(signals), signals)
    if _has_any(lower, ("mcp", "doctor", "readiness", "integration", "install", "tool exposure", "available tools"), signals, "integration-readiness"):
        return TaskModeDecision("integration_readiness", _confidence(signals), signals)
    if _looks_small_direct(lower):
        signals.append("small/direct wording")
        return TaskModeDecision("small_direct_edit", _confidence(signals), signals)
    if _has_any(lower, ("release", "changelog", "benchmark", "publish"), signals, "release-docs"):
        return TaskModeDecision("release_docs", _confidence(signals), signals)
    return TaskModeDecision("broad_feature", 0.35, ["fallback"])


def assess_prompt_quality(task: str, task_mode: str) -> PromptQualityDecision:
    lower = task.lower()
    warnings: list[str] = []
    words = _task_words(task)
    has_file_context = _has_file_context(task)
    has_output_constraint = _has_output_constraint(lower)
    has_spec_signal = _has_spec_signal(lower)
    simple_question = _is_simple_question(task, words)

    if not has_file_context and task_mode not in {"pr_review", "runtime_debugging", "integration_readiness"}:
        warnings.append("No file context detected. Add `#file` references or name the target files before using agent mode.")
    if _looks_vague_or_repeated(lower, words):
        warnings.append("Prompt is very short or retry-shaped. Rephrase with concrete files, observed behavior, and acceptance criteria instead of repeating.")
    if len(words) <= 35 and not has_output_constraint:
        warnings.append("Short prompt has no output constraint. Ask for concise bullets, no commentary, or a max length to avoid verbose output.")
    if not has_spec_signal and task_mode in {"broad_feature", "runtime_debugging", "integration_readiness", "release_docs"}:
        warnings.append("No spec or acceptance criteria detected. Add requirements, constraints, and validation before coding.")
    if simple_question:
        warnings.append("Simple question shape detected. Prefer Ask/Chat mode unless files, commands, or edits are required.")
    if _has_frustration_signal(task):
        warnings.append("Frustration signal detected. Pause retries and switch to a smaller diagnostic prompt.")
    if _has_session_drift_signal(lower):
        warnings.append("Multiple task types detected. Start a fresh focused session when switching between review, debug, release, docs, and feature work.")

    mode = "ask" if simple_question and not has_file_context else "agent"
    if mode == "ask":
        mode_reason = "short explanatory prompt; no repo file context, command execution, or code edits detected"
    elif has_file_context:
        mode_reason = "repo-specific context detected; use agent mode for file inspection, commands, or edits"
    elif task_mode in {"pr_review", "runtime_debugging", "integration_readiness", "release_docs"}:
        mode_reason = "task requires environment evidence or multi-step coordination better suited to agent mode"
    else:
        mode_reason = "task looks like actionable repo work rather than a simple explanatory question"
    return PromptQualityDecision(
        recommended_interaction_mode=mode,
        mode_reason=mode_reason,
        warnings=warnings,
        template=_prompt_template() if warnings else [],
    )

def _recommended_reviewer_agent(current_agent: str) -> str:
    if current_agent == "codex":
        return "claude"
    if current_agent == "claude":
        return "codex"
    return "codex"


def _task_words(task: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_#./:-]+", task)


def _has_file_context(task: str) -> bool:
    if "#file" in task.lower():
        return True
    return bool(re.search(r"(?:^|\s)[\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|md|json|toml|ya?ml|css|scss)(?=\s|$|[:,])", task))


def _has_output_constraint(lower: str) -> bool:
    return any(term in lower for term in ("concise", "one-line", "one line", "no commentary", "brief", "max ", "bullets", "json only", "short"))


def _has_spec_signal(lower: str) -> bool:
    return any(term in lower for term in ("acceptance", "criteria", "requirements", "constraints", "validation", "must", "ensure", "spec", "plan:"))


def _is_simple_question(task: str, words: list[str]) -> bool:
    lower = task.lower().strip()
    if len(words) > 14:
        return False
    if any(term in lower for term in ("fix", "edit", "change", "implement", "add ", "remove", "update", "release", "run ")):
        return False
    return lower.endswith("?") or lower.startswith(("what ", "why ", "how ", "can ", "should ", "is ", "does ", "do "))


def _looks_vague_or_repeated(lower: str, words: list[str]) -> bool:
    if len(words) <= 5:
        return True
    return lower.strip() in {
        "fix this",
        "try again",
        "do it again",
        "not working",
        "can you fix this",
        "can you fix these gaps",
    }


def _has_frustration_signal(task: str) -> bool:
    letters = [char for char in task if char.isalpha()]
    uppercase_ratio = sum(1 for char in letters if char.isupper()) / len(letters) if letters else 0
    return "!!!" in task or "???" in task or (len(letters) >= 10 and uppercase_ratio >= 0.65)


def _has_session_drift_signal(lower: str) -> bool:
    task_types = sum(
        1
        for terms in (
            ("review", "pr "),
            ("debug", "log", "runtime"),
            ("release", "publish", "changelog"),
            ("docs", "readme"),
            ("feature", "implement", "build"),
        )
        if any(term in lower for term in terms)
    )
    return task_types >= 3


def _looks_agentpack_tooling_task(lower: str, signals: list[str]) -> bool:
    tooling_terms = (
        "mcp",
        "validate_toon",
        "toon",
        "canonical",
        "route",
        "routing",
        "current agent",
        "active agent",
        "active-agent",
        "dry-run-post",
        "dry run post",
        "e2e safety",
        "post-inline-comments",
        "inline pr commenting",
        "review schema",
        "schema validation",
        "agentpack-review",
    )
    if not any(term in lower for term in tooling_terms):
        return False
    if not any(term in lower for term in ("agentpack", "tool", "mcp", "route", "routing", "schema", "canonical", "gap")):
        return False
    matched = [term for term in tooling_terms if term in lower]
    signals.extend(f"integration/tooling: {term}" for term in matched[:4])
    return True


def _prompt_template() -> list[str]:
    return [
        "Task: <what to change or answer>",
        "Files: #file:<path> #file:<path> (or name the target files)",
        "Acceptance criteria: <bullets>",
        "Constraints: <scope, style, risk limits>",
        "Validation: <tests/checks to run>",
        "Output: concise bullets; no extra commentary",
    ]


def _has_any(lower: str, terms: tuple[str, ...], signals: list[str], label: str) -> bool:
    matched = [term.strip() for term in terms if term in lower]
    if matched:
        signals.extend(f"{label}: {term}" for term in matched[:4])
        return True
    return False


def _confidence(signals: list[str]) -> float:
    if len(signals) >= 3:
        return 0.92
    if len(signals) == 2:
        return 0.82
    if signals:
        return 0.68
    return 0.35


def _looks_small_direct(lower: str) -> bool:
    if any(term in lower for term in ("small", "quick", "typo", "copy", "css", "style", "button", "label", "frontend", "docs")):
        return True
    words = [word for word in lower.replace("/", " ").replace(".", " ").split() if word]
    return len(words) <= 8 and any(term in lower for term in ("fix", "update", "rename", "remove", "add"))


def _route_selected_files(
    root: Path,
    task_mode: str,
    task: str,
    selected_files: list[dict],
    changed_paths: set[str],
    pr_paths: set[str] | None = None,
) -> list[dict]:
    pr_paths = pr_paths or set()
    diff_paths = _diff_paths(root) if task_mode == "pr_review" else set()
    priority_paths = changed_paths | diff_paths | pr_paths
    existing = {item["path"] for item in selected_files}
    for path in _task_seed_paths(root, task_mode, task):
        if path not in existing:
            selected_files.append(
                {
                    "path": path,
                    "score": 950.0,
                    "include_mode": "summary",
                    "reasons": ["task-specific route seed"],
                }
            )
            existing.add(path)
    for item in selected_files:
        if item["path"] in pr_paths:
            item["score"] = max(float(item.get("score") or 0), 1000.0)
            reasons = list(item.get("reasons") or [])
            if "GitHub PR file" not in reasons:
                item["reasons"] = ["GitHub PR file", *reasons]
    for path in sorted(pr_paths):
        if path not in existing and (root / path).exists() and _keep_route_path(task_mode, task, path, priority_paths):
            selected_files.append(
                {
                    "path": path,
                    "score": 1000.0,
                    "include_mode": "summary",
                    "reasons": ["GitHub PR file"],
                }
            )
            existing.add(path)
    filtered = [
        item for item in selected_files
        if _keep_route_path(task_mode, task, item["path"], priority_paths, item.get("reasons") or [])
    ]
    if not filtered:
        filtered = selected_files
    if task_mode == "pr_review":
        filtered = sorted(
            filtered,
            key=lambda item: (
                item["path"] in priority_paths,
                _is_source_or_test(item["path"]),
                item.get("score", 0),
            ),
            reverse=True,
        )
    elif task_mode == "small_direct_edit":
        filtered = sorted(
            filtered,
            key=lambda item: (
                _task_mentions_path(task, item["path"]),
                item["path"] in changed_paths,
                not _is_noisy_path(item["path"]),
                item.get("score", 0),
            ),
            reverse=True,
        )
    elif task_mode in {"integration_readiness", "runtime_debugging", "release_docs", "broad_feature"}:
        filtered = sorted(
            filtered,
            key=lambda item: _route_priority(task_mode, task, item, priority_paths),
            reverse=True,
        )
    return filtered[:20]


def _task_seed_paths(root: Path, task_mode: str, task: str) -> list[str]:
    if task_mode not in {"integration_readiness", "small_direct_edit", "broad_feature"}:
        return []
    lower = task.lower()
    candidates: list[str] = []
    if any(term in lower for term in ("route", "routing", "selected file", "noisy", "irrelevant file")):
        candidates.extend(("src/agentpack/router/service.py", "src/agentpack/router/prompt_builder.py", "tests/test_route_command.py", "tests/test_mcp_router.py"))
    if any(term in lower for term in ("mcp", "readiness", "tool exposure", "validate_toon")):
        candidates.extend(("src/agentpack/mcp_server.py", "tests/test_mcp_server.py"))
    if any(term in lower for term in ("toon", "canonical", "validate_toon", "schema", "review schema")):
        candidates.extend(("src/agentpack/core/toon_validator.py", "src/agentpack/commands/toon_validate.py", "tests/test_toon_validator.py"))
    if any(term in lower for term in ("inline", "post-inline", "dry-run", "dry run", "github e2e", "review payload")):
        candidates.extend(("src/agentpack/commands/review_cmd.py", "tests/test_review_cmd.py"))
    if any(term in lower for term in ("guard", "stale", "freshness", "refresh")):
        candidates.extend(("src/agentpack/commands/guard.py", "src/agentpack/application/pack_service.py"))
    if any(term in lower for term in ("agent identity", "current agent", "active agent", "active-agent", "detect", "codex", "antigravity")):
        candidates.extend(("src/agentpack/adapters/detect.py", "tests/test_route_command.py"))
    seen: set[str] = set()
    seeded: list[str] = []
    for path in candidates:
        if path not in seen and (root / path).exists():
            seeded.append(path)
            seen.add(path)
    return seeded


def _route_priority(task_mode: str, task: str, item: dict, priority_paths: set[str]) -> tuple[float, float]:
    path = str(item.get("path") or "")
    score = float(item.get("score") or 0)
    reasons = item.get("reasons") or []
    task_hit = 1.0 if _task_mentions_path(task, path) else 0.0
    seed = 1.0 if "task-specific route seed" in reasons else 0.0
    changed = 1.0 if path in priority_paths else 0.0
    noisy = 1.0 if _is_noisy_path(path) else 0.0
    weak_cli = 1.0 if _is_weak_cli_route(task_mode, task, path, reasons) else 0.0
    direct_term = _direct_term_overlap(task, path)
    return (
        task_hit * 1000.0
        + seed * 900.0
        + direct_term * 120.0
        + changed * 60.0
        - noisy * 250.0
        - weak_cli * 220.0,
        score,
    )


def _is_weak_cli_route(task_mode: str, task: str, path: str, reasons: list[str]) -> bool:
    if task_mode not in {"integration_readiness", "broad_feature", "small_direct_edit"}:
        return False
    if not path.startswith("src/agentpack/commands/") or _task_mentions_path(task, path):
        return False
    command_name = Path(path).stem.replace("_cmd", "").replace("_", "-")
    lower = task.lower()
    if command_name and command_name in lower:
        return False
    route_reasons = " ".join(str(reason) for reason in reasons).lower()
    return "matched entrypoint: cli command" in route_reasons or "matched role keyword: cli command" in route_reasons


def _direct_term_overlap(task: str, path: str) -> float:
    task_terms = {term for term in re.findall(r"[a-z0-9]+", task.lower()) if len(term) >= 3}
    path_terms = set(re.findall(r"[a-z0-9]+", path.lower().replace("_", " ").replace("-", " ")))
    if "routing" in task_terms:
        task_terms.add("router")
    if "stale" in task_terms:
        task_terms.add("freshness")
    if "identity" in task_terms:
        task_terms.add("detect")
    if "agent" in task_terms and ({"active", "current"} & task_terms):
        task_terms.add("detect")
    return float(len(task_terms & path_terms))


def _is_noisy_path(path: str) -> bool:
    return path in _NOISY_PATHS or any(path.startswith(prefix) for prefix in _NOISY_PATH_PREFIXES)


def _keep_route_path(task_mode: str, task: str, path: str, priority_paths: set[str], reasons: list[str] | None = None) -> bool:
    if not _is_noisy_path(path) or _task_mentions_path(task, path):
        if _is_pr_review_secret_fixture_noise(task_mode, task, path, priority_paths, reasons or []):
            return False
        return True
    if task_mode == "pr_review" and path in priority_paths and _is_review_diff_exception(path):
        return True
    return False


def _is_review_diff_exception(path: str) -> bool:
    return path.startswith(".github/workflows/")


def _is_pr_review_secret_fixture_noise(
    task_mode: str,
    task: str,
    path: str,
    priority_paths: set[str],
    reasons: list[str],
) -> bool:
    if task_mode != "pr_review" or path in priority_paths:
        return False
    if _task_mentions_path(task, path):
        return False
    lower = task.lower()
    if any(term in lower for term in ("redaction", "redactor", "secret scan", "credential leak")):
        return False
    has_secret_reason = any(reason.startswith("secret redaction candidate") for reason in reasons)
    if not has_secret_reason:
        return False
    return path.startswith("tests/") or "/fixtures/" in path or path.startswith("fixtures/")


def _task_mentions_path(task: str, path: str) -> bool:
    lower = task.lower()
    path_lc = path.lower()
    return path_lc in lower or Path(path_lc).name in lower


def _is_source_or_test(path: str) -> bool:
    return (
        path.startswith(("src/", "lib/", "app/", "backend/", "frontend/", "tests/"))
        or "/tests/" in path
        or path.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb", ".java"))
    )


def _diff_paths(root: Path) -> set[str]:
    if not git.is_git_repo(root):
        return set()
    return git.changed_files(root)


def _github_pr_paths(root: Path, task: str) -> set[str]:
    if shutil.which("gh") is None:
        return set()
    pr_number = _pr_number(task)
    cmd = ["gh", "pr", "view"]
    if pr_number:
        cmd.append(pr_number)
    cmd += ["--json", "files", "--jq", ".files[].path"]
    try:
        result = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _pr_number(task: str) -> str | None:
    match = re.search(r"(?:pr|pull request)\s*#?\s*(\d+)", task, re.IGNORECASE)
    return match.group(1) if match else None


def _routing_notes(task_mode: str, pr_paths: set[str] | None = None) -> list[str]:
    if task_mode == "small_direct_edit":
        return [
            "Small/direct task: prefer targeted `rg`, target-file inspection, and focused validation over full context packing.",
            "Use AgentPack as orientation only unless target files are unclear.",
        ]
    if task_mode == "pr_review":
        source = "GitHub PR files, local changed files, and diff files" if pr_paths else "local changed files and diff files"
        return [f"PR/review task: {source} outrank generic config or generated metadata."]
    if task_mode in {"runtime_debugging", "integration_readiness"}:
        return [
            "Repo context is only a map; verify live/runtime/tool state before concluding.",
            "Do not route by repo ranking alone; inspect exact logs, payloads, tool exposure, or runtime state first.",
        ]
    if task_mode == "release_docs":
        return ["Release/docs task: keep claims tied to existing benchmark scope; avoid outcome claims without E2E evidence."]
    return ["Broad feature task: use ranked files as starting map, then verify data flow in code."]


def _evidence_checklist(task_mode: str) -> list[str]:
    if task_mode in {"runtime_debugging", "integration_readiness"}:
        return [
            "inspect runtime/tool evidence for the exact failing session",
            "identify source flow and ownership boundary",
            "inspect mapper, sink, processor, or adapter path",
            "run targeted tests or replay checks",
            "verify external system state after the fix",
        ]
    if task_mode == "pr_review":
        return [
            "inspect PR diff and changed files first",
            "check source flow touched by the diff",
            "run or identify targeted validation",
            "separate blocking issues from suggestions",
        ]
    if task_mode == "small_direct_edit":
        return [
            "inspect named target file or nearest match with `rg`",
            "make the smallest edit",
            "run focused lint/test/build check if available",
        ]
    return []


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
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for record in _read_skill_feedback(path):
        _accumulate_skill_feedback(record, totals, counts)
    for event in read_events(root, limit=500):
        if event.get("type") not in {"task_outcome", "review", "eval"}:
            continue
        _accumulate_skill_feedback(event, totals, counts)
    return {
        key: max(0.0, min(1.0, totals[key] / counts[key]))
        for key in totals
        if counts[key] > 0 and totals[key] > 0
    }


def _read_skill_feedback(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict] = []
    for line in lines[-500:]:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _accumulate_skill_feedback(record: dict, totals: dict[str, float], counts: dict[str, int]) -> None:
    used = record.get("used_skills") or record.get("skills") or record.get("selected_skills") or []
    if not isinstance(used, list):
        return
    helpful = _feedback_value(record)
    if helpful == 0:
        return
    for skill in used:
        key = str(skill).strip().lower().replace("\\", "/").rstrip("/")
        if not key:
            continue
        totals[key] = totals.get(key, 0.0) + helpful
        counts[key] = counts.get(key, 0) + 1


def _feedback_value(record: dict) -> float:
    feedback = str(record.get("user_feedback") or "").strip().lower()
    tests_passed = record.get("tests_passed")
    outcome = str(record.get("outcome") or "").strip().lower()
    checks = record.get("checks") or []
    value = 0.0
    if tests_passed is True:
        value += 0.6
    elif tests_passed is False:
        value -= 0.4
    if outcome in {"passed", "pass", "success", "succeeded"}:
        value += 0.6
    elif outcome in {"failed", "fail", "blocked"}:
        value -= 0.4
    if isinstance(checks, list) and checks:
        passed = sum(1 for item in checks if isinstance(item, dict) and item.get("passed") is True)
        failed = sum(1 for item in checks if isinstance(item, dict) and item.get("passed") is False)
        if passed and not failed:
            value += 0.3
        elif failed:
            value -= 0.2
    if feedback in {"helpful", "good", "used", "success"}:
        value += 0.4
    elif feedback in {"noisy", "ignored", "bad", "unhelpful"}:
        value -= 0.4
    return value


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
