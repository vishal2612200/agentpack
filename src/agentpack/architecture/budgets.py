"""Deterministic architecture snapshot metrics and budget comparison."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from agentpack.architecture.models import ArchitectureSnapshot


def snapshot_metrics(snapshot: ArchitectureSnapshot) -> dict[str, Any]:
    """Return source-free metrics suitable for baselines and CI artifacts."""
    entities = snapshot.entities
    edges = snapshot.edges
    entity_counts = Counter(entity.entity_type for entity in entities)
    edge_counts = Counter(edge.edge_type for edge in edges)
    confidence_counts = Counter(
        [entity.confidence_tier for entity in entities]
        + [edge.confidence_tier for edge in edges]
    )
    unresolved = entity_counts.get("unresolved", 0)
    fallback = sum(1 for item in entities if item.confidence_tier in {"best_effort", "file_level", "unavailable"})
    fallback += sum(1 for item in edges if item.confidence_tier in {"best_effort", "file_level", "unavailable"})
    total_records = len(entities) + len(edges)
    payload = snapshot.model_dump(mode="json")
    payload.pop("file_hashes", None)
    return {
        "schema_version": 1,
        "commit_sha": snapshot.commit_sha,
        "entity_count": len(entities),
        "edge_count": len(edges),
        "artifact_bytes": len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")),
        "build_seconds": _build_seconds(snapshot),
        "entity_counts": dict(sorted(entity_counts.items())),
        "edge_counts": dict(sorted(edge_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "unresolved_ratio": round(unresolved / max(1, len(entities)), 6),
        "fallback_ratio": round(fallback / max(1, total_records), 6),
        "duplicate_entity_count": len(entities) - len({entity.entity_key for entity in entities}),
        "duplicate_edge_count": len(edges) - len({edge.edge_key for edge in edges}),
    }


def compare_budget(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    max_growth_pct: float = 25.0,
    max_quality_regression_pct: float = 5.0,
    max_build_time_multiplier: float = 2.0,
) -> dict[str, Any]:
    """Compare current metrics with accepted baseline without blocking by default."""
    if not baseline:
        return {"status": "unbaselined", "warnings": [], "deltas": {}}
    growth_fields = ("entity_count", "edge_count", "artifact_bytes")
    deltas: dict[str, float] = {}
    warnings: list[str] = []
    for field in growth_fields:
        before = float(baseline.get(field) or 0)
        after = float(current.get(field) or 0)
        delta = _percent_delta(before, after)
        deltas[field] = delta
        if delta > max_growth_pct:
            warnings.append(f"{field} grew {delta:.1f}% (limit {max_growth_pct:.1f}%)")
    for field in ("unresolved_ratio", "fallback_ratio"):
        before = float(baseline.get(field) or 0)
        after = float(current.get(field) or 0)
        delta_points = (after - before) * 100.0
        deltas[field] = round(delta_points, 4)
        if delta_points > max_quality_regression_pct:
            warnings.append(f"{field} worsened {delta_points:.2f} percentage points (limit {max_quality_regression_pct:.2f})")
    before_seconds = float(baseline.get("build_seconds") or 0)
    after_seconds = float(current.get("build_seconds") or 0)
    multiplier = after_seconds / before_seconds if before_seconds > 0 else 0.0
    deltas["build_time_multiplier"] = round(multiplier, 4)
    if before_seconds > 0 and multiplier > max_build_time_multiplier:
        warnings.append(f"build_seconds reached {multiplier:.2f}x baseline (limit {max_build_time_multiplier:.2f}x)")
    return {
        "status": "warn" if warnings else "pass",
        "warnings": warnings,
        "deltas": deltas,
        "thresholds": {
            "max_growth_pct": max_growth_pct,
            "max_quality_regression_pct": max_quality_regression_pct,
            "max_build_time_multiplier": max_build_time_multiplier,
        },
    }


def _build_seconds(snapshot: ArchitectureSnapshot) -> float:
    stats = snapshot.cache_stats
    return round(float(stats.get("cold_build_seconds") or stats.get("incremental_build_seconds") or 0.0), 6)


def _percent_delta(before: float, after: float) -> float:
    if before <= 0:
        return 0.0 if after <= 0 else 100.0
    return round((after - before) / before * 100.0, 4)
