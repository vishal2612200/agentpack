import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Brain,
  Building2,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  ClipboardList,
  Code2,
  Database,
  FileText,
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
import { apiUrl, authHeaders, dashboardToken, loadDashboardPayload, type DashboardPayload } from "./data/loadDashboard";
import type { ActionHistoryRow, DashboardAnalytics, DashboardEdge, DashboardGraph, DashboardMap, DashboardNode, DashboardSnapshot, DashboardTaskDetail, DashboardTaskRecord, DashboardTimelineEvent, MapBuilding, MapRoad, SemanticGraphSummary } from "./data/schema";
import { buildingHoverInfo, labelize, roadHoverInfo, type MapHoverInfo } from "./mapInfo";

const ContextCityMap = lazy(() => import("./MapCity").then((module) => ({ default: module.ContextCityMap })));

type View = "home" | "analytics" | "cockpit" | "tasks" | "threads" | "context" | "graph" | "files" | "settings" | "integrations" | "workflow" | "learning" | "raw";

interface CommandInspection {
  command: string;
  cwd: string;
  allowed: boolean;
  reason: string;
  risky: boolean;
  risk_reasons: string[];
  confirm_required: boolean;
}

interface TerminalSessionState {
  id: string;
  command: string;
  cwd: string;
  status: string;
  returncode?: number | null;
  output: string;
}

interface PendingCommand {
  command: string;
  inspection: CommandInspection;
}

const primaryViews: Array<{ id: View; label: string; icon: typeof Activity }> = [
  { id: "home", label: "Project home", icon: Building2 },
  { id: "tasks", label: "Tasks", icon: ClipboardList },
  { id: "analytics", label: "How AgentPack helped", icon: BarChart3 }
];

const advancedViews: Array<{ id: View; label: string; icon: typeof Activity }> = [
  { id: "graph", label: "Impact map", icon: MapIcon },
  { id: "context", label: "AI context", icon: Database },
  { id: "files", label: "Files", icon: FileText },
  { id: "workflow", label: "Run checks", icon: Workflow },
  { id: "threads", label: "Work sessions", icon: GitBranch },
  { id: "learning", label: "Learning", icon: Brain },
  { id: "settings", label: "Settings", icon: Settings },
  { id: "integrations", label: "Agent connection", icon: TerminalSquare },
  { id: "raw", label: "Diagnostics", icon: Code2 },
  { id: "cockpit", label: "Decision details", icon: Activity }
];

