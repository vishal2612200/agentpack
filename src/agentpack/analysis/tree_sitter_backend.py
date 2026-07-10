"""Optional tree-sitter backend for symbol and import extraction.

Activated when `tree-sitter` and `tree-sitter-language-pack` are installed
(via the `[tree-sitter]` extra). Callers should route through the guards in
`symbols.extract_symbols` and `dependency_graph.build` — this module never
imports itself as required.

Design:
- Grammars, parsers, and compiled queries are cached lazily per language.
- Query files live in `queries/<lang>.scm` next to this module.
- Extraction reuses the existing `Symbol` model, so downstream code
  (ranker, repo_map, explain_file, MCP tools) needs no changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import cast

from agentpack.core.models import Symbol

_QUERIES_DIR = Path(__file__).parent / "queries"

if TYPE_CHECKING:
    from tree_sitter import Language
    from tree_sitter import Node
    from tree_sitter import Parser
    from tree_sitter import Query

    QueryMatches = list[tuple[int, dict[str, list[Node]]]]
else:
    QueryMatches = list[tuple[int, dict[str, list[object]]]]

# Languages this backend can extract symbols for.
TS_SYMBOL_LANGS: set[str] = {"java", "ruby", "php", "terraform", "dockerfile", "protobuf", "graphql"}
# Languages this backend can extract imports for.
TS_IMPORT_LANGS: set[str] = {"ruby", "php", "protobuf"}

# Map AgentPack's language string to tree-sitter-language-pack's grammar name.
_TreeSitterGrammarName = Literal["java", "ruby", "php", "terraform", "dockerfile", "proto", "graphql"]
_SymbolKind = Literal["class", "function", "method", "variable"]
_TS_LANG_NAME: dict[str, _TreeSitterGrammarName] = {
    "java": "java",
    "ruby": "ruby",
    "php": "php",
    "terraform": "terraform",
    "dockerfile": "dockerfile",
    "protobuf": "proto",  # the language pack calls this grammar "proto", not "protobuf"
    "graphql": "graphql",
}


_available: bool | None = None
_language_cache: dict[str, Language] = {}
_parser_cache: dict[str, Parser] = {}
_query_cache: dict[str, Query] = {}


def is_available() -> bool:
    """Return True iff tree-sitter and language-pack are importable.

    Honors the `AGENTPACK_DISABLE_TREE_SITTER` env var for A/B testing —
    set to "1" to force the fallback path even when the extra is installed.
    """
    import os
    if os.environ.get("AGENTPACK_DISABLE_TREE_SITTER") == "1":
        return False
    global _available
    if _available is not None:
        return _available
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_language_pack  # noqa: F401
        _available = True
    except ImportError:
        _available = False
    return _available


def _ts_language_name(language: str) -> _TreeSitterGrammarName:
    return _TS_LANG_NAME.get(language, cast(_TreeSitterGrammarName, language))


def _get_parser(language: str) -> Parser:
    ts_name = _ts_language_name(language)
    if ts_name in _parser_cache:
        return _parser_cache[ts_name]
    import tree_sitter_language_pack as pack
    parser = pack.get_parser(ts_name)
    _parser_cache[ts_name] = parser
    return parser


def _get_language(language: str) -> Language:
    ts_name = _ts_language_name(language)
    if ts_name in _language_cache:
        return _language_cache[ts_name]
    import tree_sitter_language_pack as pack
    lang = pack.get_language(ts_name)
    _language_cache[ts_name] = lang
    return lang


def _get_query(language: str) -> Query:
    if language in _query_cache:
        return _query_cache[language]
    from tree_sitter import Query
    query_path = _QUERIES_DIR / f"{language}.scm"
    if not query_path.exists():
        raise FileNotFoundError(f"tree-sitter query not found: {query_path}")
    lang_obj = _get_language(language)
    query = Query(lang_obj, query_path.read_text())
    _query_cache[language] = query
    return query


def _node_text(node: Node) -> str:
    raw = node.text or b""
    return raw.decode("utf-8", errors="replace")


def _query_matches(query: Query, root_node: Node) -> QueryMatches:
    # ponytail: keep the optional floor broad by supporting both query APIs.
    if hasattr(query, "matches"):
        return cast(QueryMatches, cast(Any, query).matches(root_node))
    from tree_sitter import QueryCursor
    return cast(QueryMatches, QueryCursor(query).matches(root_node))


def _enclosing_scope_chain(node: Node, class_node_ids: set[int]) -> list[Node]:
    """Walk up from `node`, collecting captured class/module ancestors.

    Returns outermost-first (e.g. for `def greet` inside `class User` inside
    `module MyApp`, returns `[MyApp_node, User_node]`). Used to build
    Ruby/Java/PHP-style nested scope qualification (`MyApp::User.greet`
    rather than just `User.greet`), so methods with the same name in
    different modules don't collide in the ranker's keyword index.
    """
    chain = []
    parent = node.parent
    while parent is not None:
        if parent.id in class_node_ids:
            chain.append(parent)
        parent = parent.parent
    chain.reverse()
    return chain


def _qualify(own_name: str, node: Node, class_node_ids: set[int], class_name_by_id: dict[int, str]) -> str:
    """Prefix `own_name` with its full enclosing scope chain, `::`-joined."""
    chain = _enclosing_scope_chain(node, class_node_ids)
    if not chain:
        return own_name
    prefix = "::".join(class_name_by_id[n.id] for n in chain)
    return f"{prefix}.{own_name}"


def _node_signature(node: Node, src: bytes, max_len: int = 120) -> str:
    """Return a single-line snippet of the node's leading source text."""
    text = _node_text(node)
    first_line = text.split("\n", 1)[0].strip()
    return first_line[:max_len]


