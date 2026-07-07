import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  CircleDot,
  ClipboardList,
  Code2,
  Copy,
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

type View = "cockpit" | "projects" | "graph" | "memory" | "learning" | "risk" | "reviews" | "replay" | "raw";
type GraphFilter = "all" | "selected" | "risk" | "memory" | "tests";

const views: Array<{ id: View; label: string; icon: typeof Activity }> = [
  { id: "cockpit", label: "Cockpit", icon: Activity },
  { id: "projects", label: "Projects", icon: GitBranch },
  { id: "graph", label: "Task Graph", icon: Network },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "learning", label: "Learning", icon: ClipboardList },
  { id: "risk", label: "Risk & Tests", icon: ShieldAlert },
  { id: "reviews", label: "PR Reviews", icon: GitBranch },
  { id: "replay", label: "Replay", icon: PlayCircle },
  { id: "raw", label: "Raw Data", icon: Code2 }
];

export function App() {
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [error, setError] = useState<string>("");
  const [view, setView] = useState<View>("cockpit");
  const [selectedId, setSelectedId] = useState<string>("task:active");
  const [query, setQuery] = useState("");
  const [graphFilter, setGraphFilter] = useState<GraphFilter>("all");
  const [copyMessage, setCopyMessage] = useState("");

  useEffect(() => {
    loadDashboardPayload()
      .then((loaded) => {
        setPayload(loaded);
        setSelectedId(loaded.graph.root_id || "task:active");
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load dashboard data"));
  }, []);

  const loadSample = () => {
    const sample = samplePayload();
    setPayload(sample);
    setSelectedId(sample.graph.root_id || "task:active");
    setError("");
  };

  const copyText = async (value: string, label = "Command") => {
    if (!value) return;
    try {
      await navigator.clipboard?.writeText(value);
      setCopyMessage(`${label} copied`);
    } catch {
      setCopyMessage(value);
    }
    window.setTimeout(() => setCopyMessage(""), 1800);
  };

  if (error) {
    return <ErrorState message={error} onLoadSample={loadSample} />;
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
            <CockpitView
              payload={payload}
              onSelect={setSelectedId}
              onOpenGraph={(filter = "all") => {
                setGraphFilter(filter);
                setView("graph");
              }}
              onCopy={copyText}
              onLoadSample={loadSample}
            />
          )}
          {view === "projects" && <ProjectsView snapshot={payload.snapshot} onCopy={copyText} />}
          {view === "graph" && (
            <TaskGraph
              graph={payload.graph}
              query={query}
              filter={graphFilter}
              selectedId={selectedId}
              onFilterChange={setGraphFilter}
              onSelect={setSelectedId}
            />
          )}
          {view === "memory" && <MemoryView snapshot={payload.snapshot} graph={payload.graph} onSelect={setSelectedId} />}
          {view === "learning" && <LearningPrepView snapshot={payload.snapshot} onCopy={copyText} />}
          {view === "risk" && <RiskTestsView snapshot={payload.snapshot} onSelect={(id) => setSelectedId(id)} />}
          {view === "reviews" && <ReviewsView snapshot={payload.snapshot} onCopy={copyText} />}
          {view === "replay" && <ReplayView snapshot={payload.snapshot} graph={payload.graph} />}
          {view === "raw" && <RawDataView payload={payload} />}
        </section>
      </main>

      <Inspector selected={selected} onCopy={copyText} copyMessage={copyMessage} />
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
  onOpenGraph,
  onCopy,
  onLoadSample
}: {
  payload: DashboardPayload;
  onSelect: (id: string) => void;
  onOpenGraph: (filter?: GraphFilter) => void;
  onCopy: (value: string, label?: string) => void;
  onLoadSample: () => void;
}) {
  const { snapshot, graph } = payload;
  const highRisk = snapshot.task_map.filter((item) => item.risk_level === "high");
  const tests = unique(snapshot.task_map.flatMap((item) => item.tests_to_run || []));
  const selectedFiles = graph.nodes.filter((node) => node.type === "file" && node.selected);
  const omittedFiles = graph.nodes.filter((node) => node.type === "file" && !node.selected);
  const memoryNodes = graph.nodes.filter((node) => node.type === "episode" || node.type === "procedure");
  const decision = nextDecision(payload);
  const sparse = !snapshot.task.text && !snapshot.selected_files.length && !snapshot.task_map.length;

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
        <button className="primary-action" type="button" onClick={() => onOpenGraph()}>
          <Network size={17} aria-hidden="true" />
          Open graph
        </button>
      </section>

      {sparse ? <EmptyDecisionState onLoadSample={onLoadSample} /> : null}

      <section className={`decision-card ${decision.tone}`} aria-labelledby="decision-title">
        <div>
          <p className="eyebrow">Decision summary</p>
          <h2 id="decision-title">{decision.title}</h2>
          <p>{decision.detail}</p>
        </div>
        <div className="decision-actions">
          {decision.command ? (
            <button type="button" className="primary-action" onClick={() => onCopy(decision.command, "Next action")}>
              <Copy size={16} aria-hidden="true" />
              Copy command
            </button>
          ) : null}
          <button type="button" className="secondary-action" onClick={() => onOpenGraph(decision.filter)}>
            <Network size={16} aria-hidden="true" />
            Show path
          </button>
        </div>
      </section>

      <div className="metric-grid">
        <button type="button" className="metric-button" onClick={() => onOpenGraph("selected")}>
          <Metric label="Selected" value={graph.summary.selected_files} tone="good" />
        </button>
        <button type="button" className="metric-button" onClick={() => onOpenGraph()}>
          <Metric label="Omitted" value={graph.summary.omitted_files} tone="muted" />
        </button>
        <button type="button" className="metric-button" onClick={() => onOpenGraph("memory")}>
          <Metric label="Memory" value={graph.summary.memory_nodes} tone="memory" />
        </button>
        <Metric label="Prep" value={snapshot.learning_prep?.sessions?.length || 0} tone="memory" />
        <button type="button" className="metric-button" onClick={() => onOpenGraph("risk")}>
          <Metric label="High risk" value={graph.summary.high_risk_files} tone="risk" />
        </button>
        <Metric label="Projects" value={snapshot.project_index?.project_count || 0} tone="neutral" />
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
                <CopyButton value={test.endsWith(".py") ? `pytest ${test}` : test} label="test command" onCopy={onCopy} />
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
                {action.command ? <CopyButton value={action.command} label={action.label} onCopy={onCopy} /> : null}
              </div>
            ))}
            {!snapshot.suggested_actions.length ? <p className="empty">No suggested actions found.</p> : null}
          </div>
        </Panel>
        <Panel title="Memory Story" icon={Brain}>
          <div className="stack-sm">
            {memoryNodes.slice(0, 5).map((node) => (
              <button key={node.id} type="button" className="list-row" onClick={() => onSelect(node.id)}>
                <span>
                  <strong>{node.label}</strong>
                  <small>{node.summary || "Prior evidence connected to this task"}</small>
                </span>
                <span className={`badge ${node.stale ? "warn" : "memory"}`}>{node.stale ? "stale" : node.type}</span>
              </button>
            ))}
            {!memoryNodes.length ? <p className="empty">No memory influence found for this task.</p> : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function TaskGraph({
  graph,
  query,
  filter,
  selectedId,
  onFilterChange,
  onSelect
}: {
  graph: DashboardGraph;
  query: string;
  filter: GraphFilter;
  selectedId: string;
  onFilterChange: (filter: GraphFilter) => void;
  onSelect: (id: string) => void;
}) {
  const { nodes, edges } = useMemo(() => toFlowGraph(graph, query, filter, selectedId), [graph, query, filter, selectedId]);
  const handleClick: NodeMouseHandler = (_event, node) => onSelect(node.id);
  const filterItems: Array<{ id: GraphFilter; label: string }> = [
    { id: "all", label: "All" },
    { id: "selected", label: "Selected" },
    { id: "risk", label: "Risk" },
    { id: "memory", label: "Memory" },
    { id: "tests", label: "Tests" }
  ];

  return (
    <div className="graph-shell">
      <div className="graph-toolbar">
        <span><CircleDot size={14} aria-hidden="true" /> {graph.summary.node_count} nodes</span>
        <span>{graph.summary.edge_count} edges</span>
        {graph.summary.truncated ? <span className="badge warn">Truncated</span> : null}
        <div className="segmented-control" role="group" aria-label="Graph filter">
          {filterItems.map((item) => (
            <button
              key={item.id}
              type="button"
              className={filter === item.id ? "active" : ""}
              onClick={() => onFilterChange(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="graph-legend" aria-label="Graph legend">
          <span><i className="legend-dot selected" />Selected</span>
          <span><i className="legend-dot risk" />High risk</span>
          <span><i className="legend-dot memory" />Memory</span>
          <span><i className="legend-dot test" />Test</span>
        </div>
      </div>
      {nodes.length ? (
        <ReactFlow nodes={nodes} edges={edges} fitView onNodeClick={handleClick} nodesDraggable={false}>
          <Background />
          <MiniMap pannable zoomable />
          <Controls />
        </ReactFlow>
      ) : (
        <div className="center-state compact">
          <Search size={24} aria-hidden="true" />
          <h2>No graph matches</h2>
          <p>Clear search or switch filters to inspect the full context graph.</p>
        </div>
      )}
      <div className="graph-table-fallback" aria-label="Accessible graph node list">
        <h2>Graph nodes</h2>
        <ItemList
          items={graph.nodes.slice(0, 24).map((node) => ({
            id: node.id,
            title: node.path || node.label,
            detail: `${node.type}${node.summary ? `: ${node.summary}` : ""}`,
            tone: node.risk || node.type
          }))}
          empty="No graph nodes found."
          onSelect={onSelect}
        />
      </div>
    </div>
  );
}

function ProjectsView({
  snapshot,
  onCopy
}: {
  snapshot: DashboardSnapshot;
  onCopy: (value: string, label?: string) => void;
}) {
  const index = snapshot.project_index || { projects: [] };
  const projects = index.projects || [];
  return (
    <div className="view-stack">
      <SectionTitle title="Projects" subtitle="AgentPack-associated local projects, context health, token savings, and developer-productivity signals." />
      <div className="metric-grid">
        <Metric label="Projects" value={index.project_count || 0} tone="neutral" />
        <Metric label="Stale" value={index.stale_count || 0} tone={index.stale_count ? "warn" : "good"} />
        <Metric label="Saved tokens" value={formatNumber(index.estimated_saved_tokens || 0)} tone="good" />
        <Metric label="Avg savings" value={`${index.average_saving_pct || 0}%`} tone="memory" />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Project</th>
              <th>Context</th>
              <th>Tokens</th>
              <th>Signals</th>
              <th>Commands</th>
            </tr>
          </thead>
          <tbody>
            {projects.map((project) => (
              <tr key={project.path}>
                <td>
                  <strong>{project.name}{project.current ? " (current)" : ""}</strong>
                  <small>{project.task || project.path}</small>
                  <code>{project.path}</code>
                </td>
                <td>
                  <span className={`badge ${riskTone(project.context_status)}`}>{project.context_status || "unknown"}</span>
                  {project.branch ? <small>{project.branch} {project.git_sha || ""}</small> : null}
                </td>
                <td>
                  <strong>{project.saving_pct || 0}% saved</strong>
                  <small>{formatNumber(project.packed_tokens || 0)} / {formatNumber(project.raw_tokens || 0)} tokens</small>
                </td>
                <td>
                  <small>{project.selected_files_count || 0} files</small>
                  <small>{project.review_runs_count || 0} reviews</small>
                  <small>{project.memory_count || 0} memories</small>
                  <small>{project.weak_spots_count || 0} learning spots</small>
                </td>
                <td>
                  <div className="stack-sm">
                    {project.open_command ? <CopyCommand value={project.open_command} label={`open ${project.name}`} onCopy={onCopy} /> : null}
                    {project.refresh_command ? <CopyCommand value={project.refresh_command} label={`refresh ${project.name}`} onCopy={onCopy} /> : null}
                  </div>
                </td>
              </tr>
            ))}
            {!projects.length ? (
              <tr><td colSpan={5}>No AgentPack projects found near this checkout.</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
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

function LearningPrepView({
  snapshot,
  onCopy
}: {
  snapshot: DashboardSnapshot;
  onCopy: (value: string, label?: string) => void;
}) {
  const prep = snapshot.learning_prep || {};
  const sessions = prep.sessions || [];
  const weakSpots = snapshot.learning_weak_spots || [];
  return (
    <div className="view-stack">
      <SectionTitle title="Learning Prep" subtitle="Task-backed quiz, interview, and failure-drill preparation from AgentPack memory." />
      <div className="metric-grid">
        <Metric label="Queued" value={prep.queued_count || 0} tone="memory" />
        <Metric label="Needs review" value={prep.needs_review_count || 0} tone={prep.needs_review_count ? "warn" : "good"} />
        <Metric label="Completed" value={prep.completed_count || 0} tone="good" />
        <Metric label="Concepts" value={(prep.top_concepts || []).length} tone="neutral" />
      </div>
      <div className="content-grid">
        <Panel title="Prep Commands" icon={ClipboardList}>
          <div className="stack-sm">
            <PrepCommand label="Interview prep" value={prep.interview_command || 'agentpack learn "interview me on last task"'} onCopy={onCopy} />
            <PrepCommand label="Quiz" value={prep.quiz_command || 'agentpack learn "quiz me on last task"'} onCopy={onCopy} />
            <PrepCommand label="Failure drill" value={prep.failure_drill_command || 'agentpack learn "failure drill on last task"'} onCopy={onCopy} />
          </div>
        </Panel>
        <Panel title="Concept Focus" icon={Brain}>
          <div className="stack-sm">
            {(prep.top_concepts || []).slice(0, 8).map((concept) => (
              <div key={concept} className="list-row passive">
                <span>
                  <strong>{concept}</strong>
                  <small>{weakSpots.find((spot) => spot.concept === concept)?.latest_question || "Task-backed learning concept"}</small>
                </span>
                <span className="badge memory">concept</span>
              </div>
            ))}
            {!(prep.top_concepts || []).length ? <p className="empty">No learning concepts found yet.</p> : null}
          </div>
        </Panel>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Session</th>
              <th>Question</th>
              <th>Evidence</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session, index) => (
              <tr key={`${session.created_at}:${session.question}:${index}`}>
                <td>
                  <strong>{session.topic || session.mode || "Learning session"}</strong>
                  <small>{session.task}</small>
                  {session.request ? <code>{session.request}</code> : null}
                </td>
                <td>{session.question || "No question recorded."}</td>
                <td>
                  {(session.concepts || []).slice(0, 4).map((concept) => <span key={concept} className="badge memory">{concept}</span>)}
                  {(session.evidence_files || []).slice(0, 3).map((path) => <code key={path}>{path}</code>)}
                </td>
                <td>
                  <span className={`badge ${learningStatusTone(session.status)}`}>{session.status || "queued"}</span>
                  {typeof session.score === "number" ? <small>{session.score}%</small> : null}
                </td>
              </tr>
            ))}
            {!sessions.length ? (
              <tr><td colSpan={4}>No learning prep sessions found.</td></tr>
            ) : null}
          </tbody>
        </table>
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
              <tr key={`${item.kind}:${item.path}`}>
                <td>
                  <button type="button" className="link-button" onClick={() => onSelect(`file:${item.path}`)}>
                    <code>{item.path}</code>
                  </button>
                </td>
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

function ReviewsView({
  snapshot,
  onCopy
}: {
  snapshot: DashboardSnapshot;
  onCopy: (value: string, label?: string) => void;
}) {
  const runs = snapshot.review_runs || [];
  return (
    <div className="view-stack">
      <SectionTitle title="PR Reviews" subtitle="AgentPack review runs, stage state, and review commands for this project." />
      <div className="content-grid">
        <Panel title="Review Actions" icon={GitBranch}>
          <div className="stack-sm">
            <div className="command-row">
              <GitBranch size={16} aria-hidden="true" />
              <span>
                <strong>Run PR review</strong>
                <code>agentpack review --pr &lt;number&gt;</code>
              </span>
              <CopyButton value="agentpack review --pr <number>" label="run PR review" onCopy={onCopy} />
            </div>
            <div className="command-row">
              <CheckCircle2 size={16} aria-hidden="true" />
              <span>
                <strong>Check active review</strong>
                <code>agentpack review --check</code>
              </span>
              <CopyButton value="agentpack review --check" label="check review" onCopy={onCopy} />
            </div>
          </div>
        </Panel>
        <Panel title="Latest Runs" icon={ClipboardList}>
          <ItemList
            items={runs.slice(0, 6).map((run) => ({
              id: run.run_id,
              title: reviewRunTitle(run),
              detail: `${run.status || "prepared"} · ${run.changed_files_count || 0} files · ${run.diff_source || "unknown diff"}`,
              tone: reviewStatusTone(run.status)
            }))}
            empty="No AgentPack review runs found."
            onSelect={() => undefined}
          />
        </Panel>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Target</th>
              <th>Status</th>
              <th>Files</th>
              <th>Artifacts</th>
              <th>Commands</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.run_id}>
                <td>
                  <strong>{run.run_id}</strong>
                  <small>{run.generated_at || run.branch_prefix || ""}</small>
                </td>
                <td>{run.target_number ? `PR #${run.target_number}` : run.branch_prefix || "local"}</td>
                <td><span className={`badge ${reviewStatusTone(run.status)}`}>{run.status || "prepared"}</span></td>
                <td>{run.changed_files_count || 0}</td>
                <td>
                  {run.understanding_path ? <code>{run.understanding_path}</code> : null}
                  {run.findings_path ? <code>{run.findings_path}</code> : null}
                </td>
                <td>
                  <div className="stack-sm">
                    {run.resume_command ? <CopyCommand value={run.resume_command} label="resume" onCopy={onCopy} /> : null}
                    {run.check_command ? <CopyCommand value={run.check_command} label="check" onCopy={onCopy} /> : null}
                    {run.post_command ? <CopyCommand value={run.post_command} label="post" onCopy={onCopy} /> : null}
                  </div>
                </td>
              </tr>
            ))}
            {!runs.length ? (
              <tr><td colSpan={6}>No review runs found.</td></tr>
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

function Inspector({
  selected,
  onCopy,
  copyMessage
}: {
  selected: DashboardNode | DashboardEdge | null;
  onCopy: (value: string, label?: string) => void;
  copyMessage: string;
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
          <ActionList actions={selected.actions || []} onCopy={onCopy} />
          <p className="sr-only" aria-live="polite">{copyMessage}</p>
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
  onCopy
}: {
  actions: Array<{ label: string; command?: string }>;
  onCopy: (value: string, label?: string) => void;
}) {
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
              {action.command ? <CopyButton value={action.command} label={action.label} onCopy={onCopy} /> : null}
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

function ErrorState({ message, onLoadSample }: { message: string; onLoadSample: () => void }) {
  return (
    <div className="center-state">
      <AlertTriangle size={28} aria-hidden="true" />
      <h1>Dashboard failed to load</h1>
      <p>{message}</p>
      <button type="button" className="secondary-action" onClick={onLoadSample}>
        Show sample cockpit
      </button>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="center-state">
      <div className="skeleton-card" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <h1>Loading AgentPack cockpit</h1>
      <p>Reading local dashboard data.</p>
    </div>
  );
}

function EmptyDecisionState({ onLoadSample }: { onLoadSample: () => void }) {
  return (
    <section className="empty-decision" aria-labelledby="empty-title">
      <div>
        <p className="eyebrow">No context yet</p>
        <h2 id="empty-title">Start with a task or inspect a sample cockpit.</h2>
        <p>
          Run <code>agentpack start "your task"</code> or <code>agentpack pack --task auto</code> to populate selected files,
          risk, tests, and memory.
        </p>
      </div>
      <button type="button" className="secondary-action" onClick={onLoadSample}>
        Show sample decision
      </button>
    </section>
  );
}

function CopyButton({
  value,
  label,
  onCopy
}: {
  value: string;
  label: string;
  onCopy: (value: string, label?: string) => void;
}) {
  return (
    <button type="button" className="icon-button" aria-label={`Copy ${label}`} onClick={() => onCopy(value, label)}>
      <Copy size={15} aria-hidden="true" />
    </button>
  );
}

function CopyCommand({
  value,
  label,
  onCopy
}: {
  value: string;
  label: string;
  onCopy: (value: string, label?: string) => void;
}) {
  return (
    <button type="button" className="link-button" onClick={() => onCopy(value, label)}>
      <code>{value}</code>
    </button>
  );
}

function PrepCommand({
  label,
  value,
  onCopy
}: {
  label: string;
  value: string;
  onCopy: (value: string, label?: string) => void;
}) {
  return (
    <div className="command-row">
      <TerminalSquare size={16} aria-hidden="true" />
      <span>
        <strong>{label}</strong>
        <code>{value}</code>
      </span>
      <CopyButton value={value} label={label} onCopy={onCopy} />
    </div>
  );
}

function nextDecision(payload: DashboardPayload): { title: string; detail: string; command: string; tone: string; filter: GraphFilter } {
  const highRisk = payload.snapshot.task_map.find((item) => item.risk_level === "high");
  const firstTest = highRisk?.tests_to_run?.[0] || payload.snapshot.task_map.flatMap((item) => item.tests_to_run || [])[0] || "";
  if (highRisk && firstTest) {
    const command = firstTest.endsWith(".py") ? `pytest ${firstTest}` : firstTest;
    return {
      title: `Validate high-risk file ${highRisk.path}`,
      detail: highRisk.may_break?.[0] || highRisk.risk_reasons?.[0] || "Run the closest validation before editing more context.",
      command,
      tone: "risk",
      filter: "risk"
    };
  }
  if (payload.snapshot.context.status !== "fresh") {
    return {
      title: "Refresh context before trusting the pack",
      detail: payload.snapshot.context.stale_reason || "The current context state is not fresh.",
      command: "agentpack guard --agent codex --refresh-context",
      tone: "warn",
      filter: "all"
    };
  }
  const omitted = payload.snapshot.task_map.find((item) => item.kind === "omitted" && item.retrieve_ref);
  if (omitted) {
    return {
      title: `Inspect omitted context ${omitted.path}`,
      detail: omitted.why_selected?.[0] || "A relevant file was omitted from the selected pack.",
      command: `agentpack retrieve --block-id "${omitted.retrieve_ref}"`,
      tone: "warn",
      filter: "all"
    };
  }
  const action = payload.snapshot.suggested_actions.find((item) => item.command);
  return {
    title: action?.label || "Inspect the selected context path",
    detail: action?.reason || "Review selected files, memory influence, and suggested tests before editing.",
    command: action?.command || "",
    tone: "good",
    filter: "selected"
  };
}

function toFlowGraph(graph: DashboardGraph, query: string, filter: GraphFilter, selectedId: string): { nodes: Node[]; edges: Edge[] } {
  const lower = query.trim().toLowerCase();
  const memoryTargets = new Set(graph.edges.filter((edge) => edge.type === "memory_influenced").map((edge) => edge.target));
  const visible = new Set(
    graph.nodes
      .filter((node) => {
        if (!matchesGraphFilter(node, filter, memoryTargets)) return false;
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

function matchesGraphFilter(node: DashboardNode, filter: GraphFilter, memoryTargets: Set<string>) {
  if (filter === "selected") return node.type === "task" || ((node.type === "file" || node.type === "symbol") && Boolean(node.selected));
  if (filter === "risk") return node.type === "task" || node.risk === "high" || node.risk === "medium" || node.type === "action";
  if (filter === "memory") return node.type === "task" || node.type === "episode" || node.type === "procedure" || memoryTargets.has(node.id);
  if (filter === "tests") return node.type === "task" || node.type === "test" || node.actions?.some((action) => action.command?.includes("pytest"));
  return true;
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

function reviewRunTitle(run: DashboardSnapshot["review_runs"][number]) {
  if (run.target_number) return `PR #${run.target_number}`;
  return run.review_context || run.branch_prefix || run.run_id;
}

function reviewStatusTone(status?: string) {
  if (status === "findings_ready") return "good";
  if (status === "understanding_ready") return "memory";
  return "neutral";
}

function learningStatusTone(status?: string) {
  if (status === "done" || status === "completed") return "good";
  if (status === "needs_review") return "warn";
  return "memory";
}

function samplePayload(): DashboardPayload {
  const snapshot: DashboardSnapshot = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    project: {
      name: "sample-repo",
      path: "/local/sample-repo",
      branch: "feature/auth-hardening",
      git_sha: "sample"
    },
    project_index: {
      root_path: "/local",
      project_count: 2,
      stale_count: 1,
      missing_count: 0,
      total_raw_tokens: 82000,
      total_packed_tokens: 2440,
      estimated_saved_tokens: 79560,
      average_saving_pct: 96.9,
      projects: [
        {
          name: "sample-repo",
          path: "/local/sample-repo",
          current: true,
          branch: "feature/auth-hardening",
          git_sha: "sample",
          task: "Fix auth token expiry without breaking session refresh",
          context_status: "fresh",
          packed_tokens: 1240,
          raw_tokens: 42000,
          saving_pct: 97,
          selected_files_count: 2,
          review_runs_count: 1,
          memory_count: 3,
          weak_spots_count: 0,
          dashboard_path: "/local/sample-repo/.agentpack/index.html",
          open_command: "cd /local/sample-repo && agentpack dashboard --open",
          refresh_command: "cd /local/sample-repo && agentpack pack --task auto"
        },
        {
          name: "payments-service",
          path: "/local/payments-service",
          branch: "main",
          git_sha: "pay123",
          task: "Review checkout retry path",
          context_status: "stale",
          packed_tokens: 1200,
          raw_tokens: 40000,
          saving_pct: 96.8,
          selected_files_count: 5,
          review_runs_count: 2,
          memory_count: 6,
          weak_spots_count: 1,
          dashboard_path: "/local/payments-service/.agentpack/index.html",
          open_command: "cd /local/payments-service && agentpack dashboard --open",
          refresh_command: "cd /local/payments-service && agentpack pack --task auto"
        }
      ]
    },
    task: {
      text: "Fix auth token expiry without breaking session refresh",
      state: "sample"
    },
    context: {
      status: "fresh",
      mode: "balanced",
      packed_tokens: 1240,
      raw_tokens: 42000,
      selected_files_count: 2
    },
    selected_files: [
      {
        path: "src/auth/token.py",
        include_mode: "full",
        score: 220,
        reasons: ["task keyword match", "changed file"],
        symbols: [
          {
            name: "refresh_token",
            kind: "function",
            start_line: 42,
            end_line: 68,
            signature: "def refresh_token(session: Session) -> Token",
            summary: "Refreshes auth tokens for active sessions.",
            node_id: "sample-refresh-token"
          }
        ]
      },
      { path: "tests/test_auth_token.py", include_mode: "full", score: 160, reasons: ["related test"] }
    ],
    task_map: [
      {
        path: "src/auth/token.py",
        kind: "selected",
        include_mode: "full",
        score: 220,
        risk_level: "high",
        why_selected: ["task keyword match", "changed file"],
        risk_reasons: ["touches authentication contract", "reverse dependents found"],
        tests_to_run: ["tests/test_auth_token.py"],
        may_break: ["reverse dependents: src/api/session.py, src/ws/auth.py"],
        retrieve_ref: "src__auth__token.py:sample"
      },
      {
        path: "src/auth/session.py",
        kind: "omitted",
        include_mode: "summary",
        score: 96,
        risk_level: "medium",
        why_selected: ["related import"],
        tests_to_run: ["tests/test_session.py"],
        may_break: ["selected change may depend on this omitted file"],
        retrieve_ref: "src__auth__session.py:sample"
      }
    ],
    learning_memories: [
      {
        task: "Fixed session refresh regression",
        stage: "completed",
        status: "done",
        branch: "fix/session-refresh",
        git_sha: "abc123",
        concepts: ["auth", "session"],
        changed_files: ["src/auth/token.py"],
        selected_files: ["src/auth/session.py"]
      }
    ],
    learning_weak_spots: [
      {
        concept: "authentication",
        count: 2,
        mode: "interview",
        latest_task: "Fixed session refresh regression",
        latest_question: "How would you explain refresh-token expiry tradeoffs?",
        evidence_files: ["src/auth/token.py"]
      }
    ],
    learning_prep: {
      queued_count: 1,
      needs_review_count: 0,
      completed_count: 1,
      top_concepts: ["authentication", "session refresh", "test design"],
      sessions: [
        {
          task: "Fixed session refresh regression",
          request: "interview me on last task",
          mode: "interview",
          topic: "Authentication",
          question: "How would you explain refresh-token expiry tradeoffs in an interview?",
          status: "queued",
          concepts: ["authentication", "session refresh"],
          evidence_files: ["src/auth/token.py"],
          created_at: "2026-07-07T12:00:00Z"
        },
        {
          task: "Fixed session refresh regression",
          request: "quiz me on last task",
          mode: "quiz",
          topic: "Test Design",
          question: "Which regression test proves session refresh still works?",
          status: "done",
          score: 90,
          concepts: ["test design"],
          evidence_files: ["tests/test_auth_token.py"],
          created_at: "2026-07-07T12:05:00Z"
        }
      ],
      quiz_command: 'agentpack learn "quiz me on last task"',
      interview_command: 'agentpack learn "interview me on last task"',
      failure_drill_command: 'agentpack learn "failure drill on last task"'
    },
    observer: {
      events: 1,
      insights: [
        {
          kind: "similar_task",
          title: "Prior auth work touched session refresh",
          detail: "Inspect omitted session context before changing token TTL.",
          confidence: 0.7,
          related_files: ["src/auth/session.py"]
        }
      ]
    },
    benchmarks: { averages: {}, misses: [] },
    loop: {},
    review_runs: [
      {
        run_id: "20260707T120000-abcd1234",
        branch_prefix: "pr-42",
        generated_at: "2026-07-07T12:00:00Z",
        review_context: "Review auth hardening PR",
        target_number: 42,
        target_url: "https://github.com/acme/sample-repo/pull/42",
        diff_source: "pr-target",
        changed_files_count: 5,
        scaffold: "strict",
        status: "understanding_ready",
        run_dir: ".agentpack/reviews/pr-42/20260707T120000-abcd1234",
        preflight_path: ".agentpack/reviews/pr-42/20260707T120000-abcd1234/preflight.json",
        understanding_path: ".agentpack/reviews/pr-42/20260707T120000-abcd1234/understanding.toon",
        findings_path: ".agentpack/reviews/pr-42/20260707T120000-abcd1234/findings.toon",
        resume_command: "agentpack review --resume 20260707T120000-abcd1234",
        check_command: "agentpack review --check",
        post_command: "agentpack review --check --post-inline-comments"
      }
    ],
    suggested_actions: [
      { label: "Run auth token tests", command: "pytest tests/test_auth_token.py", kind: "command" },
      { label: "Retrieve omitted session context", command: "agentpack retrieve --block-id \"src__auth__session.py:sample\"", kind: "command" }
    ]
  };
  const graph: DashboardGraph = {
    schema_version: 1,
    generated_at: snapshot.generated_at,
    root_id: "task:active",
    summary: {
      node_count: 7,
      edge_count: 7,
      selected_files: 2,
      omitted_files: 1,
      memory_nodes: 1,
      high_risk_files: 1,
      max_nodes: 80,
      truncated_reason: "",
      truncated: false
    },
    nodes: [
      { id: "task:active", type: "task", label: snapshot.task.text || "Sample task", summary: snapshot.task.text },
      {
        id: "file:src/auth/token.py",
        type: "file",
        label: "token.py",
        path: "src/auth/token.py",
        selected: true,
        risk: "high",
        summary: "Authentication contract and reverse dependents.",
        actions: [
          { label: "Open file", command: "src/auth/token.py", kind: "path" },
          { label: "Run tests", command: "pytest tests/test_auth_token.py", kind: "command" }
        ]
      },
      {
        id: "symbol:sample-refresh-token",
        type: "symbol",
        label: "refresh_token",
        path: "src/auth/token.py",
        selected: true,
        summary: "Refreshes auth tokens for active sessions.",
        metadata: { file: "src/auth/token.py", symbol: "refresh_token", kind: "function", start_line: 42 },
        evidence: [{ kind: "symbol", ref: "L42-L68", summary: "def refresh_token(session: Session) -> Token", path: "src/auth/token.py", line: 42 }]
      },
      {
        id: "file:src/auth/session.py",
        type: "file",
        label: "session.py",
        path: "src/auth/session.py",
        selected: false,
        risk: "medium",
        summary: "Omitted related session context.",
        actions: [{ label: "Retrieve context", command: "agentpack retrieve --block-id \"src__auth__session.py:sample\"" }]
      },
      { id: "test:tests/test_auth_token.py", type: "test", label: "test_auth_token.py", path: "tests/test_auth_token.py", summary: "Suggested validation." },
      { id: "episode:task-memory:session-refresh", type: "episode", label: "Fixed session refresh regression", summary: "Prior auth episode referenced token and session files." },
      { id: "action:impact:auth", type: "action", label: "Session refresh may break", risk: "high", summary: "Reverse dependents use token expiry." }
    ],
    edges: [
      { id: "edge:task:file:token", source: "task:active", target: "file:src/auth/token.py", type: "selected_because", label: "selected", confidence: 0.9 },
      { id: "edge:file:symbol:refresh", source: "file:src/auth/token.py", target: "symbol:sample-refresh-token", type: "contains", label: "contains", confidence: 0.9 },
      { id: "edge:task:file:session", source: "task:active", target: "file:src/auth/session.py", type: "omitted_because", label: "omitted", confidence: 0.6 },
      { id: "edge:file:test", source: "file:src/auth/token.py", target: "test:tests/test_auth_token.py", type: "tested_by", label: "tested by", confidence: 0.8 },
      { id: "edge:memory:file", source: "episode:task-memory:session-refresh", target: "file:src/auth/token.py", type: "memory_influenced", label: "memory", confidence: 0.7 },
      { id: "edge:memory:symbol", source: "episode:task-memory:session-refresh", target: "symbol:sample-refresh-token", type: "memory_influenced", label: "memory", confidence: 0.75 },
      { id: "edge:file:impact", source: "file:src/auth/token.py", target: "action:impact:auth", type: "may_break", label: "may break", confidence: 0.6 }
    ]
  };
  return { snapshot, graph };
}
