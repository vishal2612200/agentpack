from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator

from agentpack.analysis.dependency_graph import build as build_dependency_graph
from agentpack.analysis.symbols import extract_symbols
from agentpack.analysis.tests import find_related_tests
from agentpack.application.pack_service import AdapterRegistry
from agentpack.architecture.models import (
    ArchitectureAlias,
    ArchitectureCheckResult,
    ArchitectureDiff,
    ArchitectureEdge,
    ArchitectureEntity,
    ArchitectureEvidence,
    ArchitectureLocator,
    ArchitectureSnapshot,
    ArchitectureViolation,
    EdgeChange,
    EntityChange,
)
from agentpack.core.config import Config, load_config
from agentpack.core.git import current_sha, dirty_files
from agentpack.core.ignore import load_spec
from agentpack.core.models import FileInfo, Symbol
from agentpack.core.scanner import LANGUAGE_MAP, scan

SCHEMA_VERSION = 1
_CONFIDENCE_ORDER = {
    "unavailable": 0,
    "file_level": 1,
    "best_effort": 2,
    "structured": 3,
}
_CONFIG_LANGS = {"json", "yaml", "toml", "xml", "terraform", "dockerfile"}
_BEST_EFFORT_LANGS = {"javascript", "typescript", "go", "rust"}
_TREE_SITTER_LANGS = {"java", "kotlin", "ruby", "php", "protobuf", "terraform", "dockerfile", "graphql"}


def serialize_model(model: object) -> str:
    if hasattr(model, "model_dump"):
        payload = model.model_dump(mode="json")  # type: ignore[call-arg]
    else:
        payload = model
    return json.dumps(payload, indent=2, sort_keys=True)


def build_snapshot_for_ref(root: Path, ref: str | None = None) -> ArchitectureSnapshot:
    requested_ref = (ref or "").strip()
    if requested_ref:
        commit_sha = _resolve_ref(root, requested_ref)
        cached = _load_cached_snapshot(root, commit_sha)
        if cached is not None:
            return cached
        live_sha = current_sha(root)
        if live_sha == commit_sha and not dirty_files(root):
            snapshot = _build_snapshot_from_root(root, requested_ref, commit_sha)
        else:
            with _detached_worktree(root, commit_sha) as detached_root:
                snapshot = _build_snapshot_from_root(detached_root, requested_ref, commit_sha)
        _save_cached_snapshot(root, snapshot)
        return snapshot

    live_sha = current_sha(root) or "worktree"
    return _build_snapshot_from_root(root, "WORKTREE", live_sha)


