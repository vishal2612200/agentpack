import { Check, Flag, Lightbulb, Plus, ShieldAlert, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { recordProjectEvent, updateProjectProfile } from "../../../data/loadDashboard";
import type { ProjectOutcomeState, ProjectOverview } from "../../../data/schema";
import { EvidenceList, WorkspaceFilter, percentage, projectMutationId } from "./project-shared";

export function ProjectRoadmapView({
  overview,
  workspace,
  loading,
  onWorkspaceChange,
  onOverviewChange
}: {
  overview: ProjectOverview;
  workspace: string;
  loading: boolean;
  onWorkspaceChange: (value: string) => void;
  onOverviewChange: (overview: ProjectOverview) => void;
}) {
  const [dialog, setDialog] = useState<{ kind: "outcome" | "milestone"; outcome?: ProjectOutcomeState } | null>(null);
  const [message, setMessage] = useState("");
  const [busyId, setBusyId] = useState("");

  const recordStatus = async (eventType: "project_outcome_status" | "project_milestone_status", entityId: string, status: string) => {
    setBusyId(entityId);
    setMessage("");
    try {
      onOverviewChange(await recordProjectEvent({
        event_type: eventType,
        mutation_id: projectMutationId("roadmap"),
        workspace,
        entity_id: entityId,
        status
      }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Roadmap update failed.");
    } finally {
      setBusyId("");
    }
  };

  const suggestionAction = async (suggestionId: string, action: "confirm" | "dismiss") => {
    setBusyId(suggestionId);
    setMessage("");
    try {
      onOverviewChange(await recordProjectEvent({
        event_type: action === "confirm" ? "project_initiative_confirmed" : "project_initiative_dismissed",
        mutation_id: projectMutationId(`initiative-${action}`),
        workspace,
        entity_id: suggestionId
      }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Could not ${action} initiative.`);
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className="view-stack project-product-view" data-testid="project-roadmap-view">
      <section className="project-view-heading">
        <div><span className="eyebrow">Roadmap</span><h1>Outcomes before task counts</h1><p>Progress comes only from declared milestones. AgentPack suggestions stay local until you confirm them.</p></div>
        <div className="project-header-actions">
          <WorkspaceFilter overview={overview} value={workspace} onChange={onWorkspaceChange} disabled={loading} />
          <button type="button" className="primary-action" onClick={() => setDialog({ kind: "outcome" })} disabled={overview.read_only}><Plus size={15} /> Add outcome</button>
        </div>
      </section>
      {message ? <p className="project-inline-message" role="status">{message}</p> : null}
      {overview.read_only ? <p className="project-warning"><ShieldAlert size={15} /> This project is read-only. Roadmap definitions and statuses cannot be changed.</p> : null}

      <section className="project-roadmap-band">
        <div className="project-section-heading"><div><span className="eyebrow">Declared roadmap</span><h2>Outcomes and milestones</h2></div><span>{overview.metrics.completed_milestones}/{overview.metrics.milestone_count} milestones done</span></div>
        <div className="project-roadmap-list">
          {overview.outcomes.map((outcome) => (
            <article key={outcome.outcome_id} className="project-roadmap-outcome">
              <div className="project-roadmap-outcome-heading">
                <div><strong>{outcome.title}</strong><p>{outcome.description || "No outcome description."}</p><small>{outcome.owner || "No owner"} · {outcome.target_date || "No target date"}</small></div>
                <div className="project-roadmap-controls">
                  <span>{percentage(outcome.progress_pct)}</span>
                  <select aria-label={`${outcome.title} status`} value={outcome.status} disabled={busyId === outcome.outcome_id || overview.read_only} onChange={(event) => void recordStatus("project_outcome_status", outcome.outcome_id, event.target.value)}>
                    {(["planned", "on_track", "at_risk", "achieved", "paused"] as const).map((status) => <option key={status} value={status}>{status.replace("_", " ")}</option>)}
                  </select>
                  <button type="button" className="icon-button" title="Add milestone" onClick={() => setDialog({ kind: "milestone", outcome })} disabled={overview.read_only}><Plus size={15} /></button>
                </div>
              </div>
              <div className="project-progress"><i style={{ width: `${outcome.progress_pct || 0}%` }} /></div>
              <div className="project-milestone-list">
                {outcome.milestones.map((milestone) => (
                  <div key={milestone.milestone_id} className="project-milestone-row">
                    <span className={`milestone-check ${milestone.status}`}><Check size={13} /></span>
                    <span><strong>{milestone.title}</strong><small>{milestone.owner || "No owner"} · {milestone.due_date || "No due date"}</small></span>
                    <select aria-label={`${milestone.title} status`} value={milestone.status} disabled={busyId === milestone.milestone_id || overview.read_only} onChange={(event) => void recordStatus("project_milestone_status", milestone.milestone_id, event.target.value)}>
                      {(["planned", "in_progress", "blocked", "done"] as const).map((status) => <option key={status} value={status}>{status.replace("_", " ")}</option>)}
                    </select>
                  </div>
                ))}
                {!outcome.milestones.length ? <p className="empty">No milestones declared. Progress remains Not ready.</p> : null}
              </div>
            </article>
          ))}
          {!overview.outcomes.length ? <div className="project-empty"><Flag size={20} /><strong>No outcomes declared</strong><p>Add a user-owned outcome and its first milestone.</p></div> : null}
        </div>
      </section>

      <div className="project-two-column">
        <section className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Confirmed</span><h2>Initiatives</h2></div><span>{overview.initiatives.length}</span></div>
          <div className="project-initiative-list">
            {overview.initiatives.map((initiative) => (
              <article key={initiative.initiative_id}><Flag size={15} /><div><strong>{initiative.title}</strong><p>{initiative.description}</p><small>{initiative.owner || "No owner"}{initiative.outcome_id ? ` · ${initiative.outcome_id}` : ""}</small></div></article>
            ))}
            {!overview.initiatives.length ? <p className="empty">No initiative suggestions have been confirmed.</p> : null}
          </div>
        </section>

        <section className="project-section">
          <div className="project-section-heading"><div><span className="eyebrow">Observed</span><h2>Suggested initiatives</h2></div><span>{overview.initiative_suggestions.length}/5</span></div>
          <div className="project-suggestion-list">
            {overview.initiative_suggestions.map((suggestion) => (
              <article key={suggestion.suggestion_id}>
                <div className="project-suggestion-heading"><Lightbulb size={16} /><div><strong>{suggestion.title}</strong><p>{suggestion.rationale}</p></div><span className="badge neutral">{suggestion.score}</span></div>
                <EvidenceList evidence={suggestion.evidence} />
                <div className="inline-actions">
                  <button type="button" className="primary-action" onClick={() => void suggestionAction(suggestion.suggestion_id, "confirm")} disabled={busyId === suggestion.suggestion_id || overview.read_only}><Check size={15} /> Confirm</button>
                  <button type="button" className="secondary-action" onClick={() => void suggestionAction(suggestion.suggestion_id, "dismiss")} disabled={busyId === suggestion.suggestion_id || overview.read_only}><X size={15} /> Dismiss</button>
                </div>
              </article>
            ))}
            {!overview.initiative_suggestions.length ? <p className="empty">No repeated task pattern has enough evidence for a suggestion.</p> : null}
          </div>
        </section>
      </div>

      {dialog ? <RoadmapDefinitionDialog kind={dialog.kind} outcome={dialog.outcome} overview={overview} onClose={() => setDialog(null)} onSaved={(next) => { onOverviewChange(next); setDialog(null); }} /> : null}
    </div>
  );
}

function RoadmapDefinitionDialog({ kind, outcome, overview, onClose, onSaved }: { kind: "outcome" | "milestone"; outcome?: ProjectOutcomeState; overview: ProjectOverview; onClose: () => void; onSaved: (overview: ProjectOverview) => void }) {
  const [title, setTitle] = useState("");
  const [owner, setOwner] = useState("");
  const [date, setDate] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    const outcomes = overview.outcomes.map((item) => definition(item));
    if (kind === "outcome") {
      outcomes.push({ id: "", title, description, owner, target_date: date, milestones: [] });
    } else if (outcome) {
      const target = outcomes.find((item) => item.id === outcome.outcome_id);
      target?.milestones.push({ id: "", title, owner, due_date: date });
    }
    try {
      onSaved(await updateProjectProfile({
        mutation_id: projectMutationId("roadmap-definition"),
        expected_revision: overview.profile.config_revision,
        profile: { outcomes }
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Roadmap definition update failed.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="project-modal-backdrop" role="presentation">
      <form className="project-modal compact" onSubmit={save} aria-label={`Add project ${kind}`} role="dialog" aria-modal="true">
        <div className="project-modal-heading"><div><span className="eyebrow">Shared roadmap</span><h2>Add {kind}</h2></div><button type="button" className="icon-button" onClick={onClose} title="Close"><X size={16} /></button></div>
        <p className="project-warning"><ShieldAlert size={15} /> This changes committed project definitions.</p>
        <label><span>Title</span><input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={160} autoFocus /></label>
        {kind === "outcome" ? <label><span>Description</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} maxLength={2000} /></label> : null}
        <label><span>Owner</span><input value={owner} onChange={(event) => setOwner(event.target.value)} maxLength={120} /></label>
        <label><span>{kind === "outcome" ? "Target date" : "Due date"}</span><input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        {error ? <p className="error-text" role="alert">{error}</p> : null}
        <div className="project-modal-actions"><button type="button" className="secondary-action" onClick={onClose}>Cancel</button><button type="submit" className="primary-action" disabled={saving || !title.trim()}><Plus size={15} /> {saving ? "Saving" : `Add ${kind}`}</button></div>
      </form>
    </div>
  );
}

function definition(outcome: ProjectOutcomeState) {
  return {
    id: outcome.outcome_id,
    title: outcome.title,
    description: outcome.description,
    owner: outcome.owner,
    target_date: outcome.target_date,
    milestones: outcome.milestones.map((milestone) => ({
      id: milestone.milestone_id,
      title: milestone.title,
      owner: milestone.owner,
      due_date: milestone.due_date
    }))
  };
}
