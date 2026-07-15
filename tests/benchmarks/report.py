"""Print current metrics vs. thresholds for every configured language.

Not a test — a report. Use it to decide when to raise thresholds after
shipping an improvement, or to capture before/after snapshots for
tests/benchmarks/compare.py.

Usage:
    python -m tests.benchmarks.report                     # all languages
    python -m tests.benchmarks.report php ruby java        # subset
    python -m tests.benchmarks.report --json > out.json    # machine-readable
    python -m tests.benchmarks.report --json php > out.json

Optional env: AGENTPACK_DISABLE_TREE_SITTER=1 to run the "before" side of
an A/B locally without uninstalling the [tree-sitter] extra.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

from tests.benchmarks._harness import run_public_repo_benchmark


ROW = "{lang:10} {cases:>5} {recall:>8} {tp:>8} {graph:>8} {content:>8} {symbol:>8} {relp:>8} {relr:>8} {first:>8} {route:>8} {wall:>8}"


def _metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main(argv: list[str]) -> int:
    args = argv[1:]
    as_json = "--json" in args
    targets_arg = [a for a in args if a != "--json"]

    thresholds = tomllib.loads(
        (Path(__file__).parent / "thresholds.toml").read_text()
    )
    targets = targets_arg if targets_arg else list(thresholds.keys())
    unknown = [t for t in targets if t not in thresholds]
    if unknown:
        print(f"unknown language(s): {unknown}", file=sys.stderr)
        return 2

    if as_json:
        return _run_json(targets, thresholds)
    return _run_table(targets, thresholds)


def _run_json(targets: list[str], thresholds: dict) -> int:
    results: dict[str, dict] = {}
    exit_code = 0
    for lang in targets:
        cfg = thresholds[lang]
        try:
            m = run_public_repo_benchmark(cfg["repo"])
            results[lang] = {"repo": cfg["repo"], "ok": True, **m.as_dict()}
        except Exception as exc:
            results[lang] = {"repo": cfg["repo"], "ok": False, "error": str(exc)}
            exit_code = 1
    print(json.dumps(results, indent=2))
    return exit_code


def _run_table(targets: list[str], thresholds: dict) -> int:
    print(ROW.format(
        lang="lang", cases="cases", recall="recall", tp="tokprec",
        graph="graph", content="content", symbol="symbol", first="first", route="route", wall="wall(s)",
        relp="rel-p", relr="rel-r",
    ))
    print("-" * 76)

    exit_code = 0
    for lang in targets:
        cfg = thresholds[lang]
        try:
            m = run_public_repo_benchmark(cfg["repo"])
        except Exception as exc:  # keep going on individual failures
            print(f"{lang:10} FAIL — {exc}")
            exit_code = 1
            continue
        # Mark cells red-ish (surrounded by !) when below threshold.
        def flag(actual: float, thresh: float, fmt: str = ".3f") -> str:
            s = f"{actual:{fmt}}"
            return f"!{s}!" if actual < thresh else f" {s} "
        wall_flag = (
            f"!{m.median_wall_seconds:.2f}!"
            if m.median_wall_seconds > cfg["max_median_wall_seconds"]
            else f" {m.median_wall_seconds:.2f} "
        )
        print(ROW.format(
            lang=lang,
            cases=str(m.cases),
            recall=flag(m.avg_recall, cfg["min_recall"]),
            tp=flag(m.avg_token_precision, cfg["min_token_precision"]),
            graph=flag(m.reason_graph_precision, cfg["min_reason_graph_precision"]),
            content=flag(m.reason_content_precision, cfg["min_reason_content_precision"]),
            symbol=f" {m.reason_symbol_precision:.3f} ",
            relp=f" {_metric(m.relationship_precision)} ",
            relr=f" {_metric(m.relationship_recall)} ",
            first=f" {m.first_correct_file_rate:.3f} ",
            route=f" {m.routing_recall:.3f} ",
            wall=wall_flag,
        ))
    print()
    print("cells wrapped in !...! are below the threshold in thresholds.toml")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
