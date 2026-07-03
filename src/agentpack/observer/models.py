from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ObserverEventType = Literal[
    "task_memory",
    "route",
    "learn",
    "learn_feedback",
    "review_preflight",
    "review_outcome",
]


class ObserverEntity(BaseModel):
    kind: str
    id: str
    path: str = ""
    symbol: str = ""
    source: str = ""


class ObserverEdge(BaseModel):
    source: str
    target: str
    kind: str
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class ObserverEvent(BaseModel):
    type: str
    timestamp: str
    task: str = ""
    source: str = ""
    repo: str = ""
    branch: str = ""
    git_sha: str = ""
    outcome: str = ""
    confidence: float = 0.0
    entities: list[ObserverEntity] = Field(default_factory=list)
    edges: list[ObserverEdge] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class ObserverInsight(BaseModel):
    kind: str
    title: str
    detail: str
    action: str
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    related_files: list[str] = Field(default_factory=list)
    source_events: list[str] = Field(default_factory=list)


class ObserverBrief(BaseModel):
    generated_at: str
    task: str = ""
    insights: list[ObserverInsight] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class ObserverPrior(BaseModel):
    path: str
    reason: str
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    event_count: int = 0
    task: str = ""