def build_diff(root: Path, base_ref: str, head_ref: str) -> ArchitectureDiff:
    base = build_snapshot_for_ref(root, base_ref)
    head = build_snapshot_for_ref(root, head_ref)
    base_entities = {entity.entity_key: entity for entity in base.entities}
    head_entities = {entity.entity_key: entity for entity in head.entities}
    base_edges = {edge.edge_key: edge for edge in base.edges}
    head_edges = {edge.edge_key: edge for edge in head.edges}

    changed_entities: list[EntityChange] = []
    changed_confidence: list[str] = []
    for entity_key in sorted(base_entities.keys() & head_entities.keys()):
        before = base_entities[entity_key]
        after = head_entities[entity_key]
        revision_changed = before.revision_id != after.revision_id
        locator_changed = before.locator.path != after.locator.path or before.locator.start_line != after.locator.start_line or before.locator.end_line != after.locator.end_line
        confidence_changed = before.confidence_tier != after.confidence_tier
        if not (revision_changed or locator_changed or confidence_changed):
            continue
        if confidence_changed:
            changed_confidence.append(entity_key)
        changed_entities.append(
            EntityChange(
                entity_key=entity_key,
                before_revision_id=before.revision_id,
                after_revision_id=after.revision_id,
                before_path=before.locator.path,
                after_path=after.locator.path,
                before_confidence_tier=before.confidence_tier,
                after_confidence_tier=after.confidence_tier,
                revision_changed=revision_changed,
                locator_changed=locator_changed,
            )
        )

    changed_edges: list[EdgeChange] = []
    for edge_key in sorted(base_edges.keys() & head_edges.keys()):
        before = base_edges[edge_key]
        after = head_edges[edge_key]
        if before.revision_id == after.revision_id and before.confidence_tier == after.confidence_tier:
            continue
        if before.confidence_tier != after.confidence_tier:
            changed_confidence.append(edge_key)
        changed_edges.append(
            EdgeChange(
                edge_key=edge_key,
                before_revision_id=before.revision_id,
                after_revision_id=after.revision_id,
                before_confidence_tier=before.confidence_tier,
                after_confidence_tier=after.confidence_tier,
            )
        )

    added_entities = [head_entities[key] for key in sorted(head_entities.keys() - base_entities.keys())]
    removed_entities = [base_entities[key] for key in sorted(base_entities.keys() - head_entities.keys())]
    added_edges = [head_edges[key] for key in sorted(head_edges.keys() - base_edges.keys())]
    removed_edges = [base_edges[key] for key in sorted(base_edges.keys() - head_edges.keys())]
    aliases = _detect_aliases(root, base_ref, head_ref, removed_entities, added_entities)

    affected_domains = sorted(
        {
            str(entity.metadata.get("domain"))
            for entity in [*added_entities, *removed_entities]
            if entity.metadata.get("domain")
        }
        | {
            str(head_entities[change.entity_key].metadata.get("domain"))
            for change in changed_entities
            if head_entities[change.entity_key].metadata.get("domain")
        }
    )
    test_impact = sorted({edge.target_entity_key for edge in added_edges if edge.edge_type == "tested_by"})

    return ArchitectureDiff(
        base_ref=base_ref,
        head_ref=head_ref,
        added_entities=added_entities,
        removed_entities=removed_entities,
        changed_entities=changed_entities,
        aliased_entities=aliases,
        added_edges=added_edges,
        removed_edges=removed_edges,
        changed_edges=changed_edges,
        affected_domains=affected_domains,
        test_impact=test_impact,
        changed_confidence=sorted(set(changed_confidence)),
    )


