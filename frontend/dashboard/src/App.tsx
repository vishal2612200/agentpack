import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
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
  Maximize2,
  Minimize2,
  Network,
  PlayCircle,
  Search,
  ShieldAlert,
  TerminalSquare
} from "lucide-react";
import {
  Background,
  Controls,
  ReactFlow,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type NodeMouseHandler
} from "@xyflow/react";
import { loadDashboardPayload, type DashboardPayload } from "./data/loadDashboard";
import type { DashboardEdge, DashboardGraph, DashboardNode, DashboardSnapshot } from "./data/schema";
import { AppShell } from "./components/cockpit/app-shell";
import { InspectorPanel } from "./components/cockpit/inspector-panel";
import { MetricCard } from "./components/cockpit/metric-card";
import { Badge, StatusBadge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardHeader } from "./components/ui/card";
import { DataTable, type DataTableColumn } from "./components/ui/data-table";
import { Input } from "./components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "./components/ui/tabs";

type View = "cockpit" | "projects" | "graph" | "memory" | "learning" | "risk" | "reviews" | "replay" | "raw";
type GraphFilter = "all" | "selected" | "risk" | "memory" | "reviews" | "tests";
type GraphMode = "decision" | "full";
type ProjectRow = NonNullable<DashboardSnapshot["project_index"]["projects"]>[number];
type LearningSessionRow = NonNullable<DashboardSnapshot["learning_prep"]["sessions"]>[number];
type TaskMapRow = DashboardSnapshot["task_map"][number];
type ReviewRunRow = DashboardSnapshot["review_runs"][number];

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
    <AppShell
      navItems={views}
      activeView={view}
      onViewChange={setView}
      topbar={<TopBar snapshot={payload.snapshot} query={query} onQueryChange={setQuery} />}
      inspector={<Inspector selected={selected} onCopy={copyText} copyMessage={copyMessage} />}
    >
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
    </AppShell>
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
        <Input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search paths, memory, tests" />
      </label>
      <StatusBadge status={snapshot.context.status} />
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
        <Button variant="primary" className="primary-action" onClick={() => onOpenGraph()}>
          <Network size={17} aria-hidden="true" />
          Open graph
        </Button>
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
            <Button variant="primary" className="primary-action" onClick={() => onCopy(decision.command, "Next action")}>
              <Copy size={16} aria-hidden="true" />
              Copy command
            </Button>
          ) : null}
          <Button variant="secondary" className="secondary-action" onClick={() => onOpenGraph(decision.filter)}>
            <Network size={16} aria-hidden="true" />
            Show path
          </Button>
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
                <Badge tone="risk">High</Badge>
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
                <Badge tone={node.stale ? "warn" : "memory"}>{node.stale ? "stale" : node.type}</Badge>
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
  const [graphMode, setGraphMode] = useState<GraphMode>("decision");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const flow = useMemo(() => toFlowGraph(graph, query, filter, selectedId, graphMode), [graph, query, filter, selectedId, graphMode]);
  const { nodes, edges } = flow;
  const [flowNodes, setFlowNodes] = useState<Node[]>(nodes);
  const [flowEdges, setFlowEdges] = useState<Edge[]>(edges);
  const handleClick: NodeMouseHandler = (_event, node) => onSelect(node.id);
  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    setFlowNodes((current) => applyNodeChanges(changes, current));
  }, []);
  const filterItems: Array<{ id: GraphFilter; label: string }> = [
    { id: "all", label: "All" },
    { id: "selected", label: "Selected" },
    { id: "risk", label: "Risk" },
    { id: "memory", label: "Memory" },
    { id: "reviews", label: "Reviews" },
    { id: "tests", label: "Tests" }
  ];

  useEffect(() => {
    setFlowNodes((current) => {
      const currentPositions = new Map(current.map((node) => [node.id, node.position]));
      return nodes.map((node) => ({
        ...node,
        position: currentPositions.get(node.id) || node.position
      }));
    });
    setFlowEdges(edges);
  }, [nodes, edges]);

  return (
    <div className={`graph-shell ${isFullscreen ? "fullscreen" : ""}`}>
      <div className="graph-toolbar">
        <span><CircleDot size={14} aria-hidden="true" /> {graph.summary.node_count} nodes</span>
        <span>{graph.summary.edge_count} edges</span>
        <Badge tone={flow.canvasCurated ? "warn" : "neutral"}>
          {graphMode === "decision" ? "Decision map" : "Full map"} · {nodes.length} of {flow.matchedNodeCount}
        </Badge>
        {graph.summary.truncated ? <Badge tone="warn">Truncated</Badge> : null}
        <div className="graph-mode-toggle" aria-label="Graph map mode">
          <Button variant={graphMode === "decision" ? "secondary" : "ghost"} size="sm" onClick={() => setGraphMode("decision")}>
            Decision
          </Button>
          <Button variant={graphMode === "full" ? "secondary" : "ghost"} size="sm" onClick={() => setGraphMode("full")}>
            Full
          </Button>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="graph-fullscreen-button"
          aria-pressed={isFullscreen}
          onClick={() => setIsFullscreen((value) => !value)}
        >
          {isFullscreen ? <Minimize2 size={15} aria-hidden="true" /> : <Maximize2 size={15} aria-hidden="true" />}
          {isFullscreen ? "Exit fullscreen" : "Fullscreen"}
        </Button>
        <Tabs value={filter} onValueChange={(value) => onFilterChange(value as GraphFilter)}>
          <TabsList aria-label="Graph filter">
          {filterItems.map((item) => (
            <TabsTrigger
              key={item.id}
              value={item.id}
            >
              {item.label}
            </TabsTrigger>
          ))}
          </TabsList>
        </Tabs>
        <div className="graph-legend" aria-label="Graph legend">
          <span><i className="legend-dot selected" />Selected</span>
          <span><i className="legend-dot risk" />High risk</span>
          <span><i className="legend-dot memory" />Memory</span>
          <span><i className="legend-dot review" />Review</span>
          <span><i className="legend-dot ast" />AST</span>
          <span><i className="legend-dot test" />Test</span>
        </div>
      </div>
      {flowNodes.length ? (
        <ReactFlow
          key={`${graphMode}:${filter}:${query}:${flowNodes.length}:${flowEdges.length}:${isFullscreen ? "fullscreen" : "inline"}`}
          nodes={flowNodes}
          edges={flowEdges}
          fitView
          fitViewOptions={{ padding: 0.18, minZoom: 0.35, maxZoom: 1.05 }}
          minZoom={0.25}
          maxZoom={1.35}
          onNodeClick={handleClick}
          onNodesChange={handleNodesChange}
          nodesDraggable
          nodesConnectable={false}
          panOnDrag={[2]}
        >
          <Background />
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
  const columns = useMemo<Array<DataTableColumn<ProjectRow>>>(
    () => [
      {
        id: "project",
        header: "Project",
        cell: ({ row }) => {
          const project = row.original;
          return (
            <>
              <strong>{project.name}{project.current ? " (current)" : ""}</strong>
              <small>{project.task || project.path}</small>
              <code>{project.path}</code>
            </>
          );
        }
      },
      {
        id: "context",
        header: "Context",
        cell: ({ row }) => {
          const project = row.original;
          return (
            <>
              <Badge tone={riskTone(project.context_status)}>{project.context_status || "unknown"}</Badge>
              {project.branch ? <small>{project.branch} {project.git_sha || ""}</small> : null}
            </>
          );
        }
      },
      {
        id: "tokens",
        header: "Tokens",
        cell: ({ row }) => {
          const project = row.original;
          return (
            <>
              <strong>{project.saving_pct || 0}% saved</strong>
              <small>{formatNumber(project.packed_tokens || 0)} / {formatNumber(project.raw_tokens || 0)} tokens</small>
            </>
          );
        }
      },
      {
        id: "signals",
        header: "Signals",
        cell: ({ row }) => {
          const project = row.original;
          return (
            <>
              <small>{project.selected_files_count || 0} files</small>
              <small>{project.review_runs_count || 0} reviews</small>
              <small>{project.memory_count || 0} memories</small>
              <small>{project.weak_spots_count || 0} learning spots</small>
            </>
          );
        }
      },
      {
        id: "commands",
        header: "Commands",
        cell: ({ row }) => {
          const project = row.original;
          return (
            <div className="stack-sm">
              {project.open_command ? <CopyCommand value={project.open_command} label={`open ${project.name}`} onCopy={onCopy} /> : null}
              {project.refresh_command ? <CopyCommand value={project.refresh_command} label={`refresh ${project.name}`} onCopy={onCopy} /> : null}
            </div>
          );
        }
      }
    ],
    [onCopy]
  );

  return (
    <div className="view-stack">
      <SectionTitle title="Projects" subtitle="AgentPack-associated local projects, context health, token savings, and developer-productivity signals." />
      <div className="metric-grid">
        <Metric label="Projects" value={index.project_count || 0} tone="neutral" />
        <Metric label="Stale" value={index.stale_count || 0} tone={index.stale_count ? "warn" : "good"} />
        <Metric label="Saved tokens" value={formatNumber(index.estimated_saved_tokens || 0)} tone="good" />
        <Metric label="Avg savings" value={`${index.average_saving_pct || 0}%`} tone="memory" />
      </div>
      <DataTable data={projects} columns={columns} empty="No AgentPack projects found near this checkout." getRowKey={(project) => project.path} />
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
                <Badge tone="neutral">{spot.count || 0}</Badge>
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
  const columns = useMemo<Array<DataTableColumn<LearningSessionRow>>>(
    () => [
      {
        id: "session",
        header: "Session",
        cell: ({ row }) => {
          const session = row.original;
          return (
            <>
              <strong>{session.topic || session.mode || "Learning session"}</strong>
              <small>{session.task}</small>
              {session.request ? <code>{session.request}</code> : null}
            </>
          );
        }
      },
      {
        id: "question",
        header: "Question",
        cell: ({ row }) => row.original.question || "No question recorded."
      },
      {
        id: "evidence",
        header: "Evidence",
        cell: ({ row }) => {
          const session = row.original;
          return (
            <>
              {(session.concepts || []).slice(0, 4).map((concept) => <Badge key={concept} tone="memory">{concept}</Badge>)}
              {(session.evidence_files || []).slice(0, 3).map((path) => <code key={path}>{path}</code>)}
            </>
          );
        }
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => {
          const session = row.original;
          return (
            <>
              <Badge tone={learningStatusTone(session.status)}>{session.status || "queued"}</Badge>
              {typeof session.score === "number" ? <small>{session.score}%</small> : null}
            </>
          );
        }
      }
    ],
    []
  );

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
                <Badge tone="memory">concept</Badge>
              </div>
            ))}
            {!(prep.top_concepts || []).length ? <p className="empty">No learning concepts found yet.</p> : null}
          </div>
        </Panel>
      </div>
      <DataTable
        data={sessions}
        columns={columns}
        empty="No learning prep sessions found."
        getRowKey={(session, index) => `${session.created_at || "session"}:${session.question || ""}:${index}`}
      />
    </div>
  );
}

