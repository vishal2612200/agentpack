"""Produce a Markdown before/after delta table from two report.py --json snapshots.

Used at the end of every phase in the language-coverage plan: capture a
`report.py --json` snapshot before the phase's changes land, capture another
after, and diff them here. The output is the "prove we improved" artifact
saved to benchmarks/results/phase-N-<name>.md.

Usage:
    python -m tests.benchmarks.report --json > before.json
    # ... land phase changes ...
    python -m tests.benchmarks.report --json > after.json
    python -m tests.benchmarks.compare before.json after.json > delta.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

METRICS = [
    ("avg_recall", "Recall", "pp"),
    ("avg_token_precision", "Token precision", "pp"),
    ("reason_graph_precision", "reason graph", "pp"),
    ("reason_content_precision", "reason content", "pp"),
    ("reason_symbol_precision", "reason symbol", "pp"),
    ("median_wall_seconds", "Median wall (s)", "s"),
]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: python -m tests.benchmarks.compare <before.json> <after.json>",
            file=sys.stderr,
        )
        return 2

    before = json.loads(Path(argv[1]).read_text())
    after = json.loads(Path(argv[2]).read_text())
    langs = sorted(set(before) | set(after))

    print(f"# Benchmark comparison: `{argv[1]}` → `{argv[2]}`\n")

    recall_deltas: list[float] = []

    for lang in langs:
        b = before.get(lang)
        a = after.get(lang)
        print(f"## {lang}\n")

        if b is None:
            print("_new language, not present in before snapshot_\n")
        if a is None:
            print("_missing from after snapshot — check the run_\n")
            continue
        if not a.get("ok", True):
            print(f"_after run failed: {a.get('error')}_\n")
            continue
        if b is not None and not b.get("ok", True):
            print(f"_before run failed: {b.get('error')}_\n")

        metadata_mismatches = []
        for key in ("fixture_version", "extractor_profile_hash"):
            before_value = b.get(key) if b else None
            after_value = a.get(key)
            if before_value is not None and after_value is not None and before_value != after_value:
                metadata_mismatches.append(f"{key}: {before_value} -> {after_value}")
        if metadata_mismatches:
            print("_comparison unavailable because benchmark metadata changed: " + "; ".join(metadata_mismatches) + "_\n")
            continue

        repo = a.get("repo", b.get("repo") if b else "?")
        print(f"Repo: `{repo}`\n")

        cases_b = b.get("cases") if b else None
        cases_a = a.get("cases")
        print(f"Cases: {cases_b if cases_b is not None else '—'} → {cases_a}\n")

        print("| Metric | Before | After | Δ |")
        print("|---|---|---|---|")
        for key, label, unit in METRICS:
            av = a.get(key)
            bv = b.get(key) if b else None
            if av is None:
                continue
            if bv is None:
                print(f"| {label} | — | {_fmt(av, unit)} | new |")
                continue
            delta = av - bv
            arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "=")
            print(f"| {label} | {_fmt(bv, unit)} | {_fmt(av, unit)} | {arrow} {_fmt(abs(delta), unit)} |")
            if key == "avg_recall":
                recall_deltas.append(delta)
        print()

    if recall_deltas:
        mean_delta = sum(recall_deltas) / len(recall_deltas)
        print("---\n")
        print(
            f"**Mean recall delta across {len(recall_deltas)} language(s) with "
            f"before+after data: {mean_delta * 100:+.2f} pp**"
        )
        regressed = [
            lang for lang, d in zip(
                (language for language in langs if before.get(language) and after.get(language, {}).get("ok", True)),
                recall_deltas,
            )
            if d < -0.01
        ]
        if regressed:
            print(f"\n**Regressed >1pp:** {', '.join(regressed)}")

    return 0


def _fmt(value: float, unit: str) -> str:
    if unit == "pp":
        return f"{value * 100:.1f}%"
    return f"{value:.2f}{unit}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
