from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core.config import load_config
from agentpack.core.project_index import load_project_index, project_id
from agentpack.learning.competencies import (
    COMPETENCY_DEFINITIONS,
    competency_artifact,
    competency_expected_points,
    competency_proof_requirement,
    derive_competency_summaries,
    load_learner_profile,
    map_to_competency,
    mastery_summary_from_competencies,
    readable_registered_project_roots,
    role_drill_framing,
    role_emphasizes,
)
from agentpack.learning.extractor import CONCEPT_RULES, build_learning_topic
from agentpack.learning.models import (
    CompetencyId,
    CompetencySummary,
    LearningEvidence,
    LearningProjectRef,
    LearningQuestion,
    LearningRecommendationSet,
    LearningRecommendationTopic,
    LearningReport,
)
from agentpack.learning.sessions import (
    MAX_SESSION_ROWS,
    derive_mastery_status,
    read_learning_sessions,
    read_learning_sessions_with_errors,
)
from agentpack.learning.task_memory import (
    recent_task_memories,
    recent_task_start_snapshots,
)
from agentpack.session.events import read_events, record_event


MAX_PROJECTS = 20
MAX_ARTIFACT_ROWS = 100
MAX_TASKS = 20
MAX_EVIDENCE = 5
WINDOW_DAYS = 30
COOLDOWN_DAYS = 7

_TOPIC_TITLES = {
    "authentication": "Authentication Failure Modes",
    "retry logic": "Safe Retry Logic",
    "caching": "Cache Correctness",
    "rate limiting": "Rate Limiting",
    "configuration": "Configuration Design",
    "testing": "Regression Test Design",
    "CLI design": "CLI Workflow Design",
    "context packing": "Context Packing Quality",
    "serialization": "Stable Serialization",
    "mcp": "Model Context Protocol",
}
_STOP_WORDS = {
    "add",
    "agentpack",
    "build",
    "change",
    "create",
    "current",
    "finish",
    "from",
    "implement",
    "into",
    "make",
    "project",
    "recent",
    "support",
    "task",
    "that",
    "this",
    "update",
    "with",
}


@dataclass
class _Candidate:
    project: LearningProjectRef
    subject: str
    title: str
    why: str
    task: str
    concepts: list[str]
    lanes: set[str]
    evidence: list[LearningEvidence]
    task_ids: set[str] = field(default_factory=set)
    subsystems: set[str] = field(default_factory=set)
    latest_at: str = ""
    active: bool = False
    friction_count: int = 0
    friction_latest_at: str = ""
    relation_count: int = 0
    prompt: str = ""
    questions: list[LearningQuestion] = field(default_factory=list)
    score: int = 0
    score_reasons: dict[str, int] = field(default_factory=dict)
    mastery_status: str = "unassessed"
    competency_id: CompetencyId | None = None
    competency_status: str = "unassessed"
    target_level: str = "unspecified"
    proof_requirement: str = "reasoning"
    required_artifact: str = ""
    role_emphasis: bool = False
    request_match: bool = False

    @property
    def topic_id(self) -> str:
        raw = f"{self.project.project_id}|{self.subject}"
        return "topic-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def recommend_learning_topics(
    root: Path,
    *,
    report: LearningReport | None = None,
    request: str = "",
    global_scope: bool = False,
    limit: int = 3,
    now: datetime | None = None,
) -> LearningRecommendationSet:
    root = root.resolve()
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    warnings: list[str] = []
    profile, profile_warnings = load_learner_profile()
    warnings.extend(profile_warnings)
    competencies = derive_competency_summaries(
        readable_registered_project_roots(root),
        profile,
    )
    competency_index = {item.competency_id: item for item in competencies}
    projects = _projects(root, global_scope=global_scope, warnings=warnings)
    candidates: dict[str, _Candidate] = {}

    for project_root, project in projects:
        project_report = report if project_root == root else None
        try:
            project_candidates, project_warnings = _project_candidates(
                project_root,
                project,
                report=project_report,
                now=generated_at,
            )
        except (OSError, TypeError, ValueError):
            warnings.append(f"{project.name}: skipped malformed or inaccessible learning data")
            continue
        warnings.extend(project_warnings)
        for candidate in project_candidates:
            _merge_candidate(candidates, candidate)

    ranked = list(candidates.values())
    for candidate in ranked:
        _attach_competency(candidate, competency_index, profile.role, profile.target_level)
    ranked = [candidate for candidate in ranked if candidate.competency_id is not None]
    anchors = _task_anchors_by_project(ranked)
    weak_candidate = _derived_weak_candidate(ranked, competency_index)
    if weak_candidate is not None:
        ranked.append(weak_candidate)
    for project_anchor in anchors:
        ranked.extend(_breadth_candidates(project_anchor, competencies, profile.role, profile.target_level))
    for candidate in ranked:
        _score(
            candidate,
            request=request,
            now=generated_at,
            root=Path(candidate.project.root),
        )
    ranked.sort(key=_sort_key)
    selection_limit = max(1, min(3, limit))
    selected = _select_topics(ranked, limit=selection_limit, global_scope=global_scope)
    topics = [_to_topic(candidate, request=request, global_scope=global_scope) for candidate in selected]
    if len(topics) < selection_limit:
        warnings.append("insufficient_history: fewer than three evidence-backed topics are available")

    return LearningRecommendationSet(
        recommendation_id="recommendation-" + uuid.uuid4().hex[:20],
        scope="global" if global_scope else "local",
        generated_at=generated_at.isoformat(),
        topics=topics,
        warnings=_unique(warnings),
        mastery_summary=mastery_summary_from_competencies(competencies),
        profile=profile,
        competencies=competencies,
    )


