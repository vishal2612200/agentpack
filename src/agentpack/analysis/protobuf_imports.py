from __future__ import annotations

from pathlib import Path


def extract_imports(path: Path, text: str | None = None) -> list[str]:
    """Extract Protobuf `import "x.proto"` targets via tree-sitter.

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
    return extract_imports_ts(path, text, "protobuf")
