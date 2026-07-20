import { Activity, PlayCircle, ShieldAlert, ShieldCheck } from "lucide-react";
import type { ProjectOverview } from "../../../data/schema";
import { EvidenceList, HealthMark, WorkspaceFilter, formatProjectDate, percentage } from "./project-shared";

export function ProjectHealthView({
  overview,
  workspace,
  loading,
  actionsDisabled = false,
  onWorkspaceChange,
  onRunAction,
  onRunCommand
}: {
  overview: ProjectOverview;
  workspace: string;
  loading: boolean;
  actionsDisabled?: boolean;
  onWorkspaceChange: (value: string) => void;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onRunCommand: (command: string) => void;
}) {
  return (
    <div className="view-stack project-product-view" data-testid="project-health-view">
      <section className="project-view-heading">
        <div><span className="eyebrow">Health</span><h1>Evidence, not a composite score</h1><p>Each dimension keeps its own state so missing evidence stays unknown instead of becoming zero.</p></div>
        <WorkspaceFilter overview={overview} value={workspace} onChange={onWorkspaceChange} disabled={loading} />
      </section>
      {overview.read_only ? <p className="project-warning"><ShieldAlert size={15} /> This project is read-only. Checks cannot be started from the dashboard.</p> : null}

      <section className="project-health-matrix" aria-label="Project health dimensions">
        {overview.health.dimensions.map((dimension) => (
          <article key={dimension.dimension} className={`project-health-dimension ${dimension.status}`}>
            <div className="project-health-dimension-heading">
              <HealthMark status={dimension.status} />
              <div><span className="eyebrow">{dimension.dimension}</span><h2>{label(dimension.status)}</h2></div>
              <span>{Math.round(dimension.confidence * 100)}% confidence</span>
            </div>
            <p>{dimension.summary}</p>
            <small>{dimension.updated_at ? `Updated ${formatProjectDate(dimension.updated_at)}` : "No update recorded"}</small>
            <EvidenceList evidence={dimension.evidence} />
          </article>
        ))}
      </section>

      <section className="project-health-coverage">
        <div><ShieldCheck size={18} /><span><strong>Evidence coverage</strong><small>Active entities with an owner, recent status, and concrete evidence</small></span></div>
        <strong>{percentage(overview.metrics.evidence_coverage)}</strong>
        <div className="project-progress"><i style={{ width: `${overview.metrics.evidence_coverage || 0}%` }} /></div>
      </section>

      <div className="project-two-column">
        <section className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Validation</span><h2>Run project checks</h2></div></div>
          <div className="project-check-actions">
            <button type="button" disabled={overview.read_only || actionsDisabled} onClick={() => onRunAction("dev_check")}><PlayCircle size={15} /><span><strong>Development</strong><small>Current code checks</small></span></button>
            <button type="button" disabled={overview.read_only || actionsDisabled} onClick={() => onRunAction("review")}><PlayCircle size={15} /><span><strong>Review</strong><small>Review evidence</small></span></button>
            <button type="button" disabled={overview.read_only || actionsDisabled} onClick={() => onRunCommand("agentpack architecture check")}><PlayCircle size={15} /><span><strong>Architecture</strong><small>Invariant checks</small></span></button>
            <button type="button" disabled={overview.read_only || actionsDisabled} onClick={() => onRunAction("release_check")}><PlayCircle size={15} /><span><strong>Release</strong><small>Release readiness</small></span></button>
          </div>
        </section>
        <section className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Attention</span><h2>Open project signals</h2></div><Activity size={17} /></div>
          <div className="project-record-list">
            {overview.risks.filter((item) => item.status !== "resolved").map((risk) => <div key={risk.risk_id}><HealthMark status={risk.severity === "critical" ? "blocked" : "attention"} /><span><strong>{risk.title}</strong><small>{risk.severity} · {risk.status}</small></span></div>)}
            {overview.decisions.filter((item) => item.status === "proposed").map((decision) => <div key={decision.decision_id}><HealthMark status="unknown" /><span><strong>{decision.title}</strong><small>Decision proposed</small></span></div>)}
            {!overview.metrics.open_risks && !overview.metrics.pending_decisions ? <p className="empty">No open project signals.</p> : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function label(status: string): string {
  return { healthy: "Healthy", attention: "Needs attention", blocked: "Blocked", stale: "Stale", unknown: "Unknown" }[status] || status;
}
