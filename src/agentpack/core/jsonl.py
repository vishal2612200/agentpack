"""Small bounded JSONL writer shared by local telemetry surfaces."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]

_LOCKS: dict[Path, RLock] = {}
_LOCKS_GUARD = RLock()


def append_record(path: Path, record: dict[str, Any], *, max_records: int = 2000) -> None:
    """Append one record and occasionally trim old records atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(path.resolve(), RLock())
    with lock:
        lock_handle = None
        try:
            if fcntl is not None:
                lock_path = path.with_name(path.name + ".lock")
                lock_handle = lock_path.open("a+", encoding="utf-8")
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
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
        finally:
            if lock_handle is not None:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