def record_recommendation_impressions(
    recommendations: LearningRecommendationSet,
) -> LearningRecommendationSet:
    warnings = list(recommendations.warnings)
    for topic in recommendations.topics:
        try:
            record_event(
                Path(topic.project.root),
                "learning_recommendation_shown",
                {
                    "recommendation_id": recommendations.recommendation_id,
                    "scope": recommendations.scope,
                    "topic_id": topic.topic_id,
                    "project_id": topic.project.project_id,
                    "topic": topic.model_dump(mode="json"),
                },
                source="learn",
            )
        except OSError:
            warnings.append(f"{topic.project.name}: could not record recommendation history")
    return recommendations.model_copy(update={"warnings": _unique(warnings)})


def find_recommended_topic(
    root: Path,
    topic_id: str,
    *,
    project_id_value: str = "",
) -> tuple[Path, LearningRecommendationTopic, str]:
    target = _resolve_project_root(root.resolve(), project_id_value)
    if target is None:
        raise ValueError(f"Project not found: {project_id_value}")
    for event in reversed(read_events(target, limit=MAX_ARTIFACT_ROWS)):
        if event.get("type") != "learning_recommendation_shown" or str(event.get("topic_id") or "") != topic_id:
            continue
        payload = event.get("topic") or (event.get("payload") or {}).get("topic")
        if isinstance(payload, dict):
            return (
                target,
                LearningRecommendationTopic.model_validate(payload),
                str(event.get("recommendation_id") or ""),
            )

    recommendations = recommend_learning_topics(target)
    for topic in recommendations.topics:
        if topic.topic_id == topic_id:
            return target, topic, recommendations.recommendation_id
    raise ValueError(f"Learning topic not found: {topic_id}")


def learning_project_roots(root: Path) -> list[Path]:
    roots = [root.resolve()]
    seen = {str(roots[0])}
    for row in load_project_index():
        candidate = Path(str(row.get("path") or "")).expanduser().resolve()
        if str(candidate) in seen or not candidate.is_dir():
            continue
        seen.add(str(candidate))
        roots.append(candidate)
    return roots


