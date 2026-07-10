# Infra/schema languages — baseline (Terraform, Dockerfile, Protobuf, GraphQL)

Adds tree-sitter symbol + import extraction for four declarative
languages, a second category beyond the general-purpose languages
(Java/Ruby/PHP) shipped earlier. Symbols reuse the existing
`Symbol.kind` Literal (`class`/`function`/`method`/`variable`) per
explicit scoping decision — no model change.

| Language   | "Symbol" maps to | Imports |
|---|---|---|
| Terraform  | `resource`/`module`/`variable`/`output`/`data` blocks → `class`, named `type.label1.label2` (e.g. `resource.aws_instance.web`) | none captured (`module { source = "./x" }` deferred — see rationale below) |
| Dockerfile | named build stages (`FROM x AS name`) → `class`; `ARG NAME=...` → `variable` | none (`COPY --from=<stage>` references a stage, not a path) |
| Protobuf   | `message`/`service`/`enum` → `class`; `rpc` inside `service` → `method`, qualified under the service | `import "x.proto"` → raw string, unresolved |
| GraphQL    | `object_type_definition`/`interface_type_definition`/`enum_type_definition` → `class`; `field_definition` inside a type → `method`, qualified under the type | none (no cross-file import in the base SDL spec) |

All four grammars verified end-to-end through the actual
`extract_symbols_ts`/`extract_imports_ts` functions (not just raw
grammar probes) before locking in query files. 6 new unit tests added
to `tests/test_tree_sitter_backend.py`, all passing alongside the 20
pre-existing tests (26 total, zero regressions).

