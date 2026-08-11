from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PullRequestTarget:
    number: str
    source: str


_PR_URL = re.compile(r"github\.com/[^/\s]+/[^/\s]+/pull/(\d+)", re.IGNORECASE)
_PR_NUMBER = re.compile(r"\b(?:pr|pull\s+request)\s*#?\s*(\d+)\b", re.IGNORECASE)
_PR_WORD = re.compile(r"\b(?:pr|pull\s+request)\b", re.IGNORECASE)


def pull_request_target(task: str) -> PullRequestTarget | None:
    url_match = _PR_URL.search(task)
    if url_match:
        return PullRequestTarget(url_match.group(1), "url")
    number_match = _PR_NUMBER.search(task)
    if number_match:
        return PullRequestTarget(number_match.group(1), "number")
    return None


def is_explicit_pull_request_task(task: str) -> bool:
    return bool(pull_request_target(task) or _PR_WORD.search(task))
