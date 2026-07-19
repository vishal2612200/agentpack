import type { ImpactEntity, ImpactRelationship, ImpactScene, LearningRecommendationSet as GeneratedLearningRecommendationSet } from "./dashboard-v2.generated";

export type ContextStatus = "fresh" | "stale" | "missing" | "unknown";
export type { ImpactEntity as DashboardV2ImpactEntity, ImpactRelationship as DashboardV2ImpactRelationship, ImpactScene as DashboardV2ImpactScene } from "./dashboard-v2.generated";
export type PresentationMode = "explain" | "build";
export type LearningRecommendationSet = GeneratedLearningRecommendationSet;
export type LearningScope = LearningRecommendationSet["scope"];
export type LearningLane = LearningRecommendationSet["topics"][number]["lane"];
export type NodeType = "task" | "file" | "symbol" | "test" | "episode" | "procedure" | "action";
export type EdgeType =
  | "selected_because"
  | "omitted_because"
  | "imports"
  | "tested_by"
  | "memory_influenced"
  | "procedure_applies"
  | "may_break"
  | "retrieve_ref";

export interface DashboardEvidence {
  kind: string;
  ref?: string;
  summary?: string;
  path?: string;
  line?: number | null;
}

export interface DashboardAction {
  label: string;
  command?: string;
  kind?: string;
}

export interface DashboardNode {
  id: string;
  type: NodeType;
  label: string;
  path?: string;
  status?: string;
  risk?: string;
  selected?: boolean;
  stale?: boolean;
  score?: number;
  summary?: string;
  metadata?: Record<string, unknown>;
  evidence?: DashboardEvidence[];
  actions?: DashboardAction[];
}

export interface DashboardEdge {
  id: string;
  source: string;
  target: string;
  type: EdgeType;
  label?: string;
  confidence?: number;
  reason?: string;
  stale?: boolean;
  evidence?: DashboardEvidence[];
  actions?: DashboardAction[];
}

export interface DashboardGraph {
  schema_version: number;
  generated_at?: string;
  root_id?: string;
  summary: {
    node_count: number;
    edge_count: number;
    selected_files: number;
    omitted_files: number;
    memory_nodes: number;
    high_risk_files: number;
    truncated: boolean;
  };
  nodes: DashboardNode[];
  edges: DashboardEdge[];
}

export interface MapDistrict {
  id: string;
  label: string;
  path?: string;
  x: number;
  z: number;
  building_count: number;
  selected_count: number;
}

export interface MapBuilding {
  id: string;
  node_id: string;
  label: string;
  path: string;
  district_id: string;
  building_type?: string;
  building_tier?: string;
  confidence_source?: string;
  confidence_breakdown?: Record<string, number | string | boolean>;
  layout_group?: string;
  action_refs?: string[];
  score: number;
  confidence: number;
  height: number;
  risk: string;
  selected: boolean;
  include_mode?: string;
  memory_linked?: boolean;
  tests?: string[];
  reasons?: string[];
  actions?: DashboardAction[];
  x: number;
  z: number;
  color: string;
}

export interface MapRoad {
  id: string;
  source: string;
  target: string;
  type: string;
  confidence?: number;
  reason?: string;
  route_class?: string;
  relationship_strength?: number;
  relationship_source?: string;
  source_kind?: string;
  target_kind?: string;
}

export interface MapLandmark {
  id: string;
  label: string;
  type: string;
  status?: string;
  detail?: string;
  tone?: string;
  x: number;
  z: number;
}

export interface MapWeather {
  id: string;
  label: string;
  tone?: string;
  detail?: string;
}

export interface DashboardMap {
  schema_version: number;
  generated_at?: string;
  summary: {
    district_count: number;
    building_count: number;
    road_count: number;
    selected_buildings: number;
    high_risk_buildings: number;
    max_score: number;
    stale: boolean;
    building_type_counts?: Record<string, number>;
    route_class_counts?: Record<string, number>;
    confidence_source_counts?: Record<string, number>;
  };
  districts: MapDistrict[];
  buildings: MapBuilding[];
  roads: MapRoad[];
  landmarks: MapLandmark[];
  weather: MapWeather[];
}

export interface SemanticGraphSummary {
  schema_version: number;
  commit_sha?: string;
  entity_count: number;
  edge_count: number;
  unresolved_count: number;
  capabilities: Record<string, string>;
  cache_stats?: Record<string, number | string | boolean>;
  relationship_counts: Record<string, number>;
  entities: Array<{ entity_key: string; type: string; name: string; path: string; line?: number; language?: string; confidence_tier?: string }>;
  edges: Array<{ edge_key: string; relationship: string; source: string; target: string; source_name?: string; target_name?: string; confidence_tier?: string; evidence?: Array<{ path?: string; start_line?: number; end_line?: number; source?: string; source_hash?: string; note?: string }> }>;
}

