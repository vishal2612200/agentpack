from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core import git
from agentpack.core.config import load_config
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.loop_protocol import load_loop_state
from agentpack.core.task_freshness import task_freshness
from agentpack.core.thread_context import list_thread_rows
from agentpack.dashboard.models import (
    BenchmarkSummary,
    ContextHealth,
    DashboardSnapshot,
    LearningArtifact,
    LearningMemory,
    LearningWeakSpot,
    ObserverInsightRow,
    ObserverSummary,
    LoopSummary,
    ProjectInfo,
    SelectedFileRow,
    SkillFeedbackStatus,
    SkillDomainSummary,
    SkillInventoryRow,
    SkillInventorySourceSummary,
    SkillRow,
    SkillSection,
    SkillsInventorySummary,
    SuggestedAction,
    TaskInfo,
    TaskMapFileRow,
    ThreadSummary,
)
from agentpack.learning.sessions import summarize_weak_spots
from agentpack.learning.task_memory import recent_task_memories
from agentpack.observer.brief import build_observer_brief
from agentpack.router.models import SkillArtifact
from agentpack.router.skills_index import ensure_inventory_index


MAX_JSONL_ROWS = 500
MAX_RECENT_FEEDBACK = 20
MAX_EXCERPT_CHARS = 1200
MAX_REASONS = 5
MAX_MISSES = 20
MAX_SKILL_INVENTORY_ROWS = 100
MAX_METADATA_ITEMS = 8
MAX_INFERRED_DOMAINS = 3
MIN_BM25_DOMAIN_SCORE = 1.0

_DOMAIN_CORPUS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("career", ("academic", "ats", "career", "cover-letter", "cv", "interview", "job", "linkedin", "offer", "portfolio", "reference", "resume", "salary")),
    ("testing", ("ab-test", "cypress", "junit", "playwright", "pytest", "qa", "regression", "tdd", "test", "testing")),
    ("ai", ("agent", "ai", "anthropic", "claude", "codex", "embedding", "llm", "openai", "prompt", "rag")),
    ("agent tooling", ("agentpack", "atlassian", "gmail", "google-drive", "linear", "mcp", "plugin", "tool")),
    ("android", ("android", "emulator", "gradle", "jetpack", "kotlin")),
    ("ios", ("appkit", "ios", "macos", "swift", "swiftui", "xcode")),
    ("frontend", ("angular", "component", "css", "frontend", "html", "react", "svelte", "ui", "ux", "vue", "web")),
    ("backend", ("backend", "database", "django", "express", "fastapi", "flask", "node", "server")),
    ("api", ("api", "graphql", "openapi", "rest", "webhook")),
    ("architecture", ("architect", "architecture", "clean", "ddd", "design", "pattern", "patterns", "refactor")),
    ("debugging", ("bug", "debug", "debugging", "stack-trace")),
    ("devops", ("ci", "cloud", "deploy", "docker", "kubernetes", "release")),
    ("data", ("analytics", "data", "dataset", "dbt", "spreadsheet", "sql", "warehouse")),
    ("security", ("auth", "oauth", "privacy", "secret", "security", "threat")),
    ("product", ("gtm", "marketing", "prd", "pricing", "product", "strategy")),
    ("documentation", ("article", "docs", "documentation", "readme", "writing")),
    ("cpp", ("c++", "cmake", "cpp", "ctest", "googletest", "gtest")),
    ("java", ("java", "spring")),
    ("python", ("pandas", "python")),
    ("php", ("laravel", "php", "symfony")),
    ("embedded", ("embedded", "firmware", "microcontroller", "rtos")),
    ("finance", ("banknifty", "bse", "nifty", "nse", "stock", "stocks", "trading")),
)

_DOMAIN_DOCUMENT_FREQUENCY = Counter(
    term
    for _domain, terms in _DOMAIN_CORPUS
    for term in set(terms)
)


