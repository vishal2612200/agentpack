from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ContextStatus = Literal["fresh", "stale", "missing", "unknown"]
TaskState = Literal["planned", "in_progress", "blocked", "handed_off", "done", "unknown"]
DashboardTaskStatus = Literal["todo", "in_progress", "needs_attention", "done"]
SkillFeedbackStatus = Literal[
    "none",
    "recommended_only",
    "used_helpful",
    "used_noisy",
    "ignored",
    "bad_recommendation",
]
McpHealthStatus = Literal["healthy", "warning", "missing", "unknown"]
McpLiveExposure = Literal["confirmed", "unknown"]
DashboardNodeType = Literal["task", "file", "symbol", "test", "episode", "procedure", "action"]
DashboardEdgeType = Literal[
    "selected_because",
    "omitted_because",
    "imports",
    "tested_by",
    "memory_influenced",
    "procedure_applies",
    "may_break",
    "retrieve_ref",
]
ProjectRecordSource = Literal["declared", "observed", "inferred"]
ProjectStage = Literal["idea", "planning", "active", "maintenance", "paused", "complete"]
ProjectOutcomeStatus = Literal["planned", "on_track", "at_risk", "achieved", "paused"]
ProjectMilestoneStatus = Literal["planned", "in_progress", "blocked", "done"]
ProjectRiskSeverity = Literal["low", "medium", "high", "critical"]
ProjectRiskStatus = Literal["open", "mitigating", "accepted", "resolved"]
ProjectDecisionStatus = Literal["proposed", "accepted", "rejected", "superseded"]
ProjectHealthStatus = Literal["healthy", "attention", "blocked", "stale", "unknown"]


class ProjectEvidence(BaseModel):
    kind: str
    ref: str = ""
    summary: str = ""
    path: str = ""
    occurred_at: str = ""
    workspace_id: str = ""


class ProjectDerivedRecord(BaseModel):
    source: ProjectRecordSource = "observed"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    updated_at: str = ""
    evidence: list[ProjectEvidence] = Field(default_factory=list)
    workspace_id: str = ""
    warnings: list[str] = Field(default_factory=list)


class ProjectWorkspace(ProjectDerivedRecord):
    workspace_id: str
    path: str
    branch: str = ""
    git_sha: str = ""
    is_current: bool = False
    read_only: bool = False


class ProjectProfile(ProjectDerivedRecord):
    project_id: str
    config_revision: str
    display_name: str = ""
    purpose: str = ""
    audiences: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    stage: str = ""
    links: dict[str, str] = Field(default_factory=dict)
    environments: list[str] = Field(default_factory=list)
    status_stale_days: int = 14


class ProjectMilestoneState(ProjectDerivedRecord):
    milestone_id: str
    outcome_id: str
    title: str
    owner: str = ""
    due_date: str = ""
    status: ProjectMilestoneStatus = "planned"


class ProjectOutcomeState(ProjectDerivedRecord):
    outcome_id: str
    title: str
    description: str = ""
    owner: str = ""
    target_date: str = ""
    status: ProjectOutcomeStatus = "planned"
    progress_pct: float | None = None
    milestones: list[ProjectMilestoneState] = Field(default_factory=list)


class ProjectInitiative(ProjectDerivedRecord):
    initiative_id: str
    suggestion_id: str = ""
    title: str
    description: str = ""
    owner: str = ""
    outcome_id: str = ""
    status: str = "confirmed"


class ProjectInitiativeSuggestion(ProjectDerivedRecord):
    suggestion_id: str
    title: str
    rationale: str
    outcome_id: str = ""
    score: int = Field(default=0, ge=0, le=100)
    task_ids: list[str] = Field(default_factory=list)


class ProjectRisk(ProjectDerivedRecord):
    risk_id: str
    title: str
    description: str = ""
    owner: str = ""
    severity: ProjectRiskSeverity = "medium"
    status: ProjectRiskStatus = "open"
    mitigation: str = ""


class ProjectDecision(ProjectDerivedRecord):
    decision_id: str
    title: str
    context: str = ""
    decision: str = ""
    owner: str = ""
    status: ProjectDecisionStatus = "proposed"


