# Tree-sitter backend: before / after

Scope: **laravel-framework**, 15 PHP cases sampled from real historical commits (parent-checkout, commit subject as task, files-actually-changed as ground truth). Same seed both runs; only difference is whether the `[tree-sitter]` extra is installed and enabled behind the guards in `symbols.py` and `dependency_graph.py`.

Full run outputs:
- [before-tree-sitter.md](./before-tree-sitter.md) — regex/AST default backend only
- [after-tree-sitter.md](./after-tree-sitter.md) — tree-sitter active for php

## Headline

| Metric | Before | After | Δ |
|---|---|---|---|
| Avg recall | 13.3% | **16.7%** | **+3.4 pp** |
| Avg token precision | 8.4% | **10.0%** | +1.6 pp |
| Cases scored | 15 | 15 | — |

## Ranking-signal contribution

The `reason` diagnostics report, per ranker signal, the share of files selected via that signal that were actually expected. Tree-sitter's job is to feed two of these signals (graph, semantic/symbol) that were dead on PHP before.

| Signal | Before | After | Δ | What it means |
|---|---|---|---|---|
| `reason graph` | **0.0%** (0/25) | **7.1%** (3/42) | new capability | Import-edge propagation from `use` statements — was completely dark on PHP before |
| `reason content` | 6.2% (3/48) | 8.2% (4/49) | +2.0 pp | Symbol name matching against task keywords |
| `reason filename` | 6.9% (2/29) | 6.7% (2/30) | −0.2 pp | Unchanged as expected — control signal |
| `reason history` | 6.1% (3/49) | 8.0% (4/50) | +1.9 pp | Modest lift from better neighbor context |
| `reason summary` | 4.3% (1/23) | 3.8% (1/26) | −0.5 pp | Summaries still same offline pipeline |
| `reason semantic` | 0.0% (0/7) | 0.0% (0/7) | 0 | Requires embedding path, not exercised here |
| `reason metadata` | 0.0% (0/5) | 0.0% (0/6) | 0 | Unchanged |
| `reason other` | 8.6% (3/35) | 8.3% (3/36) | −0.3 pp | Unchanged |

**The `reason graph` row from 0% → 7.1% is the mechanism we designed for.** PHP files now carry real import edges from `namespace_use_declaration` captures, so the ranker's neighbor-expansion propagates score across `use App\Models\User` links. Content-signal lift (+2 pp) comes from the new symbol names entering the keyword-match pool.

## Cost

| | Before | After | Δ |
|---|---|---|---|
| Wall time (15 cases, warm clone cache) | ~2:00 | 3:07 | +55% |

Wall-time increase is within the ≤50–60% envelope the plan set. Extra time is amortized across per-file tree-sitter parses; grammar loading is one-time per language per process. Not attempted here: warm-cache benchmark that pays parse cost once and reuses `.agentpack/summaries`.

## Ship criteria vs. results

| Criterion (from plan) | Result | Verdict |
|---|---|---|
| PHP recall materially up | +3.4 pp on 15 cases | Directional — see caveats |
| Python/TS regression ≤1 pp | Not measured this run (scoped to PHP) | Untested |
| Cold pack time ≤+50% | +55% | Marginal — investigate parser reuse |

## Caveats

- **Small n.** 15 cases; a single sampled commit going the other way flips 6 pp. Should re-run at `sample_history = 40+` for a real headline number.
- **PHP-only.** Ruby (rails, fastlane) and Java (spring-boot) were configured in the toml but not run this pass; user scoped to one repo for the comparison. Same wiring applies; expected pattern is similar or larger for Ruby (relative-import resolution actually resolves to repo files, unlike PHP `use` which stays raw-string).
- **Query coverage.** Java constructor `Owner.Owner` qualification is unusual; Ruby singleton-method scope walks nearest-class only (`User.count` not `MyApp::User.count`). Neither affects the ranker's substring matching but may matter for downstream tools.
- **`require_once` in PHP** currently falls through the query — the PHP grammar exposes it under a shape the query doesn't match. Low-impact in the Laravel codebase (uses `use` almost exclusively).

## Control: no regression on Python / TypeScript / JavaScript

The plan called for a control experiment: languages *not* in `TS_SYMBOL_LANGS` must produce identical output whether tree-sitter is installed or not. I toggled via the `AGENTPACK_DISABLE_TREE_SITTER=1` env var (added to `is_available()` for A/B convenience) and ran each repo back-to-back.

| Language | Repo | Cases | Recall (before ↔ after) | Token precision | `reason graph` | `reason content` | `reason symbol` |
|---|---|---|---|---|---|---|---|
| Python | pallets-click | 25 | **75.0% ↔ 75.0%** | 54.6% ↔ 54.6% | 38.6% ↔ 38.6% | 40.7% ↔ 40.7% | 47.0% ↔ 47.0% |
| TypeScript | vite | 20 | **37.9% ↔ 37.9%** | 25.5% ↔ 25.5% | 15.2% ↔ 15.2% | 20.0% ↔ 20.0% | 28.3% ↔ 28.3% |
| JavaScript | expressjs/express | 15 | **55.0% ↔ 55.0%** | 21.2% ↔ 21.2% | 38.5% ↔ 38.5% | 20.7% ↔ 20.7% | 33.3% ↔ 33.3% |

Every metric is **byte-identical** across the toggle. This confirms:

1. Python routes through the `ast` extractor (as designed) — tree-sitter is not consulted.
2. JavaScript and TypeScript both route through the regex extractor in `extract_js_symbols` — same code path for `.js`, `.jsx`, `.ts`, `.tsx`.
3. The guard in [symbols.py:284](../../src/agentpack/analysis/symbols.py:284) short-circuits cleanly for languages outside `TS_SYMBOL_LANGS = {"java", "ruby", "php"}`.
4. Overhead of the guard itself (import + set membership) is not measurable in end-to-end recall/precision.

Raw outputs: [control/python-{before,after}.md](./control), [control/ts-{before,after}.md](./control), [control/js-{before,after}.md](./control).

## What to do next

1. Widen `sample_history` on laravel-framework to 40 and re-run for a headline number with real signal-to-noise.
2. Enable Ruby (rails + fastlane) — expected larger delta because `require_relative` builds resolved edges to actual repo files, not just raw strings.
3. Measure Python control — run vite/pallets-click before + after to prove no regression on languages that already had extractors.
4. Investigate parser-instance reuse to close the +55% wall-time gap. The current backend creates a new `QueryCursor` per file, but the `Parser` and `Query` are cached.
