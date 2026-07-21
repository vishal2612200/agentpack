import { Command as CommandPrimitive } from "cmdk";
import {
  Activity,
  Brain,
  Building2,
  ClipboardList,
  FileText,
  Flag,
  Map as MapIcon,
  Search,
  ShieldCheck,
  TerminalSquare,
  type LucideIcon
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { DashboardPayload } from "../../data/loadDashboard";
import type { ProjectOverview } from "../../data/schema";
import type { DashboardView } from "../../state/dashboard-state";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../ui/dialog";

export interface PaletteTarget {
  view: DashboardView;
  entityId?: string;
  anchor?: string;
}

type PaletteScope = "all" | "evidence" | "actions";
type PaletteGroup = "Navigate" | "Project evidence" | "Repository evidence" | "Knowledge" | "Actions" | "Recent";

interface PaletteItem {
  id: string;
  group: Exclude<PaletteGroup, "Recent">;
  label: string;
  detail: string;
  keywords: string[];
  icon: LucideIcon;
  target?: PaletteTarget;
  action?: string;
}

const navigationItems: PaletteItem[] = [
  navigation("home", "Overview", "Current project decision", Building2),
  navigation("roadmap", "Roadmap", "Outcomes, milestones, and initiatives", Flag),
  navigation("tasks", "Work", "Tasks and execution evidence", ClipboardList),
  navigation("health", "Health", "Independent project signals", ShieldCheck),
  navigation("learning", "Knowledge", "Learning, memory, and decisions", Brain),
  navigation("activity", "View all activity", "Complete project timeline", Activity),
  navigation("graph", "Impact map", "Repository relationships and evidence", MapIcon),
  navigation("files", "Files", "Selected and affected files", FileText)
];

const safeActions = new Map([
  ["next", "Prepare next step"],
  ["doctor_all", "Run AgentPack doctor"],
  ["status", "Read project status"],
  ["pack_auto", "Pack current context"],
  ["guard_refresh", "Guard and refresh context"],
  ["dev_check", "Run development checks"],
  ["review", "Run review workflow"],
  ["release_check", "Run release checks"],
  ["skills_index", "Refresh skills index"]
]);

export function DashboardCommandPalette({
  open,
  onOpenChange,
  payload,
  overview,
  loadingFull,
  cachedOnly,
  onNavigate,
  onRunAction
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  payload: DashboardPayload;
  overview: ProjectOverview | null;
  loadingFull: boolean;
  cachedOnly: boolean;
  onNavigate: (target: PaletteTarget) => void;
  onRunAction: (action: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<PaletteScope>("all");
  const projectId = overview?.project_id || payload.snapshot.project_record?.project_id || "current";
  const items = useMemo(() => buildPaletteItems(payload, overview), [payload, overview]);
  const recentIds = useMemo(() => readRecentIds(projectId), [projectId, open]);
  const groups = useMemo(() => visibleGroups(items, recentIds, query, scope), [items, query, recentIds, scope]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setScope("all");
    }
  }, [open]);

  const select = (item: PaletteItem) => {
    if (item.action && cachedOnly) return;
    writeRecentId(projectId, item.id);
    onOpenChange(false);
    if (item.target) onNavigate(item.target);
    if (item.action) onRunAction(item.action);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="command-palette-dialog" aria-describedby="command-palette-description">
        <DialogTitle className="sr-only">AgentPack command palette</DialogTitle>
        <DialogDescription id="command-palette-description" className="sr-only">
          Navigate project views, find evidence, or run inspected AgentPack actions.
        </DialogDescription>
        <CommandPrimitive className="command-palette" shouldFilter={false} label="AgentPack command palette">
          <div className="command-palette-search">
            <Search size={17} aria-hidden="true" />
            <CommandPrimitive.Input value={query} onValueChange={setQuery} placeholder="Search project evidence or run an action" autoFocus />
            <kbd>Esc</kbd>
          </div>
          <div className="command-palette-scopes" role="group" aria-label="Command palette scope">
            {(["all", "evidence", "actions"] as const).map((value) => (
              <button key={value} type="button" className={scope === value ? "active" : ""} onClick={() => setScope(value)}>
                {value === "all" ? "All" : value === "evidence" ? "Evidence" : "Actions"}
              </button>
            ))}
          </div>
          <CommandPrimitive.List className="command-palette-list">
            {loadingFull ? <CommandPrimitive.Loading>Loading repository evidence and actions...</CommandPrimitive.Loading> : null}
            <CommandPrimitive.Empty>No matching project evidence or action.</CommandPrimitive.Empty>
            {groups.map(([group, rows]) => rows.length ? (
              <CommandPrimitive.Group key={group} heading={group}>
                {rows.map((item) => {
                  const Icon = item.icon;
                  const disabled = Boolean(item.action && cachedOnly);
                  return (
                    <CommandPrimitive.Item key={`${group}:${item.id}`} value={`${group}:${item.id}`} disabled={disabled} onSelect={() => select(item)}>
                      <Icon size={16} aria-hidden="true" />
                      <span><strong>{item.label}</strong><small>{disabled ? "Reconnect to run this action" : item.detail}</small></span>
                      {item.action ? <span className="badge neutral">Action</span> : null}
                    </CommandPrimitive.Item>
                  );
                })}
              </CommandPrimitive.Group>
            ) : null)}
          </CommandPrimitive.List>
          {cachedOnly ? <p className="command-palette-offline">Last-known mode: navigation and cached project evidence remain available.</p> : null}
        </CommandPrimitive>
      </DialogContent>
    </Dialog>
  );
}

function buildPaletteItems(payload: DashboardPayload, overview: ProjectOverview | null): PaletteItem[] {
  const items = [...navigationItems];
  for (const outcome of overview?.outcomes || []) {
    items.push(evidenceItem(`outcome:${outcome.outcome_id}`, "Project evidence", outcome.title, `${outcome.status} outcome`, [outcome.description, outcome.owner], { view: "roadmap", entityId: outcome.outcome_id }, Flag));
    for (const milestone of outcome.milestones) {
      items.push(evidenceItem(`milestone:${milestone.milestone_id}`, "Project evidence", milestone.title, `${milestone.status} milestone`, [milestone.owner, milestone.due_date], { view: "roadmap", entityId: milestone.milestone_id }, Flag));
    }
  }
  for (const risk of overview?.risks || []) {
    items.push(evidenceItem(`risk:${risk.risk_id}`, "Project evidence", risk.title, `${risk.severity} ${risk.status} risk`, [risk.description, risk.owner], { view: "home", entityId: risk.risk_id }, ShieldCheck));
  }
  for (const decision of overview?.decisions || []) {
    items.push(evidenceItem(`decision:${decision.decision_id}`, "Project evidence", decision.title, `${decision.status} decision`, [decision.context, decision.owner], { view: "home", entityId: decision.decision_id }, Building2));
  }
  for (const initiative of overview?.initiatives || []) {
    items.push(evidenceItem(`initiative:${initiative.initiative_id}`, "Project evidence", initiative.title, "Confirmed initiative", [initiative.description], { view: "roadmap", entityId: initiative.initiative_id }, Flag));
  }
  for (const change of overview?.recent_changes || []) {
    items.push(evidenceItem(`change:${change.event_id}`, "Project evidence", change.title, change.kind, [change.summary, change.branch, change.git_sha], { view: "activity", entityId: change.entity_id }, Activity));
  }
  for (const task of payload.snapshot.project_tasks || []) {
    items.push(evidenceItem(`task:${task.task_id}`, "Repository evidence", task.title, `${task.status.replace("_", " ")} task`, task.source_paths || [], { view: "tasks", entityId: task.task_id }, ClipboardList));
  }
  for (const row of payload.snapshot.task_map || []) {
    items.push(evidenceItem(`file:${row.path}`, "Repository evidence", row.path, `${row.kind || "file"} evidence`, [row.risk_level || "", ...(row.tests_to_run || [])], { view: "files", entityId: `file:${row.path}` }, FileText));
  }
  for (const node of payload.graph.nodes || []) {
    items.push(evidenceItem(`graph:${node.id}`, "Repository evidence", node.label, `${node.type} in impact map`, [node.path || "", node.summary || ""], { view: "graph", entityId: node.id }, MapIcon));
  }
  for (const memory of payload.snapshot.learning_memories || []) {
    items.push(evidenceItem(`memory:${memory.task}`, "Knowledge", memory.task, memory.status || "Learning memory", memory.concepts || [], { view: "learning" }, Brain));
  }
  for (const weakSpot of payload.snapshot.learning_weak_spots || []) {
    items.push(evidenceItem(`weak:${weakSpot.concept}`, "Knowledge", weakSpot.concept, weakSpot.mode || "Knowledge topic", weakSpot.evidence_files || [], { view: "learning" }, Brain));
  }
  for (const command of payload.snapshot.command_catalog || []) {
    const label = safeActions.get(command.id);
    if (!label) continue;
    items.push({
      id: `action:${command.id}`,
      group: "Actions",
      label,
      detail: command.description || "Inspected AgentPack action",
      keywords: [command.id, command.group, command.label, command.description || ""],
      icon: TerminalSquare,
      action: command.id
    });
  }
  return deduplicate(items);
}

function visibleGroups(items: PaletteItem[], recentIds: string[], query: string, scope: PaletteScope): Array<[PaletteGroup, PaletteItem[]]> {
  const allowed = items.filter((item) => scope === "all" || (scope === "actions" ? item.group === "Actions" : !["Actions", "Navigate"].includes(item.group)));
  const filtered = rankItems(allowed, query);
  const groups: Array<[PaletteGroup, PaletteItem[]]> = [];
  if (!query.trim() && scope === "all") {
    const byId = new Map(items.map((item) => [recentItemId(item.id), item]));
    groups.push(["Recent", recentIds.flatMap((id) => byId.get(id) ? [byId.get(id)!] : []).slice(0, 8)]);
  }
  for (const group of ["Navigate", "Project evidence", "Repository evidence", "Knowledge", "Actions"] as const) {
    groups.push([group, filtered.filter((item) => item.group === group).slice(0, 8)]);
  }
  return groups;
}

function rankItems(items: PaletteItem[], query: string): PaletteItem[] {
  const normalized = normalize(query);
  if (!normalized) return items.slice().sort((left, right) => left.label.localeCompare(right.label) || left.id.localeCompare(right.id));
  return items
    .map((item) => ({ item, score: matchScore(item, normalized) }))
    .filter((row) => row.score > 0)
    .sort((left, right) => right.score - left.score || left.item.label.localeCompare(right.item.label) || left.item.id.localeCompare(right.item.id))
    .map((row) => row.item);
}

function matchScore(item: PaletteItem, query: string): number {
  const values = [item.label, item.detail, ...item.keywords].map(normalize).filter(Boolean);
  let score = 0;
  for (const value of values) {
    if (value === query) score = Math.max(score, 400);
    else if (value.startsWith(query)) score = Math.max(score, 300);
    else if (value.split(" ").some((token) => token.startsWith(query))) score = Math.max(score, 200);
    else if (value.includes(query)) score = Math.max(score, 100);
  }
  return score;
}

function navigation(view: DashboardView, label: string, detail: string, icon: PaletteItem["icon"]): PaletteItem {
  return { id: `navigate:${view}`, group: "Navigate", label, detail, keywords: [view], icon, target: { view } };
}

function evidenceItem(id: string, group: PaletteItem["group"], label: string, detail: string, keywords: string[], target: PaletteTarget, icon: PaletteItem["icon"]): PaletteItem {
  return { id, group, label, detail, keywords: keywords.filter(Boolean), target, icon };
}

function deduplicate(items: PaletteItem[]): PaletteItem[] {
  return [...new Map(items.map((item) => [item.id, item])).values()];
}

function normalize(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9._/-]+/g, " ").trim();
}

function recentKey(projectId: string): string {
  return `agentpack.dashboard.palette.recent.v1:${projectId}`;
}

function readRecentIds(projectId: string): string[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(recentKey(projectId)) || "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").slice(0, 8) : [];
  } catch {
    return [];
  }
}

function writeRecentId(projectId: string, itemId: string): void {
  const recentId = recentItemId(itemId);
  const ids = [recentId, ...readRecentIds(projectId).filter((id) => id !== recentId)].slice(0, 8);
  window.localStorage.setItem(recentKey(projectId), JSON.stringify(ids));
}

function recentItemId(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `result-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}
