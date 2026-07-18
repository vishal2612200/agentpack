from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agentpack.core.models import ContextPack, FileInfo, OmittedRelevantFile, SelectedFile
from agentpack.core.node_identity import symbol_node_id
from agentpack.core.redactor import redact_secrets
from agentpack.core.scanner import file_hash
from agentpack.core.token_estimator import estimate_tokens


RegistryKind = Literal["selected", "omitted"]
RegistryKindFilter = Literal["selected", "omitted", "any"]


class PackRegistryRecord(BaseModel):
    block_id: str
    path: str
    kind: RegistryKind
    include_mode: str
    symbol: str | None = None
    node_id: str = ""
    file_hash: str | None = None
    content_hash: str
    tokens: int
    score: float = 0
    reasons: list[str] = Field(default_factory=list)
    summary: str | None = None
    stored_content: str | None = None


class PackRegistry(BaseModel):
    version: int = 1
    task: str
    generated_at: str
    snapshot_root_hash: str
    records: list[PackRegistryRecord] = Field(default_factory=list)


def registry_path(root: Path, configured_path: str = ".agentpack/pack-registry.json") -> Path:
    return root / configured_path


def save_pack_registry(
    root: Path,
    pack: ContextPack,
    packable: list[FileInfo],
    *,
    output_path: str = ".agentpack/pack-registry.json",
    max_records: int = 200,
) -> PackRegistry:
    registry = build_pack_registry(pack, packable, max_records=max_records)
    write_pack_registry(root, registry, output_path=output_path)
    return registry


def build_pack_registry(
    pack: ContextPack,
    packable: list[FileInfo],
    *,
    max_records: int = 200,
) -> PackRegistry:
    info_by_path = {fi.path: fi for fi in packable}
    records: list[PackRegistryRecord] = []
    for sf in pack.selected_files:
        info = info_by_path.get(sf.path)
        records.append(_selected_record(sf, info))
        for symbol_record in _symbol_records(sf, info):
            if len(records) >= max_records:
                break
            records.append(symbol_record)
    for item in pack.omitted_relevant_files:
        if len(records) >= max_records:
            break
        records.append(_omitted_record(item, info_by_path.get(item.path)))

    registry = PackRegistry(
        task=pack.task,
        generated_at=str(pack.freshness.get("generated_at") or datetime.now(timezone.utc).isoformat()),
        snapshot_root_hash=str(pack.freshness.get("snapshot_root_hash") or ""),
        records=records[:max_records],
    )
    return registry