function RiskTestsView({ snapshot, onSelect }: { snapshot: DashboardSnapshot; onSelect: (id: string) => void }) {
  const columns = useMemo<Array<DataTableColumn<TaskMapRow>>>(
    () => [
      {
        id: "path",
        header: "Path",
        cell: ({ row }) => (
          <Button variant="link" onClick={() => onSelect(`file:${row.original.path}`)}>
            <code>{row.original.path}</code>
          </Button>
        )
      },
      {
        id: "risk",
        header: "Risk",
        cell: ({ row }) => <Badge tone={riskTone(row.original.risk_level)}>{row.original.risk_level || "low"}</Badge>
      },
      {
        id: "why",
        header: "Why",
        cell: ({ row }) => (row.original.why_selected || row.original.risk_reasons || []).slice(0, 2).join("; ")
      },
      {
        id: "tests",
        header: "Tests",
        cell: ({ row }) => (row.original.tests_to_run || []).slice(0, 3).map((test) => <code key={test}>{test}</code>)
      },
      {
        id: "may-break",
        header: "May break",
        cell: ({ row }) => (row.original.may_break || []).slice(0, 2).join("; ")
      }
    ],
    [onSelect]
  );

  return (
    <div className="view-stack">
      <SectionTitle title="Risk & Tests" subtitle="Task-map risk, breakage hints, and validation commands." />
      <DataTable
        data={snapshot.task_map}
        columns={columns}
        empty="No task map found."
        getRowKey={(item) => `${item.kind}:${item.path}`}
      />
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
  const columns = useMemo<Array<DataTableColumn<ReviewRunRow>>>(
    () => [
      {
        id: "run",
        header: "Run",
        cell: ({ row }) => {
          const run = row.original;
          return (
            <>
              <strong>{run.run_id}</strong>
              <small>{run.generated_at || run.branch_prefix || ""}</small>
            </>
          );
        }
      },
      {
        id: "target",
        header: "Target",
        cell: ({ row }) => row.original.target_number ? `PR #${row.original.target_number}` : row.original.branch_prefix || "local"
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => <Badge tone={reviewStatusTone(row.original.status)}>{row.original.status || "prepared"}</Badge>
      },
      {
        id: "files",
        header: "Files",
        cell: ({ row }) => row.original.changed_files_count || 0
      },
      {
        id: "artifacts",
        header: "Artifacts",
        cell: ({ row }) => {
          const run = row.original;
          return (
            <>
              {run.understanding_path ? <code>{run.understanding_path}</code> : null}
              {run.findings_path ? <code>{run.findings_path}</code> : null}
            </>
          );
        }
      },
      {
        id: "commands",
        header: "Commands",
        cell: ({ row }) => {
          const run = row.original;
          return (
            <div className="stack-sm">
              {run.resume_command ? <CopyCommand value={run.resume_command} label="resume" onCopy={onCopy} /> : null}
              {run.check_command ? <CopyCommand value={run.check_command} label="check" onCopy={onCopy} /> : null}
              {run.post_command ? <CopyCommand value={run.post_command} label="post" onCopy={onCopy} /> : null}
            </div>
          );
        }
      }
    ],
    [onCopy]
  );

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
      <DataTable data={runs} columns={columns} empty="No review runs found." getRowKey={(run) => run.run_id} />
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
  const badges = selected
    ? [
        "type" in selected ? { label: selected.type, tone: selected.type } : null,
        "risk" in selected && selected.risk ? { label: selected.risk, tone: riskTone(selected.risk) } : null
      ].filter(Boolean) as Array<{ label: string; tone?: string }>
    : [];

  return (
    <InspectorPanel title={selected?.label || selected?.id || "Nothing selected"} badges={badges}>
      {!selected ? (
        <p className="empty">Select a node or edge to inspect evidence and actions.</p>
      ) : (
        <div className="stack">
          {"summary" in selected && selected.summary ? <p>{selected.summary}</p> : null}
          {"reason" in selected && selected.reason ? <p>{selected.reason}</p> : null}
          {"path" in selected && selected.path ? <code>{selected.path}</code> : null}
          <InspectorList title="Evidence" items={selected.evidence || []} />
          <ActionList actions={selected.actions || []} onCopy={onCopy} />
          <p className="sr-only" aria-live="polite">{copyMessage}</p>
        </div>
      )}
    </InspectorPanel>
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
    <Card className="panel">
      <CardHeader title={title} icon={Icon} />
      {children}
    </Card>
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
  return <MetricCard label={label} value={value} tone={tone} />;
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
          <Badge tone={riskTone(item.tone)}>{item.tone || "view"}</Badge>
        </button>
      ))}
    </div>
  );
}

