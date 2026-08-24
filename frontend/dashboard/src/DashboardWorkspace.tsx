import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Brain,
  Building2,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  ClipboardList,
  Code2,
  Copy,
  Database,
  FileText,
  Flag,
  FolderKanban,
  GitBranch,
  ListFilter,
  Map as MapIcon,
  Maximize2,
  Minimize2,
  Network,
  PlayCircle,
  RefreshCcw,
  Search,
  Send,
  Settings,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Table2,
  TerminalSquare,
  Workflow,
  X
} from "lucide-react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type OnNodeDrag,
  useReactFlow
} from "@xyflow/react";
import agentPackSymbolUrl from "../../../docs/assets/agentpack-symbol.png";
import { apiUrl, authHeaders, dashboardToken, loadArchitecturePRMap, loadDashboardImpact, loadDashboardPayload, loadLearningProfile, loadLearningRecommendations, loadPortfolio, loadProjectOverview, startLearningSession, updateLearningProfile, type ArchitecturePRMapPayload, type DashboardActionInspectionPayload, type DashboardImpactPayload, type DashboardPayload, type PortfolioPayload } from "./data/loadDashboard";
import type { ActionHistoryRow, DashboardAnalytics, DashboardEdge, DashboardGraph, DashboardMap, DashboardNode, DashboardSnapshot, LearnerProfile, LearningRecommendationSet, LearningScope, LearningSession, MapBuilding, MapRoad, PresentationMode, ProjectOverview, SemanticGraphSummary } from "./data/schema";
import { ProjectActivityView } from "./components/dashboard/project/ProjectActivityView";
import { DashboardCommandPalette, type PaletteTarget } from "./components/dashboard/CommandPalette";
import { RuntimeStatusDialog, type DashboardConnectionState } from "./components/dashboard/RuntimeStatusDialog";
import { ProjectHealthView } from "./components/dashboard/project/ProjectHealthView";
import { ProjectKnowledgeSummary } from "./components/dashboard/project/ProjectKnowledgeSummary";
import { ProjectOverviewView } from "./components/dashboard/project/ProjectOverviewView";
import { ProjectRoadmapView } from "./components/dashboard/project/ProjectRoadmapView";
import { ProjectWorkView } from "./components/dashboard/project/ProjectWorkView";
import { PortfolioView } from "./components/dashboard/PortfolioView";
import { ProjectViewState } from "./components/dashboard/project/project-shared";
import { ConfirmCommandDialog, ErrorState, LoadingState, Metric, Panel, StateSurface, StatusPill, TechnicalDetail, type CommandInspection, type PendingCommand } from "./components/dashboard/shared";
import { cachedStatusToOverview, cachedStatusToPayload, clearDashboardCache, readDashboardCache, writeDashboardCache } from "./data/dashboardCache";
import { buildingHoverInfo, labelize, roadHoverInfo, type MapHoverInfo } from "./mapInfo";
import { useDashboardState, type DashboardView as View, type MapMode } from "./state/dashboard-state";

const ContextCityMap = lazy(() => import("./MapCity").then((module) => ({ default: module.ContextCityMap })));

interface TerminalSessionState {
  id: string;
  command: string;
  cwd: string;
  status: string;
  returncode?: number | null;
  output: string;
}

const primaryViews: Array<{ id: View; label: string; icon: typeof Activity }> = [
  { id: "portfolio", label: "Atlas", icon: Network },
  { id: "home", label: "Overview", icon: Building2 },
  { id: "roadmap", label: "Roadmap", icon: Flag },
  { id: "tasks", label: "Work", icon: ClipboardList },
  { id: "health", label: "Health", icon: ShieldCheck },
  { id: "learning", label: "Knowledge", icon: Brain }
];

const advancedViewGroups: Array<{ label: string; views: Array<{ id: View; label: string; icon: typeof Activity }> }> = [
  { label: "Repository", views: [
    { id: "graph", label: "Impact map", icon: MapIcon },
    { id: "context", label: "AI context", icon: Database },
    { id: "files", label: "Files", icon: FileText }
  ] },
  { label: "Operations", views: [
    { id: "workflow", label: "Run checks", icon: Workflow },
    { id: "threads", label: "Work sessions", icon: GitBranch }
  ] },
  { label: "Connections", views: [
    { id: "integrations", label: "Agent connection", icon: TerminalSquare }
  ] },
  { label: "Diagnostics", views: [
    { id: "settings", label: "Settings", icon: Settings },
    { id: "raw", label: "Diagnostics", icon: Code2 },
    { id: "cockpit", label: "Decision details", icon: Activity }
  ] }
];

const advancedViews = advancedViewGroups.flatMap((group) => group.views);
const inspectorViews = new Set<View>(["graph", "context", "files", "cockpit"]);

function readPortfolioCache(): PortfolioPayload | null {
  try {
    const raw = window.localStorage.getItem("agentpack.dashboard.portfolio.last-known.v1");
    if (!raw) return null;
    const value = JSON.parse(raw) as { cached_at?: string; payload?: PortfolioPayload };
    const cachedAt = value.cached_at ? Date.parse(value.cached_at) : 0;
    if (!value.payload || !cachedAt || Date.now() - cachedAt > 7 * 24 * 60 * 60 * 1000) return null;
    return value.payload;
  } catch {
    return null;
  }
}