export function App() {
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [payloadDetail, setPayloadDetail] = useState<"home" | "full">("home");
  const [error, setError] = useState<string>("");
  const [view, setView] = useState<View>("home");
  const [selectedId, setSelectedId] = useState<string>("task:active");
  const [query, setQuery] = useState("");
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [sessions, setSessions] = useState<TerminalSessionState[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>("");
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(null);
  const streams = useRef<Map<string, EventSource>>(new Map());

  const refreshDashboard = async (detail: "home" | "full" = payloadDetail) => {
    const loaded = await loadDashboardPayload(detail);
    setPayloadDetail(detail);
    setPayload(loaded);
    setSelectedId((current) => current || loaded.graph.root_id || "task:active");
    return loaded;
  };

  const loadFullDashboard = () => {
    refreshDashboard("full").catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load dashboard details"));
  };

  useEffect(() => {
    refreshDashboard("home")
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load dashboard data"));
  }, []);

  useEffect(() => {
    return () => {
      streams.current.forEach((stream) => stream.close());
      streams.current.clear();
    };
  }, []);

  if (error) {
    return (
      <ErrorState
        message={error}
        onRetry={() => {
          setError("");
          refreshDashboard("home").catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load dashboard data"));
        }}
      />
    );
  }
  if (!payload) {
    return <LoadingState />;
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
          if (item.id !== "home" && item.id !== "analytics") loadFullDashboard();
        }}
        aria-label={item.label}
        title={item.label}
      >
        <Icon size={17} aria-hidden="true" />
        <span>{item.label}</span>
      </button>
    );
  };

  const handleRunCommand = async (command: string) => {
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
    const response = await fetch(apiUrl("/api/action/run"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ action, ...body })
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
    attachEventStream(session.id);
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
    setSelectedId((result as DashboardPayload).graph.root_id || "task:active");
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
    attachEventStream(session.id);
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

  const attachEventStream = (sessionId: string) => {
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
      }
    };
    stream.onerror = () => {
      stream.close();
      streams.current.delete(sessionId);
    };
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
    <div className="app-shell">
      <aside className="sidebar" aria-label="Dashboard navigation">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true">
            <AgentPackLogo />
          </div>
          <div className="brand-copy">
            <strong>AgentPack</strong>
            <span>Context cockpit</span>
          </div>
        </div>
        <nav className="nav-list">
          <span className="nav-group-label">Workspace</span>
          {primaryViews.map(renderNavItem)}
          <details className="advanced-nav" open={view !== "home" && view !== "tasks" && view !== "analytics"}>
            <summary>Advanced</summary>
            <div className="nav-list nav-list-nested">{advancedViews.map(renderNavItem)}</div>
          </details>
        </nav>
      </aside>

      <main className="workspace">
        <TopBar snapshot={payload.snapshot} onSwitchProject={handleSwitchProject} />
        <section className="main-panel" aria-label={`${view} view`}>
          {view === "home" && (
            <ProjectHomeView snapshot={payload.snapshot} onRunAction={handleRunAction} onRefresh={refreshDashboard} onOpenGraph={() => { setView("graph"); loadFullDashboard(); }} />
          )}
          {view === "analytics" && <AnalyticsView snapshot={payload.snapshot} />}
          {view === "cockpit" && (
            <CockpitView payload={payload} onSelect={setSelectedId} onOpenGraph={() => setView("graph")} onRunCommand={handleRunCommand} onRunAction={handleRunAction} />
          )}
          {view === "tasks" && <TasksView snapshot={payload.snapshot} onRunAction={handleRunAction} onRefresh={refreshDashboard} />}
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
            />
          )}
          {view === "files" && <FilesView snapshot={payload.snapshot} onSelect={setSelectedId} onRunAction={handleRunAction} />}
          {view === "settings" && <SettingsView snapshot={payload.snapshot} onSaveConfig={handleSaveConfig} />}
          {view === "integrations" && <IntegrationsView snapshot={payload.snapshot} onRunCommand={handleRunCommand} onRunAction={handleRunAction} />}
          {view === "workflow" && <WorkflowView snapshot={payload.snapshot} onRunCommand={handleRunCommand} onRunAction={handleRunAction} />}
          {view === "learning" && <MemoryView snapshot={payload.snapshot} graph={payload.graph} onSelect={setSelectedId} />}
          {view === "raw" && <RawDataView payload={payload} />}
        </section>
      </main>

      <Inspector selected={selected} onRunCommand={handleRunCommand} />
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
  onSwitchProject
}: {
  snapshot: DashboardSnapshot;
  onSwitchProject: (path: string) => void;
}) {
  return (
    <header className="topbar">
      <ProjectDropdown snapshot={snapshot} onSwitchProject={onSwitchProject} />
      <div className="topbar-status" aria-label="Dashboard health">
        <StatusPill label="Context" status={snapshot.context.status} />
        <StatusPill label="MCP" status={snapshot.mcp_health?.status || "unknown"} />
      </div>
    </header>
  );
}

