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
    entities: list["DashboardV2ImpactEntity"] = Field(default_factory=list)
    relationships: list["DashboardV2ImpactRelationship"] = Field(default_factory=list)
    scene: "DashboardV2ImpactScene" = Field(default_factory=lambda: DashboardV2ImpactScene())


class DashboardV2EvidenceItem(BaseModel):
    kind: str
    path: str = ""
    start_line: int = 0
    end_line: int = 0
    source: str = ""
    source_hash: str = ""
    note: str = ""


class DashboardV2ImpactEntity(BaseModel):
    id: str
    kind: Literal["file", "symbol", "test", "action", "external"]
    label: str
    path: str = ""
    line: int = 0
    parent_id: str = ""
    confidence_tier: str = ""
    task_relevant: bool = False
    risk: str = "unknown"
    reasons: list[str] = Field(default_factory=list)
    related_ids: list[str] = Field(default_factory=list)
    evidence: list[DashboardV2EvidenceItem] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class DashboardV2ImpactRelationship(BaseModel):
    id: str
    source_id: str
    target_id: str
    relationship: str
    confidence_tier: str = ""
    strength: float = 0.0
    task_relevant: bool = False
    evidence: list[DashboardV2EvidenceItem] = Field(default_factory=list)


class DashboardV2ImpactScene(BaseModel):
    available: bool = False
    unavailable_reason: str = ""
    entities: list[DashboardV2ImpactEntity] = Field(default_factory=list)
    relationships: list[DashboardV2ImpactRelationship] = Field(default_factory=list)


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
    worktree: str = ""


class DashboardV2Agents(BaseModel):
    handoffs: list[DashboardV2Handoff] = Field(default_factory=list)
    sessions: list[DashboardV2AgentSession] = Field(default_factory=list)
    threads: list[dict[str, Any]] = Field(default_factory=list)
    integrations: list[dict[str, Any]] = Field(default_factory=list)
    mcp_health: dict[str, Any] = Field(default_factory=dict)


class DashboardV2AgentsResponse(BaseModel):
    schema_version: Literal[2] = 2
    agents: DashboardV2Agents


class DashboardV2ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: str = Field(min_length=1)
    cwd: str = ""
    agent: str = ""
    thread: str = ""
    task: str = ""
    target: str = ""
    path: str = ""
    mode: str = ""
    budget: int | None = Field(default=None, ge=1)
    status: str = ""
    summary: str = ""
    thread_id: str = ""
    older_than: str = ""
    refresh: bool = False
    guard: bool = False
    global_: bool = Field(default=False, alias="global")
    confirmed: bool = False


class DashboardV2HandoffOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=48)


class ProjectMilestoneInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=64)
    title: str = Field(min_length=1, max_length=160)
    owner: str = Field(default="", max_length=120)
    due_date: str = Field(default="", max_length=10)


class ProjectOutcomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=64)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    owner: str = Field(default="", max_length=120)
    target_date: str = Field(default="", max_length=10)
    milestones: list[ProjectMilestoneInput] = Field(default_factory=list, max_length=100)


class ProjectProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=160)
    purpose: str | None = Field(default=None, max_length=2000)
    audiences: list[str] | None = Field(default=None, max_length=20)
    owners: list[str] | None = Field(default=None, max_length=20)
    stage: str | None = Field(default=None, max_length=32)
    links: dict[str, str] | None = Field(default=None, max_length=20)
    environments: list[str] | None = Field(default=None, max_length=20)
    status_stale_days: int | None = Field(default=None, ge=1, le=3650)
    outcomes: list[ProjectOutcomeInput] | None = Field(default=None, max_length=50)


class ProjectProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mutation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    expected_revision: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    profile: ProjectProfilePatch


class ProjectEventEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64)
    ref: str = Field(default="", max_length=240)
    summary: str = Field(default="", max_length=500)
    path: str = Field(default="", max_length=500)


class ProjectEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal[
        "project_outcome_status",
        "project_milestone_status",
        "project_risk_upsert",
        "project_decision_recorded",
        "project_initiative_confirmed",
        "project_initiative_dismissed",
    ]
    mutation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    entity_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    status: str = Field(default="", max_length=32)
    title: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=2000)
    owner: str = Field(default="", max_length=120)
    severity: str = Field(default="", max_length=16)
    mitigation: str = Field(default="", max_length=2000)
    context: str = Field(default="", max_length=2000)
    decision: str = Field(default="", max_length=2000)
    outcome_id: str = Field(default="", max_length=64)
    evidence: list[ProjectEventEvidenceInput] = Field(default_factory=list, max_length=20)


class DashboardV2AgentOperationResponse(BaseModel):
    schema_version: Literal[2] = 2
    handoff: DashboardV2Handoff
    warnings: list[str] = Field(default_factory=list)


class DashboardV2ActionRunResponse(BaseModel):
    schema_version: Literal[2] = 2
    session: dict[str, Any]
    command: str


class DashboardV2UnavailableState(BaseModel):
    kind: Literal[
        "stale_context",
        "tree_sitter_unavailable",
        "mcp_unavailable",
        "permission_denied",
        "repository_mismatch",
        "action_conflict",
        "webgl_unavailable",
        "server_error",
    ]
    title: str
    detail: str = ""
    next_action: str = ""
    retryable: bool = False


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


class DashboardV2ActionInspectionResponse(BaseModel):
    schema_version: Literal[2] = 2
    inspection: DashboardV2ActionInspection


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
