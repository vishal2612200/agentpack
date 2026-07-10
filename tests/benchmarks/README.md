# Continuous ranking-quality benchmarks

Per-language pytest suite that measures AgentPack's file-ranking quality
against real public-repo commits and asserts it stays above a threshold.
Ship an improvement → raise the threshold → the new floor is locked in.

## How it works

Each `test_<lang>.py` runs the public-repo benchmark on a single repo,
writes a JSONL of per-case metrics, aggregates it with `_harness.py`, and
asserts against the mins in `thresholds.toml`. The harness caches repo
clones under `.agentpack/public-repos/`, so the second run of each test
skips the clone.

Ground-truth methodology (from the AgentPack public suite): for each
sampled commit, check out its parent, pack the repo using the commit
subject as the task, and compare against the files that commit actually
changed. No hand labeling.

## Running

```bash
# All languages (slow — ~5–15 min per repo)
pytest tests/benchmarks/ --run-benchmarks -s

# One language
pytest tests/benchmarks/test_php.py --run-benchmarks -sv

# Env-var alternative
AGENTPACK_RUN_BENCHMARKS=1 pytest tests/benchmarks/

# Report mode — current numbers vs thresholds, no assertions
python -m tests.benchmarks.report            # all
python -m tests.benchmarks.report php ruby   # subset
```

Tests are **skipped by default** on `pytest tests/` — the runtime is too
long for every-commit CI. Run them:
- Manually before opening a PR that touches ranking or extraction.
- On a nightly CI job with `--run-benchmarks`.
- In the report shape when deciding whether a change lifted metrics enough
  to raise the threshold.

## Metrics reported per language

| Metric | What it means |
|---|---|
| `cases` | Number of commit-samples scored |
| `avg_recall` | Fraction of expected files selected, averaged over cases |
| `avg_token_precision` | Fraction of selected tokens that were in expected files |
| `reason_graph_precision` | Precision of files selected via import-graph signal — the main tree-sitter lever for previously-dark languages |
| `reason_content_precision` | Precision via keyword-vs-content signal — lifts when symbols enter the keyword pool |
| `reason_symbol_precision` | Precision via structural symbol signal |
| `median_wall_seconds` | Per-case wall time (guards against runaway regressions) |

Family-precision metrics are meaned only over cases where that signal
actually fired. A `0.0` means the signal never contributed a selection,
which is legitimate data (e.g. PHP's `symbol` family before we deepen the
query) — not a bug.

## Baselines and sampling noise

Each run of `sample_history = N` picks a slightly different N commits
based on recent repo state, so back-to-back runs vary. Typical noise
window I've observed:
- Recall: ±1–2 pp
- Token precision: ±0.5–1 pp
- Family precision: ±2–3 pp (thinner denominators, higher variance)

`thresholds.toml` sets each min ~2–3 pp below the observed run so
sampling variance alone doesn't fail CI. For tighter guards, freeze the
sample with `agentpack benchmark --write-public-repos-lock` and commit
the lock file.

## Workflow when you ship an improvement

1. Run `python -m tests.benchmarks.report <lang>` and record the numbers.
2. Merge your change.
3. Re-run the report. If metrics improved and are stable across 2–3 runs,
   raise the thresholds in `thresholds.toml` to ~2 pp below the new
   observed value.
4. Commit the threshold bump in the same PR as the improvement. Now the
   new floor is locked; a regression will fail the test.

## Current threshold status

Baselines were established during the tree-sitter Phase 1 shipment.
`thresholds.toml` has header comments per language documenting the
observed numbers those thresholds were set from. Java and Ruby entries
are commented out until we establish their baselines by running
`report.py java` and `report.py ruby` once and pasting the numbers.

## Files

| File | Purpose |
|---|---|
| `_harness.py` | Runs benchmark subprocess, parses JSONL, aggregates |
| `conftest.py` | Adds `--run-benchmarks` opt-in flag + `thresholds` fixture |
| `thresholds.toml` | Per-language mins — bump when metrics improve |
| `report.py` | Non-assertion runner; prints table with threshold flags |
| `test_<lang>.py` | One test per language, all with the same shape |