def run_check(root: Path, base_ref: str, head_ref: str, cfg: Config | None = None) -> ArchitectureCheckResult:
    config = cfg or load_config(root)
    diff = build_diff(root, base_ref, head_ref)
    head = build_snapshot_for_ref(root, head_ref)
    head_entities = {entity.entity_key: entity for entity in head.entities}
    file_owner_keys = _file_owner_keys(head, head_entities)
    changed_entity_keys = {
        entity.entity_key for entity in diff.added_entities
    } | {entity.entity_key for entity in diff.removed_entities} | {change.entity_key for change in diff.changed_entities}
    changed_file_keys = {file_owner_keys.get(entity_key, entity_key) for entity_key in changed_entity_keys}
    violations: list[ArchitectureViolation] = []
    warnings: list[str] = []

    for invariant in config.architecture.invariant:
        kind = invariant.kind
        if kind == "forbid_edge":
            for edge in diff.added_edges:
                if edge.edge_type not in invariant.edge_types:
                    continue
                if _CONFIDENCE_ORDER[edge.confidence_tier] < _CONFIDENCE_ORDER[invariant.min_confidence]:
                    continue
                source = head_entities.get(edge.source_entity_key)
                target = head_entities.get(edge.target_entity_key)
                if source is None or target is None:
                    continue
                if not _matches_selector(source, invariant.source) or not _matches_selector(target, invariant.target):
                    continue
                violations.append(
                    _violation(
                        invariant_id=invariant.id,
                        kind=kind,
                        requested_enforcement=invariant.enforcement,
                        message=f"{source.qualified_name} {edge.edge_type} {target.qualified_name}",
                        entity_keys=[source.entity_key, target.entity_key],
                        edge_keys=[edge.edge_key],
                        evidence=edge.evidence,
                    )
                )
        elif kind == "require_test":
            tested_targets = {
                edge.source_entity_key
                for edge in head.edges
                if edge.edge_type == "tested_by" and _CONFIDENCE_ORDER[edge.confidence_tier] >= _CONFIDENCE_ORDER[invariant.min_confidence]
            }
            for entity_key in sorted(changed_entity_keys):
                entity = head_entities.get(entity_key)
                if entity is None or entity.entity_type == "test" or not _matches_selector(entity, invariant.source):
                    continue
                if file_owner_keys.get(entity.entity_key, entity.entity_key) in tested_targets:
                    continue
                violations.append(
                    _violation(
                        invariant_id=invariant.id,
                        kind=kind,
                        requested_enforcement=invariant.enforcement,
                        message=f"{entity.qualified_name} changed without a related test edge",
                        entity_keys=[entity.entity_key],
                        evidence=entity.evidence,
                    )
                )
        elif kind == "require_consumer_update":
            for entity_key in sorted(changed_entity_keys):
                entity = head_entities.get(entity_key)
                if entity is None or not _matches_selector(entity, invariant.source):
                    continue
                file_key = file_owner_keys.get(entity.entity_key, entity.entity_key)
                consumer_edges = [
                    edge
                    for edge in head.edges
                    if edge.edge_type == "imports"
                    and edge.target_entity_key == file_key
                    and _CONFIDENCE_ORDER[edge.confidence_tier] >= _CONFIDENCE_ORDER[invariant.min_confidence]
                ]
                if not consumer_edges:
                    continue
                consumers = {edge.source_entity_key for edge in consumer_edges}
                if any(consumer in changed_file_keys for consumer in consumers):
                    continue
                consumer_evidence = [
                    evidence
                    for edge in consumer_edges
                    for evidence in edge.evidence
                ]
                violations.append(
                    _violation(
                        invariant_id=invariant.id,
                        kind=kind,
                        requested_enforcement=invariant.enforcement,
                        message=f"{entity.qualified_name} changed without a matching consumer update",
                        entity_keys=[entity.entity_key, *sorted(consumers)],
                        evidence=[*entity.evidence, *consumer_evidence],
                    )
                )
        else:
            warnings.append(f"Unsupported invariant kind: {kind}")

    return ArchitectureCheckResult(diff=diff, violations=violations, warnings=warnings)


def capability_registry() -> dict[str, str]:
    from agentpack.analysis.tree_sitter_backend import is_available as tree_sitter_available
    from agentpack.analysis.tree_sitter_backend import supports_language as tree_sitter_supports_language

    available = tree_sitter_available()
    languages = sorted(set(LANGUAGE_MAP.values()) | {"dockerfile"})
    capabilities: dict[str, str] = {}
    for language in languages:
        if language == "python":
            capabilities[language] = "structured"
        elif language in _BEST_EFFORT_LANGS:
            capabilities[language] = "best_effort"
        elif language in _TREE_SITTER_LANGS:
            capabilities[language] = "structured" if available and tree_sitter_supports_language(language) else "unavailable"
        elif language in _CONFIG_LANGS or language in {"markdown", "bash", "sql", "html", "css", "scss"}:
            capabilities[language] = "file_level"
        else:
            capabilities[language] = "file_level"
    return capabilities


