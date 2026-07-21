import { AlertTriangle, CheckCircle2, CircleHelp, Clock3, RefreshCcw, ShieldAlert } from "lucide-react";
import type { ProjectEvidence, ProjectHealthStatus, ProjectOverview } from "../../../data/schema";

export function WorkspaceFilter({
  overview,
  value,
  onChange,
  disabled = false
}: {
  overview: ProjectOverview;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="project-workspace-filter">
      <span>Workspace</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="all">All worktrees</option>
        <option value="current">Current worktree</option>
        {overview.workspaces.filter((item) => !item.is_current).map((workspace) => (
          <option key={workspace.workspace_id} value={workspace.workspace_id}>
            {workspace.branch || workspace.workspace_id}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ProjectViewState({
  status,
  message,
  onRetry
}: {
  status: "loading" | "error" | "empty";
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className={`project-view-state ${status}`} role={status === "error" ? "alert" : "status"}>
      {status === "error" ? <AlertTriangle size={18} aria-hidden="true" /> : <Clock3 size={18} aria-hidden="true" />}
      <span>{message}</span>
      {onRetry ? <button type="button" className="icon-button" onClick={onRetry} title="Retry"><RefreshCcw size={16} /></button> : null}
    </div>
  );
}

export function HealthMark({ status }: { status: ProjectHealthStatus }) {
  const Icon = status === "healthy" ? CheckCircle2 : status === "blocked" ? ShieldAlert : status === "unknown" ? CircleHelp : AlertTriangle;
  return (
    <span className={`project-health-mark ${status}`} aria-label={status} title={status}>
      <Icon size={15} aria-hidden="true" />
    </span>
  );
}

export function EvidenceList({ evidence, empty = "No concrete evidence recorded." }: { evidence: ProjectEvidence[]; empty?: string }) {
  if (!evidence.length) return <p className="empty">{empty}</p>;
  return (
    <ul className="project-evidence-list">
      {evidence.slice(0, 8).map((item, index) => (
        <li key={`${item.kind}:${item.ref}:${item.path}:${index}`}>
          <span className="badge neutral">{item.kind}</span>
          <span>{item.summary || item.ref || "Recorded evidence"}</span>
          {item.path ? <code>{item.path}</code> : null}
        </li>
      ))}
    </ul>
  );
}

export function projectMutationId(prefix: string): string {
  const suffix = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().slice(0, 12)
    : Math.random().toString(36).slice(2, 14);
  return `${prefix}-${Date.now().toString(36)}-${suffix}`;
}

export function formatProjectDate(value: string): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function percentage(value: number | null): string {
  return value === null ? "Not ready" : `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`;
}