function ProjectDropdown({
  snapshot,
  onSwitchProject
}: {
  snapshot: DashboardSnapshot;
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
      <button type="button" className="project-trigger" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span>
          <strong>{current.name}</strong>
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

function ProjectHomeView({
  snapshot,
  onRunAction,
  onRefresh,
  onOpenGraph
}: {
  snapshot: DashboardSnapshot;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onRefresh: () => Promise<unknown>;
  onOpenGraph: () => void;
}) {
  const tasks = snapshot.project_tasks || [];
  const active = snapshot.active_task || tasks.find((item) => item.active) || tasks[0] || null;
  const [selectedTaskId, setSelectedTaskId] = useState(active?.task_id || "");
  const [newTask, setNewTask] = useState("");
  const [taskError, setTaskError] = useState("");
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [taskDetail, setTaskDetail] = useState<DashboardTaskDetail | null>(null);
  const [taskDetailState, setTaskDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const selected = tasks.find((item) => item.task_id === selectedTaskId) || active;
  const analytics = snapshot.analytics;

  useEffect(() => {
    setSelectedTaskId(active?.task_id || "");
  }, [active?.task_id]);

  useEffect(() => {
    const taskId = selected?.task_id;
    if (!taskId) {
      setTaskDetail(null);
      setTaskDetailState("idle");
      return;
    }
    let cancelled = false;
    setTaskDetail(null);
    setTaskDetailState("loading");
    fetch(apiUrl(`/api/project/tasks/${encodeURIComponent(taskId)}`), { headers: authHeaders() })
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load task details.");
        return response.json() as Promise<DashboardTaskDetail>;
      })
      .then((detail) => {
        if (cancelled) return;
        setTaskDetail(detail);
        setTaskDetailState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setTaskDetail(null);
        setTaskDetailState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.task_id]);

  useEffect(() => {
    setFeedbackSent(false);
  }, [selected?.task_id]);

  const createTask = async (event: FormEvent) => {
    event.preventDefault();
    const title = newTask.trim();
    if (!title) return;
    setTaskError("");
    const response = await fetch(apiUrl("/api/project/tasks"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ title })
    });
    if (!response.ok) {
      setTaskError("Could not start this task.");
      return;
    }
    setNewTask("");
    await onRefresh();
  };

  const updateStatus = async (status: string) => {
    if (!selected) return;
    await fetch(apiUrl("/api/project/tasks/update"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ task_id: selected.task_id, status })
    });
    await onRefresh();
  };

  const submitFeedback = async (value: "helped" | "partly_helped" | "missed_context" | "not_sure") => {
    if (!selected) return;
    const response = await fetch(apiUrl("/api/project/feedback"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ task_id: selected.task_id, run_id: taskDetail && taskDetail.runs.length ? taskDetail.runs[taskDetail.runs.length - 1].run_id : selected.last_run_id || "", value })
    });
    if (response.ok) setFeedbackSent(true);
  };

  const selectedDetail = taskDetail?.task.task_id === selected?.task_id ? taskDetail : null;
  const latestRun = selectedDetail && selectedDetail.runs.length ? selectedDetail.runs[selectedDetail.runs.length - 1] : undefined;
  const selectedFiles = latestRun?.selected_files || [];
  const omittedFiles = latestRun?.omitted_files || [];

  return (
    <div className="view-stack project-home" data-testid="project-home">
      <section className="project-home-hero">
        <div>
          <p className="eyebrow">{snapshot.project.name} workspace</p>
          <h1>What are you working on?</h1>
          <p className="muted">Keep your AI work focused on one project and one task at a time.</p>
          <div className="workspace-context">
            <span>{snapshot.workspace?.branch || snapshot.project.branch || "local workspace"}</span>
            <code>{snapshot.workspace?.path || snapshot.project.path}</code>
          </div>
        </div>
        <div className="hero-actions">
          <button className="secondary-action" type="button" onClick={onOpenGraph}>
            Impact map <ChevronRight size={16} aria-hidden="true" />
          </button>
        </div>
      </section>

      <div className="metric-grid home-metrics">
        <Metric label="Open tasks" value={tasks.filter((item) => item.status !== "done").length} tone="neutral" />
        <Metric label="Completed" value={analytics?.tasks_completed || 0} tone="good" />
        <Metric label="AI context prepared" value={analytics?.context_packs || 0} tone="memory" />
        <Metric label="Context reduced" value={`${analytics?.average_saving_pct || 0}%`} tone="good" />
      </div>

      <div className="home-grid">
        <Panel title="Current task" icon={ClipboardList}>
          {active ? (
            <div className="task-focus-card">
              <div className="task-card-heading">
                <span className={`badge ${riskTone(active.status)}`}>{taskStatusLabel(active.status)}</span>
                {active.imported ? <span className="badge neutral">from task files</span> : null}
              </div>
              <h2>{active.title}</h2>
              <p className="muted">{snapshot.suggested_actions[0]?.label || "AgentPack is ready to prepare focused context for this task."}</p>
              <div className="inline-actions">
                <button type="button" className="primary-action" onClick={() => onRunAction("next")}>
                  <PlayCircle size={16} aria-hidden="true" />
                  Prepare next step
                </button>
                <button type="button" className="secondary-action" onClick={() => updateStatus(active.status === "done" ? "in_progress" : "done")}>
                  {active.status === "done" ? "Reopen task" : "Mark done"}
                </button>
              </div>
            </div>
          ) : (
            <div className="empty-state-block">
              <strong>No task is active yet.</strong>
              <p>Start with a plain-language goal and AgentPack will organize the context.</p>
            </div>
          )}
        </Panel>

        <Panel title="Tasks in this workspace" icon={FolderKanban}>
          <div className="stack-sm">
            {tasks.slice(0, 20).map((task) => (
              <button key={task.task_id} type="button" className={task.task_id === selected?.task_id ? "task-list-row active" : "task-list-row"} onClick={() => setSelectedTaskId(task.task_id)}>
                <span>
                  <strong>{task.title}</strong>
                  <small>{task.updated_at ? formatDashboardDate(task.updated_at) : "No activity yet"}</small>
                </span>
                <span className={`badge ${riskTone(task.status)}`}>{taskStatusLabel(task.status)}</span>
              </button>
            ))}
            {!tasks.length ? <p className="empty">No tasks have been recorded for this workspace.</p> : null}
          </div>
          <form className="start-task-form" onSubmit={createTask}>
            <input aria-label="New task" value={newTask} onChange={(event) => setNewTask(event.target.value)} placeholder="What do you want to build or fix?" />
            <button type="submit" className="primary-action" disabled={!newTask.trim()}><PlayCircle size={16} aria-hidden="true" /> Start task</button>
          </form>
          {taskError ? <p className="error-text">{taskError}</p> : null}
        </Panel>
      </div>

      {selected ? (
        <Panel title="Task details" icon={FileText}>
          <div className="task-detail-grid">
            <div>
              <p className="eyebrow">Selected task</p>
              <h2>{selected.title}</h2>
              <p className="muted">This task belongs to <strong>{snapshot.project.name}</strong> on <strong>{snapshot.workspace?.branch || "the current workspace"}</strong>.</p>
            </div>
            <div className="stack-sm">
              <label className="field"><span>Status</span><select value={selected.status} onChange={(event) => updateStatus(event.target.value)}><option value="todo">To do</option><option value="in_progress">In progress</option><option value="needs_attention">Needs attention</option><option value="done">Done</option></select></label>
              <div className="detail-stat"><span>Files prepared</span><strong>{selectedDetail ? selectedFiles.length : taskDetailState === "loading" ? "Loading" : "Not run"}</strong></div>
              <div className="detail-stat"><span>Evidence</span><strong>{selectedDetail ? selectedDetail.impact?.length || 0 : taskDetailState === "loading" ? "Loading" : "Not run"}</strong></div>
            </div>
          </div>
          {taskDetailState === "loading" ? <p className="muted" data-testid="task-detail-loading">Loading this task&apos;s evidence...</p> : null}
          {taskDetailState === "error" ? <p className="error-text" data-testid="task-detail-error">Task details could not be loaded.</p> : null}
          {selectedDetail ? <div className="task-evidence-grid" data-testid="task-detail-evidence">
            <div>
              <p className="eyebrow">Files prepared</p>
              {selectedFiles.length ? <ul className="compact-list">{selectedFiles.slice(0, 12).map((path) => <li key={`selected-${path}`}><code>{path}</code></li>)}</ul> : <p className="muted">No files were selected yet.</p>}
            </div>
            <div>
              <p className="eyebrow">Files left out</p>
              {omittedFiles.length ? <ul className="compact-list">{omittedFiles.slice(0, 12).map((path) => <li key={`omitted-${path}`}><code>{path}</code></li>)}</ul> : <p className="muted">No omitted-file evidence yet.</p>}
            </div>
            <div>
              <p className="eyebrow">Checks</p>
              {latestRun?.checks?.length ? <ul className="compact-list">{latestRun.checks.slice(0, 12).map((check) => <li key={check}>{check}</li>)}</ul> : <p className="muted">No checks recorded yet.</p>}
            </div>
            <div>
              <p className="eyebrow">Impact evidence</p>
              <p className="muted">{selectedDetail.impact_reason || "No impact evidence recorded yet."}</p>
              {selectedDetail.github_references?.length ? <p className="muted">GitHub: {selectedDetail.github_references.join(", ")}</p> : null}
            </div>
          </div> : null}
          <div className="feedback-box" data-testid="task-feedback">
            <div><strong>Was this useful?</strong><small>Your answer improves future context selection.</small></div>
            <div className="inline-actions">
              {feedbackSent ? <span className="badge good">Thanks for the feedback</span> : <>
                <button type="button" className="secondary-action" onClick={() => submitFeedback("helped")}>Yes</button>
                <button type="button" className="secondary-action" onClick={() => submitFeedback("partly_helped")}>Partly</button>
                <button type="button" className="secondary-action" onClick={() => submitFeedback("missed_context")}>Missed context</button>
              </>}
            </div>
          </div>
        </Panel>
      ) : null}
      {selected ? <Panel title="Work history" icon={Activity}>
        {taskDetailState === "loading" && !selectedDetail ? <p className="muted">Loading work history...</p> : null}
        {taskDetailState === "error" ? <p className="error-text">Work history could not be loaded.</p> : null}
        {taskDetailState === "ready" && selectedDetail ? <TaskTimeline events={selectedDetail.timeline || []} /> : null}
      </Panel> : null}
    </div>
  );
}