def build_project_dashboard_snapshot(root: Path) -> DashboardSnapshot:
    root = root.resolve()
    agentpack_dir = root / ".agentpack"
    meta = load_pack_metadata(root) if agentpack_dir.exists() else None
    task_text = _read_task(agentpack_dir / "task.md") or str((meta or {}).get("task") or "")
    freshness = task_freshness(root, meta) if meta else None
    context = _context_health(meta, freshness)
    selected_files = _selected_files(meta)
    feedback_rows = _load_jsonl(agentpack_dir / "skill_feedback.jsonl")
    skill_section = _skill_section(meta, feedback_rows)
    skills_inventory = _skills_inventory_summary(root, initialized=agentpack_dir.exists())
    learning = _learning_artifacts(agentpack_dir)
    learning_memories = _learning_memories(root)
    learning_weak_spots = _learning_weak_spots(root)
    observer = _observer_summary(root, task_text)
    benchmarks = _benchmark_summary(
        _load_jsonl(agentpack_dir / "metrics.jsonl"),
        _load_jsonl(agentpack_dir / "benchmark_results.jsonl"),
    )
    threads = _thread_summary(root, meta)
    loop = _loop_summary(root)
    actions = _suggested_actions(agentpack_dir, task_text, context, learning, benchmarks, feedback_rows)

    return DashboardSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        project=_project_info(root, meta),
        task=TaskInfo(
            text=task_text,
            state=_task_state(agentpack_dir / "task_state.md"),
            thread_id=_thread_id(meta),
        ),
        context=context,
        selected_files=selected_files,
        task_map=_task_map_files(meta),
        skills=skill_section,
        skills_inventory=skills_inventory,
        skill_feedback=_feedback_summary(feedback_rows),
        learning=learning,
        learning_memories=learning_memories,
        learning_weak_spots=learning_weak_spots,
        observer=observer,
        benchmarks=benchmarks,
        threads=threads,
        loop=loop,
        suggested_actions=actions,
    )


def _project_info(root: Path, meta: dict[str, Any] | None) -> ProjectInfo:
    branch = str((meta or {}).get("git_branch") or "")
    sha = str((meta or {}).get("git_sha") or "")
    if git.is_git_repo(root):
        branch = branch or (git.current_branch(root) or "")
        sha = sha or (git.current_sha(root) or "")
    return ProjectInfo(name=root.name, path=str(root), branch=branch, git_sha=sha[:12])


def _thread_id(meta: dict[str, Any] | None) -> str | None:
    concurrent = (meta or {}).get("concurrent_context")
    if isinstance(concurrent, dict):
        thread_id = concurrent.get("thread_id")
        if thread_id:
            return str(thread_id)
    thread_id = (meta or {}).get("thread_id")
    return str(thread_id) if thread_id else None


