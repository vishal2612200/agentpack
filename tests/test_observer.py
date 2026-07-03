from __future__ import annotations

import json

from agentpack.observer.brief import build_observer_brief, render_observer_brief_markdown, write_observer_brief
from agentpack.observer.events import read_observations, record_learning_observation, record_task_observation
from agentpack.observer.priors import observer_notes_for_task, observer_route_priors


def test_task_observation_records_counterfactual_and_brief(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def refresh():\n    return True\n", encoding="utf-8")
    payload = {
        "task": "fix auth refresh",
        "stage": "finish",
        "status": "done",
        "changed_files": ["src/auth.py"],
        "selected_files": [],
        "concepts": ["authentication"],
        "tests": [],
    }

    record_task_observation(tmp_path, payload)
    brief_path = write_observer_brief(tmp_path, task="fix auth refresh")

    observations = read_observations(tmp_path)
    assert observations[0]["type"] == "task_memory"
    assert observations[0]["payload"]["selected_misses"] == ["src/auth.py"]
    assert brief_path.exists()

    brief = build_observer_brief(tmp_path, task="fix auth refresh")
    assert any(insight.kind == "counterfactual" for insight in brief.insights)
    assert any("src/auth.py" in insight.related_files for insight in brief.insights)
    assert "Observer signals are advisory" in render_observer_brief_markdown(brief)


def test_observer_route_priors_use_similar_task_history(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "route.py").write_text("def route():\n    return 'ok'\n", encoding="utf-8")
    record_task_observation(
        tmp_path,
        {
            "task": "improve route explainability",
            "stage": "finish",
            "status": "done",
            "changed_files": ["src/route.py"],
            "selected_files": [],
            "concepts": ["routing"],
            "tests": [],
        },
    )

    priors = observer_route_priors(tmp_path, "route diagnostics")
    notes = observer_notes_for_task(tmp_path, "route diagnostics")

    assert priors[0].path == "src/route.py"
    assert priors[0].confidence > 0
    assert notes[0]["path"] == "src/route.py"
    assert notes[0]["reason"] == "changed or selected in similar prior work"


def test_learning_observation_adds_learning_signal(tmp_path) -> None:
    record_learning_observation(
        tmp_path,
        task="learn review flow",
        concepts=["review", "citations"],
        selected_hits=2,
        selected_misses=1,
        learning_request="quiz me",
        learning_sessions=1,
    )

    brief = build_observer_brief(tmp_path, task="learn review flow")

    assert json.loads((tmp_path / ".agentpack" / "observer-events.jsonl").read_text(encoding="utf-8"))["type"] == "learn"
    assert any(insight.kind == "learning" for insight in brief.insights)
