from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from agentpack.core.models import SelectedFile


class SelectionEngine(str, Enum):
    """Internal selector version; V1 remains the runtime default until promotion."""

    V1 = "v1"
    V2 = "v2"
    SHADOW = "shadow"


@dataclass(frozen=True)
class CandidateEvidence:
    """Independent ownership, support, and carrier evidence for one candidate."""

    owner_strength: int
    support_strength: int
    carrier_strength: int
    codes: tuple[str, ...] = ()
    protections: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("owner_strength", "support_strength", "carrier_strength"):
            value = getattr(self, name)
            if not 0 <= value <= 3:
                raise ValueError(f"{name} must be between 0 and 3")


@dataclass(frozen=True)
class RankedCandidate:
    """Typed adapter around the legacy ranker's tuple contract."""

    path: str
    file_info: Any
    score: float
    legacy_reasons: tuple[str, ...]
    evidence: CandidateEvidence


@dataclass(frozen=True)
class RepresentationOption:
    """One budgeted rendering of a candidate file."""

    path: str
    mode: Literal["full", "diff", "symbols", "skeleton", "summary"]
    token_cost: int
    coverage_level: int
    selected_file: SelectedFile

    def __post_init__(self) -> None:
        if self.token_cost < 0:
            raise ValueError("token_cost must be non-negative")
        if not 1 <= self.coverage_level <= 4:
            raise ValueError("coverage_level must be between 1 and 4")
        if self.selected_file.path != self.path:
            raise ValueError("selected_file.path must match option path")
        if self.selected_file.include_mode != self.mode:
            raise ValueError("selected_file.include_mode must match option mode")


@dataclass(frozen=True)
class SelectionDecision:
    """Explain why a candidate or representation was not admitted."""

    path: str
    blocker_codes: tuple[str, ...]
    mode: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class SelectionConstraints:
    """Hard limits applied by the V2 allocator."""

    token_budget: int
    max_files: int
    family_caps: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if self.token_budget < 0:
            raise ValueError("token_budget must be non-negative")
        if self.max_files < 0:
            raise ValueError("max_files must be non-negative")


@dataclass(frozen=True)
class SelectionTrace:
    """Deterministic allocator telemetry used by benchmarks and explain output."""

    engine: SelectionEngine
    candidate_count: int
    explored_states: int
    elapsed_ms: float
    blocker_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class SelectionPlan:
    """Selected representations and their exact lexicographic objective."""

    selected: tuple[RepresentationOption, ...]
    rejected: tuple[SelectionDecision, ...]
    total_tokens: int
    objective: tuple[int, ...]
    trace: SelectionTrace | None = None

    def __post_init__(self) -> None:
        actual = sum(option.token_cost for option in self.selected)
        if actual != self.total_tokens:
            raise ValueError(f"total_tokens must equal selected option cost ({actual})")
        paths = [option.path for option in self.selected]
        if len(paths) != len(set(paths)):
            raise ValueError("selection plan may include each path at most once")


_REASON_EVIDENCE: tuple[tuple[str, str, int], ...] = (
    ("matched entrypoint", "entrypoint", 3),
    ("entrypoint", "entrypoint", 3),
    ("matched define", "definition", 2),
    ("matched definition", "definition", 2),
    ("filename", "filename", 1),
    ("path", "path", 1),
)


def adapt_ranked_candidate(file_info: Any, score: float, reasons: list[str]) -> RankedCandidate:
    """Convert a legacy rank tuple without changing its score or receipt reasons."""

    normalized = tuple(str(reason) for reason in reasons)
    lowered = tuple(reason.lower() for reason in normalized)
    codes: list[str] = []
    owner_strength = 0
    support_strength = 0
    carrier_strength = 0
    protections: list[str] = []

    for reason in lowered:
        for marker, code, strength in _REASON_EVIDENCE:
            if marker in reason:
                if code not in codes:
                    codes.append(code)
                owner_strength = max(owner_strength, strength)
        if any(marker in reason for marker in ("dependency", "imported by", "paired test", "related test")):
            support_strength = max(support_strength, 2)
            if "dependency" not in codes:
                codes.append("dependency")
        if "matched call" in reason or "call site" in reason:
            carrier_strength = max(carrier_strength, 2)
            if "call_site" not in codes:
                codes.append("call_site")
        if "content" in reason or "phrase" in reason or "keyword" in reason:
            carrier_strength = max(carrier_strength, 1)
            if "content" not in codes:
                codes.append("content")
        for marker, protection in (
            ("changed", "changed"),
            ("memory", "memory_confirmed"),
            ("generated", "generated"),
            ("release metadata", "release_metadata"),
        ):
            if marker in reason and protection not in protections:
                protections.append(protection)

    return RankedCandidate(
        path=str(file_info.path),
        file_info=file_info,
        score=float(score),
        legacy_reasons=normalized,
        evidence=CandidateEvidence(
            owner_strength=owner_strength,
            support_strength=support_strength,
            carrier_strength=carrier_strength,
            codes=tuple(codes),
            protections=tuple(protections),
        ),
    )
