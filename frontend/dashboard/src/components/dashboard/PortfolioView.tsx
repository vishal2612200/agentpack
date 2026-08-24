import { AlertTriangle, Clock3, GitBranch, Network, Search, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import type { PortfolioPayload } from "../../data/loadDashboard";

type Project = PortfolioPayload["projects"][number];

export function PortfolioView({ portfolio, onOpenProject, onRefreshGithub }: { portfolio: PortfolioPayload; onOpenProject: (project: Project) => void; onRefreshGithub: () => Promise<void> }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [lens, setLens] = useState<"topology" | "work" | "activity">("topology");
  const [refreshing, setRefreshing] = useState(false);
  const projects = useMemo(() => portfolio.projects.filter((project) => {
    const text = `${project.name} ${project.purpose} ${project.stage} ${project.capabilities.join(" ")}`.toLowerCase();
    const matchesQuery = !query.trim() || text.includes(query.trim().toLowerCase());
    const matchesFilter = filter === "all" || (filter === "stale" && project.stale) || (filter === "attention" && project.risks.some((risk) => risk.status === "open"));
    return matchesQuery && matchesFilter;
  }), [filter, portfolio.projects, query]);

  return <div className="view-stack portfolio-view">
    <header className="view-heading">
      <div><span className="eyebrow">Engineering Atlas</span><h1>Portfolio map</h1><p>Projects, relationships, work, and evidence in one local view.</p></div>
      <div className="portfolio-heading-actions"><span className="badge neutral">{portfolio.projects.length} projects · {portfolio.relations.length} relations</span><button type="button" className="secondary-button" disabled={refreshing} onClick={() => { setRefreshing(true); void onRefreshGithub().catch(() => undefined).finally(() => setRefreshing(false)); }}>{refreshing ? "Refreshing..." : "Refresh GitHub evidence"}</button></div>
    </header>
    <div className="portfolio-toolbar">
      <div className="lens-tabs" role="tablist" aria-label="Portfolio lens">{(["topology", "work", "activity"] as const).map((value) => <button key={value} type="button" role="tab" aria-selected={lens === value} className={lens === value ? "active" : ""} onClick={() => setLens(value)}>{value[0].toUpperCase() + value.slice(1)}</button>)}</div>
      <label className="search-field"><Search size={16} aria-hidden="true" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search projects, capabilities, risks..." aria-label="Search portfolio" /></label>
      <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter portfolio"><option value="all">All projects</option><option value="attention">Needs attention</option><option value="stale">Stale or unavailable</option></select>
    </div>
    {portfolio.partial ? <div className="project-view-state error" role="status"><AlertTriangle size={17} /> <span>Portfolio has partial data. Cached project evidence remains available.</span></div> : null}
    <div className="portfolio-layout">
      <section className="portfolio-project-grid" aria-label="Projects">
        {lens !== "activity" ? projects.map((project) => <ProjectCard key={project.project_id} project={project} onOpen={() => onOpenProject(project)} showWork={lens === "work"} />) : <ActivityList activity={portfolio.recent_activity} />}
        {!projects.length ? <div className="empty-state">No projects match filters.</div> : null}
      </section>
      <aside className="portfolio-rail" aria-label="Portfolio attention">
        <div className="panel"><div className="panel-heading"><ShieldAlert size={16} /><h2>Attention</h2></div>{portfolio.attention.slice(0, 10).map((item, index) => <div className="portfolio-attention" key={`${String(item.project_id)}:${index}`}><strong>{String(item.title || item.kind || "Review")}</strong><span>{String(item.summary || "")}</span><code>{String(item.project_id || "")}</code></div>)}{!portfolio.attention.length ? <p className="empty">No cross-project attention items.</p> : null}</div>
        {lens !== "activity" ? <div className="panel"><div className="panel-heading"><Clock3 size={16} /><h2>Recent activity</h2></div><ActivityList activity={portfolio.recent_activity.slice(0, 8)} /></div> : null}
      </aside>
    </div>
    {lens !== "work" ? <section className="panel portfolio-relations"><div className="panel-heading"><Network size={16} /><h2>Declared and inferred relationships</h2></div>{portfolio.relations.slice(0, 20).map((relation) => <div className="relation-row" key={relation.relation_id}><GitBranch size={15} /><strong>{relation.source_project_id}</strong><span>{relation.type}</span><strong>{relation.target_project_id || relation.target_key || "unresolved"}</strong><span className="badge neutral">{relation.declared ? "declared" : "inferred"} · {relation.confidence.toFixed(1)}</span></div>)}{!portfolio.relations.length ? <p className="empty">No explainable cross-project relationships found.</p> : null}</section> : null}
  </div>;
}

function ActivityList({ activity }: { activity: PortfolioPayload["recent_activity"] }) { return <>{activity.map((item, index) => <div className="portfolio-activity" key={`${item.project_id}:${item.occurred_at}:${index}`}><strong>{item.title}</strong><span>{item.project_id} · {item.kind}</span></div>)}{!activity.length ? <p className="empty">No activity recorded.</p> : null}</>; }

function ProjectCard({ project, onOpen, showWork }: { project: Project; onOpen: () => void; showWork?: boolean }) {
  const health = project.health.dimensions.slice(0, 6);
  return <button type="button" className="portfolio-card" onClick={onOpen} aria-label={`Open ${project.name}`}>
    <div className="portfolio-card-heading"><div><span className="eyebrow">{project.stage || "unclassified"}</span><h2>{project.name}</h2></div><span className={`status-dot ${project.unavailable ? "blocked" : project.stale ? "stale" : "healthy"}`} title={project.unavailable ? "unavailable" : project.stale ? "stale" : "fresh"} /></div>
    <p>{project.purpose || "Purpose not declared."}</p>
    <div className="portfolio-health">{health.map((item) => <span key={item.dimension} className={`badge ${item.status}`}>{item.dimension}: {item.status}</span>)}</div>
    <div className="portfolio-card-meta"><code><GitBranch size={13} /> {project.branch || "detached"}</code><code>{project.git_sha || "no SHA"}</code><span>{project.workspaces.length} workspace{project.workspaces.length === 1 ? "" : "s"}</span></div>
    {showWork ? <div className="portfolio-work">{project.task_count} active tasks · {project.agent_count} agents</div> : null}
    {project.risks[0] ? <div className="portfolio-risk"><AlertTriangle size={14} /> {project.risks[0].title}</div> : null}
    {project.stale ? <span className="last-known">Last known · {project.cache_age_seconds ? `${Math.round(project.cache_age_seconds / 86400)}d old` : "no cache"}</span> : null}
  </button>;
}
