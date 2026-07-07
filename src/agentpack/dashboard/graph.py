from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.dashboard.models import (
    DashboardAction,
    DashboardEdge,
    DashboardEvidence,
    DashboardGraph,
    DashboardGraphSummary,
    DashboardNode,
    DashboardSnapshot,
    SelectedFileRow,
    SelectedSymbolRow,
    TaskMapFileRow,
)
from agentpack.learning.memory_timeline import build_memory_timeline

MAX_GRAPH_NODES = 80
MAX_MEMORY_ROWS = 200
MAX_SYMBOL_NODES = 36


def build_dashboard_graph(
    snapshot: DashboardSnapshot,
    root: Path | None = None,
    *,
    max_nodes: int = MAX_GRAPH_NODES,
) -> DashboardGraph:
    """Build a task-scoped graph that explains context selection decisions."""

    builder = _GraphBuilder(snapshot, max_nodes=max_nodes)
    builder.add_task()
    builder.add_review_runs()
    builder.add_related_task_nodes()
    builder.add_files()
    builder.add_suggested_actions()
    builder.add_learning_memories()
    if root is not None:
        builder.add_memory_timeline(root)
    return builder.graph()


class _GraphBuilder:
    def __init__(self, snapshot: DashboardSnapshot, *, max_nodes: int) -> None:
        self.snapshot = snapshot
        self.max_nodes = max(10, max_nodes)
        self.nodes: dict[str, DashboardNode] = {}
        self.edges: dict[str, DashboardEdge] = {}
        self.truncated = False
        self.symbol_nodes_added = 0

    def add_task(self) -> None:
        task = self.snapshot.task.text or "No active task"
        self._add_node(
            DashboardNode(
                id="task:active",
                type="task",
                label=_task_title(task),
                status=self.snapshot.task.state,
                summary=task,
                evidence=[
                    DashboardEvidence(
                        kind="task",
                        ref=".agentpack/task.md",
                        summary="Active AgentPack task text.",
                    )
                ],
            )
        )

    def add_related_task_nodes(self) -> None:
        for memory in self.snapshot.learning_memories[:6]:
            if not memory.task:
                continue
            node_id = "task:memory:" + _slug(memory.task or memory.git_sha or "task")
            self._add_node(
                DashboardNode(
                    id=node_id,
                    type="task",
                    label=_task_title(memory.task),
                    status=memory.status,
                    summary=memory.task,
                    metadata={
                        "source": "task_memory",
                        "stage": memory.stage,
                        "branch": memory.branch,
                        "git_sha": memory.git_sha,
                        "concepts": memory.concepts,
                    },
                    evidence=[DashboardEvidence(kind="task_memory", summary=memory.task)],
                )
            )
            self._add_edge(
                DashboardEdge(
                    id=f"edge:{node_id}:active-task",
                    source=node_id,
                    target="task:active",
                    type="memory_influenced",
                    label="related task",
                    confidence=0.65,
                    reason="Recent task memory is related to the current context decision.",
                    evidence=[DashboardEvidence(kind="task_memory", summary=memory.task)],
                )
            )

    def add_files(self) -> None:
        task_map_by_path = {item.path: item for item in self.snapshot.task_map if item.path}
        for selected in self.snapshot.selected_files:
            item = task_map_by_path.get(selected.path)
            if item is None:
                item = TaskMapFileRow(
                    path=selected.path,
                    kind="selected",
                    include_mode=selected.include_mode,
                    score=selected.score,
                    why_selected=selected.reasons,
                )
            self._add_file_node(item, selected=True)
            self._add_symbol_nodes(selected)

        for item in self.snapshot.task_map:
            if item.path and item.path not in {selected.path for selected in self.snapshot.selected_files}:
                self._add_file_node(item, selected=item.kind == "selected")

    def add_suggested_actions(self) -> None:
        for index, action in enumerate(self.snapshot.suggested_actions[:8], start=1):
            node_id = f"action:suggested:{index}"
            self._add_node(
                DashboardNode(
                    id=node_id,
                    type="action",
                    label=action.label,
                    summary=action.reason,
                    actions=[DashboardAction(label=action.label, command=action.command)],
                    evidence=[DashboardEvidence(kind="suggested_action", summary=action.reason)],
                )
            )
            self._add_edge(
                DashboardEdge(
                    id=f"edge:task:action:{index}",
                    source="task:active",
                    target=node_id,
                    type="retrieve_ref",
                    label="next action",
                    reason=action.reason,
                    actions=[DashboardAction(label=action.label, command=action.command)],
                )
            )

    def add_review_runs(self) -> None:
        for index, run in enumerate(self.snapshot.review_runs[:8], start=1):
            node_id = f"review:{_slug(run.run_id or str(index))}"
            target = f"PR #{run.target_number}" if run.target_number else run.branch_prefix or "local diff"
            title = target if target != "local diff" else run.review_context or run.run_id
            summary_parts = [
                run.status or "prepared",
                f"{run.changed_files_count} changed files" if run.changed_files_count else "",
                run.diff_source,
            ]
            summary = " · ".join(part for part in summary_parts if part)
            actions = [
                DashboardAction(label="Resume review", command=run.resume_command),
                DashboardAction(label="Check review", command=run.check_command),
                DashboardAction(label="Post comments", command=run.post_command),
            ]
            evidence = [
                DashboardEvidence(
                    kind="review",
                    ref=run.preflight_path or run.run_id,
                    summary=summary or "AgentPack PR review run.",
                    path=run.preflight_path,
                )
            ]
            if run.understanding_path:
                evidence.append(DashboardEvidence(kind="understanding", path=run.understanding_path, summary="Review understanding artifact."))
            if run.findings_path:
                evidence.append(DashboardEvidence(kind="findings", path=run.findings_path, summary="Review findings artifact."))
            self._add_node(
                DashboardNode(
                    id=node_id,
                    type="review",
                    label=_clip(title, 64),
                    status=run.status,
                    summary=summary or run.review_context,
                    metadata={
                        "run_id": run.run_id,
                        "target_number": run.target_number,
                        "target_url": run.target_url,
                        "branch_prefix": run.branch_prefix,
                        "generated_at": run.generated_at,
                    },
                    evidence=evidence,
                    actions=[action for action in actions if action.command],
                )
            )
            self._add_edge(
                DashboardEdge(
                    id=f"edge:task:{node_id}:review",
                    source="task:active",
                    target=node_id,
                    type="reviewed_by",
                    label="review",
                    confidence=0.85,
                    reason=summary or "AgentPack review is associated with this task context.",
                    evidence=evidence[:1],
                )
            )

    def add_memory_timeline(self, root: Path) -> None:
        try:
            rows = build_memory_timeline(root, limit=MAX_MEMORY_ROWS)
        except Exception:
            rows = []
        procedure_nodes: set[str] = set()
        episode_nodes: set[str] = set()

        for row in rows:
            kind = str(row.get("kind") or "")
            row_id = str(row.get("id") or "")
            if not row_id:
                continue
            if kind == "episode":
                episode_id = f"episode:{row_id}"
                episode_nodes.add(episode_id)
                self._add_node(
                    DashboardNode(
                        id=episode_id,
                        type="episode",
                        label=_clip(row_id.replace("episode:", "") or "episode", 48),
                        stale=bool(row.get("is_stale")),
                        summary=str(row.get("visible_reason") or "Prior task episode."),
                        metadata=_metadata(row, "timestamp", "version", "task_id", "record_hash"),
                        evidence=[
                            DashboardEvidence(
                                kind="memory",
                                ref=row_id,
                                summary=str(row.get("visible_reason") or "Prior task episode."),
                            )
                        ],
                    )
                )
            elif kind == "procedure":
                procedure_id = f"procedure:{row_id}"
                procedure_nodes.add(procedure_id)
                self._add_node(
                    DashboardNode(
                        id=procedure_id,
                        type="procedure",
                        label=_clip(row_id.replace("procedure:", "") or "procedure", 48),
                        stale=bool(row.get("is_stale")),
                        summary=str(row.get("visible_reason") or "Procedure memory."),
                        metadata=_metadata(row, "timestamp", "version", "record_hash"),
                        evidence=[
                            DashboardEvidence(
                                kind="procedure",
                                ref=row_id,
                                summary=str(row.get("visible_reason") or "Procedure memory."),
                            )
                        ],
                    )
                )
                self._add_edge(
                    DashboardEdge(
                        id=f"edge:{procedure_id}:task",
                        source=procedure_id,
                        target="task:active",
                        type="procedure_applies",
                        label="procedure",
                        confidence=_float(row.get("confidence")),
                        reason=str(row.get("visible_reason") or "Procedure may apply to this task."),
                        stale=bool(row.get("is_stale")),
                    )
                )

        for row in rows:
            if str(row.get("kind") or "") != "memory_edge":
                continue
            from_id = str(row.get("from_id") or "")
            to_id = str(row.get("to_id") or "")
            edge_type = str(row.get("edge_type") or "memory")
            source = _known_memory_node(from_id, episode_nodes, procedure_nodes)
            target = _known_memory_node(to_id, episode_nodes, procedure_nodes)
            if source and target:
                self._add_edge(
                    DashboardEdge(
                        id=f"edge:memory:{source}:{target}:{edge_type}",
                        source=source,
                        target=target,
                        type="memory_influenced",
                        label=edge_type,
                        confidence=_float(row.get("confidence")),
                        reason=str(row.get("visible_reason") or edge_type),
                        stale=bool(row.get("is_stale")),
                    )
                )

    def add_learning_memories(self) -> None:
        file_node_ids = {node.path: node.id for node in self.nodes.values() if node.type == "file" and node.path}
        symbol_nodes: dict[str, list[DashboardNode]] = {}
        for node in self.nodes.values():
            if node.type != "symbol":
                continue
            symbol_file = str(node.metadata.get("file") or "")
            if symbol_file:
                symbol_nodes.setdefault(symbol_file, []).append(node)
        for memory in self.snapshot.learning_memories[:20]:
            memory_id = "episode:task-memory:" + _slug(memory.task or memory.git_sha or "memory")
            self._add_node(
                DashboardNode(
                    id=memory_id,
                    type="episode",
                    label=_clip(memory.task or "Task memory", 56),
                    status=memory.status,
                    summary=memory.task,
                    metadata={"stage": memory.stage, "branch": memory.branch, "git_sha": memory.git_sha},
                    evidence=[DashboardEvidence(kind="task_memory", summary=memory.task)],
                )
            )
            for path in _unique_strings([*memory.changed_files, *memory.selected_files]):
                file_id = file_node_ids.get(path)
                if file_id:
                    self._add_edge(
                        DashboardEdge(
                            id=f"edge:{memory_id}:{file_id}",
                            source=memory_id,
                            target=file_id,
                            type="memory_influenced",
                            label="memory",
                            confidence=0.7,
                            reason=f"Recent task memory referenced {path}.",
                            evidence=[DashboardEvidence(kind="task_memory", summary=memory.task, path=path)],
                        )
                    )
                for symbol_node in symbol_nodes.get(path, []):
                    cues = _memory_symbol_cues(memory, symbol_node)
                    if not cues:
                        continue
                    self._add_edge(
                        DashboardEdge(
                            id=f"edge:{memory_id}:{symbol_node.id}:symbol-memory",
                            source=memory_id,
                            target=symbol_node.id,
                            type="memory_influenced",
                            label="memory",
                            confidence=0.75,
                            reason=f"Task memory referenced {path} and matched {', '.join(cues[:3])}.",
                            evidence=[
                                DashboardEvidence(
                                    kind="task_memory",
                                    ref=", ".join(cues[:3]),
                                    summary=memory.task,
                                    path=path,
                                    line=_metadata_int(symbol_node.metadata.get("start_line")),
                                )
                            ],
                        )
                    )

    def graph(self) -> DashboardGraph:
        nodes = sorted(self.nodes.values(), key=_node_sort_key)
        edges = [edge for edge in self.edges.values() if edge.source in self.nodes and edge.target in self.nodes]
        edges = sorted(edges, key=lambda edge: (edge.source, edge.target, edge.type, edge.id))
        selected_files = sum(1 for node in nodes if node.type == "file" and node.selected)
        omitted_files = sum(1 for node in nodes if node.type == "file" and not node.selected)
        memory_nodes = sum(1 for node in nodes if node.type in {"episode", "procedure"})
        high_risk_files = sum(1 for node in nodes if node.type == "file" and node.risk == "high")
        return DashboardGraph(
            generated_at=datetime.now(timezone.utc).isoformat(),
            summary=DashboardGraphSummary(
                node_count=len(nodes),
                edge_count=len(edges),
                selected_files=selected_files,
                omitted_files=omitted_files,
                memory_nodes=memory_nodes,
                high_risk_files=high_risk_files,
                max_nodes=self.max_nodes,
                truncated_reason="node limit reached" if self.truncated else "",
                truncated=self.truncated,
            ),
            nodes=nodes,
            edges=edges,
        )

    def _add_file_node(self, item: TaskMapFileRow, *, selected: bool) -> None:
        file_id = "file:" + item.path
        reasons = item.why_selected or item.risk_reasons
        self._add_node(
            DashboardNode(
                id=file_id,
                type="file",
                label=_basename(item.path),
                path=item.path,
                status="selected" if selected else "omitted",
                risk=item.risk_level,
                selected=selected,
                score=item.score,
                summary="; ".join(reasons[:3]),
                metadata={"kind": item.kind, "include_mode": item.include_mode, "retrieve_ref": item.retrieve_ref},
                evidence=[
                    DashboardEvidence(
                        kind="task_map",
                        ref=item.retrieve_ref,
                        summary="; ".join(reasons[:3]) or f"{item.kind or 'candidate'} context file.",
                        path=item.path,
                    )
                ],
                actions=_file_actions(item),
            )
        )
        self._add_edge(
            DashboardEdge(
                id=f"edge:task:{file_id}",
                source="task:active",
                target=file_id,
                type="selected_because" if selected else "omitted_because",
                label="selected" if selected else "omitted",
                confidence=_confidence(item.score),
                reason="; ".join(reasons[:3]) or f"{item.kind or 'candidate'} context file.",
                evidence=[DashboardEvidence(kind="task_map", ref=item.retrieve_ref, path=item.path)],
            )
        )
        for index, test in enumerate(item.tests_to_run[:4], start=1):
            test_id = "test:" + test
            self._add_node(
                DashboardNode(
                    id=test_id,
                    type="test",
                    label=_basename(test),
                    path=test,
                    summary=f"Suggested validation for {item.path}.",
                    actions=[DashboardAction(label="Run test", command=_test_command(test))],
                    evidence=[DashboardEvidence(kind="task_map", summary=f"tests_to_run for {item.path}", path=item.path)],
                )
            )
            self._add_edge(
                DashboardEdge(
                    id=f"edge:{file_id}:test:{index}:{test}",
                    source=file_id,
                    target=test_id,
                    type="tested_by",
                    label="tested by",
                    confidence=0.8,
                    reason=f"Task map suggests running {test}.",
                )
            )
        for index, impact in enumerate(item.may_break[:3], start=1):
            impact_id = f"action:impact:{item.path}:{index}"
            self._add_node(
                DashboardNode(
                    id=impact_id,
                    type="action",
                    label=_clip(impact, 64),
                    risk=item.risk_level,
                    summary=impact,
                    evidence=[DashboardEvidence(kind="risk", summary=impact, path=item.path)],
                )
            )
            self._add_edge(
                DashboardEdge(
                    id=f"edge:{file_id}:impact:{index}",
                    source=file_id,
                    target=impact_id,
                    type="may_break",
                    label="may break",
                    confidence=0.6,
                    reason=impact,
                )
            )

    def _add_symbol_nodes(self, selected: SelectedFileRow) -> None:
        if not selected.symbols:
            return
        file_id = "file:" + selected.path
        for symbol in selected.symbols:
            if self.symbol_nodes_added >= MAX_SYMBOL_NODES:
                self.truncated = True
                break
            symbol_id = _symbol_node_id(selected.path, symbol)
            line_label = _line_label(symbol)
            already_present = symbol_id in self.nodes
            self._add_node(
                DashboardNode(
                    id=symbol_id,
                    type="symbol",
                    label=_clip(symbol.signature or symbol.name, 72),
                    path=selected.path,
                    status=selected.include_mode,
                    selected=True,
                    score=selected.score,
                    summary=symbol.summary or symbol.signature or symbol.name,
                    metadata={
                        "file": selected.path,
                        "symbol": symbol.name,
                        "kind": symbol.kind,
                        "start_line": symbol.start_line,
                        "end_line": symbol.end_line,
                        "signature_hash": symbol.signature_hash,
                        "source_hash": symbol.source_hash,
                    },
                    evidence=[
                        DashboardEvidence(
                            kind="symbol",
                            ref=line_label,
                            summary=symbol.signature or symbol.summary or f"{symbol.kind or 'symbol'} {symbol.name}",
                            path=selected.path,
                            line=symbol.start_line or None,
                        )
                    ],
                    actions=[
                        DashboardAction(
                            label="Open symbol",
                            command=f"{selected.path}:{symbol.start_line}" if symbol.start_line else selected.path,
                            kind="path",
                        )
                    ],
                )
            )
            if symbol_id in self.nodes and not already_present:
                self.symbol_nodes_added += 1
            self._add_edge(
                DashboardEdge(
                    id=f"edge:{file_id}:{symbol_id}:contains",
                    source=file_id,
                    target=symbol_id,
                    type="contains",
                    label="contains",
                    confidence=0.9,
                    reason=f"{selected.path} contains {symbol.kind or 'symbol'} {symbol.name}.",
                    evidence=[
                        DashboardEvidence(
                            kind="symbol",
                            ref=line_label,
                            summary=symbol.signature or symbol.summary or symbol.name,
                            path=selected.path,
                            line=symbol.start_line or None,
                        )
                    ],
                )
            )

    def _add_node(self, node: DashboardNode) -> None:
        if node.id in self.nodes:
            existing = self.nodes[node.id]
            if node.evidence:
                existing.evidence.extend(node.evidence)
            if node.actions:
                existing.actions.extend(node.actions)
            return
        if len(self.nodes) >= self.max_nodes:
            self.truncated = True
            return
        self.nodes[node.id] = node

    def _add_edge(self, edge: DashboardEdge) -> None:
        if edge.id not in self.edges:
            self.edges[edge.id] = edge