export interface DashboardV2ImpactResponse {
  schema_version: 2;
  query: string;
  relationship: string;
  language: string;
  confidence: string;
  available: boolean;
  summary: SemanticGraphSummary;
  affected_tests: Array<{ entity_key: string; type: string; name: string; path: string; line?: number; language?: string; confidence_tier?: string }>;
  entities: ImpactEntity[];
  relationships: ImpactRelationship[];
  scene: ImpactScene;
}

export interface DashboardV2ActionInspection {
  schema_version: 2;
  action: string;
  command: string;
  cwd: string;
  purpose: string;
  risk: string;
  risk_reasons: string[];
  affected_paths: string[];
  expected_effect: string;
  confirm_required: boolean;
  allowed: boolean;
}

export interface DashboardV2AgentSession {
  provider: string;
  session_id: string;
  thread_id?: string;
  task?: string;
  status?: string;
  context_status?: string;
  updated_at?: string;
  worktree?: string;
}

export interface DashboardV2Handoff {
  name: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  source_provider?: string;
  source_session_id?: string;
  target_provider?: string;
  target_session_id?: string;
  task?: string;
  summary?: string;
  next_action?: string;
  claim_provider?: string;
  claim_session_id?: string;
}

export interface ActionHistoryRow {
  action_id: string;
  label?: string;
  command?: string;
  cwd?: string;
  status?: string;
  started_at?: string;
  ended_at?: string;
  returncode?: number | null;
  confirmed?: boolean;
  source?: string;
  session_id?: string;
  duration_ms?: number | null;
  output_summary?: string;
  follow_up_actions?: string[];
}

export type DashboardTaskStatus = "todo" | "in_progress" | "needs_attention" | "done";

export interface DashboardProjectRecord {
  project_id: string;
  name: string;
  repository_path: string;
  created_at?: string;
  updated_at?: string;
}

export interface DashboardWorkspaceRecord {
  workspace_id: string;
  project_id: string;
  path: string;
  branch?: string;
  git_sha?: string;
  is_current?: boolean;
  updated_at?: string;
}

export interface DashboardTaskRecord {
  task_id: string;
  project_id: string;
  workspace_id: string;
  title: string;
  description?: string;
  status: DashboardTaskStatus;
  created_at?: string;
  updated_at?: string;
  thread_ids?: string[];
  source_paths?: string[];
  active?: boolean;
  imported?: boolean;
  last_run_id?: string;
}

export interface DashboardTaskRun {
  run_id: string;
  task_id: string;
  session_id?: string;
  agent?: string;
  started_at?: string;
  ended_at?: string;
  status?: string;
  event_ids?: string[];
  context_path?: string;
  citation_manifest_path?: string;
  issue_references?: string[];
  issue_reference_details?: Array<Record<string, unknown>>;
  selected_files?: string[];
  omitted_files?: string[];
  checks?: string[];
  packed_tokens?: number;
  raw_tokens?: number;
  saving_pct?: number;
  unresolved_edges?: number;
  evidence_refs?: string[];
}

export interface DashboardTimelineEvent {
  event_id: string;
  event_type?: string;
  label?: string;
  occurred_at?: string;
  project_id?: string;
  workspace_id?: string;
  task_id?: string;
  session_id?: string;
  agent?: string;
  source?: string;
  summary?: string;
  context_path?: string;
  issue_references?: string[];
  evidence?: Array<Record<string, unknown>>;
}

export interface DashboardTaskDetail {
  task: DashboardTaskRecord;
  runs: DashboardTaskRun[];
  timeline: DashboardTimelineEvent[];
  github_references?: string[];
  feedback?: DashboardFeedback[];
  impact?: Array<Record<string, unknown>>;
  impact_available?: boolean;
  impact_reason?: string;
}

export interface DashboardFeedback {
  feedback_id: string;
  task_id: string;
  run_id?: string;
  value: "helped" | "partly_helped" | "missed_context" | "not_sure";
  note?: string;
  created_at?: string;
}

export interface DashboardAnalytics {
  range: "7d" | "30d";
  available: boolean;
  tasks_total: number;
  tasks_completed: number;
  runs_total: number;
  context_packs: number;
  files_selected: number;
  files_omitted: number;
  packed_tokens: number;
  raw_tokens: number;
  average_saving_pct: number;
  checks_total: number;
  unresolved_edges: number;
  feedback_counts: Record<string, number>;
  evidence: string[];
  unavailable_reason?: string;
}

export type ProjectRecordSource = "declared" | "observed" | "inferred";
export type ProjectHealthStatus = "healthy" | "attention" | "blocked" | "stale" | "unknown";

