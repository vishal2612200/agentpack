from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import tempfile
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agentpack.analysis.tests import find_related_tests
from agentpack.analysis.tree_sitter_backend import SemanticFacts, SemanticLocalEntityFact, SemanticRelationFact, extract_semantic_facts
from agentpack.architecture.models import (
    ArchitectureEdge,
    ArchitectureEntity,
    ArchitectureEvidence,
    ArchitectureLocator,
)
from agentpack.core.models import FileInfo


CONFIDENCE_ORDER = {"unavailable": 0, "file_level": 1, "best_effort": 2, "structured": 3}
CONFIG_LANGUAGES = {"json", "yaml", "toml", "xml", "terraform", "dockerfile"}
FACTS_CACHE_VERSION = 2
MANIFEST_CACHE_VERSION = 2
SEMANTIC_SCHEMA_VERSION = 6


@dataclass
class SemanticGraphCacheStats:
    files_total: int = 0
    parsed_files: int = 0
    reused_files: int = 0
    changed_files: int = 0
    deleted_files: int = 0
    affected_files: int = 0
    cache_manifest_writes: int = 0
    re_resolved_relationships: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "files_total": self.files_total,
            "parsed_files": self.parsed_files,
            "reused_files": self.reused_files,
            "changed_files": self.changed_files,
            "deleted_files": self.deleted_files,
            "affected_files": self.affected_files,
            "cache_manifest_writes": self.cache_manifest_writes,
            "re_resolved_relationships": self.re_resolved_relationships,
        }