class ProjectHealthDimension(ProjectDerivedRecord):
    dimension: Literal["delivery", "validation", "architecture", "release", "context", "knowledge"]
    status: ProjectHealthStatus = "unknown"
    summary: str = ""


class ProjectHealthSnapshot(ProjectDerivedRecord):
    dimensions: list[ProjectHealthDimension] = Field(default_factory=list)


class ProjectMetrics(ProjectDerivedRecord):
    outcome_count: int = 0
    active_outcomes: int = 0
    milestone_count: int = 0
    completed_milestones: int = 0
    milestone_completion_pct: float | None = None
    open_risks: int = 0
    pending_decisions: int = 0
    confirmed_initiatives: int = 0
    recent_changes: int = 0
    evidence_coverage: float | None = None


class ProjectTimelineEvent(ProjectDerivedRecord):
    event_id: str
    kind: str
    title: str
    summary: str = ""
    entity_id: str = ""
    actor: str = ""
    git_sha: str = ""
    branch: str = ""
    tags: list[str] = Field(default_factory=list)


class ProjectStatusBrief(ProjectDerivedRecord):
    mode: Literal["summary", "engineering"]
    markdown: str
    project_id: str


class ProjectOverview(ProjectDerivedRecord):
    schema_version: int = 1
    project_id: str
    generated_at: str
    selected_workspace: str = "all"
    profile: ProjectProfile
    workspaces: list[ProjectWorkspace] = Field(default_factory=list)
    metrics: ProjectMetrics = Field(default_factory=ProjectMetrics)
    outcomes: list[ProjectOutcomeState] = Field(default_factory=list)
    initiatives: list[ProjectInitiative] = Field(default_factory=list)
    initiative_suggestions: list[ProjectInitiativeSuggestion] = Field(default_factory=list)
    risks: list[ProjectRisk] = Field(default_factory=list)
    decisions: list[ProjectDecision] = Field(default_factory=list)
    health: ProjectHealthSnapshot = Field(default_factory=ProjectHealthSnapshot)
    recent_changes: list[ProjectTimelineEvent] = Field(default_factory=list)
    partial: bool = False
    read_only: bool = False


class ProjectInfo(BaseModel):
    name: str
    path: str
    branch: str = ""
    git_sha: str = ""


class DashboardProjectRecord(BaseModel):
    schema_version: int = 1
    project_id: str
    name: str
    repository_path: str
    created_at: str = ""
    updated_at: str = ""


class DashboardWorkspaceRecord(BaseModel):
    schema_version: int = 1
    workspace_id: str
    project_id: str
    path: str
    branch: str = ""
    git_sha: str = ""
    is_current: bool = False
    updated_at: str = ""


class DashboardTaskRecord(BaseModel):
    schema_version: int = 1
    task_id: str
    project_id: str
    workspace_id: str
    title: str
    description: str = ""
    status: DashboardTaskStatus = "todo"
    status_source: str = "imported"
    created_at: str = ""
    updated_at: str = ""
    thread_ids: list[str] = Field(default_factory=list)
    source_paths: list[str] = Field(default_factory=list)
    active: bool = False
    imported: bool = False
    last_run_id: str = ""


class DashboardTaskRun(BaseModel):
    schema_version: int = 1
    run_id: str
    task_id: str
    session_id: str = ""
    agent: str = ""
    started_at: str = ""
    ended_at: str = ""
    status: str = ""
    event_ids: list[str] = Field(default_factory=list)
    context_path: str = ""
    citation_manifest_path: str = ""
    issue_references: list[str] = Field(default_factory=list)
    issue_reference_details: list[dict[str, Any]] = Field(default_factory=list)
    selected_files: list[str] = Field(default_factory=list)
    omitted_files: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    packed_tokens: int = 0
    raw_tokens: int = 0
    saving_pct: float = 0.0
    unresolved_edges: int = 0
    evidence_refs: list[str] = Field(default_factory=list)


