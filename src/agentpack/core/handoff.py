"""Host-neutral handoff records, Git patches, claims, and portable bundles."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field, model_validator

from agentpack.core.config import load_config
from agentpack.core.context_pack import load_pack_metadata
from agentpack.core.project_index import agentpack_home
from agentpack.core.redactor import redact_secrets
from agentpack.core.task_freshness import task_hash
from agentpack.core.thread_context import (
    append_thread_index,
    build_thread_index_row,
    resolve_session_thread_option,
    task_state_path,
    thread_paths,
)
from agentpack.session.events import read_events, record_event
from agentpack.session.identity import logical_task_id, project_id, resolve_identity


HANDOFF_SCHEMA_VERSION = 1
EXPORT_SCHEMA_VERSION = 1
_SAFE_NAME = re.compile(r"[^a-z0-9-]+")
_HOST_ENV = (
    ("codex", "CODEX_THREAD_ID"),
    ("claude", "CLAUDE_SESSION_ID"),
    ("cursor", "CURSOR_SESSION_ID"),
    ("windsurf", "WINDSURF_SESSION_ID"),
    ("gemini", "GEMINI_SESSION_ID"),
    ("antigravity", "ANTIGRAVITY_SESSION_ID"),
    ("cline", "CLINE_SESSION_ID"),
    ("copilot", "COPILOT_SESSION_ID"),
    ("opencode", "OPENCODE_SESSION_ID"),
)


class HandoffError(RuntimeError):
    pass


class ValidationEvidence(BaseModel):
    command: str
    outcome: Literal["passed", "failed", "not_run"]
    tested_sha: str
    timestamp: datetime
    reason: str = ""

    @model_validator(mode="after")
    def validate_not_run(self) -> "ValidationEvidence":
        if not self.command.strip() or not self.tested_sha.strip():
            raise ValueError("validation evidence requires command and tested_sha")
        if self.outcome == "not_run" and not self.reason.strip():
            raise ValueError("validation evidence with outcome 'not_run' requires a reason")
        return self


class Decision(BaseModel):
    decision: str
    rationale: str

    @model_validator(mode="after")
    def validate_text(self) -> "Decision":
        if not self.decision.strip() or not self.rationale.strip():
            raise ValueError("decisions require both decision and rationale")
        return self


class Blocker(BaseModel):
    blocker: str
    required_action: str

    @model_validator(mode="after")
    def validate_text(self) -> "Blocker":
        if not self.blocker.strip() or not self.required_action.strip():
            raise ValueError("blockers require both blocker and required_action")
        return self


class HandoffReport(BaseModel):
    task: str
    acceptance_criteria: list[str]
    summary: str
    next_action: str
    completed: list[str] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    validation: list[ValidationEvidence]
    changed_files: list[str] = Field(default_factory=list)
    dirty_files: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_required_text(self) -> "HandoffReport":
        for field in ("task", "summary", "next_action"):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"{field} is required")
        if not any(item.strip() for item in self.acceptance_criteria):
            raise ValueError("at least one acceptance criterion is required")
        if not self.validation:
            raise ValueError("at least one validation evidence item is required")
        return self


class HostSession(BaseModel):
    provider: str
    session_id: str
    thread_id: str = ""


class RepositorySnapshot(BaseModel):
    repository_fingerprint: str
    project_id: str
    branch: str
    head_sha: str
    worktree: str
    task_hash: str
    context_snapshot_hash: str
    file_fingerprints: dict[str, str] = Field(default_factory=dict)


class PatchManifest(BaseModel):
    sha256: str
    base_sha: str
    compressed_size: int
    uncompressed_size: int
    affected_paths: list[str]
    post_image_hashes: dict[str, str]

    @model_validator(mode="after")
    def validate_paths(self) -> "PatchManifest":
        for path in [*self.affected_paths, *self.post_image_hashes]:
            if not path or path.startswith("/") or "\0" in path or ".." in path.split("/"):
                raise ValueError(f"unsafe repository-relative path: {path!r}")
        if set(self.affected_paths) != set(self.post_image_hashes):
            raise ValueError("patch affected_paths and post_image_hashes must match")
        if self.compressed_size < 0 or self.uncompressed_size < 0:
            raise ValueError("patch sizes cannot be negative")
        return self


class Claim(BaseModel):
    provider: str
    session_id: str
    thread_id: str = ""
    claimed_at: datetime


class HandoffRecord(BaseModel):
    schema_version: Literal[1] = 1
    handoff_id: str
    name: str
    status: Literal["ready", "claimed", "completed", "cancelled"] = "ready"
    created_at: datetime
    updated_at: datetime
    logical_task_id: str
    task_id: str
    source: HostSession
    target_provider: str = ""
    target_session_id: str = ""
    report: HandoffReport
    repository: RepositorySnapshot
    patch: PatchManifest
    recent_checks: list[dict[str, Any]] = Field(default_factory=list)
    claim: Claim | None = None


class HandoffStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.project_dir = agentpack_home() / "projects" / project_id(self.root) / "handoffs"

    def path(self, name: str) -> Path:
        return self.project_dir / validate_name(name)

    def list(self, statuses: set[str] | None = None) -> list[HandoffRecord]:
        records: list[HandoffRecord] = []
        if not self.project_dir.exists():
            return records
        for path in self.project_dir.iterdir():
            if not path.is_dir() or not (path / "handoff.json").exists():
                continue
            try:
                record = self.load(path.name)
            except (HandoffError, OSError, ValueError):
                continue
            if statuses is None or record.status in statuses:
                records.append(record)
        return sorted(records, key=lambda item: (item.created_at, item.name), reverse=True)

    def load(self, name: str) -> HandoffRecord:
        path = self.path(name) / "handoff.json"
        try:
            return HandoffRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HandoffError(f"handoff '{name}' was not found") from exc
        except (OSError, ValueError) as exc:
            raise HandoffError(f"handoff '{name}' is invalid: {exc}") from exc

    def write_record(self, record: HandoffRecord) -> None:
        _atomic_write(self.path(record.name) / "handoff.json", record.model_dump_json(indent=2).encode())

    def write_new(self, record: HandoffRecord, compressed_patch: bytes) -> None:
        destination = self.path(record.name)
        if destination.exists():
            raise HandoffError(f"handoff '{record.name}' already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{record.name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.mkdir(mode=0o700)
            _write_file(temporary / "handoff.json", record.model_dump_json(indent=2).encode())
            _write_file(temporary / "changes.patch.gz", compressed_patch)
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    @contextmanager
    def lock(self, name: str, timeout: float = 5.0) -> Iterator[None]:
        lock_path = self.project_dir / f".{validate_name(name)}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        descriptor = -1
        while descriptor < 0:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise HandoffError(f"handoff '{name}' is busy")
                time.sleep(0.05)
        try:
            os.write(descriptor, str(os.getpid()).encode())
            yield
        finally:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def create_handoff(
    root: Path,
    report: HandoffReport | dict[str, Any],
    *,
    name: str = "",
    target_provider: str = "",
    target_session_id: str = "",
    env: dict[str, str] | None = None,
) -> HandoffRecord:
    root = root.resolve()
    parsed = report if isinstance(report, HandoffReport) else HandoffReport.model_validate(report)
    _require_git(root)
    store = HandoffStore(root)
    resolved_name = next_name(store, name or parsed.task)
    patch, paths, post_hashes = capture_patch(root)
    max_bytes = load_config(root).handoff.max_patch_bytes
    if len(patch) > max_bytes:
        raise HandoffError(
            f"handoff patch is {len(patch)} bytes, above [handoff].max_patch_bytes={max_bytes}; "
            "commit large changes or explicitly raise the configured limit"
        )
    detections = scan_patch_for_secrets(root, patch, paths)
    if detections:
        raise HandoffError("secret detection blocked handoff creation:\n" + "\n".join(detections))

    compressed = gzip.compress(patch, mtime=0)
    source = detect_host_session(root, env=env)
    thread_id = source.thread_id
    identity = resolve_identity(root, task=parsed.task, thread_id=thread_id, agent=source.provider)
    parsed.changed_files = list(paths)
    parsed.dirty_files = list(paths)
    now = datetime.now(timezone.utc)
    scoped = thread_paths(root, thread_id)
    metadata_path = scoped.metadata if scoped else None
    metadata = load_pack_metadata(root, metadata_path) or {}
    snapshot = RepositorySnapshot(
        repository_fingerprint=repository_fingerprint(root),
        project_id=project_id(root),
        branch=_git_text(root, "rev-parse", "--abbrev-ref", "HEAD"),
        head_sha=_git_text(root, "rev-parse", "HEAD"),
        worktree=str(root),
        task_hash=task_hash(parsed.task),
        context_snapshot_hash=str(metadata.get("snapshot_root_hash") or metadata.get("context_hash") or ""),
        file_fingerprints=post_hashes,
    )
    record = HandoffRecord(
        handoff_id="handoff-" + uuid.uuid4().hex,
        name=resolved_name,
        created_at=now,
        updated_at=now,
        logical_task_id=logical_task_id(root, parsed.task),
        task_id=str(identity["task_id"]),
        source=source,
        target_provider=target_provider.strip().lower(),
        target_session_id=target_session_id.strip(),
        report=parsed,
        repository=snapshot,
        patch=PatchManifest(
            sha256=_sha256(patch),
            base_sha=snapshot.head_sha,
            compressed_size=len(compressed),
            uncompressed_size=len(patch),
            affected_paths=list(paths),
            post_image_hashes=post_hashes,
        ),
        recent_checks=_recent_checks(root),
    )
    store.write_new(record, compressed)
    _mark_task_status(root, thread_id, "handed_off", f"Handed off as {resolved_name}.", parsed.task, list(paths))
    record_event(root, "handoff_created", {"task": parsed.task, "thread_id": thread_id, "handoff": resolved_name})
    return record


def accept_handoff(
    root: Path,
    name: str = "",
    *,
    latest: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[HandoffRecord, list[str]]:
    root = root.resolve()
    store = HandoffStore(root)
    resolved = resolve_pending_name(store, name, latest=latest)
    warnings: list[str] = []
    with store.lock(resolved):
        record = store.load(resolved)
        session = detect_host_session(root, env=env)
        if record.status == "claimed":
            if record.claim and _same_session(record.claim, session):
                return record, warnings
            raise HandoffError(f"handoff '{resolved}' is already claimed by another session")
        if record.status != "ready":
            raise HandoffError(f"handoff '{resolved}' is {record.status}, not ready")
        _verify_target(record, session)
        _verify_repository(root, record)
        patch = _read_verified_patch(store, record)
        head = _git_text(root, "rev-parse", "HEAD")
        if not _commit_exists(root, record.patch.base_sha):
            raise HandoffError(f"source commit {record.patch.base_sha} is unavailable")
        if head != record.patch.base_sha and not _is_ancestor(root, record.patch.base_sha, head):
            raise HandoffError("destination history diverges from the handoff base commit")

        if record.repository.worktree and Path(record.repository.worktree).resolve() == root:
            current, paths, hashes = capture_patch(root)
            if _sha256(current) != record.patch.sha256 or list(paths) != record.patch.affected_paths:
                raise HandoffError("source worktree changed after the handoff was created")
            _verify_hashes(root, record.patch.post_image_hashes, hashes)
        else:
            dirty = _dirty_paths(root)
            overlap = sorted(set(dirty) & set(record.patch.affected_paths))
            actual = {path: file_fingerprint(root, path) for path in record.patch.affected_paths}
            patch_already_present = all(actual.get(path) == expected for path, expected in record.patch.post_image_hashes.items())
            if overlap and not patch_already_present:
                raise HandoffError("destination has dirty affected paths: " + ", ".join(overlap))
            unrelated = sorted(set(dirty) - set(record.patch.affected_paths))
            if unrelated:
                warnings.append("unrelated destination changes were left untouched: " + ", ".join(unrelated))
            if not patch_already_present:
                try:
                    _apply_patch(root, patch, check=True)
                    _apply_patch(root, patch, check=False)
                except Exception:
                    record_event(root, "handoff_apply_failed", {"handoff": record.name}, source="handoff")
                    raise
                try:
                    actual = {path: file_fingerprint(root, path) for path in record.patch.affected_paths}
                    _verify_hashes(root, record.patch.post_image_hashes, actual)
                except Exception:
                    _apply_patch(root, patch, reverse=True)
                    record_event(root, "handoff_apply_failed", {"handoff": record.name}, source="handoff")
                    raise

        now = datetime.now(timezone.utc)
        record.status = "claimed"
        record.updated_at = now
        record.claim = Claim(
            provider=session.provider,
            session_id=session.session_id,
            thread_id=session.thread_id,
            claimed_at=now,
        )
        store.write_record(record)
        _initialize_destination_task(root, record, session.thread_id)
        record_event(root, "handoff_claimed", {"task": record.report.task, "thread_id": session.thread_id, "handoff": record.name})
        return record, warnings


def release_handoff(root: Path, name: str, *, env: dict[str, str] | None = None) -> HandoffRecord:
    store = HandoffStore(root)
    with store.lock(name):
        record = store.load(name)
        session = detect_host_session(root, env=env)
        if record.status != "claimed" or not record.claim:
            raise HandoffError(f"handoff '{name}' is not claimed")
        if not _same_session(record.claim, session):
            raise HandoffError(f"handoff '{name}' is claimed by another session")
        record.status = "ready"
        record.claim = None
        record.updated_at = datetime.now(timezone.utc)
        store.write_record(record)
        _mark_task_status(
            root,
            session.thread_id,
            "handed_off",
            f"Released handoff {record.name}.",
            record.report.task,
            record.patch.affected_paths,
        )
        record_event(root, "handoff_released", {"handoff": name}, source="handoff")
        return record


def cancel_handoff(root: Path, name: str) -> HandoffRecord:
    store = HandoffStore(root)
    with store.lock(name):
        record = store.load(name)
        if record.status != "ready":
            raise HandoffError(f"only a ready handoff can be cancelled; '{name}' is {record.status}")
        record.status = "cancelled"
        record.updated_at = datetime.now(timezone.utc)
        store.write_record(record)
        if record.repository.worktree:
            source_root = Path(record.repository.worktree).resolve()
            if source_root.exists() and repository_fingerprint(source_root) == record.repository.repository_fingerprint:
                _mark_task_status(
                    source_root,
                    record.source.thread_id,
                    "in_progress",
                    "Handoff cancelled; source work resumed.",
                    record.report.task,
                    record.patch.affected_paths,
                )
        record_event(root, "handoff_cancelled", {"handoff": name}, source="handoff")
        return record


def complete_claimed_handoff(root: Path, *, env: dict[str, str] | None = None) -> HandoffRecord | None:
    store = HandoffStore(root)
    session = detect_host_session(root, env=env)
    matches = [r for r in store.list({"claimed"}) if r.claim and _same_session(r.claim, session)]
    if not matches:
        return None
    if len(matches) > 1:
        raise HandoffError("current session has multiple claimed handoffs; release all but the one being finished")
    record = matches[0]
    with store.lock(record.name):
        record = store.load(record.name)
        if record.status != "claimed" or not record.claim or not _same_session(record.claim, session):
            return None
        record.status = "completed"
        record.updated_at = datetime.now(timezone.utc)
        store.write_record(record)
        record_event(root, "handoff_completed", {"handoff": record.name}, source="handoff")
        return record


def export_handoff(root: Path, name: str, output: Path) -> Path:
    store = HandoffStore(root)
    record = store.load(name)
    patch_bytes = (store.path(name) / "changes.patch.gz").read_bytes()
    portable = record.model_copy(deep=True)
    portable.repository.worktree = ""
    portable.repository.project_id = ""
    portable.source.session_id = ""
    portable.source.thread_id = ""
    portable.target_session_id = ""
    portable.claim = None
    portable.status = "ready"
    handoff_bytes = portable.model_dump_json(indent=2).encode()
    checksums = {"handoff.json": _sha256(handoff_bytes), "changes.patch.gz": _sha256(patch_bytes)}
    manifest = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "name": record.name,
        "files": checksums,
        "bundle_checksum": _sha256("".join(f"{key}:{checksums[key]}" for key in sorted(checksums)).encode()),
    }
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        archive.writestr("handoff.json", handoff_bytes)
        archive.writestr("changes.patch.gz", patch_bytes)
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)
    return output


def import_handoff(root: Path, bundle: Path) -> HandoffRecord:
    root = root.resolve()
    max_patch = load_config(root).handoff.max_patch_bytes
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        expected = {"manifest.json", "handoff.json", "changes.patch.gz"}
        if len(names) != len(expected) or set(names) != expected or any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise HandoffError("handoff bundle contains unexpected or unsafe paths")
        if sum(item.file_size for item in archive.infolist()) > max_patch + 2 * 1024 * 1024:
            raise HandoffError("handoff bundle expands beyond the configured size limit")
        manifest_bytes = archive.read("manifest.json")
        handoff_bytes = archive.read("handoff.json")
        patch_bytes = archive.read("changes.patch.gz")
    try:
        manifest = json.loads(manifest_bytes)
        record = HandoffRecord.model_validate_json(handoff_bytes)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HandoffError(f"malformed handoff bundle: {exc}") from exc
    if manifest.get("schema_version") != EXPORT_SCHEMA_VERSION:
        raise HandoffError("unsupported handoff bundle schema")
    checksums = {"handoff.json": _sha256(handoff_bytes), "changes.patch.gz": _sha256(patch_bytes)}
    if manifest.get("files") != checksums:
        raise HandoffError("handoff bundle file checksum mismatch")
    bundle_checksum = _sha256("".join(f"{key}:{checksums[key]}" for key in sorted(checksums)).encode())
    if manifest.get("bundle_checksum") != bundle_checksum:
        raise HandoffError("handoff bundle checksum mismatch")
    try:
        patch = _decompress_limited(patch_bytes, max_patch)
    except (OSError, EOFError) as exc:
        raise HandoffError("handoff patch compression is invalid") from exc
    if len(patch) > max_patch:
        raise HandoffError("handoff patch expands beyond [handoff].max_patch_bytes")
    if _sha256(patch) != record.patch.sha256 or len(patch) != record.patch.uncompressed_size:
        raise HandoffError("handoff patch checksum or size mismatch")
    if record.repository.repository_fingerprint != repository_fingerprint(root):
        raise HandoffError("handoff bundle belongs to a different repository")
    if not _commit_exists(root, record.patch.base_sha):
        raise HandoffError(f"source commit {record.patch.base_sha} is unavailable")
    store = HandoffStore(root)
    if store.path(record.name).exists():
        raise HandoffError(f"handoff name collision: '{record.name}' already exists")
    record.repository.project_id = project_id(root)
    record.repository.worktree = ""
    record.status = "ready"
    record.claim = None
    record.updated_at = datetime.now(timezone.utc)
    store.write_new(record, patch_bytes)
    record_event(root, "handoff_imported", {"handoff": record.name}, source="handoff")
    return record


def capture_patch(root: Path) -> tuple[bytes, list[str], dict[str, str]]:
    _require_git(root)
    with tempfile.TemporaryDirectory(prefix="agentpack-handoff-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        _run_git(root, ["read-tree", "HEAD"], env=env)
        _run_git(root, ["add", "-A", "--", "."], env=env)
        patch = _run_git(root, ["diff", "--cached", "--binary", "--full-index", "HEAD"], env=env, text=False)
        raw_paths = _run_git(root, ["diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD"], env=env, text=False)
    paths = [part.decode("utf-8", "surrogateescape") for part in raw_paths.split(b"\0") if part]
    return patch, paths, {path: file_fingerprint(root, path) for path in paths}


def scan_patch_for_secrets(root: Path, patch: bytes, paths: list[str]) -> list[str]:
    detections: list[str] = []
    _, patch_warnings = redact_secrets(patch.decode("utf-8", "replace"), "changes.patch")
    detections.extend(patch_warnings)
    for relative in paths:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if b"\0" in content:
            continue
        _, warnings = redact_secrets(content.decode("utf-8", "replace"), relative)
        detections.extend(warnings)
    return list(dict.fromkeys(detections))


def repository_fingerprint(root: Path) -> str:
    remote = _git_text(root, "config", "--get", "remote.origin.url")
    if remote:
        normalized = remote.strip().lower().removesuffix(".git")
        normalized = re.sub(r"^[^@]+@([^:]+):", r"ssh://\1/", normalized)
        normalized = re.sub(r"^https?://[^@/]+@", "https://", normalized)
        return "repository-" + _sha256(normalized.encode())[:24]
    roots = _git_text(root, "rev-list", "--max-parents=0", "HEAD")
    if roots:
        return "repository-" + _sha256("\n".join(sorted(roots.splitlines())).encode())[:24]
    common = _git_text(root, "rev-parse", "--git-common-dir")
    path = Path(common)
    resolved = (root / path).resolve() if common and not path.is_absolute() else path.resolve()
    return "repository-" + _sha256(str(resolved).encode())[:24]


def detect_host_session(root: Path, env: dict[str, str] | None = None) -> HostSession:
    source = dict(env) if env is not None else dict(os.environ)
    explicit_thread = str(source.get("AGENTPACK_THREAD_ID") or "").strip()
    for provider, variable in _HOST_ENV:
        value = str(source.get(variable) or "").strip()
        if value:
            return HostSession(provider=provider, session_id=value, thread_id=explicit_thread or value)
    identity = resolve_identity(root, thread_id=resolve_session_thread_option("", env=source) or "")
    return HostSession(
        provider=str(identity.get("agent") or "generic"),
        session_id=str(identity.get("session_id") or f"generic-{os.getpid()}"),
        thread_id=resolve_session_thread_option("", env=source) or "",
    )


def resolve_pending_name(store: HandoffStore, name: str, *, latest: bool = False) -> str:
    if name.strip():
        return validate_name(name)
    pending = store.list({"ready"})
    if not pending:
        raise HandoffError("no ready handoffs are available")
    if len(pending) == 1 or latest:
        return pending[0].name
    raise HandoffError("multiple ready handoffs exist; provide a name")


def next_name(store: HandoffStore, value: str) -> str:
    base = slug_name(value)
    candidate = base
    suffix = 2
    while store.path(candidate).exists():
        tail = f"-{suffix}"
        candidate = base[: 48 - len(tail)].rstrip("-") + tail
        suffix += 1
    return candidate


def slug_name(value: str) -> str:
    slug = _SAFE_NAME.sub("-", value.strip().lower()).strip("-")[:48].rstrip("-")
    return slug or "handoff"


def validate_name(value: str) -> str:
    name = value.strip()
    if name != slug_name(name) or len(name) > 48:
        raise HandoffError("handoff names must be lowercase letters, numbers, and hyphens (max 48)")
    return name


def file_fingerprint(root: Path, relative: str) -> str:
    path = root / relative
    if path.is_symlink():
        return "symlink:" + _sha256(os.readlink(path).encode())
    if not path.exists():
        return "deleted"
    if path.is_file():
        return "sha256:" + _sha256(path.read_bytes())
    return "other"


def render_markdown(record: HandoffRecord) -> str:
    report = record.report
    lines = [f"# Handoff: {record.name}", "", f"Status: {record.status}", "", "## Task", report.task, "", "## Summary", report.summary, "", "## Next Action", report.next_action]
    for title, values in (("Acceptance Criteria", report.acceptance_criteria), ("Completed", report.completed), ("Remaining", report.remaining)):
        lines.extend(["", f"## {title}"])
        lines.extend(f"- {item}" for item in values)
    if report.decisions:
        lines.extend(["", "## Decisions"])
        lines.extend(f"- {item.decision}: {item.rationale}" for item in report.decisions)
    if report.blockers:
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {item.blocker}: {item.required_action}" for item in report.blockers)
    lines.extend(["", "## Validation"])
    lines.extend(f"- `{item.command}`: {item.outcome} at {item.tested_sha} ({item.timestamp.isoformat()})" for item in report.validation)
    lines.extend(["", "## Changed Files"])
    lines.extend(f"- `{path}`" for path in record.patch.affected_paths)
    return "\n".join(lines).rstrip() + "\n"


def _initialize_destination_task(root: Path, record: HandoffRecord, thread_id: str) -> None:
    scoped = thread_paths(root, thread_id)
    task_path = scoped.task if scoped else root / ".agentpack" / "task.md"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(task_path, (record.report.task.strip() + "\n").encode())
    _mark_task_status(
        root,
        thread_id,
        "in_progress",
        f"Resumed from handoff {record.name}.",
        record.report.task,
        record.patch.affected_paths,
    )


def _mark_task_status(
    root: Path,
    thread_id: str,
    status: str,
    summary: str,
    task: str,
    dirty_files: list[str],
) -> None:
    _set_task_status(root, thread_id, status, summary)
    if thread_id:
        append_thread_index(
            root,
            build_thread_index_row(
                root=root,
                thread_id=thread_id,
                task=task,
                branch=_git_text(root, "rev-parse", "--abbrev-ref", "HEAD"),
                selected_files=[],
                dirty_files=dirty_files,
                status=status,
            ),
        )


def _set_task_status(root: Path, thread_id: str, status: str, summary: str) -> None:
    path = task_state_path(root, thread_id or None)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = [line for line in existing if not line.lower().startswith(("status:", "summary:"))]
    content = [f"Status: {status}", f"Summary: {summary}"]
    if remaining:
        content.extend(["", *remaining])
    _atomic_write(path, ("\n".join(content).rstrip() + "\n").encode())


def _recent_checks(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for event in reversed(read_events(root, limit=100)):
        if event.get("event_type") != "check_completed" and event.get("type") != "check_completed":
            continue
        checks.append({key: event.get(key) for key in ("command", "status", "returncode", "summary", "occurred_at")})
        if len(checks) == 5:
            break
    return checks


def _read_verified_patch(store: HandoffStore, record: HandoffRecord) -> bytes:
    compressed = (store.path(record.name) / "changes.patch.gz").read_bytes()
    if len(compressed) != record.patch.compressed_size:
        raise HandoffError("handoff patch compressed size mismatch")
    try:
        patch = _decompress_limited(compressed, load_config(store.root).handoff.max_patch_bytes)
    except (OSError, EOFError) as exc:
        raise HandoffError("handoff patch is corrupt") from exc
    if len(patch) != record.patch.uncompressed_size or _sha256(patch) != record.patch.sha256:
        raise HandoffError("handoff patch checksum mismatch")
    return patch


def _verify_repository(root: Path, record: HandoffRecord) -> None:
    if repository_fingerprint(root) != record.repository.repository_fingerprint:
        raise HandoffError("handoff belongs to a different repository")


def _verify_target(record: HandoffRecord, session: HostSession) -> None:
    if record.target_provider and record.target_provider != session.provider:
        raise HandoffError(f"handoff is restricted to provider '{record.target_provider}'")
    if record.target_session_id and record.target_session_id != session.session_id:
        raise HandoffError("handoff is restricted to a different destination session")


def _verify_hashes(root: Path, expected: dict[str, str], actual: dict[str, str]) -> None:
    mismatches = [path for path, value in expected.items() if actual.get(path) != value]
    if mismatches:
        raise HandoffError("post-image verification failed for: " + ", ".join(mismatches))


def _same_session(claim: Claim, session: HostSession) -> bool:
    return claim.provider == session.provider and claim.session_id == session.session_id


def _apply_patch(root: Path, patch: bytes, *, check: bool = False, reverse: bool = False) -> None:
    command = ["git", "apply", "--binary"]
    if check:
        command.append("--check")
    if reverse:
        command.append("--reverse")
    result = subprocess.run(command, cwd=root, input=patch, capture_output=True, timeout=60)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise HandoffError(f"git apply{' --check' if check else ''} failed: {detail}")


def _dirty_paths(root: Path) -> list[str]:
    raw = _run_git(root, ["status", "--porcelain=v1", "-z"], text=False)
    entries = [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        status = entry[:2]
        if len(entry) >= 3:
            paths.append(entry[3:])
        if "R" in status or "C" in status:
            index += 1
            if index < len(entries):
                paths.append(entries[index])
        index += 1
    return list(dict.fromkeys(paths))


def _commit_exists(root: Path, sha: str) -> bool:
    result = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root, capture_output=True)
    return result.returncode == 0


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=root).returncode == 0


def _require_git(root: Path) -> None:
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, capture_output=True).returncode:
        raise HandoffError("handoffs require a Git worktree")


def _git_text(root: Path, *args: str) -> str:
    return str(_run_git(root, list(args), text=True)).strip()


def _run_git(root: Path, args: list[str], *, env: dict[str, str] | None = None, text: bool = True) -> Any:
    result = subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, text=text, timeout=60)
    if result.returncode:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise HandoffError(f"git {' '.join(args)} failed: {str(stderr).strip()}")
    return result.stdout


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _decompress_limited(content: bytes, max_bytes: int) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as archive:
            result = archive.read(max_bytes + 1)
    except (OSError, EOFError) as exc:
        raise HandoffError("handoff patch compression is invalid") from exc
    if len(result) > max_bytes:
        raise HandoffError("handoff patch expands beyond [handoff].max_patch_bytes")
    return result