def build_semantic_graph(
    files: list[FileInfo],
    root: Path,
    capabilities: dict[str, str],
    repo_fingerprint: str,
    make_domain: Callable[[str], str],
    facts_cache_dir: Path | None = None,
    records_cache_dir: Path | None = None,
    extractor_profile_hash: str = "semantic-graph-v5",
    cache_stats: dict[str, int] | None = None,
    previous_snapshot=None,
    affected_paths: set[str] | None = None,
) -> tuple[list[ArchitectureEntity], list[ArchitectureEdge], dict[str, str]]:
    """Build the canonical graph in two deterministic passes.

    Pass one creates stable file/symbol/comment/document entities and raw
    relationship candidates. Pass two resolves candidates against file and
    symbol indexes, retaining unresolved or ambiguous targets as entities.
    """
    ordered_files = sorted(files, key=lambda item: item.path)
    stats = SemanticGraphCacheStats(files_total=len(ordered_files))
    previous_manifest = _load_cache_manifest(facts_cache_dir, repo_fingerprint, extractor_profile_hash)
    current_manifest = _manifest_for_files(ordered_files)
    invalidation = _invalidation_sets(previous_manifest, current_manifest)
    stats.changed_files = len(invalidation["changed"])
    stats.deleted_files = len(invalidation["deleted"])
    stats.affected_files = len(invalidation["affected"])
    domains: dict[str, ArchitectureEntity] = {}
    file_entities: dict[str, ArchitectureEntity] = {}
    facts_by_path: dict[str, SemanticFacts] = {}
    aliases_by_path: dict[str, dict[str, str]] = {}
    symbols_by_path: dict[str, list[ArchitectureEntity]] = {}
    local_entities_by_path: dict[str, list[ArchitectureEntity]] = {}
    entities: list[ArchitectureEntity] = []
    edges: list[ArchitectureEdge] = []
    file_hashes = {item.path: item.hash or "" for item in ordered_files}
    all_paths = {item.path for item in ordered_files}
    requested_affected = set(affected_paths or all_paths)

    for file_info in ordered_files:
        extract_references = file_info.estimated_tokens <= 8000
        facts = _load_or_extract_facts(
            file_info,
            facts_cache_dir,
            records_cache_dir,
            repo_fingerprint,
            extractor_profile_hash,
            extract_references=extract_references,
            stats=stats,
        )
        facts_by_path[file_info.path] = facts
        aliases_by_path[file_info.path] = facts.aliases or _import_aliases(file_info)
        manifest_record = current_manifest["files"].get(file_info.path)
        if isinstance(manifest_record, dict):
            manifest_record["imports"] = sorted(
                {
                    relation.target_name
                    for relation in facts.relations
                    if relation.relation == "imports"
                }
            )
            manifest_record["symbols"] = sorted(fact.name for fact in facts.symbols)
            manifest_record["exports"] = sorted(facts.exports)
            manifest_record["reexports"] = sorted(facts.reexports)
            manifest_record["aliases"] = sorted(facts.aliases.items())
            manifest_record["targets"] = sorted(
                {
                    relation.target_name
                    for relation in facts.relations
                    if relation.relation != "imports"
                }
            )
            manifest_record["relation_types"] = sorted({relation.relation for relation in facts.relations})
    # Once cached facts are available, symbol changes can expand the closure
    # beyond importers. This keeps consumers of renamed/duplicated symbols from
    # retaining stale resolutions while still avoiding source parsing.
    invalidation = _invalidation_sets(previous_manifest, current_manifest)
    effective_affected = (requested_affected | invalidation["affected"]) if previous_snapshot is not None else all_paths
    effective_affected &= all_paths
    unaffected_paths = all_paths - effective_affected
    stats.affected_files = len(effective_affected)

    previous_entities_by_path: dict[str, list[ArchitectureEntity]] = {}
    previous_edges_by_path: dict[str, list[ArchitectureEdge]] = {}
    previous_entities_by_key: dict[str, ArchitectureEntity] = {}
    if previous_snapshot is not None:
        previous_entities_by_key = {entity.entity_key: entity for entity in previous_snapshot.entities}
        for entity in previous_snapshot.entities:
            previous_entities_by_path.setdefault(entity.locator.path, []).append(entity)
            if entity.entity_type == "domain":
                domains[entity.qualified_name] = entity
                if entity not in entities:
                    entities.append(entity)
        for edge in previous_snapshot.edges:
            for path in {evidence.path for evidence in edge.evidence if evidence.path}:
                previous_edges_by_path.setdefault(path, []).append(edge)

    for file_info in ordered_files:
        if previous_snapshot is not None and file_info.path in unaffected_paths:
            local_entities = previous_entities_by_path.get(file_info.path, [])
            file_entity = next(
                (
                    entity for entity in local_entities
                    if entity.entity_type == _file_entity_type(file_info.path, file_info.language or "text")
                ),
                None,
            )
            if file_entity is not None:
                file_entities[file_info.path] = file_entity
                symbols_by_path[file_info.path] = [
                    entity for entity in local_entities if entity.entity_type == "symbol"
                ]
                local_entities_by_path[file_info.path] = [
                    entity for entity in local_entities
                    if entity.entity_type in {"api", "schema", "queue", "config", "external", "unresolved"}
                ]
                for entity in local_entities:
                    if entity not in entities:
                        entities.append(entity)
                for edge in previous_edges_by_path.get(file_info.path, []):
                    if edge not in edges:
                        edges.append(edge)
                    for entity_key in (edge.source_entity_key, edge.target_entity_key):
                        entity = previous_entities_by_key.get(entity_key)
                        if entity is not None and entity not in entities:
                            entities.append(entity)
                continue
        domain_name = make_domain(file_info.path)
        if domain_name not in domains:
            domains[domain_name] = _entity(
                repo_fingerprint, "domain", domain_name, domain_name, "domain", None,
                ArchitectureLocator(path=domain_name), "declared:path", "file_level", _hash(domain_name),
                {"domain": domain_name}, [_evidence("path", "declared:path", "file_level", file_info.path, note="top-level path domain")],
            )
        language = file_info.language or "text"
        tier = capabilities.get(language, "file_level")
        entity_type = _file_entity_type(file_info.path, language)
        file_entity = _entity(
            repo_fingerprint,
            entity_type,
            _module_name(file_info.path),
            Path(file_info.path).name,
            f"{entity_type}:{language}",
            language,
            ArchitectureLocator(path=file_info.path, start_line=1, end_line=max(1, _line_count(file_info.abs_path))),
            f"extractor:{language}",
            tier,
            file_info.hash or _hash(file_info.path),
            {"domain": domain_name, "path": file_info.path},
            [_evidence("file", f"extractor:{language}", tier, file_info.path, source_hash=file_info.hash or "")],
        )
        file_entities[file_info.path] = file_entity
        entities.extend((domains[domain_name], file_entity) if domains[domain_name] not in entities else (file_entity,))
        edges.append(_edge(domains[domain_name], file_entity, "contains", "file_level", file_info.path, note="domain contains file"))

        facts = facts_by_path[file_info.path]
        symbols_by_path[file_info.path] = []
        for declaration_ordinal, fact in enumerate(facts.symbols):
            lexical_scope = fact.name.rsplit(".", 1)[0] if "." in fact.name else ""
            symbol = _entity(
                repo_fingerprint,
                "symbol",
                f"{_module_name(file_info.path)}:{fact.name}",
                fact.name.rsplit(".", 1)[-1],
                _normalize(fact.signature),
                language,
                ArchitectureLocator(path=file_info.path, start_line=fact.start_line, end_line=fact.end_line),
                "extractor:tree_sitter",
                "structured" if tier == "structured" else tier,
                _hash(fact.body),
                {
                    "domain": domain_name,
                    "path": file_info.path,
                    "symbol_kind": fact.kind,
                    "lexical_scope": lexical_scope,
                    "declaration_ordinal": declaration_ordinal,
                },
                [_evidence("symbol", "extractor:tree_sitter", tier, file_info.path, fact.start_line, fact.end_line, _hash(fact.body), fact.kind)],
            )
            symbols_by_path[file_info.path].append(symbol)
            entities.append(symbol)
            edges.append(_edge(file_entity, symbol, "contains", tier, file_info.path, fact.start_line, fact.end_line, "file contains symbol"))

        for comment_text, start, end, owner in facts.comments:
            comment = _entity(
                repo_fingerprint,
                "comment",
                f"{_module_name(file_info.path)}:comment:{start}:{_hash(comment_text)}",
                _comment_display(comment_text),
                "comment",
                language,
                ArchitectureLocator(path=file_info.path, start_line=start, end_line=end),
                "extractor:tree_sitter",
                tier,
                _hash(comment_text),
                {"path": file_info.path, "kind": "docstring" if comment_text.startswith(("\"", "'")) else "comment"},
                [_evidence("comment", "extractor:tree_sitter", tier, file_info.path, start, end, _hash(comment_text), "rationale/documentation evidence")],
            )
            entities.append(comment)
            edges.append(_edge(file_entity, comment, "contains", tier, file_info.path, start, end, "file contains comment"))
            owner_entity = _owner_entity(file_info.path, owner, symbols_by_path, file_entity)
            if owner_entity is not None:
                edges.append(_edge(comment, owner_entity, "documents", tier, file_info.path, start, end, "comment documents symbol"))

        local_entities_by_path[file_info.path] = []
        for ordinal, fact in enumerate(facts.local_entities):
            local_entity = _entity(
                repo_fingerprint,
                fact.entity_type,
                f"{_module_name(file_info.path)}:{fact.entity_type}:{fact.name}",
                fact.name,
                fact.entity_type,
                language,
                ArchitectureLocator(path=file_info.path, start_line=fact.start_line, end_line=fact.end_line),
                "extractor:local-fact",
                fact.confidence_tier,
                file_info.hash or _hash(fact.name),
                {
                    "path": file_info.path,
                    "local_entity_ordinal": ordinal,
                    **fact.metadata,
                },
                [_evidence(fact.entity_type, "extractor:local-fact", fact.confidence_tier, file_info.path, fact.start_line, fact.end_line, file_info.hash or "", fact.note)],
            )
            local_entities_by_path[file_info.path].append(local_entity)
            entities.append(local_entity)

    symbols = [symbol for values in symbols_by_path.values() for symbol in values]
    symbol_index = _build_symbol_index(symbols)
    local_index: dict[str, list[ArchitectureEntity]] = {}
    for values in local_entities_by_path.values():
        for entity in values:
            for name in {entity.qualified_name, entity.display_name}:
                local_index.setdefault(name, []).append(entity)
    file_index = _build_file_index(file_entities, root)
    _add_fallback_imports(
        ordered_files,
        file_entities,
        facts_by_path,
        entities,
        edges,
        file_index,
        capabilities,
        skip_paths=unaffected_paths,
    )
    unresolved: dict[str, ArchitectureEntity] = {}

    for file_info in ordered_files:
        if file_info.path not in effective_affected:
            continue
        source_file = file_entities[file_info.path]
        facts = facts_by_path[file_info.path]
        for relation in facts.relations:
            stats.re_resolved_relationships += 1
            source = _owner_entity(file_info.path, relation.source_symbol, symbols_by_path, source_file) or source_file
            target, resolution, confidence, candidates = _resolve_target(
                relation.target_name,
                file_info.path,
                relation.relation,
                file_index,
                symbol_index,
                aliases_by_path.get(file_info.path, {}),
                source_symbol=relation.source_symbol,
                symbols_by_path=symbols_by_path,
                local_index=local_index,
            )
            if target is not None and target.entity_key == source.entity_key:
                target, resolution = None, "unresolved"
            if target is None:
                target_type = "unresolved" if relation.relation in {"calls", "references", "inherits", "implements"} else "external"
                key = f"{target_type}:{resolution}:{relation.target_name}:{_hash('|'.join(candidates))}"
                target = unresolved.get(key)
                if target is None:
                    note = "target not found in repository"
                    if resolution == "ambiguous":
                        note = "ambiguous target; candidates were retained as evidence"
                    target = _entity(
                        repo_fingerprint, target_type, key, relation.target_name, relation.target_name, None,
                        ArchitectureLocator(path=relation.target_name), "resolver:unresolved", "best_effort",
                        _hash(relation.target_name), {
                            "target": relation.target_name,
                            "external": target_type == "external",
                            "resolution": resolution,
                            "candidates": candidates,
                        },
                        [_evidence("unresolved", "resolver:unresolved", "best_effort", file_info.path, relation.start_line, relation.end_line, note=note)],
                    )
                    unresolved[key] = target
                    entities.append(target)
            relation_tier = relation.confidence_tier if resolution != "ambiguous" else "best_effort"
            # Parsing is structured for the semantic-core languages, but
            # module resolution remains best-effort until package/workspace
            # aliases are available to the resolver.
            if relation.relation == "imports" and file_info.language in {"javascript", "typescript", "go", "rust"}:
                relation_tier = "best_effort"
            local_fact_note = relation.note in {
                "document link",
                "API route declaration",
                "config path reference",
                "environment variable read",
                "publishes topic",
                "consumes topic",
            } or relation.note.startswith("file open mode ")
            evidence_source = "extractor:local-fact" if local_fact_note else "extractor:tree_sitter"
            edges.append(_edge(
                source, target, relation.relation, relation_tier, file_info.path,
                relation.start_line, relation.end_line, relation.note,
                metadata={"target_name": relation.target_name, "resolution": resolution},
                evidence_source=evidence_source,
            ))

        if source_file.entity_type != "test":
            for test_path in find_related_tests(file_info.path, set(file_entities)):
                test_entity = file_entities.get(test_path)
                if test_entity is not None:
                    edges.append(_edge(source_file, test_entity, "tested_by", "best_effort", file_info.path, note="path-based related test"))

    if previous_snapshot is not None and unaffected_paths:
        previous_entities = {entity.entity_key: entity for entity in previous_snapshot.entities}
        previous_edges = [
            edge for edge in previous_snapshot.edges
            if _edge_evidence_path(edge) in unaffected_paths
        ]
        # Replace newly reconstructed local payloads with the exact previous
        # payload. This is what makes incremental snapshots byte-equivalent for
        # all unaffected records, including evidence ordering and revisions.
        entity_map = {entity.entity_key: entity for entity in entities}
        for entity in previous_snapshot.entities:
            if entity.locator.path in unaffected_paths:
                entity_map[entity.entity_key] = entity
        for edge in previous_edges:
            entity_map.setdefault(edge.source_entity_key, previous_entities.get(edge.source_entity_key))
            entity_map.setdefault(edge.target_entity_key, previous_entities.get(edge.target_entity_key))
        entities = [entity for entity in entity_map.values() if entity is not None]
        edges = [edge for edge in edges if _edge_evidence_path(edge) not in unaffected_paths]
        edges.extend(previous_edges)

    # Stable dedupe by edge key keeps repeated identifier/call captures from
    # inflating the graph while preserving the first source evidence.
    unique_edges = {edge.edge_key: edge for edge in edges}
    _write_cache_manifest(
        facts_cache_dir,
        repo_fingerprint,
        extractor_profile_hash,
        current_manifest,
        previous_manifest,
        stats,
    )
    if cache_stats is not None:
        cache_stats.update(stats.as_dict())
    return _sort_entities(entities), sorted(unique_edges.values(), key=lambda edge: (edge.edge_type, edge.source_entity_key, edge.target_entity_key)), file_hashes