function TaskTimeline({ events }: { events: DashboardTimelineEvent[] }) {
  if (!events.length) {
    return <div className="empty-state-block" data-testid="task-timeline-empty"><strong>No work history yet.</strong><p>Start a task or prepare AI context to create the first evidence-backed update.</p></div>;
  }
  return <ol className="timeline" data-testid="task-timeline">
    {events.slice().reverse().map((event) => (
      <li key={event.event_id}>
        <span className="timeline-dot good" />
        <div>
          <strong>{event.label || event.event_type || "Work update"}</strong>
          <small>{event.occurred_at ? formatDashboardDate(event.occurred_at) : "Recorded locally"}{event.agent ? ` · ${event.agent}` : ""}</small>
          {event.summary ? <p className="muted">{event.summary}</p> : null}
          {event.context_path ? <code>{event.context_path}</code> : null}
          {event.issue_references?.length ? <small>Linked: {event.issue_references.join(", ")}</small> : null}
        </div>
      </li>
    ))}
  </ol>;
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

function taskStatusLabel(status: DashboardTaskRecord["status"]): string {
  return { todo: "To do", in_progress: "In progress", needs_attention: "Needs attention", done: "Done" }[status];
}

function formatDashboardDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Recent activity" : parsed.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
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

function TasksView({
  snapshot,
  onRunAction,
  onRefresh
}: {
  snapshot: DashboardSnapshot;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onRefresh: () => void;
}) {
  const [task, setTask] = useState(snapshot.task.text || "");
  const [thread, setThread] = useState("global");
  const [state, setState] = useState(snapshot.task.state || "planned");
  const taskRows = snapshot.task_control || [];
  const history = snapshot.task_history || [];
  useEffect(() => {
    setTask(snapshot.task.text || "");
    setThread(snapshot.task.thread_id || "global");
    setState(snapshot.task.state || "planned");
  }, [snapshot.project.path, snapshot.task.text, snapshot.task.thread_id, snapshot.task.state]);
  return (
    <div className="view-stack">
      <SectionTitle title="Tasks" subtitle="Switch task text, state, and refresh context from the local control plane." />
      <div className="control-grid">
        <Panel title="Task Editor" icon={ClipboardList}>
          <div className="form-grid">
            <label className="field full">
              <span>Task history</span>
              <select
                value=""
                onChange={(event) => {
                  const selected = history.find((item, index) => `${index}:${item.task}` === event.target.value);
                  if (!selected) return;
                  setTask(selected.task);
                  setThread(selected.thread_id || "global");
                  setState(selected.status || "planned");
                }}
              >
                <option value="">Pick a previous task...</option>
                {history.map((item, index) => (
                  <option key={`${item.source}:${item.observed_at}:${item.task}:${index}`} value={`${index}:${item.task}`}>
                    {item.task} · {item.source}{item.thread_id ? ` · ${item.thread_id}` : ""}
                  </option>
                ))}
              </select>
              <small>Selecting history only fills this form. Use Set and refresh to write files.</small>
            </label>
            <label className="field full">
              <span>Task</span>
              <textarea value={task} onChange={(event) => setTask(event.target.value)} rows={4} placeholder="Describe the local task" />
            </label>
            <label className="field">
              <span>Scope</span>
              <select value={thread} onChange={(event) => setThread(event.target.value)}>
                <option value="global">global</option>
                {taskRows.filter((row) => row.thread_id).map((row) => (
                  <option key={row.thread_id || ""} value={row.thread_id || ""}>{row.thread_id}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>State</span>
              <select value={state} onChange={(event) => setState(event.target.value)}>
                <option value="planned">planned</option>
                <option value="in_progress">in_progress</option>
                <option value="blocked">blocked</option>
                <option value="done">done</option>
              </select>
            </label>
            <div className="inline-actions full">
              <button type="button" className="primary-action" onClick={() => onRunAction("set_task", { task, thread, refresh: true })}>
                <PlayCircle size={16} aria-hidden="true" />
                Set and refresh
              </button>
              <button type="button" className="secondary-action" onClick={() => onRunAction("set_state", { status: state, thread, summary: "Updated from dashboard." })}>
                Set state
              </button>
              <button type="button" className="secondary-action" onClick={() => onRunAction("clear_task", { thread })}>
                Clear task
              </button>
              <button type="button" className="secondary-action" onClick={onRefresh}>
                Refresh snapshot
              </button>
            </div>
          </div>
        </Panel>
        <Panel title="Known Task State" icon={FolderKanban}>
          <div className="stack-sm">
            {taskRows.map((row) => (
              <div key={`${row.scope}:${row.thread_id || "global"}`} className="list-row passive">
                <span>
                  <strong>{row.scope === "thread" ? row.thread_id : "global"}</strong>
                  <small>{row.task || "No task text"}</small>
                  <code>{row.task_path}</code>
                </span>
                <span className={`badge ${riskTone(row.state || row.status)}`}>{row.state || row.status || "unknown"}</span>
              </div>
            ))}
            {!taskRows.length ? <p className="empty">No task files found.</p> : null}
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

type MapMode = "city" | "network" | "semantic" | "table";

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
  onRunCommand
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
}) {
  const [mode, setMode] = useState<MapMode>(() => (hasWebGLSupport() ? "city" : "table"));
  const [demoMode, setDemoMode] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [sideCollapsed, setSideCollapsed] = useState(() => typeof window !== "undefined" && window.innerWidth <= 760);
  const [cameraSignal, setCameraSignal] = useState(0);
  const [hoverInfo, setHoverInfo] = useState<MapHoverInfo | null>(null);
  const mapRootRef = useRef<HTMLDivElement | null>(null);
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
  const showSide = !demoMode && !sideCollapsed;
  useEffect(() => {
    const handleFullscreenChange = () => {
      setFullscreen(document.fullscreenElement === mapRootRef.current);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);
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
          <button type="button" className="toolbar-button" onClick={() => setCameraSignal((value) => value + 1)}>
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
      </div>
      {mode === "network" ? (
        <label className="search-box map-search">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">Search context network</span>
          <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search files, tests, or memory" />
        </label>
      ) : null}

      <div className="map-layout">
        <section className="map-stage" aria-label="AgentPack context map">
          {mode === "city" ? (
            hasWebGLSupport() ? (
              <MapErrorBoundary resetKey={`${dashboardMap.generated_at}:${cameraSignal}`} onError={() => setMode("table")} fallback={<MapTable dashboardMap={dashboardMap} onSelect={onSelect} />}>
                <Suspense fallback={<div className="city-loading">Loading 3D city map...</div>}>
                  <ContextCityMap dashboardMap={dashboardMap} selectedId={selectedId} hoverInfo={hoverInfo} cameraSignal={cameraSignal} demoMode={demoMode} onSelect={onSelect} onHover={setHoverInfo} />
                </Suspense>
              </MapErrorBoundary>
            ) : (
              <MapTable dashboardMap={dashboardMap} onSelect={onSelect} />
            )
          ) : mode === "network" ? (
            <TaskGraph graph={graph} query={query} selectedId={selectedId} onSelect={onSelect} />
          ) : mode === "semantic" ? (
            <SemanticGraphNetwork graph={snapshot.semantic_graph} />
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
                <span><i className="legend-expressway" /> Expressway = high confidence route</span>
                <span><i className="legend-highway" /> Highway = medium confidence route</span>
                <span><i className="legend-county" /> County road = low confidence route</span>
              </div>
            </Panel>
            <Panel title="Why This File" icon={Search}>
              {selectedBuilding ? (
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
                <p className="empty">Select a building to inspect score, risk, tests, and actions.</p>
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

function SemanticGraphNetwork({ graph }: { graph: SemanticGraphSummary }) {
  const [relationship, setRelationship] = useState("");
  const [confidence, setConfidence] = useState("");
  const [language, setLanguage] = useState("");
  const [evidenceSource, setEvidenceSource] = useState("");
  const [showTable, setShowTable] = useState(false);
  const [selectedEdge, setSelectedEdge] = useState<string>("");
  const [remoteGraph, setRemoteGraph] = useState<SemanticGraphSummary | null>(null);
  const activeGraph = remoteGraph || graph;
  useEffect(() => {
    if (window.location.protocol === "file:") return;
    const params = new URLSearchParams({ limit: "200" });
    if (relationship) params.set("relationship", relationship);
    if (confidence) params.set("confidence", confidence);
    if (language) params.set("language", language);
    if (evidenceSource) params.set("evidence_source", evidenceSource);
    fetch(apiUrl(`/api/graph?${params.toString()}`), { headers: authHeaders() })
      .then((response) => response.ok ? response.json() as Promise<{ semantic_graph?: SemanticGraphSummary }> : Promise.reject(new Error("graph request failed")))
      .then((payload) => setRemoteGraph(payload.semantic_graph || null))
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
          <ReactFlow nodes={nodes} edges={flowEdges} fitView nodesDraggable nodesConnectable={false} panOnScroll minZoom={0.2} zoomOnDoubleClick={false} onEdgeClick={(_event, edge) => setSelectedEdge(edge.id)} onlyRenderVisibleElements>
            <Background />
            <MiniMap pannable zoomable className="graph-minimap" />
            <Controls />
          </ReactFlow>
        </div>
      )}
      <div className="semantic-network-receipt" data-testid="semantic-edge-receipt">
        {selected ? (() => {
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
  return (
    <div className="view-stack">
      <SectionTitle title="Memory Influence" subtitle="Episodic and procedural memory connected to this context decision." />
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
        <pre>{JSON.stringify(payload.snapshot, null, 2)}</pre>
      </Panel>
      <Panel title="Map JSON" icon={MapIcon}>
        <pre>{JSON.stringify(payload.map, null, 2)}</pre>
      </Panel>
      <Panel title="Graph JSON" icon={Network}>
        <pre>{JSON.stringify(payload.graph, null, 2)}</pre>
      </Panel>
      <Panel title="Action History JSON" icon={Activity}>
        <pre>{JSON.stringify(payload.action_history, null, 2)}</pre>
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

function ConfirmCommandDialog({
  pending,
  onCancel,
  onConfirm
}: {
  pending: PendingCommand;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-command-title">
        <div>
          <p className="eyebrow">Confirm command</p>
          <h1 id="confirm-command-title">This command can change local state</h1>
        </div>
        <code>{pending.command}</code>
        <div className="stack-sm">
          {pending.inspection.risk_reasons.map((reason) => (
            <div key={reason} className="list-row passive">
              <span>
                <strong>Risk</strong>
                <small>{reason}</small>
              </span>
            </div>
          ))}
        </div>
        <small>cwd: {pending.inspection.cwd}</small>
        <div className="modal-actions">
          <button type="button" className="secondary-action" onClick={onCancel}>Cancel</button>
          <button type="button" className="primary-action" onClick={onConfirm}>
            <PlayCircle size={16} aria-hidden="true" />
            Run command
          </button>
        </div>
      </section>
    </div>
  );
}

function Panel({ title, icon: Icon, children }: { title: string; icon: typeof Activity; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <Icon size={17} aria-hidden="true" />
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function SectionTitle({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="section-title">
      <h1>{title}</h1>
      <p>{subtitle}</p>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return (
    <div className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
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

function StatusPill({ status, label }: { status: string; label?: string }) {
  return <span role="status" className={`status-pill ${status}`}>{label ? `${label}: ${status || "unknown"}` : status || "unknown"}</span>;
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="center-state">
      <AlertTriangle size={28} aria-hidden="true" />
      <h1>Dashboard failed to load</h1>
      <p>{message}</p>
      <button type="button" className="primary-action" onClick={onRetry}>
        <RefreshCcw size={16} aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="center-state">
      <Activity size={28} aria-hidden="true" />
      <h1>Loading AgentPack cockpit</h1>
      <p>Reading local dashboard data.</p>
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
