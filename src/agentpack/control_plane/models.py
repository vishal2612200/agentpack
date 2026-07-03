from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    kind: str
    command: str
    reason: str
    why_it_matters: str
    safe_to_continue: str


class SetupSnapshot(BaseModel):
    initialized: bool
    config_path: str


class TaskSnapshot(BaseModel):
    thread_id: str | None = None
    task_path: str
    has_task: bool
    task: str = ""
    status: str = ""
    done: bool = False


class ContextSnapshot(BaseModel):
    status: Literal["fresh", "stale", "missing", "unchecked"]
    reason: str
    checked_files: bool = False
    metadata_path: str
    context_path: str = ""
    generated_at: str = ""
    packed_task: str = ""
    owner_thread_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "fresh"


class ThreadSnapshot(BaseModel):
    active_count: int = 0
    conflict_count: int = 0
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class TokenSnapshot(BaseModel):
    budget: int = 0
    estimated_tokens: int = 0
    usage_ratio: float = 0.0
    selected_count: int = 0
    mode_counts: dict[str, int] = Field(default_factory=dict)
    largest_sections: list[dict[str, Any]] = Field(default_factory=list)
    trimmed_sections: dict[str, int] = Field(default_factory=dict)
    recommended_next_context: str = ""


class LoopSnapshot(BaseModel):
    enabled: bool = False
    status: str = ""
    task: str = ""
    runner: str = ""
    blocked_reason: str = ""


class ControlPlaneSnapshot(BaseModel):
    root: str
    setup: SetupSnapshot
    task: TaskSnapshot
    context: ContextSnapshot
    threads: ThreadSnapshot = Field(default_factory=ThreadSnapshot)
    tokens: TokenSnapshot = Field(default_factory=TokenSnapshot)
    loop: LoopSnapshot = Field(default_factory=LoopSnapshot)
    skill_index_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