def _load_or_extract_facts(
    file_info: FileInfo,
    cache_dir: Path | None,
    records_cache_dir: Path | None,
    repo_fingerprint: str,
    extractor_profile_hash: str,
    *,
    extract_references: bool,
    stats: SemanticGraphCacheStats | None = None,
) -> SemanticFacts:
    """Reuse parsed facts without allowing stale extractor output to leak in."""
    record_path = _record_cache_path(records_cache_dir, file_info, repo_fingerprint, extractor_profile_hash, extract_references)
    if record_path is not None:
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if (
                record.get("schema_version") == SEMANTIC_SCHEMA_VERSION
                and record.get("extractor_profile_hash") == extractor_profile_hash
                and record.get("repository_identity") == repo_fingerprint
                and record.get("path") == file_info.path
                and record.get("content_hash") == (file_info.hash or "")
                and isinstance(record.get("facts"), dict)
            ):
                if stats is not None:
                    stats.reused_files += 1
                return _facts_from_json(record["facts"])
        except (OSError, ValueError, TypeError):
            pass
    cache_path = _facts_cache_path(
        cache_dir,
        file_info,
        repo_fingerprint,
        extractor_profile_hash,
        extract_references=extract_references,
    )
    if cache_path is not None:
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                payload.get("cache_version") == FACTS_CACHE_VERSION
                and payload.get("schema_version") == SEMANTIC_SCHEMA_VERSION
                and payload.get("path") == file_info.path
                and payload.get("file_hash") == (file_info.hash or "")
                and payload.get("language") == (file_info.language or "text")
                and payload.get("extractor_profile_hash") == extractor_profile_hash
                and bool(payload.get("extract_references")) == extract_references
            ):
                if stats is not None:
                    stats.reused_files += 1
                return _facts_from_json(payload.get("facts") or {})
        except (OSError, ValueError, TypeError):
            pass

    facts = extract_semantic_facts(
        file_info.abs_path,
        file_info.language or "text",
        extract_references=extract_references,
        max_references_per_symbol=8,
    )
    _augment_local_facts(file_info, facts)
    if stats is not None:
        stats.parsed_files += 1
    if cache_path is not None:
        payload = {
            "cache_version": FACTS_CACHE_VERSION,
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "path": file_info.path,
            "file_hash": file_info.hash or "",
            "repo_fingerprint": repo_fingerprint,
            "language": file_info.language or "text",
            "extractor_profile_hash": extractor_profile_hash,
            "extract_references": extract_references,
            "facts": _facts_to_json(facts),
        }
        try:
            _atomic_write_json(cache_path, payload)
        except OSError:
            pass
    return facts


