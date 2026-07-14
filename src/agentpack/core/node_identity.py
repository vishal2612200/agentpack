from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agentpack.core.models import Symbol


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_repo_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def symbol_signature_hash(symbol: Symbol | dict[str, Any]) -> str:
    signature = _field(symbol, "signature")
    body = _field(symbol, "body")
    summary = _field(symbol, "summary")
    name = _field(symbol, "name")
    return hash_text(signature or body or summary or name)[:16]


def symbol_node_key(path: str | Path, symbol: Symbol | dict[str, Any]) -> str:
    """Return a stable symbol identity that survives body-only revisions."""
    rel_path = normalize_repo_path(path)
    name = _field(symbol, "name")
    kind = _field(symbol, "kind")
    signature_hash = _field(symbol, "signature_hash") or symbol_signature_hash(symbol)
    base = "|".join([rel_path, name, kind, signature_hash])
    return "node:" + hash_text(base)[:20]


def symbol_node_revision(node_key: str, *, source_hash: str = "") -> str:
    """Return the append-only content revision for one stable node key."""
    return "node-revision:" + hash_text("|".join([node_key, source_hash]))[:20]


def symbol_node_id(path: str | Path, symbol: Symbol | dict[str, Any], *, source_hash: str = "") -> str:
    """Backward-compatible name for the stable node key API."""
    return symbol_node_key(path, symbol)


def symbol_node_ref(path: str | Path, symbol: Symbol | dict[str, Any], *, source_hash: str = "") -> dict[str, Any]:
    rel_path = normalize_repo_path(path)
    signature_hash = _field(symbol, "signature_hash") or symbol_signature_hash(symbol)
    file_hash = source_hash or _field(symbol, "source_hash")
    node_key = symbol_node_key(rel_path, symbol)
    return {
        "node_id": node_key,
        "node_key": node_key,
        "revision_id": symbol_node_revision(node_key, source_hash=file_hash),
        "path": rel_path,
        "symbol": _field(symbol, "name"),
        "kind": _field(symbol, "kind"),
        "start_line": _int_field(symbol, "start_line"),
        "end_line": _int_field(symbol, "end_line"),
        "signature_hash": signature_hash,
        "source_hash": file_hash,
    }


def _field(symbol: Symbol | dict[str, Any], field: str) -> str:
    if isinstance(symbol, dict):
        value = symbol.get(field)
    else:
        value = getattr(symbol, field, "")
    return str(value or "")


def _int_field(symbol: Symbol | dict[str, Any], field: str) -> int:
    if isinstance(symbol, dict):
        value = symbol.get(field)
    else:
        value = getattr(symbol, field, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
