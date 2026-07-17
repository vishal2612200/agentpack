import { Activity, AlertTriangle, PlayCircle, RefreshCcw, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

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

export function ConfirmCommandDialog({ pending, onCancel, onConfirm }: { pending: PendingCommand; onCancel: () => void; onConfirm: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-command-title">
        <div><p className="eyebrow">Confirm command</p><h1 id="confirm-command-title">Review the next workspace action</h1></div>
        {pending.inspection.purpose ? <p className="muted">{pending.inspection.purpose}</p> : null}
        <code>{pending.command}</code>
        {pending.inspection.expected_effect ? <div className="action-expectation"><strong>Expected effect</strong><span>{pending.inspection.expected_effect}</span></div> : null}
        <div className="stack-sm">
          {pending.inspection.risk_reasons.map((reason) => <div key={reason} className="list-row passive"><span><strong>Risk</strong><small>{reason}</small></span></div>)}
        </div>
        {pending.inspection.affected_paths?.length ? <details className="action-paths"><summary>Affected paths ({pending.inspection.affected_paths.length})</summary><div>{pending.inspection.affected_paths.slice(0, 20).map((path) => <code key={path}>{path}</code>)}</div></details> : null}
        <small>cwd: {pending.inspection.cwd}</small>
        <div className="modal-actions">
          <button type="button" className="secondary-action" onClick={onCancel}>Cancel</button>
          <button type="button" className="primary-action" onClick={onConfirm}><PlayCircle size={16} aria-hidden="true" />Run command</button>
        </div>
      </section>
    </div>
  );
}
