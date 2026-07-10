from __future__ import annotations

import os
from pathlib import Path


def extract_imports(path: Path, text: str | None = None) -> list[str]:
    """Extract `require` / `require_relative` targets via tree-sitter.

    Returns [] when the tree-sitter backend is unavailable.
    """
    try:
        from agentpack.analysis.tree_sitter_backend import (
            extract_imports_ts,
            is_available,
        )
    except ImportError:
        return []
    if not is_available():
        return []
    return extract_imports_ts(path, text, "ruby")


def resolve_relative_import(importer: str, import_str: str, root: Path) -> str | None:
    """Resolve a Ruby `require_relative` argument to a repo-relative path.

    Ruby's require_relative resolves relative to the file's directory and
    implicitly appends `.rb`. Returns the resolved path relative to `root`,
    or None if the target file does not exist. Only relative-looking imports
    (starting with '.' or containing a path separator) are attempted;
    plain gem names like "json" return None.
    """
    if not import_str.startswith(".") and "/" not in import_str:
        return None

    base = Path(importer).parent
    candidate = os.path.normpath(str(base / import_str))
    # Ruby require_relative auto-appends .rb; also try as-given.
    for suffix in (".rb", ""):
        p = candidate + suffix
        if (root / p).is_file():
            return p.replace(os.sep, "/")
    return None
