from __future__ import annotations

import ast
import re
from pathlib import Path

from agentpack.analysis.python_ast import parse_python_source
from agentpack.core.models import Symbol


def extract_python_symbols(path: Path) -> list[Symbol]:
    try:
        source = path.read_text(errors="replace")
        tree = parse_python_source(source, path)
    except (SyntaxError, OSError):
        return []

    symbols: list[Symbol] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            sig = f"class {node.name}"
            if node.bases:
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                sig += f"({bases})"
            doc = ast.get_docstring(node)
            symbols.append(
                Symbol(
                    name=node.name,
                    kind="class",
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=sig,
                    summary=doc[:120] if doc else None,
                    body=ast.get_source_segment(source, node),
                )
            )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    msig = f"def {item.name}({_args_str(item.args)})"
                    mdoc = ast.get_docstring(item)
                    symbols.append(
                        Symbol(
                            name=f"{node.name}.{item.name}",
                            kind="method",
                            start_line=item.lineno,
                            end_line=item.end_lineno or item.lineno,
                            signature=msig,
                            summary=mdoc[:120] if mdoc else None,
                            body=ast.get_source_segment(source, item),
                        )
                    )

    # top-level functions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = f"def {node.name}({_args_str(node.args)})"
            doc = ast.get_docstring(node)
            symbols.append(
                Symbol(
                    name=node.name,
                    kind="function",
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=sig,
                    summary=doc[:120] if doc else None,
                    body=ast.get_source_segment(source, node),
                )
            )

    return symbols


def _args_str(args: ast.arguments) -> str:
    parts: list[str] = []
    for arg in args.args:
        parts.append(arg.arg)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for arg in args.kwonlyargs:
        parts.append(arg.arg)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(parts)



_JS_FUNC = re.compile(
    r"(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(",
)
# Require => on the same line to avoid matching non-arrow assignments like:
#   const result = (a + b)
_JS_ARROW = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>",
)
_JS_EXPORTED_VAR = re.compile(
    r"\bexport\s+(?:declare\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=;]+)?=",
)
_JS_CLASS = re.compile(r"(?:export\s+)?class\s+(\w+)")
_GO_FUNC = re.compile(r"^func\s+(?:\(([^)]+)\)\s*)?(\w+)\s*\(([^)]*)\)")
_GO_TYPE = re.compile(r"^type\s+(\w+)\s+(struct|interface)\b")
# Rust: optional `pub` / `pub(crate)` visibility, then modifiers, then the keyword.
_RUST_VIS = r"(?:pub(?:\s*\([^)]*\))?\s+)?"
_RUST_FN = re.compile(
    rf"^{_RUST_VIS}(?:default\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?"
    r"(?:extern\s+\"[^\"]*\"\s+)?fn\s+(\w+)"
)
_RUST_STRUCT = re.compile(rf"^{_RUST_VIS}struct\s+(\w+)")
_RUST_ENUM = re.compile(rf"^{_RUST_VIS}enum\s+(\w+)")
_RUST_TRAIT = re.compile(rf"^{_RUST_VIS}(?:unsafe\s+)?trait\s+(\w+)")
# `impl Type`, `impl<T> Type<T>`, or `impl Trait for Type` — capture the type
# that receives the methods (the name after `for`, else the name after `impl`).
_RUST_IMPL = re.compile(
    r"^impl(?:\s*<[^>]*>)?\s+(?:[\w:]+(?:\s*<[^>]*>)?\s+for\s+)?(\w+)"
)


def extract_js_symbols(path: Path) -> list[Symbol]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []

    symbols: list[Symbol] = []
    brace_depth = 0
    open_syms: list[tuple[str, str, int]] = []

    for i, line in enumerate(lines, 1):
        brace_depth += line.count("{") - line.count("}")
        for pattern, kind in [
            (_JS_CLASS, "class"),
            (_JS_FUNC, "function"),
            (_JS_ARROW, "function"),
            (_JS_EXPORTED_VAR, "variable"),
        ]:
            m = pattern.search(line)
            if m:
                open_syms.append((m.group(1), kind, i))

    # close any unclosed syms at end of file
    end_line = len(lines)
    for name, kind, start in open_syms:
        symbols.append(
            Symbol(
                name=name,
                kind=kind,  # type: ignore[arg-type]
                start_line=start,
                end_line=end_line,
                signature=lines[start - 1].strip()[:120],
                body="\n".join(lines[start - 1 : min(start + 49, end_line)]),
            )
        )
    return symbols