function ErrorState({ message, onLoadSample }: { message: string; onLoadSample: () => void }) {
  return (
    <div className="center-state">
      <AlertTriangle size={28} aria-hidden="true" />
      <h1>Dashboard failed to load</h1>
      <p>{message}</p>
      <Button variant="secondary" onClick={onLoadSample}>
        Show sample cockpit
      </Button>
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
      <Button variant="secondary" onClick={onLoadSample}>
        Show sample decision
      </Button>
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
    <Button variant="icon" aria-label={`Copy ${label}`} onClick={() => onCopy(value, label)}>
      <Copy size={15} aria-hidden="true" />
    </Button>
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
    <Button variant="link" onClick={() => onCopy(value, label)}>
      <code>{value}</code>
    </Button>
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

type FlowGraphResult = {
  nodes: Node[];
  edges: Edge[];
  matchedNodeCount: number;
  canvasCurated: boolean;
};

const CANVAS_LIMITS = {
  tasks: 3,
  reviews: 4,
  memory: 4,
  memoryTargets: 8,
  files: 8,
  symbols: 10,
  tests: 3,
  actions: 3,
  taskFileEdges: 4
};

function toFlowGraph(graph: DashboardGraph, query: string, filter: GraphFilter, selectedId: string, mode: GraphMode): FlowGraphResult {
  const lower = query.trim().toLowerCase();
  const memoryTargets = new Set(graph.edges.filter((edge) => edge.type === "memory_influenced").map((edge) => edge.target));
  const matchedNodes = graph.nodes.filter((node) => {
    if (!matchesGraphFilter(node, filter, memoryTargets)) return false;
    if (!lower) return true;
    return [node.label, node.path, node.summary, node.type].some((value) => String(value || "").toLowerCase().includes(lower));
  });
  const visibleNodes = mode === "full" ? matchedNodes : curateCanvasNodes(graph, matchedNodes, filter, memoryTargets, selectedId);
  const visible = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = mode === "full"
    ? graph.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target))
    : curateCanvasEdges(graph.edges, visibleNodes, filter);
  const positions = layoutGraph(visibleNodes, visibleEdges);

  const nodes = visibleNodes
    .map((node, index) => ({
      id: node.id,
      position: positions.get(node.id) || positionFor(index, node.type),
      data: { label: nodeLabel(node) },
      className: `flow-node ${node.type} ${node.selected ? "selected" : ""} ${node.stale ? "stale" : ""} ${node.id === selectedId ? "active" : ""}`,
      type: "default"
    }));
  const edges = visibleEdges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edgeLabel(edge),
      className: `flow-edge ${edge.type}`,
      animated: edge.type === "memory_influenced"
    }));
  return {
    nodes,
    edges,
    matchedNodeCount: matchedNodes.length,
    canvasCurated: mode === "decision" && visibleNodes.length < matchedNodes.length
  };
}

