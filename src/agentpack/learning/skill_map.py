from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentpack.learning.models import SkillEvidence, SkillProgress


def update_skill_map(path: Path, evidence: list[SkillEvidence]) -> dict:
    payload = _read_payload(path)
    payload["schema_version"] = 2
    skills = payload.setdefault("skills", {})
    now = datetime.now(timezone.utc).isoformat()
    for item in evidence:
        current = SkillProgress.model_validate(
            skills.get(item.skill, {"skill": item.skill, "task_count": 0, "evidence": []})
        )
        if current.suppressed:
            continue
        current.task_count += 1
        current.last_task = item.task
        current.first_seen = current.first_seen or now
        current.last_seen = now
        current.confidence = _confidence(current.confidence, item.confidence, current.task_count)
        current.evidence.insert(0, item)
        current.evidence = current.evidence[:10]
        current.source_paths = _merge_unique(current.source_paths, item.evidence_files, limit=20)
        current.related_tests = _merge_unique(
            current.related_tests,
            [path for path in item.evidence_files if path.startswith("tests/") or "/test" in path],
            limit=20,
        )
        skills[item.skill] = current.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def read_skill_map(path: Path) -> dict:
    return _read_payload(path)


def render_skill_summary(path: Path, *, limit: int = 10) -> str:
    payload = _read_payload(path)
    skills = [
        SkillProgress.model_validate(value)
        for value in payload.get("skills", {}).values()
        if not value.get("suppressed", False)
    ]
    skills.sort(key=lambda item: (item.confidence, item.task_count, item.last_seen), reverse=True)
    lines = [
        "# AgentPack Skill Memory",
        "",
        "Confidence here measures observed exposure from project evidence, not demonstrated mastery.",
        "",
    ]
    if not skills:
        lines.append("No skill evidence captured yet.")
        return "\n".join(lines) + "\n"
    for skill in skills[:limit]:
        lines.append(f"- {skill.skill}: exposure confidence {skill.confidence}, {skill.task_count} task(s)")
        if skill.last_task:
            lines.append(f"  Last task: {skill.last_task}")
        if skill.source_paths:
            lines.append("  Evidence: " + ", ".join(f"`{path}`" for path in skill.source_paths[:3]))
    lines.append("")
    return "\n".join(lines)


def recommend_practice_drills(path: Path, *, limit: int = 5) -> list[str]:
    payload = _read_payload(path)
    skills = [
        SkillProgress.model_validate(value)
        for value in payload.get("skills", {}).values()
        if not value.get("suppressed", False)
    ]
    skills.sort(key=lambda item: (item.confidence, item.task_count, item.last_seen))
    drills: list[str] = []
    for skill in skills[:limit]:
        evidence = skill.source_paths[0] if skill.source_paths else "the changed files"
        drills.append(
            f"Explain {skill.skill} using `{evidence}`, then add or identify one behavior-level regression test."
        )
    return drills


def apply_skill_feedback(path: Path, *, target: str, action: str, replacement: str = "", note: str = "") -> dict:
    payload = _read_payload(path)
    skills = payload.setdefault("skills", {})
    if action == "suppress" and target in skills:
        current = SkillProgress.model_validate(skills[target])
        current.suppressed = True
        if note:
            current.accepted_corrections.insert(0, note)
        skills[target] = current.model_dump(mode="json")
    elif action == "rename" and target in skills and replacement:
        current = SkillProgress.model_validate(skills.pop(target))
        current.aliases = _merge_unique(current.aliases, [current.skill], limit=10)
        current.skill = replacement
        if note:
            current.accepted_corrections.insert(0, note)
        skills[replacement] = current.model_dump(mode="json")
    elif action == "merge" and target in skills and replacement:
        source = SkillProgress.model_validate(skills.pop(target))
        destination = SkillProgress.model_validate(
            skills.get(replacement, {"skill": replacement, "task_count": 0, "evidence": []})
        )
        destination.aliases = _merge_unique(destination.aliases, [source.skill, *source.aliases], limit=20)
        destination.task_count += source.task_count
        destination.evidence = (source.evidence + destination.evidence)[:10]
        destination.source_paths = _merge_unique(destination.source_paths, source.source_paths, limit=20)
        destination.related_tests = _merge_unique(destination.related_tests, source.related_tests, limit=20)
        destination.confidence = max(destination.confidence, source.confidence)
        destination.last_task = destination.last_task or source.last_task
        destination.first_seen = destination.first_seen or source.first_seen
        destination.last_seen = max(destination.last_seen, source.last_seen)
        if note:
            destination.accepted_corrections.insert(0, note)
        skills[replacement] = destination.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _read_payload(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 2, "skills": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _confidence(current: int, new: int, task_count: int) -> int:
    if not current:
        return min(100, new)
    reinforced = max(current, new) + min(10, task_count)
    return min(100, reinforced)


def _merge_unique(existing: list[str], new: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *new]:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result[:limit]