def _build_snapshot_from_root(root: Path, ref: str, commit_sha: str) -> ArchitectureSnapshot:
    cfg = load_config(root)
    ignore_spec = load_spec(root / cfg.project.ignore_file)
    scan_result = scan(
        root,
        ignore_spec,
        cfg.context.max_file_tokens,
        always_skip_paths=AdapterRegistry.generated_output_paths(root, cfg),
    )
    files = sorted(scan_result.packable, key=lambda item: item.path)
    capabilities = capability_registry()
    repo_fingerprint = _repo_fingerprint(root)
    graph = build_dependency_graph(files, root)

    domain_entities: dict[str, ArchitectureEntity] = {}
    file_entities: dict[str, ArchitectureEntity] = {}
    symbol_candidates: list[tuple[FileInfo, Symbol]] = []
    edges: list[ArchitectureEdge] = []

    for file_info in files:
        domain_name = _domain_for_path(file_info.path)
        if domain_name not in domain_entities:
            domain_entities[domain_name] = _make_entity(
                repo_fingerprint=repo_fingerprint,
                entity_type="domain",
                qualified_name=domain_name,
                display_name=domain_name,
                normalized_signature="domain",
                language=None,
                locator=ArchitectureLocator(path=domain_name),
                provenance="declared:path",
                confidence_tier="file_level",
                source_hash=_short_hash(domain_name),
                metadata={"domain": domain_name},
                evidence=[_evidence(kind="path", source="declared:path", confidence_tier="file_level", path=file_info.path, note="top-level path domain")],
            )
        entity_type = _classify_file_entity(file_info.path, file_info.language)
        file_entity = _make_entity(
            repo_fingerprint=repo_fingerprint,
            entity_type=entity_type,
            qualified_name=_qualified_file_name(file_info.path),
            display_name=Path(file_info.path).name,
            normalized_signature=_normalize_signature(f"{entity_type}:{file_info.language or 'text'}"),
            language=file_info.language,
            locator=ArchitectureLocator(path=file_info.path, start_line=1, end_line=1),
            provenance=f"extractor:{file_info.language or 'file'}",
            confidence_tier=capabilities.get(file_info.language or "", "file_level"),
            source_hash=file_info.hash or _short_hash(file_info.path),
            metadata={"domain": domain_name, "path": file_info.path},
            evidence=[_evidence(kind="file", source=f"extractor:{file_info.language or 'file'}", confidence_tier=capabilities.get(file_info.language or "", "file_level"), path=file_info.path, source_hash=file_info.hash or "")],
        )
        file_entities[file_info.path] = file_entity
        edges.append(_make_edge(domain_entities[domain_name], file_entity, "contains", "file_level", file_info.path, note="domain contains file"))

        if capabilities.get(file_info.language or "", "file_level") in {"structured", "best_effort"}:
            for symbol in extract_symbols(file_info.abs_path, file_info.language):
                symbol_candidates.append((file_info, symbol))

    duplicate_counts: dict[tuple[str, str], int] = {}
    for file_info, symbol in symbol_candidates:
        key = (str(symbol.name), _normalize_signature(symbol.signature))
        duplicate_counts[key] = duplicate_counts.get(key, 0) + 1

    symbol_entities: list[ArchitectureEntity] = []
    for file_info, symbol in symbol_candidates:
        file_entity = file_entities[file_info.path]
        base_name = str(symbol.name)
        normalized_signature = _normalize_signature(symbol.signature)
        duplicate_key = (base_name, normalized_signature)
        qualified_name = base_name if duplicate_counts[duplicate_key] == 1 else f"{file_entity.qualified_name}:{base_name}"
        symbol_entity = _make_entity(
            repo_fingerprint=repo_fingerprint,
            entity_type="symbol",
            qualified_name=qualified_name,
            display_name=base_name,
            normalized_signature=normalized_signature,
            language=file_info.language,
            locator=ArchitectureLocator(path=file_info.path, start_line=symbol.start_line, end_line=symbol.end_line),
            provenance=_symbol_provenance(file_info.language),
            confidence_tier=capabilities.get(file_info.language or "", "file_level"),
            source_hash=_short_hash(symbol.body or symbol.signature or base_name),
            metadata={"domain": file_entity.metadata.get("domain"), "path": file_info.path, "symbol_kind": symbol.kind},
            evidence=[
                _evidence(
                    kind="symbol",
                    source=_symbol_provenance(file_info.language),
                    confidence_tier=capabilities.get(file_info.language or "", "file_level"),
                    path=file_info.path,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    source_hash=_short_hash(symbol.body or symbol.signature or base_name),
                    note=symbol.kind,
                )
            ],
        )
        symbol_entities.append(symbol_entity)
        edges.append(_make_edge(file_entity, symbol_entity, "contains", capabilities.get(file_info.language or "", "file_level"), file_info.path, start_line=symbol.start_line, end_line=symbol.end_line, note="file contains symbol"))

    for file_info in files:
        source_entity = file_entities[file_info.path]
        graph_node = graph.get(file_info.path)
        for target_path in sorted(dep for dep in graph_node.imports if dep in file_entities):
            target_entity = file_entities[target_path]
            tier = capabilities.get(file_info.language or "", "file_level")
            if tier == "unavailable":
                continue
            edges.append(_make_edge(source_entity, target_entity, "imports", tier, file_info.path, note="local import"))
        if source_entity.entity_type == "test":
            continue
        for test_path in find_related_tests(file_info.path, set(file_entities)):
            test_entity = file_entities.get(test_path)
            if test_entity is None:
                continue
            edges.append(_make_edge(source_entity, test_entity, "tested_by", "best_effort", file_info.path, note="path-based related test"))

    entities = sorted(
        [*domain_entities.values(), *file_entities.values(), *symbol_entities],
        key=lambda entity: (entity.entity_type, entity.qualified_name, entity.locator.path, entity.locator.start_line or 0),
    )
    sorted_edges = sorted(edges, key=lambda edge: (edge.edge_type, edge.source_entity_key, edge.target_entity_key))
    return ArchitectureSnapshot(
        schema_version=SCHEMA_VERSION,
        ref=ref,
        commit_sha=commit_sha,
        repo_fingerprint=repo_fingerprint,
        extractor_profile_hash=_extractor_profile_hash(),
        capabilities=capabilities,
        entities=entities,
        edges=sorted_edges,
    )


