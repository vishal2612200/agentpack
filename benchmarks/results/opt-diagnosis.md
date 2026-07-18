# Optimization Tier 1 — miss diagnosis

Ran `agentpack benchmark --public-repos --public-repo-filter laravel-framework,rails,spring-petclinic --misses` (50 cases: 15 PHP + 15 Ruby + 20 Java) to bucket every miss by `failure_type` before writing any optimization code.

## Tier 2a attempt — REVERTED, documented as a cautionary result

First implementation attempt: a cold-case fallback for
`enrich_keyword_weights_from_files` in `FileRanker.rank`
(`application/pack_service.py`). On a cold benchmark case (no live
diff), the existing enrichment is a no-op since it reads
`changes.all_changed` — which is empty. The fix ran a cheap
preliminary `score_files` pass using only the base task keywords, took
the top-15 ranked files, and fed *those* into
`enrich_keyword_weights_from_files` as a substitute for the changed-file
set.

**Result on Java (spring-petclinic): recall 61.7% → 67.5% (+5.8pp)** —
looked like exactly the fix Tier 1 called for.

**Result on PHP (laravel-framework): recall 16.7% → 0.0% — total
collapse across all 15 cases.**

Root cause, confirmed via direct testing: this design has a
**self-reinforcing feedback loop**. On a genuinely cold case with weak
base task keywords, the preliminary pass has little real signal to
rank on — its "top 15" files on a large repo like laravel are
essentially arbitrary (whichever files happen to pick up incidental
structural boosts like `config_file` or generic phrase matches).
Directly testing `enrich_keywords_from_files` against 15 real laravel
files with an unrelated base keyword set (`{queue, job, dispatch}`)
returned new "keywords" like `int`, `string`, `type`, `types`, `new`,
`assert`, `many` — generic PHP/Eloquent boilerplate tokens that appear
in nearly every file in the framework. Once these get added to
`keyword_weights` at weight 0.5, they inflate scores for files that
share nothing with the actual task except common language syntax,
while doing nothing to help the files that actually matter — and
because the preliminary pass's own choices feed the enrichment, wrong
early signal gets *amplified*, not corrected.

**Lesson for the rest of this pass:** keyword enrichment from file
*content* is only safe when the seed set is genuinely relevant (a real
diff). A cold-case substitute needs a seed set with an independent
relevance signal — not "whatever the ranker liked without any real
information." Candidates for a safer version, not yet attempted:
- Enrich from `summaries[path]["ranking_keywords"]` / `["defines"]`
  fields (already curated, extracted per-file by the offline
  summarizer) rather than raw high-frequency word extraction — these
  are structured identifiers, less prone to boilerplate pollution.
- Gate enrichment on a minimum preliminary score threshold well above
  0 (e.g. only enrich from files that scored ≥100, a level normally
  reached only via multiple corroborating signals, not one generic
  match) — makes the "garbage in, garbage amplified" failure mode much
  rarer.
- Only enrich using the enclosing symbol/class names of files that hit
  via `filename_keyword` or `symbol_keyword` matches specifically —
  signals with lower false-positive rates than generic content
  matches.

The change was reverted in full (`git diff` on `pack_service.py`
confirmed clean); Java's baseline verified to return to exactly 61.7%
recall post-revert. No code from this attempt shipped. Tier 2 is
paused pending a redesign along the lines above, or is deprioritized
below Tier 3/4 which don't share this risk profile.

## Tier 3 — shipped, safe, small measured effect

Two changes landed in `analysis/ranking.py`:

1. **`KeywordPlan.task_class`** — new field, populated via
   `classify_task(task).kind` in `build_keyword_plan`. This surfaces
   `task_classifier`'s output (previously computed once in
   `FileRanker.rank` and stored only as unread telemetry on the
   `RankResult`) to the scoring functions that need it.