def _augment_local_facts(file_info: FileInfo, facts: SemanticFacts) -> None:
    """Persist deterministic non-tree-sitter signals with the parsed facts.

    These relationships are intentionally extracted once, at the file-record
    boundary. Materialization must resolve cached candidates, not reread every
    source file on each graph build.
    """
    try:
        content = file_info.abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    existing_entities = {(item.entity_type, item.name) for item in facts.local_entities}
    existing_relations = {
        (item.relation, item.source_symbol, item.target_name, item.start_line)
        for item in facts.relations
    }

    def add_entity(entity_type: str, name: str, line: int, *, metadata: dict | None = None, note: str) -> None:
        key = (entity_type, name)
        if key in existing_entities:
            return
        facts.local_entities.append(
            SemanticLocalEntityFact(
                entity_type=entity_type,
                name=name,
                start_line=line,
                end_line=line,
                metadata=metadata or {},
                note=note,
                confidence_tier="best_effort",
            )
        )
        existing_entities.add(key)

    def add_relation(relation: str, target: str, line: int, note: str) -> None:
        key = (relation, None, target, line)
        if key in existing_relations:
            return
        facts.relations.append(
            SemanticRelationFact(
                relation=relation,
                source_symbol=None,
                target_name=target,
                start_line=line,
                end_line=line,
                note=note,
                confidence_tier="best_effort",
            )
        )
        existing_relations.add(key)

    if file_info.language == "markdown":
        for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)|`([^`]+\.(?:py|js|ts|go|rs|java|kt|rb|php))`", content):
            target = (match.group(1) or match.group(2) or "").split("#", 1)[0].strip()
            if not target or target.startswith(("http:", "https:", "mailto:")):
                continue
            line = content.count("\n", 0, match.start()) + 1
            add_relation("documents", target, line, "document link")

    if file_info.language == "ruby":
        for match in re.finditer(r"\brequire\s+[\"']([^\"']+)[\"']", content):
            line = content.count("\n", 0, match.start()) + 1
            add_relation("imports", match.group(1), line, "Ruby require path")
    elif file_info.language == "php":
        for match in re.finditer(r"^\s*use\s+([^;]+);", content, re.MULTILINE):
            line = content.count("\n", 0, match.start()) + 1
            add_relation("imports", match.group(1).strip().replace("\\", "."), line, "PHP namespace import")
    elif file_info.language in {"java", "kotlin"}:
        for match in re.finditer(r"^\s*import\s+([\w.]+)", content, re.MULTILINE):
            line = content.count("\n", 0, match.start()) + 1
            add_relation("imports", match.group(1), line, "package import")

    if file_info.language not in {"module", "config"} and file_info.language not in {"python", "javascript", "typescript", "go", "rust", "java", "kotlin", "ruby", "php"}:
        return

    route_patterns = (
        r"@[^\n]*?\.(get|post|put|patch|delete|route)\s*\(\s*[\"']([^\"']+)",
        r"\b(?:app|router)\.(get|post|put|patch|delete|route)\s*\(\s*[\"']([^\"']+)",
        r"@(?:GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*\(\s*[\"']?([^\"')]+)",
    )
    for pattern in route_patterns:
        for match in re.finditer(pattern, content, re.IGNORECASE):
            method, route = (match.group(1).upper(), match.group(2)) if len(match.groups()) == 2 else ("HTTP", match.group(1))
            line = content.count("\n", 0, match.start()) + 1
            name = f"route:{method}:{route.strip()}"
            add_entity("api", name, line, metadata={"method": method, "route": route.strip()}, note="API route declaration")
            add_relation("contains", name, line, "API route declaration")

    if file_info.language in CONFIG_LANGUAGES:
        for match in re.finditer(r"(?:^|[\"'])([^\"'\s]+\.(?:py|js|ts|go|rs|java|kt|rb|php))(?:[\"']|$)", content, re.MULTILINE):
            line = content.count("\n", 0, match.start()) + 1
            add_relation("configures", match.group(1), line, "config path reference")

    for match in re.finditer(r"(?:getenv|environ|get_env)\s*\(?\s*[\"']([A-Z][A-Z0-9_]+)", content):
        line = content.count("\n", 0, match.start()) + 1
        target = "env:" + match.group(1)
        add_entity("external", target, line, note="environment variable")
        add_relation("reads_from", target, line, "environment variable read")

    for match in re.finditer(r"open\s*\(\s*[\"']([^\"']+)[\"'](?:\s*,\s*[\"']([^\"']+)[\"'])?", content):
        mode = match.group(2) or "r"
        relation = "writes_to" if any(flag in mode for flag in ("w", "a", "+", "x")) else "reads_from"
        line = content.count("\n", 0, match.start()) + 1
        target = "file:" + match.group(1)
        add_entity("external", target, line, note="file effect target")
        add_relation(relation, target, line, f"file open mode {mode}")

    for pattern, relation in ((r"(?:publish|emit)\s*\(\s*[\"']([^\"']+)", "publishes"), (r"(?:consume|subscribe)\s*\(\s*[\"']([^\"']+)", "consumes")):
        for match in re.finditer(pattern, content):
            line = content.count("\n", 0, match.start()) + 1
            target = "topic:" + match.group(1)
            add_entity("queue", target, line, note=f"{relation} topic")
            add_relation(relation, target, line, f"{relation} topic")


def _record_cache_path(
    records_dir: Path | None,
    file_info: FileInfo,
    repo_fingerprint: str,
    extractor_profile_hash: str,
    extract_references: bool,
) -> Path | None:
    if records_dir is None or file_info.hash is None:
        return None
    key = hashlib.sha256(
        "|".join(
            (
                repo_fingerprint,
                file_info.path,
                file_info.hash,
                str(SEMANTIC_SCHEMA_VERSION),
                extractor_profile_hash,
                str(extract_references),
            )
        ).encode("utf-8")
    ).hexdigest()[:32]
    return records_dir / f"{key}.json"


def _facts_cache_path(
    cache_dir: Path | None,
    file_info: FileInfo,
    repo_fingerprint: str,
    extractor_profile_hash: str,
    *,
    extract_references: bool,
) -> Path | None:
    if cache_dir is None or file_info.hash is None:
        return None
    key = _hash(
        "|".join(
            (
                file_info.path,
                file_info.hash,
                file_info.language or "text",
                repo_fingerprint,
                extractor_profile_hash,
                str(extract_references),
            )
        )
    )
    return cache_dir / f"{key}.json"


def _manifest_path(cache_dir: Path | None, repo_fingerprint: str, extractor_profile_hash: str) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir.parent / "manifests" / f"manifest-{repo_fingerprint}-{extractor_profile_hash}.json"


def _load_cache_manifest(cache_dir: Path | None, repo_fingerprint: str, extractor_profile_hash: str) -> dict:
    path = _manifest_path(cache_dir, repo_fingerprint, extractor_profile_hash)
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if (
        payload.get("cache_version") != MANIFEST_CACHE_VERSION
        or payload.get("schema_version") != SEMANTIC_SCHEMA_VERSION
        or payload.get("repo_fingerprint") != repo_fingerprint
        or payload.get("extractor_profile_hash") != extractor_profile_hash
    ):
        return {}
    return payload


def _manifest_for_files(files: list[FileInfo]) -> dict:
    records: dict[str, dict[str, object]] = {}
    for item in files:
        records[item.path] = {
            "file_hash": item.hash or "",
            "language": item.language or "text",
            "estimated_tokens": item.estimated_tokens,
        }
    return {"files": records}


def _invalidation_sets(previous: dict, current: dict) -> dict[str, set[str]]:
    old = previous.get("files") if isinstance(previous.get("files"), dict) else {}
    new = current.get("files") if isinstance(current.get("files"), dict) else {}
    old_paths = set(old)
    new_paths = set(new)
    changed = {
        path for path in old_paths & new_paths
        if old[path].get("file_hash") != new[path].get("file_hash")
        or old[path].get("language") != new[path].get("language")
    }
    changed |= new_paths - old_paths
    deleted = old_paths - new_paths
    old_symbols = {
        str(symbol)
        for path in changed | deleted
        for symbol in ((old.get(path) or {}).get("symbols") or [])
        if isinstance(old.get(path), dict)
    }
    new_symbols = {
        str(symbol)
        for path in changed
        for symbol in ((new.get(path) or {}).get("symbols") or [])
        if isinstance(new.get(path), dict)
    }
    changed_symbols = old_symbols | new_symbols
    # The manifest stores raw dependency metadata after extraction. Invalidate
    # reverse importers and candidate consumers, then close transitively so a
    # provider change cannot leave a downstream resolution stale.
    affected = set(changed) | set(deleted)
    manifest_names = {"package.json", "tsconfig.json", "go.mod", "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "gemfile", "composer.json"}
    if any(Path(path).name.lower() in manifest_names for path in changed | deleted):
        affected.update(new_paths)

    records = [*(old.items()), *(new.items())]
    changed_paths = changed | deleted
    changed_names = changed_symbols | changed_paths
    expanded = True
    while expanded:
        expanded = False
        for path, record in records:
            if not isinstance(record, dict) or path in affected:
                continue
            imports = {str(item) for item in record.get("imports") or []}
            candidates = imports | {str(item) for item in record.get("targets") or []}
            resolved_imports = {
                candidate
                for raw in imports
                for candidate in _file_candidates(raw, path)
            }
            target_paths = {
                candidate
                for raw in candidates
                for candidate in _file_candidates(raw, path)
            }
            if (
                resolved_imports & affected
                or target_paths & changed_paths
                or candidates & changed_names
                or any(name.rsplit("/", 1)[-1] in changed_names for name in candidates)
            ):
                affected.add(path)
                expanded = True
    return {"changed": changed, "deleted": deleted, "affected": affected}


def _write_cache_manifest(
    cache_dir: Path | None,
    repo_fingerprint: str,
    extractor_profile_hash: str,
    current: dict,
    previous: dict,
    stats: SemanticGraphCacheStats,
) -> None:
    path = _manifest_path(cache_dir, repo_fingerprint, extractor_profile_hash)
    if path is None:
        return
    records = dict(current.get("files") or {})
    old_records = previous.get("files") if isinstance(previous.get("files"), dict) else {}
    for file_path, record in records.items():
        old = old_records.get(file_path) if isinstance(old_records, dict) else None
        if isinstance(old, dict):
            record["cache_path"] = old.get("cache_path", "")
    # Cache paths are content-addressed. Reconstruct the current path so a
    # deleted file's old record can be removed without scanning the cache.
    for file_path, record in records.items():
        record["cache_path"] = _hash(
            "|".join((file_path, str(record.get("file_hash") or ""), str(record.get("language") or "text"), repo_fingerprint, extractor_profile_hash, "True" if int(record.get("estimated_tokens") or 0) <= 8000 else "False"))
        ) + ".json"
    deleted_records = (
        (set(old_records) - set(records))
        if isinstance(old_records, dict)
        else set()
    )
    for deleted_path in deleted_records:
        old = old_records.get(deleted_path)
        cache_name = old.get("cache_path") if isinstance(old, dict) else ""
        if cache_name:
            try:
                (cache_dir / cache_name).unlink(missing_ok=True)
            except OSError:
                pass
    importers_by_module: dict[str, list[str]] = {}
    referencers_by_symbol: dict[str, list[str]] = {}
    relationship_users: dict[str, dict[str, list[str]]] = {
        "inherits": {},
        "implements": {},
        "documents": {},
        "configures": {},
        "tested_by": {},
        "effects": {},
    }
    for file_path, record in records.items():
        if not isinstance(record, dict):
            continue
        for module in record.get("imports", []) or []:
            importers_by_module.setdefault(str(module), []).append(file_path)
        for symbol in record.get("targets", []) or []:
            referencers_by_symbol.setdefault(str(symbol), []).append(file_path)
        relation_types = set(record.get("relation_types") or [])
        for relation in relation_types & set(relationship_users):
            bucket = relationship_users[relation]
            for target in record.get("targets", []) or []:
                bucket.setdefault(str(target), []).append(file_path)
        if relation_types & {"reads_from", "writes_to", "publishes", "consumes"}:
            for target in record.get("targets", []) or []:
                relationship_users["effects"].setdefault(str(target), []).append(file_path)
    reverse_dependencies = {
        "importers_by_module": {key: sorted(set(value)) for key, value in sorted(importers_by_module.items())},
        "referencers_by_symbol": {key: sorted(set(value)) for key, value in sorted(referencers_by_symbol.items())},
        **{
            f"{key}_users": {name: sorted(set(paths)) for name, paths in sorted(values.items())}
            for key, values in relationship_users.items()
        },
    }
    payload = {
        "cache_version": MANIFEST_CACHE_VERSION,
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "repo_fingerprint": repo_fingerprint,
        "extractor_profile_hash": extractor_profile_hash,
        "files": records,
        "reverse_dependencies": reverse_dependencies,
    }
    try:
        _atomic_write_json(path, payload)
        stats.cache_manifest_writes += 1
    except OSError:
        pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _facts_to_json(facts: SemanticFacts) -> dict[str, object]:
    return {
        "symbols": [
            {
                "name": item.name,
                "kind": item.kind,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "signature": item.signature,
                "body": item.body,
            }
            for item in facts.symbols
        ],
        "relations": [
            {
                "relation": item.relation,
                "source_symbol": item.source_symbol,
                "target_name": item.target_name,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "note": item.note,
                "confidence_tier": item.confidence_tier,
            }
            for item in facts.relations
        ],
        "comments": [list(item) for item in facts.comments],
        "exports": list(facts.exports),
        "reexports": list(facts.reexports),
        "aliases": dict(facts.aliases),
        "local_entities": [
            {
                "entity_type": item.entity_type,
                "name": item.name,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "source_symbol": item.source_symbol,
                "metadata": item.metadata,
                "note": item.note,
                "confidence_tier": item.confidence_tier,
            }
            for item in facts.local_entities
        ],
    }


def _facts_from_json(payload: dict[str, object]) -> SemanticFacts:
    from agentpack.analysis.tree_sitter_backend import SemanticLocalEntityFact, SemanticRelationFact, SemanticSymbolFact

    symbols = [
        SemanticSymbolFact(
            str(item.get("name") or ""),
            str(item.get("kind") or "function"),
            int(item.get("start_line") or 1),
            int(item.get("end_line") or 1),
            str(item.get("signature") or ""),
            str(item.get("body") or ""),
            0,
        )
        for item in payload.get("symbols", [])
        if isinstance(item, dict)
    ]
    relations = [
        SemanticRelationFact(
            str(item.get("relation") or "references"),
            str(item["source_symbol"]) if item.get("source_symbol") is not None else None,
            str(item.get("target_name") or ""),
            int(item.get("start_line") or 1),
            int(item.get("end_line") or 1),
            str(item.get("note") or ""),
            str(item.get("confidence_tier") or "structured"),
        )
        for item in payload.get("relations", [])
        if isinstance(item, dict)
    ]
    comments = [
        (str(item[0]), int(item[1]), int(item[2]), str(item[3]) if item[3] is not None else None)
        for item in payload.get("comments", [])
        if isinstance(item, list) and len(item) == 4
    ]
    local_entities = [
        SemanticLocalEntityFact(
            entity_type=str(item.get("entity_type") or "external"),
            name=str(item.get("name") or ""),
            start_line=int(item.get("start_line") or 1),
            end_line=int(item.get("end_line") or item.get("start_line") or 1),
            source_symbol=str(item["source_symbol"]) if item.get("source_symbol") is not None else None,
            metadata=dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {},
            note=str(item.get("note") or ""),
            confidence_tier=str(item.get("confidence_tier") or "best_effort"),
        )
        for item in payload.get("local_entities", [])
        if isinstance(item, dict)
    ]
    return SemanticFacts(
        symbols=symbols,
        relations=relations,
        comments=comments,
        exports=[str(item) for item in payload.get("exports", []) if item],
        reexports=[str(item) for item in payload.get("reexports", []) if item],
        aliases={str(key): str(value) for key, value in (payload.get("aliases") or {}).items()} if isinstance(payload.get("aliases"), dict) else {},
        local_entities=local_entities,
    )


def _add_fallback_imports(files, file_entities, facts_by_path, entities, edges, file_index, capabilities, *, skip_paths: set[str] | None = None) -> None:
    """Add file-level import edges from cached semantic candidates.

    This is intentionally a projection of the same local facts used by the
    semantic resolver.  Calling the legacy dependency scanner here caused an
    incremental graph build to parse the repository a second time and could
    disagree with semantic resolution.
    """
    skip_paths = skip_paths or set()
    for file_info in files:
        if file_info.path in skip_paths:
            continue
        source = file_entities[file_info.path]
        if capabilities.get(file_info.language or "", "file_level") == "unavailable":
            continue
        targets = {
            relation.target_name
            for relation in facts_by_path.get(file_info.path, SemanticFacts()).relations
            if relation.relation == "imports"
        }
        for raw_target in sorted(targets):
            candidates = [
                entity
                for path in _file_candidates(raw_target, file_info.path)
                for entity in file_index.get(path, [])
            ]
            unique_targets = {entity.entity_key: entity for entity in candidates}
            if len(unique_targets) != 1:
                continue
            target = next(iter(unique_targets.values()))
            tier = capabilities.get(file_info.language or "", "file_level")
            if file_info.language in {"javascript", "typescript", "go", "rust"}:
                tier = "best_effort"
            relation = next(
                relation for relation in facts_by_path[file_info.path].relations
                if relation.relation == "imports" and relation.target_name == raw_target
            )
            edges.append(_edge(
                source,
                target,
                "imports",
                tier,
                file_info.path,
                relation.start_line,
                relation.end_line,
                "file import resolver",
                {"target_name": raw_target, "resolution": "resolved_file"},
            ))


def _resolve_target(
    target_name,
    source_path,
    relation,
    file_index,
    symbol_index,
    aliases=None,
    *,
    source_symbol=None,
    symbols_by_path=None,
    local_index=None,
):
    raw = target_name.strip().strip("'\"")
    aliases = aliases or {}
    symbols_by_path = symbols_by_path or {}
    local_index = local_index or {}
    local_symbols = symbols_by_path.get(source_path, [])
    for alias, original in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if raw == alias or raw.startswith(alias + "."):
            break
    raw = re.sub(r"^(?:extends|implements|super|trait)\s+", "", raw).strip()
    raw = raw.replace("::", ".")
    raw = raw.split("<", 1)[0].strip()

    def unique(values):
        return list({entity.entity_key: entity for entity in values}.values())

    # Resolve lexical and same-file symbols before consulting repository-wide
    # indexes. This prevents a duplicate helper in another module from winning
    # over the symbol visible at the source location.
    local_matches = [
        entity for entity in local_symbols
        if entity.display_name == raw
        or entity.qualified_name == raw
        or entity.qualified_name.endswith("." + raw)
    ]
    if source_symbol and "." in source_symbol and "." not in raw:
        scope = source_symbol.rsplit(".", 1)[0]
        scoped = [entity for entity in local_symbols if entity.qualified_name == f"{scope}.{raw}"]
        local_matches = scoped or local_matches
    local_matches = unique(local_matches)
    if len(local_matches) == 1:
        return local_matches[0], "resolved_same_file", "structured", [local_matches[0].qualified_name]
    if len(local_matches) > 1:
        return None, "ambiguous", "best_effort", sorted(entity.qualified_name for entity in local_matches)

    for alias, original in sorted(aliases.items(), key=lambda item: (-len(item[0]), item[0])):
        if raw == alias:
            raw = original
            break
        if raw.startswith(alias + "."):
            raw = original + raw[len(alias):]
            break
    if relation == "imports" or relation in {"documents", "configures"}:
        candidates = _file_candidates(raw, source_path)
        matches = [entity for item in candidates for entity in file_index.get(item, [])]
    else:
        matches = local_index.get(raw, []) or local_index.get(raw.rsplit(".", 1)[-1], [])
        matches = matches or symbol_index.get(raw, []) or symbol_index.get(raw.rsplit(".", 1)[-1], [])
        if len(matches) == 0:
            matches = [entity for name, values in symbol_index.items() if name.endswith("." + raw) for entity in values]
    matches = unique(matches)
    if len(matches) == 1:
        return matches[0], "resolved", "structured", [matches[0].qualified_name]
    if len(matches) > 1:
        return None, "ambiguous", "best_effort", sorted(entity.qualified_name for entity in matches)
    return None, "unresolved", "best_effort", []


def _file_candidates(raw, source_path):
    raw = raw.strip()
    if raw.startswith("."):
        dot_count = len(raw) - len(raw.lstrip("."))
        base = posixpath.dirname(source_path)
        for _ in range(max(0, dot_count - 1)):
            base = posixpath.dirname(base)
        value = posixpath.normpath(posixpath.join(base, raw[dot_count:].lstrip("/")))
    else:
        value = raw.replace("\\", "/")
    value = value.strip("/")
    path = value.replace(".", "/")
    options = [
        raw,
        value,
        path,
        path + ".py",
        path + ".js",
        path + ".ts",
        path + ".tsx",
        path + ".go",
        path + ".rs",
        path + ".java",
        path + ".kt",
        path + ".rb",
        path + ".php",
        f"{path}/__init__.py",
        f"{path}/index.ts",
        f"{path}/index.js",
    ]
    return list(dict.fromkeys(item for item in options if item and item != "."))


def _build_file_index(file_entities, root: Path):
    index: dict[str, list[ArchitectureEntity]] = {}

    def register(alias: str, entity: ArchitectureEntity) -> None:
        normalized = alias.replace("\\", "/").strip("/")
        if not normalized:
            return
        bucket = index.setdefault(normalized, [])
        if entity not in bucket:
            bucket.append(entity)

    for path in file_entities:
        entity = file_entities[path]
        register(path, entity)
        register(_module_name(path), entity)
        register(Path(path).stem, entity)
        if path.endswith("/__init__.py"):
            register(path.removesuffix("/__init__.py"), entity)
        _register_declared_package_aliases(root, path, entity, register)
    _register_workspace_aliases(root, file_entities, register)
    _register_language_manifest_aliases(root, file_entities, register)
    return index


def _register_declared_package_aliases(root, path, entity, register) -> None:
    """Index language-declared package/module names without guessing targets."""
    try:
        content = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    package = re.search(r"^\s*package\s+([A-Za-z_][\w.]*)\s*;", content, re.MULTILINE)
    if package:
        register(package.group(1), entity)
    namespace = re.search(r"^\s*namespace\s+([^;{]+)", content, re.MULTILINE)
    if namespace:
        register(namespace.group(1).strip().replace("\\", "."), entity)


def _register_workspace_aliases(root, file_entities, register) -> None:
    """Register package/workspace aliases from standard manifests."""
    manifests = sorted(root.rglob("package.json"))
    for manifest in manifests:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        package_name = payload.get("name") if isinstance(payload, dict) else None
        if not isinstance(package_name, str) or not package_name:
            continue
        package_root = manifest.parent.relative_to(root).as_posix()
        for path, entity in file_entities.items():
            if package_root and not (path == package_root or path.startswith(package_root + "/")):
                continue
            relative = path[len(package_root):].lstrip("/") if package_root else path
            stem = Path(relative).with_suffix("").as_posix()
            if stem.endswith("/index"):
                stem = stem.removesuffix("/index")
            register(f"{package_name}/{stem}".rstrip("/"), entity)
        exports = payload.get("exports") if isinstance(payload, dict) else None
        if isinstance(exports, dict):
            for export_name, export_target in exports.items():
                targets = [export_target] if isinstance(export_target, str) else []
                if isinstance(export_target, dict):
                    targets = [value for value in export_target.values() if isinstance(value, str)]
                for target in targets:
                    target_path = (manifest.parent / target.lstrip("./")).relative_to(root).as_posix()
                    target_entity = file_entities.get(target_path)
                    if target_entity is None and target_path.endswith("/index.js"):
                        target_entity = file_entities.get(target_path.removesuffix("/index.js") + "/index.ts")
                    if target_entity is not None:
                        register(f"{package_name}/{str(export_name).lstrip('./')}".rstrip("/"), target_entity)

    for tsconfig in sorted(root.rglob("tsconfig.json")):
        try:
            payload = json.loads(tsconfig.read_text(encoding="utf-8"))
            compiler = payload.get("compilerOptions", {}) if isinstance(payload, dict) else {}
            paths = compiler.get("paths", {}) if isinstance(compiler, dict) else {}
            base_url = str(compiler.get("baseUrl") or ".") if isinstance(compiler, dict) else "."
        except (OSError, ValueError):
            continue
        if not isinstance(paths, dict):
            continue
        config_root = tsconfig.parent
        for alias, targets in paths.items():
            if not isinstance(alias, str) or not isinstance(targets, list):
                continue
            alias_key = alias.removesuffix("/*")
            for target in targets:
                if not isinstance(target, str):
                    continue
                target_key = target.removesuffix("/*")
                target_path = (config_root / base_url / target_key).resolve()
                try:
                    relative = target_path.relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue
                for file_path, entity in file_entities.items():
                    if file_path == relative or file_path.startswith(relative.rstrip("/") + "/"):
                        remainder = file_path[len(relative):].lstrip("/")
                        stem = Path(remainder).with_suffix("").as_posix() if remainder else ""
                        register(f"{alias_key}/{stem}".rstrip("/"), entity)
                for suffix in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
                    entity = file_entities.get(relative + suffix)
                    if entity is not None:
                        register(alias_key, entity)
                        break

    try:
        go_mod = root / "go.mod"
        match = re.search(r"^module\s+(\S+)", go_mod.read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        match = None
    if match:
        module = match.group(1)
        for path, entity in file_entities.items():
            if path.endswith(".go"):
                register(f"{module}/{Path(path).parent.as_posix()}".rstrip("/"), entity)

    for cargo in sorted(root.rglob("Cargo.toml")):
        try:
            payload = tomllib.loads(cargo.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        package = payload.get("package") if isinstance(payload, dict) else None
        crate = package.get("name") if isinstance(package, dict) else None
        if not isinstance(crate, str) or not crate:
            continue
        crate_root = cargo.parent.relative_to(root).as_posix()
        for path, entity in file_entities.items():
            if path == f"{crate_root}/src/lib.rs" or path == f"{crate_root}/src/main.rs":
                register(crate.replace("-", "_"), entity)


def _register_language_manifest_aliases(root: Path, file_entities, register) -> None:
    """Index language-specific package roots without resolving symbols eagerly."""
    for path, entity in file_entities.items():
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            relative = path[:-3].replace("/", ".")
            if relative.endswith(".__init__"):
                relative = relative[:-9]
            register(relative, entity)
            register(relative.replace(".", "/"), entity)
        elif suffix in {".java", ".kt"}:
            try:
                content = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            package = re.search(r"^\s*package\s+([A-Za-z_][\w.]*)", content, re.MULTILINE)
            if package:
                register(f"{package.group(1)}.{Path(path).stem}", entity)
        elif suffix == ".rb":
            relative = path[:-3].lstrip("./")
            register(relative, entity)
            register(relative.removeprefix("lib/"), entity)

    for composer in sorted(root.rglob("composer.json")):
        try:
            payload = json.loads(composer.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        autoload = payload.get("autoload", {}) if isinstance(payload, dict) else {}
        psr4 = autoload.get("psr-4", {}) if isinstance(autoload, dict) else {}
        if not isinstance(psr4, dict):
            continue
        for namespace, roots in psr4.items():
            root_values = roots if isinstance(roots, list) else [roots]
            for root_value in root_values:
                if not isinstance(root_value, str):
                    continue
                base = (composer.parent / root_value).resolve()
                for path, entity in file_entities.items():
                    absolute = (root / path).resolve()
                    try:
                        relative = absolute.relative_to(base).as_posix()
                    except ValueError:
                        continue
                    if relative.endswith(".php"):
                        name = relative[:-4].replace("/", "\\")
                        register(namespace + name, entity)


def _import_aliases(file_info: FileInfo) -> dict[str, str]:
    """Normalize common import aliases before symbol resolution."""
    try:
        content = file_info.abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    aliases: dict[str, str] = {}
    language = file_info.language or ""
    if language == "python":
        for match in re.finditer(r"^\s*from\s+[\w.]+\s+import\s+([\w]+)(?:\s+as\s+([\w]+))?", content, re.MULTILINE):
            aliases[match.group(2) or match.group(1)] = match.group(1)
        for match in re.finditer(r"^\s*import\s+([\w.]+)(?:\s+as\s+([\w]+))?", content, re.MULTILINE):
            aliases[match.group(2) or match.group(1).split(".")[-1]] = match.group(1)
    elif language in {"javascript", "typescript"}:
        for match in re.finditer(r"\bimport\s+(?:type\s+)?([A-Za-z_$][\w$]*)\s+from\s+[\"']([^\"']+)", content):
            aliases[match.group(1)] = match.group(1)
        for match in re.finditer(r"\bimport\s*\{([^}]+)\}\s*from", content):
            for item in match.group(1).split(","):
                parts = re.split(r"\s+as\s+", item.strip())
                if parts and parts[0]:
                    aliases[parts[-1].strip()] = parts[0].strip()
    elif language == "go":
        for match in re.finditer(r"^\s*([\w]+)\s+[\"']([^\"']+)[\"']", content, re.MULTILINE):
            aliases[match.group(1)] = match.group(2).split("/")[-1]
    elif language == "rust":
        for match in re.finditer(r"\buse\s+([^;]+?)\s+as\s+([A-Za-z_][\w]*)", content):
            aliases[match.group(2)] = match.group(1).split("::")[-1]
    elif language in {"java", "kotlin"}:
        for match in re.finditer(r"^\s*import\s+([\w.]+)", content, re.MULTILINE):
            aliases[match.group(1).split(".")[-1]] = match.group(1).split(".")[-1]
    elif language == "php":
        for match in re.finditer(r"\buse\s+([^;]+?)(?:\s+as\s+([A-Za-z_][\w]*))?\s*;", content):
            qualified = match.group(1).strip().replace("\\", ".")
            aliases[match.group(2) or qualified.rsplit(".", 1)[-1]] = qualified.rsplit(".", 1)[-1]
    elif language == "ruby":
        for match in re.finditer(r"\brequire\s+[\"']([^\"']+)[\"']", content):
            aliases[Path(match.group(1)).stem] = Path(match.group(1)).stem
    return aliases


def _build_symbol_index(symbols):
    index = {}
    for entity in symbols:
        index.setdefault(entity.qualified_name.split(":", 1)[-1], []).append(entity)
        index.setdefault(entity.display_name, []).append(entity)
        index.setdefault(entity.qualified_name, []).append(entity)
    return index


def _owner_entity(path, owner, symbols_by_path, fallback):
    if not owner:
        return fallback
    for entity in symbols_by_path.get(path, []):
        if entity.qualified_name.endswith(":" + owner) or entity.display_name == owner.rsplit(".", 1)[-1]:
            return entity
    return fallback


def _entity(repo, entity_type, qualified, display, signature, language, locator, provenance, tier, source_hash, metadata, evidence):
    structural = "|".join(
        (
            qualified,
            _normalize(signature),
            str(metadata.get("path") or locator.path),
            str(metadata.get("lexical_scope") or ""),
            str(metadata.get("declaration_ordinal") or ""),
        )
    )
    key = _hash(f"{repo}|{entity_type}|{structural}")
    return ArchitectureEntity(entity_key=key, revision_id=_hash(f"{key}|{source_hash}"), entity_type=entity_type, qualified_name=qualified, display_name=display, normalized_signature=_normalize(signature), language=language, locator=locator, provenance=provenance, confidence_tier=tier, source_hash=source_hash, metadata=dict(metadata), evidence=list(evidence))


def _edge(source, target, edge_type, tier, path, start_line=None, end_line=None, note="", metadata=None, evidence_source=None):
    source_name = evidence_source or ("extractor:tree_sitter" if "tree_sitter" in source.provenance else "extractor:graph")
    evidence = _evidence(edge_type, source_name, tier, path, start_line, end_line, _hash(f"{path}:{start_line}:{end_line}:{note}"), note)
    candidate_anchor = str((metadata or {}).get("target_name") or "")
    anchor = f"{path}:{start_line}:{end_line}:{candidate_anchor}:{note}"
    key = _hash(f"{source.entity_key}|{edge_type}|{target.entity_key}|{anchor}")
    return ArchitectureEdge(edge_key=key, revision_id=_hash(f"{key}|{evidence.source_hash}|{tier}"), edge_type=edge_type, source_entity_key=source.entity_key, target_entity_key=target.entity_key, confidence_tier=tier, metadata=dict(metadata or {}), evidence=[evidence])


def _edge_evidence_path(edge: ArchitectureEdge) -> str:
    return edge.evidence[0].path if edge.evidence else ""


def _evidence(kind, source, tier, path, start_line=None, end_line=None, source_hash="", note=""):
    return ArchitectureEvidence(kind=kind, source=source, confidence_tier=tier, confidence=float(CONFIDENCE_ORDER.get(tier, 1)) / 3.0, path=path, start_line=start_line, end_line=end_line, source_hash=source_hash, note=note)


def _file_entity_type(path, language):
    lowered = path.lower()
    if "/tests/" in f"/{lowered}" or lowered.startswith("tests/") or Path(path).name.startswith("test_") or ".test." in lowered or ".spec." in lowered:
        return "test"
    if language in CONFIG_LANGUAGES:
        return "config"
    if language == "markdown":
        return "document"
    return "module"


def _module_name(path):
    return ".".join(Path(path).with_suffix("").parts)


def _line_count(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").count("\n") + 1
    except OSError:
        return 1


def _comment_display(value):
    return " ".join(value.split())[:120]


def _normalize(value):
    return " ".join((value or "").split())


def _hash(value):
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:20]


def _sort_entities(entities):
    unique = {entity.entity_key: entity for entity in entities}
    return sorted(unique.values(), key=lambda entity: (entity.entity_type, entity.qualified_name, entity.locator.path, entity.locator.start_line or 0))