def _matches_selector(entity: ArchitectureEntity, selector) -> bool:
    entity_types = set(selector.entity_types)
    if entity_types and entity.entity_type not in entity_types:
        return False
    path_globs = list(selector.path_globs)
    if path_globs and not any(fnmatch(entity.locator.path, pattern) for pattern in path_globs):
        return False
    qualified_names = set(selector.qualified_names)
    if qualified_names and entity.qualified_name not in qualified_names:
        return False
    substrings = list(selector.qualified_name_contains)
    if substrings and not any(part in entity.qualified_name for part in substrings):
        return False
    return True


def _file_owner_keys(
    snapshot: ArchitectureSnapshot,
    entities: dict[str, ArchitectureEntity],
) -> dict[str, str]:
    """Map symbols to their file entity so file-level relationships cover source changes."""
    file_entity_types = {"module", "config", "test"}
    owners = {
        entity.entity_key: entity.entity_key
        for entity in snapshot.entities
        if entity.entity_type in file_entity_types
    }
    for edge in snapshot.edges:
        if edge.edge_type != "contains":
            continue
        source = entities.get(edge.source_entity_key)
        if source is not None and source.entity_type in file_entity_types:
            owners.setdefault(edge.target_entity_key, source.entity_key)
    return owners


def _violation(
    *,
    invariant_id: str,
    kind: str,
    requested_enforcement: str,
    message: str,
    entity_keys: list[str],
    evidence: list[ArchitectureEvidence],
    edge_keys: list[str] | None = None,
) -> ArchitectureViolation:
    """Keep blocking architecture policy limited to high-confidence source evidence."""
    blocking = requested_enforcement == "block" and _has_blocking_evidence(evidence)
    if requested_enforcement == "block" and not blocking:
        message += " (advisory: evidence is not declared or structured)"
    return ArchitectureViolation(
        invariant_id=invariant_id,
        kind=kind,
        enforcement="block" if blocking else "warn",
        requested_enforcement=requested_enforcement,
        message=message,
        blocking=blocking,
        entity_keys=entity_keys,
        edge_keys=edge_keys or [],
        evidence=evidence,
    )


def _has_blocking_evidence(evidence: list[ArchitectureEvidence]) -> bool:
    return any(
        item.confidence_tier == "structured" or item.source.startswith("declared:")
        for item in evidence
    )


