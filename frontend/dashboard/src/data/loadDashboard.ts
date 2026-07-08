import type { ActionHistoryRow, DashboardGraph, DashboardMap, DashboardSnapshot } from "./schema";

declare global {
  interface Window {
    __AGENTPACK_DASHBOARD_API__?: string;
    __AGENTPACK_DASHBOARD_TOKEN__?: string;
  }
}

export interface DashboardPayload {
  snapshot: DashboardSnapshot;
  graph: DashboardGraph;
  map: DashboardMap;
  action_history: ActionHistoryRow[];
}

export async function loadDashboardPayload(): Promise<DashboardPayload> {
  if (window.location.protocol === "file:") {
    throw new Error("Static dashboard files are no longer supported. Run `agentpack dashboard` and open the served URL.");
  }
  const apiBase = normalizedApiBase();
  if (apiBase === null) {
    throw new Error("Dashboard server API is unavailable. Run `agentpack dashboard` and open the served URL.");
  }
  const response = await fetch(`${apiBase}/api/dashboard`, {
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
