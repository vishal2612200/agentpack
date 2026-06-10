from __future__ import annotations

import fnmatch
import math
import re

from agentpack.router.models import SelectedSkill, SkillArtifact

_TEST_TERMS = {"test", "tests", "pytest", "flaky", "fixture", "mock", "failing", "fail"}
_CODING_INTENT_TERMS = {
    "add", "bug", "build", "change", "code", "debug", "fix", "implement", "patch",
    "refactor", "review", "test", "update",
}
_GENERAL_CODING_SKILL_TERMS = {
    "assumption", "assumptions", "coding", "guideline", "guidelines", "mistake",
    "mistakes", "overcomplication", "refactoring", "reviewing", "success",
    "surgical", "verification", "verifiable", "writing",
}
_STOPWORDS = {
    "and", "are", "but", "for", "from", "into", "the", "this", "that", "then",
    "with", "your", "fix", "add", "make", "debug",
}
_TASK_TYPE_TERMS = {
    "bugfix": {"bug", "broken", "error", "exception", "fail", "failing", "fix", "issue", "regression"},
    "test": _TEST_TERMS,
    "debug": {"debug", "diagnose", "trace", "investigate", "repro", "root-cause"},
    "docs": {"doc", "docs", "documentation", "readme"},
    "refactor": {"cleanup", "refactor", "restructure", "simplify"},
    "feature": {"add", "build", "create", "feature", "implement"},
    "release": {"publish", "release", "version", "changelog"},
    "security": {"auth", "csrf", "secret", "security", "token", "vulnerability"},
    "ui": {"css", "frontend", "react", "ui", "ux"},
    "infra": {"ci", "config", "docker", "infra", "kubernetes"},
}
_LANGUAGE_EXTENSIONS = {
    "python": (".py", ".pyi"),
    "javascript": (".js", ".jsx"),
    "typescript": (".ts", ".tsx"),
    "go": (".go",),
    "rust": (".rs",),
    "ruby": (".rb",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "swift": (".swift",),
}
_FRAMEWORK_TERMS = {
    "django", "fastapi", "flask", "pytest", "react", "next", "nextjs", "vue",
    "svelte", "express", "rails", "docker", "kubernetes", "temporal",
}


def score_skills(
    skills: list[SkillArtifact],
    *,
    task: str,
    selected_paths: list[str],
    selected_files: list[dict] | None = None,
    max_selected: int,
    allow_external: bool,
    always_recommend: list[str] | None = None,
    historical_success: dict[str, float] | None = None,
) -> tuple[list[SelectedSkill], list[str], list[SelectedSkill]]:
    pinned = _always_recommend_keys(always_recommend or [])
    file_weights = _selected_file_weights(selected_files, selected_paths)
    taxonomy = _classify_task(task, selected_paths)
    history = historical_success or {}
    scored = [
        _score_skill(
            skill,
            task=task,
            selected_paths=selected_paths,
            selected_file_weights=file_weights,
            taxonomy=taxonomy,
            always_recommend=pinned,
            historical_success=history,
        )
        for skill in skills
    ]
    scored = [
        item for item in scored
        if item.score > 0
        or item.confidence >= item.skill.confidence_threshold
        or (item.skill.side_effect_level == "external" and item.reasons)
    ]
    scored.sort(key=lambda item: (-item.score, item.skill.name.lower()))

    warnings: list[str] = []
    omitted_external_warnings = 0
    selected: list[SelectedSkill] = []
    pool = list(scored)
    while pool and len(selected) < max_selected:
        item = max(pool, key=lambda candidate: (_diverse_score(candidate, selected), candidate.score))
        pool.remove(item)
        if item.skill.side_effect_level == "external" and not allow_external:
            if len(warnings) < 10:
                warnings.append(
                    f"External side-effect skill not auto-selected: {item.skill.name} ({item.skill.path})"
                )
            else:
                omitted_external_warnings += 1
            continue
        if item.confidence < item.skill.confidence_threshold:
            continue
        selected.append(item)
    if omitted_external_warnings:
        warnings.append(f"{omitted_external_warnings} more external side-effect skills not shown.")
    return selected, warnings, scored


def _score_skill(
    skill: SkillArtifact,
    *,
    task: str,
    selected_paths: list[str],
    selected_file_weights: dict[str, float],
    taxonomy: dict[str, set[str]],
    always_recommend: set[str],
    historical_success: dict[str, float],
) -> SelectedSkill:
    task_terms = _terms(task)
    skill_terms = (
        set(skill.triggers)
        | _terms(skill.name)
        | _terms(skill.description)
        | set(skill.task_types)
        | set(skill.languages)
        | set(skill.frameworks)
    )
    anti_terms = set(skill.anti_triggers)
    selected_path_terms = set().union(*(_terms(path) for path in selected_paths)) if selected_paths else set()
    has_coding_intent = _has_coding_intent(task, selected_paths)

    reasons: list[str] = []
    score = 0.0
    matched_signal = False

    task_type_matches = sorted(set(skill.task_types) & taxonomy["task_types"])
    if task_type_matches:
        score += 35
        matched_signal = True
        reasons.append(f"task type match: {', '.join(task_type_matches[:4])}")

    framework_matches = sorted(set(skill.frameworks) & taxonomy["frameworks"])
    if framework_matches:
        score += 30
        matched_signal = True
        reasons.append(f"framework match: {', '.join(framework_matches[:4])}")

    language_matches = sorted(set(skill.languages) & taxonomy["languages"])
    if language_matches:
        score += 25
        matched_signal = True
        reasons.append(f"language match: {', '.join(language_matches[:4])}")

    keyword_matches = sorted(task_terms & skill_terms)
    if keyword_matches:
        score += min(len(keyword_matches), 8) * 10
        matched_signal = True
        reasons.append(f"task keyword match: {', '.join(keyword_matches[:6])}")

    path_term_matches = sorted(selected_path_terms & skill_terms)
    if path_term_matches:
        score += min(len(path_term_matches), 6) * 6
        matched_signal = True
        reasons.append(f"selected path term match: {', '.join(path_term_matches[:5])}")

    matched_globs, path_strength = _weighted_path_matches(skill.applies_to_paths, selected_file_weights)
    if matched_globs:
        score += min(30.0, 12.0 + (18.0 * path_strength))
        matched_signal = True
        reasons.append(f"path hint match: {', '.join(matched_globs[:3])}")

    tool_matches = sorted(set(skill.tools_required) & (task_terms | selected_path_terms | _tool_terms(task)))
    if tool_matches:
        score += len(tool_matches) * 8
        matched_signal = True
        reasons.append(f"tool match: {', '.join(tool_matches)}")

    if task_terms & _TEST_TERMS and ("pytest" in skill_terms or "test" in skill_terms):
        score += 18
        matched_signal = True
        reasons.append("test task match")

    if _is_general_coding_skill(skill, skill_terms) and has_coding_intent:
        score += 22
        matched_signal = True
        reasons.append("general coding guidance match")

    if _is_always_recommended(skill, always_recommend) and has_coding_intent:
        score += 36
        matched_signal = True
        reasons.append("always-recommend skill")
    elif (
        skill.side_effect_level == "external"
        and _matches_always_recommend_key(skill, always_recommend)
        and has_coding_intent
    ):
        matched_signal = True
        reasons.append("external always-recommend skill blocked")

    if skill.side_effect_level == "none":
        score += 4
        reasons.append("safe read-only skill")
    elif skill.side_effect_level == "command":
        score -= 4
        reasons.append("command side-effect risk")
    elif skill.side_effect_level == "external":
        score -= 10
        reasons.append("external side-effect penalty")

    anti_trigger_matches = sorted((task_terms | selected_path_terms) & anti_terms)
    if anti_trigger_matches:
        score -= 35
        reasons.append(f"anti-trigger match: {', '.join(anti_trigger_matches[:4])}")

    anti_path_matches, _anti_strength = _weighted_path_matches(skill.anti_paths, selected_file_weights)
    if anti_path_matches:
        score -= 30
        reasons.append(f"anti-path match: {', '.join(anti_path_matches[:3])}")

    if _is_general_coding_skill(skill, skill_terms) and not _is_always_recommended(skill, always_recommend):
        score -= 10
        reasons.append("generic skill penalty")

    if not matched_signal:
        return SelectedSkill(skill=skill, score=0.0, confidence=0.0, reasons=[])

    history_signal = _historical_success_for(skill, historical_success, taxonomy, selected_paths)
    if history_signal > 0:
        score += min(history_signal, 1.0) * 15
        reasons.append(f"historical success boost: {history_signal:.2f}")
    elif history_signal < 0:
        score -= min(abs(history_signal), 1.0) * 20
        reasons.append(f"historical noise penalty: {abs(history_signal):.2f}")

    score += max(0, min(skill.priority, 100)) / 10
    confidence = _confidence(score)
    return SelectedSkill(skill=skill, score=score, confidence=confidence, reasons=reasons)


def _terms(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.replace("_", "-"))
        if token.lower() not in _STOPWORDS
    }


