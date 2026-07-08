import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  Brain,
  Building2,
  CheckCircle2,
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
import { Canvas, useFrame } from "@react-three/fiber";
import { Html, OrbitControls, RoundedBox } from "@react-three/drei";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import agentPackSymbolUrl from "../../../docs/assets/agentpack-symbol.png";
import { apiUrl, authHeaders, dashboardToken, loadDashboardPayload, type DashboardPayload } from "./data/loadDashboard";
import type { ActionHistoryRow, DashboardEdge, DashboardGraph, DashboardMap, DashboardNode, DashboardSnapshot, MapBuilding } from "./data/schema";

type View = "cockpit" | "tasks" | "threads" | "context" | "graph" | "files" | "settings" | "integrations" | "workflow" | "learning" | "raw";

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

const views: Array<{ id: View; label: string; icon: typeof Activity }> = [
  { id: "cockpit", label: "Cockpit", icon: Activity },
  { id: "tasks", label: "Tasks", icon: ClipboardList },
  { id: "threads", label: "Threads", icon: GitBranch },
  { id: "context", label: "Context", icon: Database },
  { id: "graph", label: "Map", icon: MapIcon },
  { id: "files", label: "Files", icon: FileText },
  { id: "settings", label: "Settings", icon: Settings },
  { id: "integrations", label: "Integrations", icon: TerminalSquare },
  { id: "workflow", label: "Workflow", icon: Workflow },
  { id: "learning", label: "Learning & Skills", icon: Brain },
  { id: "raw", label: "Raw Data", icon: Code2 }
];