export interface ProjectEvidence {
  kind: string;
  ref: string;
  summary: string;
  path: string;
  occurred_at: string;
  workspace_id: string;
}

export interface ProjectDerivedRecord {
  source: ProjectRecordSource;
  confidence: number;
  updated_at: string;
  evidence: ProjectEvidence[];
  workspace_id: string;
  warnings: string[];
}

export interface ProjectWorkspace extends ProjectDerivedRecord {
  workspace_id: string;
  path: string;
  branch: string;
  git_sha: string;
  is_current: boolean;
  read_only: boolean;
}

export interface ProjectProfile extends ProjectDerivedRecord {
  project_id: string;
  config_revision: string;
  display_name: string;
  purpose: string;
  audiences: string[];
  owners: string[];
  stage: string;
  links: Record<string, string>;
  environments: string[];
  status_stale_days: number;
}

export interface ProjectMilestoneState extends ProjectDerivedRecord {
  milestone_id: string;
  outcome_id: string;
  title: string;
  owner: string;
  due_date: string;
  status: "planned" | "in_progress" | "blocked" | "done";
}

export interface ProjectOutcomeState extends ProjectDerivedRecord {
  outcome_id: string;
  title: string;
  description: string;
  owner: string;
  target_date: string;
  status: "planned" | "on_track" | "at_risk" | "achieved" | "paused";
  progress_pct: number | null;
  milestones: ProjectMilestoneState[];
}

export interface ProjectInitiative extends ProjectDerivedRecord {
  initiative_id: string;
  suggestion_id: string;
  title: string;
  description: string;
  owner: string;
  outcome_id: string;
  status: string;
}

export interface ProjectInitiativeSuggestion extends ProjectDerivedRecord {
  suggestion_id: string;
  title: string;
  rationale: string;
  outcome_id: string;
  score: number;
  task_ids: string[];
}

export interface ProjectRisk extends ProjectDerivedRecord {
  risk_id: string;
  title: string;
  description: string;
  owner: string;
  severity: "low" | "medium" | "high" | "critical";
  status: "open" | "mitigating" | "accepted" | "resolved";
  mitigation: string;
}

export interface ProjectDecision extends ProjectDerivedRecord {
  decision_id: string;
  title: string;
  context: string;
  decision: string;
  owner: string;
  status: "proposed" | "accepted" | "rejected" | "superseded";
}

export interface ProjectHealthDimension extends ProjectDerivedRecord {
  dimension: "delivery" | "validation" | "architecture" | "release" | "context" | "knowledge";
  status: ProjectHealthStatus;
  summary: string;
}

export interface ProjectHealthSnapshot extends ProjectDerivedRecord {
  dimensions: ProjectHealthDimension[];
}

export interface ProjectMetrics extends ProjectDerivedRecord {
  outcome_count: number;
  active_outcomes: number;
  milestone_count: number;
  completed_milestones: number;
  milestone_completion_pct: number | null;
  open_risks: number;
  pending_decisions: number;
  confirmed_initiatives: number;
  recent_changes: number;
  evidence_coverage: number | null;
}

export interface ProjectTimelineEvent extends ProjectDerivedRecord {
  event_id: string;
  kind: string;
  title: string;
  summary: string;
  entity_id: string;
  actor: string;
  git_sha: string;
  branch: string;
  tags: string[];
}

export interface ProjectStatusBrief extends ProjectDerivedRecord {
  mode: "summary" | "engineering";
  markdown: string;
  project_id: string;
}

export interface ProjectOverview extends ProjectDerivedRecord {
  schema_version: number;
  project_id: string;
  generated_at: string;
  selected_workspace: string;
  profile: ProjectProfile;
  workspaces: ProjectWorkspace[];
  metrics: ProjectMetrics;
  outcomes: ProjectOutcomeState[];
  initiatives: ProjectInitiative[];
  initiative_suggestions: ProjectInitiativeSuggestion[];
  risks: ProjectRisk[];
  decisions: ProjectDecision[];
  health: ProjectHealthSnapshot;
  recent_changes: ProjectTimelineEvent[];
  partial: boolean;
  read_only: boolean;
}

