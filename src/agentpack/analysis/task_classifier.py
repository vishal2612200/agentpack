from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskClassification:
    kind: str
    confidence: float
    signals: list[str]


_TASK_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("bugfix", ("fix", "bug", "broken", "regression", "debug", "issue", "error", "failing", "fail")),
    ("refactor", ("refactor", "cleanup", "clean", "simplify", "restructure", "rename")),
    ("docs", ("readme", "doc", "docs", "documentation", "changelog", "guide")),
    ("release", ("release", "publish", "version", "bump", "tag", "pypi", "npm")),
    ("test", ("test", "tests", "coverage", "pytest", "jest", "spec", "eval", "benchmark")),
    ("ui", ("ui", "frontend", "component", "page", "screen", "css", "style", "layout")),
    ("infra", ("ci", "workflow", "docker", "deploy", "build", "package", "hook", "mcp")),
    ("audit", ("audit", "review", "inspect", "scan", "verify", "doctor", "status")),
    ("feature", ("add", "implement", "create", "support", "enable", "new")),
]


def classify_task(task: str) -> TaskClassification:
    """Classify a task into a coarse bucket for ranking/context hints."""
    text = task.lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9_-]*", text))
    scores: dict[str, tuple[int, list[str]]] = {}
    for kind, pattern_signals in _TASK_PATTERNS:
        hits = [signal for signal in pattern_signals if signal in words or signal in text]
        if hits:
            scores[kind] = (len(hits), hits[:5])

    if not scores:
        return TaskClassification(kind="general", confidence=0.2, signals=[])

    kind, (count, signals) = max(scores.items(), key=lambda item: (item[1][0], _priority(item[0])))
    total_hits = sum(value[0] for value in scores.values())
    confidence = min(0.95, 0.45 + (count / max(1, total_hits)) * 0.45)
    return TaskClassification(kind=kind, confidence=round(confidence, 2), signals=signals)


def _priority(kind: str) -> int:
    order = [name for name, _signals in _TASK_PATTERNS]
    return len(order) - order.index(kind)
