import { Activity, AlertTriangle, PlayCircle, RefreshCcw, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { DashboardResourceState } from "../../state/dashboard-state";
import { useDashboardState } from "../../state/dashboard-state";

export interface CommandInspection {
  command: string;
  cwd: string;
  allowed: boolean;
  reason: string;
  risky: boolean;
  risk_reasons: string[];
  confirm_required: boolean;
  purpose?: string;
  affected_paths?: string[];
  expected_effect?: string;
  risk?: string;
}

export interface PendingCommand {
  command: string;
  inspection: CommandInspection;
}

export function Panel({ title, icon: Icon, children }: { title: string; icon: LucideIcon; children: ReactNode }) {
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

export function Metric({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return <div className={`metric ${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}

export function StatusPill({ status, label }: { status: string; label?: string }) {
  return <span role="status" className={`status-pill ${status}`}>{label ? `${label}: ${status || "unknown"}` : status || "unknown"}</span>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="center-state">
      <AlertTriangle size={28} aria-hidden="true" />
      <h1>Dashboard failed to load</h1>
      <p>{message}</p>
      <button type="button" className="primary-action" onClick={onRetry}>
        <RefreshCcw size={16} aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

export function LoadingState() {
  return (
    <div className="center-state">
      <Activity size={28} aria-hidden="true" />
      <h1>Loading AgentPack cockpit</h1>
      <p>Reading local dashboard data.</p>
    </div>
  );
}

export function StateSurface({ state, title, onRetry }: { state: DashboardResourceState; title?: string; onRetry?: () => void }) {
  if (["idle", "ready"].includes(state.status)) return null;
  const labels: Record<string, string> = {
    loading: "Refreshing workspace evidence",
    empty: "No evidence is available yet",
    stale: "This context is stale",
    unavailable: "This capability is unavailable",
    forbidden: "This operation is not permitted",
    conflict: "The workspace changed during this operation",
    error: "The workspace could not be refreshed"
  };
  return (
    <section className={`state-surface ${state.status}`} role={state.status === "loading" ? "status" : "alert"} aria-live="polite">
      <AlertTriangle size={18} aria-hidden="true" />
      <span><strong>{title || labels[state.status] || "Workspace state changed"}</strong><small>{state.message || "Previously loaded information remains available."}</small></span>
      {onRetry && state.retryable ? <button type="button" className="secondary-action" onClick={onRetry}><RefreshCcw size={14} aria-hidden="true" />Retry</button> : null}
    </section>
  );
}

export function TechnicalDetail({ summary = "Technical details", children }: { summary?: string; children: ReactNode }) {
  const { state } = useDashboardState();
  if (state.presentationMode === "build") return <div className="technical-detail">{children}</div>;
  return <details className="technical-disclosure"><summary>{summary}</summary><div className="technical-detail">{children}</div></details>;
}

export function ConfirmCommandDialog({ pending, onCancel, onConfirm }: { pending: PendingCommand; onCancel: () => void; onConfirm: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-command-title">
        <div><p className="eyebrow">Confirm command</p><h1 id="confirm-command-title">Review the next workspace action</h1></div>
        {pending.inspection.purpose ? <p className="muted">{pending.inspection.purpose}</p> : null}
        <TechnicalDetail summary="Show exact command and working directory"><code>{pending.command}</code><small>cwd: {pending.inspection.cwd}</small></TechnicalDetail>
        {pending.inspection.expected_effect ? <div className="action-expectation"><strong>Expected effect</strong><span>{pending.inspection.expected_effect}</span></div> : null}
        <div className="stack-sm">
          {pending.inspection.risk_reasons.map((reason) => <div key={reason} className="list-row passive"><span><strong>Risk</strong><small>{reason}</small></span></div>)}
        </div>
        {pending.inspection.affected_paths?.length ? <details className="action-paths"><summary>Affected paths ({pending.inspection.affected_paths.length})</summary><div>{pending.inspection.affected_paths.slice(0, 20).map((path) => <code key={path}>{path}</code>)}</div></details> : null}
        <div className="modal-actions">
          <button type="button" className="secondary-action" onClick={onCancel}>Cancel</button>
          <button type="button" className="primary-action" onClick={onConfirm}><PlayCircle size={16} aria-hidden="true" />Run command</button>
        </div>
      </section>
    </div>
  );
}