export function DashboardWorkspace() {
  const expectedProjectId = window.__AGENTPACK_PROJECT_ID__ && !window.__AGENTPACK_PROJECT_ID__.startsWith("__AGENTPACK_") ? window.__AGENTPACK_PROJECT_ID__ : "";
  const initialCacheRef = useRef(readDashboardCache(expectedProjectId));
  const { state: dashboardState, dispatch } = useDashboardState();
  const view = dashboardState.view;
  const selectedId = dashboardState.selectedEntityId;
  const presentationMode = dashboardState.presentationMode;
  const setView = (value: View) => dispatch({ type: "view", value });
  const setSelectedId = (value: string | ((current: string) => string)) => dispatch({ type: "select", value: typeof value === "function" ? value(dashboardState.selectedEntityId) : value });
  const setPresentationMode = (value: PresentationMode) => dispatch({ type: "presentation", value });
  const [payload, setPayload] = useState<DashboardPayload | null>(() => initialCacheRef.current ? cachedStatusToPayload(initialCacheRef.current.status) : null);
  const [portfolio, setPortfolio] = useState<PortfolioPayload | null>(() => readPortfolioCache());
  const [payloadDetail, setPayloadDetail] = useState<"home" | "full">("home");
  const [projectOverview, setProjectOverview] = useState<ProjectOverview | null>(() => initialCacheRef.current ? cachedStatusToOverview(initialCacheRef.current.status) : null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [projectWorkspace, setProjectWorkspace] = useState("all");
  const [projectLoading, setProjectLoading] = useState(false);
  const [projectError, setProjectError] = useState("");
  const [error, setError] = useState<string>("");
  const [connection, setConnection] = useState<DashboardConnectionState>(initialCacheRef.current ? "stale" : "connecting");
  const [observedAt, setObservedAt] = useState("");
  const [cachedAt, setCachedAt] = useState(initialCacheRef.current?.cached_at || "");
  const [loadingPhase, setLoadingPhase] = useState("Connecting to the local AgentPack dashboard.");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteLoading, setPaletteLoading] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [sessions, setSessions] = useState<TerminalSessionState[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>("");
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(null);
  const streams = useRef<Map<string, EventSource>>(new Map());
  const selectedEntityRef = useRef(selectedId);
  const impactParentRef = useRef<Map<string, string>>(new Map());
  const refreshGenerationRef = useRef(0);
  const latestActionGenerationRef = useRef(0);
  const projectWorkspaceRef = useRef("all");
  const cachedOnly = connection !== "live";

  useEffect(() => { selectedEntityRef.current = selectedId; }, [selectedId]);

  const refreshDashboard = async (detail: "home" | "full" = payloadDetail) => {
    const loaded = await loadDashboardPayload(detail);
    setPayloadDetail(detail);
    setPayload(loaded);
    const overview = projectWorkspaceRef.current === "all"
      ? loaded.snapshot.project_overview || null
      : await loadProjectOverview(projectWorkspaceRef.current, selectedProjectId);
    setProjectOverview(overview);
    setConnection("live");
    const observed = new Date().toISOString();
    setObservedAt(observed);
    setError("");
    if (loaded.cached_project_status) {
      writeDashboardCache(loaded.cached_project_status);
      setCachedAt(observed);
    }
    return loaded;
  };

  const refreshPortfolio = async () => {
    const loaded = await loadPortfolio();
    setPortfolio(loaded);
    try {
      const safe = { ...loaded, projects: loaded.projects.map((project) => ({ ...project, workspaces: project.workspaces.map((workspace) => ({ ...workspace, path: "" })) })) };
      window.localStorage.setItem("agentpack.dashboard.portfolio.last-known.v1", JSON.stringify({ cached_at: new Date().toISOString(), payload: safe }));
    } catch { /* storage is optional */ }
    return loaded;
  };

  const refreshGithubEvidence = async () => {
    const response = await fetch(apiUrl("/api/dashboard/v2/portfolio/github/refresh"), { method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() }, body: JSON.stringify(selectedProjectId ? { project_id: selectedProjectId } : {}) });
    if (!response.ok) throw new Error(`GitHub evidence refresh failed: ${response.status}`);
    await refreshPortfolio();
  };

  const handleProjectWorkspaceChange = async (value: string, projectId = selectedProjectId) => {
    projectWorkspaceRef.current = value;
    setProjectWorkspace(value);
    setProjectLoading(true);
    setProjectError("");
    try {
      setProjectOverview(await loadProjectOverview(value, projectId));
    } catch (caught) {
      setProjectError(caught instanceof Error ? caught.message : "Project scope could not be loaded.");
    } finally {
      setProjectLoading(false);
    }
  };

  const handleProjectOverviewMutation = (next: ProjectOverview) => {
    if (projectWorkspaceRef.current === "all") {
      setProjectOverview(next);
      return;
    }
    setProjectLoading(true);
    loadProjectOverview(projectWorkspaceRef.current, selectedProjectId)
      .then(setProjectOverview)
      .catch((caught: unknown) => setProjectError(caught instanceof Error ? caught.message : "Project scope could not be refreshed."))
      .finally(() => setProjectLoading(false));
  };

  const loadFullDashboard = async () => {
    if (cachedOnly || payloadDetail === "full" || paletteLoading) return;
    setPaletteLoading(true);
    try {
      await refreshDashboard("full");
    } catch (err) {
      dispatch({ type: "resource", key: "workspace", value: { status: "error", message: err instanceof Error ? err.message : "Failed to load dashboard details", retryable: true } });
    } finally {
      setPaletteLoading(false);
    }
  };

  useEffect(() => {
    const readingTimer = window.setTimeout(() => setLoadingPhase("Reading project and workspace evidence."), 2_000);
    const waitingTimer = window.setTimeout(() => setLoadingPhase("Still waiting for the local dashboard server."), 6_000);
    Promise.all([refreshDashboard("home"), refreshPortfolio()])
      .catch((err: unknown) => {
        setConnection(initialCacheRef.current ? "stale" : "unavailable");
        setError(err instanceof Error ? err.message : "Failed to load dashboard data");
      })
      .finally(() => {
        window.clearTimeout(readingTimer);
        window.clearTimeout(waitingTimer);
      });
    return () => {
      window.clearTimeout(readingTimer);
      window.clearTimeout(waitingTimer);
    };
  }, []);

  useEffect(() => {
    const openPalette = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", openPalette);
    return () => window.removeEventListener("keydown", openPalette);
  }, []);

  useEffect(() => {
    if (paletteOpen) void loadFullDashboard();
  }, [paletteOpen]);


  useEffect(() => {
    return () => {
      streams.current.forEach((stream) => stream.close());
      streams.current.clear();
    };
  }, []);

  const retryDashboard = () => {
    setError("");
    setConnection("connecting");
    setLoadingPhase("Connecting to the local AgentPack dashboard.");
    refreshDashboard("home").catch((err: unknown) => {
      setConnection(payload ? "stale" : "unavailable");
      setError(err instanceof Error ? err.message : "Failed to load dashboard data");
    });
  };

  if (error && !payload) {
    return (
      <ErrorState
        message={error}
        onRetry={retryDashboard}
      />
    );
  }
  if (!payload) {
    return <LoadingState phase={loadingPhase} />;
  }

  const selected = findSelected(payload.graph, selectedId);
  const renderNavItem = (item: (typeof primaryViews)[number]) => {
    const Icon = item.icon;
    return (
      <button
        key={item.id}
        type="button"
        className={view === item.id ? "nav-item active" : "nav-item"}
        onClick={() => {
          setView(item.id);
          if (inspectorViews.has(item.id) && !selectedId && payload.graph.root_id) setSelectedId(payload.graph.root_id);
          if (["graph", "context", "files", "workflow", "threads", "learning", "settings", "integrations", "raw", "cockpit"].includes(item.id)) void loadFullDashboard();
        }}
        aria-label={item.label}
        title={item.label}
      >
        <Icon size={17} aria-hidden="true" />
        <span>{item.label}</span>
      </button>
    );
  };

  const handlePaletteNavigate = (target: PaletteTarget) => {
    setView(target.view);
    if (target.entityId) setSelectedId(target.entityId);
    else if (inspectorViews.has(target.view) && payload.graph.root_id) setSelectedId(payload.graph.root_id);
    if (target.anchor) window.setTimeout(() => document.getElementById(target.anchor!)?.scrollIntoView({ block: "start" }), 0);
    if (advancedViews.some((item) => item.id === target.view) || target.view === "learning") void loadFullDashboard();
  };

  const handleRunCommand = async (command: string) => {
    if (cachedOnly) {
      setStatusOpen(true);
      return;
    }
    const inspection = await inspectCommand(command);
    if (!inspection.allowed) {
      openLocalError(command, inspection.reason);
      return;
    }
    if (inspection.confirm_required) {
      setPendingCommand({ command, inspection });
      return;
    }
    await startCommand(command, false);
  };

  const handleRunAction = async (action: string, body: Record<string, unknown> = {}) => {
    if (cachedOnly) {
      setStatusOpen(true);
      return;
    }
    const scopedBody = { action, ...body, ...(selectedProjectId ? { project_id: selectedProjectId } : {}) };
    const inspectionResponse = await fetch(apiUrl("/api/dashboard/v2/actions/inspect"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(scopedBody)
    });
    const inspectionPayload = await inspectionResponse.json() as { inspection?: DashboardActionInspectionPayload; error?: string };
    if (!inspectionResponse.ok || !inspectionPayload.inspection) {
      openLocalError(action, inspectionPayload.error || `Could not inspect action: ${inspectionResponse.status}`);
      return;
    }
    const inspected = inspectionPayload.inspection;
    if (!inspected.allowed) {
      openLocalError(action, inspected.purpose || "This action is not allowed from the dashboard.");
      return;
    }
    if (inspected.confirm_required) {
      setPendingCommand({
        command: inspected.command,
        inspection: {
          ...inspected,
          risky: inspected.risk !== "low",
          reason: inspected.purpose || "Confirmation required before this action."
        }
      });
      return;
    }
    const response = await fetch(apiUrl("/api/dashboard/v2/actions/run"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(scopedBody)
    });
    const result = await response.json();
    if (response.status === 409 && result.inspection) {
      setPendingCommand({ command: String(result.command || result.inspection.command || action), inspection: result.inspection as CommandInspection });
      return;
    }
    if (!response.ok) {
      openLocalError(String(result.command || action), String(result.error || `Action failed: ${response.status}`));
      return;
    }
    const session = result.session as { id: string; command: string; cwd: string; status: string; returncode?: number | null };
    setSessions((current) => [
      ...current,
      {
        id: session.id,
        command: session.command,
        cwd: session.cwd,
        status: session.status,
        returncode: session.returncode,
        output: ""
      }
    ]);
    setActiveSessionId(session.id);
    setTerminalOpen(true);
    attachEventStream(session.id, ++latestActionGenerationRef.current);
  };

  const handleSaveConfig = async (updates: Record<string, unknown>) => {
    const response = await fetch(apiUrl("/api/config/update"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ updates })
    });
    const result = await response.json();
    if (!response.ok) {
      openLocalError("config update", String(result.error || `Config update failed: ${response.status}`));
      return;
    }
    await refreshDashboard();
  };

  const handleSwitchProject = async (path: string) => {
    if (cachedOnly) {
      setStatusOpen(true);
      return;
    }
    const response = await fetch(apiUrl("/api/projects/switch"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ path })
    });
    const result = await response.json();
    if (!response.ok) {
      openLocalError("project switch", String(result.error || `Project switch failed: ${response.status}`));
      return;
    }
    setPayload(result as DashboardPayload);
    setPayloadDetail("full");
    projectWorkspaceRef.current = "all";
    setProjectWorkspace("all");
    setProjectOverview((result as DashboardPayload).snapshot.project_overview || null);
    setProjectError("");
    setSelectedId(inspectorViews.has(view) ? (result as DashboardPayload).graph.root_id || "" : "");
    setSessions([]);
    setActiveSessionId("");
    setTerminalOpen(false);
  };

  const handleConfirmRun = async () => {
    if (!pendingCommand) return;
    const command = pendingCommand.command;
    setPendingCommand(null);
    await startCommand(command, true);
  };

  const startCommand = async (command: string, confirmed: boolean) => {
    const response = await fetch(apiUrl("/api/terminal/start"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ command, confirmed })
    });
    const payload = await response.json();
    if (response.status === 409 && payload.inspection) {
      setPendingCommand({ command, inspection: payload.inspection as CommandInspection });
      return;
    }
    if (!response.ok) {
      openLocalError(command, String(payload.error || `Command failed to start: ${response.status}`));
      return;
    }
    const session = payload.session as { id: string; command: string; cwd: string; status: string; returncode?: number | null };
    setSessions((current) => [
      ...current,
      {
        id: session.id,
        command: session.command,
        cwd: session.cwd,
        status: session.status,
        returncode: session.returncode,
        output: ""
      }
    ]);
    setActiveSessionId(session.id);
    setTerminalOpen(true);
    attachEventStream(session.id, ++latestActionGenerationRef.current);
  };

  const openLocalError = (command: string, message: string) => {
    const id = `local:${Date.now()}`;
    setSessions((current) => [
      ...current,
      {
        id,
        command,
        cwd: payload.snapshot.project.path,
        status: "blocked",
        output: `${message}\n`
      }
    ]);
    setActiveSessionId(id);
    setTerminalOpen(true);
  };

  const attachEventStream = (sessionId: string, actionGeneration: number) => {
    const token = dashboardToken();
    const queryToken = token ? `?token=${encodeURIComponent(token)}` : "";
    const stream = new EventSource(apiUrl(`/api/terminal/${sessionId}/events${queryToken}`));
    streams.current.set(sessionId, stream);
    stream.onmessage = (event) => {
      const item = JSON.parse(event.data) as { type: string; data?: string; status?: string; returncode?: number | null };
      setSessions((current) =>
        current.map((session) =>
          session.id === sessionId
            ? {
                ...session,
                output: item.data ? `${session.output}${item.data}` : session.output,
                status: item.status || session.status,
                returncode: item.returncode ?? session.returncode
              }
            : session
        )
      );
      if (item.type === "exit" || item.type === "error") {
        stream.close();
        streams.current.delete(sessionId);
        if (item.type === "exit" && item.status === "completed" && actionGeneration === latestActionGenerationRef.current) void refreshAfterSuccessfulAction();
      }
    };
    stream.onerror = () => {
      stream.close();
      streams.current.delete(sessionId);
    };
  };

  const refreshAfterSuccessfulAction = async () => {
    const generation = ++refreshGenerationRef.current;
    const current = selectedEntityRef.current;
    const previousParent = impactParentRef.current.get(current);
    dispatch({ type: "refresh_generation", value: generation });
    dispatch({ type: "resource", key: "workspace", value: { status: "loading" } });
    try {
      const [loaded, impact] = await Promise.all([
        loadDashboardPayload(payloadDetail),
        loadDashboardImpact(new URLSearchParams({ limit: "300" }))
      ]);
      if (generation !== refreshGenerationRef.current) return;
      setPayload(loaded);
      setProjectOverview(loaded.snapshot.project_overview || null);
      setConnection("live");
      const observed = new Date().toISOString();
      setObservedAt(observed);
      if (loaded.cached_project_status) {
        writeDashboardCache(loaded.cached_project_status);
        setCachedAt(observed);
      }
      const exists = Boolean(findSelected(loaded.graph, current) || impact.entities.some((entity) => entity.id === current) || impact.relationships.some((relationship) => relationship.id === current));
      if (!exists) {
        const parentExists = previousParent && impact.entities.some((entity) => entity.id === previousParent);
        setSelectedId(parentExists ? previousParent : inspectorViews.has(view) ? loaded.graph.root_id || "" : "");
      }
      impactParentRef.current = new Map(impact.entities.flatMap((entity) => entity.parent_id ? [[entity.id, entity.parent_id]] : []));
      dispatch({ type: "resource", key: "workspace", value: { status: "ready" } });
    } catch (error) {
      if (generation !== refreshGenerationRef.current) return;
      dispatch({ type: "resource", key: "workspace", value: { status: "error", message: error instanceof Error ? error.message : "Refresh failed", retryable: true } });
    }
  };

  const sendTerminalInput = async (sessionId: string, data: string) => {
    if (!sessionId.startsWith("local:")) {
      await fetch(apiUrl(`/api/terminal/${sessionId}/input`), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ data })
      });
    }
  };

  const killTerminalSession = async (sessionId: string) => {
    if (!sessionId.startsWith("local:")) {
      await fetch(apiUrl(`/api/terminal/${sessionId}/kill`), {
        method: "POST",
        headers: authHeaders()
      });
    }
  };

  return (
    <div className="app-shell" data-presentation-mode={presentationMode} data-inspector-open={Boolean(inspectorViews.has(view) && selected)} data-testid="dashboard-workspace">
      <aside className="sidebar" aria-label="Dashboard navigation">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true">
            <AgentPackLogo />
          </div>
          <div className="brand-copy">
            <strong>AgentPack</strong>
            <span>Local control plane</span>
          </div>
        </div>
        <nav className="nav-list">
          <span className="nav-group-label">Project</span>
          {primaryViews.map(renderNavItem)}
          <details className="advanced-nav" open={!primaryViews.some((item) => item.id === view)}>
            <summary>Explore</summary>
            <div className="nav-list nav-list-nested">
              {advancedViewGroups.map((group) => (
                <section key={group.label} className="advanced-nav-group">
                  <span>{group.label}</span>
                  {group.views.map(renderNavItem)}
                </section>
              ))}
            </div>
          </details>
        </nav>
        <div className="sidebar-footer">
          <button type="button" onClick={() => setStatusOpen(true)}><CircleDot size={12} aria-hidden="true" /> {connection === "live" ? "Live data" : connection === "stale" ? "Last known" : connection}</button>
          <code>{payload.snapshot.workspace?.branch || payload.snapshot.project.branch || "local workspace"}</code>
        </div>
      </aside>

      <main className="workspace">
        <TopBar
          snapshot={payload.snapshot}
          mode={presentationMode}
          overview={projectOverview}
          connection={connection}
          cachedAt={cachedAt}
          onModeChange={setPresentationMode}
          onSwitchProject={handleSwitchProject}
          onOpenSearch={() => setPaletteOpen(true)}
          onOpenStatus={() => setStatusOpen(true)}
        />
        <section className="main-panel" aria-label={`${view} view`}>
          {cachedOnly ? <StateSurface state={{ status: "stale", message: error || `Showing stored project status from ${cachedAt || payload.snapshot.generated_at || "an earlier session"}.`, retryable: true }} onRetry={retryDashboard} /> : <StateSurface state={dashboardState.resources.workspace || { status: "ready" }} onRetry={() => void refreshAfterSuccessfulAction()} />}
          {projectLoading && ["home", "roadmap", "health", "activity"].includes(view) ? <ProjectViewState status="loading" message="Loading the selected project workspace..." /> : null}
          {projectError && ["home", "roadmap", "health", "activity"].includes(view) ? <ProjectViewState status="error" message={projectError} onRetry={() => void handleProjectWorkspaceChange(projectWorkspace)} /> : null}
          {view === "portfolio" && portfolio ? <PortfolioView portfolio={portfolio} onRefreshGithub={refreshGithubEvidence} onOpenProject={(project) => { window.__AGENTPACK_SELECTED_PROJECT_ID__ = project.project_id; setSelectedProjectId(project.project_id); setView("home"); if (project.workspaces[0]) void handleProjectWorkspaceChange(project.workspaces[0].workspace_id, project.project_id); }} /> : null}
          {view === "portfolio" && !portfolio ? <ProjectViewState status="empty" message="Portfolio metadata is not available yet." onRetry={() => void refreshPortfolio()} /> : null}
          {view === "home" && projectOverview ? <ProjectOverviewView overview={projectOverview} workspace={projectWorkspace} loading={projectLoading} offline={cachedOnly} mode={presentationMode} onWorkspaceChange={(value) => void handleProjectWorkspaceChange(value)} onOverviewChange={handleProjectOverviewMutation} onNavigate={(nextView, entityId) => handlePaletteNavigate({ view: nextView, entityId })} /> : null}
          {view === "home" && !projectOverview ? <ProjectViewState status="empty" message="Project overview is not available yet." /> : null}
          {view === "roadmap" && projectOverview ? <ProjectRoadmapView overview={projectOverview} workspace={projectWorkspace} loading={projectLoading} onWorkspaceChange={(value) => void handleProjectWorkspaceChange(value)} onOverviewChange={handleProjectOverviewMutation} /> : null}
          {view === "health" && projectOverview ? <><ProjectHealthView overview={projectOverview} workspace={projectWorkspace} loading={projectLoading} actionsDisabled={cachedOnly} onWorkspaceChange={(value) => void handleProjectWorkspaceChange(value)} onRunAction={handleRunAction} onRunCommand={handleRunCommand} /><AnalyticsView snapshot={payload.snapshot} /></> : null}
          {view === "activity" && projectOverview ? <ProjectActivityView overview={projectOverview} workspace={projectWorkspace} loading={projectLoading} onWorkspaceChange={(value) => void handleProjectWorkspaceChange(value)} /> : null}
          {view === "analytics" && <AnalyticsView snapshot={payload.snapshot} />}
          {view === "cockpit" && (
            <CockpitView payload={payload} onSelect={setSelectedId} onOpenGraph={() => setView("graph")} onRunCommand={handleRunCommand} onRunAction={handleRunAction} />
          )}
          {view === "tasks" && <ProjectWorkView snapshot={payload.snapshot} overview={projectOverview} selectedTaskId={selectedId} onRunAction={handleRunAction} onRefresh={refreshDashboard} />}
          {view === "threads" && <ThreadsView snapshot={payload.snapshot} onRunAction={handleRunAction} />}
          {view === "context" && <ContextView snapshot={payload.snapshot} onSelect={setSelectedId} onRunAction={handleRunAction} onRunCommand={handleRunCommand} />}
          {view === "graph" && (
            <MapView
              dashboardMap={payload.map}
              graph={payload.graph}
              snapshot={payload.snapshot}
              actionHistory={payload.action_history || []}
              query={query}
              onQueryChange={setQuery}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onRunAction={handleRunAction}
              onRunCommand={handleRunCommand}
              onImpactLoaded={(impact) => {
                impactParentRef.current = new Map(impact.entities.flatMap((entity) => entity.parent_id ? [[entity.id, entity.parent_id]] : []));
              }}
            />
          )}
          {view === "files" && <FilesView snapshot={payload.snapshot} onSelect={setSelectedId} onRunAction={handleRunAction} />}
          {view === "settings" && <SettingsView snapshot={payload.snapshot} onSaveConfig={handleSaveConfig} />}
          {view === "integrations" && <IntegrationsView snapshot={payload.snapshot} onRunCommand={handleRunCommand} onRunAction={handleRunAction} />}
          {view === "workflow" && <WorkflowView snapshot={payload.snapshot} onRunCommand={handleRunCommand} onRunAction={handleRunAction} />}
          {view === "learning" && <div className="view-stack">{projectOverview ? <ProjectKnowledgeSummary overview={projectOverview} graph={payload.graph} /> : null}<MemoryView snapshot={payload.snapshot} graph={payload.graph} onSelect={setSelectedId} /></div>}
          {view === "raw" && <RawDataView payload={payload} />}
        </section>
      </main>

      {inspectorViews.has(view) && selected ? <Inspector selected={selected} onRunCommand={handleRunCommand} /> : null}
      <DashboardCommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} payload={payload} overview={projectOverview} loadingFull={paletteLoading} cachedOnly={cachedOnly} onNavigate={handlePaletteNavigate} onRunAction={(action) => void handleRunAction(action)} />
      <RuntimeStatusDialog open={statusOpen} onOpenChange={setStatusOpen} connection={connection} observedAt={observedAt} cachedAt={cachedAt} snapshot={payload.snapshot} onRetry={retryDashboard} onClearCache={() => { clearDashboardCache(expectedProjectId); setCachedAt(""); if (cachedOnly) { setPayload(null); setProjectOverview(null); setConnection("unavailable"); setError("Stored project status was cleared. Reconnect to the local dashboard server."); } }} />
      <TerminalPanel
        open={terminalOpen}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={setActiveSessionId}
        onClose={() => setTerminalOpen(false)}
        onInput={sendTerminalInput}
        onRunCommand={handleRunCommand}
        onKill={killTerminalSession}
      />
      {pendingCommand ? (
        <ConfirmCommandDialog
          pending={pendingCommand}
          onCancel={() => setPendingCommand(null)}
          onConfirm={handleConfirmRun}
        />
      ) : null}
    </div>
  );
}

function AgentPackLogo() {
  return (
    <img src={agentPackSymbolUrl} alt="" />
  );
}

async function inspectCommand(command: string): Promise<CommandInspection> {
  const response = await fetch(apiUrl("/api/commands/inspect"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ command })
  });
  const payload = await response.json();
  return payload.inspection as CommandInspection;
}

