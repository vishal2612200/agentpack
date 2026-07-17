import { Handshake, Users, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { DashboardPayload } from "../../data/loadDashboard";

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
  const handoffs = agents?.handoffs || [];
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
          {handoffs.slice(0, 4).map((handoff) => (
            <div className="handoff-row" key={String(handoff.name)}>
              <span>
                <strong>{String(handoff.name || "Unnamed handoff")}</strong>
                <small>{String(handoff.status || "unknown")} · {String(handoff.target_provider || handoff.source_provider || "generic agent")}</small>
                <small>{handoff.task || handoff.summary || "No task summary provided"}</small>
                {handoff.claim_session_id ? <small>Claimed by {handoff.claim_provider || "agent"} · {handoff.claim_session_id}</small> : null}
              </span>
              <span className="handoff-actions">
                <span className={`badge ${handoffTone(String(handoff.status || "unknown"))}`}>{String(handoff.status || "unknown")}</span>
                {handoff.status === "ready" ? <button type="button" className="command-chip" onClick={() => onHandoffAction("resume", String(handoff.name))}>Resume</button> : null}
                {handoff.status === "claimed" ? <button type="button" className="command-chip" onClick={() => onHandoffAction("release", String(handoff.name))}>Release</button> : null}
              </span>
            </div>
          ))}
          {!handoffs.length ? <p className="empty">No handoffs are waiting for this workspace.</p> : null}
        </div>
      </div>
    </FeaturePanel>
  );
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
  return "neutral";
}
