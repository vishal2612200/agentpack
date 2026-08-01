from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr


CapabilityTier = Literal["structured", "best_effort", "file_level", "unavailable"]
EntityType = Literal[
    "domain",
    "module",
    "symbol",
    "api",
    "schema",
    "queue",
    "config",
    "test",
    "comment",
    "document",
    "external",
    "unresolved",
]
EdgeType = Literal[
    "contains",
    "imports",
    "calls",
    "references",
    "inherits",
    "implements",
    "tested_by",
    "documents",
    "configures",
    "reads_from",
    "writes_to",
    "publishes",
    "consumes",
    "declared_dependency",
]


class ArchitectureLocator(BaseModel):
    path: str
    start_line: int | None = None
    end_line: int | None = None


class ArchitectureEvidence(BaseModel):
    kind: str
    source: str
    confidence_tier: CapabilityTier
    confidence: float = 0.0
    path: str
    start_line: int | None = None
    end_line: int | None = None
    source_hash: str = ""
    note: str = ""


class ArchitectureEntity(BaseModel):
    entity_key: str
    revision_id: str
    entity_type: EntityType
    qualified_name: str
    display_name: str
    normalized_signature: str
    language: str | None = None
    locator: ArchitectureLocator
    provenance: str
    confidence_tier: CapabilityTier
    source_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[ArchitectureEvidence] = Field(default_factory=list)


class ArchitectureEdge(BaseModel):
    edge_key: str
    revision_id: str
    edge_type: EdgeType
    source_entity_key: str
    target_entity_key: str
    confidence_tier: CapabilityTier
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[ArchitectureEvidence] = Field(default_factory=list)


class EntityChange(BaseModel):
    entity_key: str
    before_revision_id: str
    after_revision_id: str
    before_path: str
    after_path: str
    before_confidence_tier: CapabilityTier
    after_confidence_tier: CapabilityTier
    revision_changed: bool
    locator_changed: bool


class EdgeChange(BaseModel):
    edge_key: str
    before_revision_id: str
    after_revision_id: str
    before_confidence_tier: CapabilityTier
    after_confidence_tier: CapabilityTier


class ArchitectureAlias(BaseModel):
    before_entity_key: str
    after_entity_key: str
    reason: str
    before_path: str
    after_path: str
    confidence: float = 0.0
    status: Literal["confirmed", "ambiguous"] = "confirmed"
    evidence: list[ArchitectureEvidence] = Field(default_factory=list)


class ArchitectureSnapshot(BaseModel):
    schema_version: int
    ref: str
    commit_sha: str
    repo_fingerprint: str
    extractor_profile_hash: str
    capabilities: dict[str, CapabilityTier]
    entities: list[ArchitectureEntity]
    edges: list[ArchitectureEdge]
    file_hashes: dict[str, str] = Field(default_factory=dict)
    _cache_stats: dict[str, Any] = PrivateAttr(default_factory=dict)

    @property
    def cache_stats(self) -> dict[str, Any]:
        """Runtime extraction counters; excluded from canonical serialization."""
        return self._cache_stats


class ArchitectureDiff(BaseModel):
    base_ref: str
    head_ref: str
    added_entities: list[ArchitectureEntity] = Field(default_factory=list)
    removed_entities: list[ArchitectureEntity] = Field(default_factory=list)
    changed_entities: list[EntityChange] = Field(default_factory=list)
    aliased_entities: list[ArchitectureAlias] = Field(default_factory=list)
    added_edges: list[ArchitectureEdge] = Field(default_factory=list)
    removed_edges: list[ArchitectureEdge] = Field(default_factory=list)
    changed_edges: list[EdgeChange] = Field(default_factory=list)
    affected_domains: list[str] = Field(default_factory=list)
    test_impact: list[str] = Field(default_factory=list)
    changed_confidence: list[str] = Field(default_factory=list)


class ArchitectureInvariant(BaseModel):
    """Portable invariant declaration used by local checks and CI configuration."""

    id: str
    kind: Literal["forbid_edge", "require_test", "require_consumer_update"]
    enforcement: Literal["block", "warn"] = "warn"
    description: str = ""
    owner: str = ""
    enabled: bool = True
    edge_types: list[EdgeType] = Field(default_factory=lambda: ["imports"])
    min_confidence: CapabilityTier = "best_effort"
    source: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)


class ArchitectureViolation(BaseModel):
    invariant_id: str
    kind: str
    enforcement: Literal["block", "warn"]
    requested_enforcement: Literal["block", "warn"] = "warn"
    description: str = ""
    owner: str = ""
    message: str
    blocking: bool
    suppression_reason: str = ""
    entity_keys: list[str] = Field(default_factory=list)
    edge_keys: list[str] = Field(default_factory=list)
    evidence: list[ArchitectureEvidence] = Field(default_factory=list)


class ArchitectureCheckResult(BaseModel):
    schema_version: int = 2
    base_sha: str = ""
    head_sha: str = ""
    policy_mode: Literal["off", "warn", "enforce"] = "warn"
    diff: ArchitectureDiff
    violations: list[ArchitectureViolation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
