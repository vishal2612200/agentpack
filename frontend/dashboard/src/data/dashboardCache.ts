import type { DashboardPayload } from "./loadDashboard";
import type { CachedProjectStatus, DashboardSnapshot, ProjectOverview } from "./schema";

const CACHE_KEY = "agentpack.dashboard.last-known.v1";
const CACHE_VERSION = 1;
const CACHE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const CACHE_MAX_STATUS_BYTES = 256 * 1024;

export interface DashboardCacheEnvelope {
  schema_version: 1;
  cached_at: string;
  expires_at: string;
  project_id: string;
  status: CachedProjectStatus;
}

export function readDashboardCache(now = Date.now()): DashboardCacheEnvelope | null {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<DashboardCacheEnvelope>;
    const statusRaw = JSON.stringify(parsed.status || null);
    const cached = Date.parse(String(parsed.cached_at || ""));
    const expires = Date.parse(String(parsed.expires_at || ""));
    const valid = parsed.schema_version === CACHE_VERSION
      && typeof parsed.project_id === "string"
      && parsed.project_id === parsed.status?.project_id
      && isCachedProjectStatus(parsed.status)
      && Number.isFinite(cached)
      && Number.isFinite(expires)
      && expires > cached
      && expires - cached <= CACHE_MAX_AGE_MS
      && expires > now
      && new TextEncoder().encode(statusRaw).byteLength <= CACHE_MAX_STATUS_BYTES;
    if (!valid) {
      clearDashboardCache();
      return null;
    }
    return parsed as DashboardCacheEnvelope;
  } catch {
    clearDashboardCache();
    return null;
  }
}

function isCachedProjectStatus(value: unknown): value is CachedProjectStatus {
  if (!isRecord(value)) return false;
  if (value.schema_version !== 1 || typeof value.project_id !== "string" || !Number.isFinite(Date.parse(String(value.generated_at || "")))) return false;
  if (!isRecord(value.profile) || !isRecord(value.metrics) || !isRecord(value.health)) return false;
  if (!stringArray(value.profile.audiences) || !stringArray(value.profile.owners) || !stringArray(value.profile.environments)) return false;
  if (!recordArray(value.health.dimensions)) return false;
  for (const key of ["outcomes", "initiatives", "risks", "decisions", "recent_changes", "warnings"] as const) {
    if (!Array.isArray(value[key])) return false;
  }
  if (!recordArray(value.outcomes) || !value.outcomes.every((item) => typeof item.title === "string" && recordArray(item.milestones))) return false;
  if (!recordArray(value.initiatives) || !recordArray(value.risks) || !recordArray(value.decisions) || !recordArray(value.recent_changes)) return false;
  if (!stringArray(value.warnings)) return false;
  if (value.focus !== null && value.focus !== undefined) {
    if (!isRecord(value.focus) || !recordArray(value.focus.attention) || !recordArray(value.focus.next_actions)) return false;
  }
  return typeof value.profile.display_name === "string"
    && typeof value.branch === "string"
    && typeof value.git_sha === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function recordArray(value: unknown): value is Array<Record<string, unknown>> {
  return Array.isArray(value) && value.every(isRecord);
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

export function writeDashboardCache(status: CachedProjectStatus | null | undefined, now = Date.now()): void {
  if (!status) return;
  const statusRaw = JSON.stringify(status);
  if (new TextEncoder().encode(statusRaw).byteLength > CACHE_MAX_STATUS_BYTES) return;
  const envelope: DashboardCacheEnvelope = {
    schema_version: CACHE_VERSION,
    cached_at: new Date(now).toISOString(),
    expires_at: new Date(now + CACHE_MAX_AGE_MS).toISOString(),
    project_id: status.project_id,
    status
  };
  try {
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(envelope));
  } catch {
    clearDashboardCache();
  }
}

export function clearDashboardCache(): void {
  try {
    window.localStorage.removeItem(CACHE_KEY);
  } catch {
    // Storage can be disabled by browser policy; clearing remains best effort.
  }
}

export function cachedStatusToOverview(status: CachedProjectStatus): ProjectOverview {
  const derived = {
    source: "observed" as const,
    confidence: 1,
    updated_at: status.generated_at,
    evidence: [],
    workspace_id: "all",
    warnings: status.warnings
  };
  return {
    ...derived,
    schema_version: 1,
    project_id: status.project_id,
    generated_at: status.generated_at,
    selected_workspace: "all",
    profile: {
      ...derived,
      project_id: status.project_id,
      config_revision: "",
      display_name: status.profile.display_name,
      purpose: status.profile.purpose,
      audiences: status.profile.audiences,
      owners: status.profile.owners,
      stage: status.profile.stage,
      links: {},
      environments: status.profile.environments,
      status_stale_days: status.profile.status_stale_days
    },
    workspaces: [],
    metrics: status.metrics,
    outcomes: status.outcomes,
    initiatives: status.initiatives,
    initiative_suggestions: [],
    risks: status.risks,
    decisions: status.decisions,
    health: status.health,
    focus: status.focus,
    recent_changes: status.recent_changes,
    partial: status.partial,
    read_only: true
  };
}

export function cachedStatusToPayload(status: CachedProjectStatus): DashboardPayload {
  const overview = cachedStatusToOverview(status);
  const snapshot = {
    schema_version: 1,
    generated_at: status.generated_at,
    project: { name: status.profile.display_name, path: "", branch: status.branch, git_sha: status.git_sha },
    project_overview: overview,
    task: {},
    context: { status: "unknown" },
    selected_files: [],
    task_map: [],
    learning_memories: [],
    learning_weak_spots: [],
    observer: {},
    benchmarks: {},
    loop: {},
    suggested_actions: [],
    project_tasks: [],
    command_catalog: [],
    artifacts: [],
    projects: [],
    mcp_health: { status: "unknown" }
  } as unknown as DashboardSnapshot;
  return {
    schema_version: 2,
    detail: "home",
    snapshot,
    graph: {
      schema_version: 1,
      summary: { node_count: 0, edge_count: 0, selected_files: 0, omitted_files: 0, memory_nodes: 0, high_risk_files: 0, truncated: false },
      nodes: [],
      edges: []
    },
    map: {
      schema_version: 1,
      summary: { district_count: 0, building_count: 0, road_count: 0, selected_buildings: 0, high_risk_buildings: 0, max_score: 0, stale: true },
      districts: [],
      buildings: [],
      roads: [],
      landmarks: [],
      weather: []
    },
    action_history: [],
    workspace: { project: snapshot.project, workspace: null, task: snapshot.task, context: snapshot.context },
    agents: { handoffs: [], sessions: [], threads: [], integrations: [], mcp_health: snapshot.mcp_health },
    impact: { schema_version: 0, available: false, entity_count: 0, edge_count: 0, unresolved_count: 0, capabilities: {} },
    cached_project_status: status
  };
}