class DashboardTimelineEvent(BaseModel):
    schema_version: int = 1
    event_id: str
    event_type: str = ""
    label: str = ""
    occurred_at: str = ""
    project_id: str = ""
    workspace_id: str = ""
    task_id: str = ""
    session_id: str = ""
    agent: str = ""
    source: str = ""
    summary: str = ""
    context_path: str = ""
    issue_references: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class DashboardFeedback(BaseModel):
    schema_version: int = 1
    feedback_id: str
    task_id: str
    run_id: str = ""
    value: Literal["helped", "partly_helped", "missed_context", "not_sure"]
    note: str = ""
    created_at: str = ""


class DashboardAnalytics(BaseModel):
    range: Literal["7d", "30d"] = "7d"
    available: bool = False
    tasks_total: int = 0
    tasks_completed: int = 0
    runs_total: int = 0
    context_packs: int = 0
    files_selected: int = 0
    files_omitted: int = 0
    packed_tokens: int = 0
    raw_tokens: int = 0
    average_saving_pct: float = 0.0
    checks_total: int = 0
    unresolved_edges: int = 0
    feedback_counts: dict[str, int] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    unavailable_reason: str = ""


class TaskInfo(BaseModel):
    text: str = ""
    state: TaskState = "unknown"
    thread_id: str | None = None


class ContextHealth(BaseModel):
    status: ContextStatus = "unknown"
    generated_at: str = ""
    mode: str = ""
    packed_tokens: int = 0
    raw_tokens: int = 0
    saving_pct: float = 0.0
    selected_files_count: int = 0
    stale_reason: str = ""
    source_command: str = ""


class SelectedFileRow(BaseModel):
    path: str
    include_mode: str = ""
    score: float = 0.0
    tokens: int = 0
    reasons: list[str] = Field(default_factory=list)


class TaskMapFileRow(BaseModel):
    path: str
    kind: str = ""
    include_mode: str = ""
    score: float = 0.0
    risk_level: str = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    why_selected: list[str] = Field(default_factory=list)
    tests_to_run: list[str] = Field(default_factory=list)
    may_break: list[str] = Field(default_factory=list)
    retrieve_ref: str = ""


class SkillRow(BaseModel):
    name: str
    path: str = ""
    confidence: float = 0.0
    score: float = 0.0
    side_effect_level: str = ""
    status: SkillFeedbackStatus = "none"
    reasons: list[str] = Field(default_factory=list)


class SkillSection(BaseModel):
    task_specific: list[SkillRow] = Field(default_factory=list)
    baseline: list[SkillRow] = Field(default_factory=list)


class SkillInventorySourceSummary(BaseModel):
    configured_path: str
    resolved_path: str
    exists: bool
    file_count: int = 0


class SkillDomainSummary(BaseModel):
    name: str
    count: int


class SkillInventoryRow(BaseModel):
    name: str
    path: str
    source: str
    domains: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    side_effect_level: str = ""
    metadata_quality: str = "inferred"
    metadata: list[str] = Field(default_factory=list)
    domain_confidence: float = 0.0
    domain_source: str = "inferred"


class SkillsInventorySummary(BaseModel):
    available: bool = False
    index_refreshed: bool = False
    index_reason: str = ""
    index_error: str = ""
    total_skills: int = 0
    total_rules: int = 0
    uncategorized_count: int = 0
    missing_metadata_count: int = 0
    duplicate_names: list[str] = Field(default_factory=list)
    sources: list[SkillInventorySourceSummary] = Field(default_factory=list)
    domains: list[SkillDomainSummary] = Field(default_factory=list)
    rows: list[SkillInventoryRow] = Field(default_factory=list)


class LearningArtifact(BaseModel):
    label: str
    path: str
    exists: bool
    excerpt: str = ""


class LearningMemory(BaseModel):
    task: str
    stage: str = ""
    status: str = ""
    branch: str = ""
    git_sha: str = ""
    concepts: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    selected_files: list[str] = Field(default_factory=list)


class LearningWeakSpot(BaseModel):
    concept: str
    count: int = 0
    mode: str = ""
    latest_task: str = ""
    latest_question: str = ""
    evidence_files: list[str] = Field(default_factory=list)


