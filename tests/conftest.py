from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_agentpack_session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "AGENTPACK_THREAD_ID",
        "CODEX_THREAD_ID",
        "CLAUDE_SESSION_ID",
        "CURSOR_SESSION_ID",
        "WINDSURF_SESSION_ID",
        "ANTIGRAVITY_SESSION_ID",
        "GEMINI_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
