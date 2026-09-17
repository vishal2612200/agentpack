from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.you.com/v1/research"
DEFAULT_EFFORT = "standard"


def build_prompt(payload: dict[str, Any]) -> str:
    task = str(payload.get("task", "")).strip()
    current = payload.get("current_report") or {}
    source_files = current.get("source_files") or []

    parts: list[str] = []
    if task:
        parts.append(f"Task: {task}")
    if source_files:
        files = ", ".join(
            f'{item.get("path", "").strip()} ({", ".join(item.get("concepts", [])[:3])})'.strip()
            for item in source_files
            if isinstance(item, dict) and item.get("path")
        )
        if files:
            parts.append(f"Changed files: {files}")
    parts.append(
        "Use live web sources to surface any relevant docs, maintainer guidance, or implementation details "
        "that would help a developer work safely on this task. Keep the answer concise and practical."
    )
    return "\n\n".join(parts)


def enrich_with_research(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("YDC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("YDC_API_KEY environment variable is required")

    research_body = json.dumps({"input": build_prompt(payload), "research_effort": DEFAULT_EFFORT}).encode("utf-8")
    request = Request(
        API_URL,
        data=research_body,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"You.com Research API error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"You.com Research API error: {exc.reason}") from exc

    data = json.loads(raw)
    content = ((data.get("output") or {}).get("content") or "").strip()
    sources = (data.get("output") or {}).get("sources") or []
    if not content:
        raise RuntimeError("You.com Research API returned no content")

    summary = [content.splitlines()[0].lstrip("# ").strip() or "Live research notes"]
    if sources:
        summary.append(f"Live sources reviewed: {len(sources)}")

    topics: list[dict[str, Any]] = []
    if sources:
        topics.append(
            {
                "title": "Live research follow-up",
                "why": "Use the cited sources to confirm external implementation guidance before changing the code.",
                "prompt": content[:1200],
                "files": [item.get("url", "") for item in sources if isinstance(item, dict) and item.get("url")],
                "concepts": ["live web research", "cited sources"],
            }
        )

    return {
        "summary": summary,
        "learning_topics": topics,
        "concepts": ["live web research", "cited sources"],
        "next_practice": "Use the cited sources as a quick sanity check before implementing any externally documented behavior.",
    }


def main() -> int:
    payload = json.load(sys.stdin)
    override = enrich_with_research(payload)
    json.dump(override, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
