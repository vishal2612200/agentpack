import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  CircleDot,
  ClipboardList,
  Code2,
  FileText,
  GitBranch,
  ListFilter,
  Network,
  PlayCircle,
  Search,
  ShieldAlert,
  TerminalSquare
} from "lucide-react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler
} from "@xyflow/react";
import { loadDashboardPayload, type DashboardPayload } from "./data/loadDashboard";
import type { DashboardEdge, DashboardGraph, DashboardNode, DashboardSnapshot } from "./data/schema";

type View = "cockpit" | "graph" | "memory" | "risk" | "replay" | "raw";

const views: Array<{ id: View; label: string; icon: typeof Activity }> = [
  { id: "cockpit", label: "Cockpit", icon: Activity },
  { id: "graph", label: "Task Graph", icon: Network },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "risk", label: "Risk & Tests", icon: ShieldAlert },
  { id: "replay", label: "Replay", icon: PlayCircle },
  { id: "raw", label: "Raw Data", icon: Code2 }
];

export function App() {
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [error, setError] = useState<string>("");
  const [view, setView] = useState<View>("cockpit");
  const [selectedId, setSelectedId] = useState<string>("task:active");
  const [query, setQuery] = useState("");

  useEffect(() => {
    loadDashboardPayload()
      .then((loaded) => {
        setPayload(loaded);
        setSelectedId(loaded.graph.root_id || "task:active");
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load dashboard data"));
  }, []);

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!payload) {
    return <LoadingState />;
  }

  const selected = findSelected(payload.graph, selectedId);

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Dashboard navigation">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true">AP</div>
          <div>
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
        <TopBar snapshot={payload.snapshot} query={query} onQueryChange={setQuery} />
        <section className="main-panel" aria-label={`${view} view`}>
          {view === "cockpit" && (
            <CockpitView payload={payload} onSelect={setSelectedId} onOpenGraph={() => setView("graph")} />
          )}
          {view === "graph" && (
            <TaskGraph graph={payload.graph} query={query} selectedId={selectedId} onSelect={setSelectedId} />
          )}
          {view === "memory" && <MemoryView snapshot={payload.snapshot} graph={payload.graph} onSelect={setSelectedId} />}
          {view === "risk" && <RiskTestsView snapshot={payload.snapshot} onSelect={(id) => setSelectedId(id)} />}
          {view === "replay" && <ReplayView snapshot={payload.snapshot} graph={payload.graph} />}
          {view === "raw" && <RawDataView payload={payload} />}
        </section>
      </main>

      <Inspector selected={selected} />
    </div>
  );
}

function TopBar({
  snapshot,
  query,
  onQueryChange
}: {
  snapshot: DashboardSnapshot;
  query: string;
  onQueryChange: (value: string) => void;
}) {
  return (
    <header className="topbar">
      <div className="project-meta">
        <strong>{snapshot.project.name}</strong>
        <span>{snapshot.project.branch || "unknown branch"}</span>
        {snapshot.project.git_sha ? <span>{snapshot.project.git_sha}</span> : null}
      </div>
      <label className="search-box">
        <Search size={16} aria-hidden="true" />
        <span className="sr-only">Search graph</span>
        <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search paths, memory, tests" />
      </label>
      <StatusPill status={snapshot.context.status} />
    </header>
  );
}

