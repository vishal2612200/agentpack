"""Small bounded JSONL writer shared by local telemetry surfaces."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX has no msvcrt
    msvcrt = None  # type: ignore[assignment]

_LOCKS: dict[Path, RLock] = {}
_LOCKS_GUARD = RLock()


@contextmanager
def _process_lock(path: Path) -> Iterator[None]:
    """Coordinate append/retention across processes on POSIX and Windows."""
    lock_path = path.with_name(path.name + ".lock")
    handle = None
    try:
        if fcntl is not None:
            handle = lock_path.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:
            handle = lock_path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if handle is not None:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            handle.close()


def append_record(path: Path, record: dict[str, Any], *, max_records: int = 2000) -> None:
    """Append one record and occasionally trim old records atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(path.resolve(), RLock())
    with lock:
        with _process_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            if max_records <= 0:
                return
            try:
                if path.stat().st_size < max_records * 512:
                    return
                lines = path.read_text(encoding="utf-8").splitlines()
                if len(lines) <= max_records:
                    return
                fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", text=True)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write("\n".join(lines[-max_records:]) + "\n")
                    os.replace(temporary, path)
                except Exception:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                    raise
            except OSError:
                # Telemetry must never break the MCP request.
                return
