"""Typed, versioned dashboard v2 contracts.

The JSON schema in ``docs/schemas/dashboard-v2.schema.json`` is the public
wire contract. These models keep the server implementation and tests typed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentpack.dashboard.models import (
    ActionHistoryRow,
    DashboardGraph,
    DashboardMap,
    DashboardSnapshot,
)


class DashboardV2Workspace(BaseModel):
    project: dict[str, Any]
    workspace: dict[str, Any] | None = None
    task: dict[str, Any]
    context: dict[str, Any]


class DashboardV2Impact(BaseModel):
    schema_version: int
    available: bool
    entity_count: int = 0
    edge_count: int = 0
    unresolved_count: int = 0
    capabilities: dict[str, str] = Field(default_factory=dict)


class DashboardV2ImpactResponse(BaseModel):
    schema_version: Literal[2] = 2
    query: str = ""
    relationship: str = ""
    language: str = ""
    confidence: str = ""
    available: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
    affected_tests: list[dict[str, Any]] = Field(default_factory=list)


class DashboardV2Handoff(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    status: str
    created_at: str = ""
    updated_at: str = ""
    source_provider: str = ""
    source_session_id: str = ""
    target_provider: str = ""
    target_session_id: str = ""
    task: str = ""
    summary: str = ""
    next_action: str = ""
    claim_provider: str = ""
    claim_session_id: str = ""


class DashboardV2AgentSession(BaseModel):
    provider: str
    session_id: str = ""
    thread_id: str = ""
    task: str = ""
    status: str = "unknown"
    context_status: str = "unknown"
    updated_at: str = ""


class DashboardV2Agents(BaseModel):
    handoffs: list[DashboardV2Handoff] = Field(default_factory=list)
    sessions: list[DashboardV2AgentSession] = Field(default_factory=list)
    threads: list[dict[str, Any]] = Field(default_factory=list)
    integrations: list[dict[str, Any]] = Field(default_factory=list)
    mcp_health: dict[str, Any] = Field(default_factory=dict)


class DashboardV2Evidence(BaseModel):
    schema_version: Literal[2] = 2
    context: dict[str, Any] = Field(default_factory=dict)
    selected_files: list[dict[str, Any]] = Field(default_factory=list)
    task_map: list[dict[str, Any]] = Field(default_factory=list)
    observer: dict[str, Any] = Field(default_factory=dict)
    timeline: list[dict[str, Any]] = Field(default_factory=list)


class DashboardV2Action(BaseModel):
    id: str
    label: str
    command: str = ""
    description: str = ""
    risk: str = "low"
    confirm_required: bool = False


class DashboardV2Actions(BaseModel):
    schema_version: Literal[2] = 2
    suggested: list[dict[str, Any]] = Field(default_factory=list)
    catalog: list[dict[str, Any]] = Field(default_factory=list)


class DashboardV2ActionInspection(BaseModel):
    schema_version: Literal[2] = 2
    action: str
    command: str
    cwd: str
    purpose: str = ""
    risk: str = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    affected_paths: list[str] = Field(default_factory=list)
    expected_effect: str = ""
    confirm_required: bool = False
    allowed: bool = True


class DashboardV2Error(BaseModel):
    schema_version: Literal[2] = 2
    error: str
    kind: str = "server_error"
    retryable: bool = False
    detail: str = ""


class DashboardV2Payload(BaseModel):
    schema_version: Literal[2] = 2
    detail: Literal["home", "full"]
    snapshot: DashboardSnapshot
    graph: DashboardGraph
    map: DashboardMap
    action_history: list[ActionHistoryRow] = Field(default_factory=list)
    workspace: DashboardV2Workspace
    agents: DashboardV2Agents
    impact: DashboardV2Impact