class ObserverInsightRow(BaseModel):
    kind: str
    title: str
    detail: str = ""
    action: str = ""
    confidence: float = 0.0
    related_files: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class ObserverSummary(BaseModel):
    generated_at: str = ""
    events: int = 0
    event_types: dict[str, int] = Field(default_factory=dict)
    insights: list[ObserverInsightRow] = Field(default_factory=list)
    brief_path: str = ".agentpack/observer-brief.md"


class BenchmarkSummary(BaseModel):
    latest: dict[str, Any] = Field(default_factory=dict)
    averages: dict[str, float] = Field(default_factory=dict)
    misses: list[dict[str, Any]] = Field(default_factory=list)


class ThreadSummary(BaseModel):
    active_count: int = 0
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class McpRegistration(BaseModel):
    scope: str
    path: str
    status: str = "unknown"
    detail: str = ""


class McpHealth(BaseModel):
    status: McpHealthStatus = "unknown"
    runtime_status: str = ""
    runtime_ok: bool = False
    runtime_detail: str = ""
    registered: bool = False
    registrations: list[McpRegistration] = Field(default_factory=list)
    live_exposure: McpLiveExposure = "unknown"
    expected_tools: list[str] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)


class LoopSummary(BaseModel):
    exists: bool = False
    status: str = ""
    task: str = ""
    iteration: int = 0
    max_iterations: int = 0
    runner: str = ""
    last_runner_status: str = ""
    last_verification_status: str = ""
    blocked_reason: str = ""
    failure_class: str = ""
    risk_level: str = ""
    changed_files: list[str] = Field(default_factory=list)
    diagnosis_file: str = ""
    handoff_file: str = ""
    acceptance_file: str = ""
    rollback_patch: str = ""
    runs: int = 0
    blocked_runs: int = 0
    ready_runs: int = 0
    avg_iterations: float = 0.0
    next_action: str = ""


class SuggestedAction(BaseModel):
    label: str
    command: str
    reason: str = ""


class DashboardConfigField(BaseModel):
    section: str
    key: str
    value: Any = None
    default: Any = None
    value_type: str = "unknown"
    editable: bool = False
    source: str = "effective"
    description: str = ""
    allowed_values: list[str] = Field(default_factory=list)
    doc_ref: str = ""


class DashboardConfigSection(BaseModel):
    name: str
    fields: list[DashboardConfigField] = Field(default_factory=list)


class DashboardConfigSummary(BaseModel):
    path: str = ""
    exists: bool = False
    valid: bool = True
    error: str = ""
    sections: list[DashboardConfigSection] = Field(default_factory=list)
    editable_fields: list[str] = Field(default_factory=list)


class TaskControlRow(BaseModel):
    scope: Literal["global", "thread"] = "global"
    thread_id: str | None = None
    task: str = ""
    task_path: str = ""
    state: TaskState = "unknown"
    state_path: str = ""
    status: str = ""
    summary: str = ""
    done: bool = False
    exists: bool = False


class ThreadRow(BaseModel):
    thread_id: str
    task: str = ""
    status: str = ""
    summary: str = ""
    branch: str = ""
    updated_at: str = ""
    worktree: str = ""
    selected_count: int = 0
    dirty_count: int = 0
    conflicts: list[str] = Field(default_factory=list)
    overlap_files: list[str] = Field(default_factory=list)
    prune_eligible: bool = False


class IntegrationFileRow(BaseModel):
    agent: str
    label: str
    path: str
    exists: bool = False
    status: str = "missing"
    detail: str = ""
    repair_command: str = ""


class CommandCatalogItem(BaseModel):
    id: str
    group: str
    label: str
    command: str
    description: str = ""
    risk: Literal["low", "medium", "high"] = "low"
    confirm_required: bool = False
    primary: bool = False


class ArtifactRow(BaseModel):
    label: str
    path: str
    exists: bool = False
    kind: str = ""
    modified_at: str = ""
    size: int = 0
    destination: str = ""


