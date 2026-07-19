import type { ActionHistoryRow, DashboardGraph, DashboardMap, DashboardSnapshot, DashboardV2AgentSession, DashboardV2Handoff, DashboardV2ImpactResponse, DashboardV2ActionInspection, LearningRecommendationSet, LearningScope, ProjectOverview, ProjectStatusBrief, ProjectTimelineEvent } from "./schema";

declare global {
  interface Window {
    __AGENTPACK_DASHBOARD_API__?: string;
    __AGENTPACK_DASHBOARD_TOKEN__?: string;
  }
}

export interface DashboardPayload {
  schema_version?: number;
  detail?: "home" | "full";
  snapshot: DashboardSnapshot;
  graph: DashboardGraph;
  map: DashboardMap;
  action_history: ActionHistoryRow[];
  workspace?: {
    project: DashboardSnapshot["project"];
    workspace: DashboardSnapshot["workspace"];
    task: DashboardSnapshot["task"] | DashboardSnapshot["active_task"];
    context: DashboardSnapshot["context"];
  };
  agents?: {
    handoffs: DashboardV2Handoff[];
    sessions: DashboardV2AgentSession[];
    threads: DashboardSnapshot["thread_rows"];
    integrations: DashboardSnapshot["integrations"];
    mcp_health: DashboardSnapshot["mcp_health"];
  };
  impact?: {
    schema_version: number;
    available: boolean;
    entity_count: number;
    edge_count: number;
    unresolved_count: number;
    capabilities: Record<string, string>;
  };
}

export type DashboardImpactPayload = DashboardV2ImpactResponse;
export type DashboardActionInspectionPayload = DashboardV2ActionInspection;

export interface ProjectProfileMutation {
  mutation_id: string;
  expected_revision: string;
  profile: Record<string, unknown>;
}

export interface ProjectEventMutation {
  event_type: "project_outcome_status" | "project_milestone_status" | "project_risk_upsert" | "project_decision_recorded" | "project_initiative_confirmed" | "project_initiative_dismissed";
  mutation_id: string;
  entity_id: string;
  status?: string;
  title?: string;
  description?: string;
  owner?: string;
  severity?: string;
  mitigation?: string;
  context?: string;
  decision?: string;
  outcome_id?: string;
  evidence?: Array<{ kind: string; ref?: string; summary?: string; path?: string }>;
}

export async function loadDashboardPayload(detail: "home" | "full" = "home"): Promise<DashboardPayload> {
  if (window.location.protocol === "file:") {
    throw new Error("Static dashboard files are no longer supported. Run `agentpack dashboard` and open the served URL.");
  }
  const apiBase = normalizedApiBase();
  if (apiBase === null) {
    throw new Error("Dashboard server API is unavailable. Run `agentpack dashboard` and open the served URL.");
  }
  const response = await fetch(`${apiBase}/api/dashboard/v2?detail=${detail}`, {
    headers: authHeaders()
  });
  if (!response.ok) {
    throw new Error(`Dashboard API failed: ${response.status}`);
  }
  return (await response.json()) as DashboardPayload;
}

export async function loadDashboardImpact(params: URLSearchParams = new URLSearchParams()): Promise<DashboardImpactPayload> {
  const response = await fetch(apiUrl(`/api/dashboard/v2/impact?${params.toString()}`), { headers: authHeaders() });
  if (!response.ok) throw new Error(`Impact API failed: ${response.status}`);
  return await response.json() as DashboardImpactPayload;
}

export async function loadLearningRecommendations(scope: LearningScope = "local"): Promise<LearningRecommendationSet> {
  const response = await fetch(apiUrl(`/api/learning/recommendations?scope=${scope}`), { headers: authHeaders() });
  if (!response.ok) throw new Error(`Learning recommendations API failed: ${response.status}`);
  return await response.json() as LearningRecommendationSet;
}

export async function loadProjectOverview(workspace = "all"): Promise<ProjectOverview> {
  const response = await fetch(apiUrl(`/api/project/overview?workspace=${encodeURIComponent(workspace)}`), { headers: authHeaders() });
  if (!response.ok) throw new Error(`Project overview API failed: ${response.status}`);
  return await response.json() as ProjectOverview;
}

export async function loadProjectTimeline(workspace = "all", kind = "", limit = 50): Promise<ProjectTimelineEvent[]> {
  const params = new URLSearchParams({ workspace, limit: String(limit) });
  if (kind) params.set("kind", kind);
  const response = await fetch(apiUrl(`/api/project/timeline?${params.toString()}`), { headers: authHeaders() });
  if (!response.ok) throw new Error(`Project timeline API failed: ${response.status}`);
  const payload = await response.json() as { timeline: ProjectTimelineEvent[] };
  return payload.timeline;
}

export async function loadProjectBrief(mode: "summary" | "engineering"): Promise<ProjectStatusBrief> {
  const response = await fetch(apiUrl(`/api/project/brief?mode=${mode}`), { headers: authHeaders() });
  if (!response.ok) throw new Error(`Project brief API failed: ${response.status}`);
  return await response.json() as ProjectStatusBrief;
}

export async function updateProjectProfile(request: ProjectProfileMutation): Promise<ProjectOverview> {
  const response = await fetch(apiUrl("/api/project/profile"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(request)
  });
  const payload = await response.json() as { project_overview?: ProjectOverview; error?: string };
  if (!response.ok || !payload.project_overview) throw new Error(payload.error || `Project profile update failed: ${response.status}`);
  return payload.project_overview;
}

export async function recordProjectEvent(request: ProjectEventMutation): Promise<ProjectOverview> {
  const response = await fetch(apiUrl("/api/project/events"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(request)
  });
  const payload = await response.json() as { project_overview?: ProjectOverview; error?: string };
  if (!response.ok || !payload.project_overview) throw new Error(payload.error || `Project event failed: ${response.status}`);
  return payload.project_overview;
}

export function authHeaders(): HeadersInit {
  const token = window.__AGENTPACK_DASHBOARD_TOKEN__;
  return token && !token.startsWith("__AGENTPACK_") ? { "X-AgentPack-Token": token } : {};
}

export function apiUrl(path: string): string {
  const base = normalizedApiBase() || "";
  return `${base}${path}`;
}

export function dashboardToken(): string {
  const token = window.__AGENTPACK_DASHBOARD_TOKEN__;
  return token && !token.startsWith("__AGENTPACK_") ? token : "";
}

function normalizedApiBase(): string | null {
  const value = window.__AGENTPACK_DASHBOARD_API__;
  if (value && !value.startsWith("__AGENTPACK_")) {
    return value.replace(/\/$/, "");
  }
  if (window.location.protocol === "http:" || window.location.protocol === "https:") {
    return "";
  }
  return null;
}
