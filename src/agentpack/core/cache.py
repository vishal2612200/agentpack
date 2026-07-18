from __future__ import annotations

from pathlib import Path

from agentpack.core.models import (
    FileSummary,
    SUMMARY_EXTRACTOR_PROFILE,
    SUMMARY_SCHEMA_VERSION,
)


def _cache_key(
    path: str,
    file_hash: str,
    provider: str,
    schema_version: int,
    extractor_profile_hash: str = SUMMARY_EXTRACTOR_PROFILE,
) -> str:
    import hashlib
    raw = f"{path}|{file_hash}|{provider}|{schema_version}|{extractor_profile_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_dir(root: Path) -> Path:
    return root / ".agentpack" / "cache"


def load_summary(
    root: Path,
    path: str,
    file_hash: str,
    provider: str = "offline",
    schema_version: int = SUMMARY_SCHEMA_VERSION,
    extractor_profile_hash: str = SUMMARY_EXTRACTOR_PROFILE,
) -> FileSummary | None:
    key = _cache_key(path, file_hash, provider, schema_version, extractor_profile_hash)
    cache_file = _cache_dir(root) / f"{key}.json"
    if not cache_file.exists():
        return None
    try:
        return FileSummary.model_validate_json(cache_file.read_text())
    except Exception:
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def save_summary(root: Path, summary: FileSummary) -> None:
    key = _cache_key(
        summary.path,
        summary.hash,
        summary.provider,
        summary.schema_version,
        summary.extractor_profile_hash,
    )
    cache_dir = _cache_dir(root)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{key}.json"
    cache_file.write_text(summary.model_dump_json(indent=2))
