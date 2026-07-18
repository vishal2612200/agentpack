from __future__ import annotations

import hashlib
from pathlib import Path

import pathspec

from agentpack.core.ignore import is_ignored
from agentpack.core.models import FileInfo, ScanResult
from agentpack.core.token_estimator import estimate_tokens, estimate_tokens_bytes

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".ttf", ".woff", ".woff2", ".eot",
    ".pyc", ".pyo", ".class",
    ".db", ".sqlite", ".sqlite3",
}

LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".tf": "terraform",
    ".xml": "xml",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".graphqls": "graphql",
    ".gql": "graphql",
}

ALWAYS_SKIP = {".git", ".agentpack", ".claude"}


def _detect_language(abs_path: Path) -> str | None:
    """Detect language by extension, falling back to filename for Dockerfiles.

    Dockerfiles have no extension (`Dockerfile`, `Dockerfile.dev`, ...), so a
    pure suffix lookup can't classify them.
    """
    name = abs_path.name.lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "dockerfile"
    return LANGUAGE_MAP.get(abs_path.suffix.lower())


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        chunk = path.read_bytes()[:1024]
        return b"\x00" in chunk
    except OSError:
        return True


def _build_glob_specs(
    include_globs: list[str],
    exclude_globs: list[str],
) -> tuple[pathspec.PathSpec | None, pathspec.PathSpec | None]:
    inc = pathspec.PathSpec.from_lines("gitignore", include_globs) if include_globs else None
    exc = pathspec.PathSpec.from_lines("gitignore", exclude_globs) if exclude_globs else None
    return inc, exc


def _rel_path(path: str) -> str:
    return path.replace("\\", "/")


def _is_always_skipped(rel: Path, generated_paths: set[str]) -> bool:
    return any(p in ALWAYS_SKIP for p in rel.parts) or _rel_path(str(rel)) in generated_paths


def _is_glob_ignored(
    rel_str: str,
    ignore_spec: pathspec.PathSpec,
    inc_spec: pathspec.PathSpec | None,
    exc_spec: pathspec.PathSpec | None,
) -> bool:
    if inc_spec is not None and not inc_spec.match_file(rel_str):
        return True
    if exc_spec is not None and exc_spec.match_file(rel_str):
        return True
    return is_ignored(ignore_spec, rel_str)


def _ignored_file_info(path: str, abs_path: Path) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=abs_path,
        size_bytes=abs_path.stat().st_size,
        estimated_tokens=0,
        ignored=True,
    )


def _binary_file_info(path: str, abs_path: Path) -> FileInfo:
    size = abs_path.stat().st_size
    lang = _detect_language(abs_path)
    return FileInfo(
        path=path,
        abs_path=abs_path,
        language=lang,
        size_bytes=size,
        estimated_tokens=0,
        binary=True,
    )


def _packable_file_info(
    path: str,
    abs_path: Path,
    max_file_tokens: int,
    prev_files: dict[str, dict],
) -> FileInfo | None:
    size = abs_path.stat().st_size
    lang = _detect_language(abs_path)
    fhash = file_hash(abs_path)

    prev = prev_files.get(path)
    if prev and prev.get("hash") == fhash:
        cached_tokens = prev.get("estimated_tokens", estimate_tokens_bytes(size))
        return FileInfo(
            path=path,
            abs_path=abs_path,
            language=lang,
            size_bytes=size,
            estimated_tokens=cached_tokens,
            hash=fhash,
            too_large=cached_tokens > max_file_tokens,
            content=None,
        )

    try:
        text = abs_path.read_text(errors="replace")
    except OSError:
        return None

    tokens = estimate_tokens(text)
    return FileInfo(
        path=path,
        abs_path=abs_path,
        language=lang,
        size_bytes=size,
        estimated_tokens=tokens,
        hash=fhash,
        too_large=tokens > max_file_tokens,
        content=text,
    )


def _scan_one_file(
    root: Path,
    rel_str: str,
    ignore_spec: pathspec.PathSpec,
    inc_spec: pathspec.PathSpec | None,
    exc_spec: pathspec.PathSpec | None,
    max_file_tokens: int,
    prev_files: dict[str, dict],
    generated_paths: set[str],
) -> tuple[str, FileInfo] | None:
    rel_path = Path(rel_str)
    abs_path = root / rel_path
    if not abs_path.exists() or not abs_path.is_file():
        return None
    if _is_always_skipped(rel_path, generated_paths):
        return None
    normalized = _rel_path(str(rel_path))
    if _is_glob_ignored(normalized, ignore_spec, inc_spec, exc_spec):
        return "ignored", _ignored_file_info(normalized, abs_path)
    if _is_binary(abs_path):
        return "binary", _binary_file_info(normalized, abs_path)
    info = _packable_file_info(normalized, abs_path, max_file_tokens, prev_files)
    return ("packable", info) if info is not None else None


