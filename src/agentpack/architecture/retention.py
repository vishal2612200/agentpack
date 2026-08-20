from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from agentpack.core.config import load_config

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt below.
    fcntl = None


_SNAPSHOT_NAME = re.compile(
    r"^(?:worktree-[^-]+|[0-9a-f]{7,64})-(?P<schema>\d+)-(?P<profile>[0-9a-f]+)\.json$"
)
_STATE_NAME = re.compile(r"^[0-9a-f]+-[0-9a-f]+-[0-9a-f]+\.json$")
_HEADER_SIZE = 4096


@dataclass
class PruneReport:
    cache_dir: str
    keep_refs: int
    dry_run: bool
    max_cache_bytes: int = 0
    deleted_files: int = 0
    deleted_bytes: int = 0
    retained_files: int = 0
    retained_bytes: int = 0
    skipped_files: int = 0
    skipped_paths: list[str] = field(default_factory=list)
    cache_bytes: int = 0
    over_budget: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "cache_dir": self.cache_dir,
            "keep_refs": self.keep_refs,
            "dry_run": self.dry_run,
            "max_cache_bytes": self.max_cache_bytes,
            "deleted_files": self.deleted_files,
            "deleted_bytes": self.deleted_bytes,
            "retained_files": self.retained_files,
            "retained_bytes": self.retained_bytes,
            "skipped_files": self.skipped_files,
            "skipped_paths": sorted(self.skipped_paths),
            "cache_bytes": self.cache_bytes,
            "over_budget": self.over_budget,
        }

    def skip(self, path: Path) -> None:
        self.skipped_files += 1
        if len(self.skipped_paths) < 20:
            self.skipped_paths.append(str(path))


