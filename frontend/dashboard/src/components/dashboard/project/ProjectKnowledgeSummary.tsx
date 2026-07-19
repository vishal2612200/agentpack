import { BookOpenCheck, GitMerge, ScrollText } from "lucide-react";
import type { DashboardGraph, ProjectOverview } from "../../../data/schema";
import { EvidenceList, formatProjectDate } from "./project-shared";

export function ProjectKnowledgeSummary({ overview, graph }: { overview: ProjectOverview; graph: DashboardGraph }) {
  const decisions = overview.decisions.filter((item) => item.status === "accepted");
  const procedures = graph.nodes.filter((item) => item.type === "procedure");
  return (
    <section className="project-knowledge-summary" data-testid="project-knowledge-summary">
      <div className="project-section-heading"><div><span className="eyebrow">Project knowledge</span><h1>Decisions, procedures, and mastery</h1></div><span>{decisions.length + procedures.length} durable records</span></div>
      <div className="project-two-column">
        <div className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Accepted</span><h2>Decisions</h2></div><GitMerge size={17} /></div>
          <div className="project-knowledge-list">
            {decisions.map((decision) => (
              <article key={decision.decision_id}><BookOpenCheck size={15} /><div><strong>{decision.title}</strong><p>{decision.decision || decision.context || "Decision details were not recorded."}</p><small>{decision.owner || "No owner"} · {formatProjectDate(decision.updated_at)}</small><EvidenceList evidence={decision.evidence} /></div></article>
            ))}
            {!decisions.length ? <p className="empty">No accepted project decisions recorded.</p> : null}
          </div>
        </div>
        <div className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Reusable</span><h2>Procedures</h2></div><ScrollText size={17} /></div>
          <div className="project-knowledge-list">
            {procedures.slice(0, 12).map((procedure) => <article key={procedure.id}><ScrollText size={15} /><div><strong>{procedure.label}</strong><p>{procedure.summary || "Reusable project procedure."}</p>{procedure.path ? <code>{procedure.path}</code> : null}</div></article>)}
            {!procedures.length ? <p className="empty">No reusable procedures have been observed.</p> : null}
          </div>
        </div>
      </div>
    </section>
  );
}
