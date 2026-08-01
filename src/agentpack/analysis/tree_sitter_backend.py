"""Tree-sitter extraction backend for symbols, relationships, and evidence.

The standard AgentPack installation provides `tree-sitter` and
`tree-sitter-language-pack`. The import guards remain so source checkouts can
still report an honest unavailable capability instead of failing during scans.

Design:
- Grammars, parsers, and compiled queries are cached lazily per language.
- Query files live in `queries/<lang>.scm` next to this module.
- Extraction reuses the existing `Symbol` model, so downstream code
  (ranker, repo_map, explain_file, MCP tools) needs no changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
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
TS_SYMBOL_LANGS: set[str] = {
    "python", "javascript", "typescript", "go", "rust",
    "java", "kotlin", "ruby", "php", "terraform", "dockerfile", "protobuf", "graphql",
}
# Languages this backend can extract imports for.
TS_IMPORT_LANGS: set[str] = {"kotlin", "ruby", "php", "protobuf"}

# Map AgentPack's language string to tree-sitter-language-pack's grammar name.
_TreeSitterGrammarName = Literal[
    "python", "javascript", "typescript", "go", "rust", "java", "kotlin", "ruby", "php",
    "terraform", "dockerfile", "proto", "graphql",
]
_SymbolKind = Literal["class", "function", "method", "variable"]
_TS_LANG_NAME: dict[str, _TreeSitterGrammarName] = {
    "java": "java",
    "kotlin": "kotlin",
    "ruby": "ruby",
    "php": "php",
    "terraform": "terraform",
    "dockerfile": "dockerfile",
    "protobuf": "proto",  # the language pack calls this grammar "proto", not "protobuf"
    "graphql": "graphql",
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "rust": "rust",
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


def supports_language(language: str) -> bool:
    """Return whether the installed optional parser can serve one language."""
    if not is_available() or language not in TS_SYMBOL_LANGS:
        return False
    try:
        _get_parser(language)
    except Exception:
        return False
    return True


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
        kind: _SymbolKind = "method"
        if language == "ruby" and not _enclosing_scope_chain(node, class_node_ids):
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

    for node, name in function_matches:
        # In Ruby, `def foo` inside a class is a method, not a function.
        chain = _enclosing_scope_chain(node, class_node_ids)
        if chain:
            qualified = _qualify(name, node, class_node_ids, class_name_by_id)
            kind = "method"
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


@dataclass(frozen=True)
class SemanticSymbolFact:
    name: str
    kind: str
    start_line: int
    end_line: int
    signature: str
    body: str
    node_id: int


@dataclass(frozen=True)
class SemanticRelationFact:
    relation: str
    source_symbol: str | None
    target_name: str
    start_line: int
    end_line: int
    note: str = ""
    confidence_tier: str = "structured"


@dataclass(frozen=True)
class SemanticLocalEntityFact:
    entity_type: str
    name: str
    start_line: int
    end_line: int
    source_symbol: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    confidence_tier: str = "best_effort"


@dataclass
class SemanticFacts:
    symbols: list[SemanticSymbolFact] = field(default_factory=list)
    relations: list[SemanticRelationFact] = field(default_factory=list)
    comments: list[tuple[str, int, int, str | None]] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    reexports: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    local_entities: list[SemanticLocalEntityFact] = field(default_factory=list)


_DEFINITION_TYPES = {
    "function_definition", "function_declaration", "method_definition", "method_declaration",
    "class_definition", "class_declaration", "interface_declaration", "struct_item", "enum_item",
    "trait_item", "impl_item", "module", "module_definition", "singleton_method",
}
_CALL_TYPES = {
    "call", "call_expression", "function_call", "method_invocation", "command_invocation",
}
_IMPORT_TYPES = {
    "import_statement", "import_from_statement", "import_declaration", "import_spec", "use_declaration", "using_directive",
    "package_clause", "preproc_include", "require_statement",
}
_INHERIT_TYPES = {"class_definition", "class_declaration", "interface_declaration", "struct_item", "impl_item"}
_IDENTIFIER_TYPES = {"identifier", "type_identifier", "field_identifier", "shorthand_field_identifier", "constant"}


def extract_semantic_facts(
    path: Path,
    language: str,
    cached_text: str | None = None,
    *,
    extract_references: bool = True,
    max_references_per_symbol: int = 24,
) -> SemanticFacts:
    """Extract deterministic semantic facts using the grammar's concrete tree.

    This intentionally returns raw relationship candidates. Resolution is a
    repository concern and belongs in the architecture service's second pass.
    Unknown grammar constructs are retained as file-level facts by callers.
    """
    if language not in TS_SYMBOL_LANGS or not is_available():
        return SemanticFacts()
    try:
        source = cached_text.encode("utf-8", errors="replace") if cached_text is not None else path.read_bytes()
        tree = _get_parser(language).parse(source)
    except (OSError, Exception):
        return SemanticFacts()

    facts = SemanticFacts()
    definition_nodes: dict[int, SemanticSymbolFact] = {}
    definition_tree_nodes: dict[int, Node] = {}
    definition_name_nodes: set[int] = set()
    import_nodes: set[int] = set()
    call_name_nodes: set[int] = set()

    def text(node: Node) -> str:
        return _node_text(node).strip()

    def line_range(node: Node) -> tuple[int, int]:
        return node.start_point[0] + 1, node.end_point[0] + 1

    def name_for(node: Node) -> str:
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return text(name_node)
        for child in node.named_children:
            if child.type in {"identifier", "type_identifier", "field_identifier", "constant"}:
                return text(child)
        return ""

    def go_receiver_name(node: Node) -> str:
        receiver = node.child_by_field_name("receiver")
        if receiver is None:
            return ""
        for child in descendants(receiver):
            if child.type == "type_identifier":
                return text(child).lstrip("*")
        return ""

    def containing_symbol(node: Node) -> str | None:
        parent = node.parent
        while parent is not None:
            candidate = definition_nodes.get(parent.id)
            if candidate is not None:
                return candidate.name
            parent = parent.parent
        return None

    def descendants(node: Node):
        yield node
        for child in node.named_children:
            yield from descendants(child)

    nodes = list(descendants(tree.root_node))

    for node in nodes:
        if node.type not in _DEFINITION_TYPES:
            continue
        name = name_for(node)
        if not name:
            continue
        if language == "go" and node.type == "method_declaration":
            receiver_name = go_receiver_name(node)
            if receiver_name:
                name = f"{receiver_name}.{name}"
        parent = node.parent
        scope_parts: list[str] = []
        while parent is not None:
            enclosing = definition_nodes.get(parent.id)
            if enclosing is not None:
                scope_parts.append(enclosing.name)
            parent = parent.parent
        qualified = ".".join([*reversed(scope_parts), name])
        start, end = line_range(node)
        kind = "class" if "class" in node.type or "interface" in node.type or node.type in {"struct_item", "trait_item", "impl_item"} else "function"
        if language == "go" and node.type == "method_declaration":
            kind = "method"
        if scope_parts and kind == "function":
            kind = "method"
        if name.lower().startswith(("test", "it", "describe")):
            kind = "test"
        fact = SemanticSymbolFact(qualified, kind, start, end, text(node).split("\n", 1)[0][:160], text(node), node.id)
        definition_nodes[node.id] = fact
        definition_tree_nodes[node.id] = node
        definition_name = node.child_by_field_name("name")
        if definition_name is not None:
            definition_name_nodes.add(definition_name.id)
        facts.symbols.append(fact)

    for node in nodes:
        start, end = line_range(node)
        owner = containing_symbol(node)
        if node.type == "comment":
            facts.comments.append((text(node), start, end, owner))
            continue
        if node.type in _IMPORT_TYPES or node.type in {"import", "use_clause", "import_clause"}:
            raw = text(node)
            target = _import_target(raw, language)
            if target:
                facts.relations.append(SemanticRelationFact("imports", owner, target, start, end, raw))
            import_nodes.add(node.id)
            continue
        if node.type in _CALL_TYPES:
            target_node = node.child_by_field_name("function") or node.child_by_field_name("method")
            target = text(target_node) if target_node is not None else _call_target(text(node))
            if target:
                facts.relations.append(SemanticRelationFact("calls", owner, target, start, end, text(node).split("(", 1)[0]))
                if target_node is not None:
                    call_name_nodes.add(target_node.id)
            continue
        if node.type in _INHERIT_TYPES:
            for field_name, relation in (("superclass", "inherits"), ("interfaces", "implements"), ("type", "implements"), ("trait", "implements")):
                field_node = node.child_by_field_name(field_name)
                if field_node is not None and text(field_node):
                    facts.relations.append(SemanticRelationFact(relation, name_for(node) or owner, text(field_node), start, end, field_name))
            if node.type == "impl_item":
                raw = text(node)
                match = re.search(r"^impl(?:<[^>]+>)?\s+([^\s{]+)\s+for\s+([^\s{]+)", raw)
                if match:
                    facts.relations.append(SemanticRelationFact("implements", owner, match.group(1), start, end, "rust impl trait"))

    # Generic identifier references are deliberately conservative: capture
    # names inside a definition body, but exclude declarations, imports, and
    # call heads. The resolver decides whether a name is local, ambiguous, or
    # external and preserves that outcome in the graph.
    seen_refs: set[tuple[str, str]] = set()
    reference_counts: dict[str, int] = {}
    for node in nodes if extract_references else []:
        if node.type not in _IDENTIFIER_TYPES or node.id in definition_name_nodes or node.id in call_name_nodes:
            continue
        if any(ancestor.id in import_nodes for ancestor in _ancestors(node)):
            continue
        owner = containing_symbol(node)
        if not owner:
            continue
        target = text(node)
        if not target or target in {"self", "this", "true", "false", "None", "null"}:
            continue
        key = (owner, target)
        if key in seen_refs:
            continue
        if reference_counts.get(owner, 0) >= max_references_per_symbol:
            continue
        seen_refs.add(key)
        reference_counts[owner] = reference_counts.get(owner, 0) + 1
        start, end = line_range(node)
        facts.relations.append(SemanticRelationFact("references", owner, target, start, end, "identifier reference", "best_effort"))

    source_text = source.decode("utf-8", errors="replace")
    _supplement_language_facts(facts, source_text, language)
    facts.exports, facts.reexports = _export_metadata(source_text, language)
    facts.aliases = _alias_metadata(source_text, language)

    # A string literal immediately inside a definition is a docstring in
    # Python and a common documentation form in several DSL grammars.
    for symbol in facts.symbols:
        node = definition_tree_nodes.get(symbol.node_id)
        if node is None or not node.named_children:
            continue
        first = node.named_children[0]
        if first.type in {"string", "string_literal", "interpreted_string_literal"}:
            value = text(first)
            if value:
                start, end = line_range(first)
                facts.comments.append((value, start, end, symbol.name))
    return facts


def _supplement_language_facts(facts: SemanticFacts, source: str, language: str) -> None:
    """Cover stable declaration forms missing from older grammar node names."""
    patterns: list[tuple[str, str]] = []
    if language == "rust":
        patterns = [(r"\b(?:pub\s+)?fn\s+([A-Za-z_]\w*)\s*\(", "function")]
    elif language == "kotlin":
        patterns = [(r"\b(?:public\s+|private\s+|protected\s+|internal\s+|override\s+)*fun\s+([A-Za-z_]\w*)\s*\(", "function")]
    elif language == "ruby":
        patterns = [
            (r"\bmodule\s+([A-Za-z_:][\w:]*)", "class"),
            (r"\bclass\s+([A-Za-z_:][\w:]*)", "class"),
            (r"\bdef\s+([A-Za-z_]\w*[!?=]?)", "function"),
        ]
    for pattern, kind in patterns:
        for match in re.finditer(pattern, source):
            name = match.group(1)
            if any(symbol.name == name or symbol.name.endswith("." + name) for symbol in facts.symbols):
                continue
            line = source.count("\n", 0, match.start()) + 1
            facts.symbols.append(
                SemanticSymbolFact(
                    name=name,
                    kind=kind,
                    start_line=line,
                    end_line=line,
                    signature=match.group(0).strip(),
                    body=match.group(0).strip(),
                    node_id=-match.start() - 1,
                )
            )
    if language in {"java", "kotlin"}:
        for match in re.finditer(r"\bclass\s+([A-Za-z_]\w*)\s*:\s*([A-Za-z_][\w.]*)", source):
            line = source.count("\n", 0, match.start()) + 1
            candidate = SemanticRelationFact("implements", match.group(1), match.group(2), line, line, "kotlin interface implementation")
            if not any(
                item.relation == candidate.relation
                and item.source_symbol == candidate.source_symbol
                and item.target_name == candidate.target_name
                for item in facts.relations
            ):
                facts.relations.append(candidate)
    if language == "php":
        for match in re.finditer(
            r"\bclass\s+([A-Za-z_]\w*)\s*(?:extends\s+([A-Za-z_]\w*))?\s*(?:implements\s+([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*))?",
            source,
        ):
            source_symbol = match.group(1)
            line = source.count("\n", 0, match.start()) + 1
            candidates: list[tuple[str, str]] = []
            if match.group(2):
                candidates.append(("inherits", match.group(2)))
            if match.group(3):
                candidates.extend(("implements", value.strip()) for value in match.group(3).split(","))
            for relation, target in candidates:
                candidate = SemanticRelationFact(relation, source_symbol, target, line, line, "php declaration relationship")
                if not any(
                    item.relation == candidate.relation
                    and item.source_symbol == candidate.source_symbol
                    and item.target_name == candidate.target_name
                    for item in facts.relations
                ):
                    facts.relations.append(candidate)
    if language == "rust":
        # Some Rust grammars expose a one-line function body without attaching
        # the call node to its function declaration. Re-anchor those calls to
        # the lexical function while preserving the raw qualified target.
        facts.relations[:] = [
            relation
            for relation in facts.relations
            if not (relation.relation == "calls" and relation.source_symbol is None and "::" in relation.target_name)
        ]
        for match in re.finditer(r"\bfn\s+([A-Za-z_]\w*)[^{}]*\{([^{}]*)\}", source, re.DOTALL):
            source_symbol = match.group(1)
            body_start = match.start(2)
            for call in re.finditer(r"([A-Za-z_]\w*::[A-Za-z_]\w*)\s*\(", match.group(2)):
                line = source.count("\n", 0, body_start + call.start()) + 1
                candidate = SemanticRelationFact(
                    "calls",
                    source_symbol,
                    call.group(1),
                    line,
                    line,
                    "rust qualified call",
                )
                if not any(
                    item.relation == candidate.relation
                    and item.source_symbol == candidate.source_symbol
                    and item.target_name == candidate.target_name
                    for item in facts.relations
                ):
                    facts.relations.append(candidate)


def _export_metadata(source: str, language: str) -> tuple[list[str], list[str]]:
    """Collect export metadata without inventing export edges."""
    exports: set[str] = set()
    reexports: set[str] = set()
    if language in {"javascript", "typescript"}:
        exports.update(re.findall(r"\bexport\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)", source))
        for match in re.finditer(r"\bexport\s*\{([^}]+)\}(?:\s*from\s*[\"']([^\"']+))?", source):
            exports.update(part.strip().split(" as ")[-1] for part in match.group(1).split(",") if part.strip())
            if match.group(2):
                reexports.add(match.group(2))
    elif language == "python":
        for match in re.finditer(r"__all__\s*=\s*\[([^]]*)\]", source, re.DOTALL):
            exports.update(re.findall(r"[\"']([A-Za-z_]\w*)[\"']", match.group(1)))
    elif language == "go":
        exports.update(name for name in re.findall(r"\b(?:func|type|var|const)\s+([A-Za-z_]\w*)", source) if name[:1].isupper())
    elif language == "rust":
        exports.update(re.findall(r"\bpub\s+(?:async\s+)?(?:fn|struct|enum|trait|mod|const|static)\s+([A-Za-z_]\w*)", source))
    elif language in {"java", "kotlin"}:
        exports.update(re.findall(r"\bpublic\s+(?:static\s+)?(?:class|interface|enum|fun|void|[A-Za-z_]\w*\s+)?([A-Za-z_]\w*)\s*[(<{]", source))
    elif language == "ruby":
        exports.update(re.findall(r"\b(?:class|module|def)\s+([A-Za-z_:][\w:]*)", source))
    elif language == "php":
        exports.update(re.findall(r"\b(?:class|interface|trait|function)\s+([A-Za-z_]\w*)", source))
    return sorted(exports), sorted(reexports)


def _alias_metadata(source: str, language: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if language == "python":
        for match in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+([\w]+)(?:\s+as\s+([\w]+))?", source, re.MULTILINE):
            aliases[match.group(3) or match.group(2)] = f"{match.group(1)}.{match.group(2)}"
        for match in re.finditer(r"^\s*import\s+([\w.]+)(?:\s+as\s+([\w]+))?", source, re.MULTILINE):
            aliases[match.group(2) or match.group(1).split(".")[-1]] = match.group(1)
    elif language in {"javascript", "typescript"}:
        for match in re.finditer(r"\bimport\s+(?:type\s+)?([A-Za-z_$][\w$]*)\s+from\s+[\"']([^\"']+)", source):
            aliases[match.group(1)] = match.group(1)
        for match in re.finditer(r"\bimport\s*\{([^}]+)\}\s*from", source):
            for item in match.group(1).split(","):
                parts = re.split(r"\s+as\s+", item.strip())
                if parts and parts[0]:
                    aliases[parts[-1].strip()] = parts[0].strip()
    elif language == "go":
        for match in re.finditer(r"^\s*(?:(\w+)\s+)?[\"']([^\"']+)[\"']", source, re.MULTILINE):
            aliases[match.group(1) or Path(match.group(2)).stem] = Path(match.group(2)).stem
    elif language == "rust":
        for match in re.finditer(r"\buse\s+([^;]+?)\s+as\s+([A-Za-z_][\w]*)", source):
            aliases[match.group(2)] = match.group(1).split("::")[-1]
    elif language in {"java", "kotlin"}:
        for match in re.finditer(r"^\s*import\s+([\w.]+)", source, re.MULTILINE):
            aliases[match.group(1).split(".")[-1]] = match.group(1)
    elif language == "php":
        for match in re.finditer(r"\buse\s+([^;]+?)(?:\s+as\s+([A-Za-z_][\w]*))?\s*;", source):
            qualified = match.group(1).strip().replace("\\", ".")
            aliases[match.group(2) or qualified.rsplit(".", 1)[-1]] = qualified
    elif language == "ruby":
        for match in re.finditer(r"\brequire\s+[\"']([^\"']+)[\"']", source):
            aliases[Path(match.group(1)).stem] = Path(match.group(1)).stem
    return aliases


def _ancestors(node: Node):
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def _import_target(raw: str, language: str) -> str:
    value = raw.strip().rstrip(";")
    if language in {"javascript", "typescript"}:
        match = re.search(r"(?:from|require\s*\()\s*[\"']([^\"']+)", value)
        return match.group(1) if match else ""
    if language == "python":
        match = re.match(r"from\s+([\w.]+)\s+import", value)
        if match:
            return match.group(1)
        match = re.match(r"import\s+([\w.]+)", value)
        return match.group(1) if match else ""
    if language == "go":
        match = re.search(r"[\"']([^\"']+)[\"']", value)
        return match.group(1) if match else ""
    if language == "rust":
        match = re.match(r"use\s+([^;]+)", value)
        return match.group(1).strip() if match else ""
    match = re.search(r"[\"']([^\"']+)[\"']", value)
    return match.group(1) if match else value.split()[1] if len(value.split()) > 1 else ""


def _call_target(raw: str) -> str:
    match = re.search(r"(?:call|invoke)?\s*([A-Za-z_$][\w$.:]*)\s*\(", raw)
    return match.group(1) if match else ""
