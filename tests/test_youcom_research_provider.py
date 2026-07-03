from __future__ import annotations

from scripts.youcom_research_provider import build_prompt


def test_build_prompt_includes_task_and_files() -> None:
    prompt = build_prompt(
        {
            "task": "add web-grounded learning notes",
            "current_report": {
                "source_files": [
                    {"path": "src/agentpack/commands/learn.py", "concepts": ["provider command"]},
                    {"path": "src/agentpack/learning/provider.py", "concepts": ["JSON command"]},
                ]
            },
        }
    )

    assert "Task: add web-grounded learning notes" in prompt
    assert "src/agentpack/commands/learn.py (provider command)" in prompt
    assert "src/agentpack/learning/provider.py (JSON command)" in prompt
    assert "live web sources" in prompt