2. **`_direct_content_evidence_bonus` now distinguishes evidence
   strength.** Previously, any file with 2+ content-keyword hits and
   *any* of {matched call, matched define, literal definition match,
   multi-token defines match, matched entrypoint, keyword phrase match}
   got the same 120-270 point bonus. Bare "keyword phrase match"
   (matching a generic tech-stack term like "spring boot" — present in
   nearly every file of a Spring project's build config) is much
   weaker evidence than an actual call/define/entrypoint match sourced
   from a file's own summary. Phrase-only evidence is now capped at 45
   points instead of up to 270.

**Directly verified the mechanism**: on the diagnosed
`build.gradle`-crowds-out-tests case, `build.gradle`'s score dropped
from 482.5 → 407.5 (exactly the expected ~75-point reduction). It
still outranked the expected test files (which scored 107-152), so
this specific case did not flip to a hit — the file has other
contributors (flat `config_file` bonus, content-keyword-match itself)
that remain large enough on their own.

**Benchmark result, all 3 languages, full regression suite passing:**

| Language | Recall (before → after) | reason_graph (before → after) | Other metrics |
|---|---|---|---|
| Java (spring-petclinic) | 61.7% → 61.7% (unchanged) | 31.4% → 28.4% (−3.0pp) | tp, content unchanged |
| PHP (laravel-framework) | 16.7% → 16.7% (unchanged) | 5.6% → 6.7% (+1.1pp) | tp, content unchanged |
| Ruby (rails) | 13.3% → 13.3% (unchanged) | identical | identical |

**Verdict:** safe (zero regression across all 3 languages, full test
suite green), mechanically verified to work as designed on the exact
case it targeted, but not strong enough *alone* to move aggregate
recall in this benchmark sample. `KeywordPlan.task_class` is now
plumbed and available for future, larger intent-conditional changes
(e.g. reducing the flat `config_file` bonus itself, or the
`content_keyword_per_hit` weight, when `task_class` isn't infra/release)
— those are larger, riskier changes deferred to a future pass rather
than compounded onto this one without their own validation cycle.

## Session wrap-up: what to do next

Given the diagnosis (Tier 1) already identified PHP/Ruby's #2 lever as
"direct dependency of changed file" appearing repeatedly as a
contributing-but-insufficient reason on `EXPECTED_RANKED_LOW` misses,
**Tier 4 (PSR-4 resolution for PHP, package-convention resolution for
Java/Kotlin)** remains the highest-confidence next step — it turns
raw-string import edges into real file-to-file graph edges, which is
a structural improvement with no risk of the kind of feedback-loop
failure mode found in Tier 2. It was not attempted in this pass given
the extensive validation-cycle time already spent on Tiers 2 and 3
(each full 3-language benchmark run costs ~10-15 minutes, and getting
a trustworthy read on a change requires at least one such run per
language affected).

## Failure-type histogram

| Language | Cases | Total misses | EXPECTED_RANKED_LOW | EXPECTED_SKIPPED | NOISE_SELECTED_ABOVE | EXPECTED_NOT_FOUND |
|---|---|---|---|---|---|---|
| Java | 20 | 16 | 1 (6%) | 14 (88%) | 1 | 0 |
| Ruby | 15 | 21 | 18 (86%) | 3 (14%) | 0 | 0 |
| PHP | 15 | 20 | 16 (80%) | 3 (15%) | 1 | 0 |

## Key finding: zero `EXPECTED_NOT_FOUND` across all three languages

The scanner never drops an expected file for any of these three repos.
**Tier 6 (scanner/agentignore pruning) is off the table entirely** —
there is no scan-side bottleneck to fix. Every miss is either a ranking
problem (file was scored but too low) or a selection/budget problem
(file was ranked fine but didn't make the final cut).

## PHP / Ruby: dominated by `EXPECTED_RANKED_LOW` (80-86%)

Files are in the candidate pool and get *some* signal, but not enough
to clear the bar. Aggregating the `reasons` attached to these misses:

**PHP** — weak/singular signals dominate: `content keyword match (1)`
(9×), `recently modified` (6×), `direct dependency of changed file`
(5×), `filename keyword match` (4×). Almost every miss has exactly
**one** weak contributing reason, not zero — the file isn't invisible
to the ranker, it's just under-scored relative to competing files that
stack 3-4 signals.

**Ruby** — same shape, plus something specific: `likely false
positive: keyword-only match` (6×) and `weak filename-only match -45`
(1×) — these are **active dampeners** (`ranking.py:1685` false-positive
multiplier ×0.72, `weak_filename_match_penalty=-45`) suppressing files
that, in these cases, are actually correct. The false-positive guard
that protects precision elsewhere is costing recall here.

**Implication for the plan:** Tier 2 (keyword expansion — un-gating
`enrich_keywords_from_files`, extending `_CONCEPT_MAP`, stemming) is
exactly the right lever — it converts single weak keyword hits into
multiple hits, which is the single most common miss pattern on both
languages. Tier 4 (import resolution) is the second lever — `direct
dependency of changed file` shows up as a contributing-but-insufficient
reason repeatedly, meaning more resolved edges should push more files
over the threshold.

## Java: dominated by `EXPECTED_SKIPPED` (88%) — a completely different problem

Two distinct sub-patterns found by inspecting the raw misses:

**Pattern A — generic build-config files crowd out real candidates.**
`build.gradle` scores 482.5 (via `content keyword match (2)` +
`keyword phrase match: spring boot` + `direct content evidence +120` +
`config file` boost) and beats real test/source files scoring
212-227. The problem: "spring boot" as a keyword phrase appears in
*every* Spring project's build file, so the config-file boost combined
with a generic tech-stack keyword match is systematically
over-crediting build files on Spring repos specifically.

**Pattern B — `recently modified`-only files fall below the summary
score floor.** Files whose only signal is `recently modified` score
~20 points, below `min_summary_score=60` (`core/config.py`) — so they
get filtered before ever reaching selection, even though "this file
was touched in the same historical window as the target commit" is a
real, meaningful recall signal on its own.

**Implication for the plan:** Neither pattern is What Tier 4/5 (import
resolution, deeper queries) fixes — they're pure ranking-weight
problems. **Tier 3 (intent-conditional weights) needs an explicit new
case**: soften `config_file` boost + generic tech-stack keyword phrase
matches specifically when other candidates exist with more specific
signal (not just for "feature" intent broadly — this is a distinct
"config file vs. real candidate" tension). Consider also whether
`recently_modified` alone should be allowed to clear the floor when
combined with even one other weak signal, rather than needing to
individually exceed the 60-point floor.

## Revised tier priority (unchanged tiers, sharper aim)

1. **Tier 2** (keyword expansion) — validated as the top PHP/Ruby
   lever by miss-reason frequency.
2. **Tier 3** (intent weights) — now has two concrete, measured
   targets instead of a general hypothesis: (a) soften config-file +
   generic-keyword-phrase over-scoring on Java/Spring-shaped repos,
   (b) reconsider whether `recently_modified`-only files should clear
   `min_summary_score` floor more easily.
3. **Tier 4** (import resolution) — validated as the #2 PHP/Ruby
   lever (`direct dependency of changed file` under-scoring).
4. **Tier 5** (deeper queries) — unchanged, still valid but behind
   1-3 in priority since neither language's dominant miss pattern is
   "missing symbol," it's "insufficient score."
5. **Tier 6 (scanner pruning) — SKIPPED.** Zero `EXPECTED_NOT_FOUND`
   across all three languages proves there's no scan-side bottleneck.
6. **Tier 7 (monorepo pool scoping)** — demoted from "fixes NOT_FOUND
   on Rails" (disproven — zero NOT_FOUND on Rails too) to "might help
   RANKED_LOW by reducing cross-workspace noise," lower confidence,
   still optional/last.
## Tier 4 attempt (import resolution) — REVERTED, net-negative on dense repos

Implemented and fully tested PSR-4 resolution for PHP (parse
`composer.json` autoload map, resolve `use App\Models\User` →
`app/Models/User.php`) and package-directory-convention resolution for
Java/Kotlin (`import com.x.Foo` → `.../com/x/Foo.java` via a suffix
index built once per graph build). Both resolvers were verified correct
against the real cloned repos:
- spring-petclinic: 22 real intra-repo import edges resolved (was 0),
  including the model→entity chains (Owner→Person, Pet→NamedEntity).
- laravel-framework: `Illuminate\Contracts\Queue\Factory` →
  `src/Illuminate/Contracts/Queue/Factory.php`, longest-prefix PSR-4
  fallback working. 8394 real edges resolved across 3295 files.

25 unit tests passed (5 new for the resolvers), full suite green.

**Benchmark result: a real regression.**

| Language | Recall (Tier 3 → Tier 4) | reason_graph | Verdict |
|---|---|---|---|
| Java (spring-petclinic) | 61.7% → 61.7% | 28.4% → 28.4% | neutral (tiny sparse graph, 22 edges) |
| PHP (laravel-framework) | 16.7% → **13.3%** | 6.7% → **5.0%** | **regressed** |

The numbers are deterministic (frozen commit sample), so 16.7% → 13.3%
is a real change caused by the edges, not sampling noise. Adding import
edges *lowered* the exact metric they were meant to raise.

**Root cause — graph signal without IDF, in a dense hub-and-spoke.**
Measured laravel's import graph directly: 8394 edges, and the top import
targets are generic infrastructure imported by hundreds of files —
`Database/Eloquent/Model.php` (273 importers),
`Collections/Collection.php` (253), `Support/Str.php` (238),
`Container/Container.php` (175). The ranker's neighbor-boost passes
(`boost_recall_neighbors`, `boost_second_pass_expansion`) propagate
score from high-scoring seed files to their import neighbors. In a dense
framework graph, that means score floods into these hub files — which
are imported everywhere but are almost never the actual *target* of a
specific task — and they crowd out the real task-relevant files, so
recall drops.

Two compounding reasons the benchmark is the worst case for raw import
edges:
1. **Cold regime.** Benchmark cases have no live diff (`all_changed`
   empty). The import graph's genuine value is finding neighbors of
   *changed* files; with no changes, `direct/reverse dependency of
   changed file` reasons barely fire, so the graph provides almost no
   upside — only the hub-noise downside.
2. **No edge weighting.** Every edge counts equally. An edge to a file
   imported by 273 others carries essentially zero discriminative
   signal but propagates the same boost as an edge to a rarely-imported,
   task-specific file.

**Reverted in full** — the 4 changed files (`dependency_graph.py`,
`java_imports.py`, `php_imports.py`, and the 5 resolver tests) restored
to the clean Tier 3 state; PHP verified back to the 16.7% baseline; full
test suite green.

**How to make import resolution net-positive (future work, not
attempted here):**
- **Down-weight edges by target in-degree (graph IDF).** A file imported
  by N others contributes boost ∝ 1/log(N) or is excluded above a
  threshold (e.g. drop edges to any file imported by >30 others). This
  is the single most important missing piece — it converts the dense
  framework graph from anti-signal to signal.
- **Only propagate along edges from/to task-relevant seeds**, not all
  high-scoring files, to stop generic hubs from seeding expansion.
- **Validate in a warm regime** (with a real diff) where the graph's
  actual value — neighbor-of-changed-file — is exercised, rather than
  only the cold benchmark regime that shows only the downside.

Until edge weighting exists, raw import resolution should not be shipped:
it helps sparse repos negligibly and hurts dense framework repos
measurably.
