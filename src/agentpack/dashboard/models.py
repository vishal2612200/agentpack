from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ContextStatus = Literal["fresh", "stale", "missing", "unknown"]
TaskState = Literal["planned", "in_progress", "blocked", "done", "unknown"]
SkillFeedbackStatus = Literal[
    "none",
    "recommended_only",
    "used_helpful",
    "used_noisy",
    "ignored",
    "bad_recommendation",
]
DashboardNodeType = Literal["task", "file", "symbol", "test", "episode", "procedure", "action"]
DashboardEdgeType = Literal[
    "contains",
    "selected_because",
    "omitted_because",
    "imports",
    "tested_by",
    "memory_influenced",
    "procedure_applies",
    "may_break",
    "retrieve_ref",
]


class ProjectInfo(BaseModel):
    name: str
    path: str
    branch: str = ""
    git_sha: str = ""


class ProjectIndexRow(BaseModel):
    name: str
    path: str
    current: bool = False
    branch: str = ""
    git_sha: str = ""
    task: str = ""
    context_status: ContextStatus = "unknown"
    packed_tokens: int = 0
    raw_tokens: int = 0
    saving_pct: float = 0.0
    selected_files_count: int = 0
    review_runs_count: int = 0
    memory_count: int = 0
    weak_spots_count: int = 0
    dashboard_path: str = ""
    open_command: str = ""
    refresh_command: str = ""


class ProjectIndexSummary(BaseModel):
    root_path: str = ""
    project_count: int = 0
    stale_count: int = 0
    missing_count: int = 0
    total_raw_tokens: int = 0
    total_packed_tokens: int = 0
    estimated_saved_tokens: int = 0
    average_saving_pct: float = 0.0
    projects: list[ProjectIndexRow] = Field(default_factory=list)


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


class SelectedSymbolRow(BaseModel):
    name: str
    kind: str = ""
    start_line: int = 0
    end_line: int = 0
    signature: str = ""
    summary: str = ""
    node_id: str = ""
    signature_hash: str = ""
    source_hash: str = ""


class SelectedFileRow(BaseModel):
    path: str
    include_mode: str = ""
    score: float = 0.0
    tokens: int = 0
    reasons: list[str] = Field(default_factory=list)
    symbols: list[SelectedSymbolRow] = Field(default_factory=list)


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


class LearningPrepSessionRow(BaseModel):
    task: str
    request: str = ""
    mode: str = ""
    topic: str = ""
    question: str = ""
    status: str = ""
    score: int | None = None
    concepts: list[str] = Field(default_factory=list)
    evidence_files: list[str] = Field(default_factory=list)
    created_at: str = ""


class LearningPrepSummary(BaseModel):
    queued_count: int = 0
    needs_review_count: int = 0
    completed_count: int = 0
    top_concepts: list[str] = Field(default_factory=list)
    sessions: list[LearningPrepSessionRow] = Field(default_factory=list)
    quiz_command: str = 'agentpack learn "quiz me on last task"'
    interview_command: str = 'agentpack learn "interview me on last task"'
    failure_drill_command: str = 'agentpack learn "failure drill on last task"'


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


class ReviewRunRow(BaseModel):
    run_id: str
    branch_prefix: str = ""
    generated_at: str = ""
    review_context: str = ""
    target_number: int | None = None
    target_url: str = ""
    diff_source: str = ""
    changed_files_count: int = 0
    scaffold: str = ""
    status: str = "prepared"
    run_dir: str = ""
    preflight_path: str = ""
    understanding_path: str = ""
    findings_path: str = ""
    resume_command: str = ""
    check_command: str = "agentpack review --check"
    post_command: str = "agentpack review --check --post-inline-comments"


class SuggestedAction(BaseModel):
    label: str
    command: str
    reason: str = ""


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
    max_nodes: int = 0
    truncated_reason: str = ""
    truncated: bool = False


class DashboardGraph(BaseModel):
    schema_version: int = 1
    generated_at: str = ""
    root_id: str = "task:active"
    summary: DashboardGraphSummary = Field(default_factory=DashboardGraphSummary)
    nodes: list[DashboardNode] = Field(default_factory=list)
    edges: list[DashboardEdge] = Field(default_factory=list)


class DashboardSnapshot(BaseModel):
    schema_version: int = 1
    generated_at: str = ""
    project: ProjectInfo
    project_index: ProjectIndexSummary = Field(default_factory=ProjectIndexSummary)
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
    learning_prep: LearningPrepSummary = Field(default_factory=LearningPrepSummary)
    observer: ObserverSummary = Field(default_factory=ObserverSummary)
    benchmarks: BenchmarkSummary = Field(default_factory=BenchmarkSummary)
    threads: ThreadSummary = Field(default_factory=ThreadSummary)
    loop: LoopSummary = Field(default_factory=LoopSummary)
    review_runs: list[ReviewRunRow] = Field(default_factory=list)
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