function matchesGraphFilter(node: DashboardNode, filter: GraphFilter, memoryTargets: Set<string>) {
  if (filter === "selected") return node.type === "task" || ((node.type === "file" || node.type === "symbol") && Boolean(node.selected));
  if (filter === "risk") return node.type === "task" || node.risk === "high" || node.risk === "medium" || node.type === "action";
  if (filter === "memory") return node.type === "task" || node.type === "episode" || node.type === "procedure" || memoryTargets.has(node.id);
  if (filter === "reviews") return node.type === "task" || node.type === "review";
  if (filter === "tests") return node.type === "task" || node.type === "test" || node.actions?.some((action) => action.command?.includes("pytest"));
  return true;
}

function curateCanvasNodes(
  graph: DashboardGraph,
  nodes: DashboardNode[],
  filter: GraphFilter,
  memoryTargets: Set<string>,
  selectedId: string
): DashboardNode[] {
  const edges = graph.edges;
  const focused = nodes.find((node) => node.id === selectedId);
  const focus = focused ? [focused] : [];
  const tasks = takeRanked(nodes.filter((node) => node.type === "task"), edges, CANVAS_LIMITS.tasks);
  const reviews = takeRanked(nodes.filter((node) => node.type === "review"), edges, CANVAS_LIMITS.reviews);
  const memory = takeRanked(
    nodes.filter((node) => node.type === "episode" || node.type === "procedure"),
    edges,
    CANVAS_LIMITS.memory
  );
  const files = takeRanked(nodes.filter((node) => node.type === "file"), edges, CANVAS_LIMITS.files);
  const visibleFilePaths = new Set(files.map((node) => node.path).filter(Boolean));
  const symbolsForVisibleFiles = nodes.filter(
    (node) => node.type === "symbol" && visibleFilePaths.has(String(node.metadata?.file || node.path || ""))
  );
  const symbols = takeRanked(symbolsForVisibleFiles.length ? symbolsForVisibleFiles : nodes.filter((node) => node.type === "symbol"), edges, CANVAS_LIMITS.symbols);
  const tests = takeRanked(nodes.filter((node) => node.type === "test"), edges, CANVAS_LIMITS.tests);
  const actions = takeRanked(nodes.filter((node) => node.type === "action"), edges, CANVAS_LIMITS.actions);

  if (filter === "reviews") {
    return uniqueNodes([...focus, ...tasks, ...reviews]);
  }
  if (filter === "memory") {
    const targets = takeRanked(
      nodes.filter((node) => memoryTargets.has(node.id) && node.type !== "episode" && node.type !== "procedure"),
      edges,
      CANVAS_LIMITS.memoryTargets
    );
    return uniqueNodes([...focus, ...tasks, ...memory, ...targets]);
  }
  if (filter === "tests") {
    const testTargets = connectedNodes(nodes, edges, new Set(tests.map((node) => node.id)));
    return uniqueNodes([...focus, ...tasks, ...tests, ...takeRanked(testTargets, edges, CANVAS_LIMITS.files), ...actions]);
  }
  if (filter === "risk") {
    return uniqueNodes([...focus, ...tasks, ...files, ...tests, ...actions]);
  }
  if (filter === "selected") {
    return uniqueNodes([...focus, ...tasks, ...files, ...symbols, ...tests]);
  }
  return uniqueNodes([...focus, ...tasks, ...reviews, ...memory, ...files, ...symbols, ...tests, ...actions]);
}

