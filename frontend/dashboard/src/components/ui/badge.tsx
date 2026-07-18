import { type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export type BadgeTone =
  | "neutral"
  | "good"
  | "warn"
  | "risk"
  | "memory"
  | "file"
  | "test"
  | "task"
  | "action"
  | "episode"
  | "procedure"
  | "fresh"
  | "stale"
  | "missing";

export function Badge({ tone = "neutral", className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone | string }) {
  return <span className={cn("ui-badge", `ui-badge-${tone}`, className)} {...props} />;
}

export function StatusBadge({ status }: { status?: string }) {
  return <Badge tone={status || "neutral"}>{status || "unknown"}</Badge>;
}
