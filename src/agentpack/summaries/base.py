from __future__ import annotations

import concurrent.futures
import multiprocessing
import os
from pathlib import Path

from agentpack.core.models import FileInfo, FileSummary, SUMMARY_EXTRACTOR_PROFILE
from agentpack.core import cache as summary_cache
from agentpack.summaries import offline

_SUMMARY_MEMORY_CACHE: dict[tuple[str, str, str, str], FileSummary] = {}


def _build_one(path: str, abs_path_str: str, language: str | None, file_hash: str) -> FileSummary:
    return offline.summarize(path, Path(abs_path_str), language, file_hash)


def _memory_key(root: Path, fi: FileInfo) -> tuple[str, str, str, str] | None:
    if fi.hash is None:
        return None
    return (str(root.resolve()), fi.path, fi.hash, "offline")


def get_or_build_summary(fi: FileInfo, root: Path) -> FileSummary:
    if fi.hash is None:
        return offline.summarize(fi.path, fi.abs_path, fi.language, "")

    key = _memory_key(root, fi)
    if key and key in _SUMMARY_MEMORY_CACHE:
        return _SUMMARY_MEMORY_CACHE[key]

    cached = summary_cache.load_summary(
        root, fi.path, fi.hash, "offline", extractor_profile_hash=SUMMARY_EXTRACTOR_PROFILE
    )
    if cached:
        if key:
            _SUMMARY_MEMORY_CACHE[key] = cached
        return cached

    summary = offline.summarize(fi.path, fi.abs_path, fi.language, fi.hash)
    summary_cache.save_summary(root, summary)
    if key:
        _SUMMARY_MEMORY_CACHE[key] = summary
    return summary


def build_all_summaries(
    files: list[FileInfo],
    root: Path,
) -> dict[str, FileSummary]:
    """Build summaries for packable files. Skips ignored and binary entries defensively."""
    packable = [fi for fi in files if not (fi.ignored or fi.binary)]
    result: dict[str, FileSummary] = {}
    cache_misses: list[FileInfo] = []

    # Pass 1: check cache concurrently (I/O-bound)
    def _check_cache(fi: FileInfo) -> FileSummary | None:
        key = _memory_key(root, fi)
        if key and key in _SUMMARY_MEMORY_CACHE:
            return _SUMMARY_MEMORY_CACHE[key]
        if fi.hash is None:
            return None
        cached = summary_cache.load_summary(
            root, fi.path, fi.hash, "offline", extractor_profile_hash=SUMMARY_EXTRACTOR_PROFILE
        )
        if cached and key:
            _SUMMARY_MEMORY_CACHE[key] = cached
        return cached

    if not (root / ".agentpack" / "cache").exists():
        cache_misses = packable
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(32, os.cpu_count() or 4)
        ) as executor:
            cache_futures = {executor.submit(_check_cache, fi): fi for fi in packable}
            for fut in concurrent.futures.as_completed(cache_futures):
                fi = cache_futures[fut]
                cached = fut.result()
                if cached is not None:
                    result[fi.path] = cached
                else:
                    cache_misses.append(fi)

    if not cache_misses:
        return result

    # Pass 2: build summaries for cache misses. Threaded execution is the
    # default because public benchmark runs often execute from temporary script
    # or CLI entrypoints where spawn-based process pools can stall before any
    # worker starts. The offline summarizer is lightweight enough that reliability
    # matters more than process-level parallelism here.
    if len(cache_misses) >= 50 and os.environ.get("AGENTPACK_SUMMARY_PROCESS_POOL") == "1":
        ctx = multiprocessing.get_context("spawn")
        executor_cls = concurrent.futures.ProcessPoolExecutor
        executor_kwargs: dict = {
            "max_workers": min(os.cpu_count() or 4, 8),
            "mp_context": ctx,
        }
    else:
        executor_cls = concurrent.futures.ThreadPoolExecutor
        executor_kwargs = {"max_workers": min(32, os.cpu_count() or 4)}

    with executor_cls(**executor_kwargs) as executor:
        build_futures = {
            executor.submit(
                _build_one,
                fi.path,
                str(fi.abs_path),
                fi.language,
                fi.hash if fi.hash is not None else "",
            ): fi
            for fi in cache_misses
        }
        for fut in concurrent.futures.as_completed(build_futures):
            fi = build_futures[fut]
            try:
                summary = fut.result()
            except Exception:
                summary = offline.summarize(fi.path, fi.abs_path, fi.language, fi.hash or "")
            result[fi.path] = summary
            if fi.hash is not None:
                summary_cache.save_summary(root, summary)
                key = _memory_key(root, fi)
                if key:
                    _SUMMARY_MEMORY_CACHE[key] = summary

    return result
