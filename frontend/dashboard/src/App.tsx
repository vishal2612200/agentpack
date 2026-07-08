import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  CircleDot,
  ClipboardList,
  Code2,
  Database,
  FileText,
  FolderKanban,
  GitBranch,
  ListFilter,
  Network,
  PlayCircle,
  Search,
  Send,
  Settings,
  ShieldAlert,
  SlidersHorizontal,
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
import type { DashboardEdge, DashboardGraph, DashboardNode, DashboardSnapshot } from "./data/schema";

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
  { id: "graph", label: "Graph", icon: Network },
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
  const [view, setView] = useState<View>("cockpit");
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
            <TaskGraph graph={payload.graph} query={query} selectedId={selectedId} onSelect={setSelectedId} />
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
        <span className="sr-only">Search graph</span>
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
        <span className="badge neutral">Project</span>
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
                  <small>{project.source || "candidate"} · {project.detail || (project.valid ? "ready" : "unavailable")}</small>
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
            <Network size={17} aria-hidden="true" />
            Open graph
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

  return (
    <div className="graph-shell">
      <div className="graph-toolbar">
        <span><CircleDot size={14} aria-hidden="true" /> {graph.summary.node_count} nodes</span>
        <span>{graph.summary.edge_count} edges</span>
        <span>Drag grip to move nodes</span>
        {graph.summary.truncated ? <span className="badge warn">Truncated</span> : null}
        <button type="button" className="toolbar-button" onClick={() => setFitSignal((value) => value + 1)}>Fit view</button>
        <button type="button" className="toolbar-button" onClick={() => setNodePositions({})}>Reset layout</button>
        <button type="button" className={showOverview ? "toolbar-button active" : "toolbar-button"} onClick={() => setShowOverview((value) => !value)}>{showOverview ? "Hide overview" : "Overview"}</button>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        onNodeClick={handleClick}
        onNodeDragStop={handleNodeDragStop}
        nodesDraggable
        nodesConnectable={false}
        panOnScroll
        minZoom={0.12}
        zoomOnDoubleClick={false}
      >
        <GraphViewportController fitSignal={fitSignal} />
        <Background />
        {showOverview ? <MiniMap pannable zoomable className="graph-minimap" maskColor="rgba(8, 13, 22, 0.72)" nodeColor="#4b668a" nodeStrokeColor="#80a9ff" /> : null}
        <Controls />
      </ReactFlow>
    </div>
  );
}

function GraphViewportController({ fitSignal }: { fitSignal: number }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (fitSignal > 0) {
      fitView({ duration: 220, padding: 0.16 });
    }
  }, [fitSignal, fitView]);
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
        <Panel title="Graph Memory" icon={Brain}>
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
  const [selectedField, setSelectedField] = useState("");
  useEffect(() => setValues(initial), [initial]);
  useEffect(() => {
    if (!selectedField && editableFields[0]) {
      setSelectedField(editableFields[0].field_id);
    }
  }, [editableFields, selectedField]);
  const update = (field: string, value: unknown) => setValues((current) => ({ ...current, [field]: value }));
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
      <Panel title="Graph JSON" icon={Network}>
        <pre>{JSON.stringify(payload.graph, null, 2)}</pre>
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

  const nodes = graph.nodes
    .filter((node) => visible.has(node.id))
    .map((node, index) => ({
      id: node.id,
      position: nodePositions[node.id] || positionFor(index, node.type),
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

function positionFor(index: number, type: string) {
  const lane = type === "task" ? 0 : type === "episode" || type === "procedure" ? -1 : type === "test" || type === "action" ? 1 : 0;
  return {
    x: 120 + (index % 5) * 270,
    y: 120 + lane * 150 + Math.floor(index / 5) * 190
  };
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
