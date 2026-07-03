from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.observer.events import DEFAULT_OBSERVER_BRIEF_PATH, read_observations
from agentpack.observer.models import ObserverBrief, ObserverInsight
from agentpack.observer.priors import observer_route_priors

MAX_INSIGHTS = 8


def build_observer_brief(root: Path, *, task: str = "", limit: int = 500) -> ObserverBrief:
    events = read_observations(root, limit=limit)
    insights = [
        *_missed_context_insights(events),
        *_test_gap_insights(events),
        *_review_insights(events),
        *_learning_insights(events),
        *_prior_insights(root, task),
    ]
    insights = sorted(insights, key=lambda item: (-item.confidence, item.kind, item.title))[:MAX_INSIGHTS]
    stats = {
        "events": len(events),
        "types": dict(Counter(str(event.get("type") or "unknown") for event in events)),
    }
    return ObserverBrief(
        generated_at=datetime.now(timezone.utc).isoformat(),
        task=task,
        insights=insights,
        stats=stats,
    )


def write_observer_brief(root: Path, *, task: str = "", output_path: str = DEFAULT_OBSERVER_BRIEF_PATH) -> Path:
    path = root / output_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_observer_brief_markdown(build_observer_brief(root, task=task)), encoding="utf-8")
    return path


def render_observer_brief_markdown(brief: ObserverBrief) -> str:
    lines = [
        "# AgentPack Observer Brief",
        "",
        f"Generated: {brief.generated_at}",
    ]
    if brief.task:
        lines.append(f"Task: {brief.task}")
    lines += [
        "",
        "Observer signals are advisory. Verify source files and diffs before acting.",
        "",
        "## Signals",
    ]
    if not brief.insights:
        lines.append("- No observer signals yet.")
    for insight in brief.insights:
        evidence = ", ".join(insight.evidence[:3]) if insight.evidence else "no direct evidence"
        files = ", ".join(insight.related_files[:5]) if insight.related_files else "none"
        lines.extend([
            f"- {insight.title} ({insight.kind}, confidence {insight.confidence:.2f})",
            f"  - Detail: {insight.detail}",
            f"  - Action: {insight.action}",
            f"  - Files: {files}",
            f"  - Evidence: {evidence}",
        ])
    lines += [
        "",
        "## Stats",
        f"- Events: {brief.stats.get('events', 0)}",
    ]
    types = brief.stats.get("types")
    if isinstance(types, dict):
        for event_type, count in sorted(types.items()):
            lines.append(f"- {event_type}: {count}")
    return "\n".join(lines) + "\n"


def _missed_context_insights(events: list[dict[str, Any]]) -> list[ObserverInsight]:
    counter: Counter[str] = Counter()
    evidence: dict[str, list[str]] = {}
    for event in events:
        payload = _payload(event)
        for path in _str_list(payload.get("selected_misses")):
            counter[path] += 1
            evidence.setdefault(path, []).append(str(event.get("task") or ""))
    if not counter:
        return []
    paths = [path for path, _count in counter.most_common(5)]
    return [
        ObserverInsight(
            kind="counterfactual",
            title="Prior work changed files that route context missed",
            detail="These files were changed in completed task memory but were not in selected context for that task.",
            action="Inspect these files earlier on similar tasks; treat them as hypotheses, not proof.",
            confidence=min(0.82, 0.48 + counter[paths[0]] * 0.08),
            evidence=[item for path in paths for item in evidence.get(path, [])[:1]][:3],
            related_files=paths,
        )
    ]


def _test_gap_insights(events: list[dict[str, Any]]) -> list[ObserverInsight]:
    source_without_tests: list[str] = []
    for event in events:
        payload = _payload(event)
        changed = _str_list(payload.get("changed_files"))
        tests = _str_list(payload.get("tests"))
        if changed and not tests:
            source_without_tests.extend(path for path in changed if not path.startswith("tests/"))
    if not source_without_tests:
        return []
    common = [path for path, _count in Counter(source_without_tests).most_common(5)]
    return [
        ObserverInsight(
            kind="test_gap",
            title="Recent task memory shows changed files without test changes",
            detail="The observer saw implementation files change while no test paths were recorded.",
            action="Look for focused tests or document why manual validation is enough before closing similar work.",
            confidence=0.58,
            evidence=common[:3],
            related_files=common,
        )
    ]


def _review_insights(events: list[dict[str, Any]]) -> list[ObserverInsight]:
    review_events = [event for event in events if event.get("type") == "review_outcome"]
    findings = sum(int(_payload(event).get("findings_count") or 0) for event in review_events)
    if findings <= 0:
        return []
    files = [
        path
        for event in review_events
        for path in _str_list(_payload(event).get("changed_files"))
    ]
    return [
        ObserverInsight(
            kind="review_risk",
            title="Recent review outcomes found issues in similar flow",
            detail=f"Review checks recorded {findings} finding(s) across recent observer events.",
            action="Use review findings as risk hints, then re-check the current diff directly.",
            confidence=min(0.8, 0.5 + findings * 0.08),
            evidence=[str(event.get("outcome") or "") for event in review_events[:3]],
            related_files=[path for path, _count in Counter(files).most_common(5)],
        )
    ]


def _learning_insights(events: list[dict[str, Any]]) -> list[ObserverInsight]:
    concepts = Counter(
        concept
        for event in events
        if event.get("type") == "learn"
        for concept in _str_list(_payload(event).get("concepts"))
    )
    if not concepts:
        return []
    top = [concept for concept, _count in concepts.most_common(5)]
    return [
        ObserverInsight(
            kind="learning",
            title="Learning memory has recurring concepts",
            detail="Recent learning runs repeatedly named these concepts.",
            action="Prefer skills, files, and checks that cover these concepts when the task overlaps.",
            confidence=min(0.75, 0.42 + concepts[top[0]] * 0.08),
            evidence=top[:3],
            related_files=[],
        )
    ]


def _prior_insights(root: Path, task: str) -> list[ObserverInsight]:
    if not task:
        return []
    priors = observer_route_priors(root, task, limit=5)
    if not priors:
        return []
    return [
        ObserverInsight(
            kind="route_prior",
            title="Observer found files from similar prior work",
            detail="Prior task memory points to files that may deserve early inspection.",
            action="Read these files only if they still fit the task after current source verification.",
            confidence=max(prior.confidence for prior in priors),
            evidence=[prior.task for prior in priors if prior.task][:3],
            related_files=[prior.path for prior in priors],
        )
    ]


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]
