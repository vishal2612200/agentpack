"""Aggregate and compare local semantic-fixture JSONL benchmark runs."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


METRICS = (
    "relationship_precision",
    "relationship_recall",
    "source_line_grounding",
    "path_correctness",
    "first_correct_file_rate",
    "routing_recall",
    "incremental_rebuild_cost",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def aggregate(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("fixture") or "unknown")].append(record)
    result: dict[str, dict[str, Any]] = {}
    for fixture, rows in sorted(grouped.items()):
        versions = sorted({row.get("fixture_version") for row in rows})
        profiles = sorted({row.get("extractor_profile_hash") for row in rows})
        result[fixture] = {
            "fixture_version": versions[0] if len(versions) == 1 else None,
            "extractor_profile_hash": profiles[0] if len(profiles) == 1 else None,
            "ground_truth_status": "available" if all(row.get("ground_truth_status") == "available" for row in rows) else "unavailable",
            **{
                metric: _mean(row.get(metric) for row in rows)
                for metric in METRICS
            },
        }
    return result


def compare(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    before_rows = aggregate(before)
    after_rows = aggregate(after)
    output: dict[str, Any] = {}
    for fixture in sorted(set(before_rows) | set(after_rows)):
        old = before_rows.get(fixture)
        new = after_rows.get(fixture)
        if old is None or new is None:
            output[fixture] = {"status": "missing_run"}
            continue
        mismatches = [
            key for key in ("fixture_version", "extractor_profile_hash")
            if old.get(key) != new.get(key)
        ]
        if mismatches:
            output[fixture] = {"status": "metadata_mismatch", "keys": mismatches}
            continue
        output[fixture] = {
            "status": "comparable",
            "fixture_version": new.get("fixture_version"),
            "extractor_profile_hash": new.get("extractor_profile_hash"),
            "delta": {
                metric: _delta(old.get(metric), new.get(metric))
                for metric in METRICS
            },
        }
    return output


def _mean(values) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


def _delta(before: Any, after: Any) -> float | None:
    return round(float(after) - float(before), 6) if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m tests.benchmarks.semantic_compare BEFORE.jsonl AFTER.jsonl")
    print(json.dumps(compare(load_jsonl(Path(sys.argv[1])), load_jsonl(Path(sys.argv[2]))), indent=2, sort_keys=True))