def _decode_name(name_node: Node) -> str:
    return _node_text(name_node)


def extract_symbols_ts(path: Path, language: str) -> list[Symbol]:
    """Parse `path` with tree-sitter and return AgentPack `Symbol` records.

    Returns an empty list on read/parse error; callers fall back to the
    existing regex/AST extractors.
    """
    if language not in TS_SYMBOL_LANGS:
        return []
    try:
        src_bytes = path.read_bytes()
    except OSError:
        return []

    try:
        parser = _get_parser(language)
        tree = parser.parse(src_bytes)
        query = _get_query(language)
        matches = _query_matches(query, tree.root_node)
    except Exception:
        return []

    # First pass: collect all class/module-like nodes so method/function
    # captures can find their enclosing scope for `Owner.method` qualification.
    class_matches: list[tuple[Node, str]] = []  # (outer_node, name)
    method_matches: list[tuple[Node, str]] = []
    function_matches: list[tuple[Node, str]] = []
    variable_matches: list[tuple[Node, str]] = []
    for _pat_idx, cap in matches:
        # cap is dict[str, list[Node]] — one entry per capture name in the pattern.
        for outer_key, name_key, bucket in (
            ("class", "class.name", class_matches),
            ("method", "method.name", method_matches),
            ("function", "function.name", function_matches),
            ("variable", "variable.name", variable_matches),
        ):
            if outer_key in cap and name_key in cap:
                outer_node = cap[outer_key][0]
                name_node = cap[name_key][0]
                name = _decode_name(name_node)
                if outer_key == "class" and "class.label" in cap:
                    # Some DSLs identify a block by type + one or more string
                    # labels rather than a single name node (e.g. Terraform's
                    # `resource "aws_instance" "web" {...}` — the identifier
                    # captures the block type "resource", and class.label
                    # captures each string label). Join them into one name:
                    # resource.aws_instance.web.
                    labels = [_decode_name(n) for n in cap["class.label"]]
                    name = ".".join([name, *labels])
                bucket.append((outer_node, name))

    class_node_ids = {n.id for n, _ in class_matches}
    class_name_by_id = {n.id: name for n, name in class_matches}

    symbols: list[Symbol] = []

    for node, name in class_matches:
        # Nested classes/modules get their full scope path too, so
        # `class User` inside `module MyApp` becomes `MyApp::User` — keeps
        # same-named classes in different modules distinct in the ranker's
        # keyword index.
        chain = _enclosing_scope_chain(node, class_node_ids)
        qualified_name = "::".join([*(class_name_by_id[n.id] for n in chain), name])
        symbols.append(
            Symbol(
                name=qualified_name,
                kind="class",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=_node_signature(node, src_bytes),
                body=_node_text(node),
            )
        )

    for node, name in variable_matches:
        # Flat, top-level declarations (e.g. Dockerfile `ARG NAME=...`) — no
        # enclosing-scope qualification, these DSLs don't nest variables
        # inside a class-like construct.
        symbols.append(
            Symbol(
                name=name,
                kind="variable",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=_node_signature(node, src_bytes),
                body=_node_text(node),
            )
        )

    for node, name in method_matches:
        qualified = _qualify(name, node, class_node_ids, class_name_by_id)
        symbols.append(
            Symbol(
                name=qualified,
                kind="method",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=_node_signature(node, src_bytes),
                body=_node_text(node),
            )
        )

    for node, name in function_matches:
        # In Ruby, `def foo` inside a class is a method, not a function.
        chain = _enclosing_scope_chain(node, class_node_ids)
        if chain:
            qualified = _qualify(name, node, class_node_ids, class_name_by_id)
            kind: _SymbolKind = "method"
        else:
            qualified = name
            kind = "function"
        symbols.append(
            Symbol(
                name=qualified,
                kind=kind,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=_node_signature(node, src_bytes),
                body=_node_text(node),
            )
        )

    return symbols


def extract_imports_ts(path: Path, cached_text: str | None, language: str) -> list[str]:
    """Extract raw import strings for `path` using the language's TS query."""
    if language not in TS_IMPORT_LANGS:
        return []
    try:
        src_bytes = (
            cached_text.encode("utf-8", errors="replace")
            if cached_text is not None
            else path.read_bytes()
        )
    except OSError:
        return []

    try:
        parser = _get_parser(language)
        tree = parser.parse(src_bytes)
        query = _get_query(language)
        matches = _query_matches(query, tree.root_node)
    except Exception:
        return []

    imports: list[str] = []
    seen: set[str] = set()
    for _pat_idx, cap in matches:
        for node in cap.get("import.path", []):
            raw = _node_text(node).strip()
            # Ruby/PHP capture a content-only sub-node (no surrounding quotes).
            # Protobuf's grammar has no such sub-node, so @import.path is the
            # whole `string` node including its quote-mark tokens — strip them
            # here rather than special-casing the query. Safe no-op for the
            # other languages since their captured text is already quote-free.
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                raw = raw[1:-1]
            if raw and raw not in seen:
                imports.append(raw)
                seen.add(raw)
    return imports