export function App() {
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [error, setError] = useState<string>("");
  const [view, setView] = useState<View>("graph");
  const [selectedId, setSelectedId] = useState<string>("task:active");
  const [query, setQuery] = useState("");
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [sessions, setSessions] = useState<TerminalSessionState[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>("");
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(null);
  const streams = useRef<Map<string, EventSource>>(new Map());

  const refreshDashboard = async () => {
    const loaded = await loadDashboardPayload();
    setPayload(loaded);
    setSelectedId((current) => current || loaded.graph.root_id || "task:active");
    return loaded;
  };

  useEffect(() => {
    refreshDashboard()
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load dashboard data"));
  }, []);

  useEffect(() => {
    return () => {
      streams.current.forEach((stream) => stream.close());
      streams.current.clear();
    };
  }, []);

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!payload) {
    return <LoadingState />;
  }

  const selected = findSelected(payload.graph, selectedId);

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
          {views.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={view === item.id ? "nav-item active" : "nav-item"}
                onClick={() => setView(item.id)}
              >
                <Icon size={17} aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <main className="workspace">
        <TopBar snapshot={payload.snapshot} query={query} onQueryChange={setQuery} onSwitchProject={handleSwitchProject} />
        <section className="main-panel" aria-label={`${view} view`}>
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
  query,
  onQueryChange,
  onSwitchProject
}: {
  snapshot: DashboardSnapshot;
  query: string;
  onQueryChange: (value: string) => void;
  onSwitchProject: (path: string) => void;
}) {
  return (
    <header className="topbar">
      <ProjectDropdown snapshot={snapshot} onSwitchProject={onSwitchProject} />
      <label className="search-box">
        <Search size={16} aria-hidden="true" />
        <span className="sr-only">Search map</span>
        <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search paths, memory, tests" />
      </label>
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
    valid: true
  };
  return (
    <div className="project-dropdown">
      <button type="button" className="project-trigger" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span>
          <strong>{current.name}</strong>
          <small>{current.branch || "unknown branch"}{current.git_sha ? ` · ${current.git_sha}` : ""}</small>
        </span>
        <span className="badge neutral">Atlas</span>
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
                  <small>{project.source || "candidate"} · {project.detail || (project.valid ? "map-ready" : "unavailable")}</small>
                  <code>{project.path}</code>
                </span>
                <span className={`badge ${project.valid ? "good" : "warn"}`}>{project.current ? "current" : project.valid ? "open" : "skip"}</span>
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

type MapMode = "city" | "network" | "table";

function MapView({
  dashboardMap,
  graph,
  snapshot,
  actionHistory,
  query,
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
  selectedId: string;
  onSelect: (id: string) => void;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onRunCommand: (command: string) => void;
}) {
  const [mode, setMode] = useState<MapMode>(() => (hasWebGLSupport() ? "city" : "table"));
  const [demoMode, setDemoMode] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [cameraSignal, setCameraSignal] = useState(0);
  const mapRootRef = useRef<HTMLDivElement | null>(null);
  const selectedBuilding = dashboardMap.buildings.find((building) => building.node_id === selectedId);
  const payloadRequiredActions = new Set(["work", "route_task", "retrieve"]);
  const primaryCatalog = (snapshot.command_catalog || []).filter((item) => item.primary && !payloadRequiredActions.has(item.id)).slice(0, 8);
  const weather = dashboardMap.weather || [];
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
          <p className="muted">Buildings are files. Height is confidence. Color is risk. Glow marks selected context.</p>
        </div>
        <div className="map-hero-actions">
          <button type="button" className={demoMode ? "toolbar-button active" : "toolbar-button"} onClick={() => setDemoMode((value) => !value)}>Demo</button>
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

      <div className="map-layout">
        <section className="map-stage" aria-label="AgentPack context map">
          {mode === "city" ? (
            hasWebGLSupport() ? (
              <MapErrorBoundary resetKey={`${dashboardMap.generated_at}:${cameraSignal}`} onError={() => setMode("table")} fallback={<MapTable dashboardMap={dashboardMap} onSelect={onSelect} />}>
                <ContextCityMap dashboardMap={dashboardMap} selectedId={selectedId} cameraSignal={cameraSignal} demoMode={demoMode} onSelect={onSelect} />
              </MapErrorBoundary>
            ) : (
              <MapTable dashboardMap={dashboardMap} onSelect={onSelect} />
            )
          ) : mode === "network" ? (
            <TaskGraph graph={graph} query={query} selectedId={selectedId} onSelect={onSelect} />
          ) : (
            <MapTable dashboardMap={dashboardMap} onSelect={onSelect} />
          )}
        </section>

        {!demoMode && !fullscreen ? (
          <aside className="map-side">
            <Panel title="Map Legend" icon={MapIcon}>
              <div className="map-legend">
                <span><i className="legend-height" /> Height = confidence</span>
                <span><i className="legend-selected" /> Glow = selected context</span>
                <span><i className="legend-risk" /> Red = high risk</span>
                <span><i className="legend-memory" /> Cyan = memory linked</span>
              </div>
            </Panel>
            <Panel title="Why This File" icon={Search}>
              {selectedBuilding ? (
                <div className="map-building-detail">
                  <strong>{selectedBuilding.path}</strong>
                  <span className={`badge ${riskTone(selectedBuilding.risk)}`}>{selectedBuilding.risk || "unknown"}</span>
                  <small>score {Math.round(selectedBuilding.score)} · confidence {Math.round(selectedBuilding.confidence * 100)}% · {selectedBuilding.include_mode || "mode unknown"}</small>
                  {(selectedBuilding.reasons || []).slice(0, 5).map((reason) => <p key={reason}>{reason}</p>)}
                  {(selectedBuilding.tests || []).slice(0, 3).map((test) => (
                    <CommandAction key={test} label="Run validation" command={test.endsWith(".py") ? `pytest ${test}` : test} compact onRunCommand={onRunCommand} />
                  ))}
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

function ContextCityMap({
  dashboardMap,
  selectedId,
  cameraSignal,
  demoMode,
  onSelect
}: {
  dashboardMap: DashboardMap;
  selectedId: string;
  cameraSignal: number;
  demoMode: boolean;
  onSelect: (id: string) => void;
}) {
  const reducedMotion = useReducedMotion();
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  useEffect(() => {
    controlsRef.current?.reset();
  }, [cameraSignal]);

  return (
    <div className="city-canvas-wrap">
      <Canvas shadows camera={{ position: [122, 118, 172], fov: 34 }} dpr={[1, 1.6]} gl={{ antialias: true, alpha: true }}>
        <color attach="background" args={["#08111f"]} />
        <ambientLight intensity={0.62} />
        <directionalLight castShadow position={[34, 54, 34]} intensity={1.18} />
        <CityScene dashboardMap={dashboardMap} selectedId={selectedId} reducedMotion={reducedMotion || demoMode} onSelect={onSelect} />
        <OrbitControls ref={controlsRef} makeDefault target={[32, 5, 22]} enableDamping={!reducedMotion} dampingFactor={0.08} minDistance={24} maxDistance={320} maxPolarAngle={Math.PI / 2.08} />
      </Canvas>
    </div>
  );
}

function CityScene({
  dashboardMap,
  selectedId,
  reducedMotion,
  onSelect
}: {
  dashboardMap: DashboardMap;
  selectedId: string;
  reducedMotion: boolean;
  onSelect: (id: string) => void;
}) {
  const center = useMemo(() => mapCenter(dashboardMap), [dashboardMap]);
  const points = useMemo(() => mapPoints(dashboardMap), [dashboardMap]);
  return (
    <group position={[-center.x, 0, -center.z]}>
      <mesh position={[center.x, -0.08, center.z]} receiveShadow>
        <boxGeometry args={[Math.max(42, center.width + 28), 0.12, Math.max(34, center.depth + 28)]} />
        <meshStandardMaterial color="#0f1b2d" roughness={0.92} metalness={0.05} />
      </mesh>
      {dashboardMap.districts.map((district) => (
        <group key={district.id}>
          <mesh position={[district.x + 8, 0.02, district.z + 8]} rotation={[0, Math.PI / 8, 0]}>
            <cylinderGeometry args={[22, 22, 0.1, 8]} />
            <meshStandardMaterial color={district.selected_count ? "#182c46" : "#131f30"} roughness={0.9} />
          </mesh>
          <Html position={[district.x + 4, 0.35, district.z - 12]} center className="district-label">
            {district.label}
          </Html>
        </group>
      ))}
      {dashboardMap.roads.slice(0, 80).map((road) => (
        <RoadMesh key={road.id} road={road} points={points} />
      ))}
      {dashboardMap.landmarks.map((landmark) => (
        <group key={landmark.id} position={[landmark.x, 0, landmark.z]}>
          <mesh>
            <cylinderGeometry args={landmark.type === "action" ? [0.72, 0.72, 0.7, 16] : [1.6, 1.6, 1.8, 18]} />
            <meshStandardMaterial color={landmark.tone === "risk" ? "#ff7a7f" : landmark.tone === "good" ? "#6ed49a" : "#80a9ff"} emissive="#1b355d" emissiveIntensity={0.28} />
          </mesh>
          {landmark.type === "action" ? null : (
            <Html position={[0, 2.4, 0]} center className="district-label">
              {landmark.label}
            </Html>
          )}
        </group>
      ))}
      {dashboardMap.buildings.map((building) => (
        <BuildingMesh key={building.id} building={building} selected={building.node_id === selectedId} reducedMotion={reducedMotion} onSelect={onSelect} />
      ))}
    </group>
  );
}

function BuildingMesh({
  building,
  selected,
  reducedMotion,
  onSelect
}: {
  building: MapBuilding;
  selected: boolean;
  reducedMotion: boolean;
  onSelect: (id: string) => void;
}) {
  const ref = useRef<any>(null);
  const width = 6.0 + building.confidence * 3.2 + (building.selected ? 0.5 : 0);
  const depth = 5.4 + building.confidence * 2.6 + (building.memory_linked ? 0.35 : 0);
  const moduleHeight = 2.8 + building.confidence * 7.2;
  const domeHeight = 0.85 + building.confidence * 1.2;
  const antennaHeight = building.selected || building.memory_linked ? 2.4 + building.confidence * 3.2 : 1.1 + building.confidence * 1.8;
  const podiumHeight = 0.42;
  const accentColor = building.memory_linked ? "#38cfd3" : selected ? "#80a9ff" : "#d9e7ff";
  const roofColor = selected ? "#cfe0ff" : building.memory_linked ? "#b5f7f5" : "#d7e2ef";
  const windowRows = Math.min(4, Math.max(2, Math.floor(moduleHeight / 2.4)));
  useFrame(({ clock }) => {
    if (!ref.current || reducedMotion || !selected) return;
    ref.current.position.y = Math.sin(clock.elapsedTime * 2.4) * 0.18;
  });
  return (
    <group
      ref={ref}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(building.node_id);
      }}
    >
      {selected ? (
        <mesh position={[building.x, 0.05, building.z]}>
          <cylinderGeometry args={[Math.max(width, depth) * 0.82, Math.max(width, depth) * 0.82, 0.08, 48]} />
          <meshBasicMaterial color="#80a9ff" transparent opacity={0.32} />
        </mesh>
      ) : null}
      {building.selected ? (
        <mesh position={[building.x, 0.09, building.z]}>
          <cylinderGeometry args={[Math.max(width, depth) * 0.68, Math.max(width, depth) * 0.68, 0.06, 40]} />
          <meshBasicMaterial color={accentColor} transparent opacity={0.22} />
        </mesh>
      ) : null}
      <mesh
        castShadow
        receiveShadow
        position={[building.x, podiumHeight / 2, building.z]}
      >
        <cylinderGeometry args={[Math.max(width, depth) * 0.68, Math.max(width, depth) * 0.76, podiumHeight, 8]} />
        <meshStandardMaterial
          color="#182940"
          roughness={0.76}
          metalness={0.12}
        />
      </mesh>
      <RoundedBox castShadow receiveShadow args={[width, moduleHeight, depth]} radius={0.52} smoothness={7} position={[building.x, podiumHeight + moduleHeight / 2, building.z]}>
        <meshStandardMaterial
          color={building.color}
          roughness={0.64}
          metalness={0.12}
          emissive={selected ? "#294f91" : building.memory_linked ? "#0f5d61" : "#000000"}
          emissiveIntensity={selected ? 0.22 : building.memory_linked ? 0.18 : 0}
        />
      </RoundedBox>
      <mesh position={[building.x, podiumHeight + moduleHeight + domeHeight * 0.12, building.z]} scale={[width * 0.44, domeHeight * 0.42, depth * 0.44]}>
        <sphereGeometry args={[1, 28, 14]} />
        <meshStandardMaterial color={roofColor} roughness={0.42} metalness={0.24} emissive={accentColor} emissiveIntensity={selected || building.memory_linked ? 0.2 : 0.05} />
      </mesh>
      <mesh position={[building.x - width * 0.62, podiumHeight + moduleHeight * 0.42, building.z]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[Math.max(1.05, depth * 0.16), Math.max(1.05, depth * 0.16), width * 0.38, 20]} />
        <meshStandardMaterial color={building.color} roughness={0.66} metalness={0.1} emissive={building.memory_linked ? "#0f5d61" : "#000000"} emissiveIntensity={building.memory_linked ? 0.14 : 0} />
      </mesh>
      <mesh position={[building.x + width * 0.62, podiumHeight + moduleHeight * 0.38, building.z + depth * 0.08]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[Math.max(0.95, depth * 0.14), Math.max(0.95, depth * 0.14), width * 0.32, 20]} />
        <meshStandardMaterial color={building.color} roughness={0.66} metalness={0.1} />
      </mesh>
      {Array.from({ length: windowRows }).map((_, index) => (
        <mesh key={`${building.id}:window-front:${index}`} position={[building.x, podiumHeight + 1.25 + index * Math.max(1.35, moduleHeight / (windowRows + 1)), building.z + depth / 2 + 0.016]}>
          <boxGeometry args={[Math.max(1.2, width * 0.42), 0.09, 0.035]} />
          <meshBasicMaterial color={accentColor} transparent opacity={selected || building.memory_linked ? 0.78 : 0.46} />
        </mesh>
      ))}
      {Array.from({ length: Math.min(5, windowRows) }).map((_, index) => (
        <mesh key={`${building.id}:window-side:${index}`} position={[building.x + width / 2 + 0.016, podiumHeight + 1.45 + index * Math.max(1.45, moduleHeight / (windowRows + 1)), building.z]} rotation={[0, Math.PI / 2, 0]}>
          <boxGeometry args={[Math.max(1.0, depth * 0.42), 0.075, 0.035]} />
          <meshBasicMaterial color={accentColor} transparent opacity={selected || building.memory_linked ? 0.68 : 0.38} />
        </mesh>
      ))}
      <mesh position={[building.x, podiumHeight + moduleHeight + domeHeight + antennaHeight / 2, building.z]}>
        <cylinderGeometry args={[0.045, 0.045, antennaHeight, 10]} />
        <meshBasicMaterial color={accentColor} transparent opacity={0.74} />
      </mesh>
      <mesh position={[building.x, podiumHeight + moduleHeight + domeHeight + antennaHeight + 0.16, building.z]}>
        <sphereGeometry args={[0.2, 12, 8]} />
        <meshBasicMaterial color={accentColor} transparent opacity={selected || building.memory_linked ? 0.95 : 0.56} />
      </mesh>
    </group>
  );
}

function RoadMesh({
  road,
  points
}: {
  road: { source: string; target: string; type: string };
  points: Map<string, { x: number; z: number }>;
}) {
  const source = points.get(road.source);
  const target = points.get(road.target);
  if (!source || !target) return null;
  const sx = source.x;
  const sz = source.z;
  const tx = target.x;
  const tz = target.z;
  const dx = tx - sx;
  const dz = tz - sz;
  const length = Math.sqrt(dx * dx + dz * dz);
  const angle = Math.atan2(dz, dx);
  return (
    <mesh position={[sx + dx / 2, 0.11, sz + dz / 2]} rotation={[0, -angle, 0]}>
      <boxGeometry args={[length, 0.06, road.type === "selected_because" ? 0.42 : 0.24]} />
      <meshBasicMaterial color={road.type === "memory_influenced" ? "#38cfd3" : "#6d86ae"} transparent opacity={road.type === "selected_because" ? 0.34 : 0.2} />
    </mesh>
  );
}

function MapTable({ dashboardMap, onSelect }: { dashboardMap: DashboardMap; onSelect: (id: string) => void }) {
  return (
    <div className="table-wrap map-table">
      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>District</th>
            <th>Confidence</th>
            <th>Risk</th>
            <th>Mode</th>
            <th>Tests</th>
          </tr>
        </thead>
        <tbody>
          {dashboardMap.buildings.map((building) => (
            <tr key={building.id} onClick={() => onSelect(building.node_id)}>
              <td><code>{building.path}</code></td>
              <td>{building.district_id}</td>
              <td>{Math.round(building.confidence * 100)}%</td>
              <td><span className={`badge ${riskTone(building.risk)}`}>{building.risk || "unknown"}</span></td>
              <td>{building.include_mode || "unknown"}</td>
              <td>{(building.tests || []).slice(0, 2).join(", ") || "none"}</td>
            </tr>
          ))}
          {!dashboardMap.buildings.length ? (
            <tr><td colSpan={6}>No map buildings found.</td></tr>
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
            <small>{row.status || "recorded"} · {row.started_at || row.ended_at || "no timestamp"}</small>
            {row.command ? <code>{row.command}</code> : null}
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
        id: "local-dev",
        label: "Local dev",
        description: "Balanced context with tests included for daily local work.",
        updates: {
          "context.default_mode": "balanced",
          "context.default_budget": 12000,
          "context.include_tests": true
        }
      },
      {
        id: "review-mode",
        label: "Review mode",
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
        label: "Fast context",
        description: "Smaller context for fast iteration and quick routing.",
        updates: {
          "context.default_mode": "lite",
          "context.default_budget": 8000,
          "context_lite.max_selected_files": 8
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
  return <span className={`status-pill ${status}`}>{label ? `${label}: ${status || "unknown"}` : status || "unknown"}</span>;
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="center-state">
      <AlertTriangle size={28} aria-hidden="true" />
      <h1>Dashboard failed to load</h1>
      <p>{message}</p>
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

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleChange = () => setReduced(query.matches);
    handleChange();
    query.addEventListener("change", handleChange);
    return () => query.removeEventListener("change", handleChange);
  }, []);

  return reduced;
}

function mapPoints(dashboardMap: DashboardMap) {
  const points = new Map<string, { x: number; z: number }>();
  dashboardMap.buildings.forEach((building) => {
    points.set(building.id, { x: building.x, z: building.z });
    points.set(building.node_id, { x: building.x, z: building.z });
  });
  dashboardMap.landmarks.forEach((landmark) => {
    points.set(landmark.id, { x: landmark.x, z: landmark.z });
  });
  return points;
}

function mapCenter(dashboardMap: DashboardMap) {
  const coordinates = [
    ...dashboardMap.buildings.map((building) => ({ x: building.x, z: building.z })),
    ...dashboardMap.landmarks.map((landmark) => ({ x: landmark.x, z: landmark.z })),
    ...dashboardMap.districts.map((district) => ({ x: district.x, z: district.z }))
  ];
  if (!coordinates.length) {
    return { x: 0, z: 0, width: 48, depth: 36 };
  }
  const minX = Math.min(...coordinates.map((item) => item.x));
  const maxX = Math.max(...coordinates.map((item) => item.x));
  const minZ = Math.min(...coordinates.map((item) => item.z));
  const maxZ = Math.max(...coordinates.map((item) => item.z));
  return {
    x: (minX + maxX) / 2,
    z: (minZ + maxZ) / 2,
    width: Math.max(12, maxX - minX),
    depth: Math.max(12, maxZ - minZ)
  };
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