@contextmanager
def _cache_lock(cache_dir: Path) -> Iterator[None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / ".prune.lock"
    with lock_path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:  # pragma: no cover - exercised only on Windows.
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - exercised only on Windows.
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def prune_architecture_cache(
    root: Path,
    *,
    keep_refs: int | None = None,
    dry_run: bool = True,
    force: bool = False,
    schema_version: int | None = None,
    extractor_profile_hash: str | None = None,
    repo_fingerprint: str | None = None,
) -> PruneReport:
    """Keep bounded architecture snapshots and sweep unreachable cache data."""
    cfg = load_config(root)
    keep_refs = cfg.architecture.max_cached_refs if keep_refs is None else keep_refs
    if keep_refs < 2:
        raise ValueError("keep_refs must be at least 2")

    configured_cache = Path(cfg.architecture.cache_dir)
    cache_dir = configured_cache if configured_cache.is_absolute() else root / configured_cache
    report = PruneReport(str(cache_dir), keep_refs, dry_run, cfg.architecture.max_cache_bytes)
    if not cache_dir.exists():
        return report

    with _cache_lock(cache_dir):
        schema_version, extractor_profile_hash, repo_fingerprint = _cache_identity(
            root,
            schema_version=schema_version,
            extractor_profile_hash=extractor_profile_hash,
            repo_fingerprint=repo_fingerprint,
        )
        candidates = _collect_candidates(
            cache_dir,
            keep_refs=keep_refs,
            schema_version=schema_version,
            extractor_profile_hash=extractor_profile_hash,
            repo_fingerprint=repo_fingerprint,
            report=report,
            force=force,
            max_cache_bytes=cfg.architecture.max_cache_bytes,
        )
        planned: set[Path] = set()
        for path in sorted(candidates):
            if _delete(path, report):
                planned.add(path)

        retained_states = _retained_state_paths(cache_dir, candidates)
        live_records, live_facts, safe_records, safe_facts = _live_cache_keys(retained_states, report)
        if safe_records:
            for path in (cache_dir / "records").glob("*.json"):
                if path.stem not in live_records:
                    if _delete(path, report):
                        planned.add(path)
        if safe_facts:
            for path in (cache_dir / "facts").glob("*.json"):
                if path.name not in live_facts:
                    if _delete(path, report):
                        planned.add(path)

        _summarize(cache_dir, planned, report)
        report.over_budget = bool(
            report.max_cache_bytes and report.cache_bytes > report.max_cache_bytes
        )
    return report


def _cache_identity(
    root: Path,
    *,
    schema_version: int | None,
    extractor_profile_hash: str | None,
    repo_fingerprint: str | None,
) -> tuple[int, str | None, str | None]:
    if schema_version is None or extractor_profile_hash is None or repo_fingerprint is None:
        from agentpack.architecture.service import SCHEMA_VERSION, _extractor_profile_hash, _repo_fingerprint

        schema_version = SCHEMA_VERSION if schema_version is None else schema_version
        extractor_profile_hash = (
            _extractor_profile_hash() if extractor_profile_hash is None else extractor_profile_hash
        )
        repo_fingerprint = _repo_fingerprint(root) if repo_fingerprint is None else repo_fingerprint
    return schema_version, extractor_profile_hash, repo_fingerprint


def _collect_candidates(
    cache_dir: Path,
    *,
    keep_refs: int,
    schema_version: int,
    extractor_profile_hash: str,
    repo_fingerprint: str,
    report: PruneReport,
    force: bool,
    max_cache_bytes: int,
) -> set[Path]:
    root_candidates = [
        path
        for path in cache_dir.glob("*.json")
        if path.is_file() and _SNAPSHOT_NAME.fullmatch(path.name)
    ]
    state_dir = cache_dir / "state"
    state_candidates = [
        path
        for path in state_dir.glob("*.json")
        if path.is_file() and _STATE_NAME.fullmatch(path.name)
    ]
    current_prefix = f"{repo_fingerprint}-{extractor_profile_hash}-"
    current_states = [path for path in state_candidates if path.name.startswith(current_prefix)]
    stale_states = [path for path in state_candidates if not path.name.startswith(current_prefix)]
    current_root = [
        path
        for path in root_candidates
        if (match := _SNAPSHOT_NAME.fullmatch(path.name))
        and int(match["schema"]) == schema_version
        and match["profile"] == extractor_profile_hash
    ]
    current_ref_count = sum(not path.name.startswith("worktree-") for path in current_root)
    current_worktree_count = sum(path.name.startswith("worktree-") for path in current_root)
    over_limit = (
        current_ref_count > keep_refs
        or current_worktree_count > 1
        or len(current_states) > keep_refs + 1
    )
    stale_root = any(
        (match := _SNAPSHOT_NAME.fullmatch(path.name))
        and (int(match["schema"]) != schema_version or match["profile"] != extractor_profile_hash)
        for path in root_candidates
    )
    over_budget = bool(max_cache_bytes and _directory_bytes(cache_dir) > max_cache_bytes)
    if not force and not over_limit and not stale_root and not stale_states and not over_budget:
        return set()

    candidates = set(stale_states)
    valid_states: list[tuple[Path, dict[str, str]]] = []
    unreadable_current_state = False
    for path in current_states:
        header = _read_state_header(path)
        if header is None:
            report.skip(path)
            unreadable_current_state = True
            continue
        if (
            header.get("schema_version") != str(schema_version)
            or header.get("repository_identity") != repo_fingerprint
            or header.get("extractor_profile_hash") != extractor_profile_hash
        ):
            candidates.add(path)
            continue
        valid_states.append((path, header))

    if unreadable_current_state:
        # Do not break snapshot/state pairing when current metadata cannot be
        # inspected. Stale identities remain safe to remove.
        for path in root_candidates:
            match = _SNAPSHOT_NAME.fullmatch(path.name)
            if match and (int(match["schema"]) != schema_version or match["profile"] != extractor_profile_hash):
                candidates.add(path)
        return candidates

    worktree_states = sorted(
        (item for item in valid_states if item[1].get("ref") == "WORKTREE"),
        key=lambda item: _mtime(item[0]),
        reverse=True,
    )
    ref_states = sorted(
        (item for item in valid_states if item[1].get("ref") != "WORKTREE"),
        key=lambda item: _mtime(item[0]),
        reverse=True,
    )
    retained_states = {path for path, _ in worktree_states[:1]}
    retained_states.update(path for path, _ in ref_states[:keep_refs])
    candidates.update(path for path, _ in valid_states if path not in retained_states)

    retained_basenames = {path.name for path in retained_states}
    manifest_dir = cache_dir / "manifests"
    for path in manifest_dir.glob("*.json"):
        if _STATE_NAME.fullmatch(path.name) and path.name not in retained_basenames:
            candidates.add(path)

    expected_snapshots = {
        f"{header['commit_sha']}-{schema_version}-{extractor_profile_hash}.json"
        for path, header in valid_states
        if path in retained_states and header.get("ref") != "WORKTREE" and header.get("commit_sha")
    }
    retained_worktree = bool(worktree_states and worktree_states[0][0] in retained_states)
    worktree_snapshots = sorted(
        [
            path
            for path in root_candidates
            if (match := _SNAPSHOT_NAME.fullmatch(path.name))
            and match["schema"] == str(schema_version)
            and match["profile"] == extractor_profile_hash
            and path.name.startswith("worktree-")
        ],
        key=_mtime,
        reverse=True,
    )
    if retained_worktree and worktree_snapshots:
        expected_snapshots.add(worktree_snapshots[0].name)

    if max_cache_bytes:
        retained_ref_items = [item for item in ref_states[:keep_refs]]
        while _directory_bytes(cache_dir, candidates) > max_cache_bytes and len(retained_ref_items) > 2:
            state_path, header = retained_ref_items.pop()
            candidates.add(state_path)
            manifest_path = manifest_dir / state_path.name
            if manifest_path.exists():
                candidates.add(manifest_path)
            commit_sha = header.get("commit_sha")
            if commit_sha:
                candidates.add(cache_dir / f"{commit_sha}-{schema_version}-{extractor_profile_hash}.json")
            expected_snapshots.discard(
                f"{commit_sha}-{schema_version}-{extractor_profile_hash}.json"
            )
    for path in root_candidates:
        if path.name not in expected_snapshots:
            candidates.add(path)

    # Keep dependency files reachable from retained states. State parsing below
    # is deliberately deferred until after old states are selected.
    return candidates


def _retained_state_paths(cache_dir: Path, candidates: set[Path]) -> list[Path]:
    state_dir = cache_dir / "state"
    return [path for path in state_dir.glob("*.json") if path.is_file() and path not in candidates]


def _live_cache_keys(
    state_paths: list[Path], report: PruneReport
) -> tuple[set[str], set[str], bool, bool]:
    live_records: set[str] = set()
    live_facts: set[str] = set()
    records_safe = True
    facts_safe = True
    for path in state_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            report.skip(path)
            records_safe = False
            facts_safe = False
            continue
        record_keys = payload.get("record_keys")
        if not isinstance(record_keys, dict):
            report.skip(path)
            records_safe = False
        else:
            live_records.update(str(key) for key in record_keys.values() if key)

        manifest = payload.get("manifest")
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(files, dict):
            report.skip(path)
            facts_safe = False
            continue
        for value in files.values():
            cache_path = value.get("cache_path") if isinstance(value, dict) else None
            if cache_path:
                live_facts.add(Path(str(cache_path)).name)
            else:
                facts_safe = False
    return live_records, live_facts, records_safe, facts_safe


def _read_state_header(path: Path) -> dict[str, str] | None:
    try:
        prefix = path.read_text(encoding="utf-8")[:_HEADER_SIZE]
    except (OSError, UnicodeDecodeError):
        return None
    values: dict[str, str] = {}
    for key in ("schema_version", "repository_identity", "ref", "commit_sha", "extractor_profile_hash"):
        match = re.search(rf'"{key}"\s*:\s*(?:"([^"]*)"|(\d+))', prefix)
        if match is None:
            return None
        values[key] = match.group(1) or match.group(2)
    return values


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _delete(path: Path, report: PruneReport) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        report.skip(path)
        return False
    if not report.dry_run:
        try:
            path.unlink()
        except OSError:
            report.skip(path)
            return False
    report.deleted_files += 1
    report.deleted_bytes += size
    return True


def _summarize(cache_dir: Path, planned: set[Path], report: PruneReport) -> None:
    for path in cache_dir.rglob("*"):
        if not path.is_file() or path in planned:
            continue
        try:
            report.retained_files += 1
            report.retained_bytes += path.stat().st_size
        except OSError:
            report.skip(path)
    report.cache_bytes = report.retained_bytes


def _directory_bytes(cache_dir: Path, planned: set[Path] | None = None) -> int:
    planned = planned or set()
    total = 0
    for path in cache_dir.rglob("*"):
        if path.is_file() and path not in planned:
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total