def _load_cached_snapshot(root: Path, commit_sha: str) -> ArchitectureSnapshot | None:
    path = _cache_path(root, commit_sha)
    if not path.exists():
        return None
    try:
        return ArchitectureSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _save_cached_snapshot(root: Path, snapshot: ArchitectureSnapshot) -> None:
    path = _cache_path(root, snapshot.commit_sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_model(snapshot), encoding="utf-8")


def _cache_path(root: Path, commit_sha: str) -> Path:
    cfg = load_config(root)
    return root / cfg.architecture.cache_dir / f"{commit_sha}-{SCHEMA_VERSION}-{_extractor_profile_hash()}.json"


def _repo_fingerprint(root: Path) -> str:
    identity = _git_output(root, ["git", "config", "--get", "remote.origin.url"]) or _git_output(root, ["git", "rev-parse", "--git-common-dir"]) or str(root.resolve())
    return _short_hash(identity.strip())


def _extractor_profile_hash() -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "capabilities": capability_registry(),
    }
    return _short_hash(json.dumps(payload, sort_keys=True))


def _make_entity(
    *,
    repo_fingerprint: str,
    entity_type: str,
    qualified_name: str,
    display_name: str,
    normalized_signature: str,
    language: str | None,
    locator: ArchitectureLocator,
    provenance: str,
    confidence_tier: str,
    source_hash: str,
    metadata: dict[str, object],
    evidence: list[ArchitectureEvidence],
) -> ArchitectureEntity:
    entity_key = _short_hash(f"{repo_fingerprint}|{entity_type}|{qualified_name}|{normalized_signature}")
    revision_id = _short_hash(f"{entity_key}|{source_hash}")
    return ArchitectureEntity(
        entity_key=entity_key,
        revision_id=revision_id,
        entity_type=entity_type,
        qualified_name=qualified_name,
        display_name=display_name,
        normalized_signature=normalized_signature,
        language=language,
        locator=locator,
        provenance=provenance,
        confidence_tier=confidence_tier,
        source_hash=source_hash,
        metadata=dict(metadata),
        evidence=list(evidence),
    )


def _make_edge(
    source: ArchitectureEntity,
    target: ArchitectureEntity,
    edge_type: str,
    confidence_tier: str,
    path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    note: str = "",
) -> ArchitectureEdge:
    evidence = [
        _evidence(
            kind=edge_type,
            source="extractor:graph",
            confidence_tier=confidence_tier,
            path=path,
            start_line=start_line,
            end_line=end_line,
            source_hash=_short_hash(f"{path}:{start_line}:{end_line}:{note}"),
            note=note,
        )
    ]
    edge_key = _short_hash(f"{source.entity_key}|{edge_type}|{target.entity_key}")
    revision_id = _short_hash(f"{edge_key}|{evidence[0].source_hash}|{confidence_tier}")
    return ArchitectureEdge(
        edge_key=edge_key,
        revision_id=revision_id,
        edge_type=edge_type,
        source_entity_key=source.entity_key,
        target_entity_key=target.entity_key,
        confidence_tier=confidence_tier,
        metadata={},
        evidence=evidence,
    )


def _evidence(
    *,
    kind: str,
    source: str,
    confidence_tier: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    source_hash: str = "",
    note: str = "",
) -> ArchitectureEvidence:
    return ArchitectureEvidence(
        kind=kind,
        source=source,
        confidence_tier=confidence_tier,
        confidence=float(_CONFIDENCE_ORDER[confidence_tier]) / float(max(_CONFIDENCE_ORDER.values()) or 1),
        path=path,
        start_line=start_line,
        end_line=end_line,
        source_hash=source_hash,
        note=note,
    )


def _symbol_provenance(language: str | None) -> str:
    if language == "python":
        return "extractor:python_ast"
    if language in _TREE_SITTER_LANGS:
        return "extractor:tree_sitter"
    return f"extractor:{language or 'unknown'}"


def _normalize_signature(signature: str | None) -> str:
    return " ".join((signature or "").split())


def _qualified_file_name(path: str) -> str:
    return ".".join(Path(path).with_suffix("").parts)