def _tool_terms(task: str) -> set[str]:
    terms = _terms(task)
    if terms & _TEST_TERMS:
        terms.add("pytest")
    return terms


def _classify_task(task: str, selected_paths: list[str]) -> dict[str, set[str]]:
    terms = (
        _raw_terms(task) | set().union(*(_raw_terms(path) for path in selected_paths))
        if selected_paths
        else _raw_terms(task)
    )
    task_types = {
        task_type for task_type, needles in _TASK_TYPE_TERMS.items()
        if terms & needles
    }
    languages = {
        language for language, suffixes in _LANGUAGE_EXTENSIONS.items()
        if any(path.endswith(suffixes) for path in selected_paths)
    }
    frameworks = terms & _FRAMEWORK_TERMS
    if "nextjs" in frameworks:
        frameworks.add("next")
    return {"task_types": task_types, "languages": languages, "frameworks": frameworks}


def _selected_file_weights(selected_files: list[dict] | None, selected_paths: list[str]) -> dict[str, float]:
    if selected_files:
        raw: dict[str, float] = {}
        for item in selected_files:
            path = str(item.get("path", "")).strip()
            if not path:
                continue
            try:
                raw[path] = max(0.0, float(item.get("score", 0.0)))
            except (TypeError, ValueError):
                raw[path] = 0.0
        max_score = max(raw.values(), default=0.0)
        if max_score > 0:
            return {path: score / max_score for path, score in raw.items()}
    count = len(selected_paths)
    return {
        path: 1.0 - (idx / max(count, 1) * 0.5)
        for idx, path in enumerate(selected_paths)
    }


