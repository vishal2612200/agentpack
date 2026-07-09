from __future__ import annotations

from pathlib import Path

from agentpack.core.models import DependencyGraph, DependencyNode, FileInfo
from agentpack.analysis.python_imports import extract_imports as py_imports
from agentpack.analysis.python_imports import resolve_relative_import as py_resolve
from agentpack.analysis.js_ts_imports import extract_imports as js_imports
from agentpack.analysis.js_ts_imports import resolve_relative_import as js_resolve
from agentpack.analysis.go_imports import extract_imports as go_imports
from agentpack.analysis.rust_imports import extract_imports as rust_imports
from agentpack.analysis.java_imports import extract_imports as java_imports
from agentpack.analysis.ruby_imports import extract_imports as ruby_imports
from agentpack.analysis.ruby_imports import resolve_relative_import as ruby_resolve
from agentpack.analysis.php_imports import extract_imports as php_imports
from agentpack.analysis.protobuf_imports import extract_imports as protobuf_imports

_GRAPH_CACHE: dict[tuple[tuple[tuple[str, str | None], ...], bool], DependencyGraph] = {}


def build(
    files: list[FileInfo],
    root: Path,
    summaries: dict | None = None,
) -> DependencyGraph:
    """Build an import/imported-by graph over packable files.

    Args:
        files: Packable (non-ignored, non-binary) FileInfo objects.
        root: Repository root for resolving relative imports.
        summaries: Optional pre-built summary cache; cached imports avoid re-parsing.

    Returns:
        DependencyGraph with typed DependencyNode entries. Caller fills tests
        via find_related_tests after construction.
    """
    cache_key = (
        tuple(sorted((fi.path, fi.hash) for fi in files)),
        bool(summaries),
    )
    cached_graph = _GRAPH_CACHE.get(cache_key)
    if cached_graph is not None:
        return cached_graph.model_copy(deep=True)

    graph = DependencyGraph(
        nodes={fi.path: DependencyNode(path=fi.path) for fi in files}
    )
    path_set = {fi.path for fi in files}

    for fi in files:
        if summaries and fi.path in summaries:
            cached_imports = summaries[fi.path].get("imports", [])
            if cached_imports:
                resolved_cached = _resolve_imports(fi.path, fi.language, cached_imports, root, path_set)
                graph.nodes[fi.path].imports = resolved_cached
                for dep in resolved_cached:
                    if dep in graph:
                        graph.nodes[dep].imported_by.append(fi.path)
                continue

        raw_imports: list[str] = []
        lang = fi.language
        cached = fi.content

        if lang == "python":
            raw_imports = py_imports(fi.abs_path, cached)
        elif lang in ("javascript", "typescript"):
            raw_imports = js_imports(fi.abs_path, cached)
        elif lang == "go":
            raw_imports = go_imports(fi.abs_path, cached)
        elif lang == "rust":
            raw_imports = rust_imports(fi.abs_path, cached)
        elif lang in ("java", "kotlin"):
            raw_imports = java_imports(fi.abs_path, cached)
        elif lang == "ruby":
            raw_imports = ruby_imports(fi.abs_path, cached)
        elif lang == "php":
            raw_imports = php_imports(fi.abs_path, cached)
        elif lang == "protobuf":
            raw_imports = protobuf_imports(fi.abs_path, cached)

        resolved = _resolve_imports(fi.path, lang, raw_imports, root, path_set)

        graph.nodes[fi.path].imports = resolved
        for dep in resolved:
            if dep in graph:
                graph.nodes[dep].imported_by.append(fi.path)

    _GRAPH_CACHE[cache_key] = graph.model_copy(deep=True)
    return graph


def _resolve_imports(
    importer: str,
    language: str | None,
    imports: list[str],
    root: Path,
    path_set: set[str],
) -> list[str]:
    resolved: list[str] = []
    for imp in imports:
        if imp.startswith("."):
            if language == "python":
                r = py_resolve(importer, imp, root)
            elif language in ("javascript", "typescript"):
                r = js_resolve(importer, imp, root)
            elif language == "ruby":
                r = ruby_resolve(importer, imp, root)
            else:
                r = None
            if r and r in path_set:
                resolved.append(r)
        else:
            # Bare `require "foo/bar"` (no dot prefix) resolves against
            # Ruby's $LOAD_PATH (typically a gem's lib/ root), not relative
            # to the importing file's directory. ruby_resolve only
            # implements importer-relative resolution (require_relative
            # semantics), so it isn't applied here — a coincidental file at
            # the guessed importer-relative path would otherwise silently
            # resolve to the wrong target. Kept as a raw string, matching
            # how PHP's non-relative `use` imports are handled.
            resolved.append(imp)
    return resolved
