import { Activity, CheckCircle2, Code2, GitCommitHorizontal, GraduationCap, ListFilter } from "lucide-react";
import { useEffect, useState } from "react";
import { loadProjectTimeline } from "../../../data/loadDashboard";
import type { ProjectOverview, ProjectTimelineEvent } from "../../../data/schema";
import { EvidenceList, ProjectViewState, WorkspaceFilter, formatProjectDate } from "./project-shared";

const kinds = ["", "project", "task", "check", "review", "learning", "commit", "dashboard"];

export function ProjectActivityView({
  overview,
  workspace,
  loading,
  onWorkspaceChange
}: {
  overview: ProjectOverview;
  workspace: string;
  loading: boolean;
  onWorkspaceChange: (value: string) => void;
}) {
  const [kind, setKind] = useState("");
  const [rows, setRows] = useState<ProjectTimelineEvent[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const load = () => {
    setState("loading");
    setError("");
    loadProjectTimeline(workspace, kind, 100)
      .then((items) => { setRows(items); setState("ready"); })
      .catch((caught: unknown) => { setError(caught instanceof Error ? caught.message : "Activity could not be loaded."); setState("error"); });
  };
  useEffect(load, [workspace, kind, overview.generated_at]);

  return (
    <div className="view-stack project-product-view" data-testid="project-activity-view">
      <section className="project-view-heading">
        <div><span className="eyebrow">Activity</span><h1>One project timeline</h1><p>Project updates, work, checks, learning, agents, dashboard actions, commits, and tags in timestamp order.</p></div>
        <WorkspaceFilter overview={overview} value={workspace} onChange={onWorkspaceChange} disabled={loading || state === "loading"} />
      </section>
      <div className="project-activity-toolbar">
        <ListFilter size={16} aria-hidden="true" />
        <label><span>Kind</span><select value={kind} onChange={(event) => setKind(event.target.value)}>{kinds.map((item) => <option key={item || "all"} value={item}>{item || "All activity"}</option>)}</select></label>
        <span>{rows.length} events</span>
      </div>
      {state === "loading" ? <ProjectViewState status="loading" message="Loading project activity..." /> : null}
      {state === "error" ? <ProjectViewState status="error" message={error} onRetry={load} /> : null}
      {state === "ready" && !rows.length ? <ProjectViewState status="empty" message="No activity matches this workspace and kind." /> : null}
      {state === "ready" && rows.length ? (
        <ol className="project-activity-list">
          {rows.map((item) => {
            const Icon = icon(item.kind);
            return (
              <li key={item.event_id}>
                <span className={`project-activity-icon ${item.kind}`}><Icon size={15} /></span>
                <div className="project-activity-content">
                  <div><strong>{item.title}</strong><span className="badge neutral">{item.kind}</span></div>
                  {item.summary ? <p>{item.summary}</p> : null}
                  <small>{formatProjectDate(item.updated_at)}{item.actor ? ` · ${item.actor}` : ""}{item.branch ? ` · ${item.branch}` : ""}{item.git_sha ? ` · ${item.git_sha}` : ""}</small>
                  {item.tags.length ? <div className="inline-actions">{item.tags.map((tag) => <span className="badge good" key={tag}>{tag}</span>)}</div> : null}
                  <details><summary>Evidence ({item.evidence.length})</summary><EvidenceList evidence={item.evidence} /></details>
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}

function icon(kind: string) {
  if (kind === "commit") return GitCommitHorizontal;
  if (kind === "check" || kind === "review") return CheckCircle2;
  if (kind === "learning") return GraduationCap;
  if (kind === "project" || kind === "task") return Activity;
  return Code2;
}
