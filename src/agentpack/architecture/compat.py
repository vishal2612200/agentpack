"""Compatibility adapters for legacy file-only graph APIs."""

from dataclasses import dataclass

from agentpack.architecture.index import SemanticGraphIndex
from agentpack.core.models import DependencyGraph


@dataclass(frozen=True)
class LegacyGraphQuery:
    """Read-only semantic-query-shaped facade over a legacy graph."""

    graph: DependencyGraph

    def file_relations(self, path: str) -> dict[str, list[str]]:
        node = self.graph.get(path)
        return {
            "imports": list(node.imports),
            "imported_by": list(node.imported_by),
            "tests": list(node.tests),
        }

    def relationship_receipts(self, path: str, *, limit: int = 50) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        relations = self.file_relations(path)
        rows: list[dict] = []
        for relationship, paths in (("imports", relations["imports"]), ("imported_by", relations["imported_by"]), ("tested_by", relations["tests"])):
            for target in paths:
                rows.append({
                    "edge_key": "",
                    "relationship": relationship,
                    "source_entity_key": path,
                    "target_entity_key": target,
                    "source": path,
                    "target": target,
                    "confidence_tier": "file_level",
                    "source_line": None,
                    "source_end_line": None,
                    "evidence_reference": "",
                    "evidence": [],
                })
        return rows[:limit]


def to_dependency_graph(graph: SemanticGraphIndex) -> DependencyGraph:
    """Project the canonical semantic graph for legacy callers only."""
    return graph.to_dependency_graph()