def scan_incremental(
    root: Path,
    ignore_spec: pathspec.PathSpec,
    changed_paths: set[str],
    max_file_tokens: int = 4000,
    previous_snapshot: dict | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    always_skip_paths: set[str] | None = None,
) -> ScanResult:
    """Reuse snapshot entries and re-hash only known changed paths.

    Caller is responsible for invoking this only after validating that ignore
    rules, config, branch, and cache metadata are safe for incremental reuse.
    """
    prev_files: dict[str, dict] = (previous_snapshot or {}).get("files", {})
    inc_spec, exc_spec = _build_glob_specs(include_globs or [], exclude_globs or [])
    generated_paths = {_rel_path(p) for p in (always_skip_paths or set())}

    candidates: set[str] = set()
    for raw_path in changed_paths:
        normalized = _rel_path(raw_path).strip()
        if not normalized:
            continue
        abs_path = root / normalized
        if abs_path.is_dir():
            for child in abs_path.rglob("*"):
                if child.is_file():
                    candidates.add(_rel_path(str(child.relative_to(root))))
        else:
            candidates.add(normalized)

    packable: list[FileInfo] = []
    ignored: list[FileInfo] = []
    binary: list[FileInfo] = []
    removed_or_changed = set(candidates)

    for path, prev in prev_files.items():
        if path in removed_or_changed:
            continue
        if _rel_path(path) in generated_paths:
            continue
        abs_path = root / path
        if not abs_path.exists():
            continue
        packable.append(
            FileInfo(
                path=path,
                abs_path=abs_path,
                language=prev.get("language"),
                size_bytes=int(prev.get("size_bytes") or 0),
                estimated_tokens=int(prev.get("estimated_tokens") or 0),
                hash=prev.get("hash"),
                too_large=int(prev.get("estimated_tokens") or 0) > max_file_tokens,
                content=None,
            )
        )

    rehashed = 0
    for path in sorted(candidates):
        scanned = _scan_one_file(
            root,
            path,
            ignore_spec,
            inc_spec,
            exc_spec,
            max_file_tokens,
            prev_files,
            generated_paths,
        )
        if scanned is None:
            continue
        bucket, info = scanned
        rehashed += 1
        if bucket == "packable":
            packable.append(info)
        elif bucket == "ignored":
            ignored.append(info)
        else:
            binary.append(info)

    return ScanResult(
        packable=sorted(packable, key=lambda item: item.path),
        ignored=ignored,
        binary=binary,
        scan_mode="incremental",
        rehashed_count=rehashed,
        reused_count=len(packable) - sum(1 for f in packable if f.path in candidates),
    )


def scan(
    root: Path,
    ignore_spec: pathspec.PathSpec,
    max_file_tokens: int = 4000,
    previous_snapshot: dict | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    always_skip_paths: set[str] | None = None,
) -> ScanResult:
    packable: list[FileInfo] = []
    ignored: list[FileInfo] = []
    binary: list[FileInfo] = []

    prev_files: dict[str, dict] = (previous_snapshot or {}).get("files", {})
    inc_spec, exc_spec = _build_glob_specs(include_globs or [], exclude_globs or [])
    generated_paths = {p.replace("\\", "/") for p in (always_skip_paths or set())}

    for abs_path in root.rglob("*"):
        if not abs_path.is_file():
            continue

        rel = abs_path.relative_to(root)
        rel_str = str(rel)

        if _is_always_skipped(rel, generated_paths):
            continue

        rel_str = _rel_path(rel_str)
        if _is_glob_ignored(rel_str, ignore_spec, inc_spec, exc_spec):
            ignored.append(_ignored_file_info(rel_str, abs_path))
            continue

        if _is_binary(abs_path):
            binary.append(_binary_file_info(rel_str, abs_path))
            continue

        info = _packable_file_info(rel_str, abs_path, max_file_tokens, prev_files)
        if info is not None:
            packable.append(info)

    return ScanResult(
        packable=packable,
        ignored=ignored,
        binary=binary,
        scan_mode="full",
        rehashed_count=len(packable),
        reused_count=0,
    )