export interface DashboardSnapshot {
  schema_version: number;
  generated_at?: string;
  project: {
    name: string;
    path: string;
    branch?: string;
    git_sha?: string;
  };
  project_overview?: ProjectOverview | null;
  project_record?: DashboardProjectRecord | null;
  workspace?: DashboardWorkspaceRecord | null;
  project_tasks?: DashboardTaskRecord[];
  active_task?: DashboardTaskRecord | null;
  task_runs?: DashboardTaskRun[];
  task_timeline?: DashboardTimelineEvent[];
  dashboard_feedback?: DashboardFeedback[];
  analytics?: DashboardAnalytics;
  unassigned_history_count?: number;
  task: {
    text?: string;
    state?: string;
    thread_id?: string | null;
  };
  context: {
    status: ContextStatus;
    generated_at?: string;
    mode?: string;
    packed_tokens?: number;
    raw_tokens?: number;
    saving_pct?: number;
    selected_files_count?: number;
    stale_reason?: string;
    source_command?: string;
  };
  selected_files: Array<{
    path: string;
    include_mode?: string;
    score?: number;
    tokens?: number;
    reasons?: string[];
  }>;
  task_map: Array<{
    path: string;
    kind?: string;
    include_mode?: string;
    score?: number;
    risk_level?: string;
    risk_reasons?: string[];
    why_selected?: string[];
    tests_to_run?: string[];
    may_break?: string[];
    retrieve_ref?: string;
  }>;
  learning_memories: Array<{
    task: string;
    stage?: string;
    status?: string;
    branch?: string;
    git_sha?: string;
    concepts?: string[];
    changed_files?: string[];
    selected_files?: string[];
  }>;
  learning_weak_spots: Array<{
    concept: string;
    count?: number;
    mode?: string;
    latest_task?: string;
    latest_question?: string;
    evidence_files?: string[];
  }>;
  observer: {
    events?: number;
    insights?: Array<{
      kind: string;
      title: string;
      detail?: string;
      action?: string;
      confidence?: number;
      related_files?: string[];
      evidence?: string[];
    }>;
  };
  mcp_health?: {
    status?: "healthy" | "warning" | "missing" | "unknown";
    runtime_status?: string;
    runtime_ok?: boolean;
    runtime_detail?: string;
    registered?: boolean;
    registrations?: Array<{
      scope: string;
      path: string;
      status?: string;
      detail?: string;
    }>;
    live_exposure?: "confirmed" | "unknown";
    expected_tools?: string[];
    remediation?: string[];
  };
  threads?: {
    active_count?: number;
    conflicts?: Array<Record<string, unknown>>;
  };
  benchmarks: {
    latest?: Record<string, unknown>;
    averages?: Record<string, number>;
    misses?: Array<Record<string, unknown>>;
  };
  loop: {
    exists?: boolean;
    status?: string;
    task?: string;
    iteration?: number;
    max_iterations?: number;
    runner?: string;
    blocked_reason?: string;
    next_action?: string;
  };
  suggested_actions: DashboardAction[];
  config?: {
    path?: string;
    exists?: boolean;
    valid?: boolean;
    error?: string;
    editable_fields?: string[];
    sections?: Array<{
      name: string;
      fields: Array<{
        section: string;
        key: string;
        value: unknown;
        default?: unknown;
        value_type?: string;
        editable?: boolean;
        source?: string;
        description?: string;
        allowed_values?: string[];
        doc_ref?: string;
      }>;
    }>;
  };
  task_control?: Array<{
    scope: "global" | "thread";
    thread_id?: string | null;
    task?: string;
    task_path?: string;
    state?: string;
    state_path?: string;
    status?: string;
    summary?: string;
    done?: boolean;
    exists?: boolean;
  }>;
  thread_rows?: Array<{
    thread_id: string;
    task?: string;
    status?: string;
    summary?: string;
    branch?: string;
    updated_at?: string;
    worktree?: string;
    selected_count?: number;
    dirty_count?: number;
    conflicts?: string[];
    overlap_files?: string[];
    prune_eligible?: boolean;
  }>;
  integrations?: Array<{
    agent: string;
    label: string;
    path: string;
    exists?: boolean;
    status?: string;
    detail?: string;
    repair_command?: string;
  }>;
  command_catalog?: Array<{
    id: string;
    group: string;
    label: string;
    command: string;
    description?: string;
    risk?: "low" | "medium" | "high";
    confirm_required?: boolean;
    primary?: boolean;
  }>;
  artifacts?: Array<{
    label: string;
    path: string;
    exists?: boolean;
    kind?: string;
    modified_at?: string;
    size?: number;
    destination?: string;
  }>;
  projects?: Array<{
    name: string;
    path: string;
    branch?: string;
    git_sha?: string;
    source?: string;
    current?: boolean;
      exists?: boolean;
      valid?: boolean;
      detail?: string;
      context_status?: string;
      mcp_status?: string;
      map_ready?: boolean;
      last_seen_at?: string;
    }>;
  task_history?: Array<{
    task: string;
    source: string;
    observed_at?: string;
    thread_id?: string;
    agent?: string;
    branch?: string;
    git_sha?: string;
    cwd?: string;
    context_path?: string;
    status?: string;
    summary?: string;
  }>;
  semantic_graph: SemanticGraphSummary;
}