def _known_memory_node(value: str, episodes: set[str], procedures: set[str]) -> str:
    candidates = [value, f"episode:{value}", f"procedure:{value}"]
    for candidate in candidates:
        if candidate in episodes or candidate in procedures:
            return candidate
    return ""


def _node_sort_key(node: DashboardNode) -> tuple[int, str, str]:
    order = {
        "task": 0,
        "file": 1,
        "symbol": 2,
        "episode": 3,
        "procedure": 4,
        "review": 5,
        "test": 6,
        "action": 7,
    }
    selected_rank = 0 if node.selected else 1
    risk_rank = {"high": 0, "medium": 1, "low": 2}.get(node.risk, 3)
    return (order.get(node.type, 99), f"{selected_rank}:{risk_rank}", node.path or node.label or node.id)


def _file_actions(item: TaskMapFileRow) -> list[DashboardAction]:
    actions = [DashboardAction(label="Open file", command=item.path, kind="path")]
    if item.retrieve_ref:
        actions.append(
            DashboardAction(
                label="Retrieve context",
                command=f'agentpack retrieve --block-id "{item.retrieve_ref}"',
                kind="command",
            )
        )
    for test in item.tests_to_run[:2]:
        actions.append(DashboardAction(label=f"Run {_basename(test)}", command=_test_command(test)))
    return actions