def _classify_file_entity(path: str, language: str | None) -> str:
    name = path.lower()
    if "/tests/" in f"/{name}" or name.startswith("tests/") or Path(path).name.startswith("test_") or ".test." in name or ".spec." in name:
        return "test"
    if language in _CONFIG_LANGS:
        return "config"
    return "module"


def _domain_for_path(path: str) -> str:
    parts = Path(path).parts
    return parts[0] if parts else "root"


def _resolve_ref(root: Path, ref: str) -> str:
    resolved = _git_output(root, ["git", "rev-parse", ref])
    if not resolved:
        raise ValueError(f"Could not resolve git ref: {ref}")
    return resolved.strip()


@contextmanager
def _detached_worktree(root: Path, ref: str) -> Iterator[Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="agentpack-architecture-"))
    hooks_dir = Path(tempfile.mkdtemp(prefix="agentpack-architecture-hooks-"))
    try:
        # Snapshot extraction must not execute arbitrary repository checkout hooks.
        subprocess.run(
            ["git", "-c", f"core.hooksPath={hooks_dir}", "worktree", "add", "--detach", str(temp_dir), ref],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        yield temp_dir
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(temp_dir)], cwd=root, capture_output=True, text=True, check=False)
        shutil.rmtree(hooks_dir, ignore_errors=True)


def _detect_aliases(
    root: Path,
    base_ref: str,
    head_ref: str,
    removed_entities: list[ArchitectureEntity],
    added_entities: list[ArchitectureEntity],
) -> list[ArchitectureAlias]:
    rename_map = _git_renames(root, base_ref, head_ref)
    if not rename_map:
        return []
    removed_by_path = {entity.locator.path: entity for entity in removed_entities}
    added_by_path = {entity.locator.path: entity for entity in added_entities}
    aliases: list[ArchitectureAlias] = []
    seen_pairs: set[tuple[str, str]] = set()
    for old_path, new_path in sorted(rename_map.items()):
        before = removed_by_path.get(old_path)
        after = added_by_path.get(new_path)
        if before is None or after is None or before.entity_type != after.entity_type:
            continue
        aliases.append(
            ArchitectureAlias(
                before_entity_key=before.entity_key,
                after_entity_key=after.entity_key,
                reason="git rename detection",
                before_path=old_path,
                after_path=new_path,
            )
        )
        seen_pairs.add((before.entity_key, after.entity_key))

    # Git rename data is authoritative for moved files. For semantic entities,
    # add an alias only when the same qualified name/signature is unique on both
    # sides; ambiguous candidates are intentionally left unaliased.
    removed_by_identity = _unique_alias_candidates(removed_entities)
    added_by_identity = _unique_alias_candidates(added_entities)
    for identity in sorted(removed_by_identity.keys() & added_by_identity.keys()):
        before = removed_by_identity[identity]
        after = added_by_identity[identity]
        if before.entity_key == after.entity_key or (before.entity_key, after.entity_key) in seen_pairs:
            continue
        aliases.append(
            ArchitectureAlias(
                before_entity_key=before.entity_key,
                after_entity_key=after.entity_key,
                reason="unique qualified-name/signature match",
                before_path=before.locator.path,
                after_path=after.locator.path,
            )
        )
    return aliases


def _unique_alias_candidates(entities: list[ArchitectureEntity]) -> dict[tuple[str, str, str], ArchitectureEntity]:
    grouped: dict[tuple[str, str, str], list[ArchitectureEntity]] = {}
    for entity in entities:
        identity = (entity.entity_type, entity.qualified_name, entity.normalized_signature)
        grouped.setdefault(identity, []).append(entity)
    return {identity: candidates[0] for identity, candidates in grouped.items() if len(candidates) == 1}


def _git_renames(root: Path, base_ref: str, head_ref: str) -> dict[str, str]:
    out = _git_output(root, ["git", "diff", "--find-renames", "--name-status", base_ref, head_ref])
    if not out:
        return {}
    renames: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            renames[parts[1]] = parts[2]
    return renames


def _git_output(root: Path, args: list[str]) -> str:
    result = subprocess.run(args, cwd=root, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