def extract_go_symbols(path: Path) -> list[Symbol]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []

    symbols: list[Symbol] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        func_match = _GO_FUNC.match(stripped)
        if func_match:
            receiver, name, _args = func_match.groups()
            symbol_name = f"{_go_receiver_name(receiver)}.{name}" if receiver else name
            symbols.append(
                Symbol(
                    name=symbol_name,
                    kind="method" if receiver else "function",
                    start_line=i,
                    end_line=_brace_block_end_line(lines, i),
                    signature=stripped[:120],
                    body="\n".join(lines[i - 1 : min(i + 49, len(lines))]),
                )
            )
            continue
        type_match = _GO_TYPE.match(stripped)
        if type_match:
            name, type_kind = type_match.groups()
            symbols.append(
                Symbol(
                    name=name,
                    kind="class",
                    start_line=i,
                    end_line=_brace_block_end_line(lines, i),
                    signature=stripped[:120],
                    summary=f"Go {type_kind}",
                    body="\n".join(lines[i - 1 : min(i + 49, len(lines))]),
                )
            )
    return symbols


def _go_receiver_name(receiver: str | None) -> str:
    if not receiver:
        return ""
    parts = receiver.replace("*", " ").split()
    return parts[-1] if parts else receiver.strip()


def _brace_block_end_line(lines: list[str], start_line: int) -> int:
    depth = 0
    saw_open = False
    for index in range(start_line - 1, len(lines)):
        line = lines[index]
        depth += line.count("{") - line.count("}")
        saw_open = saw_open or "{" in line
        if saw_open and depth <= 0:
            return index + 1
    return start_line


def _rust_symbol_end_line(lines: list[str], start_line: int, signature: str) -> int:
    # Declarations without a body (unit/tuple structs, trait method signatures)
    # end on their own line; only brace-delimited items span multiple lines.
    if "{" not in signature and signature.rstrip().endswith(";"):
        return start_line
    return _brace_block_end_line(lines, start_line)


def extract_rust_symbols(path: Path) -> list[Symbol]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []

    symbols: list[Symbol] = []
    depth = 0
    # Stack of (owner_type, brace_depth_at_open) for open impl/trait blocks so
    # nested `fn` items can be qualified as `Type.method`.
    containers: list[tuple[str, int]] = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        while containers and depth <= containers[-1][1]:
            containers.pop()
        owner = containers[-1][0] if containers else None

        fn_match = _RUST_FN.match(stripped)
        impl_match = _RUST_IMPL.match(stripped)
        if fn_match:
            name = fn_match.group(1)
            symbols.append(
                Symbol(
                    name=f"{owner}.{name}" if owner else name,
                    kind="method" if owner else "function",
                    start_line=i,
                    end_line=_rust_symbol_end_line(lines, i, stripped),
                    signature=stripped[:120],
                    body="\n".join(lines[i - 1 : min(i + 49, len(lines))]),
                )
            )
        elif impl_match:
            # `impl` blocks contribute methods but are not named symbols.
            containers.append((impl_match.group(1), depth))
        else:
            for pattern, type_kind in ((_RUST_STRUCT, "struct"), (_RUST_ENUM, "enum"), (_RUST_TRAIT, "trait")):
                type_match = pattern.match(stripped)
                if type_match:
                    name = type_match.group(1)
                    symbols.append(
                        Symbol(
                            name=name,
                            kind="class",
                            start_line=i,
                            end_line=_rust_symbol_end_line(lines, i, stripped),
                            signature=stripped[:120],
                            summary=f"Rust {type_kind}",
                            body="\n".join(lines[i - 1 : min(i + 49, len(lines))]),
                        )
                    )
                    if type_kind == "trait":
                        containers.append((name, depth))
                    break

        depth += line.count("{") - line.count("}")

    return symbols


def extract_symbols(path: Path, language: str | None) -> list[Symbol]:
    # Optional tree-sitter path: activates only when the `[tree-sitter]` extra
    # is installed and the language has no first-class extractor here. Failures
    # fall through so behavior with the extra installed can never be worse than
    # without it.
    if language is not None:
        try:
            from agentpack.analysis.tree_sitter_backend import (
                TS_SYMBOL_LANGS,
                extract_symbols_ts,
                is_available,
            )
            if language in TS_SYMBOL_LANGS and is_available():
                ts_syms = extract_symbols_ts(path, language)
                if ts_syms:
                    return ts_syms
        except Exception:
            pass

    if language == "python":
        return extract_python_symbols(path)
    if language in ("javascript", "typescript"):
        return extract_js_symbols(path)
    if language == "go":
        return extract_go_symbols(path)
    if language == "rust":
        return extract_rust_symbols(path)
    return []


def filter_symbols_by_keywords(symbols: list[Symbol], keywords: set[str]) -> list[Symbol]:
    """Return symbols whose name or summary matches any keyword."""
    if not keywords:
        return symbols
    result = []
    for s in symbols:
        name_lower = s.name.lower()
        summary_lower = (s.summary or "").lower()
        if any(kw in name_lower or kw in summary_lower for kw in keywords):
            result.append(s)
    return result