function CockpitView({
  payload,
  onSelect,
  onOpenGraph
}: {
  payload: DashboardPayload;
  onSelect: (id: string) => void;
  onOpenGraph: () => void;
}) {
  const { snapshot, graph } = payload;
  const highRisk = snapshot.task_map.filter((item) => item.risk_level === "high");
  const tests = unique(snapshot.task_map.flatMap((item) => item.tests_to_run || []));
  const selectedFiles = graph.nodes.filter((node) => node.type === "file" && node.selected);
  const omittedFiles = graph.nodes.filter((node) => node.type === "file" && !node.selected);

  return (
    <div className="view-stack">
      <section className="hero-row">
        <div>
          <p className="eyebrow">Active task</p>
          <h1>{snapshot.task.text || "No task found"}</h1>
          <p className="muted">
            AgentPack selected context, memory, risk, and next actions for this local run.
          </p>
        </div>
        <button className="primary-action" type="button" onClick={onOpenGraph}>
          <Network size={17} aria-hidden="true" />
          Open graph
        </button>
      </section>

      <div className="metric-grid">
        <Metric label="Selected" value={graph.summary.selected_files} tone="good" />
        <Metric label="Omitted" value={graph.summary.omitted_files} tone="muted" />
        <Metric label="Memory" value={graph.summary.memory_nodes} tone="memory" />
        <Metric label="High risk" value={graph.summary.high_risk_files} tone="risk" />
        <Metric label="Tokens" value={formatNumber(snapshot.context.packed_tokens || 0)} tone="neutral" />
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
              <div key={test} className="command-row">
                <TerminalSquare size={16} aria-hidden="true" />
                <code>{test.endsWith(".py") ? `pytest ${test}` : test}</code>
              </div>
            ))}
            {!highRisk.length && !tests.length ? <p className="empty">No risk or test hints found.</p> : null}
          </div>
        </Panel>
        <Panel title="Next Actions" icon={ClipboardList}>
          <div className="stack-sm">
            {snapshot.suggested_actions.slice(0, 6).map((action) => (
              <div key={`${action.label}:${action.command}`} className="command-row">
                <CheckCircle2 size={16} aria-hidden="true" />
                <span>
                  <strong>{action.label}</strong>
                  {action.command ? <code>{action.command}</code> : null}
                </span>
              </div>
            ))}
            {!snapshot.suggested_actions.length ? <p className="empty">No suggested actions found.</p> : null}
          </div>
        </Panel>
      </div>
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
  const { nodes, edges } = useMemo(() => toFlowGraph(graph, query, selectedId), [graph, query, selectedId]);
  const handleClick: NodeMouseHandler = (_event, node) => onSelect(node.id);

  return (
    <div className="graph-shell">
      <div className="graph-toolbar">
        <span><CircleDot size={14} aria-hidden="true" /> {graph.summary.node_count} nodes</span>
        <span>{graph.summary.edge_count} edges</span>
        {graph.summary.truncated ? <span className="badge warn">Truncated</span> : null}
      </div>
      <ReactFlow nodes={nodes} edges={edges} fitView onNodeClick={handleClick} nodesDraggable={false}>
        <Background />
        <MiniMap pannable zoomable />
        <Controls />
      </ReactFlow>
    </div>
  );
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

function RiskTestsView({ snapshot, onSelect }: { snapshot: DashboardSnapshot; onSelect: (id: string) => void }) {
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
              </tr>
            ))}
            {!snapshot.task_map.length ? (
              <tr><td colSpan={5}>No task map found.</td></tr>
            ) : null}
          </tbody>
        </table>
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

function Inspector({ selected }: { selected: DashboardNode | DashboardEdge | null }) {
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
          <ActionList actions={selected.actions || []} />
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

function ActionList({ actions }: { actions: Array<{ label: string; command?: string }> }) {
  return (
    <section className="inspector-section">
      <h3>Actions</h3>
      {actions.length ? (
        <div className="stack-sm">
          {actions.slice(0, 8).map((action) => (
            <div key={`${action.label}:${action.command}`} className="command-row">
              <TerminalSquare size={15} aria-hidden="true" />
              <span>
                <strong>{action.label}</strong>
                {action.command ? <code>{action.command}</code> : null}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="empty">No direct action attached.</p>
      )}
    </section>
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

function StatusPill({ status }: { status: string }) {
  return <span className={`status-pill ${status}`}>{status || "unknown"}</span>;
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

function toFlowGraph(graph: DashboardGraph, query: string, selectedId: string): { nodes: Node[]; edges: Edge[] } {
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
      position: positionFor(index, node.type),
      data: { label: nodeLabel(node) },
      className: `flow-node ${node.type} ${node.selected ? "selected" : ""} ${node.stale ? "stale" : ""} ${node.id === selectedId ? "active" : ""}`,
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
  return (
    <div className="node-label">
      <span>{node.label}</span>
      {node.risk ? <small>{node.risk}</small> : null}
    </div>
  );
}

function positionFor(index: number, type: string) {
  const lane = type === "task" ? 0 : type === "episode" || type === "procedure" ? -1 : type === "test" || type === "action" ? 1 : 0;
  return {
    x: 120 + (index % 6) * 190,
    y: 120 + lane * 140 + Math.floor(index / 6) * 170
  };
}

function findSelected(graph: DashboardGraph, selectedId: string): DashboardNode | DashboardEdge | null {
  return graph.nodes.find((node) => node.id === selectedId) || graph.edges.find((edge) => edge.id === selectedId) || null;
}

function riskTone(value?: string) {
  if (value === "high" || value === "risk" || value === "missing") return "risk";
  if (value === "medium" || value === "stale" || value === "warn") return "warn";
  if (value === "low" || value === "fresh" || value === "good") return "good";
  if (value === "memory") return "memory";
  return "neutral";
}

function unique(values: string[]) {
  return Array.from(new Set(values.filter(Boolean)));
}

function formatNumber(value: number) {
  return new Intl.NumberFormat().format(value);
}