def _symbol_node_id(path: str, symbol: SelectedSymbolRow) -> str:
    if symbol.node_id:
        return "symbol:" + symbol.node_id
    return f"symbol:{path}:{symbol.name}:{symbol.start_line}:{symbol.end_line}"


def _line_label(symbol: SelectedSymbolRow) -> str:
    if symbol.start_line and symbol.end_line and symbol.end_line != symbol.start_line:
        return f"L{symbol.start_line}-L{symbol.end_line}"
    if symbol.start_line:
        return f"L{symbol.start_line}"
    return ""


def _task_title(task: str) -> str:
    value = " ".join(str(task or "No active task").split())
    if len(value) <= 58:
        return value
    for separator in (":", " - ", " — ", ".", ";"):
        head = value.split(separator, 1)[0].strip()
        if 18 <= len(head) <= 58:
            return head
    words = value.split()
    title: list[str] = []
    for word in words:
        candidate = " ".join([*title, word])
        if len(candidate) > 58:
            break
        title.append(word)
    return " ".join(title) or _clip(value, 58)


def _memory_symbol_cues(memory: Any, symbol_node: DashboardNode) -> list[str]:
    symbol_text = " ".join(
        str(value or "")
        for value in (
            symbol_node.metadata.get("symbol"),
            symbol_node.metadata.get("kind"),
            symbol_node.label,
            symbol_node.summary,
        )
    ).lower()
    cues: list[str] = []
    for concept in getattr(memory, "concepts", []) or []:
        concept_text = str(concept).strip().lower()
        if concept_text and any(variant in symbol_text for variant in _concept_variants(concept_text)):
            cues.append(f"concept:{concept_text}")

    memory_task = str(getattr(memory, "task", "") or "").lower()
    symbol_name = str(symbol_node.metadata.get("symbol") or symbol_node.label or "")
    for token in _identifier_tokens(symbol_name):
        if token in memory_task:
            cues.append(f"symbol:{token}")

    return _unique_strings(cues)


def _identifier_tokens(value: str) -> list[str]:
    normalized = value.replace("_", " ").replace("-", " ")
    return [token for token in (part.lower() for part in normalized.split()) if len(token) >= 3]


def _concept_variants(value: str) -> set[str]:
    variants = {value}
    if value.endswith("ing") and len(value) > 5:
        stem = value[:-3]
        variants.add(stem)
        variants.add(stem + "e")
    return {variant for variant in variants if len(variant) >= 3}


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _metadata_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number or None


def _test_command(path: str) -> str:
    return f"pytest {path}" if path.endswith(".py") or "/test" in path else path


def _metadata(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if row.get(key) not in (None, "")}


def _confidence(score: float) -> float:
    if score <= 0:
        return 0.0
    return min(1.0, max(0.05, score / 100.0))


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _slug(value: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return "-".join(part for part in clean.split("-") if part)[:80] or "memory"


def _basename(path: str) -> str:
    return path.rstrip("/").split("/")[-1] or path


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."
