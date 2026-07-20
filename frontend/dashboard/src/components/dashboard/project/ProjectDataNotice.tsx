import { ShieldAlert } from "lucide-react";
import type { PresentationMode } from "../../../data/schema";

type WarningKind = "worktrees" | "limit" | "records" | "roadmap" | "history" | "other";

interface WarningGroup {
  kind: WarningKind;
  label: string;
  count: number;
  description: string;
  code: string;
  items: string[];
}

const WORKTREE_PREFIX = "inaccessible_worktree:";
const DISCOVERY_LIMIT_PREFIX = "partial_result:";
const ROADMAP_PREFIX = "empty_roadmap:";
const HISTORY_PREFIX = "empty_history:";
const RECORD_PREFIXES = ["malformed_", "inaccessible_events:", "inaccessible_tasks:"];

export function ProjectDataNotice({
  warnings,
  accessibleWorktrees,
  mode,
}: {
  warnings: string[];
  accessibleWorktrees: number;
  mode: PresentationMode;
}) {
  const groups = groupWarnings(warnings);
  const summary = summarizeWarnings(groups, accessibleWorktrees);
  const gapLabel = `${groups.length} data gap${groups.length === 1 ? "" : "s"}`;

  return (
    <section className="project-data-notice" role="region" aria-labelledby="project-data-notice-title" data-testid="project-data-notice">
      <div className="project-data-notice-heading">
        <ShieldAlert size={18} aria-hidden="true" />
        <div>
          <h2 id="project-data-notice-title">Some project information is unavailable</h2>
          <p>{summary}</p>
        </div>
      </div>
      <details>
        <summary>Review {gapLabel}</summary>
        <div className="project-data-groups">
          {groups.map((group) => (
            <div key={group.kind} className="project-data-group">
              <div>
                <strong>{group.label}</strong>
                <span>{group.count}</span>
              </div>
              <p>{group.description}</p>
              {mode === "build" ? (
                <div className="project-data-technical">
                  <code>{group.code}</code>
                  {group.items.length ? (
                    <p>
                      Affected: {group.items.slice(0, 5).join(", ")}
                      {group.items.length > 5 ? `, +${group.items.length - 5} more` : ""}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}

function groupWarnings(warnings: string[]): WarningGroup[] {
  const worktrees = warnings.filter((warning) => warning.startsWith(WORKTREE_PREFIX));
  const records = warnings.filter((warning) => RECORD_PREFIXES.some((prefix) => warning.startsWith(prefix)));
  const known = new Set([...worktrees, ...records]);
  const groups: WarningGroup[] = [];

  if (worktrees.length) {
    groups.push({
      kind: "worktrees",
      label: "Unavailable worktrees",
      count: worktrees.length,
      description: `${worktrees.length} registered worktree${worktrees.length === 1 ? " was" : "s were"} excluded because ${worktrees.length === 1 ? "it is" : "they are"} no longer readable.`,
      code: "inaccessible_worktree",
      items: worktrees.map((warning) => workspaceName(warning.slice(WORKTREE_PREFIX.length))),
    });
  }

  if (warnings.some((warning) => warning.startsWith(DISCOVERY_LIMIT_PREFIX))) {
    groups.push({
      kind: "limit",
      label: "Discovery limit reached",
      count: 1,
      description: "Only the first 20 registered worktrees were evaluated for this project view.",
      code: "partial_result",
      items: [],
    });
    warnings.filter((warning) => warning.startsWith(DISCOVERY_LIMIT_PREFIX)).forEach((warning) => known.add(warning));
  }

  if (records.length) {
    groups.push({
      kind: "records",
      label: "Unreadable project records",
      count: records.length,
      description: `${records.length} project data source${records.length === 1 ? " was" : "s were"} skipped because ${records.length === 1 ? "it could" : "they could"} not be read safely.`,
      code: "malformed_or_inaccessible_data",
      items: records.map((warning) => compactWarningDetail(warning)),
    });
  }

  if (warnings.some((warning) => warning.startsWith(ROADMAP_PREFIX))) {
    groups.push({
      kind: "roadmap",
      label: "Roadmap not configured",
      count: 1,
      description: "No project outcomes are declared, so milestone progress is not available yet.",
      code: "empty_roadmap",
      items: [],
    });
    warnings.filter((warning) => warning.startsWith(ROADMAP_PREFIX)).forEach((warning) => known.add(warning));
  }

  if (warnings.some((warning) => warning.startsWith(HISTORY_PREFIX))) {
    groups.push({
      kind: "history",
      label: "No project history",
      count: 1,
      description: "No project activity is available for the selected workspace scope.",
      code: "empty_history",
      items: [],
    });
    warnings.filter((warning) => warning.startsWith(HISTORY_PREFIX)).forEach((warning) => known.add(warning));
  }

  const other = warnings.filter((warning) => !known.has(warning));
  if (other.length) {
    groups.push({
      kind: "other",
      label: "Other data warnings",
      count: other.length,
      description: `${other.length} additional project data warning${other.length === 1 ? " needs" : "s need"} attention.`,
      code: "project_data_warning",
      items: other.map(compactWarningDetail),
    });
  }

  return groups;
}

function summarizeWarnings(groups: WarningGroup[], accessibleWorktrees: number): string {
  const parts = [`${accessibleWorktrees} accessible worktree${accessibleWorktrees === 1 ? "" : "s"} loaded.`];
  for (const group of groups) {
    if (group.kind === "worktrees") parts.push(`${group.count} registered worktree${group.count === 1 ? " is" : "s are"} unavailable.`);
    if (group.kind === "limit") parts.push("Additional worktrees were not evaluated.");
    if (group.kind === "records") parts.push(`${group.count} data source${group.count === 1 ? " was" : "s were"} skipped.`);
    if (group.kind === "roadmap") parts.push("Project outcomes are not configured.");
    if (group.kind === "history") parts.push("Project history is not available.");
    if (group.kind === "other") parts.push(`${group.count} additional warning${group.count === 1 ? " remains" : "s remain"}.`);
  }
  return parts.join(" ");
}

function compactWarningDetail(warning: string): string {
  const separator = warning.indexOf(":");
  const detail = separator >= 0 ? warning.slice(separator + 1).trim() : warning;
  if (!detail) return "No additional detail";
  if (detail.includes("/") || detail.includes("\\")) return workspaceName(detail);
  return detail
    .replace(/_/g, " ");
}

function workspaceName(path: string): string {
  const parts = path.trim().replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || "Unknown workspace";
}
