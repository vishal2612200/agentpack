"""Incremental materialization for the canonical semantic graph.

The store deliberately keeps runtime cache state outside ``ArchitectureSnapshot``.
That makes snapshots reproducible while allowing the builder to report useful
cache and invalidation diagnostics to callers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agentpack.architecture.models import ArchitectureSnapshot
from agentpack.architecture.records import GraphManifest, LocalSemanticRecord, MaterializedGraphState
from agentpack.architecture.semantic_graph import (
    SEMANTIC_SCHEMA_VERSION,
    _atomic_write_json,
    _facts_cache_path,
    _invalidation_sets,
    _load_cache_manifest,
    _manifest_for_files,
    build_semantic_graph,
)
from agentpack.core.models import FileInfo


@dataclass
class GraphBuildStats:
    files_total: int = 0
    parsed_files: int = 0
    reused_records: int = 0
    affected_files: int = 0
    re_resolved_relationships: int = 0
    removed_entities: int = 0
    removed_edges: int = 0
    incremental_build_seconds: float = 0.0
    cold_build_seconds: float = 0.0
    fallback_to_cold: bool = False
    build_mode: str = "cold"
    changed_files: int = 0
    deleted_files: int = 0
    cache_invalid_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "files_total": self.files_total,
            "parsed_files": self.parsed_files,
            "reused_records": self.reused_records,
            "reused_files": self.reused_records,
            "affected_files": self.affected_files,
            "re_resolved_relationships": self.re_resolved_relationships,
            "removed_entities": self.removed_entities,
            "removed_edges": self.removed_edges,
            "incremental_build_seconds": round(self.incremental_build_seconds, 6),
            "cold_build_seconds": round(self.cold_build_seconds, 6),
            "fallback_to_cold": self.fallback_to_cold,
            "build_mode": self.build_mode,
            "changed_files": self.changed_files,
            "deleted_files": self.deleted_files,
            "cache_invalid_reason": self.cache_invalid_reason,
        }


@dataclass
class GraphBuildResult:
    snapshot: ArchitectureSnapshot
    stats: GraphBuildStats
    affected_paths: tuple[str, ...] = field(default_factory=tuple)
    mode: str = "cold"


class SemanticGraphStore:
    """Persist file records and materialize only the affected graph owners."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        schema_version: int = SEMANTIC_SCHEMA_VERSION,
        make_domain: Callable[[str], str] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.schema_version = schema_version
        self.make_domain = make_domain or (lambda path: Path(path).parts[0] if Path(path).parts else "root")
        self._last_invalid_reason: str | None = None

    def build(
        self,
        files: list[FileInfo],
        *,
        root: Path,
        repo_fingerprint: str,
        ref: str,
        commit_sha: str,
        capabilities: dict[str, str],
        extractor_profile_hash: str,
        cold: bool = False,
        verify_incremental: bool = False,
    ) -> GraphBuildResult:
        ordered_files = sorted(files, key=lambda item: item.path)
        current_manifest = _manifest_for_files(ordered_files)
        previous_state = self._load_state(repo_fingerprint, extractor_profile_hash, ref=ref)
        previous_snapshot = None
        if previous_state:
            try:
                previous_snapshot = ArchitectureSnapshot.model_validate(previous_state.get("snapshot") or {})
            except (ValueError, TypeError):
                previous_snapshot = None
        previous_manifest = previous_state.get("manifest") if previous_state else None
        if not isinstance(previous_manifest, dict):
            previous_manifest = _load_cache_manifest(self._facts_dir, repo_fingerprint, extractor_profile_hash)

        invalidation = _invalidation_sets(previous_manifest or {}, current_manifest)
        affected = set(invalidation["affected"])
        if not previous_snapshot or cold:
            mode = "cold"
            previous_snapshot = None
            affected = {item.path for item in ordered_files}
        else:
            mode = "incremental"
            if len(affected) / max(1, len(ordered_files)) > 0.40:
                mode = "cold"
                previous_snapshot = None
                affected = {item.path for item in ordered_files}

        stats = GraphBuildStats(
            files_total=len(ordered_files),
            affected_files=len(affected),
            changed_files=len(invalidation["changed"]),
            deleted_files=len(invalidation["deleted"]),
            build_mode=mode,
            cache_invalid_reason=self._last_invalid_reason,
        )
        cache_stats: dict[str, int] = {}
        started = time.perf_counter()
        entities, edges, file_hashes = build_semantic_graph(
            ordered_files,
            root,
            capabilities,
            repo_fingerprint,
            self.make_domain,
            facts_cache_dir=self._facts_dir,
            records_cache_dir=self._records_dir,
            extractor_profile_hash=extractor_profile_hash,
            cache_stats=cache_stats,
            previous_snapshot=previous_snapshot,
            affected_paths=affected,
        )
        elapsed = time.perf_counter() - started
        stats.parsed_files = int(cache_stats.get("parsed_files", 0))
        stats.reused_records = int(cache_stats.get("reused_files", 0))
        stats.affected_files = int(cache_stats.get("affected_files", len(affected)))
        stats.re_resolved_relationships = int(cache_stats.get("re_resolved_relationships", 0))
        if mode == "incremental":
            stats.incremental_build_seconds = elapsed
        else:
            stats.cold_build_seconds = elapsed

        snapshot = self._snapshot(
            ref=ref,
            commit_sha=commit_sha,
            repo_fingerprint=repo_fingerprint,
            extractor_profile_hash=extractor_profile_hash,
            capabilities=capabilities,
            entities=entities,
            edges=edges,
            file_hashes=file_hashes,
        )

        if mode == "incremental" and verify_incremental:
            cold_started = time.perf_counter()
            cold_stats: dict[str, int] = {}
            cold_entities, cold_edges, cold_hashes = build_semantic_graph(
                ordered_files,
                root,
                capabilities,
                repo_fingerprint,
                self.make_domain,
                facts_cache_dir=self._facts_dir,
                records_cache_dir=self._records_dir,
                extractor_profile_hash=extractor_profile_hash,
                cache_stats=cold_stats,
                previous_snapshot=None,
                affected_paths={item.path for item in ordered_files},
            )
            stats.cold_build_seconds = time.perf_counter() - cold_started
            cold_snapshot = self._snapshot(
                ref=ref,
                commit_sha=commit_sha,
                repo_fingerprint=repo_fingerprint,
                extractor_profile_hash=extractor_profile_hash,
                capabilities=capabilities,
                entities=cold_entities,
                edges=cold_edges,
                file_hashes=cold_hashes,
            )
            if snapshot.model_dump(mode="json") != cold_snapshot.model_dump(mode="json"):
                stats.fallback_to_cold = True
                stats.build_mode = "cold"
                snapshot = cold_snapshot
                stats.parsed_files = int(cold_stats.get("parsed_files", stats.parsed_files))
                stats.reused_records = int(cold_stats.get("reused_files", stats.reused_records))
                stats.affected_files = len(ordered_files)
                stats.cold_build_seconds = max(stats.cold_build_seconds, elapsed)

        stats.removed_entities, stats.removed_edges = self._removed_counts(previous_state, snapshot, affected)
        snapshot.cache_stats.update(stats.as_dict())
        self._persist(ordered_files, repo_fingerprint, extractor_profile_hash, snapshot)
        return GraphBuildResult(snapshot=snapshot, stats=stats, affected_paths=tuple(sorted(affected)), mode=stats.build_mode)

    @property
    def _facts_dir(self) -> Path:
        return self.cache_dir / "facts"

    @property
    def _records_dir(self) -> Path:
        return self.cache_dir / "records"

    @property
    def _manifests_dir(self) -> Path:
        return self.cache_dir / "manifests"

    @property
    def _state_dir(self) -> Path:
        return self.cache_dir / "state"

    def _state_path(self, repo_fingerprint: str, profile: str, ref: str = "WORKTREE") -> Path:
        return self._state_dir / f"{repo_fingerprint}-{profile}-{self._ref_namespace(ref)}.json"

    def _load_state(self, repo_fingerprint: str, profile: str, *, ref: str = "WORKTREE") -> dict | None:
        self._last_invalid_reason = None
        path = self._state_path(repo_fingerprint, profile, ref)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            snapshot = ArchitectureSnapshot.model_validate(payload.get("snapshot") or {})
        except (OSError, ValueError, TypeError):
            self._last_invalid_reason = "cache_invalid"
            return None
        if (
            payload.get("schema_version") != self.schema_version
            or payload.get("extractor_profile_hash") != profile
            or payload.get("repo_fingerprint") != repo_fingerprint
            or snapshot.schema_version != self.schema_version
            or snapshot.ref != ref
        ):
            self._last_invalid_reason = "schema_changed" if payload.get("schema_version") != self.schema_version or snapshot.schema_version != self.schema_version else "snapshot_mismatch"
            return None
        graph_manifest = payload.get("graph_manifest")
        if isinstance(graph_manifest, dict) and (
            graph_manifest.get("ref") != ref
            or graph_manifest.get("commit_sha") != snapshot.commit_sha
            or graph_manifest.get("repository_identity") != repo_fingerprint
        ):
            self._last_invalid_reason = "snapshot_mismatch"
            return None
        manifest = payload.get("manifest")
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != self.schema_version
            or manifest.get("repo_fingerprint", repo_fingerprint) != repo_fingerprint
            or manifest.get("extractor_profile_hash", profile) != profile
            or not isinstance(manifest.get("files"), dict)
        ):
            self._last_invalid_reason = "manifest_corrupt"
            return None
        record_keys = payload.get("record_keys")
        manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        if not isinstance(record_keys, dict) or set(record_keys) != set(manifest_files):
            self._last_invalid_reason = "manifest_corrupt"
            return None
        snapshot_entities = {entity.entity_key: entity for entity in snapshot.entities}
        snapshot_edges = {edge.edge_key: edge for edge in snapshot.edges}
        entity_owners = payload.get("entity_owners") if isinstance(payload.get("entity_owners"), dict) else {}
        edge_owners = payload.get("edge_owners") if isinstance(payload.get("edge_owners"), dict) else {}
        for path, record_key in record_keys.items():
            record_path = self._records_dir / f"{record_key}.json"
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record_model = LocalSemanticRecord.model_validate(record)
            except (OSError, ValueError, TypeError):
                self._last_invalid_reason = "record_corrupt"
                return None
            if (
                record.get("schema_version") != self.schema_version
                or record.get("extractor_profile_hash") != profile
                or record.get("repository_identity") != repo_fingerprint
                or record.get("path") != path
                or record.get("content_hash") != ((manifest.get("files") or {}).get(path) or {}).get("file_hash")
                or record_model.record_key != record_key
            ):
                self._last_invalid_reason = "record_corrupt"
                return None
            for value in record.get("local_edges", []):
                edge_key = value.get("edge_key") if isinstance(value, dict) else None
                if edge_key in snapshot_edges and snapshot_edges[edge_key].model_dump(mode="json") != value:
                    self._last_invalid_reason = "record_corrupt"
                    return None
            for collection_name in (
                "file_entity",
                "symbol_entities",
                "comment_entities",
                "document_entities",
                "local_entities",
                "relationship_entities",
            ):
                values = record.get(collection_name)
                values = values if isinstance(values, list) else [values]
                for value in values:
                    entity_key = value.get("entity_key") if isinstance(value, dict) else None
                    if entity_key in snapshot_entities and snapshot_entities[entity_key].model_dump(mode="json") != value:
                        self._last_invalid_reason = "record_corrupt"
                        return None
            for entity_key in record_model.owned_entity_keys:
                entity = snapshot_entities.get(entity_key)
                if entity is None or entity.locator.path != path or entity_owners.get(entity_key) != path:
                    self._last_invalid_reason = "record_corrupt"
                    return None
            for edge_key in record_model.owned_edge_keys:
                edge = snapshot_edges.get(edge_key)
                if edge is None or edge_owners.get(edge_key) != path:
                    self._last_invalid_reason = "record_corrupt"
                    return None
        return payload

    def _snapshot(self, **kwargs) -> ArchitectureSnapshot:
        return ArchitectureSnapshot(schema_version=self.schema_version, **kwargs)

    def validate_cached_snapshot(
        self,
        files: list[FileInfo],
        *,
        repo_fingerprint: str,
        extractor_profile_hash: str,
        snapshot: ArchitectureSnapshot,
    ) -> tuple[bool, str | None]:
        """Validate the materialized state before a snapshot fast path returns.

        Snapshot files are keyed by the worktree manifest, but that key cannot
        detect a record being truncated or manually removed.  Validate both
        the canonical snapshot and its state/record manifest here.
        """
        if snapshot.schema_version != self.schema_version:
            return False, "schema_changed"
        if snapshot.extractor_profile_hash != extractor_profile_hash:
            return False, "profile_changed"
        if snapshot.repo_fingerprint != repo_fingerprint:
            return False, "cache_invalid"
        current_hashes = {item.path: item.hash or "" for item in files}
        if snapshot.file_hashes != current_hashes:
            return False, "file_hash_changed"
        state = self._load_state(repo_fingerprint, extractor_profile_hash, ref=snapshot.ref)
        if state is None:
            return False, self._last_invalid_reason or "cache_invalid"
        try:
            state_snapshot = ArchitectureSnapshot.model_validate(state.get("snapshot") or {})
        except (ValueError, TypeError):
            return False, "cache_invalid"
        if state_snapshot.model_dump(mode="json") != snapshot.model_dump(mode="json"):
            return False, "cache_invalid"
        record_keys = state.get("record_keys")
        if not isinstance(record_keys, dict) or set(record_keys) != set(current_hashes):
            return False, "manifest_corrupt"
        snapshot_entities = {entity.entity_key: entity for entity in snapshot.entities}
        snapshot_edges = {edge.edge_key: edge for edge in snapshot.edges}
        entity_owners = state.get("entity_owners") if isinstance(state.get("entity_owners"), dict) else {}
        edge_owners = state.get("edge_owners") if isinstance(state.get("edge_owners"), dict) else {}
        for path, key in record_keys.items():
            if not isinstance(key, str) or not key:
                return False, "record_corrupt"
            record_path = self._records_dir / f"{key}.json"
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record_model = LocalSemanticRecord.model_validate(record)
            except (OSError, ValueError, TypeError):
                return False, "record_corrupt"
            if (
                record_model.record_key != key
                or record_model.schema_version != self.schema_version
                or record_model.extractor_profile_hash != extractor_profile_hash
                or record_model.repository_identity != repo_fingerprint
                or record.get("path") != path
                or record.get("content_hash") != current_hashes[path]
                or not isinstance(record.get("file_entity"), dict)
                or not isinstance(record.get("symbol_entities"), list)
                or not isinstance(record.get("local_edges"), list)
                or not isinstance(record.get("raw_relationship_candidates"), list)
            ):
                return False, "record_corrupt"
            for collection_name in (
                "file_entity",
                "symbol_entities",
                "comment_entities",
                "document_entities",
                "local_entities",
                "relationship_entities",
            ):
                values = record.get(collection_name)
                values = values if isinstance(values, list) else [values]
                for value in values:
                    if not isinstance(value, dict):
                        return False, "record_corrupt"
                    entity_key = value.get("entity_key")
                    if entity_key and entity_key in snapshot_entities:
                        if snapshot_entities[entity_key].model_dump(mode="json") != value:
                            return False, "record_corrupt"
            for value in record.get("local_edges", []):
                if not isinstance(value, dict):
                    return False, "record_corrupt"
                edge_key = value.get("edge_key")
                if edge_key and edge_key in snapshot_edges:
                    if snapshot_edges[edge_key].model_dump(mode="json") != value:
                        return False, "record_corrupt"
            for entity_key in record_model.owned_entity_keys:
                entity = snapshot_entities.get(entity_key)
                if entity is None or entity.locator.path != path or entity_owners.get(entity_key) != path:
                    return False, "record_corrupt"
                if not any(evidence.path == path for evidence in entity.evidence):
                    return False, "record_corrupt"
            for edge_key in record_model.owned_edge_keys:
                edge = snapshot_edges.get(edge_key)
                if edge is None or edge_owners.get(edge_key) != path:
                    return False, "record_corrupt"
                if not any(evidence.path == path for evidence in edge.evidence):
                    return False, "record_corrupt"
        return True, None

    def _removed_counts(self, previous_state: dict | None, snapshot: ArchitectureSnapshot, affected: set[str]) -> tuple[int, int]:
        if not previous_state:
            return 0, 0
        try:
            previous = ArchitectureSnapshot.model_validate(previous_state.get("snapshot") or {})
        except (ValueError, TypeError):
            return 0, 0
        old_entities = {entity.entity_key for entity in previous.entities if entity.locator.path in affected}
        new_entities = {entity.entity_key for entity in snapshot.entities if entity.locator.path in affected}
        old_edges = {edge.edge_key for edge in previous.edges if edge.evidence and edge.evidence[0].path in affected}
        new_edges = {edge.edge_key for edge in snapshot.edges if edge.evidence and edge.evidence[0].path in affected}
        return len(old_entities - new_entities), len(old_edges - new_edges)

    def _persist(self, files: list[FileInfo], repo_fingerprint: str, profile: str, snapshot: ArchitectureSnapshot) -> None:
        manifest = _load_cache_manifest(self._facts_dir, repo_fingerprint, profile) or _manifest_for_files(files)
        record_keys: dict[str, str] = {}
        entities_by_path: dict[str, list[dict[str, object]]] = {}
        for entity in snapshot.entities:
            entities_by_path.setdefault(entity.locator.path, []).append(entity.model_dump(mode="json"))
        edges_by_path: dict[str, list[dict[str, object]]] = {}
        entities_by_key = {entity.entity_key: entity.model_dump(mode="json") for entity in snapshot.entities}
        for edge in snapshot.edges:
            for path in {evidence.path for evidence in edge.evidence if evidence.path}:
                edges_by_path.setdefault(path, []).append(edge.model_dump(mode="json"))
        for item in files:
            extract_references = item.estimated_tokens <= 8000
            cache_path = _facts_cache_path(self._facts_dir, item, repo_fingerprint, profile, extract_references=extract_references)
            if cache_path is None or not cache_path.exists():
                continue
            key = self._record_key(repo_fingerprint, item, profile, extract_references)
            record_path = self._records_dir / f"{key}.json"
            try:
                facts_payload = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            facts = facts_payload.get("facts", {}) if isinstance(facts_payload.get("facts"), dict) else {}
            manifest_record = (manifest.get("files") or {}).get(item.path, {})
            local_entities = entities_by_path.get(item.path, [])
            file_entities = [entity for entity in local_entities if entity.get("entity_type") in {"module", "config", "test", "document"}]
            symbol_entities = [entity for entity in local_entities if entity.get("entity_type") == "symbol"]
            comment_entities = [entity for entity in local_entities if entity.get("entity_type") == "comment"]
            document_entities = [entity for entity in local_entities if entity.get("entity_type") == "document"]
            semantic_entities = [
                entity for entity in local_entities
                if entity.get("entity_type") in {"api", "schema", "queue", "config", "external", "unresolved"}
            ]
            local_edges = sorted(edges_by_path.get(item.path, []), key=lambda edge: str(edge.get("edge_key", "")))
            target_entities = sorted(
                (
                    entities_by_key[key]
                    for edge in local_edges
                    for key in (edge.get("source_entity_key"), edge.get("target_entity_key"))
                    if isinstance(key, str) and key in entities_by_key and entities_by_key[key] not in local_entities
                ),
                key=lambda entity: str(entity.get("entity_key", "")),
            )
            record = LocalSemanticRecord(
                schema_version=self.schema_version,
                extractor_profile_hash=profile,
                repository_identity=repo_fingerprint,
                path=item.path,
                content_hash=item.hash or "",
                language=item.language or "text",
                record_key=key,
                facts=facts,
                manifest=manifest_record if isinstance(manifest_record, dict) else {},
                file_entity=file_entities[0] if file_entities else {"path": item.path, "language": item.language or "text"},
                symbol_entities=symbol_entities,
                comment_entities=comment_entities,
                document_entities=document_entities,
                local_entities=semantic_entities,
                relationship_entities=target_entities,
                local_edges=local_edges,
                raw_relationship_candidates=facts.get("relations", []),
                imports=manifest_record.get("imports", []) if isinstance(manifest_record, dict) else [],
                exports=manifest_record.get("exports", []) if isinstance(manifest_record, dict) else [],
                reexports=manifest_record.get("reexports", []) if isinstance(manifest_record, dict) else [],
                aliases=manifest_record.get("aliases", []) if isinstance(manifest_record, dict) else [],
                declared_symbols=manifest_record.get("symbols", []) if isinstance(manifest_record, dict) else [],
                referenced_names=manifest_record.get("targets", []) if isinstance(manifest_record, dict) else [],
                source_evidence=[
                    *facts.get("relations", []),
                    *facts.get("comments", []),
                    *(evidence for edge in local_edges for evidence in edge.get("evidence", [])),
                ],
                owned_entity_keys=sorted({
                    str(entity.get("entity_key"))
                    for entity in [*file_entities, *symbol_entities, *comment_entities, *document_entities, *semantic_entities]
                    if entity.get("entity_key")
                }),
                owned_edge_keys=sorted({str(edge.get("edge_key")) for edge in local_edges if edge.get("edge_key")}),
            )
            _atomic_write_json(record_path, record.model_dump(mode="json"))
            record_keys[item.path] = key

        previous_state = self._load_state(repo_fingerprint, profile, ref=snapshot.ref)
        old_keys = set((previous_state or {}).get("record_keys", {}).values())
        for removed_key in old_keys - set(record_keys.values()):
            if self._record_referenced_by_other_state(removed_key, repo_fingerprint, profile, snapshot.ref):
                continue
            try:
                (self._records_dir / f"{removed_key}.json").unlink(missing_ok=True)
            except OSError:
                pass
        manifest_model = GraphManifest(
            schema_version=self.schema_version,
            repository_identity=repo_fingerprint,
            ref=snapshot.ref,
            commit_sha=snapshot.commit_sha,
            extractor_profile_hash=profile,
            files=manifest.get("files", {}),
            record_keys=record_keys,
            reverse_dependencies=manifest.get("reverse_dependencies", {}),
        )
        entity_owners = {
            entity_key: path
            for path, key in record_keys.items()
            for entity_key in self._record_entity_keys(path, key)
        }
        edge_owners = {
            edge_key: path
            for path, key in record_keys.items()
            for edge_key in self._record_edge_keys(path, key)
        }
        payload = MaterializedGraphState(
            schema_version=self.schema_version,
            repository_identity=repo_fingerprint,
            ref=snapshot.ref,
            commit_sha=snapshot.commit_sha,
            extractor_profile_hash=profile,
            manifest=manifest_model,
            record_keys=record_keys,
            entity_owners=entity_owners,
            edge_owners=edge_owners,
            snapshot=snapshot.model_dump(mode="json"),
        ).model_dump(mode="json")
        payload["manifest"] = manifest
        payload["repo_fingerprint"] = repo_fingerprint
        payload["graph_manifest"] = manifest_model.model_dump(mode="json")
        _atomic_write_json(self._state_path(repo_fingerprint, profile, snapshot.ref), payload)
        _atomic_write_json(
            self._manifests_dir / f"{repo_fingerprint}-{profile}-{self._ref_namespace(snapshot.ref)}.json",
            {
                "schema_version": self.schema_version,
                "repository_identity": repo_fingerprint,
                "ref": snapshot.ref,
                "commit_sha": snapshot.commit_sha,
                "extractor_profile_hash": profile,
                "files": manifest.get("files", {}),
                "record_keys": record_keys,
                "reverse_dependencies": manifest.get("reverse_dependencies", {}),
            },
        )

    def _record_entity_keys(self, path: str, key: str) -> list[str]:
        try:
            payload = json.loads((self._records_dir / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        owned = payload.get("owned_entity_keys")
        if isinstance(owned, list):
            return [str(key) for key in owned if key]
        return []

    def _record_edge_keys(self, path: str, key: str) -> list[str]:
        try:
            payload = json.loads((self._records_dir / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        owned = payload.get("owned_edge_keys")
        if isinstance(owned, list):
            return [str(key) for key in owned if key]
        return []

    def _record_referenced_by_other_state(self, record_key: str, repo_fingerprint: str, profile: str, ref: str) -> bool:
        """Keep content-addressed records still referenced by another ref."""
        for state_path in self._state_dir.glob("*.json"):
            if state_path == self._state_path(repo_fingerprint, profile, ref):
                continue
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if record_key in set((payload.get("record_keys") or {}).values()):
                return True
        return False

    @staticmethod
    def _ref_namespace(ref: str) -> str:
        import hashlib

        return hashlib.sha256(ref.encode("utf-8")).hexdigest()[:16]

    def _record_key(self, repo_fingerprint: str, item: FileInfo, profile: str, extract_references: bool) -> str:
        import hashlib

        value = "|".join((repo_fingerprint, item.path, item.hash or "", str(self.schema_version), profile, str(extract_references)))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
