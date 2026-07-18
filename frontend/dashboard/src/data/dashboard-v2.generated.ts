/* Generated from docs/schemas/dashboard-v2.schema.json. Do not edit. */

export interface AgentPackDashboardV2 {
  schema_version: 2;
  detail: "home" | "full";
  snapshot: {
    [k: string]: unknown;
  };
  graph: ObjectEnvelope;
  map: ObjectEnvelope;
  action_history: {
    [k: string]: unknown;
  }[];
  workspace: {
    project: {
      [k: string]: unknown;
    };
    workspace: {
      [k: string]: unknown;
    } | null;
    task: {
      [k: string]: unknown;
    };
    context: {
      [k: string]: unknown;
    };
  };
  agents: {
    handoffs: Handoff[];
    sessions: Session[];
    threads: {
      [k: string]: unknown;
    }[];
    integrations: {
      [k: string]: unknown;
    }[];
    mcp_health: {
      [k: string]: unknown;
    };
  };
  impact: {
    schema_version: number;
    available: boolean;
    entity_count: number;
    edge_count: number;
    unresolved_count: number;
    capabilities: {
      [k: string]: string;
    };
  };
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "objectEnvelope".
 */
export interface ObjectEnvelope {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "handoff".
 */
export interface Handoff {
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
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "session".
 */
export interface Session {
  provider: string;
  session_id: string;
  thread_id?: string;
  task?: string;
  status?: string;
  context_status?: string;
  updated_at?: string;
  worktree?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "impactResponse".
 */
export interface ImpactResponse {
  schema_version: 2;
  query: string;
  relationship: string;
  language: string;
  confidence: string;
  available: boolean;
  summary: {
    [k: string]: unknown;
  };
  affected_tests: {
    [k: string]: unknown;
  }[];
  entities: ImpactEntity[];
  relationships: ImpactRelationship[];
  scene: ImpactScene;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "impactEntity".
 */
export interface ImpactEntity {
  id: string;
  kind: "file" | "symbol" | "test" | "action" | "external";
  label: string;
  path: string;
  line: number;
  parent_id: string;
  confidence_tier: string;
  task_relevant: boolean;
  risk: string;
  reasons: string[];
  related_ids: string[];
  evidence: {
    [k: string]: unknown;
  }[];
  actions: string[];
  x: number;
  y: number;
  z: number;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "impactRelationship".
 */
export interface ImpactRelationship {
  id: string;
  source_id: string;
  target_id: string;
  relationship: string;
  confidence_tier: string;
  strength: number;
  task_relevant: boolean;
  evidence: {
    [k: string]: unknown;
  }[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "impactScene".
 */
export interface ImpactScene {
  available: boolean;
  unavailable_reason: string;
  entities: ImpactEntity[];
  relationships: ImpactRelationship[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionRequest".
 */
export interface ActionRequest {
  action: string;
  cwd?: string;
  agent?: string;
  thread?: string;
  task?: string;
  target?: string;
  path?: string;
  mode?: string;
  budget?: number | null;
  status?: string;
  summary?: string;
  thread_id?: string;
  older_than?: string;
  refresh?: boolean;
  guard?: boolean;
  global?: boolean;
  confirmed?: boolean;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "handoffOperationRequest".
 */
export interface HandoffOperationRequest {
  name: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "agentsResponse".
 */
export interface AgentsResponse {
  schema_version: 2;
  agents: Agents;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "agents".
 */
export interface Agents {
  handoffs: Handoff[];
  sessions: Session[];
  threads: {
    [k: string]: unknown;
  }[];
  integrations: {
    [k: string]: unknown;
  }[];
  mcp_health: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "agentOperationResponse".
 */
export interface AgentOperationResponse {
  schema_version: 2;
  handoff: Handoff;
  warnings: string[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionInspection".
 */
export interface ActionInspection {
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
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionInspectionResponse".
 */
export interface ActionInspectionResponse {
  schema_version: 2;
  inspection: ActionInspection;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionRunResponse".
 */
export interface ActionRunResponse {
  schema_version: 2;
  session: {
    [k: string]: unknown;
  };
  command: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "unavailableState".
 */
export interface UnavailableState {
  kind:
    | "stale_context"
    | "tree_sitter_unavailable"
    | "mcp_unavailable"
    | "permission_denied"
    | "repository_mismatch"
    | "action_conflict"
    | "webgl_unavailable"
    | "server_error";
  title: string;
  detail: string;
  next_action: string;
  retryable: boolean;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "evidenceResponse".
 */
export interface EvidenceResponse {
  schema_version: 2;
  context: {
    [k: string]: unknown;
  };
  selected_files: {
    [k: string]: unknown;
  }[];
  task_map: {
    [k: string]: unknown;
  }[];
  observer: {
    [k: string]: unknown;
  };
  timeline: {
    [k: string]: unknown;
  }[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionsResponse".
 */
export interface ActionsResponse {
  schema_version: 2;
  suggested: {
    [k: string]: unknown;
  }[];
  catalog: {
    [k: string]: unknown;
  }[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "errorResponse".
 */
export interface ErrorResponse {
  schema_version: 2;
  error: string;
  kind: string;
  retryable: boolean;
  detail: string;
}
