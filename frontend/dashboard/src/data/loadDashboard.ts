import type { ActionHistoryRow, DashboardGraph, DashboardMap, DashboardSnapshot, DashboardV2AgentSession, DashboardV2Handoff, DashboardV2ImpactResponse, DashboardV2ActionInspection } from "./schema";

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