class ProjectCandidate(BaseModel):
    name: str
    path: str
    branch: str = ""
    git_sha: str = ""
    source: str = ""
    current: bool = False
    exists: bool = False
    valid: bool = False
    detail: str = ""
    context_status: str = "unknown"
    mcp_status: str = "unknown"
    map_ready: bool = False
    last_seen_at: str = ""


class TaskHistoryRow(BaseModel):
    task: str
    source: str
    observed_at: str = ""
    thread_id: str = ""
    agent: str = ""
    branch: str = ""
    git_sha: str = ""
    cwd: str = ""
    context_path: str = ""
    status: str = ""
    summary: str = ""


class DashboardEvidence(BaseModel):
    kind: str
    ref: str = ""
    summary: str = ""
    path: str = ""
    line: int | None = None


class DashboardAction(BaseModel):
    label: str
    command: str = ""
    kind: str = "command"


class DashboardNode(BaseModel):
    id: str
    type: DashboardNodeType
    label: str
    path: str = ""
    status: str = ""
    risk: str = ""
    selected: bool = False
    stale: bool = False
    score: float = 0.0
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[DashboardEvidence] = Field(default_factory=list)
    actions: list[DashboardAction] = Field(default_factory=list)


class DashboardEdge(BaseModel):
    id: str
    source: str
    target: str
    type: DashboardEdgeType
    label: str = ""
    confidence: float = 0.0
    reason: str = ""
    stale: bool = False
    evidence: list[DashboardEvidence] = Field(default_factory=list)
    actions: list[DashboardAction] = Field(default_factory=list)


class DashboardGraphSummary(BaseModel):
    node_count: int = 0
    edge_count: int = 0
    selected_files: int = 0
    omitted_files: int = 0
    memory_nodes: int = 0
    high_risk_files: int = 0
    truncated: bool = False


class DashboardGraph(BaseModel):
    schema_version: int = 1
    generated_at: str = ""
    root_id: str = "task:active"
    summary: DashboardGraphSummary = Field(default_factory=DashboardGraphSummary)
    nodes: list[DashboardNode] = Field(default_factory=list)
    edges: list[DashboardEdge] = Field(default_factory=list)


class MapDistrict(BaseModel):
    id: str
    label: str
    path: str = ""
    x: float = 0.0
    z: float = 0.0
    building_count: int = 0
    selected_count: int = 0


class MapBuilding(BaseModel):
    id: str
    node_id: str
    label: str
    path: str
    district_id: str
    building_type: str = "unknown"
    building_tier: str = "pavilion"
    confidence_source: str = "fallback"
    confidence_breakdown: dict[str, float | str | bool] = Field(default_factory=dict)
    layout_group: str = "unknown"
    action_refs: list[str] = Field(default_factory=list)
    score: float = 0.0
    confidence: float = 0.08
    height: float = 7.36
    risk: str = "unknown"
    selected: bool = False
    include_mode: str = ""
    memory_linked: bool = False
    tests: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    actions: list[DashboardAction] = Field(default_factory=list)
    x: float = 0.0
    z: float = 0.0
    color: str = "#6b7280"


class MapRoad(BaseModel):
    id: str
    source: str
    target: str
    type: str
    confidence: float = 0.0
    reason: str = ""
    route_class: str = "local"
    relationship_strength: float = 0.0
    relationship_source: str = "fallback"
    source_kind: str = "unknown"
    target_kind: str = "unknown"


class MapLandmark(BaseModel):
    id: str
    label: str
    type: str
    status: str = ""
    detail: str = ""
    tone: str = "neutral"
    x: float = 0.0
    z: float = 0.0


class MapWeather(BaseModel):
    id: str
    label: str
    tone: str = "neutral"
    detail: str = ""


class DashboardMapSummary(BaseModel):
    district_count: int = 0
    building_count: int = 0
    road_count: int = 0
    selected_buildings: int = 0
    high_risk_buildings: int = 0
    max_score: float = 0.0
    stale: bool = False
    building_type_counts: dict[str, int] = Field(default_factory=dict)
    route_class_counts: dict[str, int] = Field(default_factory=dict)
    confidence_source_counts: dict[str, int] = Field(default_factory=dict)


