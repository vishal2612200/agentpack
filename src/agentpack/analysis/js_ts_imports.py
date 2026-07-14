from __future__ import annotations

import re
from pathlib import Path

_IMPORT_PATTERNS = [
    re.compile(r'import\s+.*?\s+from\s+["\']([^"\']+)["\']'),
    re.compile(r'import\s+["\']([^"\']+)["\']'),
    re.compile(r'require\s*\(\s*["\']([^"\']+)["\']\s*\)'),
    re.compile(r'export\s+.*?\s+from\s+["\']([^"\']+)["\']'),
]

_RELATIVE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def extract_imports(path: Path, text: str | None = None) -> list[str]:
    if text is None:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return []

    imports: list[str] = []
    for pattern in _IMPORT_PATTERNS:
        for m in pattern.finditer(text):
            imports.append(m.group(1))
    return imports


def resolve_relative_import(importer: str, import_str: str, root: Path) -> str | None:
    if not import_str.startswith("."):
        return None

    resolved_root = root.resolve()
    base = (resolved_root / importer).parent
    candidate = (base / import_str).resolve()

    for ext in _RELATIVE_EXTS:
        p = candidate.with_suffix(ext)
        if p.exists():
            try:
                return str(p.relative_to(resolved_root))
            except ValueError:
                pass

    for ext in _RELATIVE_EXTS:
        p = candidate / f"index{ext}"
        if p.exists():
            try:
                return str(p.relative_to(resolved_root))
            except ValueError:
                pass

    return None
