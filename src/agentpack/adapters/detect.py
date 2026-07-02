from __future__ import annotations

import os
from pathlib import Path


def detect_agent(root: Path) -> str:
    """Infer the target agent from environment and project files.

    Priority:
      1. AGENTPACK_AGENT env var (explicit override)
      2. CLAUDECODE / CLAUDE_CODE_ENTRYPOINT env → claude
      3. Codex env markers → codex
      4. ANTIGRAVITY env var present → antigravity
      5. Codex project markers → codex
      6. .agent/skills/ dir exists → antigravity
      7. GEMINI.md exists → antigravity
      8. .cursor/ dir or .cursorrules exists → cursor
      9. .windsurfrules exists → windsurf
      10. Fallback → claude
    """
    if override := os.environ.get("AGENTPACK_AGENT"):
        return override

    # Host env wins over repository files. Multi-agent repos can contain every
    # rule file, so project artifacts are only fallback hints.
    # Claude Code sets CLAUDECODE=1 and CLAUDE_CODE_ENTRYPOINT in its shell env
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"

    if (
        os.environ.get("OPENAI_CODEX")
        or os.environ.get("CODEX_ENVIRONMENT")
        or os.environ.get("CODEX_SHELL")
        or os.environ.get("CODEX_CI")
        or os.environ.get("CODEX_THREAD_ID")
    ):
        return "codex"

    if os.environ.get("ANTIGRAVITY"):
        return "antigravity"

    if _has_codex_project_markers(root):
        return "codex"

    if (root / ".agent" / "skills").is_dir():
        return "antigravity"

    if (root / "GEMINI.md").exists():
        return "antigravity"

    if (root / ".cursor").is_dir() or (root / ".cursorrules").exists():
        return "cursor"

    if (root / ".windsurfrules").exists():
        return "windsurf"

    return "claude"


def _has_codex_project_markers(root: Path) -> bool:
    return (
        (root / ".codex").is_dir()
        or (root / ".codex-plugin" / "plugin.json").exists()
        or (root / ".codexignore").exists()
    )