function curateCanvasEdges(edges: DashboardEdge[], nodes: DashboardNode[], filter: GraphFilter): DashboardEdge[] {
  const visible = new Set(nodes.map((node) => node.id));
  const typeById = new Map(nodes.map((node) => [node.id, node.type]));
  const rankById = new Map(nodes.map((node, index) => [node.id, index]));
  const candidates = edges
    .filter((edge) => visible.has(edge.source) && visible.has(edge.target))
    .sort((left, right) => (rankById.get(left.source) || 0) - (rankById.get(right.source) || 0));
  const taskFileEdges = candidates.filter((edge) => typeById.get(edge.source) === "task" && typeById.get(edge.target) === "file");
  const allowedTaskFileTargets = new Set(taskFileEdges.slice(0, CANVAS_LIMITS.taskFileEdges).map((edge) => edge.target));

  return candidates.filter((edge) => {
    const sourceType = typeById.get(edge.source);
    const targetType = typeById.get(edge.target);
    if (sourceType === "task" && targetType === "file" && !allowedTaskFileTargets.has(edge.target)) {
      return false;
    }
    if (filter === "all" && edge.type === "contains" && sourceType === "task") {
      return allowedTaskFileTargets.has(edge.target);
    }
    return true;
  });
}

function takeRanked(nodes: DashboardNode[], edges: DashboardEdge[], limit: number) {
  const degree = connectionDegree(edges);
  return [...nodes]
    .sort((left, right) => nodePriority(right, degree) - nodePriority(left, degree) || nodeSortLabel(left).localeCompare(nodeSortLabel(right)))
    .slice(0, limit);
}

