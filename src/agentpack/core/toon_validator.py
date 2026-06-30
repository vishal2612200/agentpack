from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentpack.core.toon_parser import parse_toon


@dataclass(frozen=True)
class ToonValidationResult:
    ok: bool
    source: str
    root: str | None = None
    parsed_type: str | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "root": self.root,
            "parsed_type": self.parsed_type,
            "error": self.error,
            "warnings": self.warnings,
        }


def validate_toon_text(text: str, *, source: str = "<string>", require_format: bool = True) -> ToonValidationResult:
    warnings: list[str] = []
    root = _root_directive(text)
    if require_format and not _has_format_directive(text):
        return ToonValidationResult(
            ok=False,
            source=source,
            root=root,
            error="missing required @format toon directive",
        )
    if not require_format and not _has_format_directive(text):
        warnings.append("missing @format toon directive")

    try:
        payload = parse_toon(text)
    except Exception as exc:
        return ToonValidationResult(
            ok=False,
            source=source,
            root=root,
            error=str(exc),
            warnings=warnings,
        )

    return ToonValidationResult(
        ok=True,
        source=source,
        root=root,
        parsed_type=type(payload).__name__,
        warnings=warnings,
    )


def validate_toon_file(path: Path, *, require_format: bool = True) -> ToonValidationResult:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ToonValidationResult(ok=False, source=str(path), error=f"unable to read file: {exc}")
    return validate_toon_text(text, source=str(path), require_format=require_format)


def _has_format_directive(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip().lower()
        if not line:
            continue
        return line == "@format toon"
    return False


def _root_directive(text: str) -> str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("@root "):
            root = line.removeprefix("@root ").strip()
            return root or None
    return None
