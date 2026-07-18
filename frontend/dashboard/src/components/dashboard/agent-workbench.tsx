import { Handshake, Users, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { DashboardPayload } from "../../data/loadDashboard";
import { useDashboardState, type OperationStatus } from "../../state/dashboard-state";

type PresentationMode = "explain" | "build";

export function AgentWorkbench({
  agents,
  mode,
  onRunAction,
  onHandoffAction
}: {
  agents?: DashboardPayload["agents"];
  mode: PresentationMode;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onHandoffAction: (action: "resume" | "release", name: string) => void;
}) {
  const { state } = useDashboardState();
  const handoffs = agents?.handoffs || [];
  const sessions = agents?.sessions || [];
  const ready = handoffs.filter((item) => item.status === "ready").length;
  const claimed = handoffs.filter((item) => item.status === "claimed").length;
  return (
    <FeaturePanel title="Agent workbench" icon={Users}>
      <div className="agent-workbench-grid">
        <div className="agent-status-block">
          <div className="agent-status-heading"><span className="signal-dot" /><strong>Continuity control</strong></div>
          <p className="muted">{mode === "explain" ? "Keep work moving between real agent sessions without losing task intent or evidence." : "Inspect provider sessions, claims, and handoff state before resuming work."}</p>
          <div className="inline-actions">
            <span className="badge good"><Handshake size={13} aria-hidden="true" /> {ready} ready</span>
            <span className="badge neutral">{claimed} claimed</span>
            <button type="button" className="secondary-action" onClick={() => onRunAction("refresh_context", { agent: "codex", thread: "global" })}>Refresh context</button>
          </div>
        </div>
        <div className="handoff-list">
          {sessions.slice(0, 4).map((session) => (
            <div className="handoff-row" key={`${session.provider}:${session.session_id}`}>
              <span>
                <strong>{session.provider || "generic"} session</strong>
                <small>{session.task || "No active task"}</small>
                {mode === "build" ? <><small>{session.session_id || session.thread_id || "session unavailable"}</small><small>{session.worktree || "worktree unavailable"}</small><small>{session.updated_at || "activity time unavailable"}</small></> : <small>{session.context_status || "unknown context"} · {session.status || "unknown status"}</small>}
              </span>
              <span className={`badge ${handoffTone(String(session.status || session.context_status || "unknown"))}`}>{session.context_status || session.status || "unknown"}</span>
            </div>
          ))}
          {handoffs.slice(0, 4).map((handoff) => (
            <div className="handoff-row" key={String(handoff.name)}>
              <span>
                <strong>{String(handoff.name || "Unnamed handoff")}</strong>
                <small>{String(handoff.status || "unknown")} · {String(handoff.target_provider || handoff.source_provider || "generic agent")}</small>
                <small>{handoff.task || handoff.summary || "No task summary provided"}</small>
                {mode === "build" && handoff.claim_session_id ? <small>Claimed by {handoff.claim_provider || "agent"} · {handoff.claim_session_id}</small> : null}
              </span>
              <span className="handoff-actions">
                <span className={`badge ${handoffTone(state.handoffOperations[handoff.name] || String(handoff.status || "unknown"))}`}>{operationLabel(state.handoffOperations[handoff.name], String(handoff.status || "unknown"))}</span>
                {handoff.status === "ready" ? <button type="button" className="command-chip" disabled={state.handoffOperations[handoff.name] === "pending"} onClick={() => onHandoffAction("resume", String(handoff.name))}>Resume</button> : null}
                {handoff.status === "claimed" ? <button type="button" className="command-chip" disabled={state.handoffOperations[handoff.name] === "pending"} onClick={() => onHandoffAction("release", String(handoff.name))}>Release</button> : null}
              </span>
            </div>
          ))}
          {!handoffs.length && !sessions.length ? <p className="empty">No active sessions or handoffs are available for this workspace.</p> : null}
        </div>
      </div>
    </FeaturePanel>
  );
}

function operationLabel(operation: OperationStatus | undefined, fallback: string) {
  if (!operation || operation === "idle") return fallback;
  return operation.replace(/_/g, " ");
}

function FeaturePanel({ title, icon: Icon, children }: { title: string; icon: LucideIcon; children: ReactNode }) {
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

function handoffTone(status: string) {
  if (status === "ready" || status === "completed") return "good";
  if (status === "claimed") return "memory";
  if (status === "cancelled") return "warn";
  if (status === "pending") return "neutral";
  if (["conflict", "stale", "repository_mismatch", "permission_denied", "error"].includes(status)) return "risk";
  return "neutral";
}