def _projects(
    root: Path,
    *,
    global_scope: bool,
    warnings: list[str],
) -> list[tuple[Path, LearningProjectRef]]:
    if not global_scope:
        return [(root, _project_ref(root))]
    rows = load_project_index()
    rows.sort(
        key=lambda row: str(row.get("last_seen_at") or row.get("first_seen_at") or ""),
        reverse=True,
    )
    current = {
        "path": str(root),
        "name": root.name,
        "project_id": project_id(root),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    rows = [
        current,
        *[row for row in rows if str(Path(str(row.get("path") or "")).expanduser().resolve()) != str(root)],
    ]
    projects: list[tuple[Path, LearningProjectRef]] = []
    for row in rows[:MAX_PROJECTS]:
        candidate = Path(str(row.get("path") or "")).expanduser().resolve()
        name = str(row.get("name") or candidate.name or candidate)
        artifact_root = candidate / ".agentpack"
        if not candidate.is_dir() or not artifact_root.is_dir() or not os.access(artifact_root, os.R_OK):
            warnings.append(f"{name}: registered project is missing or inaccessible")
            continue
        if not os.access(artifact_root, os.W_OK):
            warnings.append(f"{name}: registered project is read-only")
            continue
        projects.append((candidate, _project_ref(candidate, row)))
    return projects


def _project_candidates(
    root: Path,
    project: LearningProjectRef,
    *,
    report: LearningReport | None,
    now: datetime,
) -> tuple[list[_Candidate], list[str]]:
    candidates: dict[str, _Candidate] = {}
    warnings: list[str] = []
    if report is not None:
        for topic in report.learning_topics:
            if not topic.files:
                continue
            subsystems = _subsystems(topic.files)
            subject = _subject(topic.concepts or [topic.title], subsystems)
            _merge_candidate(
                candidates,
                _Candidate(
                    project=project,
                    subject=subject,
                    title=topic.title,
                    why=topic.why,
                    task=report.task,
                    concepts=topic.concepts,
                    lanes={"now"},
                    evidence=[
                        LearningEvidence(
                            kind="current_change",
                            task=report.task,
                            path=path,
                            summary=topic.why,
                            status="active",
                        )
                        for path in topic.files[:MAX_EVIDENCE]
                    ],
                    task_ids={"current:" + _hash(report.task)},
                    subsystems=subsystems,
                    latest_at=now.isoformat(),
                    active=True,
                    prompt=topic.prompt,
                    questions=topic.questions,
                ),
            )
            _add_system_candidate(
                candidates,
                project,
                report.task,
                topic.concepts,
                topic.files,
                now.isoformat(),
                topic.why,
            )

    memories = recent_task_memories(root, limit=MAX_TASKS)
    for memory in memories:
        observed_at = _event_value(memory, "occurred_at") or _event_value(memory, "timestamp")
        if observed_at and not _within_window(observed_at, now):
            continue
        _add_task_candidates(candidates, project, memory, observed_at=observed_at, active=False)

    starts = recent_task_start_snapshots(root, limit=1)
    if starts:
        start = starts[-1]
        observed_at = str(start.get("started_at") or start.get("timestamp") or "")
        if not observed_at or _within_window(observed_at, now):
            _add_task_candidates(
                candidates,
                project,
                start,
                observed_at=observed_at or now.isoformat(),
                active=True,
            )

    cfg = load_config(root)
    episodes, episode_errors = _read_jsonl(root / cfg.learning.episodic_cases_output, limit=MAX_ARTIFACT_ROWS)
    procedures, procedure_errors = _read_jsonl(root / cfg.learning.procedures_output, limit=MAX_ARTIFACT_ROWS)
    edges, edge_errors = _read_jsonl(root / cfg.learning.memory_edges_output, limit=MAX_ARTIFACT_ROWS)
    malformed = episode_errors + procedure_errors + edge_errors
    if malformed:
        warnings.append(f"{project.name}: skipped {malformed} malformed learning records")

    for episode in episodes:
        observed_at = str(episode.get("completed_at") or episode.get("timestamp") or "")
        if observed_at and not _within_window(observed_at, now):
            continue
        _add_task_candidates(candidates, project, episode, observed_at=observed_at, active=False)

    edge_counts: dict[str, int] = {}
    for edge in edges:
        for key in (str(edge.get("from_id") or ""), str(edge.get("to_id") or "")):
            if key:
                edge_counts[key] = edge_counts.get(key, 0) + 1
    for procedure in procedures:
        title = str(procedure.get("title") or procedure.get("procedure_id") or "").strip()
        procedure_key = str(procedure.get("procedure_id") or title)
        if not title:
            continue
        concepts = _concepts(title + " " + " ".join(_strings(procedure.get("triggers"))), [])
        topic = build_learning_topic(
            task=title,
            title=f"How {title} Works",
            why="A reusable project procedure is evidence that this workflow matters across tasks.",
            concepts=concepts,
            files=[],
            mode="system-design",
        )
        _merge_candidate(
            candidates,
            _Candidate(
                project=project,
                subject="procedure:" + _normalize(procedure_key),
                title=topic.title,
                why=topic.why,
                task=title,
                concepts=concepts,
                lanes={"system"},
                evidence=[
                    LearningEvidence(
                        kind="procedure",
                        summary=title,
                        observed_at=str(procedure.get("updated_at") or procedure.get("created_at") or ""),
                        status="reusable",
                    )
                ],
                task_ids={procedure_key},
                latest_at=str(procedure.get("updated_at") or procedure.get("created_at") or ""),
                relation_count=min(3, edge_counts.get(procedure_key, 0)),
                prompt=topic.prompt,
                questions=topic.questions,
            ),
        )

    sessions, session_errors = read_learning_sessions_with_errors(root, limit=MAX_SESSION_ROWS)
    if session_errors:
        warnings.append(f"{project.name}: skipped {session_errors} malformed learning sessions")
    for session in sessions:
        if derive_mastery_status(session) != "needs_practice":
            continue
        concepts = session.concepts or [session.topic]
        title = session.topic or _topic_title(concepts[0])
        _merge_candidate(
            candidates,
            _Candidate(
                project=project,
                subject="weak:" + _subject(concepts, _subsystems(session.evidence_files)),
                title=title,
                why="A scored coaching session showed that this topic still needs practice.",
                task=session.task,
                concepts=concepts,
                lanes={"weak_spot"},
                evidence=[
                    LearningEvidence(
                        kind="prior_assessment",
                        task=session.task,
                        path=session.evidence_files[0] if session.evidence_files else "",
                        summary=session.question,
                        observed_at=session.updated_at or session.created_at,
                        status="needs_practice",
                    )
                ],
                task_ids={session.session_id},
                subsystems=_subsystems(session.evidence_files),
                latest_at=session.updated_at or session.created_at,
                competency_id=session.competency_id,
                questions=[
                    LearningQuestion(
                        mode="quiz",
                        question=session.question,
                        expected_points=session.expected_points,
                        evidence_files=session.evidence_files,
                    )
                ],
            ),
        )

    return list(candidates.values()), warnings


def _add_task_candidates(
    candidates: dict[str, _Candidate],
    project: LearningProjectRef,
    record: dict[str, Any],
    *,
    observed_at: str,
    active: bool,
) -> None:
    task = str(_record_value(record, "task") or "Recent project work")
    paths = _strings(_record_value(record, "changed_files")) or _strings(_record_value(record, "selected_files"))
    concepts = _concepts(task, paths, _strings(_record_value(record, "concepts")))
    status = str(_record_value(record, "status") or ("failed" if _record_value(record, "passed") is False else ""))
    stage = str(_record_value(record, "stage") or "")
    friction = status.lower() in {"failed", "blocked", "needs_review", "review_required"} or stage.lower() == "review"
    subsystems = _subsystems(paths)
    task_id = str(_record_value(record, "task_id") or "task:" + _hash(task + observed_at))
    for concept in concepts:
        title = _specific_title(concept, subsystems)
        evidence = LearningEvidence(
            kind="task",
            task_id=task_id,
            task=task,
            path=paths[0] if paths else "",
            summary=str(_record_value(record, "summary") or task),
            observed_at=observed_at,
            status=status or ("active" if active else "completed"),
        )
        candidate = _Candidate(
            project=project,
            subject=_subject([concept], subsystems),
            title=title,
            why=f"Recent project work used {concept} in concrete task evidence.",
            task=task,
            concepts=[concept],
            lanes={"now"},
            evidence=[evidence],
            task_ids={task_id},
            subsystems=subsystems,
            latest_at=observed_at,
            active=active,
            friction_count=1 if friction else 0,
            friction_latest_at=observed_at if friction else "",
        )
        _merge_candidate(candidates, candidate)
        _add_system_candidate(
            candidates,
            project,
            task,
            [concept],
            paths,
            observed_at,
            candidate.why,
            task_id=task_id,
        )


def _add_system_candidate(
    candidates: dict[str, _Candidate],
    project: LearningProjectRef,
    task: str,
    concepts: list[str],
    paths: list[str],
    observed_at: str,
    why: str,
    *,
    task_id: str = "",
) -> None:
    subsystems = _subsystems(paths)
    if len(subsystems) < 2:
        return
    concept = concepts[0] if concepts else "project architecture"
    names = [_display_subsystem(value) for value in sorted(subsystems)[:2]]
    title = f"{_topic_title(concept)} Across {names[0]} and {names[1]}"
    _merge_candidate(
        candidates,
        _Candidate(
            project=project,
            subject="system:" + _normalize(concept) + ":" + ":".join(sorted(subsystems)[:2]),
            title=title,
            why=f"{why} The evidence crosses {names[0]} and {names[1]}.",
            task=task,
            concepts=[concept],
            lanes={"system"},
            evidence=[
                LearningEvidence(
                    kind="system_boundary",
                    task_id=task_id,
                    task=task,
                    path=path,
                    summary=why,
                    observed_at=observed_at,
                )
                for path in paths[:MAX_EVIDENCE]
            ],
            task_ids={task_id} if task_id else {"task:" + _hash(task + observed_at)},
            subsystems=subsystems,
            latest_at=observed_at,
            relation_count=len(subsystems) - 1,
        ),
    )


def _merge_candidate(candidates: dict[str, _Candidate], incoming: _Candidate) -> None:
    if not incoming.evidence:
        return
    key = incoming.project.project_id + "|" + incoming.subject
    existing = candidates.get(key)
    if existing is None:
        incoming.evidence = incoming.evidence[:MAX_EVIDENCE]
        candidates[key] = incoming
        return
    existing.lanes.update(incoming.lanes)
    existing.task_ids.update(incoming.task_ids)
    existing.subsystems.update(incoming.subsystems)
    existing.active = existing.active or incoming.active
    existing.friction_count += incoming.friction_count
    existing.relation_count += incoming.relation_count
    existing.concepts = _unique([*existing.concepts, *incoming.concepts])
    if incoming.competency_id and not existing.competency_id:
        existing.competency_id = incoming.competency_id
    seen = {(item.kind, item.task_id, item.path, item.summary) for item in existing.evidence}
    for item in incoming.evidence:
        marker = (item.kind, item.task_id, item.path, item.summary)
        if marker not in seen:
            existing.evidence.append(item)
            seen.add(marker)
        if len(existing.evidence) >= MAX_EVIDENCE:
            break
    if incoming.latest_at > existing.latest_at:
        existing.latest_at = incoming.latest_at
        existing.task = incoming.task
        existing.why = incoming.why
    if incoming.friction_latest_at > existing.friction_latest_at:
        existing.friction_latest_at = incoming.friction_latest_at
    if incoming.prompt:
        existing.prompt = incoming.prompt
    if incoming.questions:
        existing.questions = incoming.questions


def _attach_competency(
    candidate: _Candidate,
    competencies: dict[CompetencyId, CompetencySummary],
    role: str,
    target_level: str,
) -> None:
    evidence_kind = candidate.evidence[0].kind if candidate.evidence else ""
    competency_id = candidate.competency_id or map_to_competency(
        concepts=candidate.concepts,
        task=" ".join([candidate.task, candidate.title, candidate.why]),
        paths=[item.path for item in candidate.evidence if item.path],
        evidence_kind=evidence_kind,
    )
    if competency_id is None:
        return
    summary = competencies[competency_id]
    candidate.competency_id = competency_id
    candidate.competency_status = summary.status
    candidate.mastery_status = summary.status
    candidate.target_level = target_level
    candidate.proof_requirement = competency_proof_requirement(competency_id)
    candidate.required_artifact = competency_artifact(competency_id)
    candidate.role_emphasis = role_emphasizes(role, competency_id)


def _task_anchors_by_project(candidates: list[_Candidate]) -> list[_Candidate]:
    grounded = [
        candidate
        for candidate in candidates
        if candidate.task
        and any(item.kind in {"current_change", "task", "episode"} for item in candidate.evidence)
    ]
    if not grounded:
        return []
    projects: dict[str, _Candidate] = {}
    for candidate in grounded:
        existing = projects.get(candidate.project.project_id)
        if existing is None or _anchor_sort_key(candidate) > _anchor_sort_key(existing):
            projects[candidate.project.project_id] = candidate
    return sorted(projects.values(), key=_anchor_sort_key, reverse=True)


def _anchor_sort_key(candidate: _Candidate) -> tuple[Any, ...]:
    return (
        candidate.active,
        _parse_datetime(candidate.latest_at) or datetime.min.replace(tzinfo=timezone.utc),
        candidate.topic_id,
    )


def _derived_weak_candidate(
    candidates: list[_Candidate],
    competencies: dict[CompetencyId, CompetencySummary],
) -> _Candidate | None:
    if any("weak_spot" in candidate.lanes for candidate in candidates):
        return None
    for status in ("needs_practice", "developing"):
        matches = [
            candidate
            for candidate in candidates
            if candidate.competency_id is not None
            and competencies[candidate.competency_id].status == status
            and "breadth" not in candidate.lanes
        ]
        if not matches:
            continue
        anchor = max(
            matches,
            key=lambda item: (_parse_datetime(item.latest_at) or datetime.min.replace(tzinfo=timezone.utc), item.topic_id),
        )
        competency_id = anchor.competency_id
        assert competency_id is not None
        name = COMPETENCY_DEFINITIONS[competency_id]["name"]
        return replace(
            anchor,
            subject=f"weak:{competency_id}:{anchor.subject}",
            title=f"Strengthen {name} in {anchor.project.name}",
            why=(
                f"The candidate's latest assessed {name.lower()} evidence is {status.replace('_', ' ')}. "
                "Use current project work to produce a distinct proof."
            ),
            lanes={"weak_spot"},
            evidence=list(anchor.evidence),
            task_ids=set(anchor.task_ids),
            subsystems=set(anchor.subsystems),
            score=0,
            score_reasons={},
        )
    return None


def _breadth_candidates(
    anchor: _Candidate | None,
    competencies: list[CompetencySummary],
    role: str,
    target_level: str,
) -> list[_Candidate]:
    if anchor is None:
        return []
    gaps = [
        summary
        for summary in competencies
        if summary.status != "mastered" and summary.passing_proofs < 2
    ]
    if len(gaps) > 1 and anchor.competency_id is not None:
        gaps = [summary for summary in gaps if summary.competency_id != anchor.competency_id]
    results: list[_Candidate] = []
    anchor_evidence = next(
        (item for item in anchor.evidence if item.kind in {"current_change", "task", "episode"}),
        anchor.evidence[0],
    )
    for summary in gaps:
        competency_id = summary.competency_id
        definition = COMPETENCY_DEFINITIONS[competency_id]
        framing = role_drill_framing(role)
        state = "unassessed" if summary.status == "unassessed" else "underrepresented"
        why = (
            f"{definition['name']} is {state}; this records a breadth gap, not a demonstrated weakness. "
            f"Ground the drill in the latest real task and frame it around {framing}."
        )
        question = LearningQuestion(
            mode="review",
            question=(
                f"Using '{anchor.task}', demonstrate {definition['name'].lower()} with specific project evidence."
            ),
            expected_points=competency_expected_points(competency_id),
            evidence_files=[anchor_evidence.path] if anchor_evidence.path else [],
            difficulty="medium",
        )
        results.append(
            _Candidate(
                project=anchor.project,
                subject=f"breadth:{competency_id}:{_hash(anchor.task)}",
                title=f"{definition['name']} Breadth Drill",
                why=why,
                task=anchor.task,
                concepts=[definition["name"].lower()],
                lanes={"breadth"},
                evidence=[
                    LearningEvidence(
                        kind="competency_gap",
                        task_id=anchor_evidence.task_id or (sorted(anchor.task_ids)[0] if anchor.task_ids else ""),
                        task=anchor.task,
                        path=anchor_evidence.path,
                        summary=why,
                        observed_at=anchor.latest_at,
                        status=summary.status,
                    )
                ],
                task_ids=set(anchor.task_ids),
                subsystems=set(anchor.subsystems),
                latest_at=anchor.latest_at,
                active=anchor.active,
                prompt=question.question,
                questions=[question],
                competency_id=competency_id,
                competency_status=summary.status,
                mastery_status=summary.status,
                target_level=target_level,
                proof_requirement=competency_proof_requirement(competency_id),
                required_artifact=competency_artifact(competency_id),
                role_emphasis=role_emphasizes(role, competency_id),
            )
        )
    return results


def _score(candidate: _Candidate, *, request: str, now: datetime, root: Path) -> None:
    reasons: dict[str, int] = {}
    if candidate.active:
        reasons["current_relevance"] = 30
    age = _age_days(candidate.latest_at, now)
    if age is not None and age <= 7:
        reasons["recent_relevance"] = 20
    elif age is not None and age <= WINDOW_DAYS:
        reasons["recent_relevance"] = 10
    recurrence = min(20, max(0, len(candidate.task_ids) - 1) * 5)
    if recurrence:
        reasons["recurrence"] = recurrence
    friction = min(20, candidate.friction_count * 10)
    if friction:
        reasons["friction"] = friction
    breadth = min(15, max(max(0, len(candidate.subsystems) - 1), candidate.relation_count) * 5)
    if breadth:
        reasons["system_breadth"] = breadth
    if candidate.competency_status == "needs_practice":
        reasons["needs_practice"] = 20
    elif candidate.competency_status == "developing":
        reasons["developing"] = 10
    elif candidate.competency_status == "unassessed":
        reasons["unassessed"] = 5
        if "breadth" in candidate.lanes:
            reasons["unassessed_breadth"] = 25
    elif candidate.competency_status == "mastered":
        reasons["mastered"] = -40
    if candidate.role_emphasis:
        reasons["role_emphasis"] = 10
    request_terms = _terms(request)
    candidate.request_match = bool(request_terms and request_terms & _terms(" ".join([candidate.title, *candidate.concepts])))
    shown_at = _last_shown(root, candidate.topic_id)
    if shown_at and _age_days(shown_at, now) is not None and _age_days(shown_at, now) <= COOLDOWN_DAYS:
        if not candidate.friction_latest_at or not _is_newer(candidate.friction_latest_at, shown_at):
            reasons["cooldown"] = -25
    candidate.score_reasons = reasons
    candidate.score = max(0, min(100, sum(reasons.values())))


def _select_topics(candidates: list[_Candidate], *, limit: int, global_scope: bool) -> list[_Candidate]:
    if not candidates:
        return []
    selected: list[_Candidate] = []
    used_ids: set[str] = set()
    used_projects: set[str] = set()
    lanes = ["now", "weak_spot", "breadth"]
    for lane in lanes:
        matches = [candidate for candidate in candidates if lane in candidate.lanes and candidate.topic_id not in used_ids]
        not_mastered = [candidate for candidate in matches if candidate.competency_status != "mastered"]
        matches = not_mastered or matches
        if global_scope:
            fresh_project = [candidate for candidate in matches if candidate.project.project_id not in used_projects]
            matches = fresh_project or matches
        if not matches:
            continue
        chosen = matches[0]
        selected.append(chosen)
        used_ids.add(chosen.topic_id)
        used_projects.add(chosen.project.project_id)
        if len(selected) >= limit:
            break
    lane_order = {"now": 0, "weak_spot": 1, "breadth": 2}
    return sorted(selected, key=lambda item: (lane_order[_display_lane(item)], *_sort_key(item)))


def _to_topic(candidate: _Candidate, *, request: str, global_scope: bool) -> LearningRecommendationTopic:
    lane = _display_lane(candidate)
    default_mode = "quiz" if lane == "weak_spot" else "review" if lane == "breadth" else "failure" if candidate.friction_count else "review"
    generated = build_learning_topic(
        task=candidate.task,
        title=candidate.title,
        why=candidate.why,
        concepts=candidate.concepts,
        files=[item.path for item in candidate.evidence if item.path],
        mode=default_mode,
        request=request,
    )
    questions = candidate.questions or generated.questions
    prompt = candidate.prompt or generated.prompt
    first = questions[0] if questions else None
    command = ["agentpack", "learn", "--topic", candidate.topic_id]
    if global_scope:
        command.extend(["--project", candidate.project.project_id])
    return LearningRecommendationTopic(
        topic_id=candidate.topic_id,
        lane=lane,
        project=candidate.project,
        title=candidate.title,
        why_now=candidate.why,
        score=candidate.score,
        score_reasons=candidate.score_reasons,
        concepts=candidate.concepts,
        evidence=candidate.evidence[:MAX_EVIDENCE],
        exercise=first.question if first else f"Explain {candidate.title} using the cited project evidence.",
        completion_check=("Cover: " + ", ".join(first.expected_points))
        if first and first.expected_points
        else "Explain the tradeoff, failure mode, and one validating check.",
        default_mode=default_mode,
        prompt=prompt,
        questions=questions,
        mastery_status=candidate.mastery_status,
        competency_id=candidate.competency_id or "implementation",
        competency_status=candidate.competency_status,
        target_level=candidate.target_level,
        proof_requirement=candidate.proof_requirement,
        required_artifact=candidate.required_artifact,
        start_command=shlex.join(command),
    )


def _mastery_index(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for session in read_learning_sessions(root, limit=MAX_SESSION_ROWS):
        status = derive_mastery_status(session)
        keys = [
            session.topic_id,
            _normalize(session.topic),
            *[_normalize(value) for value in session.concepts],
        ]
        for key in keys:
            if key:
                result[key] = status
    return result


def _candidate_mastery(candidate: _Candidate, mastery: dict[str, str]) -> str:
    if candidate.topic_id in mastery:
        return mastery[candidate.topic_id]
    statuses = [mastery.get(_normalize(value)) for value in [candidate.title, *candidate.concepts]]
    rank = {"mastered": 0, "unassessed": 1, "developing": 2, "needs_practice": 3}
    return max(
        (value for value in statuses if value),
        key=lambda value: rank[value],
        default="unassessed",
    )


def _last_shown(root: Path, topic_id: str) -> str:
    for event in reversed(read_events(root, limit=MAX_ARTIFACT_ROWS)):
        if event.get("type") == "learning_recommendation_shown" and str(event.get("topic_id") or "") == topic_id:
            return str(event.get("occurred_at") or event.get("timestamp") or "")
    return ""


def _resolve_project_root(root: Path, project_id_value: str) -> Path | None:
    if not project_id_value or project_id(root) == project_id_value:
        return root
    for row in load_project_index():
        if str(row.get("project_id") or "") == project_id_value:
            candidate = Path(str(row.get("path") or "")).expanduser().resolve()
            return candidate if candidate.is_dir() else None
    return None


def _project_ref(root: Path, row: dict[str, Any] | None = None) -> LearningProjectRef:
    data = row or {}
    return LearningProjectRef(
        project_id=str(data.get("project_id") or project_id(root)),
        name=str(data.get("name") or root.name or root),
        root=str(root),
    )


def _record_value(record: dict[str, Any], key: str) -> Any:
    return record.get(key) if key in record else (record.get("payload") or {}).get(key)


def _event_value(record: dict[str, Any], key: str) -> str:
    value = _record_value(record, key)
    return str(value or "")


def _concepts(task: str, paths: list[str], provided: list[str] | None = None) -> list[str]:
    values = _unique([value for value in provided or [] if value])
    haystack = "\n".join([task, *paths]).lower()
    values.extend(concept for concept, needles in CONCEPT_RULES if any(needle in haystack for needle in needles))
    values = _unique(values)
    if values:
        return values[:4]
    fallback = [term for term in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", task.lower()) if term not in _STOP_WORDS]
    return [" ".join(fallback[:2])] if fallback else []


def _subject(concepts: list[str], subsystems: set[str]) -> str:
    concept = _normalize(concepts[0] if concepts else "project behavior")
    suffix = ":" + sorted(subsystems)[0] if subsystems else ""
    return f"concept:{concept}{suffix}"


def _topic_title(concept: str) -> str:
    return _TOPIC_TITLES.get(
        concept,
        " ".join(part.capitalize() for part in re.split(r"[\s_-]+", concept) if part),
    )


def _specific_title(concept: str, subsystems: set[str]) -> str:
    base = _topic_title(concept)
    if not subsystems:
        return base
    return f"{base} in {_display_subsystem(sorted(subsystems)[0])}"


def _subsystems(paths: list[str]) -> set[str]:
    result: set[str] = set()
    for raw in paths:
        parts = [part for part in raw.replace("\\", "/").split("/") if part]
        if not parts:
            continue
        directories = parts[:-1] if "." in parts[-1] else parts
        if not directories:
            result.add(Path(parts[0]).stem)
        elif directories[0] in {"src", "tests", "frontend", "apps", "packages"}:
            result.add("/".join(directories[:3]))
        elif len(directories) >= 2:
            result.add("/".join(directories[:2]))
        else:
            result.add(directories[0])
    return result


def _display_subsystem(value: str) -> str:
    return value.rsplit("/", 1)[-1].replace("_", " ").replace("-", " ").title()


def _display_lane(candidate: _Candidate) -> str:
    for lane in ("weak_spot", "breadth", "now"):
        if lane in candidate.lanes:
            return lane
    return "now"


def _sort_key(candidate: _Candidate) -> tuple[Any, ...]:
    return (
        -(candidate.score + (10 if candidate.request_match else 0)),
        -candidate.score,
        _reverse_timestamp(candidate.latest_at),
        candidate.project.name.lower(),
        candidate.topic_id,
    )


def _reverse_timestamp(value: str) -> float:
    parsed = _parse_datetime(value)
    return -parsed.timestamp() if parsed else 0.0


def _within_window(value: str, now: datetime) -> bool:
    age = _age_days(value, now)
    return age is None or age <= WINDOW_DAYS


def _age_days(value: str, now: datetime) -> int | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0, (now - parsed).days)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_newer(candidate: str, baseline: str) -> bool:
    candidate_at = _parse_datetime(candidate)
    baseline_at = _parse_datetime(baseline)
    return bool(candidate_at and baseline_at and candidate_at > baseline_at)


def _read_jsonl(path: Path, *, limit: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return [], 1
    rows: list[dict[str, Any]] = []
    errors = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            errors += 1
    return rows, errors


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", value.lower()) if term not in _STOP_WORDS}


def _normalize(value: str) -> str:
    return "-".join(re.findall(r"[a-zA-Z0-9]+", str(value).lower()))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