function connectedNodes(nodes: DashboardNode[], edges: DashboardEdge[], ids: Set<string>) {
  const related = new Set<string>();
  for (const edge of edges) {
    if (ids.has(edge.source)) related.add(edge.target);
    if (ids.has(edge.target)) related.add(edge.source);
  }
  return nodes.filter((node) => related.has(node.id));
}

function connectionDegree(edges: DashboardEdge[]) {
  const degree = new Map<string, number>();
  for (const edge of edges) {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  }
  return degree;
}

function nodePriority(node: DashboardNode, degree: Map<string, number>) {
  const risk = node.risk === "high" ? 70 : node.risk === "medium" ? 45 : node.risk === "low" ? 12 : 0;
  const family =
    node.type === "task" ? 120 :
    node.type === "review" ? 95 :
    node.type === "episode" || node.type === "procedure" ? 90 :
    node.type === "file" ? 75 :
    node.type === "symbol" ? 45 :
    node.type === "test" ? 55 :
    node.type === "action" ? 50 :
    0;
  return family + risk + (node.selected ? 80 : 0) + (Number(node.score) || 0) * 10 + (degree.get(node.id) || 0) * 2;
}

function uniqueNodes(nodes: DashboardNode[]) {
  const seen = new Set<string>();
  return nodes.filter((node) => {
    if (seen.has(node.id)) return false;
    seen.add(node.id);
    return true;
  });
}

function edgeLabel(edge: DashboardEdge) {
  if (edge.type === "contains" || edge.type === "selected_because") return undefined;
  return edge.label || edge.type;
}