def _read_task(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _task_state(path: Path) -> str:
    if not path.exists():
        return "unknown"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"
    valid = {"planned", "in_progress", "blocked", "done"}
    for line in lines:
        if line.lower().startswith("status:"):
            value = line.split(":", 1)[1].strip()
            return value if value in valid else "unknown"
    return "unknown"


def _context_health(meta: dict[str, Any] | None, freshness: Any) -> ContextHealth:
    if not meta:
        return ContextHealth(status="missing")

    status = "fresh"
    stale_reason = ""
    if freshness is not None and getattr(freshness, "is_stale", False):
        status = "stale"
        stale_reason = getattr(freshness, "reason", "") or ""
    elif isinstance(meta.get("freshness"), dict):
        freshness_status = str(meta["freshness"].get("status") or "").lower()
        if freshness_status in {"fresh", "stale", "missing", "unknown"}:
            status = freshness_status
        stale_reason = str(meta["freshness"].get("reason") or meta["freshness"].get("stale_reason") or "")

    selected = meta.get("selected_files_meta") or []
    packed_tokens = _as_int(meta.get("token_estimate"), _as_int(meta.get("packed_tokens"), 0))
    raw_tokens = _as_int(meta.get("raw_tokens"), 0)
    saving_pct = _as_float(meta.get("saving_pct"), 0.0)
    if saving_pct == 0.0 and raw_tokens > 0 and packed_tokens > 0:
        saving_pct = round((1 - packed_tokens / raw_tokens) * 100, 1)

    return ContextHealth(
        status=status,
        generated_at=str(meta.get("generated_at") or ""),
        mode=str(meta.get("mode") or ""),
        packed_tokens=packed_tokens,
        raw_tokens=raw_tokens,
        saving_pct=saving_pct,
        selected_files_count=len(selected) if isinstance(selected, list) else 0,
        stale_reason=stale_reason,
    )


def _selected_files(meta: dict[str, Any] | None) -> list[SelectedFileRow]:
    rows: list[SelectedFileRow] = []
    for item in (meta or {}).get("selected_files_meta") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            SelectedFileRow(
                path=str(item.get("path") or ""),
                include_mode=str(item.get("mode") or item.get("include_mode") or ""),
                score=_as_float(item.get("score"), 0.0),
                tokens=_as_int(item.get("tokens"), _as_int(item.get("estimated_tokens"), 0)),
                reasons=_string_list(item.get("reasons"))[:MAX_REASONS],
            )
        )
    return rows


def _task_map_files(meta: dict[str, Any] | None) -> list[TaskMapFileRow]:
    task_map = (meta or {}).get("task_map") or {}
    files = task_map.get("files") if isinstance(task_map, dict) else []
    rows: list[TaskMapFileRow] = []
    if not isinstance(files, list):
        return rows
    for item in files[:50]:
        if not isinstance(item, dict):
            continue
        rows.append(
            TaskMapFileRow(
                path=str(item.get("path") or ""),
                kind=str(item.get("kind") or ""),
                include_mode=str(item.get("include_mode") or ""),
                score=_as_float(item.get("score"), 0.0),
                risk_level=str(item.get("risk_level") or "low"),
                risk_reasons=_string_list(item.get("risk_reasons"))[:MAX_REASONS],
                why_selected=_string_list(item.get("why_selected"))[:MAX_REASONS],
                tests_to_run=_string_list(item.get("tests_to_run"))[:MAX_REASONS],
                may_break=_string_list(item.get("may_break"))[:MAX_REASONS],
                retrieve_ref=str(item.get("retrieve_ref") or ""),
            )
        )
    return rows


def _skill_section(meta: dict[str, Any] | None, feedback_rows: list[dict[str, Any]]) -> SkillSection:
    feedback = _feedback_summary_by_skill(feedback_rows)
    return SkillSection(
        task_specific=_skill_rows((meta or {}).get("selected_skills") or [], feedback),
        baseline=_skill_rows((meta or {}).get("baseline_skills") or [], feedback),
    )


def _skill_rows(values: list[Any], feedback: dict[str, SkillFeedbackStatus]) -> list[SkillRow]:
    rows: list[SkillRow] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        skill = item.get("skill") if isinstance(item.get("skill"), dict) else item
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or item.get("name") or "")
        if not name:
            continue
        rows.append(
            SkillRow(
                name=name,
                path=str(skill.get("path") or ""),
                confidence=_as_float(item.get("confidence"), 0.0),
                score=_as_float(item.get("score"), 0.0),
                side_effect_level=str(skill.get("side_effect_level") or ""),
                status=feedback.get(name.lower(), "none"),
                reasons=_string_list(item.get("reasons"))[:MAX_REASONS],
            )
        )
    return rows


