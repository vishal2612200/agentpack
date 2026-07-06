# Benchmark Learnings

This page records the engineering lessons from the public-suite precision push.
It is a decision log, not a marketing table.

## Latest Public Evidence

The current public release evidence table is
[`benchmarks/results/2026-06-25-public.md`](https://github.com/vishal2612200/agentpack/blob/main/benchmarks/results/2026-06-25-public.md):
107 scored public cases at 65.7% recall and 51.4% token precision. Treat it as
scoped file-selection evidence, not broad proof of downstream agent success.

## 2026-07-06 Precision Push Decision Log

This section records the July release-gate precision investigation. It is
intentionally detailed because the main outcome was not a single magic weight.
The durable learning was how to avoid benchmark gaming while reducing packed
token waste.

The evidence below comes from local release-gate JSONL artifacts and a scoped
origin replay worktree. The final code change was committed as
`d1b469e fix(benchmark): improve release precision and memory gate`. Treat the
`/tmp/...jsonl` paths as audit breadcrumbs from the local run, not as published
benchmark artifacts. Before using these numbers in release copy, publish a
dated `benchmarks/results/YYYY-MM-DD-public.md` file from a fresh run.

### Latest Validation Snapshot

After the scoped replay below, a bounded owner-over-carrier rescue was added to
the marginal replacement path. The rule is intentionally narrow: a source
candidate with filename/path evidence plus symbol/definition ownership may
replace a broad source carrier selected mostly through content/call evidence,
but not when the incumbent has stronger owner evidence or the score gap is too
large.

Release-worktree command:

```bash
PYTHONPATH=src python -m agentpack.cli benchmark \
  --release-gate \
  --no-public-table \
  --benchmark-jsonl /tmp/agentpack-release-0.3.39-owner-rescue-fixed-full.jsonl
```

Release-worktree result:

| Metric | Before owner rescue | After owner rescue |
|---|---:|---:|
| Scored cases | 107 | 107 |
| Avg recall | 64.6% | 67.2% |
| Avg token precision | 48.6% | 50.6% |
| Packed tokens | 37,093 | 35,877 |
| TypeScript recall | 34.2% | 47.9% |
| TypeScript token precision | 24.8% | 33.0% |
| TypeScript monorepo recall | 66.7% | 66.7% |
| TypeScript monorepo token precision | 38.2% | 38.2% |

The same patch was checked in the main checkout default sampled run:

```bash
PYTHONPATH=src python -m agentpack.cli benchmark \
  --release-gate \
  --no-public-table \
  --benchmark-jsonl /tmp/agentpack-main-owner-rescue-fixed-official.jsonl
```

Main-checkout result: 94 scored cases, 65.8% recall, 50.1% average token
precision, and 31,685 packed tokens.

This is still a narrow pass, not a reason to loosen the stop rules. The useful
learning is that action ownership can be more important than raw local content
density when two compressed source candidates compete for the same marginal
slot.

### Prior Scoped Replay Snapshot

Final scoped replay command:

```bash
PYTHONPATH=src python -m agentpack.cli benchmark \
  --release-gate \
  --no-public-table \
  --public-repos-file /tmp/agentpack-public-lock-current-refs.toml \
  --public-repos-cache /Users/vishal/Documents/agentpack/.agentpack/public-repos \
  --benchmark-jsonl /tmp/agentpack-full-origin-audit-final.jsonl
```

Final scored release-gate result from
`/tmp/agentpack-full-origin-audit-final.jsonl`:

| Metric | Result |
|---|---:|
| Scored cases | 94 |
| Avg recall | 65.8% |
| Avg token precision | 50.1% |
| Aggregate token precision | 44.1% |
| Packed tokens | 31,714 |
| Expected tokens reconstructed from rounded JSONL | about 13,985 |
| Strict noise reconstructed from rounded JSONL | about 17,729 |

The release gate uses average token precision. Aggregate token precision is
still useful because it shows whether a few large packs are carrying most of the
waste.

The final fixed-selected-file oracle still showed remaining headroom:

| Diagnostic | Result |
|---|---:|
| Baseline aggregate token precision | 44.1% |
| Oracle projected aggregate token precision | 58.7% |
| Oracle gain | +14.6pp |
| Tokens removable from non-expected selected files | 7,878 |
| Expected token loss | 0 |
| Selected-file-set violations | 0 |

This means the final implementation improved the gate, but it did not exhaust
the theoretical opportunity. Future work should still focus on reducing token
mass inside already selected files, not on expanding the selected file set.

The seeded memory gate was also run:

```bash
PYTHONPATH=src python -m agentpack.cli eval --memory-ab --prove-targets
```

The important property is not that memory improves every benchmark case. It is
that `--prove-targets` now fails when no memory-confirmed selected files are
tested, and it fails if memory-confirmed context is compacted away. A public
benchmark run with `0` memory-confirmed markers does not reject memory learning;
it only proves that the benchmark did not exercise that path.

### Run Chronology

These are the key full-run checkpoints from the July loop. Average token
precision and recall are directly comparable only within the same scored case
set and benchmark configuration, so the lesson column matters as much as the
headline number.

| Run / artifact | Recall | Avg token precision | Packed tokens | Aggregate TP | Decision |
|---|---:|---:|---:|---:|---|
| `/tmp/agentpack-full-benchmark-noise-buckets.jsonl` | 64.0% | 41.7% | 52,980 | 38.3% | Baseline failed precision. Added strict/plausible/true-noise diagnostics before tuning. |
| `/tmp/agentpack-full-benchmark-replacement-token-neutral.jsonl` | 64.2% | 42.0% | 52,725 | 38.4% | Rejected. A +0.3pp to +0.4pp move was too small and earlier true-noise auditing worsened. |
| `/tmp/agentpack-full-benchmark-probabilistic.jsonl` | 43.5% | 35.3% | 38,755 | 33.9% | Rejected. Probabilistic knapsack optimized the wrong unit and damaged recall. |
| `/tmp/agentpack-full-benchmark-cga.jsonl` | 57.0% | 37.3% | 59,450 | 38.2% | Rejected. The nature-inspired graph formula did not clear recall or precision. |
| `/tmp/agentpack-full-benchmark-cga-optimized.jsonl` | 53.6% | 38.0% | 52,179 | 35.1% | Rejected. Optimization improved one dimension but still failed the gate. |
| `/tmp/agentpack-full-benchmark-targeted-atoms.jsonl` | 67.7% | 35.1% | 71,326 | 30.8% | Rejected. Atom rescue expanded the selected file set and bought recall with too much noise. |
| `/tmp/agentpack-full-benchmark-targeted-atoms-postselect.jsonl` | 62.0% | 40.2% | 54,914 | 37.5% | Rejected as production behavior. Fixed selected-set projection was safer but barely moved precision. |
| `/tmp/agentpack-full-benchmark-oracle-excerpt-ceiling.jsonl` | 62.0% | 40.0% | 57,512 | 37.3% | Diagnostic only. Oracle compression of non-expected selected files showed +24.8pp aggregate TP headroom. |
| `/tmp/agentpack-full-benchmark-label-free-tiered-excerpts.jsonl` | 62.0% | 40.0% | 57,512 | 37.3% | Rejected. Removed 3,332 tokens but lost 1,510 expected tokens, for -0.5pp aggregate TP. |
| `/tmp/agentpack-full-benchmark-risk-aware-excerpt-variants-corrected.jsonl` | 62.0% | 40.0% | 57,512 | 37.3% | Rejected. Risk-aware compression was safer but too small: +0.06pp aggregate TP. |
| `/tmp/agentpack-full-benchmark-guarded-strong-carrier-excerpts.jsonl` | 62.0% | 40.0% | 57,512 | 37.3% | Kept as diagnostic. +1.63pp projected aggregate TP, but not enough alone. |
| `/tmp/agentpack-full-benchmark-ast-checkpoint-memory-excerpts.jsonl` | 62.0% | 40.0% | 57,512 | 37.3% | Kept as diagnostic. +1.33pp projected aggregate TP. Public run had 0 memory markers, so it did not test live memory. |
| `/tmp/agentpack-full-benchmark-mav-span.jsonl` | 62.0% | 40.0% | 57,512 | 37.3% | Kept as diagnostic. +1.63pp projected aggregate TP with only 73 expected-token loss. |
| `/tmp/agentpack-full-neutral-mav.jsonl` | 65.8% | 45.9% | 35,628 | 39.3% | Useful intermediate. Better recall and lower token mass, but still below the official precision gate. |
| `/tmp/agentpack-full-production-compaction.jsonl` | 65.8% | 48.4% | 31,057 | 42.4% | Useful but below gate. Proved production compaction direction was moving the right metric. |
| `/tmp/agentpack-full-production-compaction-guarded.jsonl` | 65.8% | 48.8% | 32,177 | 43.2% | Useful but below gate. Guarding recovered some expected-token loss. |
| `/tmp/agentpack-full-production-compaction-rank1guard.jsonl` | 65.8% | 49.9% | 31,776 | 44.0% | Near miss. Rank guard helped but still did not clear average token precision. |
| `/tmp/agentpack-full-production-compaction-config.jsonl` | 65.8% | 50.1% | 31,714 | 44.1% | Accepted. Same selected-file discipline, lower packed tokens, precision gate passed. |
| `/tmp/agentpack-full-origin-audit-final.jsonl` | 65.8% | 50.1% | 31,714 | 44.1% | Final scoped origin replay. Used for commit validation. |

### What Worked

The working implementation was a compaction and evidence-preservation change,
not a broad selector boost.

| Principle | Evidence | Implementation shape |
|---|---|---|
| Keep the selected file set stable. | Atom rescue raised recall to 67.7% but dropped average token precision to 35.1% with 71,326 packed tokens. | Production work focused on shrinking selected payloads rather than adding files. |
| Separate action owners from evidence carriers. | Strong-looking files were often not the place an edit would happen. Guarded strong-carrier projection showed positive projected gain before productionization. | Action owners are protected; carrier files can be reduced to source-aware spans. |
| Optimize marginal action value per token, not file score alone. | MAV span diagnostics had a better expected-loss profile than broader tier compression: +1.63pp projected aggregate TP with 73 expected-token loss in the 57,512-token baseline. | Span extraction keeps headers/imports and high-density evidence spans instead of whole skeletons. |
| Treat memory as an owner signal when it is actually present. | Public benchmark runs had 0 memory markers, so they could not validate memory learning. Seeded `eval --memory-ab --prove-targets` did exercise memory-confirmed selection. | Memory-confirmed files are protected from compaction, and the eval gate fails if no memory signal is tested. |
| Use diagnostics before behavior changes. | The oracle showed +24.8pp headroom on one baseline and +14.6pp remaining after final compaction. | Oracle, tiered, AST checkpoint, MAV, and neutral-MAV projections remain benchmark diagnostics. |
| Measure true removal, not only precision headline. | The first replacement attempt looked slightly better by strict TP but worsened true-noise accounting in the earlier audit. | Benchmark diagnostics now expose strict noise, plausible usefulness, and adjusted precision to prevent false wins. |

The best short description of the accepted direction is:

```text
Preserve the lowest-action path through the repo graph.
Spend tokens on action-owner evidence.
Compress files that only carry weak or localized evidence.
Do not expand the selected-file set to hide precision loss.
```

This is close to a least-action or minimum-energy principle from physics, but
the implementation is not mystical. The production behavior is an engineering
hybrid:

```text
source-aware structure
+ selected-file-set stability
+ evidence-carrier vs action-owner classification
+ marginal value per token discipline
+ memory-confirmed owner protection
```

Lexical matches still exist because code evidence begins with names, paths,
symbols, tests, imports, comments, and summaries. The important change is that
a keyword or symbol match no longer means "keep the whole file." It means
"there is evidence here; decide whether this file owns the action or merely
carries a span of evidence."

### What Failed And Why

#### Tiny precision wins were not trustworthy

The first baseline was:

| Metric | Baseline |
|---|---:|
| Recall | 64.0% |
| Avg token precision | 41.7% |
| Packed tokens | 52,980 |

The token-neutral replacement attempt moved to roughly 64.2% recall and 42.0%
average token precision. That was not enough. Earlier true-noise diagnostics
also showed true noise worsening from 13,367 tokens to 13,906 tokens. The
lesson is that a small strict precision increase can be a metric artifact if it
does not also reduce audited true noise.

Stop rule:

```text
Do not keep a benchmark policy because it moves strict token precision by less
than about 1pp unless true noise, selected token mass, and slices also improve.
```

#### Sample fixtures were too saturated to guide this work

The sample-fixture suite stayed green and often unchanged while full benchmark
results moved. That was not necessarily a bug. The fixtures are small, stable
regression smoke tests. They are good at catching obvious behavior regressions,
but they are not sensitive enough to validate release precision work across
large public repositories.

Rule:

```text
Use --sample-fixtures as a smoke test.
Use full release JSONL plus slices as the tuning signal.
```

#### Probabilistic knapsack optimized the wrong object

The probabilistic knapsack experiment was attractive because it sounded
mathematically neutral. It failed badly: 43.5% recall and 35.3% average token
precision. The failure mode was that file-level probability and token budget did
not preserve the action path. The system needs to know which evidence is
structural, which is owner evidence, and which is just nearby signal.

Rejected formulation:

```text
maximize file probability under token budget
```

Better formulation:

```text
minimize packed token mass
subject to preserving action-owner evidence and recall
```

#### CGA and nature-inspired graph formulas were not enough

The CGA runs were useful because they forced a better mental model, but they did
not pass the gate:

| Run | Recall | Avg token precision | Decision |
|---|---:|---:|---|
| CGA | 57.0% | 37.3% | Rejected |
| CGA optimized | 53.6% | 38.0% | Rejected |

The lesson is not that nature analogies are useless. The lesson is that the
analogy must point to a measurable invariant. "Lowest-action path" became useful
only after it was translated into concrete constraints:

```text
selected_file_ids_before == selected_file_ids_after
expected-token loss must stay near zero
token removal should come from evidence carriers
memory-confirmed owners must not be compacted
```

#### Atom rescue expanded the wrong surface

The first targeted-atoms implementation selected more files. It raised recall to
67.7%, but average token precision fell to 35.1% and packed tokens rose to
71,326. That violated the core constraint:

```text
Targeted atoms should reduce token mass inside already selected files.
They should not expand the selected-file set.
```

The post-selection atom projection respected the selected set and improved
average token precision only to 40.2%. That proved the idea was safer, but the
extraction quality was too weak.

#### Label-free tiers misclassified valuable files

The label-free tier policy removed 3,332 tokens but lost 1,510 expected tokens,
for a -0.5pp aggregate TP move. The classifier treated some valuable
medium-evidence files as compressible. The useful learning was:

```text
Weak files were mostly safe but too small.
Medium files were large enough to matter but unsafe.
```

The follow-up risk-aware policy was safer but too small: +0.06pp aggregate TP.
That rejected a weak-only pruning path as the main solution.

#### Strong evidence is not the same as action ownership

The audit found the most important classifier bug: "strong evidence" had been
treated as "must keep full file." Many strong-looking files were only evidence
carriers. A file can contain a matched definition, symbol, or API surface and
still not be where the action should happen.

New distinction:

```text
action_owner_strong:
  preserve full or conservative context

evidence_carrier_strong:
  preserve imports/header + matched structural spans
```

This distinction is the heart of the accepted direction.

#### AST checkpoint and MAV were useful but not standalone wins

Guarded strong-carrier, AST checkpoint, and MAV span projections all showed real
signal on the 57,512-token diagnostic baseline:

| Diagnostic | Projected aggregate TP gain | Removed tokens | Strict noise removed | Expected token loss |
|---|---:|---:|---:|---:|
| Guarded strong-carrier | +1.63pp | 2,829 | 2,667 | 162 |
| AST checkpoint | +1.33pp | 2,610 | 2,366 | 244 |
| MAV span | +1.63pp | 2,592 | 2,519 | 73 |

None of these alone captured enough of the oracle ceiling. They were still worth
keeping because they proved which direction had a lower expected-token loss
profile. The production change combined the safe parts rather than promoting
one diagnostic formula directly.

On the final 31,714-token replay, the same diagnostics had much less remaining
room:

| Diagnostic | Projected aggregate TP gain | Removed tokens | Strict noise removed | Expected token loss |
|---|---:|---:|---:|---:|
| Oracle ceiling | +14.58pp | 7,878 | 7,878 | 0 |
| AST checkpoint | +0.18pp | 132 | 132 | 0 |
| Guarded strong-carrier | -0.05pp | 346 | 179 | 167 |
| MAV span | -0.14pp | 78 | 0 | 78 |
| Neutral MAV | -0.89pp | 496 | 0 | 496 |

That is expected after production compaction already removed much of the
obvious mass. Future diagnostics should compare against the current baseline,
not an older high-noise run.

### Mathematical Learning

The failed formulas all tried to answer:

```text
Which file has the best score?
```

The successful direction answers:

```text
Which tokens carry marginal action value?
```

Useful working objective:

```text
minimize total packed tokens

subject to:
  selected-file recall >= release target
  action-owner evidence is preserved
  memory-confirmed evidence is preserved
  selected file set does not expand during compaction
  expected-token loss is explainable and small
```

Diagnostic score for a span:

```text
marginal_action_value_per_token =
  action_evidence(span, task, reasons, symbols, summaries)
  / max(1, estimated_tokens(span))
```

But the objective must be gated. A pure multiplication or density score can
destroy recall when one signal is missing. The safe shape is:

```text
Stage 1: discover candidates broadly
Stage 2: classify selected files as action owners, carriers, or uncertain
Stage 3: protect owners and memory-confirmed files
Stage 4: compact carriers by source-aware spans
Stage 5: prove the result with full JSONL, not sample fixtures alone
```

This is why the final implementation is not "generic keyword matching." Keyword
and symbol matches are inputs. The decision is structural and token-economic:

```text
keyword/symbol evidence -> find possible evidence
action-owner classification -> decide protection
source-aware spans -> keep the smallest useful evidence
token accounting -> reject false precision wins
```

### Benchmark Methodology Lessons

Keep these guardrails for future work:

1. Keep diagnostics separate from production behavior until a full run proves
   the policy. Oracle and label-aware diagnostics are allowed only as ceilings.
2. Do not use benchmark `expected_files` inside production selection or
   extraction. Expected labels are for scoring and diagnostics only.
3. Require selected-file-set stability for compaction experiments unless the
   experiment is explicitly about selection.
4. Report both average token precision and aggregate token precision.
5. Report removed tokens, strict noise removed, expected token loss, and
   selected-file-set violations for every projection.
6. Treat `0` memory markers as "memory path untested", not as evidence that
   memory failed.
7. Avoid broad static path or filename lists unless they encode portable
   ecosystem conventions and include negative tests.
8. Do not tune against one repo slice without a full-suite replay.
9. Treat sample fixtures as smoke tests. They can stay unchanged after real
   improvements because they are small and already saturated.
10. Prefer a scoped clean replay worktree for final benchmark and publish steps
    when the main checkout is dirty.

### Production Bar For Future Precision Work

Do not productionize the next selector or compactor change unless it clears this
minimum bar on a fresh full run:

| Requirement | Bar |
|---|---|
| Release recall | >= 60% for current gate, or the active release target |
| Average token precision | >= active release target |
| Strict TP movement | Prefer >= +3pp unless the release gate is already passing |
| Selected file count | Must not increase for compaction-only work |
| Packed tokens | Must decrease or stay flat for compaction-only work |
| Expected token loss | Small, explainable, and lower than strict noise removed |
| Memory-confirmed files | Must be protected or explicitly tested by seeded memory A/B |
| Slice regressions | No major language/task slice regression without a written reason |

### Next Open Problems

The final oracle still shows +14.6pp aggregate TP headroom, so the work is not
finished. The next improvement should not be another broad keyword or score
boost. It should target the remaining gap between oracle compression and
label-free compression.

Promising next diagnostics:

1. Better source-span extraction for selected non-expected carriers, especially
   Go, TypeScript, Java, YAML, and test files.
2. A label-free "evidence carrier, not action owner" classifier that uses graph
   role, source summaries, rank, token mass, match density, and memory signals.
3. Per-language AST summaries with stable symbol ranges and nearby comments.
4. Case-level reports showing the top harmful compressed files and top safe
   compressed files by expected-token loss per 1,000 tokens removed.
5. A published dated benchmark result for the final 94-case locked release run,
   if this run becomes release-facing evidence.

## 2026-06-14 Release-Target State

This older release-target gate was run against the expanded public suite:

```bash
PYTHONPATH=src python -m agentpack.cli benchmark \
  --public-suite \
  --no-public-table \
  --benchmark-jsonl /tmp/agentpack-full-maintenance-recovery.jsonl
```

Result:

| Metric | Result |
|---|---:|
| Scored cases | 108 |
| Avg precision | 43.6% |
| Avg recall | 66.0% |
| Avg F1 | 48.2% |
| Avg token precision | 51.1% |
| Recall target | Passed, 65.0% |
| Token precision target | Passed, 51.0% |

This is the first local checkpoint in this cycle that clears the 65% recall bar
while keeping token precision above 51%. The precision margin is real but thin,
so release notes should report the exact benchmark command and result artifact
rather than only the rounded headline. The release result note is
[`benchmarks/results/2026-06-14-public.md`](https://github.com/vishal2612200/agentpack/blob/main/benchmarks/results/2026-06-14-public.md).

## 2026-06-13 Release-Target Experiment Log

The release-target work converged only after separating evaluation noise,
diagnostics, and selector behavior. The successful path was not a larger budget
or broader ranking boost; it was a narrow maintenance-context recovery rule
validated by intent-level diagnostics.

| Run | Avg recall | Avg token precision | Outcome |
|---|---:|---:|---|
| Intent diagnostics baseline | 64.2% | 52.5% | Below 65% recall, but precision was healthy enough to inspect misses by intent. |
| Cleanup recovery, first full run | 64.8% | 51.9% | Recall improved but stayed below target; precision stayed above the 51% floor. |
| Maintenance recovery final run | 66.0% | 51.1% | Target cleared across 108 scored public cases. |

Two sensitive regression slices were run after the full suite:

| Slice | Cases | Avg recall | Avg token precision | Readout |
|---|---:|---:|---:|---|
| `pallets-click` | 25 | 71.0% | 50.3% | Passed the default gate, but precision is thin. |
| `nestjs` | 4 | 75.0% | 43.9% | Recall is stable; token precision remains below the slice target. |

The final useful intent readout from the 108-case run was:

| Intent | Cases | Avg recall | Avg token precision | Misses | Interpretation |
|---|---:|---:|---:|---:|---|
| `cleanup_refactor` | 9 | 57.4% | 67.1% | 5 | Best safe recall-recovery area; high precision allowed narrow cap/floor exceptions. |
| `typing_api` | 17 | 70.6% | 57.5% | 10 | Improved from deprecation maintenance recovery, but still has cap/floor misses. |
| `dependency_release` | 25 | 70.1% | 66.9% | 21 | Good precision, but many misses are legitimate multi-file dependency changes. |
| `test_focus` | 20 | 77.4% | 51.1% | 21 | Recall is strong, but precision is near the floor; avoid broad test expansion. |
| `config_build` | 14 | 48.8% | 35.0% | 14 | Main remaining bottleneck; low precision makes broad config recovery risky. |
| `source_behavior` | 10 | 70.0% | 35.1% | 9 | Many plausible source files are unlabeled or noisy; needs better scope/package intent. |
| `docs_metadata` | 5 | 40.0% | 9.1% | 6 | Too noisy for selector tuning until labels and task intent are audited. |

### Successful Experiments

| Experiment | Result | Why it worked |
|---|---|---|
| Intent diagnostics | Added `By Intent` summaries and JSON diagnostics without changing selector behavior. | It identified `cleanup_refactor` as low recall but high token precision, which made it a safer target than `config_build` or docs. |
| Label-audit diagnostics | Separated audited noise from plausibly useful unlabeled context. | It prevented overreacting to low raw precision in cases where selected context was related but not labeled expected. |
| Maintenance summary-floor bypass | Allowed cleanup/refactor/deprecation candidates through the summary floor only with direct evidence. | It recovered files that were ranked and relevant but blocked by strict precision guards. |
| Cleanup/refactor cap overflow | Allowed at most two cheap compressed maintenance candidates under strict caps. | It recovered maintenance files without widening the global summary cap. |
| Token-neutral maintenance replacement | Let stronger maintenance candidates replace weaker selected maintenance summaries. | It improved slot quality without increasing token volume. |
| Deprecation-specific content gate | Allowed deprecation maintenance files with at least two content hits and a 60-point score floor. | It recovered MarkupSafe deprecation cleanup while avoiding broad recently-modified file inclusion. |

### Failed Or Marginal Experiments

| Experiment | Result | Lesson |
|---|---|---|
| Drastically widening the token budget | Did not produce a reliable move past 65% recall and risked lowering token precision. | The bottleneck was not only budget size; it was which marginal files survived selection. |
| Broad ranking boosts | Produced little or no durable aggregate gain. | Many expected files were already candidates; ranking-only changes did not fix cap, floor, and replacement losses. |
| Static variable/path-style expansion | Rejected as overfitting risk. | New rules should encode portable conventions or dynamic evidence, not public-suite expected-file lists. |
| Broad cleanup/refactor trigger including generic `refactor` and `import` | Caused Vite precision regressions without recall gains. | Generic refactor/import wording is too broad; the retained trigger is limited to maintenance terms such as `unused`, `cleanup`, `simplify`, `lint`, `format`, `polish`, and deprecation. |
| Scope-sticky cleanup overflow | Dropped the Spring slice from 65.0% to 63.3% recall. | The first overflow scope is not always the expected scope; replacement is safer than locking future overflow to the first selected scope. |
| Forcing `remove unused config` toward `pyproject.toml` | Rejected after slice inspection. | The competing workflow file scored higher with the same evidence; forcing the expected file would be label-specific rather than generic. |
| More test cap expansion | Kept narrow only. | Test-focused recall is already high, while token precision is close to the floor; broad test expansion would spend the precision margin. |

### Eval-Set Caveats

The expanded public suite is useful, but it is not a perfect product-quality
oracle.

- The command prints 128 public cases in some runs, but the current scored
  release-target JSONL has 108 cases with `expected_files`. Report the scored
  count when citing recall and precision.
- Expected files come from real commits, but labels are incomplete. Some
  selected "noise" is plausibly useful context, especially same-package,
  same-family, dependency, and test-adjacent files.
- Added files must be filtered out when benchmarking the parent checkout,
  because AgentPack cannot select files that do not exist yet.
- Token precision is harsher than file recall. A selected expected file can
  still contribute few expected tokens if the packed summary is too broad.
- Vite and config/build tasks remain precision-sensitive. A change that improves
  Spring or MarkupSafe can still regress Vite because plausible source,
  playground, config, and test files compete for the same slots.
- Slice wins are diagnostic, not release claims. The release claim needs a full
  public-suite run with exact recall and token precision.

## What Improved

The precision target improved because the work separated the problem into
ranking, selection, and packing failures instead of tuning one global score.

The most useful improvements were:

| Change | Why it helped |
|---|---|
| Literal definition matching | Quoted API names such as `parseAst` should prefer the defining/exporting file over call-site noise. |
| Multi-term path ranking | Tasks with several concrete path terms should reward files whose paths contain those terms, especially config files. |
| Conditional two-config cap | Low-budget strict packs can include two strongly matched config files without opening the door to generic config noise. |
| Package-root source detection | Monorepos often keep source under `packages/<name>/...` without a `src/` segment. Those files need direct-source priority when evidence is strong. |
| Narrow root-Go strict support | Root Go source files with conventional-scope source evidence recovered Gin recall without expanding the summary cap. |
| Same-package paired-test overflow | Balanced no-live packs may include one extra `packages/<name>/...` test only when it directly tests an already selected source file and has direct content evidence. This recovered a NestJS expected test without a broad cap increase. |
| Same-playground test overflow | Balanced no-live packs may include one extra `playground/<name>/...` test only when the same playground already has selected context plus scope and phrase/content evidence. This recovered one Vite playground test with near-neutral token precision. |
| JVM build metadata signal | Java build/dependency tasks often expect root `pom.xml` or Gradle metadata. Root JVM build files now get a scoped boost. |
| Reason/family diagnostics | `reason_family_precision`, selected family waste, failure type counts, and low-budget last-file waste made tuning evidence-backed. |
| Parent-checkout expected-file filtering | Public history samples now exclude paths that do not exist in the parent checkout, so added files are not counted as selectable recall misses. |

## What Failed

These experiments were rejected because they helped one slice while hurting the
full suite:

| Rejected change | Failure mode |
|---|---|
| Broad content-only concrete boost | It over-selected noisy files with generic content hits and regressed TypeScript precision. |
| Treating `.go` files as release metadata | It pulled `version.go` into unrelated Gin tasks and hurt Go precision. |
| Broad explicit-test cap increase | It improved some recall but admitted too much test noise. |
| Specific-config pack suppression | It cleaned one Tailwind config case, but regressed CSS/config tasks that still needed source files. |
| Broad build metadata boost for `pyproject.toml` and package files | It fixed Spring-like tasks but hurt Python dependency/update cases, especially MarkupSafe. |
| Broad source strict-support exception | It recovered a few Go source files but admitted non-expected Java source and spent precision margin. Narrowing to the measured root-Go pattern kept the gain and removed the Java regression. |
| Compact-cost selection priority for JS/TS skeletons | It fixed one NestJS ordering issue, but reduced Vite precision by promoting plausible package tests over expected playground/config files. The safer retained rule is paired-test overflow only. |
| Weak package-test overflow | A Vite `worker.spec.ts` test looked locally related but had only 3 content hits and no task phrase for an `import glob` task. Package test overflow now requires stronger task evidence to avoid this slot waste. |

The rule from these failures: keep boosts scoped to the evidence family that
proved the gain. Do not generalize from one benchmark case until the repo slice
and full suite confirm it.

## Benchmark Validity Guardrail

Public history cases run AgentPack against the parent of a real commit. A file
added by that commit does not exist in the parent checkout, so AgentPack cannot
select it. Sampled expected files must therefore be filtered to paths that exist
in the parent commit.

This is a methodology correction, not a ranking improvement. It removes
impossible `EXPECTED_NOT_FOUND` misses from sampled public cases while preserving
modified/deleted files that are selectable from the parent checkout.

## Static-List Guardrail

Static lists are acceptable only when they encode portable ecosystem
conventions, not benchmark-case outcomes.

Acceptable examples:

| Convention | Why it is acceptable |
|---|---|
| Test filename forms such as `_test.go`, `.spec.ts`, and `tests/` | These are language and framework conventions used outside the benchmark suite. |
| Root build metadata such as `pom.xml`, Gradle files, and package manifests | These are standard project files for dependency/build tasks. |
| File extensions and generated/example/test directory names | These describe broad file families, not one repo's expected answer. |

Risky examples:

| Shortcut | Why it is risky |
|---|---|
| Boosting a specific filename because one public case expected it | It can inflate one slice while hurting unrelated tasks. |
| Adding repo-shaped path rules such as a known package or fixture directory | It makes benchmark numbers less trustworthy and does not generalize. |
| Treating a language extension as task metadata without stronger evidence | It admits plausible-but-not-actionable files and wastes selection slots. |

New ranking rules should prefer dynamic evidence: task literals, symbol
definitions, imports/calls, same-package locality, dependency edges, changed-file
history, and measured reason-family precision. If a static convention is added,
it needs a non-benchmark rationale plus negative tests that prove it does not
revive known noisy families.

## Slice Readout

The latest useful slice picture:

| Slice | Status |
|---|---|
| Python CLI/library | No longer the main precision bottleneck. Click and itsdangerous are healthy; MarkupSafe recovered after narrowing build metadata. |
| TypeScript/Vite | Same-playground test overflow moved the current Vite slice from 44.5% to 45.7% recall with token precision essentially neutral at 45.5% to 45.4%. Config/source ranking and ranked-low expected files remain the main blockers. |
| Go/Gin | Improved after root Go source and test-path handling. Latest targeted slice moved from 48.1% to 55.6% recall while token precision rose slightly from 46.5% to 47.3%. |
| Java/Spring | Strong gain from scoped JVM build metadata. |
| TypeScript monorepo/NestJS | Same-package paired-test overflow improved the current scored NestJS slice from 62.5% to 70.8% recall and from 40.5% to 44.0% token precision, but the slice remains below the 50% precision target. |

## Benchmark Loop Guardrail

Full public-suite runs are expensive enough that experiments should not use them
as the first diagnostic. Use this loop instead:

1. Establish one full-suite baseline JSONL.
2. Inspect case-level misses and choose the affected repo or task-type slices.
3. Run only those slices with `--public-repo-filter` or
   `--public-task-type-filter` and write `--benchmark-jsonl`.
4. Compare case-level recall, token precision, selected paths, and miss status.
5. Run the full public suite once only after the slice result has a credible
   chance of improving the aggregate gate.

Example:

```bash
agentpack benchmark \
  --public-repos \
  --public-repo-filter gin,spring-petclinic \
  --public-repos-cache /tmp/agentpack-public-cache-full \
  --benchmark-jsonl /tmp/agentpack-go-java.jsonl \
  --misses
```

## Current Mental Model

Do not ask only whether recall went up. Ask where the expected file was lost:

1. Candidate generation: was the expected file found at all?
2. Ranking: did it reach the top candidates?
3. Selection: did the pack choose it under budget and family caps?
4. Packing: did selected tokens include useful expected-file content or mostly noise?

Useful recall is not just "the file appeared somewhere." It means the expected
file appeared early, survived selection, and contributed enough useful tokens.

## Metrics To Keep Watching

These metrics were the most useful during tuning:

| Metric | Use |
|---|---|
| `candidate_recall_at_50` | Separates discovery failures from ranking/selection failures. |
| `candidate_precision_at_3` | Shows whether noisy files dominate the top of the ranked list. |
| `token_precision` | Main measure for packed-token usefulness. |
| `expected_token_coverage` | Approximates valuable recall for selected expected files. |
| `selected_family_waste_tokens` | Shows whether source, test, docs, config, fixtures, or generated files leak noise. |
| `reason_family_precision` | Shows which ranking reasons are trustworthy. |
| `failure_type_counts` | Splits misses into not found, ranked low, skipped, or noise selected above expected. |
| `precision_delta_if_drop_last_summary` | Identifies low-budget extra-file waste. |

## Next Optimization Areas

The 2026-06-13 local checkpoint clears the immediate release target:

| Metric | Target |
|---|---:|
| Expanded public-suite recall | 66.0% |
| Expanded public-suite token precision | 51.1% |
| Major language/task-slice recall regression | <= 2 points |
| `EXPECTED_NOT_FOUND` sampled-public misses | 0 |

The next release should treat this as a achieved-but-thin margin, not as room
for broad expansion. Token precision has only about one point of headroom above
the 50% floor, so the next changes should start with diagnostics and targeted
slice validation.

The next precision/recall work should focus on these areas:

1. Config/build intent recovery.
   `config_build` is the main remaining bottleneck: 48.8% recall and 35.0%
   token precision in the latest full run. Do not raise config caps broadly.
   First diagnose config-only, source-only, and config-plus-source tasks.

2. Vite config/source balance.
   Some config tasks need only config files, while CSS/build tasks need both source
   and config. Selection needs conditional inclusion, not global suppression.

3. NestJS wrong-package locality.
   Monorepo tasks need better package/workspace intent detection so `packages/core`
   and `integration/*` do not steal from each other unless the task evidence says
   so.

4. Snippet/block packing.
   Several cases select the expected file but include too much surrounding noise.
   Function, class, config-section, and diff-hunk packing should improve token
   precision without sacrificing selected-file recall.

5. Publish decision gates.
   Keep changes only when full-suite recall stays at or above 65%, token
   precision stays at or above 51%, and no major language/task slice regresses
   by more than 2 recall points.