class DashboardMap(BaseModel):
    schema_version: int = 1
    generated_at: str = ""
    summary: DashboardMapSummary = Field(default_factory=DashboardMapSummary)
    districts: list[MapDistrict] = Field(default_factory=list)
    buildings: list[MapBuilding] = Field(default_factory=list)
    roads: list[MapRoad] = Field(default_factory=list)
    landmarks: list[MapLandmark] = Field(default_factory=list)
    weather: list[MapWeather] = Field(default_factory=list)


class ActionHistoryRow(BaseModel):
    action_id: str
    label: str = ""
    command: str = ""
    cwd: str = ""
    status: str = ""
    started_at: str = ""
    ended_at: str = ""
    returncode: int | None = None
    confirmed: bool = False
    source: str = "dashboard"
    session_id: str = ""
    duration_ms: int | None = None
    output_summary: str = ""
    follow_up_actions: list[str] = Field(default_factory=list)


class SemanticGraphSummary(BaseModel):
    schema_version: int = 0
    commit_sha: str = ""
    entity_count: int = 0
    edge_count: int = 0
    unresolved_count: int = 0
    capabilities: dict[str, str] = Field(default_factory=dict)
    cache_stats: dict[str, Any] = Field(default_factory=dict)
    relationship_counts: dict[str, int] = Field(default_factory=dict)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class DashboardSnapshot(BaseModel):
    schema_version: int = 1
    generated_at: str = ""
    project: ProjectInfo
    project_overview: ProjectOverview | None = None
    project_record: DashboardProjectRecord | None = None
    workspace: DashboardWorkspaceRecord | None = None
    project_tasks: list[DashboardTaskRecord] = Field(default_factory=list)
    active_task: DashboardTaskRecord | None = None
    task_runs: list[DashboardTaskRun] = Field(default_factory=list)
    task_timeline: list[DashboardTimelineEvent] = Field(default_factory=list)
    dashboard_feedback: list[DashboardFeedback] = Field(default_factory=list)
    analytics: DashboardAnalytics = Field(default_factory=DashboardAnalytics)
    unassigned_history_count: int = 0
    task: TaskInfo = Field(default_factory=TaskInfo)
    context: ContextHealth = Field(default_factory=ContextHealth)
    selected_files: list[SelectedFileRow] = Field(default_factory=list)
    task_map: list[TaskMapFileRow] = Field(default_factory=list)
    skills: SkillSection = Field(default_factory=SkillSection)
    skills_inventory: SkillsInventorySummary = Field(default_factory=SkillsInventorySummary)
    skill_feedback: dict[str, Any] = Field(default_factory=dict)
    learning: list[LearningArtifact] = Field(default_factory=list)
    learning_memories: list[LearningMemory] = Field(default_factory=list)
    learning_weak_spots: list[LearningWeakSpot] = Field(default_factory=list)
    observer: ObserverSummary = Field(default_factory=ObserverSummary)
    benchmarks: BenchmarkSummary = Field(default_factory=BenchmarkSummary)
    threads: ThreadSummary = Field(default_factory=ThreadSummary)
    mcp_health: McpHealth = Field(default_factory=McpHealth)
    loop: LoopSummary = Field(default_factory=LoopSummary)
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    config: DashboardConfigSummary = Field(default_factory=DashboardConfigSummary)
    task_control: list[TaskControlRow] = Field(default_factory=list)
    thread_rows: list[ThreadRow] = Field(default_factory=list)
    integrations: list[IntegrationFileRow] = Field(default_factory=list)
    command_catalog: list[CommandCatalogItem] = Field(default_factory=list)
    artifacts: list[ArtifactRow] = Field(default_factory=list)
    projects: list[ProjectCandidate] = Field(default_factory=list)
    task_history: list[TaskHistoryRow] = Field(default_factory=list)
    semantic_graph: SemanticGraphSummary = Field(default_factory=SemanticGraphSummary)