def _skills_inventory_summary(root: Path, *, initialized: bool) -> SkillsInventorySummary:
    if not initialized:
        return SkillsInventorySummary(index_error="AgentPack is not initialized.")
    try:
        cfg = load_config(root)
        result = ensure_inventory_index(root, cfg.skills.paths)
    except Exception as exc:
        return SkillsInventorySummary(index_error=str(exc))

    inventory = result.document.inventory
    rows = [_skill_inventory_row(skill) for skill in inventory.skills]
    names: dict[str, int] = {}
    for row in rows:
        key = row.name.lower()
        names[key] = names.get(key, 0) + 1
    return SkillsInventorySummary(
        available=True,
        index_refreshed=result.refreshed,
        index_reason=result.reason,
        total_skills=len(inventory.skills),
        total_rules=len(inventory.rules),
        uncategorized_count=sum(1 for row in rows if row.domains == ["uncategorized"]),
        missing_metadata_count=sum(1 for row in rows if row.metadata_quality == "inferred"),
        duplicate_names=sorted(name for name, count in names.items() if count > 1),
        sources=[
            SkillInventorySourceSummary(
                configured_path=source.configured_path,
                resolved_path=source.resolved_path,
                exists=source.exists,
                file_count=source.file_count,
            )
            for source in result.document.sources
        ],
        domains=_domain_counts(rows),
        rows=rows[:MAX_SKILL_INVENTORY_ROWS],
    )


def _skill_inventory_row(skill: SkillArtifact) -> SkillInventoryRow:
    explicit_metadata = bool(
        skill.domains
        or skill.task_types
        or skill.languages
        or skill.frameworks
        or skill.applies_to_paths
        or skill.anti_paths
        or skill.anti_triggers
    )
    domains, domain_confidence, domain_source = _skill_domains(skill)
    metadata_quality = "explicit" if explicit_metadata else "inferred"
    return SkillInventoryRow(
        name=skill.name,
        path=skill.path,
        source=skill.source,
        domains=domains,
        languages=skill.languages,
        frameworks=skill.frameworks,
        side_effect_level=skill.side_effect_level,
        metadata_quality=metadata_quality,
        metadata=_skill_metadata(
            skill,
            domains=domains,
            quality=metadata_quality,
            domain_confidence=domain_confidence,
            domain_source=domain_source,
        ),
        domain_confidence=domain_confidence,
        domain_source=domain_source,
    )


def _skill_domains(skill: SkillArtifact) -> tuple[list[str], float, str]:
    if skill.domains:
        return skill.domains, 1.0, "explicit domains"
    if skill.task_types:
        return skill.task_types, 1.0, "explicit task_types"
    scored = _bm25_skill_domains(skill)
    if scored:
        top_score = scored[0][1]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        confidence = top_score / (top_score + second_score + 1.0)
        return [domain for domain, _score in scored[:MAX_INFERRED_DOMAINS]], round(confidence, 2), "bm25"
    keyword_domains = _keyword_skill_domains(skill)
    if keyword_domains != ["uncategorized"]:
        return keyword_domains, 0.35, "keyword fallback"
    return keyword_domains, 0.0, "none"


def _bm25_skill_domains(skill: SkillArtifact) -> list[tuple[str, float]]:
    text = _skill_domain_text(skill)
    token_counts = Counter(_domain_token_list(text))
    if not token_counts:
        return []

    scored: list[tuple[str, float]] = []
    total_domains = len(_DOMAIN_CORPUS)
    for domain, terms in _DOMAIN_CORPUS:
        score = 0.0
        for term in terms:
            term_count = token_counts.get(term, 0)
            if "-" in term and term.replace("-", " ") in text.lower():
                term_count += 1
            if term_count <= 0:
                continue
            document_frequency = _DOMAIN_DOCUMENT_FREQUENCY[term]
            idf = math.log(1 + (total_domains - document_frequency + 0.5) / (document_frequency + 0.5))
            score += idf * ((term_count * 2.2) / (term_count + 1.2))
        if score >= MIN_BM25_DOMAIN_SCORE:
            scored.append((domain, score))
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def _keyword_skill_domains(skill: SkillArtifact) -> list[str]:
    text = _skill_domain_text(skill)
    tokens = set(_domain_token_list(text))
    scores: dict[str, int] = {}
    for domain, terms in _DOMAIN_CORPUS:
        score = sum(2 if term in tokens else 0 for term in terms)
        score += sum(1 for term in terms if "-" in term and term.replace("-", " ") in text.lower())
        if score:
            scores[domain] = score
    if not scores:
        return ["uncategorized"]
    return [
        domain
        for domain, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:MAX_INFERRED_DOMAINS]
    ]


