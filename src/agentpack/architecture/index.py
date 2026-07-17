from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from agentpack.architecture.models import ArchitectureEdge, ArchitectureEntity, ArchitectureSnapshot
from agentpack.core.models import DependencyGraph, DependencyNode


@dataclass(frozen=True)
class GraphHit:
    entity: ArchitectureEntity
    score: int


class SemanticGraphIndex:
    """Bounded query facade over an ArchitectureSnapshot."""

    def __init__(self, snapshot: ArchitectureSnapshot) -> None:
        self.snapshot = snapshot
        self.entities = {entity.entity_key: entity for entity in snapshot.entities}
        self.edges = {edge.edge_key: edge for edge in snapshot.edges}
        self._name_index: dict[str, list[ArchitectureEntity]] = {}
        for entity in snapshot.entities:
            for value in {entity.entity_key, entity.qualified_name, entity.display_name, entity.locator.path}:
                self._name_index.setdefault(value.lower(), []).append(entity)

    def query(self, text: str, *, limit: int = 20, entity_type: str = "") -> list[GraphHit]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        terms = [term for term in text.lower().split() if term]
        hits: list[GraphHit] = []
        for entity in self.snapshot.entities:
            if entity_type and entity.entity_type != entity_type:
                continue
            haystack = " ".join((entity.qualified_name, entity.display_name, entity.locator.path, entity.normalized_signature)).lower()
            score = sum(3 if term == entity.display_name.lower() else 1 for term in terms if term in haystack)
            if score:
                hits.append(GraphHit(entity, score))
        return sorted(hits, key=lambda hit: (-hit.score, hit.entity.entity_type, hit.entity.qualified_name))[:max(1, limit)]

    def resolve(self, name: str) -> list[ArchitectureEntity]:
        exact = self._name_index.get(name.lower(), [])
        if exact:
            return exact
        return [hit.entity for hit in self.query(name, limit=20)]

    def file_relations(self, path: str) -> dict[str, list[str]]:
        """Return the file-level compatibility view without materializing it."""
        file_keys = {
            entity.entity_key
            for entity in self.snapshot.entities
            if entity.locator.path == path
            and entity.entity_type in {"module", "config", "test", "document"}
        }
        relations = {"imports": set(), "imported_by": set(), "tests": set()}
        if not file_keys:
            return {key: [] for key in relations}
        entities = self.entities
        for edge in self.snapshot.edges:
            if edge.edge_type == "imports":
                if edge.source_entity_key in file_keys:
                    target = entities.get(edge.target_entity_key)
                    if target is not None:
                        relations["imports"].add(target.locator.path)
                if edge.target_entity_key in file_keys:
                    source = entities.get(edge.source_entity_key)
                    if source is not None:
                        relations["imported_by"].add(source.locator.path)
            elif edge.edge_type == "tested_by" and edge.source_entity_key in file_keys:
                target = entities.get(edge.target_entity_key)
                if target is not None:
                    relations["tests"].add(target.locator.path)
        return {key: sorted(value) for key, value in relations.items()}

    def relationship_receipts(self, path: str, *, limit: int = 50) -> list[dict]:
        """Return bounded file-level relationship evidence for ranking and task maps."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        file_keys = {
            entity.entity_key
            for entity in self.snapshot.entities
            if entity.locator.path == path
            and entity.entity_type in {"module", "config", "test", "document"}
        }
        rows: list[dict] = []
        for edge in self.snapshot.edges:
            if edge.source_entity_key not in file_keys and edge.target_entity_key not in file_keys:
                continue
            source = self.entities.get(edge.source_entity_key)
            target = self.entities.get(edge.target_entity_key)
            if source is None or target is None:
                continue
            rows.append(
                {
                    "edge_key": edge.edge_key,
                    "relationship": edge.edge_type,
                    "source_entity_key": source.entity_key,
                    "target_entity_key": target.entity_key,
                    "source": source.locator.path,
                    "target": target.locator.path,
                    "confidence_tier": edge.confidence_tier,
                    "source_line": edge.evidence[0].start_line if edge.evidence else None,
                    "source_end_line": edge.evidence[0].end_line if edge.evidence else None,
                    "evidence_reference": edge.evidence[0].source_hash if edge.evidence else "",
                    "evidence": [item.model_dump(mode="json") for item in edge.evidence],
                }
            )
        return sorted(rows, key=lambda row: (row["relationship"], row["source"], row["target"], row["edge_key"]))[:limit]

    def neighbors(self, node: str, *, relationship: str = "", direction: str = "both", limit: int = 50) -> list[dict]:
        if direction not in {"in", "out", "both"}:
            raise ValueError("direction must be 'in', 'out', or 'both'")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        entities = self.resolve(node)
        if not entities:
            return []
        keys = {entity.entity_key for entity in entities}
        rows: list[dict] = []
        for edge in self.snapshot.edges:
            matched = (direction in {"out", "both"} and edge.source_entity_key in keys) or (direction in {"in", "both"} and edge.target_entity_key in keys)
            if not matched or (relationship and edge.edge_type != relationship):
                continue
            other_key = edge.target_entity_key if edge.source_entity_key in keys else edge.source_entity_key
            other = self.entities.get(other_key)
            if other is None:
                continue
            rows.append(self._edge_row(edge, other))
        return sorted(rows, key=lambda row: (row["relationship"], row["node"]["qualified_name"]))[:max(1, limit)]

    def shortest_path(self, source: str, target: str, *, max_hops: int = 8) -> list[dict]:
        if max_hops < 1:
            raise ValueError("max_hops must be at least 1")
        sources = self.resolve(source)
        targets = {entity.entity_key for entity in self.resolve(target)}
        if not sources or not targets:
            return []
        queue: deque[tuple[str, list[str]]] = deque((entity.entity_key, [entity.entity_key]) for entity in sources)
        visited = {entity.entity_key for entity in sources}
        outgoing: dict[str, list[ArchitectureEdge]] = {}
        for edge in self.snapshot.edges:
            outgoing.setdefault(edge.source_entity_key, []).append(edge)
            outgoing.setdefault(edge.target_entity_key, []).append(edge)
        while queue:
            current, path = queue.popleft()
            if current in targets:
                rows: list[dict] = []
                for left, right in zip(path, path[1:]):
                    edge = next((candidate for candidate in outgoing.get(left, []) if {candidate.source_entity_key, candidate.target_entity_key} == {left, right}), None)
                    if edge is not None and right in self.entities:
                        rows.append(self._edge_row(edge, self.entities[right]))
                return rows
            if len(path) - 1 >= max_hops:
                continue
            for edge in outgoing.get(current, []):
                other = edge.target_entity_key if edge.source_entity_key == current else edge.source_entity_key
                if other in visited:
                    continue
                visited.add(other)
                queue.append((other, [*path, other]))
        return []

    def explain_edge(self, edge_key: str) -> dict | None:
        edge = self.edges.get(edge_key)
        if edge is None:
            return None
        source = self.entities.get(edge.source_entity_key)
        target = self.entities.get(edge.target_entity_key)
        if source is None or target is None:
            return None
        return {
            "edge": self._edge_row(edge, target),
            "source": source.model_dump(mode="json"),
            "target": target.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in edge.evidence],
        }

    def to_dependency_graph(self) -> DependencyGraph:
        """Return the compatibility file projection used by legacy pack APIs."""
        file_entities = {
            entity.entity_key: entity
            for entity in self.snapshot.entities
            if entity.entity_type in {"module", "config", "test", "document"}
        }
        nodes = {entity.locator.path: DependencyNode(path=entity.locator.path) for entity in file_entities.values()}
        paths = {key: entity.locator.path for key, entity in file_entities.items()}
        for edge in self.snapshot.edges:
            source = paths.get(edge.source_entity_key)
            target = paths.get(edge.target_entity_key)
            if source is None or target is None or source not in nodes or target not in nodes:
                continue
            if edge.edge_type == "imports":
                if target not in nodes[source].imports:
                    nodes[source].imports.append(target)
                if source not in nodes[target].imported_by:
                    nodes[target].imported_by.append(source)
            elif edge.edge_type == "tested_by" and target not in nodes[source].tests:
                nodes[source].tests.append(target)
        for node in nodes.values():
            node.imports.sort()
            node.imported_by.sort()
            node.tests.sort()
        return DependencyGraph(nodes=nodes)

    def _edge_row(self, edge: ArchitectureEdge, other: ArchitectureEntity) -> dict:
        return {
            "edge_key": edge.edge_key,
            "relationship": edge.edge_type,
            "node": other.model_dump(mode="json"),
            "confidence_tier": edge.confidence_tier,
            "metadata": edge.metadata,
            "evidence": [item.model_dump(mode="json") for item in edge.evidence],
        }
