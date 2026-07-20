import { CircleDot, Copy, RefreshCcw, Trash2 } from "lucide-react";
import type { DashboardSnapshot } from "../../data/schema";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../ui/dialog";

export type DashboardConnectionState = "connecting" | "live" | "stale" | "unavailable";

export function RuntimeStatusDialog({
  open,
  onOpenChange,
  connection,
  observedAt,
  cachedAt,
  snapshot,
  onRetry,
  onClearCache
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connection: DashboardConnectionState;
  observedAt: string;
  cachedAt: string;
  snapshot: DashboardSnapshot;
  onRetry: () => void;
  onClearCache: () => void;
}) {
  const mcp = snapshot.mcp_health || {};
  const signals = [
    {
      label: "API",
      status: connection,
      summary: connection === "live" ? "The local dashboard API responded." : connection === "stale" ? "Showing the last stored project status." : connection === "connecting" ? "Waiting for the local dashboard API." : "The local dashboard API is unavailable.",
      source: "/api/dashboard/v2",
      time: observedAt || cachedAt,
      remediation: connection === "live" ? "No action needed." : "Start the local server, then retry."
    },
    {
      label: "Snapshot",
      status: connection === "live" ? "fresh" : "stale",
      summary: connection === "live" ? "Project status came from the current server response." : "Project status came from the privacy-bounded browser cache.",
      source: "dashboard project snapshot",
      time: snapshot.generated_at || cachedAt,
      remediation: connection === "live" ? "No action needed." : "Retry for live evidence or clear the stored snapshot."
    },
    {
      label: "Context",
      status: snapshot.context.status || "unknown",
      summary: snapshot.context.stale_reason || "No additional context detail was reported.",
      source: ".agentpack/pack_metadata.json",
      time: snapshot.context.generated_at || "",
      remediation: snapshot.context.status === "fresh" ? "No action needed." : snapshot.context.source_command || "Refresh AgentPack context."
    },
    {
      label: "MCP",
      status: mcp.status || "unknown",
      summary: mcp.runtime_detail || "No MCP runtime detail was reported.",
      source: mcp.source || "AgentPack MCP evidence",
      time: mcp.checked_at || "",
      remediation: mcp.status === "healthy" ? "No action needed." : mcp.remediation?.[0] || "Repair the AgentPack agent integration."
    }
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="runtime-status-dialog" aria-describedby="runtime-status-description">
        <DialogTitle>Dashboard evidence status</DialogTitle>
        <DialogDescription id="runtime-status-description">
          Independent connection, snapshot, Context, and MCP signals. Unknown evidence is never reported as healthy.
        </DialogDescription>
        <div className="runtime-signal-list">
          {signals.map((signal) => (
            <section key={signal.label} className="runtime-signal-row">
              <CircleDot size={15} aria-hidden="true" />
              <div>
                <span><strong>{signal.label}</strong><i className={`status-pill ${signal.status}`}>{signal.status}</i></span>
                <p>{signal.summary}</p>
                <small>Source: {signal.source}</small>
                <small>Observed: {formatStatusTime(signal.time)}</small>
                <small>Remediation: {signal.remediation}</small>
              </div>
            </section>
          ))}
        </div>
        {mcp.remediation?.length ? (
          <section className="runtime-remediation">
            <strong>MCP remediation</strong>
            {mcp.remediation.slice(0, 4).map((command) => <code key={command}>{command}</code>)}
          </section>
        ) : null}
        <div className="runtime-status-actions">
          <button type="button" className="secondary-action" onClick={() => navigator.clipboard.writeText("agentpack dashboard --port 8765")}>
            <Copy size={15} aria-hidden="true" /> Copy server command
          </button>
          <button type="button" className="secondary-action" onClick={onRetry}>
            <RefreshCcw size={15} aria-hidden="true" /> Retry
          </button>
          {cachedAt ? <button type="button" className="secondary-action" onClick={onClearCache}><Trash2 size={15} aria-hidden="true" /> Clear stored snapshot</button> : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function formatStatusTime(value: string): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}