def _skill_domain_text(skill: SkillArtifact) -> str:
    skill_path = Path(skill.path)
    return " ".join(
        [
            skill.name,
            skill.description,
            skill_path.parent.name,
            " ".join(skill.triggers[:20]),
            " ".join(skill.frameworks),
            " ".join(skill.languages),
            " ".join(skill.tools_required),
        ]
    )


def _skill_metadata(
    skill: SkillArtifact,
    *,
    domains: list[str],
    quality: str,
    domain_confidence: float,
    domain_source: str,
) -> list[str]:
    items: list[str] = []
    if domains != ["uncategorized"]:
        items.append(f"domain source: {domain_source}")
        items.append("domain confidence: " + f"{domain_confidence:.2f}")
    if skill.domains:
        items.append("domain: " + ", ".join(skill.domains))
    if quality == "inferred" and domains != ["uncategorized"]:
        items.append("inferred domains: " + ", ".join(domains))
    if skill.description:
        items.append("description: " + " ".join(skill.description.split()))
    if skill.task_types:
        items.append("task: " + ", ".join(skill.task_types))
    if skill.languages:
        items.append("language: " + ", ".join(skill.languages))
    if skill.frameworks:
        items.append("framework: " + ", ".join(skill.frameworks))
    if skill.tools_required:
        items.append("tools: " + ", ".join(skill.tools_required))
    if skill.applies_to_paths:
        items.append("paths: " + ", ".join(skill.applies_to_paths[:3]))
    name_parts = _trigger_name_parts(skill.name)
    evidenced_terms = set(_domain_token_list(skill.description))
    path = Path(skill.path)
    hidden_triggers = {
        skill.name.lower().replace("_", "-"),
        path.parent.name.lower().replace("_", "-"),
        path.stem.lower().replace("_", "-"),
    }
    candidate_triggers = [
        trigger
        for trigger in skill.triggers
        if _show_skill_trigger(trigger, hidden_triggers=hidden_triggers, name_parts=name_parts, evidenced_terms=evidenced_terms)
    ]
    useful_triggers = _rank_skill_triggers(candidate_triggers, skill=skill, domains=domains, evidenced_terms=evidenced_terms)[:8]
    if useful_triggers:
        items.append("triggers: " + ", ".join(useful_triggers))
    return items[:MAX_METADATA_ITEMS]


def _show_skill_trigger(
    trigger: str,
    *,
    hidden_triggers: set[str],
    name_parts: set[str],
    evidenced_terms: set[str],
) -> bool:
    if trigger in hidden_triggers:
        return False
    parts = trigger.split("-")
    if trigger in name_parts:
        return trigger in evidenced_terms
    if len(parts) > 1:
        return not all(part in name_parts for part in parts)
    return not any(part in name_parts for part in parts)


def _rank_skill_triggers(
    triggers: list[str],
    *,
    skill: SkillArtifact,
    domains: list[str],
    evidenced_terms: set[str],
) -> list[str]:
    domain_terms = set(domains) | set(skill.domains) | set(skill.languages) | set(skill.frameworks) | set(skill.tools_required)
    indexed = list(enumerate(triggers))

    def score(item: tuple[int, str]) -> tuple[int, int]:
        index, trigger = item
        parts = trigger.split("-")
        value = 0
        if len(parts) > 1:
            value += 100 + min(len(parts), 3) * 8
            if any(char.isdigit() for char in trigger) or any(marker in trigger for marker in ("+", ".")):
                value += 4
        else:
            value -= 30
            if trigger in evidenced_terms:
                value += 20
            if trigger in domain_terms:
                value += 20
            if any(char.isdigit() for char in trigger) or any(marker in trigger for marker in ("+", ".")):
                value += 8
            if trigger not in domain_terms and trigger not in evidenced_terms and trigger not in {"ai", "go", "ui"}:
                value -= 25
        return (-value, index)

    return [trigger for _index, trigger in sorted(indexed, key=score)]