function nodeLabel(node: DashboardNode) {
  const family = nodeFamily(node);
  const detail = node.type === "symbol"
    ? String(node.metadata?.kind || "symbol")
    : node.status || node.risk || node.type;
  return (
    <div className="node-label">
      <small className={`node-family ${family.tone}`}>{family.label}</small>
      <span>{node.label}</span>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

function layoutGraph(nodes: DashboardNode[], edges: DashboardEdge[]): Map<string, { x: number; y: number }> {
  const byType = new Map<string, DashboardNode[]>();
  for (const node of nodes) {
    const key = node.type === "episode" || node.type === "procedure" ? "memory" : node.type;
    byType.set(key, [...(byType.get(key) || []), node]);
  }
  const fileOrder = orderByConnections(byType.get("file") || [], edges);
  const symbolOrder = orderSymbolsByFile(byType.get("symbol") || [], fileOrder);
  const positions = new Map<string, { x: number; y: number }>();
  placeLane(positions, byType.get("task") || [], 40, 260, 116, 180, 3);
  placeLane(positions, byType.get("review") || [], 260, 64, 112, 180, 3);
  placeLane(positions, byType.get("memory") || [], 260, 260, 112, 180, 4);
  placeLane(positions, fileOrder, 480, 72, 90, 180, 6);
  placeLane(positions, symbolOrder, 840, 72, 74, 180, 8);
  placeLane(positions, byType.get("test") || [], 1080, 96, 108, 180, 4);
  placeLane(positions, byType.get("action") || [], 1080, 390, 108, 180, 4);
  return positions;
}

function placeLane(
  positions: Map<string, { x: number; y: number }>,
  nodes: DashboardNode[],
  x: number,
  yStart: number,
  yStep: number,
  xStep: number,
  maxRows: number
) {
  nodes.forEach((node, index) => {
    const column = Math.floor(index / maxRows);
    const row = index % maxRows;
    positions.set(node.id, { x: x + column * xStep, y: yStart + row * yStep });
  });
}

function orderByConnections(nodes: DashboardNode[], edges: DashboardEdge[]) {
  const score = new Map(edges.flatMap((edge) => [[edge.source, 0], [edge.target, 0]]));
  for (const edge of edges) {
    score.set(edge.source, (score.get(edge.source) || 0) + 1);
    score.set(edge.target, (score.get(edge.target) || 0) + 1);
  }
  return [...nodes].sort((left, right) => {
    const selected = Number(Boolean(right.selected)) - Number(Boolean(left.selected));
    if (selected) return selected;
    return (score.get(right.id) || 0) - (score.get(left.id) || 0) || (left.path || left.label).localeCompare(right.path || right.label);
  });
}

function orderSymbolsByFile(symbols: DashboardNode[], files: DashboardNode[]) {
  const fileRank = new Map(files.map((file, index) => [file.path, index]));
  return [...symbols].sort((left, right) => {
    const leftRank = fileRank.get(String(left.metadata?.file || left.path || "")) ?? 999;
    const rightRank = fileRank.get(String(right.metadata?.file || right.path || "")) ?? 999;
    return leftRank - rightRank || (left.label || left.id).localeCompare(right.label || right.id);
  });
}

function positionFor(index: number, type: string) {
  const lane = type === "task" ? 0 : type === "episode" || type === "procedure" ? -1 : type === "review" ? 1 : type === "test" || type === "action" ? 2 : 0;
  return {
    x: 120 + (index % 6) * 190,
    y: 120 + lane * 140 + Math.floor(index / 6) * 170
  };
}

function nodeSortLabel(node: DashboardNode) {
  return node.path || node.label || node.id;
}

function nodeFamily(node: DashboardNode): { label: string; tone: string } {
  if (node.type === "symbol") return { label: "AST", tone: "ast" };
  if (node.type === "episode") return { label: "Episode", tone: "memory" };
  if (node.type === "procedure") return { label: "Procedure", tone: "memory" };
  if (node.type === "review") return { label: "Review", tone: "review" };
  if (node.type === "test") return { label: "Test", tone: "test" };
  if (node.type === "file") return { label: node.selected ? "Selected file" : "Candidate file", tone: node.selected ? "selected" : "file" };
  if (node.type === "action") return { label: "Action", tone: "action" };
  return { label: "Task", tone: "task" };
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