No import resolution was attempted for any of the four languages this
pass (all raw-string edges, same treatment PHP's `use` gets today).
This is a deliberate consequence of this session's Tier 4 finding
(`opt-diagnosis.md`): import resolution without graph-IDF weighting
regressed PHP's dense monorepo graph by flooding hub files. Terraform's
`module { source = "./x" }` is a real cross-file reference but is
explicitly deferred for the same reason.

## Benchmark repos

Every repo choice in `benchmarks/public-repos.toml` was verified
against real commit history before locking in — three of the four
original candidates were rejected after measurement and swapped, same
"verify actual yield, don't guess" discipline as the earlier
spring-boot → spring-petclinic swap.

| Language | Repo | Cases | Recall | Token precision | reason_graph | reason_content | reason_symbol | Median wall |
|---|---|---|---|---|---|---|---|---|
| Terraform | terraform-aws-modules/terraform-aws-eks | 20 | 24.2% | 15.0% | 6.25% | 15.8% | 0.0% | 0.39s |
| Dockerfile | docker-library/python | 20 | 6.7% | 50.0% | — | 100% | 0.0% | 0.15s |
| Protobuf | istio/api | 30 | 10.0% | 3.33% | 15.6% | 3.57% | 0.0% | 1.8s |
| GraphQL | saleor/saleor | 11 | 3.0% | 1.1% | 1.3% | 1.1% | 1.3% | 116.5s |

Terraform and Dockerfile numbers are from two identical runs (stable).
Protobuf is from two identical runs on `istio-api` after `googleapis`
was rejected (see below). GraphQL is a single run given per-case cost
(see the wall-time finding below) — thresholds in `thresholds.toml`
are set with extra headroom versus the usual ~2pp-below-observed
convention until a second run is affordable.

### Repo selection: rejected candidates and why

**Dockerfile — `docker-library/official-images` rejected.** Looked
like the obvious pure-Dockerfile repo, but it only stores manifest
pointer files (`GitCommit:`/`Directory:` metadata pointing at *other*
repos) — zero real Dockerfiles. Swapped to `docker-library/python`,
which has real multi-stage Dockerfiles with substantial version-bump
history. Its low recall (6.7%) is structural, not a ranking bug: most
commits touch 6-8 near-duplicate per-variant Dockerfiles
(alpine/bookworm/slim/trixie/windows/...) differing only in a version
string — correctly ranking one variant while missing its siblings is
expected.

**Protobuf — `googleapis/googleapis` rejected.** 7,000+ `.proto`
files with commit history dominated by bulk, auto-generated pushes
spanning unrelated API families. Scanning the full tree per sampled
commit took minutes; narrowing `include_globs` to two subtrees
(`google/spanner`, `google/firestore`, ~53 files) didn't help because
`include_globs` only filters *which commits get sampled* — it does
not restrict the actual per-case scan (see the wall-time finding
below for why that matters). Even `sample_history=400` only found 2
usable cases, since so few commits in that window stayed under
`max_changed_files=8` while touching those directories. Swapped to
`istio/api` — a real, actively-maintained pure-protobuf-schema repo
(109 files) with genuine single/few-file engineering commits. 30/30
requested cases sampled cleanly, twice.

**GraphQL — `99designs/gqlgen` tried and rejected.** Has 170
independently-edited `*.graphql`/`*.graphqls` files (a genuinely
better shape than saleor's single generated schema), and cases ran in
seconds. But it's a schema-first *codegen tool*: editing a
`schema.graphql` fixture regenerates hundreds of `generated_*.go`
files in the *same* commit (11 of 13 schema-touching commits in the
120-commit clone window touched 11–1,180 total files). No
`max_changed_files` value makes this repo usable without accepting
mostly-generated-code diffs. `saleor/saleor` was the final choice,
scoped to `saleor/graphql/**/*.py` + `saleor/graphql/schema.graphql`
(not the original repo-wide `**/*.py`, which was a mistake — 4,284
files, minutes/case for no benefit). Real per-commit file counts in
this scope were checked directly against history before locking it
in (most commits stay under 8 files; a few outliers like a 1,513-file
merge commit are naturally excluded by `max_changed_files=8`).

## Finding: pre-existing ranking-pipeline scaling issue, surfaced by GraphQL

GraphQL's 116.5s median wall time (vs. 0.15–1.8s for the other three
languages) is not a benchmark-repo problem or a bug in the new query —
it's a **pre-existing performance cliff in `ranking.py` that this PR's
GraphQL query happens to trigger for the first time**, because it's the
first tree-sitter query in the codebase capturing one file's *every
single field* as its own qualified symbol.

Isolated by direct profiling of a single case
(`PackPlanner().plan()` against the actual `saleor` checkout,
`cProfile`, not guesswork):

- One case: 110s with `schema.graphql` present, 14s with it removed
  from the same checkout — the file alone adds ~96s.
- `schema.graphql` is 38,093 lines and produces 5,759 GraphQL symbols
  (verified via `extract_symbols_ts` directly: 0.07s to parse and
  extract — tree-sitter itself is fast).
- Profiler hotspot: `ranking.py`'s `boost_paired_tests` →
  `_test_matches_source`, called 1,749,623 times for this one case,
  cascading into ~3.9M `pathlib.Path` constructions. This is
  pre-existing test-pairing logic, unrelated to symbol extraction
  itself, but its cost scales with something in the packable file /
  symbol set that 5,759 same-file symbols blow up disproportionately
  (most other languages' files rarely exceed low hundreds of symbols).

This is a real, worth-fixing issue, but it's a `ranking.py` change —
out of scope for this PR, which is scoped to the tree-sitter backend,
query files, `dependency_graph.py`, and `scanner.py`
(`transient-leaping-milner.md` plan). Flagged here as a concrete
follow-up rather than silently worked around: any real-world project
with one very large, hand-authored or generated schema/config file
(a common GraphQL pattern — e.g. a single checked-in combined schema)
will hit the same cliff regardless of language.

## Verdict

All four languages: symbol/import extraction is correct (verified via
direct probes + unit tests + `--misses` inspection), backward
compatible (zero regressions on the existing java/ruby/php/python/js/
ts/go suite), and produces non-trivial ranking signal (Terraform and
Protobuf show real `reason_graph`/`reason_content` lift; GraphQL's
`reason_symbol_precision` is non-zero, meaning the new capture is
already being used by the ranker, just on a repo/task mix too sparse
to move recall much yet). Recall numbers across all four are modest —
consistent with this project's now-established lesson (first observed
on Java/spring-petclinic) that adding structural symbols alone doesn't
automatically move ranking-quality metrics; it took Tier 2/3
(keyword enrichment, task classification) to unlock real gains on the
first three languages, and the same follow-up work likely applies here
once there's benchmark budget for it.
