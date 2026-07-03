from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from agentpack.observer.events import read_observations
from agentpack.observer.models import ObserverPrior

MAX_PRIORS = 5
_NOISY_PREFIXES = (".agentpack/", ".agent/", ".git/", ".mypy_cache/", "__pycache__/")
_NOISY_SUFFIXES = (".pyc", ".log")


def observer_route_priors(root: Path, task: str, *, limit: int = MAX_PRIORS) -> list[ObserverPrior]:
    task_terms = _terms(task)
    if not task_terms:
        return []
    events = read_observations(root, limit=500)
    scores: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    evidence: dict[str, list[str]] = defaultdict(list)
    last_task: dict[str, str] = {}

    for event in events:
        event_task = str(event.get("task") or "")
        overlap = len(task_terms & _terms(event_task))
        if overlap <= 0:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        paths = _paths_from_event(event, payload)
        for path in paths:
            if _is_noisy(path) or not (root / path).exists():
                continue
            scores[path] += overlap + _path_term_bonus(path, task_terms)
            counts[path] += 1
            last_task[path] = event_task
            if event_task and len(evidence[path]) < 3:
                evidence[path].append(event_task)

    priors: list[ObserverPrior] = []
    for path, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]:
        event_count = counts[path]
        confidence = min(0.85, 0.35 + (score * 0.08) + min(event_count, 3) * 0.08)
        priors.append(
            ObserverPrior(
                path=path,
                reason="changed or selected in similar prior work",
                confidence=round(confidence, 2),
                evidence=evidence[path][:3],
                event_count=event_count,
                task=last_task.get(path, ""),
            )
        )
    return priors


def observer_notes_for_task(root: Path, task: str, *, limit: int = MAX_PRIORS) -> list[dict[str, Any]]:
    return [
        {
            "path": prior.path,
            "reason": prior.reason,
            "confidence": prior.confidence,
            "evidence": prior.evidence,
            "event_count": prior.event_count,
            "task": prior.task,
        }
        for prior in observer_route_priors(root, task, limit=limit)
    ]


def _paths_from_event(event: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("changed_files", "selected_misses", "selected_files"):
        paths.extend(_str_list(payload.get(key)))
    entities = event.get("entities")
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict) and isinstance(entity.get("path"), str):
                paths.append(entity["path"])
    return _unique(paths)


def _terms(value: str) -> set[str]:
    raw = "".join(ch.lower() if ch.isalnum() else " " for ch in value).split()
    return {term for term in raw if len(term) >= 3}


def _path_term_bonus(path: str, task_terms: set[str]) -> float:
    path_terms = set(path.lower().replace("/", " ").replace("_", " ").replace("-", " ").replace(".", " ").split())
    return min(3.0, float(len(path_terms & task_terms)))


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _is_noisy(path: str) -> bool:
    return path.startswith(_NOISY_PREFIXES) or path.endswith(_NOISY_SUFFIXES)