function TopBar({
  snapshot,
  overview,
  connection,
  cachedAt,
  mode,
  onModeChange,
  onSwitchProject,
  onOpenSearch,
  onOpenStatus
}: {
  snapshot: DashboardSnapshot;
  overview: ProjectOverview | null;
  connection: DashboardConnectionState;
  cachedAt: string;
  mode: PresentationMode;
  onModeChange: (mode: PresentationMode) => void;
  onSwitchProject: (path: string) => void;
  onOpenSearch: () => void;
  onOpenStatus: () => void;
}) {
  return (
    <header className="topbar">
      <div className="topbar-leading">
        <ProjectDropdown snapshot={snapshot} displayName={overview?.profile.display_name || ""} disabled={connection !== "live"} onSwitchProject={onSwitchProject} />
      </div>
      <button type="button" className="command-trigger" onClick={onOpenSearch} aria-label="Open project command palette">
        <Search size={16} aria-hidden="true" />
        <span>Find project evidence or action</span>
        <kbd>⌘ K</kbd>
      </button>
      <div className="mode-switch" role="group" aria-label="Workspace detail mode">
        <button type="button" className={mode === "explain" ? "active" : ""} onClick={() => onModeChange("explain")}>Summary</button>
        <button type="button" className={mode === "build" ? "active" : ""} onClick={() => onModeChange("build")}>Engineering</button>
      </div>
      <div className="topbar-status" aria-label="Dashboard health">
        <span className="topbar-meta">{snapshot.workspace?.branch || snapshot.project.branch || "local"}</span>
        <StatusPill label="Context" status={snapshot.context.status} />
        <StatusPill label="MCP" status={snapshot.mcp_health?.status || "unknown"} />
        <button type="button" className={`runtime-status-trigger ${connection}`} onClick={onOpenStatus}>
          <CircleDot size={12} aria-hidden="true" /> {connection === "live" ? "Live" : connection === "stale" ? `Last known${cachedAt ? ` · ${new Date(cachedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : ""}` : connection}
        </button>
      </div>
    </header>
  );
}

function ProjectDropdown({
  snapshot,
  displayName,
  disabled,
  onSwitchProject
}: {
  snapshot: DashboardSnapshot;
  displayName: string;
  disabled: boolean;
  onSwitchProject: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [manualPath, setManualPath] = useState("");
  const projects = snapshot.projects || [];
  const current = projects.find((item) => item.current) || {
    name: snapshot.project.name,
    path: snapshot.project.path,
    branch: snapshot.project.branch,
    git_sha: snapshot.project.git_sha,
    source: "current",
    context_status: snapshot.context.status,
    mcp_status: snapshot.mcp_health?.status || "unknown",
    map_ready: true,
    valid: true
  };
  return (
    <div className="project-dropdown">
      <button type="button" className="project-trigger" onClick={() => setOpen((value) => !value)} aria-expanded={open} disabled={disabled}>
        <span>
          <strong>{displayName || current.name}</strong>
          <small>{snapshot.workspace?.branch || current.branch || "local workspace"}{current.git_sha ? ` · ${current.git_sha}` : ""}</small>
        </span>
        <span className="badge neutral">Workspace</span>
      </button>
      {open ? (
        <div className="project-menu">
          <div className="project-current">
            <span className={`badge ${riskTone(snapshot.context.status)}`}>{snapshot.context.status}</span>
            <code>{current.path}</code>
          </div>
          <div className="stack-sm">
            {projects.map((project) => (
              <button
                key={project.path}
                type="button"
                className={project.current ? "project-option active" : "project-option"}
                disabled={!project.valid || project.current}
                onClick={() => {
                  setOpen(false);
                  onSwitchProject(project.path);
                }}
              >
                <span>
                  <strong>{project.name}</strong>
                  <small>
                    {project.source || "candidate"} · {project.detail || (project.valid ? "map-ready" : "unavailable")} · context {project.context_status || "unknown"} · MCP {project.mcp_status || "unknown"}
                  </small>
                  <code>{project.path}</code>
                </span>
                <span className={`badge ${project.map_ready ? "good" : project.valid ? "warn" : "risk"}`}>{project.current ? "current" : project.map_ready ? "map" : project.valid ? "setup" : "skip"}</span>
              </button>
            ))}
          </div>
          <form
            className="project-manual"
            onSubmit={(event) => {
              event.preventDefault();
              const value = manualPath.trim();
              if (!value) return;
              setOpen(false);
              onSwitchProject(value);
            }}
          >
            <input value={manualPath} onChange={(event) => setManualPath(event.target.value)} placeholder="/absolute/path/to/project" />
            <button type="submit" className="run-button" disabled={!manualPath.trim()}>Open path</button>
          </form>
        </div>
      ) : null}
    </div>
  );
}

function AnalyticsView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const [range, setRange] = useState<"7d" | "30d">("7d");
  const [analytics, setAnalytics] = useState<DashboardAnalytics | null>(snapshot.analytics || null);

  useEffect(() => {
    fetch(apiUrl(`/api/project/analytics?range=${range}`), { headers: authHeaders() })
      .then((response) => response.json())
      .then((payload) => setAnalytics(payload.analytics as DashboardAnalytics))
      .catch(() => setAnalytics(snapshot.analytics || null));
  }, [range, snapshot.analytics]);

  const data = analytics;
  return (
    <div className="view-stack analytics-view" data-testid="analytics-view">
      <SectionTitle title="How AgentPack helped" subtitle="Measured evidence from this project and workspace. No productivity guesses." />
      <div className="analytics-toolbar" role="group" aria-label="Analytics range">
        <span className="muted">Showing</span>
        {(["7d", "30d"] as const).map((item) => <button key={item} type="button" className={range === item ? "toolbar-button active" : "toolbar-button"} onClick={() => setRange(item)}>{item === "7d" ? "Last 7 days" : "Last 30 days"}</button>)}
      </div>
      {!data?.available ? <div className="empty-state-block"><strong>Analytics will appear after your first task.</strong><p>{data?.unavailable_reason || "Start a task to create measurable AgentPack evidence."}</p></div> : null}
      <div className="analytics-grid">
        <ValueCard title="Work completed" value={`${data?.tasks_completed || 0} tasks`} detail={`${data?.runs_total || 0} recorded work sessions`} />
        <ValueCard title="AI context prepared" value={`${data?.context_packs || 0} packs`} detail={`${data?.files_selected || 0} files selected for the agent`} />
        <ValueCard title="Focus improved" value={`${data?.average_saving_pct || 0}%`} detail="Average context reduction from recorded packs" />
        <ValueCard title="Checks completed" value={`${data?.checks_total || 0}`} detail="Validation hints captured from task runs" />
        <ValueCard title="Impact understood" value={`${data?.unresolved_edges || 0} unresolved`} detail="Relationship gaps kept visible instead of hidden" />
        <ValueCard title="Agent feedback" value={`${Object.values(data?.feedback_counts || {}).reduce((sum, count) => sum + count, 0)}`} detail="Optional feedback responses" />
      </div>
      <Panel title="Evidence behind these numbers" icon={BarChart3}>
        <div className="evidence-list">
          {(data?.evidence || []).map((item) => <code key={item}>{item}</code>)}
          {!data?.evidence?.length ? <p className="empty">No evidence references have been recorded for this range.</p> : null}
        </div>
      </Panel>
    </div>
  );
}

function ValueCard({ title, value, detail }: { title: string; value: string | number; detail: string }) {
  return <article className="value-card"><span className="eyebrow">{title}</span><strong>{value}</strong><p>{detail}</p></article>;
}

function CockpitView({
  payload,
  onSelect,
  onOpenGraph,
  onRunCommand,
  onRunAction
}: {
  payload: DashboardPayload;
  onSelect: (id: string) => void;
  onOpenGraph: () => void;
  onRunCommand: (command: string) => void;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
}) {
  const { snapshot, graph } = payload;
  const highRisk = snapshot.task_map.filter((item) => item.risk_level === "high");
  const tests = unique(snapshot.task_map.flatMap((item) => item.tests_to_run || []));
  const selectedFiles = graph.nodes.filter((node) => node.type === "file" && node.selected);
  const omittedFiles = graph.nodes.filter((node) => node.type === "file" && !node.selected);
  const missingIntegrations = (snapshot.integrations || []).filter((item) => item.status !== "present").length;
  const activeThreads = snapshot.thread_rows?.filter((thread) => thread.status !== "done").length || snapshot.threads?.active_count || 0;

  return (
    <div className="view-stack">
      <section className="hero-row">
        <div className="hero-content">
          <p className="eyebrow">Active task</p>
          <h1>{snapshot.task.text || "No task found"}</h1>
          <p className="muted">
            AgentPack selected context, memory, risk, and next actions for this local run.
          </p>
          <div className="hero-kpis" aria-label="Context summary">
            <span><strong>{graph.summary.selected_files}</strong> selected files</span>
            <span><strong>{tests.length}</strong> validation hints</span>
            <span><strong>{snapshot.context.status}</strong> context state</span>
          </div>
        </div>
        <div className="hero-actions">
          <span className="hero-tag">Local-first control plane</span>
          <button className="primary-action" type="button" onClick={onOpenGraph}>
            <MapIcon size={17} aria-hidden="true" />
            Open map
          </button>
          <button className="secondary-action inverse" type="button" onClick={() => onRunAction("next")}>
            <PlayCircle size={17} aria-hidden="true" />
            Run next
          </button>
        </div>
      </section>

      <div className="metric-grid">
        <Metric label="Selected" value={graph.summary.selected_files} tone="good" />
        <Metric label="Omitted" value={graph.summary.omitted_files} tone="muted" />
        <Metric label="Memory" value={graph.summary.memory_nodes} tone="memory" />
        <Metric label="High risk" value={graph.summary.high_risk_files} tone="risk" />
        <Metric label="Tokens" value={formatNumber(snapshot.context.packed_tokens || 0)} tone="neutral" />
        <Metric label="Threads" value={activeThreads} tone={activeThreads ? "warn" : "good"} />
        <Metric label="Integrations" value={missingIntegrations ? `${missingIntegrations} check` : "ready"} tone={missingIntegrations ? "warn" : "good"} />
      </div>
      <div className="content-grid">
        <Panel title="Selected Context" icon={FileText}>
          <ItemList
            items={selectedFiles.slice(0, 8).map((node) => ({
              id: node.id,
              title: node.path || node.label,
              detail: node.summary || "Selected for current task",
              tone: node.risk || "neutral"
            }))}
            empty="No selected files found."
            onSelect={onSelect}
          />
        </Panel>
        <Panel title="Omitted But Relevant" icon={ListFilter}>
          <ItemList
            items={omittedFiles.slice(0, 8).map((node) => ({
              id: node.id,
              title: node.path || node.label,
              detail: node.summary || "Candidate context outside the selected pack",
              tone: "muted"
            }))}
            empty="No omitted candidates found."
            onSelect={onSelect}
          />
        </Panel>
        <Panel title="Risk & Tests" icon={ShieldAlert}>
          <div className="stack-sm">
            {highRisk.slice(0, 4).map((item) => (
              <button key={item.path} type="button" className="list-row" onClick={() => onSelect(`file:${item.path}`)}>
                <span>
                  <strong>{item.path}</strong>
                  <small>{(item.may_break || []).slice(0, 1).join("; ") || "High-risk task map file"}</small>
                </span>
                <span className="badge risk">High</span>
              </button>
            ))}
            {tests.slice(0, 5).map((test) => (
              <CommandAction key={test} label="Validation" command={test.endsWith(".py") ? `pytest ${test}` : test} onRunCommand={onRunCommand} />
            ))}
            {!highRisk.length && !tests.length ? <p className="empty">No risk or test hints found.</p> : null}
          </div>
        </Panel>
        <Panel title="Next Actions" icon={ClipboardList}>
          <div className="stack-sm">
            {snapshot.suggested_actions.slice(0, 6).map((action) => (
              <CommandAction key={`${action.label}:${action.command}`} label={action.label} command={action.command || ""} onRunCommand={onRunCommand} icon={CheckCircle2} />
            ))}
            {!snapshot.suggested_actions.length ? <p className="empty">No suggested actions found.</p> : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ThreadsView({
  snapshot,
  onRunAction
}: {
  snapshot: DashboardSnapshot;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
}) {
  const rows = snapshot.thread_rows || [];
  return (
    <div className="view-stack">
      <SectionTitle title="Threads" subtitle="Active AgentPack sessions, file overlap, and cleanup controls." />
      <div className="inline-actions">
        <button type="button" className="primary-action" onClick={() => onRunAction("threads_active")}>
          <PlayCircle size={16} aria-hidden="true" />
          List active
        </button>
        <button type="button" className="secondary-action" onClick={() => onRunAction("prune_threads", { older_than: "7d" })}>
          Prune old
        </button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Thread</th>
              <th>Status</th>
              <th>Task</th>
              <th>Branch</th>
              <th>Files</th>
              <th>Overlap</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.thread_id}>
                <td><code>{row.thread_id}</code></td>
                <td><span className={`badge ${riskTone(row.status)}`}>{row.status || "unknown"}</span></td>
                <td>{row.task || "No task"}</td>
                <td>{row.branch || "unknown"}</td>
                <td>{(row.selected_count || 0) + (row.dirty_count || 0)}</td>
                <td>{(row.overlap_files || []).slice(0, 3).join(", ") || "none"}</td>
                <td>
                  <button type="button" className="run-button" onClick={() => onRunAction("archive_thread", { thread_id: row.thread_id })}>
                    Archive
                  </button>
                </td>
              </tr>
            ))}
            {!rows.length ? <tr><td colSpan={7}>No thread rows found.</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ContextView({
  snapshot,
  onSelect,
  onRunAction,
  onRunCommand
}: {
  snapshot: DashboardSnapshot;
  onSelect: (id: string) => void;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onRunCommand: (command: string) => void;
}) {
  return (
    <div className="view-stack">
      <SectionTitle title="Context" subtitle="Guard, route, pack, status, selected files, and validation hints." />
      <div className="metric-grid">
        <Metric label="State" value={snapshot.context.status} tone={riskTone(snapshot.context.status)} />
        <Metric label="Mode" value={snapshot.context.mode || "unknown"} tone="neutral" />
        <Metric label="Packed tokens" value={formatNumber(snapshot.context.packed_tokens || 0)} tone="neutral" />
        <Metric label="Selected" value={snapshot.context.selected_files_count || 0} tone="good" />
        <Metric label="Saved" value={`${snapshot.context.saving_pct || 0}%`} tone="memory" />
      </div>
      <EvidenceReceipt snapshot={snapshot} />
      <div className="content-grid">
        <Panel title="Context Actions" icon={Database}>
          <div className="action-grid">
            <button type="button" className="primary-action" onClick={() => onRunAction("refresh_context", { agent: "codex", thread: "global" })}>Refresh context</button>
            <button type="button" className="secondary-action" onClick={() => onRunAction("pack_auto")}>Pack auto</button>
            <button type="button" className="secondary-action" onClick={() => onRunAction("route_context", { task: snapshot.task.text || "describe the task" })}>Route task</button>
            <button type="button" className="secondary-action" onClick={() => onRunAction("status")}>Status</button>
          </div>
        </Panel>
        <Panel title="Validation Hints" icon={ShieldAlert}>
          <div className="stack-sm">
            {unique(snapshot.task_map.flatMap((item) => item.tests_to_run || [])).slice(0, 8).map((test) => (
              <CommandAction key={test} label="Run validation" command={test.endsWith(".py") ? `pytest ${test}` : test} onRunCommand={onRunCommand} />
            ))}
            {!snapshot.task_map.some((item) => item.tests_to_run?.length) ? <p className="empty">No validation hints found.</p> : null}
          </div>
        </Panel>
      </div>
      <RiskTestsView snapshot={snapshot} onSelect={onSelect} onRunCommand={onRunCommand} />
    </div>
  );
}

function EvidenceReceipt({ snapshot }: { snapshot: DashboardSnapshot }) {
  const checkedAt = snapshot.context.generated_at || snapshot.generated_at;
  const verificationCommands = unique(snapshot.task_map.flatMap((item) => item.tests_to_run || [])).slice(0, 5);
  return (
    <Panel title="Evidence Receipt" icon={ShieldAlert}>
      <dl className="evidence-receipt">
        <div>
          <dt>Checked</dt>
          <dd>{checkedAt ? `${formatAge(checkedAt)} · ${formatTimestamp(checkedAt)}` : "No check timestamp"}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>{snapshot.context.source_command || snapshot.project.path}</dd>
        </div>
        <div>
          <dt>Owning task</dt>
          <dd>{snapshot.task.thread_id || "global"}</dd>
        </div>
        <div>
          <dt>Freshness</dt>
          <dd>{snapshot.context.stale_reason || snapshot.context.status}</dd>
        </div>
        <div className="full">
          <dt>Verification</dt>
          <dd>
            {verificationCommands.length ? verificationCommands.map((command) => <code key={command}>{command}</code>) : "No verification command recorded"}
          </dd>
        </div>
      </dl>
    </Panel>
  );
}

function MapView({
  dashboardMap,
  graph,
  snapshot,
  actionHistory,
  query,
  onQueryChange,
  selectedId,
  onSelect,
  onRunAction,
  onRunCommand,
  onImpactLoaded
}: {
  dashboardMap: DashboardMap;
  graph: DashboardGraph;
  snapshot: DashboardSnapshot;
  actionHistory: ActionHistoryRow[];
  query: string;
  onQueryChange: (value: string) => void;
  selectedId: string;
  onSelect: (id: string) => void;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onRunCommand: (command: string) => void;
  onImpactLoaded: (impact: DashboardImpactPayload) => void;
}) {
  const { state: dashboardState, dispatch } = useDashboardState();
  const mode = dashboardState.mapMode;
  const setMode = (value: MapMode | ((current: MapMode) => MapMode)) => dispatch({ type: "map_mode", value: typeof value === "function" ? value(dashboardState.mapMode) : value });
  const [demoMode, setDemoMode] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [sideCollapsed, setSideCollapsed] = useState(() => typeof window !== "undefined" && window.innerWidth <= 760);
  const cameraSignal = dashboardState.cameraRequest;
  const [hoverInfo, setHoverInfo] = useState<MapHoverInfo | null>(null);
  const [impactPayload, setImpactPayload] = useState<DashboardImpactPayload | null>(null);
  const [impactError, setImpactError] = useState("");
  const [architectureMap, setArchitectureMap] = useState<ArchitecturePRMapPayload | null>(null);
  const [architectureMapError, setArchitectureMapError] = useState("");
  const [architectureBaseRef, setArchitectureBaseRef] = useState("origin/main");
  const [architectureHeadRef, setArchitectureHeadRef] = useState("HEAD");
  const mapRootRef = useRef<HTMLDivElement | null>(null);
  const automaticTableFallback = useRef(false);
  const selectedBuilding = dashboardMap.buildings.find((building) => building.node_id === selectedId);
  const selectedRoad = dashboardMap.roads.find((road) => road.id === selectedId);
  const pointById = useMemo(() => {
    const points = new Map<string, { x: number; z: number }>();
    dashboardMap.buildings.forEach((building) => {
      points.set(building.id, { x: building.x, z: building.z });
      points.set(building.node_id, { x: building.x, z: building.z });
    });
    dashboardMap.landmarks.forEach((landmark) => points.set(landmark.id, { x: landmark.x, z: landmark.z }));
    return points;
  }, [dashboardMap]);
  const activeMapInfo = selectedBuilding
    ? buildingHoverInfo(selectedBuilding)
    : selectedRoad
      ? roadHoverInfo(selectedRoad, roadMidpoint(selectedRoad, pointById))
      : null;
  const payloadRequiredActions = new Set(["work", "route_task", "retrieve"]);
  const primaryCatalog = (snapshot.command_catalog || []).filter((item) => item.primary && !payloadRequiredActions.has(item.id)).slice(0, 8);
  const weather = dashboardMap.weather || [];
  const impactPaths = useMemo(() => new Set([
    ...(snapshot.selected_files || []).map((row) => row.path),
    ...(snapshot.task_map || []).map((row) => row.path)
  ].filter(Boolean)), [snapshot.selected_files, snapshot.task_map]);
  const selectedImpactEntity = impactPayload?.entities.find((entity) => entity.id === selectedId);
  const selectedImpactRelationship = impactPayload?.relationships.find((relationship) => relationship.id === selectedId);
  const showSide = !demoMode && !sideCollapsed;
  useEffect(() => {
    const handleFullscreenChange = () => {
      setFullscreen(document.fullscreenElement === mapRootRef.current);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);
  useEffect(() => {
    const hasMapData = Boolean(dashboardMap.buildings.length || dashboardMap.roads.length);
    if (!hasMapData && mode === "city") {
      automaticTableFallback.current = true;
      setMode("table");
    } else if (hasMapData && automaticTableFallback.current && mode === "table" && hasWebGLSupport()) {
      automaticTableFallback.current = false;
      setMode("city");
    }
  }, [dashboardMap.buildings.length, dashboardMap.roads.length, mode]);
  useEffect(() => {
    loadDashboardImpact(new URLSearchParams({ limit: "300" }))
      .then((value) => { setImpactPayload(value); onImpactLoaded(value); setImpactError(""); })
      .catch((error: unknown) => setImpactError(error instanceof Error ? error.message : "Impact data is unavailable"));
  }, [snapshot.generated_at]);
  useEffect(() => {
    loadArchitecturePRMap(new URLSearchParams({ base: architectureBaseRef, head: architectureHeadRef, limit: "250" }))
      .then((value) => { setArchitectureMap(value); setArchitectureMapError(""); })
      .catch((error: unknown) => setArchitectureMapError(error instanceof Error ? error.message : "Architecture PR map unavailable"));
  }, [architectureBaseRef, architectureHeadRef, snapshot.generated_at]);
  const toggleFullscreen = async () => {
    const element = mapRootRef.current;
    if (!element) return;
    if (document.fullscreenElement === element) {
      await document.exitFullscreen().catch(() => undefined);
      setFullscreen(false);
      return;
    }
    if (element.requestFullscreen) {
      await element.requestFullscreen().catch(() => setFullscreen(true));
    } else {
      setFullscreen((value) => !value);
    }
  };

  return (
    <div ref={mapRootRef} className={["map-view", demoMode ? "demo" : "", fullscreen ? "fullscreen" : ""].filter(Boolean).join(" ")}>
      <section className="map-hero">
        <div>
          <p className="eyebrow">AgentPack Map</p>
          <h1>Live context city for this local task</h1>
          <p className="muted">Buildings are files. Building class is confidence. Route class shows relationship strength.</p>
        </div>
        <div className="map-hero-actions">
          <button type="button" className={demoMode ? "toolbar-button active" : "toolbar-button"} onClick={() => setDemoMode((value) => !value)}>Demo</button>
          <button type="button" className={sideCollapsed ? "toolbar-button active" : "toolbar-button"} onClick={() => setSideCollapsed((value) => !value)}>
            {sideCollapsed ? "Show cards" : "Hide cards"}
          </button>
          <button type="button" className={fullscreen ? "toolbar-button active" : "toolbar-button"} onClick={toggleFullscreen}>
            {fullscreen ? <Minimize2 size={14} aria-hidden="true" /> : <Maximize2 size={14} aria-hidden="true" />}
            {fullscreen ? "Exit full screen" : "Full screen"}
          </button>
          <button type="button" className={mode === "city" ? "toolbar-button active" : "toolbar-button"} onClick={() => setMode(hasWebGLSupport() ? "city" : "table")}>
            <Building2 size={14} aria-hidden="true" /> 3D City
          </button>
          <button type="button" className={mode === "network" ? "toolbar-button active" : "toolbar-button"} onClick={() => setMode("network")}>
            <Network size={14} aria-hidden="true" /> Network
          </button>
          <button type="button" data-testid="semantic-mode-button" className={mode === "semantic" ? "toolbar-button active" : "toolbar-button"} onClick={() => setMode("semantic")}>
            <GitBranch size={14} aria-hidden="true" /> Semantic graph
          </button>
          <button type="button" className={mode === "table" ? "toolbar-button active" : "toolbar-button"} onClick={() => setMode("table")}>
            <Table2 size={14} aria-hidden="true" /> Table
          </button>
          <button type="button" className="toolbar-button" onClick={() => dispatch({ type: "focus" })}>
            <RefreshCcw size={14} aria-hidden="true" /> Reset
          </button>
        </div>
      </section>

      <div className="metric-grid">
        <Metric label="Districts" value={dashboardMap.summary.district_count} tone="neutral" />
        <Metric label="Buildings" value={dashboardMap.summary.building_count} tone="good" />
        <Metric label="Selected" value={dashboardMap.summary.selected_buildings} tone="good" />
        <Metric label="High risk" value={dashboardMap.summary.high_risk_buildings} tone={dashboardMap.summary.high_risk_buildings ? "risk" : "good"} />
        <Metric label="Max score" value={formatNumber(Math.round(dashboardMap.summary.max_score || 0))} tone="memory" />
        <Metric label="Task impact" value={impactPaths.size} tone="memory" />
      </div>
      {mode === "network" ? (
        <label className="search-box map-search">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">Search context network</span>
          <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search files, tests, or memory" />
        </label>
      ) : null}

      <ArchitecturePRPanel
        payload={architectureMap}
        error={architectureMapError}
        baseRef={architectureBaseRef}
        headRef={architectureHeadRef}
        onLoad={(base, head) => {
          setArchitectureBaseRef(base);
          setArchitectureHeadRef(head);
        }}
      />

      <div className="map-layout">
        <section className="map-stage" aria-label="AgentPack context map">
          {mode === "city" ? (
            hasWebGLSupport() ? (
              <MapErrorBoundary resetKey={`${dashboardMap.generated_at}:${cameraSignal}`} onError={() => setMode("table")} fallback={<MapTable dashboardMap={dashboardMap} onSelect={onSelect} />}>
                <Suspense fallback={<div className="city-loading">Loading 3D city map...</div>}>
                  <ContextCityMap dashboardMap={dashboardMap} impactScene={impactPayload?.scene || null} impactPaths={impactPaths} selectedId={selectedId} hoverInfo={hoverInfo} cameraSignal={cameraSignal} demoMode={demoMode} onSelect={onSelect} onHover={setHoverInfo} />
                </Suspense>
              </MapErrorBoundary>
            ) : (
              <MapTable dashboardMap={dashboardMap} onSelect={onSelect} />
            )
          ) : mode === "network" ? (
            <TaskGraph graph={graph} query={query} selectedId={selectedId} onSelect={onSelect} />
          ) : mode === "semantic" ? (
            <SemanticGraphNetwork graph={snapshot.semantic_graph} onSelect={onSelect} />
          ) : (
            <MapTable dashboardMap={dashboardMap} onSelect={onSelect} />
          )}
        </section>

        {showSide ? (
          <aside className="map-side">
            <Panel title="Map Focus" icon={Search}>
              {activeMapInfo ? (
                <div className="map-hover-card">
                  <span className={`badge ${riskTone(activeMapInfo.tone)}`}>{activeMapInfo.kind}</span>
                  <strong>{activeMapInfo.title}</strong>
                  <small>{activeMapInfo.subtitle}</small>
                  <dl>
                    {activeMapInfo.rows.map((row) => (
                      <div key={`${row.label}:${row.value}`}>
                        <dt>{row.label}</dt>
                        <dd>{row.value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ) : (
                <p className="empty">Hover a habitat or route to inspect map metadata.</p>
              )}
            </Panel>
            <Panel title="Map Legend" icon={MapIcon}>
              <div className="map-legend">
                <span><i className="legend-height" /> Building scale = confidence</span>
                <span><i className="legend-selected" /> Glow = selected context</span>
                <span><i className="legend-risk" /> Red = high risk</span>
                <span><i className="legend-memory" /> Cyan = memory linked</span>
                <span><i className="legend-impact" /> Lime = task impact</span>
                <span><i className="legend-expressway" /> Expressway = high confidence route</span>
                <span><i className="legend-highway" /> Highway = medium confidence route</span>
                <span><i className="legend-county" /> County road = low confidence route</span>
              </div>
            </Panel>
            <Panel title="Why This File" icon={Search}>
              {selectedImpactEntity ? (
                <div className="map-building-detail">
                  <span className={`badge ${riskTone(selectedImpactEntity.risk)}`}>{selectedImpactEntity.kind}</span>
                  <strong>{selectedImpactEntity.label}</strong>
                  <small>{selectedImpactEntity.path}{selectedImpactEntity.line ? `:${selectedImpactEntity.line}` : ""}</small>
                  <small>{labelize(selectedImpactEntity.confidence_tier || "unknown confidence")} · {selectedImpactEntity.task_relevant ? "task impact" : "related context"}</small>
                  {(selectedImpactEntity.reasons || []).slice(0, 5).map((reason) => <p key={reason}>{reason}</p>)}
                  {(selectedImpactEntity.related_ids || []).length ? <small>Related entities: {selectedImpactEntity.related_ids.length}</small> : null}
                  <div className="catalog-grid compact">{(selectedImpactEntity.actions || []).map((action) => <button key={action} type="button" className="command-chip" onClick={() => onRunAction(action, { target: selectedImpactEntity.path })}>{labelize(action)}</button>)}</div>
                </div>
              ) : selectedImpactRelationship ? (
                <div className="map-building-detail">
                  <span className="badge memory">relationship</span>
                  <strong>{labelize(selectedImpactRelationship.relationship)}</strong>
                  <small>{selectedImpactRelationship.source_id} → {selectedImpactRelationship.target_id}</small>
                  <small>{labelize(selectedImpactRelationship.confidence_tier)} · {Math.round(selectedImpactRelationship.strength * 100)}% strength</small>
                  {(selectedImpactRelationship.evidence || []).map((item) => <p key={`${String(item.path || "")}:${String(item.start_line || "")}`}>{String(item.path || "")}{Number(item.start_line || 0) ? `:${Number(item.start_line)}` : ""} {String(item.note || "")}</p>)}
                </div>
              ) : selectedBuilding ? (
                <div className="map-building-detail">
                  <strong>{selectedBuilding.path}</strong>
                  <span className={`badge ${riskTone(selectedBuilding.risk)}`}>{selectedBuilding.risk || "unknown"}</span>
                  <small>
                    {labelize(selectedBuilding.building_tier || "pavilion")} · {labelize(selectedBuilding.building_type || "file")} · score {Math.round(selectedBuilding.score)} · confidence {Math.round(selectedBuilding.confidence * 100)}%
                  </small>
                  <small>{labelize(selectedBuilding.confidence_source || "fallback")} · {selectedBuilding.include_mode || "mode unknown"}</small>
                  {(selectedBuilding.reasons || []).slice(0, 5).map((reason) => <p key={reason}>{reason}</p>)}
                  <MapBuildingActions building={selectedBuilding} onRunAction={onRunAction} onRunCommand={onRunCommand} />
                </div>
              ) : (
                <p className="empty">{impactError || "Select a file, symbol, test, action, or relationship to inspect its evidence."}</p>
              )}
            </Panel>
            <Panel title="Weather" icon={AlertTriangle}>
              <div className="stack-sm">
                {weather.map((item) => (
                  <div key={item.id} className="list-row passive">
                    <span>
                      <strong>{item.label}</strong>
                      <small>{item.detail || "No detail"}</small>
                    </span>
                    <span className={`badge ${riskTone(item.tone)}`}>{item.tone || "info"}</span>
                  </div>
                ))}
                {!weather.length ? <p className="empty">No active map alerts.</p> : null}
              </div>
            </Panel>
            <Panel title="Command Palette" icon={TerminalSquare}>
              <div className="catalog-grid compact">
                {(primaryCatalog.length ? primaryCatalog : snapshot.command_catalog?.slice(0, 6) || []).map((item) => (
                  <button key={item.id} type="button" className="command-chip" onClick={() => onRunAction(item.id)}>
                    {item.label}
                  </button>
                ))}
                <button type="button" className="command-chip" onClick={() => onRunAction("doctor")}>Doctor</button>
                <button type="button" className="command-chip" onClick={() => onRunAction("refresh_context", { agent: "codex", thread: "global" })}>Refresh context</button>
              </div>
            </Panel>
            <Panel title="Action History" icon={Activity}>
              <ActionHistoryList rows={actionHistory} />
            </Panel>
          </aside>
        ) : null}
      </div>
    </div>
  );
}

function ArchitecturePRPanel({
  payload,
  error,
  baseRef,
  headRef,
  onLoad
}: {
  payload: ArchitecturePRMapPayload | null;
  error: string;
  baseRef: string;
  headRef: string;
  onLoad: (baseRef: string, headRef: string) => void;
}) {
  const [baseInput, setBaseInput] = useState(baseRef);
  const [headInput, setHeadInput] = useState(headRef);
  const [district, setDistrict] = useState("");
  const [kind, setKind] = useState("");
  const [confidence, setConfidence] = useState("");
  const [changedOnly, setChangedOnly] = useState(true);
  const nodes = (payload?.nodes || []).filter((node) => (
    (!district || node.domain === district)
    && (!kind || node.entity_type === kind)
    && (!confidence || node.confidence_tier === confidence)
    && (!changedOnly || ["added", "removed", "changed", "alias", "ambiguous", "context"].includes(node.status))
  ));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = (payload?.edges || []).filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  const policyWarnings = [
    ...(payload?.policies?.violations || []).filter((violation) => violation.blocking !== true),
    ...(payload?.policies?.warnings || [])
  ];
  return (
    <Panel title="PR architecture map" icon={GitBranch}>
      <form className="inline-actions" onSubmit={(event) => { event.preventDefault(); if (baseInput.trim() && headInput.trim()) onLoad(baseInput.trim(), headInput.trim()); }}>
        <label>Base ref <input value={baseInput} onChange={(event) => setBaseInput(event.target.value)} aria-label="Architecture map base ref" /></label>
        <label>Head ref <input value={headInput} onChange={(event) => setHeadInput(event.target.value)} aria-label="Architecture map head ref" /></label>
        <button type="submit" className="secondary-action">Load refs</button>
      </form>
      {error ? <p className="empty">{error}</p> : payload ? (
        <>
          <div className="inline-actions">
            <label>District <select value={district} onChange={(event) => setDistrict(event.target.value)}><option value="">All</option>{payload.districts.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
            <label>Kind <select value={kind} onChange={(event) => setKind(event.target.value)}><option value="">All</option>{Array.from(new Set(payload.nodes.map((item) => item.entity_type))).sort().map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
            <label>Confidence <select value={confidence} onChange={(event) => setConfidence(event.target.value)}><option value="">All</option><option value="structured">Structured</option><option value="best_effort">Best effort</option><option value="file_level">File level</option></select></label>
            <label><input type="checkbox" checked={changedOnly} onChange={(event) => setChangedOnly(event.target.checked)} /> Changed only</label>
          </div>
          <div className="metric-grid">
            <Metric label="Base" value={payload.base_sha.slice(0, 8)} tone="neutral" />
            <Metric label="Head" value={payload.head_sha.slice(0, 8)} tone="neutral" />
            <Metric label="Nodes" value={nodes.length} tone="good" />
            <Metric label="Roads" value={edges.length} tone="memory" />
            <Metric label="Policy warnings" value={policyWarnings.length} tone={policyWarnings.length ? "risk" : "good"} />
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>District</th><th>Location</th><th>Change</th><th>Confidence</th><th>Roads</th></tr></thead>
              <tbody>{nodes.slice(0, 80).map((node) => <tr key={node.id}><td>{node.domain}</td><td><strong>{node.label}</strong><small>{node.path}</small></td><td><span className={`badge ${node.status === "removed" || node.status === "ambiguous" ? "risk" : node.status === "added" ? "good" : "memory"}`}>{node.status}</span></td><td>{node.confidence_tier}</td><td>{edges.filter((edge) => edge.source === node.id || edge.target === node.id).length}</td></tr>)}</tbody>
            </table>
          </div>
          {!nodes.length ? <p className="empty">No architecture changes match filters.</p> : null}
        </>
      ) : <LoadingState phase="Loading PR architecture map" />}
    </Panel>
  );
}

class MapErrorBoundary extends Component<
  { children: ReactNode; fallback: ReactNode; onError: () => void; resetKey: string },
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch() {
    this.props.onError();
  }

  componentDidUpdate(previous: { resetKey: string }) {
    if (previous.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false });
    }
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

function MapBuildingActions({
  building,
  onRunAction,
  onRunCommand
}: {
  building: MapBuilding;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onRunCommand: (command: string) => void;
}) {
  const refs = new Set(building.action_refs || []);
  return (
    <div className="map-action-row">
      {refs.has("open_file") ? <CommandAction label="Open file" command={building.path} kind="path" compact onRunCommand={onRunCommand} /> : null}
      {refs.has("retrieve") ? (
        <button type="button" className="command-chip" onClick={() => onRunAction("retrieve", { target: building.path })}>
          Retrieve context
        </button>
      ) : null}
      {refs.has("run_tests") ? (
        <button type="button" className="command-chip" onClick={() => onRunAction("dev_check")}>
          Run validation
        </button>
      ) : null}
      {refs.has("refresh_context") ? (
        <button type="button" className="command-chip" onClick={() => onRunAction("refresh_context", { agent: "codex", thread: "global" })}>
          Refresh context
        </button>
      ) : null}
      {refs.has("ignore_suggest") ? (
        <button type="button" className="command-chip" onClick={() => onRunAction("ignore_suggest")}>
          Mark risky / ignore
        </button>
      ) : null}
    </div>
  );
}

function roadMidpoint(road: MapRoad, points: Map<string, { x: number; z: number }>): [number, number, number] {
  const source = points.get(road.source);
  const target = points.get(road.target);
  if (!source || !target) return [0, 2, 0];
  return [(source.x + target.x) / 2, 2, (source.z + target.z) / 2];
}

function SemanticGraphTable({ graph }: { graph: SemanticGraphSummary }) {
  const [relationship, setRelationship] = useState("");
  const [confidence, setConfidence] = useState("");
  const entities = useMemo(() => new Map(graph.entities.map((entity) => [entity.entity_key, entity])), [graph.entities]);
  const edges = graph.edges.filter((edge) => (!relationship || edge.relationship === relationship) && (!confidence || edge.confidence_tier === confidence));
  return (
    <div className="semantic-graph-table" data-testid="semantic-evidence-table">
      <div className="semantic-graph-toolbar" data-testid="semantic-evidence-toolbar">
        <label>Relationship <select data-testid="semantic-table-relationship-filter" value={relationship} onChange={(event) => setRelationship(event.target.value)}><option value="">All</option>{Object.keys(graph.relationship_counts).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label>Confidence <select data-testid="semantic-table-confidence-filter" value={confidence} onChange={(event) => setConfidence(event.target.value)}><option value="">All</option><option value="structured">Structured</option><option value="best_effort">Best effort</option><option value="file_level">File level</option></select></label>
        <span className="muted">{graph.entity_count} entities · {graph.edge_count} edges · {graph.unresolved_count} unresolved · parsed {graph.cache_stats?.parsed_files ?? 0} · reused {graph.cache_stats?.reused_files ?? 0}</span>
      </div>
      <div className="semantic-graph-receipts">
        {edges.slice(0, 200).map((edge) => {
          const source = entities.get(edge.source);
          const target = entities.get(edge.target);
          const evidence = edge.evidence?.[0];
          return <div className="semantic-edge-row" key={edge.edge_key}><strong>{source?.name || edge.source_name || edge.source}</strong><span className="semantic-edge-type">{edge.relationship}</span><strong>{target?.name || edge.target_name || edge.target}</strong><small>{evidence?.path || "unknown source"}{evidence?.start_line ? `:${evidence.start_line}` : ""}{evidence?.note ? ` · ${evidence.note}` : ""}</small></div>;
        })}
        {!edges.length ? <p className="empty">No semantic relationships match these filters.</p> : null}
      </div>
    </div>
  );
}

function SemanticGraphNetwork({ graph, onSelect }: { graph: SemanticGraphSummary; onSelect: (id: string) => void }) {
  const [relationship, setRelationship] = useState("");
  const [confidence, setConfidence] = useState("");
  const [language, setLanguage] = useState("");
  const [evidenceSource, setEvidenceSource] = useState("");
  const [showTable, setShowTable] = useState(false);
  const [selectedEdge, setSelectedEdge] = useState<string>("");
  const [selectedEntity, setSelectedEntity] = useState<string>("");
  const [remoteGraph, setRemoteGraph] = useState<SemanticGraphSummary | null>(null);
  const activeGraph = remoteGraph || graph;
  useEffect(() => {
    if (window.location.protocol === "file:") return;
    const params = new URLSearchParams({ limit: "200" });
    if (relationship) params.set("relationship", relationship);
    if (confidence) params.set("confidence", confidence);
    if (language) params.set("language", language);
    if (evidenceSource) params.set("evidence_source", evidenceSource);
    fetch(apiUrl(`/api/dashboard/v2/impact?${params.toString()}`), { headers: authHeaders() })
      .then((response) => response.ok ? response.json() as Promise<{ summary?: SemanticGraphSummary }> : Promise.reject(new Error("impact request failed")))
      .then((payload) => setRemoteGraph(payload.summary || null))
      .catch(() => setRemoteGraph(null));
  }, [confidence, evidenceSource, language, relationship]);
  const entities = useMemo(() => new Map(activeGraph.entities.map((entity) => [entity.entity_key, entity])), [activeGraph.entities]);
  const languages = useMemo(() => Array.from(new Set(graph.entities.map((entity) => entity.language).filter(Boolean))).sort(), [graph.entities]);
  const evidenceSources = useMemo(() => Array.from(new Set(graph.edges.flatMap((edge) => (edge.evidence || []).map((item) => item.source).filter(Boolean)))).sort(), [graph.edges]);
  const edges = useMemo(
    () => activeGraph.edges.filter((edge) => (!relationship || edge.relationship === relationship) && (!confidence || edge.confidence_tier === confidence) && (!language || entities.get(edge.source)?.language === language || entities.get(edge.target)?.language === language) && (!evidenceSource || (edge.evidence || []).some((item) => item.source === evidenceSource))).slice(0, 160),
    [activeGraph.edges, confidence, entities, evidenceSource, language, relationship]
  );
  const visibleKeys = useMemo(() => new Set(edges.flatMap((edge) => [edge.source, edge.target])), [edges]);
  const nodes = useMemo<Node[]>(() => Array.from(visibleKeys).map((key, index) => {
    const entity = entities.get(key);
    const unresolved = entity?.type === "unresolved" || entity?.type === "external";
    return {
      id: key,
      position: { x: (index % 4) * 240, y: Math.floor(index / 4) * 120 },
      data: { label: entity ? `${entity.name}\n${entity.path}${entity.line ? `:${entity.line}` : ""}` : key },
      className: `semantic-node ${unresolved ? "unresolved" : ""}`
    };
  }), [entities, visibleKeys]);
  const flowEdges = useMemo<Edge[]>(() => edges.map((edge) => ({
    id: edge.edge_key,
    source: edge.source,
    target: edge.target,
    label: edge.relationship,
    animated: edge.confidence_tier === "structured",
    className: `semantic-flow-edge ${edge.confidence_tier || "best_effort"}`,
    style: { stroke: edge.edge_key === selectedEdge ? "#f6c453" : edge.confidence_tier === "structured" ? "#7dd3fc" : "#74839a" }
  })), [edges, selectedEdge]);
  const selected = activeGraph.edges.find((edge) => edge.edge_key === selectedEdge);
  return (
    <div className="semantic-graph-network">
      <div className="semantic-network-toolbar" data-testid="semantic-network-toolbar">
        <label>Relationship <select data-testid="semantic-relationship-filter" value={relationship} onChange={(event) => setRelationship(event.target.value)}><option value="">All</option>{Object.keys(graph.relationship_counts).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label>Confidence <select data-testid="semantic-confidence-filter" value={confidence} onChange={(event) => setConfidence(event.target.value)}><option value="">All</option><option value="structured">Structured</option><option value="best_effort">Best effort</option><option value="file_level">File level</option></select></label>
        <label>Language <select data-testid="semantic-language-filter" value={language} onChange={(event) => setLanguage(event.target.value)}><option value="">All</option>{languages.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label>Evidence <select data-testid="semantic-evidence-filter" value={evidenceSource} onChange={(event) => setEvidenceSource(event.target.value)}><option value="">All</option>{evidenceSources.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <span className="muted">{nodes.length} nodes · {edges.length} edges of {activeGraph.edge_count} · unresolved {activeGraph.unresolved_count}</span>
        <button type="button" data-testid="semantic-evidence-toggle" className={showTable ? "toolbar-button active" : "toolbar-button"} onClick={() => setShowTable((value) => !value)}><Table2 size={14} aria-hidden="true" /> {showTable ? "Hide evidence" : "Evidence table"}</button>
      </div>
      {showTable ? <SemanticGraphTable graph={activeGraph} /> : (
        <div className="semantic-network-canvas" data-testid="semantic-network-canvas">
          <ReactFlow nodes={nodes} edges={flowEdges} fitView nodesDraggable nodesConnectable={false} panOnScroll minZoom={0.2} zoomOnDoubleClick={false} onNodeClick={(_event, node) => { setSelectedEntity(node.id); onSelect(node.id); }} onEdgeClick={(_event, edge) => { setSelectedEdge(edge.id); onSelect(edge.id); }} onlyRenderVisibleElements>
            <Background />
            <MiniMap pannable zoomable className="graph-minimap" />
            <Controls />
          </ReactFlow>
        </div>
      )}
      <div className="semantic-network-receipt" data-testid="semantic-edge-receipt">
        {selectedEntity && entities.get(selectedEntity) ? (() => {
          const entity = entities.get(selectedEntity)!;
          return <><strong>{entity.name}</strong><small>{entity.path}{entity.line ? `:${entity.line}` : ""} · {entity.type} · {entity.confidence_tier || "unknown confidence"}</small><p>Select a related edge to inspect its source evidence.</p></>;
        })() : selected ? (() => {
          const source = entities.get(selected.source);
          const target = entities.get(selected.target);
          const evidence = selected.evidence?.[0];
          return <><strong>{source?.name || selected.source} <span className="semantic-edge-type">{selected.relationship}</span> {target?.name || selected.target}</strong><small>{evidence?.path || "unknown source"}{evidence?.start_line ? `:${evidence.start_line}` : ""}{evidence?.end_line && evidence.end_line !== evidence.start_line ? `-${evidence.end_line}` : ""} · {evidence?.source || "unknown extractor"} · {selected.confidence_tier || "unknown confidence"}{evidence?.source_hash ? ` · ${evidence.source_hash}` : ""}</small><p>{evidence?.note || "No evidence note recorded."}</p></>;
        })() : <span className="muted">Select an edge to inspect its source receipt.</span>}
      </div>
    </div>
  );
}

function MapTable({ dashboardMap, onSelect }: { dashboardMap: DashboardMap; onSelect: (id: string) => void }) {
  return (
    <div className="table-wrap map-table">
      <table>
        <caption className="sr-only">Context map files</caption>
        <thead>
          <tr>
            <th>File</th>
            <th>District</th>
            <th>Type</th>
            <th>Tier</th>
            <th>Confidence</th>
            <th>Source</th>
            <th>Risk</th>
            <th>Mode</th>
            <th>Tests</th>
          </tr>
        </thead>
        <tbody>
          {dashboardMap.buildings.map((building) => (
            <tr
              key={building.id}
              tabIndex={0}
              aria-label={`Inspect ${building.path}`}
              onClick={() => onSelect(building.node_id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(building.node_id);
                }
              }}
            >
              <td><code>{building.path}</code></td>
              <td>{building.district_id}</td>
              <td>{labelize(building.building_type || "unknown")}</td>
              <td>{labelize(building.building_tier || "pavilion")}</td>
              <td>{Math.round(building.confidence * 100)}%</td>
              <td>{labelize(building.confidence_source || "fallback")}</td>
              <td><span className={`badge ${riskTone(building.risk)}`}>{building.risk || "unknown"}</span></td>
              <td>{building.include_mode || "unknown"}</td>
              <td>{(building.tests || []).slice(0, 2).join(", ") || "none"}</td>
            </tr>
          ))}
          {!dashboardMap.buildings.length ? (
            <tr><td colSpan={9}>No map buildings found.</td></tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function ActionHistoryList({ rows }: { rows: ActionHistoryRow[] }) {
  if (!rows.length) {
    return <p className="empty">No dashboard actions recorded yet.</p>;
  }
  return (
    <ol className="action-history">
      {rows.slice(0, 8).map((row) => (
        <li key={row.action_id}>
          <span className={`timeline-dot ${riskTone(row.status)}`} />
          <div>
            <strong>{row.label || row.command || "AgentPack action"}</strong>
            <small>
              {row.status || "recorded"} · {formatTimestamp(row.started_at || row.ended_at)}{typeof row.duration_ms === "number" ? ` · ${formatDuration(row.duration_ms)}` : ""}
            </small>
            {row.command ? <code>{row.command}</code> : null}
            {row.output_summary ? <p>{row.output_summary}</p> : null}
            {row.follow_up_actions?.length ? (
              <div className="timeline-actions">
                {row.follow_up_actions.slice(0, 3).map((action) => <span key={action}>{labelize(action)}</span>)}
              </div>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

function TaskGraph({
  graph,
  query,
  selectedId,
  onSelect
}: {
  graph: DashboardGraph;
  query: string;
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const [nodePositions, setNodePositions] = useState<Record<string, { x: number; y: number }>>({});
  const [fitSignal, setFitSignal] = useState(0);
  const [showOverview, setShowOverview] = useState(false);
  const { nodes, edges } = useMemo(() => toFlowGraph(graph, query, selectedId, nodePositions), [graph, query, selectedId, nodePositions]);
  const handleClick: NodeMouseHandler = (_event, node) => onSelect(node.id);
  const handleNodeDragStop: OnNodeDrag = (_event, node) => {
    setNodePositions((current) => ({
      ...current,
      [node.id]: node.position
    }));
  };
  const resetLayout = () => {
    setNodePositions({});
    setFitSignal((value) => value + 1);
  };

  return (
    <div className="graph-shell">
      <div className="graph-toolbar">
        <span><CircleDot size={14} aria-hidden="true" /> {graph.summary.node_count} nodes</span>
        <span>{graph.summary.edge_count} edges</span>
        <span>Drag grip to move nodes</span>
        {graph.summary.truncated ? <span className="badge warn">Truncated</span> : null}
        <button type="button" className="toolbar-button" onClick={() => setFitSignal((value) => value + 1)}>Fit view</button>
        <button type="button" className="toolbar-button" onClick={resetLayout}>Reset layout</button>
        <button type="button" className={showOverview ? "toolbar-button active" : "toolbar-button"} onClick={() => setShowOverview((value) => !value)}>{showOverview ? "Hide overview" : "Overview"}</button>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.14, includeHiddenNodes: false }}
        onNodeClick={handleClick}
        onNodeDragStop={handleNodeDragStop}
        nodesDraggable
        nodesConnectable={false}
        panOnScroll
        minZoom={0.2}
        zoomOnDoubleClick={false}
        onlyRenderVisibleElements
      >
        <GraphViewportController fitSignal={fitSignal} nodeCount={nodes.length} />
        <Background />
        {showOverview ? <MiniMap pannable zoomable className="graph-minimap" maskColor="rgba(8, 13, 22, 0.72)" nodeColor="#4b668a" nodeStrokeColor="#80a9ff" /> : null}
        <Controls />
      </ReactFlow>
    </div>
  );
}

function GraphViewportController({ fitSignal, nodeCount }: { fitSignal: number; nodeCount: number }) {
  const { fitView, setCenter, getNodes } = useReactFlow();
  useEffect(() => {
    const timeout = window.setTimeout(() => {
      const nodes = getNodes();
      const taskNode = nodes.find((node) => node.id === "task:active");
      if (nodes.length <= 1 && taskNode) {
        setCenter(taskNode.position.x + 130, taskNode.position.y + 44, { duration: 0, zoom: 1 });
        return;
      }
      fitView({ duration: fitSignal > 0 ? 220 : 0, padding: 0.16, includeHiddenNodes: false });
    }, 80);
    return () => window.clearTimeout(timeout);
  }, [fitSignal, fitView, getNodes, nodeCount, setCenter]);
  return null;
}

function MemoryView({
  snapshot,
  graph,
  onSelect
}: {
  snapshot: DashboardSnapshot;
  graph: DashboardGraph;
  onSelect: (id: string) => void;
}) {
  const memoryNodes = graph.nodes.filter((node) => node.type === "episode" || node.type === "procedure");
  const [scope, setScope] = useState<LearningScope>("local");
  const [recommendations, setRecommendations] = useState<LearningRecommendationSet | null>(null);
  const [profile, setProfile] = useState<LearnerProfile | null>(null);
  const [profileWarnings, setProfileWarnings] = useState<string[]>([]);
  const [profileSaving, setProfileSaving] = useState(false);
  const [activeSession, setActiveSession] = useState<LearningSession | null>(null);
  const [coachingPrompt, setCoachingPrompt] = useState("");
  const [startingTopic, setStartingTopic] = useState("");
  const [learningError, setLearningError] = useState("");
  const [copyError, setCopyError] = useState("");
  const [learningLoading, setLearningLoading] = useState(true);
  const [copiedValue, setCopiedValue] = useState("");
  const learningRequestId = useRef(0);

  const refreshLearning = (nextScope: LearningScope, preserve = false) => {
    const requestId = ++learningRequestId.current;
    setLearningLoading(true);
    setLearningError("");
    if (!preserve) setRecommendations(null);
    Promise.all([loadLearningRecommendations(nextScope), loadLearningProfile()])
      .then(([value, profilePayload]) => {
        if (requestId !== learningRequestId.current) return;
        setRecommendations(value);
        setProfile(profilePayload.profile);
        setProfileWarnings(profilePayload.warnings);
      })
      .catch((error: unknown) => {
        if (requestId === learningRequestId.current) {
          setLearningError(error instanceof Error ? error.message : "Failed to load learning recommendations");
        }
      })
      .finally(() => { if (requestId === learningRequestId.current) setLearningLoading(false); });
  };

  useEffect(() => {
    refreshLearning(scope);
    return () => { learningRequestId.current += 1; };
  }, [scope]);

  useEffect(() => {
    const handleFocus = () => refreshLearning(scope, true);
    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [scope]);

  const copyText = async (key: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopyError("");
      setCopiedValue(key);
      window.setTimeout(() => setCopiedValue((current) => current === key ? "" : current), 1500);
    } catch {
      setCopyError("Clipboard access failed. Select the text manually.");
    }
  };

  const saveProfile = async (next: LearnerProfile) => {
    if (!profile || profileSaving) return;
    const previous = profile;
    setProfile(next);
    setProfileSaving(true);
    setLearningError("");
    try {
      const saved = await updateLearningProfile({
        mutation_id: learningMutationId("profile"),
        role: next.role,
        target_level: next.target_level
      });
      setProfile(saved);
      refreshLearning(scope, true);
    } catch (error) {
      setProfile(previous);
      setLearningError(error instanceof Error ? error.message : "Could not update learner profile");
    } finally {
      setProfileSaving(false);
    }
  };

  const startTopic = async (topicId: string, projectId: string) => {
    setStartingTopic(topicId);
    setLearningError("");
    try {
      const payload = await startLearningSession({
        mutation_id: learningMutationId("start"),
        topic_id: topicId,
        project_id: projectId
      });
      setActiveSession(payload.session);
      setCoachingPrompt(payload.coaching_prompt);
    } catch (error) {
      setLearningError(error instanceof Error ? error.message : "Could not start learning session");
    } finally {
      setStartingTopic("");
    }
  };

  return (
    <div className="view-stack">
      <SectionTitle title="Knowledge" subtitle="Next technical topics, assessed mastery, decisions, procedures, and durable project memory." />
      {profile ? (
        <div className="learning-profile-controls" aria-label="Learner profile">
          <label>
            <span>Role</span>
            <select
              aria-label="Learner role"
              value={profile.role}
              disabled={profileSaving}
              onChange={(event) => saveProfile({ ...profile, role: event.target.value as LearnerProfile["role"] })}
            >
              <option value="general">General</option>
              <option value="frontend">Frontend</option>
              <option value="backend">Backend</option>
              <option value="mobile">Mobile</option>
              <option value="platform">Platform</option>
            </select>
          </label>
          <label>
            <span>Target level</span>
            <select
              aria-label="Target level"
              value={profile.target_level}
              disabled={profileSaving}
              onChange={(event) => saveProfile({ ...profile, target_level: event.target.value as LearnerProfile["target_level"] })}
            >
              <option value="unspecified">Unspecified</option>
              <option value="junior">Junior</option>
              <option value="mid">Mid</option>
              <option value="senior">Senior</option>
              <option value="staff">Staff</option>
            </select>
          </label>
          <span className="learning-profile-state" aria-live="polite">{profileSaving ? "Saving" : "Global profile"}</span>
        </div>
      ) : null}
      <div className="learning-scope" role="group" aria-label="Learning recommendation scope">
        <button type="button" className={scope === "local" ? "active" : ""} onClick={() => setScope("local")}>This project</button>
        <button type="button" className={scope === "global" ? "active" : ""} onClick={() => setScope("global")}>All projects</button>
      </div>
      {recommendations ? (
        <div className="metric-grid learning-metrics">
          <Metric label="Mastered" value={recommendations.mastery_summary.mastered} tone="good" />
          <Metric label="Developing" value={recommendations.mastery_summary.developing} tone="memory" />
          <Metric label="Needs practice" value={recommendations.mastery_summary.needs_practice} tone="warn" />
          <Metric label="Unassessed" value={recommendations.mastery_summary.unassessed} tone="neutral" />
        </div>
      ) : null}
      {recommendations ? (
        <Panel title="Complete Engineer Matrix" icon={BarChart3}>
          <div className="competency-matrix" role="table" aria-label="Complete engineer competency matrix">
            <div className="competency-row competency-header" role="row">
              <span role="columnheader">Competency</span>
              <span role="columnheader">Status</span>
              <span role="columnheader">Proofs</span>
              <span role="columnheader">Artifacts</span>
              <span role="columnheader">Latest evidence</span>
            </div>
            {recommendations.competencies.map((competency) => (
              <div className="competency-row" role="row" key={competency.competency_id}>
                <span role="cell"><strong>{competency.name}</strong>{competency.role_emphasis ? <small>Role focus</small> : null}</span>
                <span role="cell" className={`badge ${competency.status === "mastered" ? "good" : competency.status === "needs_practice" ? "warn" : competency.status === "developing" ? "memory" : "neutral"}`}>{competency.status.replace("_", " ")}</span>
                <span role="cell">{competency.passing_proofs}</span>
                <span role="cell">{competency.verified_artifacts}</span>
                <span role="cell" className="competency-evidence">{competency.latest_evidence || "No assessed proof"}</span>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}
      {activeSession ? (
        <Panel title="Active Learning Session" icon={ClipboardList}>
          <div className="learning-session-view">
            <div className="learning-topic-meta">
              <span className="badge memory">{activeSession.competency_id?.replace("_", " ") || "competency"}</span>
              <span className="badge neutral">{activeSession.proof_requirement} proof</span>
              <span className="badge neutral">{activeSession.target_level}</span>
            </div>
            <strong>{activeSession.question}</strong>
            <p><b>Required artifact:</b> {activeSession.required_artifact || "Reasoned answer"}</p>
            <div>
              <small>Evidence files</small>
              <ul>{activeSession.evidence_files.map((path) => <li key={path}><code>{path}</code></li>)}</ul>
            </div>
            <div className="learning-prompt-copy">
              <pre>{coachingPrompt}</pre>
              <button type="button" className="toolbar-button" onClick={() => copyText("coaching-prompt", coachingPrompt)}>
                {copiedValue === "coaching-prompt" ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
                {copiedValue === "coaching-prompt" ? "Copied" : "Copy coaching prompt"}
              </button>
            </div>
          </div>
        </Panel>
      ) : null}
      <Panel title="Next Technical Topics" icon={Brain}>
        {learningLoading ? <p className="empty">Loading project learning history...</p> : null}
        {learningError ? (
          <div className="learning-error">
            <p>{learningError}</p>
            <button type="button" className="toolbar-button" onClick={() => refreshLearning(scope)}>Retry</button>
          </div>
        ) : null}
        {!learningLoading && !learningError && recommendations ? (
          <div className="learning-topic-list">
            {recommendations.topics.map((topic) => (
              <div key={topic.topic_id} className="learning-topic-row">
                <div className="learning-topic-copy">
                  <div className="learning-topic-meta">
                    <span className={`badge ${topic.lane === "weak_spot" ? "warn" : topic.lane === "breadth" ? "memory" : "good"}`}>{topic.lane.replace("_", " ")}</span>
                    <span className="badge neutral">{topic.competency_id.replace("_", " ")}</span>
                    <span className="badge neutral">{topic.proof_requirement} proof</span>
                    <span className="badge neutral">{topic.project.name}</span>
                    <span className="badge neutral">{topic.score}</span>
                  </div>
                  <strong>{topic.title}</strong>
                  <p>{topic.why_now}</p>
                  <p className="learning-exercise"><b>Exercise:</b> {topic.exercise}</p>
                  <small>{topic.evidence[0]?.kind}: {topic.evidence[0]?.path || topic.evidence[0]?.summary || "No direct file evidence"}</small>
                  <p className="learning-required-artifact"><b>Proof:</b> {topic.required_artifact}</p>
                  <div className="learning-command">
                    <code>{topic.start_command}</code>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={`Copy command for ${topic.title}`}
                      title="Copy start command"
                      onClick={() => copyText(topic.topic_id, topic.start_command)}
                    >
                      {copiedValue === topic.topic_id ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
                    </button>
                    <button
                      type="button"
                      className="toolbar-button"
                      disabled={Boolean(startingTopic)}
                      onClick={() => startTopic(topic.topic_id, topic.project.project_id)}
                    >
                      <PlayCircle size={15} aria-hidden="true" />
                      {startingTopic === topic.topic_id ? "Starting" : "Start"}
                    </button>
                  </div>
                </div>
              </div>
            ))}
            {!recommendations.topics.length ? <p className="empty">No evidence-backed topics yet. Complete more AgentPack work to build learning history.</p> : null}
            {copyError ? <p className="learning-warning">{copyError}</p> : null}
            {profileWarnings.map((warning) => <p key={warning} className="learning-warning">{warning}</p>)}
            {recommendations.warnings.map((warning) => <p key={warning} className="learning-warning">{warning}</p>)}
          </div>
        ) : null}
      </Panel>
      <div className="content-grid">
        <Panel title="Map Memory" icon={Brain}>
          <ItemList
            items={memoryNodes.map((node) => ({
              id: node.id,
              title: node.label,
              detail: node.summary || node.type,
              tone: node.stale ? "risk" : "memory"
            }))}
            empty="No memory nodes found."
            onSelect={onSelect}
          />
        </Panel>
        <Panel title="Learning Weak Spots" icon={AlertTriangle}>
          <div className="stack-sm">
            {snapshot.learning_weak_spots.slice(0, 8).map((spot) => (
              <div key={`${spot.concept}:${spot.latest_task}`} className="list-row passive">
                <span>
                  <strong>{spot.concept}</strong>
                  <small>{spot.latest_question || spot.latest_task || "Learning weak spot"}</small>
                </span>
                <span className="badge neutral">{spot.count || 0}</span>
              </div>
            ))}
            {!snapshot.learning_weak_spots.length ? <p className="empty">No learning weak spots found.</p> : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function learningMutationId(kind: string): string {
  const suffix = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `learning-${kind}-${suffix}`;
}

function RiskTestsView({
  snapshot,
  onSelect,
  onRunCommand
}: {
  snapshot: DashboardSnapshot;
  onSelect: (id: string) => void;
  onRunCommand: (command: string) => void;
}) {
  return (
    <div className="view-stack">
      <SectionTitle title="Risk & Tests" subtitle="Task-map risk, breakage hints, and validation commands." />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Path</th>
              <th>Risk</th>
              <th>Why</th>
              <th>Tests</th>
              <th>May break</th>
              <th>Run</th>
            </tr>
          </thead>
          <tbody>
            {snapshot.task_map.map((item) => (
              <tr key={`${item.kind}:${item.path}`} onClick={() => onSelect(`file:${item.path}`)}>
                <td><code>{item.path}</code></td>
                <td><span className={`badge ${riskTone(item.risk_level)}`}>{item.risk_level || "low"}</span></td>
                <td>{(item.why_selected || item.risk_reasons || []).slice(0, 2).join("; ")}</td>
                <td>{(item.tests_to_run || []).slice(0, 3).map((test) => <code key={test}>{test}</code>)}</td>
                <td>{(item.may_break || []).slice(0, 2).join("; ")}</td>
                <td>
                  {(item.tests_to_run || []).slice(0, 1).map((test) => (
                    <CommandAction key={test} label="Run" command={test.endsWith(".py") ? `pytest ${test}` : test} onRunCommand={onRunCommand} compact />
                  ))}
                </td>
              </tr>
            ))}
            {!snapshot.task_map.length ? (
              <tr><td colSpan={6}>No task map found.</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FilesView({
  snapshot,
  onSelect,
  onRunAction
}: {
  snapshot: DashboardSnapshot;
  onSelect: (id: string) => void;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
}) {
  const [target, setTarget] = useState(snapshot.selected_files[0]?.path || "");
  const artifacts = snapshot.artifacts || [];
  return (
    <div className="view-stack">
      <SectionTitle title="Files" subtitle="Selected files, omitted candidates, retrieve context, and AgentPack artifacts." />
      <div className="content-grid">
        <Panel title="Selected Files" icon={FileText}>
          <ItemList
            items={snapshot.selected_files.slice(0, 18).map((item) => ({
              id: `file:${item.path}`,
              title: item.path,
              detail: `${item.tokens || 0} tokens · ${(item.reasons || []).slice(0, 2).join("; ") || "selected"}`,
              tone: "good"
            }))}
            empty="No selected files found."
            onSelect={onSelect}
          />
        </Panel>
        <Panel title="Retrieve Context" icon={Search}>
          <div className="form-grid">
            <label className="field full">
              <span>Path or ref</span>
              <input value={target} onChange={(event) => setTarget(event.target.value)} placeholder="src/agentpack/dashboard/collectors.py" />
            </label>
            <div className="inline-actions full">
              <button type="button" className="primary-action" disabled={!target.trim()} onClick={() => onRunAction("retrieve", { target })}>
                <PlayCircle size={16} aria-hidden="true" />
                Retrieve
              </button>
            </div>
          </div>
        </Panel>
      </div>
      <Panel title="AgentPack Artifacts" icon={FolderKanban}>
        <div className="artifact-grid">
          {artifacts.map((item) => (
            <div key={item.path} className="artifact-card">
              <span className={`badge ${item.exists ? "good" : "warn"}`}>{item.exists ? "present" : "missing"}</span>
              <strong>{item.label}</strong>
              <code>{item.path}</code>
              <small>{item.kind || "artifact"} · {item.size ? `${formatNumber(item.size)} bytes` : "no file"}</small>
            </div>
          ))}
          {!artifacts.length ? <p className="empty">No artifacts reported.</p> : null}
        </div>
      </Panel>
    </div>
  );
}

function SettingsView({
  snapshot,
  onSaveConfig
}: {
  snapshot: DashboardSnapshot;
  onSaveConfig: (updates: Record<string, unknown>) => void;
}) {
  const config = snapshot.config;
  const initial = useMemo(() => editableConfigValues(config), [config]);
  const [values, setValues] = useState<Record<string, unknown>>(initial);
  const editableFields = useMemo(
    () => (config?.sections || []).flatMap((section) => section.fields.filter((field) => field.editable).map((field) => ({ ...field, field_id: `${field.section}.${field.key}` }))),
    [config]
  );
  const editableFieldIds = useMemo(() => new Set(editableFields.map((field) => field.field_id)), [editableFields]);
  const settingsPresets = useMemo(
    () => [
      {
        id: "codex-local",
        label: "Codex local",
        description: "Balanced context and blocking refresh behavior for Codex local work.",
        updates: {
          "context.default_mode": "balanced",
          "context.default_budget": 12000,
          "context.include_tests": true,
          "hooks.blocking_task_refresh": true
        }
      },
      {
        id: "claude-mcp",
        label: "Claude MCP",
        description: "Broader context, receipts, and task switch detection for Claude MCP sessions.",
        updates: {
          "context.default_mode": "deep",
          "context.default_budget": 18000,
          "context.include_receipts": true,
          "hooks.task_switch_detection": true
        }
      },
      {
        id: "review-mode",
        label: "Review heavy",
        description: "Broader context and receipts for review-heavy workflows.",
        updates: {
          "context.default_mode": "deep",
          "context.default_budget": 20000,
          "context.include_tests": true,
          "context.include_receipts": true
        }
      },
      {
        id: "fast-context",
        label: "Fast routing",
        description: "Smaller context for fast iteration and quick routing.",
        updates: {
          "context.default_mode": "lite",
          "context.default_budget": 8000,
          "context_lite.max_selected_files": 8
        }
      },
      {
        id: "benchmark-debug",
        label: "Benchmark/debug",
        description: "Repeatable context with compact lite bounds and tests enabled.",
        updates: {
          "context.default_mode": "balanced",
          "context.include_tests": true,
          "context_lite.max_selected_files": 12,
          "context_lite.max_omitted_files": 20
        }
      }
    ],
    []
  );
  const [selectedField, setSelectedField] = useState("");
  useEffect(() => setValues(initial), [initial]);
  useEffect(() => {
    if (!selectedField && editableFields[0]) {
      setSelectedField(editableFields[0].field_id);
    }
  }, [editableFields, selectedField]);
  const update = (field: string, value: unknown) => setValues((current) => ({ ...current, [field]: value }));
  const applyPreset = (updates: Record<string, unknown>) => {
    setValues((current) => {
      const next = { ...current };
      Object.entries(updates).forEach(([key, value]) => {
        if (editableFieldIds.has(key)) {
          next[key] = value;
        }
      });
      return next;
    });
  };
  const dirty = Object.keys(values).filter((key) => JSON.stringify(values[key]) !== JSON.stringify(initial[key]));
  const selectedDocs = editableFields.find((field) => field.field_id === selectedField);

  return (
    <div className="view-stack">
      <SectionTitle title="Settings" subtitle="Effective config with safe v1 fields editable and scoring read-only." />
      <div className="settings-docs">
        <label className="field">
          <span>Editable field docs</span>
          <select
            value={selectedField}
            onChange={(event) => {
              const value = event.target.value;
              setSelectedField(value);
              requestAnimationFrame(() => document.getElementById(configFieldDomId(value))?.scrollIntoView({ block: "center", behavior: "smooth" }));
            }}
          >
            {editableFields.map((field) => (
              <option key={field.field_id} value={field.field_id}>{field.field_id}</option>
            ))}
          </select>
        </label>
        <div className="settings-doc-card">
          <strong>{selectedDocs?.field_id || "No editable field selected"}</strong>
          <p>{selectedDocs?.description || "No docs metadata available for this field."}</p>
          {selectedDocs?.allowed_values?.length ? <small>Allowed: {selectedDocs.allowed_values.join(", ")}</small> : null}
          {selectedDocs?.doc_ref ? <code>{selectedDocs.doc_ref}</code> : null}
        </div>
      </div>
      <Panel title="Recommended Presets" icon={SlidersHorizontal}>
        <div className="preset-grid">
          {settingsPresets.map((preset) => {
            const available = Object.keys(preset.updates).some((key) => editableFieldIds.has(key));
            return (
              <button key={preset.id} type="button" className="preset-card" disabled={!available} onClick={() => applyPreset(preset.updates)}>
                <strong>{preset.label}</strong>
                <small>{preset.description}</small>
              </button>
            );
          })}
        </div>
      </Panel>
      <div className="inline-actions">
        <span className={`badge ${config?.valid === false ? "risk" : "good"}`}>{config?.valid === false ? "invalid config" : "config valid"}</span>
        {config?.path ? <code>{config.path}</code> : null}
        <button type="button" className="primary-action" disabled={!dirty.length} onClick={() => onSaveConfig(Object.fromEntries(dirty.map((key) => [key, values[key]])))}>
          Save changes
        </button>
      </div>
      {dirty.length ? (
        <Panel title="Pending Config Diff" icon={SlidersHorizontal}>
          <div className="config-diff-list">
            {dirty.map((key) => (
              <div key={key} className="list-row passive">
                <span>
                  <strong>{key}</strong>
                  <small>{stringifyValue(initial[key])} {"->"} {stringifyValue(values[key])}</small>
                </span>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}
      {config?.error ? <p className="empty">{config.error}</p> : null}
      <div className="settings-grid">
        {(config?.sections || []).map((section) => (
          <Panel key={section.name} title={section.name} icon={SlidersHorizontal}>
            <div className="config-fields">
              {section.fields.map((field) => {
                const fieldId = `${field.section}.${field.key}`;
                const current = values[fieldId] ?? field.value;
                return (
                  <label
                    key={fieldId}
                    id={configFieldDomId(fieldId)}
                    className={`${field.editable ? "config-field editable" : "config-field"} ${selectedField === fieldId ? "focused" : ""}`}
                  >
                    <span>
                      <strong>{field.key}</strong>
                      <small>{field.value_type || "value"} · {field.editable ? "editable" : "read-only"}</small>
                      {field.editable && field.description ? <small>{field.description}</small> : null}
                    </span>
                    {field.editable ? (
                      <ConfigInput field={field} value={current} onChange={(value) => update(fieldId, value)} />
                    ) : (
                      <code>{stringifyValue(field.value)}</code>
                    )}
                  </label>
                );
              })}
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}

function ConfigInput({
  field,
  value,
  onChange
}: {
  field: { value_type?: string; allowed_values?: string[] };
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (field.value_type === "boolean") {
    return <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />;
  }
  if (field.value_type === "integer" || field.value_type === "number") {
    return <input type="number" value={Number(value || 0)} onChange={(event) => onChange(Number(event.target.value))} />;
  }
  if (field.value_type === "list") {
    const text = Array.isArray(value) ? value.join("\n") : "";
    return <textarea rows={3} value={text} onChange={(event) => onChange(event.target.value.split("\n").map((item) => item.trim()).filter(Boolean))} />;
  }
  if (field.allowed_values?.length) {
    return (
      <select value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
        {field.allowed_values.map((item) => <option key={item} value={item}>{item || "(empty)"}</option>)}
      </select>
    );
  }
  return <input value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />;
}

function WorkflowView({
  snapshot,
  onRunCommand,
  onRunAction
}: {
  snapshot: DashboardSnapshot;
  onRunCommand: (command: string) => void;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
}) {
  const catalog = snapshot.command_catalog || [];
  const groups = unique(catalog.map((item) => item.group));
  const [task, setTask] = useState(snapshot.task.text || "");
  const [summary, setSummary] = useState("Completed from dashboard.");
  return (
    <div className="view-stack">
      <SectionTitle title="Workflow" subtitle="Typed AgentPack workflows first; advanced commands stay in the terminal drawer." />
      <div className="control-grid">
        <Panel title="Primary Workflow" icon={Workflow}>
          <div className="form-grid">
            <label className="field full">
              <span>Task</span>
              <input value={task} onChange={(event) => setTask(event.target.value)} placeholder="Describe the task" />
            </label>
            <label className="field full">
              <span>Finish summary</span>
              <input value={summary} onChange={(event) => setSummary(event.target.value)} />
            </label>
            <div className="inline-actions full">
              <button type="button" className="primary-action" onClick={() => onRunAction("work", { task })}>Work</button>
              <button type="button" className="secondary-action" onClick={() => onRunAction("finish", { summary })}>Finish</button>
              <button type="button" className="secondary-action" onClick={() => onRunAction("dev_check")}>Dev check</button>
              <button type="button" className="secondary-action" onClick={() => onRunAction("review")}>Review</button>
            </div>
          </div>
        </Panel>
        <Panel title="Loop State" icon={Activity}>
          <div className="stack-sm">
            <div className="list-row passive">
              <span>
                <strong>{snapshot.loop.status || "not running"}</strong>
                <small>{snapshot.loop.next_action || snapshot.loop.blocked_reason || "No loop action reported."}</small>
              </span>
              <span className={`badge ${riskTone(snapshot.loop.status)}`}>{snapshot.loop.runner || "runner"}</span>
            </div>
          </div>
        </Panel>
      </div>
      {groups.map((group) => (
        <Panel key={group} title={group} icon={TerminalSquare}>
          <div className="catalog-grid">
            {catalog.filter((item) => item.group === group).map((item) => (
              <CommandAction
                key={item.id}
                label={item.label}
                command={item.command}
                onRunCommand={onRunCommand}
                icon={item.confirm_required ? AlertTriangle : TerminalSquare}
              />
            ))}
          </div>
        </Panel>
      ))}
    </div>
  );
}

function IntegrationsView({
  snapshot,
  onRunCommand,
  onRunAction
}: {
  snapshot: DashboardSnapshot;
  onRunCommand: (command: string) => void;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
}) {
  const health = snapshot.mcp_health || {};
  const registrations = health.registrations || [];
  const tools = health.expected_tools || [];
  const remediation = health.remediation || [];
  const integrationFiles = snapshot.integrations || [];

  return (
    <div className="view-stack">
      <SectionTitle title="Integrations" subtitle="MCP runtime, host registration, live exposure boundary, and repair commands." />
      <div className="metric-grid">
        <Metric label="MCP" value={health.status || "unknown"} tone={riskTone(health.status)} />
        <Metric label="Runtime" value={health.runtime_status || "unknown"} tone={health.runtime_ok ? "good" : riskTone(health.runtime_status)} />
        <Metric label="Registered" value={health.registered ? "yes" : "no"} tone={health.registered ? "good" : "warn"} />
        <Metric label="Host Exposure" value={health.live_exposure || "unknown"} tone={health.live_exposure === "confirmed" ? "good" : "warn"} />
      </div>

      <div className="content-grid">
        <Panel title="Host Files" icon={FolderKanban}>
          <div className="stack-sm">
            {integrationFiles.map((item) => (
              <div key={`${item.label}:${item.path}`} className="list-row passive">
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.detail || item.agent}</small>
                  <code>{item.path}</code>
                </span>
                <button type="button" className="run-button" onClick={() => onRunAction("repair_integration", { agent: item.agent === "mcp" ? "all" : item.agent })}>
                  Repair
                </button>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="MCP Runtime" icon={TerminalSquare}>
          <div className="stack-sm">
            <div className="list-row passive">
              <span>
                <strong>{health.runtime_status || "unknown"}</strong>
                <small>{health.runtime_detail || "No runtime detail captured."}</small>
              </span>
              <span className={`badge ${health.runtime_ok ? "good" : riskTone(health.runtime_status)}`}>
                {health.runtime_ok ? "ok" : "check"}
              </span>
            </div>
            <p className="muted">
              Dashboard probes whether `agentpack mcp` can start locally. Only a tool call from the agent host proves live exposure.
            </p>
          </div>
        </Panel>

        <Panel title="Registrations" icon={Network}>
          <div className="stack-sm">
            {registrations.map((item) => (
              <div key={`${item.scope}:${item.path}`} className="list-row passive">
                <span>
                  <strong>{item.scope}</strong>
                  <small>{item.detail || item.path}</small>
                  <code>{item.path}</code>
                </span>
                <span className={`badge ${riskTone(item.status)}`}>{item.status || "unknown"}</span>
              </div>
            ))}
            {!registrations.length ? <p className="empty">No MCP registration checks found.</p> : null}
          </div>
        </Panel>

        <Panel title="Expected Tools" icon={ClipboardList}>
          <div className="tool-list">
            {tools.slice(0, 24).map((tool) => (
              <span key={tool} className="badge neutral">{tool}</span>
            ))}
            {!tools.length ? <p className="empty">No expected MCP tools reported.</p> : null}
          </div>
        </Panel>

        <Panel title="Repair Path" icon={CheckCircle2}>
          <div className="stack-sm">
            {remediation.map((entry) => (
              isRunnableAgentPackCommand(entry) ? (
                <CommandAction key={entry} label="Repair" command={entry} onRunCommand={onRunCommand} />
              ) : (
                <div key={entry} className="list-row passive">
                  <span>
                    <strong>Manual step</strong>
                    <small>{entry}</small>
                  </span>
                </div>
              )
            ))}
            {!remediation.length ? <p className="empty">No repair action needed.</p> : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ReplayView({ snapshot, graph }: { snapshot: DashboardSnapshot; graph: DashboardGraph }) {
  const steps = [
    { label: "Task loaded", detail: snapshot.task.text || "No task text", tone: "neutral" },
    { label: "Context checked", detail: `${snapshot.context.status} ${snapshot.context.stale_reason || ""}`.trim(), tone: snapshot.context.status },
    { label: "Files selected", detail: `${graph.summary.selected_files} selected, ${graph.summary.omitted_files} omitted`, tone: "good" },
    { label: "Memory evaluated", detail: `${graph.summary.memory_nodes} memory nodes`, tone: "memory" },
    { label: "Risk mapped", detail: `${graph.summary.high_risk_files} high-risk files`, tone: graph.summary.high_risk_files ? "risk" : "good" }
  ];

  return (
    <div className="view-stack">
      <SectionTitle title="Replay" subtitle="Compact timeline of the dashboard decision state." />
      <ol className="timeline">
        {steps.map((step) => (
          <li key={step.label}>
            <span className={`timeline-dot ${step.tone}`} />
            <div>
              <strong>{step.label}</strong>
              <p>{step.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function RawDataView({ payload }: { payload: DashboardPayload }) {
  return (
    <div className="raw-grid">
      <Panel title="Snapshot JSON" icon={Code2}>
        <TechnicalDetail summary="Show workspace contract"><pre>{JSON.stringify(payload.snapshot, null, 2)}</pre></TechnicalDetail>
      </Panel>
      <Panel title="Map JSON" icon={MapIcon}>
        <TechnicalDetail summary="Show map contract"><pre>{JSON.stringify(payload.map, null, 2)}</pre></TechnicalDetail>
      </Panel>
      <Panel title="Graph JSON" icon={Network}>
        <TechnicalDetail summary="Show graph contract"><pre>{JSON.stringify(payload.graph, null, 2)}</pre></TechnicalDetail>
      </Panel>
      <Panel title="Action History JSON" icon={Activity}>
        <TechnicalDetail summary="Show action history"><pre>{JSON.stringify(payload.action_history, null, 2)}</pre></TechnicalDetail>
      </Panel>
    </div>
  );
}

function Inspector({
  selected,
  onRunCommand
}: {
  selected: DashboardNode | DashboardEdge | null;
  onRunCommand: (command: string) => void;
}) {
  return (
    <aside className="inspector" aria-label="Selection inspector">
      <div className="inspector-header">
        <span className="eyebrow">Inspector</span>
        <h2>{selected?.label || selected?.id || "Nothing selected"}</h2>
      </div>
      {!selected ? (
        <p className="empty">Select a node or edge to inspect evidence and actions.</p>
      ) : (
        <div className="stack">
          {"type" in selected ? <span className={`badge ${selected.type}`}>{selected.type}</span> : null}
          {"risk" in selected && selected.risk ? <span className={`badge ${riskTone(selected.risk)}`}>{selected.risk}</span> : null}
          {"summary" in selected && selected.summary ? <p>{selected.summary}</p> : null}
          {"reason" in selected && selected.reason ? <p>{selected.reason}</p> : null}
          {"path" in selected && selected.path ? <code>{selected.path}</code> : null}
          <InspectorList title="Evidence" items={selected.evidence || []} />
          <ActionList actions={selected.actions || []} onRunCommand={onRunCommand} />
        </div>
      )}
    </aside>
  );
}

function InspectorList({ title, items }: { title: string; items: Array<{ kind?: string; ref?: string; summary?: string; path?: string }> }) {
  return (
    <section className="inspector-section">
      <h3>{title}</h3>
      {items.length ? (
        <ul>
          {items.slice(0, 8).map((item, index) => (
            <li key={`${item.kind}:${item.ref}:${index}`}>
              <strong>{item.kind || "evidence"}</strong>
              <span>{item.summary || item.ref || item.path}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty">No evidence attached.</p>
      )}
    </section>
  );
}

function ActionList({
  actions,
  onRunCommand
}: {
  actions: Array<{ label: string; command?: string; kind?: string }>;
  onRunCommand: (command: string) => void;
}) {
  return (
    <section className="inspector-section">
      <h3>Actions</h3>
      {actions.length ? (
        <div className="stack-sm">
          {actions.slice(0, 8).map((action) => (
            <CommandAction
              key={`${action.label}:${action.command}`}
              label={action.label}
              command={action.command || ""}
              kind={action.kind}
              onRunCommand={onRunCommand}
            />
          ))}
        </div>
      ) : (
        <p className="empty">No direct action attached.</p>
      )}
    </section>
  );
}

function CommandAction({
  label,
  command,
  kind,
  icon: Icon = TerminalSquare,
  compact = false,
  onRunCommand
}: {
  label: string;
  command: string;
  kind?: string;
  icon?: typeof Activity;
  compact?: boolean;
  onRunCommand: (command: string) => void;
}) {
  const runnable = Boolean(command) && kind !== "path";
  return (
    <div className={compact ? "command-action compact" : "command-action"}>
      {!compact ? <Icon size={16} aria-hidden="true" /> : null}
      <span>
        {!compact ? <strong>{label}</strong> : null}
        {command ? <code>{command}</code> : <small>No command</small>}
      </span>
      {runnable ? (
        <button
          type="button"
          className="run-button"
          onClick={(event) => {
            event.stopPropagation();
            onRunCommand(command);
          }}
        >
          <PlayCircle size={14} aria-hidden="true" />
          <span>Run</span>
        </button>
      ) : null}
    </div>
  );
}

function TerminalPanel({
  open,
  sessions,
  activeSessionId,
  onSelect,
  onClose,
  onInput,
  onRunCommand,
  onKill
}: {
  open: boolean;
  sessions: TerminalSessionState[];
  activeSessionId: string;
  onSelect: (id: string) => void;
  onClose: () => void;
  onInput: (sessionId: string, data: string) => void;
  onRunCommand: (command: string) => void;
  onKill: (sessionId: string) => void;
}) {
  const [input, setInput] = useState("");
  const active = sessions.find((session) => session.id === activeSessionId) || sessions[sessions.length - 1];
  if (!open) return null;
  return (
    <section className="terminal-panel" aria-label="Command terminal">
      <div className="terminal-titlebar">
        <div className="terminal-window-controls" aria-hidden="true">
          <span className="close" />
          <span className="minimize" />
          <span className="zoom" />
        </div>
        <div className="terminal-title">
          <strong>bash</strong>
          <small>{active?.cwd || "No active session"}</small>
        </div>
        <button type="button" className="terminal-close" onClick={onClose} aria-label="Close terminal">
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <div className="terminal-tabs" role="tablist">
        {sessions.map((session) => (
          <button
            key={session.id}
            type="button"
            className={session.id === active?.id ? "terminal-tab active" : "terminal-tab"}
            onClick={() => onSelect(session.id)}
          >
            <span>{session.command}</span>
            <small>{session.status}</small>
          </button>
        ))}
        {!sessions.length ? <span className="terminal-empty">Run an AgentPack command to start a session.</span> : null}
      </div>
      <div className="terminal-screen">
        {active ? (
          <div className="terminal-command-line">
            <span className="terminal-prompt">$</span>
            <span>{active.command}</span>
            <span className={`terminal-status ${riskTone(active.status)}`}>{active.status}</span>
          </div>
        ) : null}
        <pre className="terminal-output">{active?.output || ""}</pre>
      </div>
      <form
        className="terminal-input"
        onSubmit={(event) => {
          event.preventDefault();
          const value = input.trim();
          if (!value) return;
          if (active && (active.status === "starting" || active.status === "running")) {
            onInput(active.id, `${input}\n`);
          } else {
            onRunCommand(value);
          }
          setInput("");
        }}
      >
        <span className="terminal-prompt">$</span>
        <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="agentpack doctor --agent codex" />
        <button type="submit" className="terminal-send" aria-label="Send input" disabled={!input.trim()}>
          <Send size={16} aria-hidden="true" />
        </button>
        <button type="button" className="kill-button" disabled={!active} onClick={() => active && onKill(active.id)}>
          Kill
        </button>
      </form>
    </section>
  );
}

function SectionTitle({ title, subtitle }: { title: string; subtitle: string }) {
  const { state } = useDashboardState();
  return (
    <div className="section-title">
      <div className="section-title-heading"><h1>{title}</h1><span className="mode-context">{state.presentationMode === "explain" ? "Guided" : "Technical"}</span></div>
      <p>{subtitle}</p>
      <small className="mode-guidance">{state.presentationMode === "explain" ? "Outcomes and recommended next steps are prioritized. Expand technical details when needed." : "Paths, commands, evidence, identifiers, and diagnostics are shown inline."}</small>
    </div>
  );
}

function ItemList({
  items,
  empty,
  onSelect
}: {
  items: Array<{ id: string; title: string; detail: string; tone?: string }>;
  empty: string;
  onSelect: (id: string) => void;
}) {
  if (!items.length) {
    return <p className="empty">{empty}</p>;
  }
  return (
    <div className="stack-sm">
      {items.map((item) => (
        <button key={item.id} type="button" className="list-row" onClick={() => onSelect(item.id)}>
          <span>
            <strong>{item.title}</strong>
            <small>{item.detail}</small>
          </span>
          <span className={`badge ${riskTone(item.tone)}`}>{item.tone || "view"}</span>
        </button>
      ))}
    </div>
  );
}

function toFlowGraph(
  graph: DashboardGraph,
  query: string,
  selectedId: string,
  nodePositions: Record<string, { x: number; y: number }>
): { nodes: Node[]; edges: Edge[] } {
  const lower = query.trim().toLowerCase();
  const visible = new Set(
    graph.nodes
      .filter((node) => {
        if (!lower) return true;
        return [node.label, node.path, node.summary, node.type].some((value) => String(value || "").toLowerCase().includes(lower));
      })
      .map((node) => node.id)
  );

  const visibleNodes = graph.nodes.filter((node) => visible.has(node.id));
  const layout = layoutGraphNodes(visibleNodes);
  const nodes = visibleNodes
    .map((node) => ({
      id: node.id,
      position: nodePositions[node.id] || layout[node.id],
      data: { label: nodeLabel(node) },
      className: `flow-node ${node.type} ${node.selected ? "selected" : ""} ${node.stale ? "stale" : ""} ${node.id === selectedId ? "active" : ""}`,
      dragHandle: ".node-drag-handle",
      type: "default"
    }));
  const edges = graph.edges
    .filter((edge) => visible.has(edge.source) && visible.has(edge.target))
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.label || edge.type,
      className: `flow-edge ${edge.type}`,
      animated: edge.type === "memory_influenced"
    }));
  return { nodes, edges };
}

function layoutGraphNodes(nodes: DashboardNode[]): Record<string, { x: number; y: number }> {
  const layout: Record<string, { x: number; y: number }> = {};
  const groups = {
    task: nodes.filter((node) => node.type === "task"),
    memory: nodes.filter((node) => node.type === "episode" || node.type === "procedure"),
    selectedFiles: nodes.filter((node) => node.type === "file" && node.selected),
    omittedFiles: nodes.filter((node) => node.type === "file" && !node.selected),
    tests: nodes.filter((node) => node.type === "test"),
    actions: nodes.filter((node) => node.type === "action"),
    other: nodes.filter((node) => !["task", "episode", "procedure", "file", "test", "action"].includes(node.type))
  };

  placeStack(groups.task, layout, { x: 80, y: 230, rowGap: 118 });
  placeStack(groups.memory, layout, { x: 80, y: 390, rowGap: 116 });
  placeGrid(groups.selectedFiles, layout, { x: 390, y: 80, columns: 3, columnGap: 286, rowGap: 118 });

  const selectedRows = Math.max(1, Math.ceil(groups.selectedFiles.length / 3));
  placeGrid(groups.omittedFiles, layout, {
    x: 390,
    y: 80 + selectedRows * 118 + 78,
    columns: 3,
    columnGap: 286,
    rowGap: 118
  });

  placeGrid([...groups.tests, ...groups.actions, ...groups.other], layout, {
    x: 1260,
    y: 100,
    columns: 2,
    columnGap: 286,
    rowGap: 118
  });

  return layout;
}

function placeStack(
  nodes: DashboardNode[],
  layout: Record<string, { x: number; y: number }>,
  options: { x: number; y: number; rowGap: number }
) {
  nodes.forEach((node, index) => {
    layout[node.id] = { x: options.x, y: options.y + index * options.rowGap };
  });
}

function placeGrid(
  nodes: DashboardNode[],
  layout: Record<string, { x: number; y: number }>,
  options: { x: number; y: number; columns: number; columnGap: number; rowGap: number }
) {
  const columns = Math.max(1, options.columns);
  nodes.forEach((node, index) => {
    layout[node.id] = {
      x: options.x + (index % columns) * options.columnGap,
      y: options.y + Math.floor(index / columns) * options.rowGap
    };
  });
}

function nodeLabel(node: DashboardNode) {
  const detail = node.path || node.summary || node.type;
  return (
    <div className="node-label">
      <div className="node-drag-handle" title="Drag to arrange node">
        <span>{node.type}</span>
        {node.risk ? <span className={`node-risk ${riskTone(node.risk)}`}>{node.risk}</span> : null}
      </div>
      <div className="node-body nopan nodrag">
        <strong>{node.label}</strong>
        {detail ? <small>{detail}</small> : null}
      </div>
    </div>
  );
}

function findSelected(graph: DashboardGraph, selectedId: string): DashboardNode | DashboardEdge | null {
  return graph.nodes.find((node) => node.id === selectedId) || graph.edges.find((edge) => edge.id === selectedId) || null;
}

function riskTone(value?: string) {
  if (value === "high" || value === "risk" || value === "missing" || value === "command_missing" || value === "missing_extra" || value === "server_error") return "risk";
  if (value === "medium" || value === "stale" || value === "warn" || value === "warning" || value === "unknown" || value === "invalid") return "warn";
  if (value === "low" || value === "fresh" || value === "good" || value === "healthy" || value === "present" || value === "ready" || value === "stdio_waiting") return "good";
  if (value === "memory") return "memory";
  return "neutral";
}

function unique(values: string[]) {
  return Array.from(new Set(values.filter(Boolean)));
}

function editableConfigValues(config: DashboardSnapshot["config"]) {
  const values: Record<string, unknown> = {};
  for (const section of config?.sections || []) {
    for (const field of section.fields) {
      if (field.editable) {
        values[`${field.section}.${field.key}`] = field.value;
      }
    }
  }
  return values;
}

function stringifyValue(value: unknown) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

function configFieldDomId(fieldId: string) {
  return `config-field-${fieldId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function formatTimestamp(value?: string) {
  if (!value) return "no timestamp";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatAge(value: string) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "unknown age";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function formatDuration(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

let cachedWebGLSupport: boolean | null = null;

function hasWebGLSupport() {
  if (cachedWebGLSupport !== null) return cachedWebGLSupport;
  if (typeof document === "undefined") {
    cachedWebGLSupport = false;
    return cachedWebGLSupport;
  }
  try {
    const canvas = document.createElement("canvas");
    cachedWebGLSupport = Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl") || canvas.getContext("experimental-webgl"));
  } catch {
    cachedWebGLSupport = false;
  }
  return cachedWebGLSupport;
}

function isRunnableAgentPackCommand(value: string) {
  const command = value.trim();
  return (
    command.startsWith("agentpack ") ||
    command.startsWith("python -m agentpack ") ||
    command.startsWith("python -m agentpack.cli ") ||
    command.startsWith("npx @vishal2612200/agentpack ")
  );
}

function formatNumber(value: number) {
  return new Intl.NumberFormat().format(value);
}