def _trigger_name_parts(value: str) -> set[str]:
    normalized = value.lower().replace("_", "-")
    return {part for part in re.split(r"[^a-z0-9]+", normalized) if part}


def _domain_token_list(text: str) -> list[str]:
    lower = text.lower().replace("_", "-")
    raw_tokens = re.findall(r"[a-z][a-z0-9+.-]{1,}", lower)
    split_tokens = [
        part
        for token in raw_tokens
        for part in token.split("-")
        if len(part) > 1
    ]
    return raw_tokens + split_tokens


def _clip(value: str, max_chars: int) -> str:
    clean = " ".join(value.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "…"


def _domain_counts(rows: list[SkillInventoryRow]) -> list[SkillDomainSummary]:
    counts: dict[str, int] = {}
    for row in rows:
        for domain in row.domains:
            counts[domain] = counts.get(domain, 0) + 1
    return [
        SkillDomainSummary(name=name, count=count)
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-MAX_JSONL_ROWS:]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _feedback_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "recent": rows[-MAX_RECENT_FEEDBACK:],
        "summary_by_skill": _feedback_summary_by_skill(rows),
    }


def _feedback_summary_by_skill(rows: list[dict[str, Any]]) -> dict[str, SkillFeedbackStatus]:
    status: dict[str, SkillFeedbackStatus] = {}
    precedence = {
        "none": 0,
        "recommended_only": 1,
        "ignored": 2,
        "used_helpful": 3,
        "used_noisy": 4,
        "bad_recommendation": 5,
    }

    def assign(skill: Any, new_status: SkillFeedbackStatus) -> None:
        key = str(skill).strip().lower()
        if not key:
            return
        current = status.get(key, "none")
        if precedence[new_status] >= precedence[current]:
            status[key] = new_status

    for row in rows:
        for skill in _string_list(row.get("recommended_skills")):
            assign(skill, "recommended_only")
        for skill in _string_list(row.get("used_skills")):
            feedback = str(row.get("user_feedback") or "").lower()
            assign(skill, "used_noisy" if feedback in {"bad", "noisy", "unhelpful", "not-helpful"} else "used_helpful")
        for skill in _string_list(row.get("ignored_skills")):
            assign(skill, "ignored")
        for skill in _string_list(row.get("bad_recommendations")):
            assign(skill, "bad_recommendation")
    return status


def _learning_artifacts(agentpack_dir: Path) -> list[LearningArtifact]:
    artifacts = [
        ("Learning notes", "learning.md"),
        ("Daily summary", "daily-summary.md"),
        ("Agent lessons", "agent-lessons.md"),
        ("Skill progress", "skills-progress.json"),
        ("Learning feedback", "learning-feedback.jsonl"),
    ]
    return [
        LearningArtifact(
            label=label,
            path=f".agentpack/{name}",
            exists=(agentpack_dir / name).exists(),
            excerpt=_bounded_excerpt(agentpack_dir / name),
        )
        for label, name in artifacts
    ]


