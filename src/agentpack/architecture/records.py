"""Persisted contracts for incremental semantic graph materialization."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LocalSemanticRecord(BaseModel):
    """Complete file-owned semantic facts and materialized graph payload."""

    schema_version: int
    extractor_profile_hash: str
    repository_identity: str
    path: str
    content_hash: str
    language: str
    record_key: str
    facts: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    file_entity: dict[str, Any]
    symbol_entities: list[dict[str, Any]] = Field(default_factory=list)
    comment_entities: list[dict[str, Any]] = Field(default_factory=list)
    document_entities: list[dict[str, Any]] = Field(default_factory=list)
    local_entities: list[dict[str, Any]] = Field(default_factory=list)
    relationship_entities: list[dict[str, Any]] = Field(default_factory=list)
    local_edges: list[dict[str, Any]] = Field(default_factory=list)
    raw_relationship_candidates: list[dict[str, Any]] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)
    reexports: list[str] = Field(default_factory=list)
    aliases: list[Any] = Field(default_factory=list)
    declared_symbols: list[str] = Field(default_factory=list)
    referenced_names: list[str] = Field(default_factory=list)
    source_evidence: list[Any] = Field(default_factory=list)
    owned_entity_keys: list[str] = Field(default_factory=list)
    owned_edge_keys: list[str] = Field(default_factory=list)


class GraphManifest(BaseModel):
    """Deterministic repository/ref/profile manifest for cache validation."""

    schema_version: int
    repository_identity: str
    ref: str
    commit_sha: str
    extractor_profile_hash: str
    files: dict[str, dict[str, Any]] = Field(default_factory=dict)
    record_keys: dict[str, str] = Field(default_factory=dict)
    reverse_dependencies: dict[str, dict[str, list[str]]] = Field(default_factory=dict)


class MaterializedGraphState(BaseModel):
    """Ownership metadata used for record-level delta merges.

    New states point at the canonical snapshot file. ``snapshot`` remains
    optional so older embedded-snapshot states remain readable during rollout.
    """

    schema_version: int
    repository_identity: str
    ref: str
    commit_sha: str
    extractor_profile_hash: str
    manifest: GraphManifest
    record_keys: dict[str, str] = Field(default_factory=dict)
    entity_owners: dict[str, str] = Field(default_factory=dict)
    edge_owners: dict[str, str] = Field(default_factory=dict)
    snapshot: dict[str, Any] | None = None
    snapshot_path: str | None = None
