export type ContextStatus = "fresh" | "stale" | "missing" | "unknown";
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

export interface DashboardSnapshot {
  schema_version: number;
  generated_at?: string;
  project: {
    name: string;
    path: string;
    branch?: string;
    git_sha?: string;
  };
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
}
