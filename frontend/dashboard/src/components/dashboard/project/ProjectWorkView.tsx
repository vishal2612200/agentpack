import { CheckCircle2, ClipboardList, FileText, PlayCircle, Plus, RefreshCcw, ShieldAlert } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { apiUrl, authHeaders } from "../../../data/loadDashboard";
import type { DashboardSnapshot, DashboardTaskDetail, DashboardTaskRecord, ProjectOverview } from "../../../data/schema";
import { formatProjectDate } from "./project-shared";

export function ProjectWorkView({
  snapshot,
  overview,
  onRunAction,
  onRefresh
}: {
  snapshot: DashboardSnapshot;
  overview: ProjectOverview | null;
  onRunAction: (action: string, body?: Record<string, unknown>) => void;
  onRefresh: () => Promise<unknown> | void;
}) {
  const tasks = snapshot.project_tasks || [];
  const defaultTask = snapshot.active_task || tasks.find((item) => item.active) || tasks[0] || null;
  const [selectedId, setSelectedId] = useState(defaultTask?.task_id || "");
  const [newTask, setNewTask] = useState("");
  const [detail, setDetail] = useState<DashboardTaskDetail | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [message, setMessage] = useState("");
  const selected = tasks.find((item) => item.task_id === selectedId) || defaultTask;
  const readOnly = overview?.read_only || false;

  useEffect(() => {
    if (!selected?.task_id) {
      setDetail(null);
      setDetailState("idle");
      return;
    }
    let cancelled = false;
    setDetailState("loading");
    fetch(apiUrl(`/api/project/tasks/${encodeURIComponent(selected.task_id)}`), { headers: authHeaders() })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("Task details unavailable.")))
      .then((payload: DashboardTaskDetail) => { if (!cancelled) { setDetail(payload); setDetailState("ready"); } })
      .catch(() => { if (!cancelled) { setDetail(null); setDetailState("error"); } });
    return () => { cancelled = true; };
  }, [selected?.task_id, snapshot.generated_at]);

  useEffect(() => {
    if (!selectedId && defaultTask) setSelectedId(defaultTask.task_id);
  }, [defaultTask?.task_id, selectedId]);

  const createTask = async (event: FormEvent) => {
    event.preventDefault();
    if (!newTask.trim()) return;
    setMessage("");
    const response = await fetch(apiUrl("/api/project/tasks"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ title: newTask.trim(), status: "in_progress" })
    });
    const payload = await response.json() as { task?: DashboardTaskRecord; error?: string };
    if (!response.ok || !payload.task) {
      setMessage(payload.error || "Task could not be created.");
      return;
    }
    setNewTask("");
    setSelectedId(payload.task.task_id);
    await onRefresh();
  };

  const updateStatus = async (status: DashboardTaskRecord["status"]) => {
    if (!selected) return;
    setMessage("");
    const response = await fetch(apiUrl("/api/project/tasks/update"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ task_id: selected.task_id, status })
    });
    if (!response.ok) {
      const payload = await response.json() as { error?: string };
      setMessage(payload.error || "Task status could not be updated.");
      return;
    }
    await onRefresh();
  };

  const latestRun = detail?.runs.length ? detail.runs[detail.runs.length - 1] : undefined;
  return (
    <div className="view-stack project-product-view" data-testid="project-work-view">
      <section className="project-view-heading">
        <div><span className="eyebrow">Work</span><h1>Tasks and execution evidence</h1><p>Plan, run, update, and inspect task evidence in the current worktree.</p></div>
        <div className="project-header-actions">
          <button type="button" className="secondary-action" onClick={() => onRunAction("refresh_context", { agent: "codex", thread: "global" })} disabled={readOnly}><RefreshCcw size={15} /> Refresh context</button>
          <button type="button" className="primary-action" onClick={() => onRunAction("next")} disabled={!selected || readOnly}><PlayCircle size={15} /> Prepare next step</button>
        </div>
      </section>
      {message ? <p className="project-inline-message" role="status">{message}</p> : null}
      {readOnly ? <p className="project-warning"><ShieldAlert size={15} /> This project is read-only. Task and context mutations are disabled.</p> : null}

      <div className="project-work-layout">
        <aside className="project-work-queue">
          <form onSubmit={createTask}>
            <label><span>New task</span><input value={newTask} onChange={(event) => setNewTask(event.target.value)} placeholder="Build, fix, or investigate" disabled={readOnly} /></label>
            <button type="submit" className="icon-button" title="Create task" disabled={!newTask.trim() || readOnly}><Plus size={16} /></button>
          </form>
          <div className="project-task-list">
            {tasks.map((task) => (
              <button key={task.task_id} type="button" className={selected?.task_id === task.task_id ? "active" : ""} onClick={() => setSelectedId(task.task_id)}>
                <span><strong>{task.title}</strong><small>{task.updated_at ? formatProjectDate(task.updated_at) : "No activity"}</small></span>
                <span className={`badge ${taskTone(task.status)}`}>{task.status.replace("_", " ")}</span>
              </button>
            ))}
            {!tasks.length ? <div className="project-empty"><ClipboardList size={18} /><strong>No tasks recorded</strong><p>Create the first work item for this workspace.</p></div> : null}
          </div>
        </aside>

        <section className="project-work-detail">
          {selected ? (
            <>
              <div className="project-work-detail-heading">
                <div><span className="eyebrow">Selected task</span><h2>{selected.title}</h2><p>{selected.description || "No task description recorded."}</p></div>
                <select aria-label="Task status" value={selected.status} disabled={readOnly} onChange={(event) => void updateStatus(event.target.value as DashboardTaskRecord["status"])}>
                  {(["todo", "in_progress", "needs_attention", "done"] as const).map((status) => <option key={status} value={status}>{status.replace("_", " ")}</option>)}
                </select>
              </div>
              <div className="project-work-metrics">
                <span><strong>{latestRun?.selected_files?.length || 0}</strong> selected files</span>
                <span><strong>{latestRun?.checks?.length || 0}</strong> checks</span>
                <span><strong>{detail?.timeline.length || 0}</strong> updates</span>
                <span><strong>{latestRun?.saving_pct || 0}%</strong> context saved</span>
              </div>
              {detailState === "loading" ? <p className="muted">Loading task evidence...</p> : null}
              {detailState === "error" ? <p className="error-text">Task evidence could not be loaded.</p> : null}
              {detailState === "ready" ? (
                <div className="project-task-evidence-grid">
                  <section><span className="eyebrow">Selected files</span>{latestRun?.selected_files?.length ? <ul>{latestRun.selected_files.slice(0, 20).map((path: string) => <li key={path}><FileText size={13} /><code>{path}</code></li>)}</ul> : <p className="empty">No selected-file evidence.</p>}</section>
                  <section><span className="eyebrow">Checks</span>{latestRun?.checks?.length ? <ul>{latestRun.checks.map((check: string) => <li key={check}><CheckCircle2 size={13} /><span>{check}</span></li>)}</ul> : <p className="empty">No checks recorded.</p>}</section>
                  <section className="wide"><span className="eyebrow">Task timeline</span>{detail?.timeline.length ? <ol>{detail.timeline.slice().reverse().slice(0, 12).map((item) => <li key={item.event_id}><i /><span><strong>{item.label || item.event_type}</strong><small>{formatProjectDate(item.occurred_at || "")}</small>{item.summary ? <p>{item.summary}</p> : null}</span></li>)}</ol> : <p className="empty">No lifecycle events recorded.</p>}</section>
                </div>
              ) : null}
              <div className="inline-actions project-work-actions"><button type="button" className="primary-action" disabled={readOnly} onClick={() => onRunAction("next")}><PlayCircle size={15} /> Continue work</button><button type="button" className="secondary-action" disabled={readOnly} onClick={() => onRunAction("finish", { summary: `Completed ${selected.title}` })}><CheckCircle2 size={15} /> Finish task</button></div>
            </>
          ) : <div className="project-empty"><ClipboardList size={22} /><strong>Select or create a task</strong><p>Task evidence stays scoped to the current worktree.</p></div>}
        </section>
      </div>
    </div>
  );
}

function taskTone(status: DashboardTaskRecord["status"]): string {
  if (status === "done") return "good";
  if (status === "needs_attention") return "warn";
  if (status === "in_progress") return "memory";
  return "neutral";
}
