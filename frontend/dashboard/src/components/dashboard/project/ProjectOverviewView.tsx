import { ClipboardCheck, Copy, Download, FilePenLine, Flag, Plus, ShieldAlert, X } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { loadProjectBrief, recordProjectEvent, updateProjectProfile } from "../../../data/loadDashboard";
import type { PresentationMode, ProjectOverview } from "../../../data/schema";
import { EvidenceList, HealthMark, WorkspaceFilter, formatProjectDate, percentage, projectMutationId } from "./project-shared";

export function ProjectOverviewView({
  overview,
  workspace,
  loading,
  mode,
  onWorkspaceChange,
  onOverviewChange
}: {
  overview: ProjectOverview;
  workspace: string;
  loading: boolean;
  mode: PresentationMode;
  onWorkspaceChange: (value: string) => void;
  onOverviewChange: (overview: ProjectOverview) => void;
}) {
  const [profileOpen, setProfileOpen] = useState(false);
  const [recordOpen, setRecordOpen] = useState<"risk" | "decision" | "">("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const health = Object.fromEntries(overview.health.dimensions.map((item) => [item.dimension, item]));
  const activeOutcomes = overview.outcomes.filter((item) => item.status !== "achieved" && item.status !== "paused");
  const nextActions = useMemo(() => {
    const actions = overview.health.dimensions
      .filter((item) => ["blocked", "attention", "stale"].includes(item.status))
      .map((item) => `${item.dimension}: ${item.summary}`);
    actions.push(...overview.risks.filter((item) => item.status !== "resolved").map((item) => `Risk: ${item.title}`));
    actions.push(...overview.decisions.filter((item) => item.status === "proposed").map((item) => `Decision: ${item.title}`));
    return actions.slice(0, 6);
  }, [overview]);

  const exportBrief = async (action: "copy" | "download") => {
    setBusy(true);
    setMessage("");
    try {
      const brief = await loadProjectBrief(mode === "explain" ? "summary" : "engineering");
      if (action === "copy") {
        await navigator.clipboard.writeText(brief.markdown);
        setMessage("Status brief copied.");
      } else {
        const blob = new Blob([brief.markdown], { type: "text/markdown;charset=utf-8" });
        const href = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = href;
        link.download = `${slug(overview.profile.display_name)}-${brief.mode}-status.md`;
        link.click();
        URL.revokeObjectURL(href);
        setMessage("Status brief downloaded.");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Status brief failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="view-stack project-product-view" data-testid="project-overview-view">
      <section className="project-product-header">
        <div>
          <span className="eyebrow">Project</span>
          <h1>{overview.profile.display_name}</h1>
          <p>{overview.profile.purpose || "Purpose has not been declared in shared project configuration."}</p>
          <div className="project-header-meta">
            <span>{overview.profile.stage || "Stage not declared"}</span>
            <span>{overview.profile.owners.join(", ") || "No owner declared"}</span>
            <span>{overview.workspaces.length} accessible worktree{overview.workspaces.length === 1 ? "" : "s"}</span>
          </div>
        </div>
        <div className="project-header-actions">
          <WorkspaceFilter overview={overview} value={workspace} onChange={onWorkspaceChange} disabled={loading} />
          <button type="button" className="secondary-action" onClick={() => setProfileOpen(true)} disabled={overview.read_only}>
            <FilePenLine size={15} aria-hidden="true" /> Edit profile
          </button>
          <button type="button" className="icon-button" onClick={() => void exportBrief("copy")} disabled={busy} title={`Copy ${mode === "explain" ? "Summary" : "Engineering"} brief`}>
            <Copy size={16} aria-hidden="true" />
          </button>
          <button type="button" className="icon-button" onClick={() => void exportBrief("download")} disabled={busy} title={`Download ${mode === "explain" ? "Summary" : "Engineering"} brief`}>
            <Download size={16} aria-hidden="true" />
          </button>
        </div>
      </section>

      {message ? <p className="project-inline-message" role="status">{message}</p> : null}
      {overview.partial ? <p className="project-warning"><ShieldAlert size={15} /> Partial project data: {overview.warnings.join("; ")}</p> : null}
      {overview.read_only ? <p className="project-warning"><ShieldAlert size={15} /> This project is read-only. Shared definitions and local project status cannot be changed.</p> : null}

      <section className="project-metric-strip" aria-label="Project metrics">
        <ProjectMetric label="Milestones" value={percentage(overview.metrics.milestone_completion_pct)} detail={`${overview.metrics.completed_milestones}/${overview.metrics.milestone_count} done`} />
        <ProjectMetric label="Open risks" value={overview.metrics.open_risks} detail={overview.metrics.open_risks ? "Needs ownership" : "None recorded"} />
        <ProjectMetric label="Pending decisions" value={overview.metrics.pending_decisions} detail={overview.metrics.pending_decisions ? "Awaiting resolution" : "No pending decisions"} />
        <ProjectMetric label="Evidence coverage" value={percentage(overview.metrics.evidence_coverage)} detail="Owner, recent status, evidence" />
      </section>

      <div className="project-overview-grid">
        <section className="project-section project-section-wide">
          <div className="project-section-heading"><div><span className="eyebrow">Roadmap</span><h2>Outcomes and milestones</h2></div><span>{activeOutcomes.length} active</span></div>
          {overview.outcomes.length ? (
            <div className="project-outcome-list">
              {overview.outcomes.slice(0, 6).map((outcome) => (
                <article key={outcome.outcome_id} className="project-outcome-row">
                  <div><strong>{outcome.title}</strong><small>{outcome.owner || "No owner"} · {outcome.target_date || "No target date"}</small></div>
                  <span className={`badge ${tone(outcome.status)}`}>{outcome.status.replace("_", " ")}</span>
                  <div className="project-progress" aria-label={`${outcome.title} progress`}><i style={{ width: `${outcome.progress_pct || 0}%` }} /></div>
                  <span>{percentage(outcome.progress_pct)}</span>
                </article>
              ))}
            </div>
          ) : <EmptyProjectState title="No outcomes declared" detail="Add the first user-owned outcome from Roadmap." />}
        </section>

        <section className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Health</span><h2>Independent signals</h2></div></div>
          <div className="project-health-list">
            {overview.health.dimensions.map((dimension) => (
              <div key={dimension.dimension} className="project-health-row">
                <HealthMark status={dimension.status} />
                <span><strong>{dimension.dimension}</strong><small>{dimension.summary}</small></span>
                <span className={`badge ${tone(dimension.status)}`}>{dimension.status}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="project-section">
          <div className="project-section-heading">
            <div><span className="eyebrow">Project record</span><h2>Risks and decisions</h2></div>
            <div className="inline-actions">
              <button type="button" className="icon-button" title="Record risk" onClick={() => setRecordOpen("risk")} disabled={overview.read_only}><ShieldAlert size={15} /></button>
              <button type="button" className="icon-button" title="Record decision" onClick={() => setRecordOpen("decision")} disabled={overview.read_only}><ClipboardCheck size={15} /></button>
            </div>
          </div>
          <div className="project-record-list">
            {overview.risks.filter((item) => item.status !== "resolved").slice(0, 4).map((risk) => (
              <div key={risk.risk_id}><Flag size={14} /><span><strong>{risk.title}</strong><small>{risk.severity} · {risk.owner || "No owner"}</small></span></div>
            ))}
            {overview.decisions.filter((item) => item.status === "proposed").slice(0, 4).map((decision) => (
              <div key={decision.decision_id}><ClipboardCheck size={14} /><span><strong>{decision.title}</strong><small>proposed · {decision.owner || "No owner"}</small></span></div>
            ))}
            {!overview.metrics.open_risks && !overview.metrics.pending_decisions ? <p className="empty">No open risks or pending decisions.</p> : null}
          </div>
        </section>

        <section className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Activity</span><h2>Recent changes</h2></div></div>
          <ol className="project-compact-timeline">
            {overview.recent_changes.slice(0, 6).map((item) => (
              <li key={item.event_id}><i /><span><strong>{item.title}</strong><small>{formatProjectDate(item.updated_at)} · {item.kind}</small></span></li>
            ))}
          </ol>
          {!overview.recent_changes.length ? <p className="empty">No project activity is available yet.</p> : null}
        </section>

        <section className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Focus</span><h2>Next actions</h2></div></div>
          {nextActions.length ? <ul className="project-next-actions">{nextActions.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="empty">No evidence-backed next action is available.</p>}
          {mode === "build" && health.validation ? <EvidenceList evidence={health.validation.evidence} /> : null}
        </section>
      </div>

      {profileOpen ? <ProfileDialog overview={overview} onClose={() => setProfileOpen(false)} onSaved={(next) => { onOverviewChange(next); setProfileOpen(false); }} /> : null}
      {recordOpen ? <RecordDialog kind={recordOpen} overview={overview} onClose={() => setRecordOpen("")} onSaved={(next) => { onOverviewChange(next); setRecordOpen(""); }} /> : null}
    </div>
  );
}

function ProjectMetric({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return <div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function EmptyProjectState({ title, detail }: { title: string; detail: string }) {
  return <div className="project-empty"><strong>{title}</strong><p>{detail}</p></div>;
}

function ProfileDialog({ overview, onClose, onSaved }: { overview: ProjectOverview; onClose: () => void; onSaved: (overview: ProjectOverview) => void }) {
  const profile = overview.profile;
  const [displayName, setDisplayName] = useState(profile.display_name);
  const [purpose, setPurpose] = useState(profile.purpose);
  const [owners, setOwners] = useState(profile.owners.join(", "));
  const [audiences, setAudiences] = useState(profile.audiences.join(", "));
  const [stage, setStage] = useState(profile.stage);
  const [staleDays, setStaleDays] = useState(profile.status_stale_days);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const next = await updateProjectProfile({
        mutation_id: projectMutationId("profile"),
        expected_revision: profile.config_revision,
        profile: {
          display_name: displayName,
          purpose,
          owners: commaList(owners),
          audiences: commaList(audiences),
          stage,
          status_stale_days: staleDays
        }
      });
      onSaved(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Profile update failed.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="project-modal-backdrop" role="presentation">
      <form className="project-modal" onSubmit={save} aria-label="Edit shared project profile" role="dialog" aria-modal="true">
        <div className="project-modal-heading"><div><span className="eyebrow">Shared configuration</span><h2>Edit project profile</h2></div><button type="button" className="icon-button" onClick={onClose} title="Close"><X size={16} /></button></div>
        <p className="project-warning"><ShieldAlert size={15} /> This updates committed <code>.agentpack/config.toml</code> for every collaborator.</p>
        <label><span>Display name</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={160} /></label>
        <label><span>Purpose</span><textarea value={purpose} onChange={(event) => setPurpose(event.target.value)} rows={4} maxLength={2000} /></label>
        <div className="project-form-grid">
          <label><span>Owners</span><input value={owners} onChange={(event) => setOwners(event.target.value)} placeholder="Platform, Product" /></label>
          <label><span>Audiences</span><input value={audiences} onChange={(event) => setAudiences(event.target.value)} placeholder="Developers, Leads" /></label>
          <label><span>Stage</span><select value={stage} onChange={(event) => setStage(event.target.value)}><option value="">Not declared</option>{["idea", "planning", "active", "maintenance", "paused", "complete"].map((item) => <option key={item}>{item}</option>)}</select></label>
          <label><span>Status stale after</span><input type="number" min={1} max={3650} value={staleDays} onChange={(event) => setStaleDays(Number(event.target.value))} /></label>
        </div>
        {error ? <p className="error-text" role="alert">{error}</p> : null}
        <div className="project-modal-actions"><button type="button" className="secondary-action" onClick={onClose}>Cancel</button><button type="submit" className="primary-action" disabled={saving || !displayName.trim()}>{saving ? "Saving" : "Save shared profile"}</button></div>
      </form>
    </div>
  );
}

function RecordDialog({ kind, overview, onClose, onSaved }: { kind: "risk" | "decision"; overview: ProjectOverview; onClose: () => void; onSaved: (overview: ProjectOverview) => void }) {
  const [title, setTitle] = useState("");
  const [owner, setOwner] = useState("");
  const [detail, setDetail] = useState("");
  const [severity, setSeverity] = useState("medium");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => setError(""), [kind]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    const id = `${kind}-${Date.now().toString(36)}`;
    try {
      const next = await recordProjectEvent(kind === "risk" ? {
        event_type: "project_risk_upsert",
        mutation_id: projectMutationId("risk"),
        entity_id: id,
        title,
        owner,
        description: detail,
        severity,
        status: "open"
      } : {
        event_type: "project_decision_recorded",
        mutation_id: projectMutationId("decision"),
        entity_id: id,
        title,
        owner,
        context: detail,
        status: "proposed"
      });
      onSaved(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `Could not record ${kind}.`);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="project-modal-backdrop" role="presentation">
      <form className="project-modal compact" onSubmit={save} aria-label={`Record project ${kind}`} role="dialog" aria-modal="true">
        <div className="project-modal-heading"><h2>Record {kind}</h2><button type="button" className="icon-button" onClick={onClose} title="Close"><X size={16} /></button></div>
        <label><span>Title</span><input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={160} autoFocus /></label>
        <label><span>Owner</span><input value={owner} onChange={(event) => setOwner(event.target.value)} maxLength={120} /></label>
        {kind === "risk" ? <label><span>Severity</span><select value={severity} onChange={(event) => setSeverity(event.target.value)}>{["low", "medium", "high", "critical"].map((item) => <option key={item}>{item}</option>)}</select></label> : null}
        <label><span>{kind === "risk" ? "Description" : "Context"}</span><textarea value={detail} onChange={(event) => setDetail(event.target.value)} rows={4} maxLength={2000} /></label>
        {error ? <p className="error-text" role="alert">{error}</p> : null}
        <div className="project-modal-actions"><button type="button" className="secondary-action" onClick={onClose}>Cancel</button><button type="submit" className="primary-action" disabled={saving || !title.trim()}><Plus size={15} /> {saving ? "Recording" : "Record"}</button></div>
      </form>
    </div>
  );
}

function commaList(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean).slice(0, 20);
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "project";
}

function tone(value: string): string {
  if (["healthy", "achieved", "done", "on_track", "resolved", "accepted"].includes(value)) return "good";
  if (["blocked", "critical", "failed"].includes(value)) return "risk";
  if (["attention", "at_risk", "high", "stale", "mitigating"].includes(value)) return "warn";
  return "neutral";
}