def _learning_memories(root: Path) -> list[LearningMemory]:
    rows: list[LearningMemory] = []
    for item in reversed(recent_task_memories(root, limit=6)):
        rows.append(
            LearningMemory(
                task=str(item.get("task") or ""),
                stage=str(item.get("stage") or ""),
                status=str(item.get("status") or ""),
                branch=str(item.get("branch") or ""),
                git_sha=str(item.get("git_sha") or "")[:12],
                concepts=_string_list(item.get("concepts"))[:6],
                changed_files=_string_list(item.get("changed_files"))[:8],
                selected_files=_string_list(item.get("selected_files"))[:8],
            )
        )
    return rows


def _observer_summary(root: Path, task: str) -> ObserverSummary:
    try:
        brief = build_observer_brief(root, task=task)
    except Exception as exc:
        return ObserverSummary(
            event_types={"error": 1},
            insights=[
                ObserverInsightRow(
                    kind="error",
                    title="Observer summary unavailable",
                    detail=str(exc),
                    action="Run `agentpack guard --agent codex --repair-stale --refresh-context` and retry.",
                    confidence=0.0,
                )
            ],
        )
    event_types = brief.stats.get("types") if isinstance(brief.stats, dict) else {}
    if not isinstance(event_types, dict):
        event_types = {}
    return ObserverSummary(
        generated_at=brief.generated_at,
        events=_as_int(brief.stats.get("events") if isinstance(brief.stats, dict) else 0, 0),
        event_types={str(key): _as_int(value, 0) for key, value in event_types.items()},
        insights=[
            ObserverInsightRow(
                kind=insight.kind,
                title=insight.title,
                detail=insight.detail,
                action=insight.action,
                confidence=insight.confidence,
                related_files=insight.related_files,
                evidence=insight.evidence,
            )
            for insight in brief.insights[:8]
        ],
    )


def _learning_weak_spots(root: Path) -> list[LearningWeakSpot]:
    rows: list[LearningWeakSpot] = []
    for item in summarize_weak_spots(root, limit=6):
        rows.append(
            LearningWeakSpot(
                concept=str(item.get("concept") or ""),
                count=_as_int(item.get("count"), 0),
                mode=str(item.get("mode") or ""),
                latest_task=str(item.get("latest_task") or ""),
                latest_question=str(item.get("latest_question") or ""),
                evidence_files=_string_list(item.get("evidence_files"))[:6],
            )
        )
    return rows


def _bounded_excerpt(path: Path, limit: int = MAX_EXCERPT_CHARS) -> str:
    if not path.exists() or path.suffix == ".jsonl":
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:limit]


def _benchmark_summary(metrics_rows: list[dict[str, Any]], benchmark_rows: list[dict[str, Any]]) -> BenchmarkSummary:
    numeric_keys = [
        "selection_recall",
        "selection_precision",
        "selection_token_precision",
        "rank_at_k",
        "skill_recall_at_3",
        "skill_precision_at_3",
        "skill_mrr",
        "skill_noise_rate",
    ]
    recent = metrics_rows[-10:] + benchmark_rows[-10:]
    averages: dict[str, float] = {}
    for key in numeric_keys:
        values = [_as_float(row[key], 0.0) for row in recent if _is_number(row.get(key))]
        if values:
            averages[key] = sum(values) / len(values)
    latest = (benchmark_rows or metrics_rows or [{}])[-1]
    misses = [miss for row in benchmark_rows[-5:] for miss in (row.get("misses") or row.get("missed_expected") or []) if isinstance(miss, dict)]
    return BenchmarkSummary(latest=latest, averages=averages, misses=misses[:MAX_MISSES])


def _thread_summary(root: Path, meta: dict[str, Any] | None) -> ThreadSummary:
    rows = list_thread_rows(root, active_only=True)
    conflicts: list[dict[str, Any]] = []
    concurrent = (meta or {}).get("concurrent_context")
    if isinstance(concurrent, dict):
        raw_conflicts = concurrent.get("conflicts") or []
        if isinstance(raw_conflicts, list):
            conflicts = [item for item in raw_conflicts if isinstance(item, dict)]
    return ThreadSummary(active_count=len(rows), conflicts=conflicts)