def _weighted_path_matches(patterns: list[str], selected_file_weights: dict[str, float]) -> tuple[list[str], float]:
    matched: list[str] = []
    strength = 0.0
    for pattern in patterns:
        pattern_strength = sum(
            weight for path, weight in selected_file_weights.items()
            if _path_matches(path, pattern)
        )
        if pattern_strength <= 0:
            continue
        matched.append(pattern)
        strength += pattern_strength
    return matched, min(strength, 1.0)


def _diverse_score(candidate: SelectedSkill, selected: list[SelectedSkill]) -> float:
    if not selected:
        return candidate.score
    similarity = max(_skill_similarity(candidate.skill, item.skill) for item in selected)
    return candidate.score - (70.0 * similarity)


def _skill_similarity(left: SkillArtifact, right: SkillArtifact) -> float:
    left_terms = _skill_profile_terms(left)
    right_terms = _skill_profile_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    overlap = len(left_terms & right_terms)
    union = len(left_terms | right_terms)
    return overlap / union if union else 0.0


def _skill_profile_terms(skill: SkillArtifact) -> set[str]:
    return (
        set(skill.task_types)
        | set(skill.languages)
        | set(skill.frameworks)
        | set(skill.tools_required)
        | {_normalize_skill_key(skill.source)}
        | {_normalize_skill_key(path.split("/", 1)[0]) for path in skill.applies_to_paths}
    )