def write_pack_registry(
    root: Path,
    registry: PackRegistry,
    *,
    output_path: str = ".agentpack/pack-registry.json",
) -> None:
    path = registry_path(root, output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(registry.model_dump_json(indent=2), encoding="utf-8")


def load_pack_registry(root: Path, path: Path | None = None) -> PackRegistry | None:
    registry_file = path or registry_path(root)
    if not registry_file.exists():
        return None
    try:
        return PackRegistry.model_validate(json.loads(registry_file.read_text(encoding="utf-8")))
    except Exception:
        return None


def retrieve_from_registry(
    root: Path,
    *,
    path: str = "",
    block_id: str = "",
    mode: str = "as_stored",
    allow_stale: bool = False,
    kind: RegistryKindFilter = "any",
    max_chars: int = 20000,
    registry_file: Path | None = None,
) -> str:
    registry = load_pack_registry(root, registry_file)
    if registry is None:
        return "No pack registry found. Run `agentpack pack` first."
    record = _find_record(registry, path=path, block_id=block_id, kind=kind)
    if record is None:
        target = block_id or path
        return f"No registry record found for `{target}`."

    if mode == "as_stored":
        content = record.stored_content or record.summary or ""
        if content:
            return _format_retrieval(record, content, stale=False, truncated=False)
        mode = "full"

    file_path = root / record.path
    if not file_path.exists():
        return f"Registered file no longer exists: `{record.path}`."
    try:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Failed to read `{record.path}`: {exc}"

    current_hash = file_hash(file_path)
    stale = bool(record.file_hash and current_hash != record.file_hash)
    if stale and not allow_stale:
        return (
            f"`{record.path}` changed since the latest pack registry. "
            "Run `agentpack pack` or pass allow_stale=true to retrieve current contents."
        )

    redacted, warnings = redact_secrets(raw, record.path)
    content = _mode_view(redacted, mode)
    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + "\n... retrieval truncated by AgentPack ..."
        truncated = True
    if warnings:
        content += "\n\n> Secrets redacted: " + ", ".join(warnings)
    return _format_retrieval(record, content, stale=stale, truncated=truncated)


def _selected_record(sf: SelectedFile, fi: FileInfo | None) -> PackRegistryRecord:
    content = sf.content or sf.summary or ""
    content_hash = _hash_text(content or sf.path)
    block_id = _block_id(sf.path, content_hash)
    return PackRegistryRecord(
        block_id=block_id,
        path=sf.path,
        kind="selected",
        include_mode=sf.include_mode,
        file_hash=fi.hash if fi else None,
        content_hash=content_hash,
        tokens=estimate_tokens(content) if content else 0,
        score=sf.score,
        reasons=sf.reasons,
        summary=sf.summary,
        stored_content=content if sf.include_mode in {"full", "diff", "symbols", "skeleton"} else None,
    )


def _symbol_records(sf: SelectedFile, fi: FileInfo | None) -> list[PackRegistryRecord]:
    records: list[PackRegistryRecord] = []
    for sym in sf.symbols:
        content = sym.body or sym.signature or sym.summary or sym.name
        content_hash = _hash_text(f"{sf.path}:{sym.name}:{content}")
        node_id = sym.node_id or symbol_node_id(sf.path, sym, source_hash=fi.hash if fi else sf.source_hash or "")
        records.append(PackRegistryRecord(
            block_id=_block_id(f"{sf.path}::{sym.name}", content_hash),
            path=sf.path,
            kind="selected",
            include_mode="symbol",
            symbol=sym.name,
            node_id=node_id,
            file_hash=fi.hash if fi else None,
            content_hash=content_hash,
            tokens=estimate_tokens(content),
            score=sf.score,
            reasons=sf.reasons,
            summary=sym.summary or sym.signature,
            stored_content=content,
        ))
    return records


def _omitted_record(item: OmittedRelevantFile, fi: FileInfo | None) -> PackRegistryRecord:
    summary = item.omission_reason
    source_hash = fi.hash if fi and fi.hash else str(item.score)
    content_hash = _hash_text(f"{item.path}:{source_hash}:{summary}")
    return PackRegistryRecord(
        block_id=_block_id(item.path, content_hash),
        path=item.path,
        kind="omitted",
        include_mode=item.suggested_mode,
        file_hash=fi.hash if fi else None,
        content_hash=content_hash,
        tokens=fi.estimated_tokens if fi else item.estimated_tokens,
        score=item.score,
        reasons=item.reasons,
        summary=summary,
    )


def _find_record(
    registry: PackRegistry,
    *,
    path: str,
    block_id: str,
    kind: RegistryKindFilter = "any",
) -> PackRegistryRecord | None:
    for record in registry.records:
        if kind != "any" and record.kind != kind:
            continue
        if block_id and record.block_id == block_id:
            return record
        if path and record.path == path:
            return record
    return None


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _block_id(path: str, content_hash: str) -> str:
    slug = path.replace("\\", "/").replace("/", "__")
    return f"{slug}:{content_hash[:12]}"


def _mode_view(content: str, mode: str) -> str:
    if mode in {"full", "as_stored"}:
        return content
    if mode == "skeleton":
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(("def ", "class ", "async def ", "function ", "export ", "import ", "from ")):
                lines.append(line)
        return "\n".join(lines) if lines else content[:4000]
    if mode == "symbols":
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(("def ", "class ", "async def ", "function ", "export function ", "export class ")):
                lines.append(line)
        return "\n".join(lines) if lines else content[:4000]
    if mode == "summary":
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return "\n".join(lines[:40])
    return content


def _format_retrieval(record: PackRegistryRecord, content: str, *, stale: bool, truncated: bool) -> str:
    status = []
    if stale:
        status.append("stale-current")
    if truncated:
        status.append("truncated")
    suffix = f" ({', '.join(status)})" if status else ""
    symbol_line = f"- symbol: {record.symbol}\n" if record.symbol else ""
    node_line = f"- node_id: `{record.node_id}`\n" if record.node_id else ""
    return (
        f"## {record.path}{suffix}\n\n"
        f"- block_id: `{record.block_id}`\n"
        f"- kind: {record.kind}\n"
        f"- mode: {record.include_mode}\n"
        f"{symbol_line}"
        f"{node_line}"
        f"- tokens: {record.tokens:,}\n\n"
        "```text\n"
        f"{content}\n"
        "```"
    )