def _loop_summary(root: Path) -> LoopSummary:
    state = load_loop_state(root)
    if state is None:
        return LoopSummary()
    metrics = _loop_metrics(root)
    return LoopSummary(
        exists=True,
        status=state.status,
        task=state.task,
        iteration=state.iteration,
        max_iterations=state.max_iterations,
        runner=state.runner,
        last_runner_status=_result_status(state.last_runner),
        last_verification_status=_result_status(state.last_verification),
        blocked_reason=state.blocked_reason,
        failure_class=state.failure_class,
        risk_level=state.risk_review.level,
        changed_files=state.last_diff.files_changed[:20],
        diagnosis_file=".agentpack/loop_diagnosis.md" if (root / ".agentpack" / "loop_diagnosis.md").exists() else "",
        handoff_file=state.handoff_file,
        acceptance_file=state.acceptance_file,
        rollback_patch=state.rollback_patch,
        runs=metrics["runs"],
        blocked_runs=metrics["blocked"],
        ready_runs=metrics["ready_to_finish"],
        avg_iterations=metrics["avg_iterations"],
        next_action=_loop_next_action(state.status, state.task, state.runner, bool(state.verification_commands)),
    )


def _loop_metrics(root: Path) -> dict[str, Any]:
    rows = _load_jsonl(root / ".agentpack" / "loop_metrics.jsonl")
    total_iterations = sum(_as_int(row.get("iterations"), 0) for row in rows)
    return {
        "runs": len(rows),
        "blocked": sum(1 for row in rows if row.get("outcome") == "blocked"),
        "ready_to_finish": sum(1 for row in rows if row.get("outcome") == "ready_to_finish"),
        "avg_iterations": round(total_iterations / len(rows), 2) if rows else 0.0,
    }


def _suggested_actions(
    agentpack_dir: Path,
    task_text: str,
    context: ContextHealth,
    learning: list[LearningArtifact],
    benchmarks: BenchmarkSummary,
    feedback_rows: list[dict[str, Any]],
) -> list[SuggestedAction]:
    actions: list[SuggestedAction] = []
    if not agentpack_dir.exists():
        actions.append(
            SuggestedAction(
                label="Initialize AgentPack",
                command="agentpack init --yes",
                reason="No .agentpack directory exists.",
            )
        )
    if not task_text:
        actions.append(
            SuggestedAction(
                label="Start a task",
                command='agentpack work "describe the task"',
                reason="No current task found.",
            )
        )
    if context.status in {"missing", "stale"}:
        actions.append(
            SuggestedAction(
                label="Refresh context",
                command="agentpack pack --task auto",
                reason=f"Context is {context.status}.",
            )
        )
    if not any(item.exists for item in learning):
        actions.append(
            SuggestedAction(
                label="Generate learning notes",
                command="agentpack learn",
                reason="No learning artifacts found.",
            )
        )
    if not benchmarks.averages:
        actions.append(
            SuggestedAction(
                label="Initialize benchmarks",
                command="agentpack benchmark --init",
                reason="No benchmark metrics found.",
            )
        )
    if not feedback_rows:
        actions.append(
            SuggestedAction(
                label="Record skill feedback",
                command='agentpack skills feedback --task "..." --recommended-skill skill-name --user-feedback helpful',
                reason="No skill feedback found.",
            )
        )
    return actions


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _result_status(result: Any) -> str:
    if result is None:
        return ""
    return "passed" if getattr(result, "returncode", 1) == 0 else "failed"


def _loop_next_action(status: str, task: str, runner: str, has_verification: bool) -> str:
    if not runner:
        return 'agentpack work "..." --run --runner "..."'
    if not has_verification:
        return f'agentpack work "{task}" --run --verify "pytest -q"'
    if status == "ready_to_finish":
        return "agentpack finish --since main"
    if status == "blocked":
        return "agentpack dashboard"
    return f'agentpack work "{task}" --run'