def _historical_success_for(
    skill: SkillArtifact,
    historical_success: dict[str, float],
    taxonomy: dict[str, set[str]],
    selected_paths: list[str],
) -> float:
    values = [
        historical_success[key]
        for key in _historical_lookup_keys(skill, taxonomy, selected_paths)
        if key in historical_success
    ]
    if not values:
        return 0.0
    positive = max((value for value in values if value > 0), default=0.0)
    negative = min((value for value in values if value < 0), default=0.0)
    return negative if abs(negative) > positive else positive


def _historical_lookup_keys(
    skill: SkillArtifact,
    taxonomy: dict[str, set[str]],
    selected_paths: list[str],
) -> list[str]:
    base_keys = [
        _normalize_skill_key(skill.name),
        _normalize_skill_key(skill.path),
        _normalize_skill_key(skill.path.rsplit("/", 1)[0]),
    ]
    contexts: list[str] = []
    for task_type in sorted(taxonomy["task_types"]):
        contexts.append(f"task_type:{task_type}")
    for language in sorted(taxonomy["languages"]):
        contexts.append(f"language:{language}")
    for framework in sorted(taxonomy["frameworks"]):
        contexts.append(f"framework:{framework}")
    for prefix in _selected_path_prefixes(selected_paths):
        contexts.append(f"path:{prefix}")

    keys: list[str] = []
    seen: set[str] = set()
    for base in base_keys:
        for key in [base, *(f"{base}|{context}" for context in contexts)]:
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


def _selected_path_prefixes(paths: list[str]) -> list[str]:
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


def _confidence(score: float) -> float:
    return 1 / (1 + math.exp(-((score - 45) / 15)))


def _is_general_coding_skill(skill: SkillArtifact, skill_terms: set[str]) -> bool:
    if skill.name.lower() in {"karpathy-guidelines", "karpathy behavioral guidelines"}:
        return True
    return len(skill_terms & _GENERAL_CODING_SKILL_TERMS) >= 4


def _always_recommend_keys(values: list[str]) -> set[str]:
    return {_normalize_skill_key(value) for value in values if value.strip()}


def _is_always_recommended(skill: SkillArtifact, always_recommend: set[str]) -> bool:
    if not always_recommend or skill.side_effect_level == "external":
        return False
    return _matches_always_recommend_key(skill, always_recommend)


def _matches_always_recommend_key(skill: SkillArtifact, always_recommend: set[str]) -> bool:
    if not always_recommend:
        return False
    keys = {
        _normalize_skill_key(skill.name),
        _normalize_skill_key(skill.path),
        _normalize_skill_key(skill.path.rsplit("/", 1)[0]),
    }
    return bool(keys & always_recommend)


def _normalize_skill_key(value: str) -> str:
    return value.strip().lower().replace("\\", "/").rstrip("/")


def _has_coding_intent(task: str, selected_paths: list[str]) -> bool:
    task_words = set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", task.lower().replace("_", "-")))
    if task_words & _CODING_INTENT_TERMS:
        return True
    return any(_looks_like_code_path(path) for path in selected_paths)


def _raw_terms(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", text.replace("_", "-"))
    }


def _looks_like_code_path(path: str) -> bool:
    return bool(re.search(
        r"\.(py|pyi|js|jsx|ts|tsx|go|rs|rb|php|java|kt|kts|swift|c|cc|cpp|h|hpp|cs|m|mm)$",
        path,
    ))


def _path_matches(path: str, pattern: str) -> bool:
    normalized_path = path.strip("/")
    normalized_pattern = pattern.strip("/")
    return (
        fnmatch.fnmatch(normalized_path, normalized_pattern)
        or fnmatch.fnmatch(normalized_path, f"{normalized_pattern}/**")
        or normalized_path.startswith(normalized_pattern.rstrip("/") + "/")
    )
