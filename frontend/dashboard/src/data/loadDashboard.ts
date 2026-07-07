import type { DashboardGraph, DashboardSnapshot } from "./schema";

declare global {
  interface Window {
    __AGENTPACK_DASHBOARD_DATA__?: string;
    __AGENTPACK_DASHBOARD_GRAPH__?: string;
  }
}

export interface DashboardPayload {
  snapshot: DashboardSnapshot;
  graph: DashboardGraph;
}

export async function loadDashboardPayload(): Promise<DashboardPayload> {
  const snapshot = readEmbeddedJson<DashboardSnapshot>("agentpack-dashboard-data");
  const graph = readEmbeddedJson<DashboardGraph>("agentpack-dashboard-graph");
  if (snapshot && graph) {
    return { snapshot, graph };
  }

  const [snapshotResponse, graphResponse] = await Promise.all([
    fetch(window.__AGENTPACK_DASHBOARD_DATA__ || "./dashboard-data.json"),
    fetch(window.__AGENTPACK_DASHBOARD_GRAPH__ || "./dashboard-graph.json")
  ]);
  return {
    snapshot: await snapshotResponse.json(),
    graph: await graphResponse.json()
  };
}

function readEmbeddedJson<T>(id: string): T | null {
  const el = document.getElementById(id);
  const text = el?.textContent?.trim();
  if (!text || text.startsWith("__AGENTPACK_")) {
    return null;
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}
