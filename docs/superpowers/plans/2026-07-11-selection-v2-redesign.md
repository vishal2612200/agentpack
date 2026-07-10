# AgentPack Selection V2 Redesign Implementation Plan

## Goal

Replace the coupled greedy selector with typed ownership evidence, a constrained
allocator, and span-level packing. V2 remains a shadow engine until the frozen
release suite improves recall by at least two percentage points without a
precision regression and paired E2E trials show a statistically positive result.

## Constraints

- Build from the benchmark foundation merged by PR #77.
- Add no solver dependency and never read benchmark labels at runtime.
- Keep `PackRequest`, `SelectedFile`, `ContextPack`, MCP, and configuration
  contracts unchanged.
- Keep V1 as the production default until all promotion gates pass.
- Preserve legacy V1 selected paths and benchmark metrics while evaluation-only
  contracts are introduced.

## Phase 1: Typed Evidence And Evaluation Contract

1. Add internal selection models for engines, evidence, representation options,
   decisions, constraints, plans, and traces.
2. Adapt existing `(FileInfo, score, reasons)` candidates into typed evidence
   without changing V1 receipts.
3. Extend frozen cases with reviewed `action_owner_files`,
   `required_support_files`, `incidental_changed_files`, and
   `optional_context_files` labels.
4. Validate that action, support, and incidental labels partition
   `expected_files`; optional context must be disjoint and present in the parent
   checkout.
5. Add owner recall, support recall, useful-context precision, and incidental
   diagnostics while preserving legacy recall and token precision exactly.

Stop if legacy V1 JSON metrics or selected paths change.

## Phase 2: Ownership And Support Inference

1. Add `analysis/ownership.py` with deterministic `build_candidate_evidence`.
2. Consume task keywords, typed summaries, dependency direction, path scope,
   changed state, test pairing, and memory evidence only.
3. Model owner, support, and carrier strengths independently and attach stable
   evidence and protection codes.
4. Protect changed files, memory-confirmed owners, release metadata, generated
   or redaction-sensitive files, and explicit task tests.
5. Emit benchmark-only evidence diagnostics without changing selection.

Stop unless classifications are deterministic and no protected file is
misclassified across the audited lock.

## Phase 3: Deterministic Shadow Allocator

1. Add a bounded beam-search allocator with width 512.
2. Search the top 200 ranked files plus protected and strong-owner candidates.
3. Generate at most four representations plus drop for each candidate.
4. Preserve mandatory files, token budget, family and scope guards, and ensure
   shadow V2 selects no more files than V1.
5. Optimize lexicographically for protected coverage, owner coverage, support
   coverage, carrier-token reduction, total-token reduction, then rank and path.
6. Emit stable blocker codes and expose a maintainer-only
   `benchmark --compare-selection-v2` path.

Stop if allocator p95 exceeds 100 ms, planning time rises over 5 percent, or
frozen recall does not improve without precision loss.

## Phase 4: Exact Span Packing

1. Add a V2 span packer without moving V1 compaction.
2. Build options from headers/imports, matching definitions, enclosing control
   flow, callers, and related tests.
3. Preserve complete owner definitions and memory-confirmed spans; narrow only
   carrier excerpts.
4. Measure rendered options through the active adapter and reserve fixed pack
   overhead before allocation.
5. Render once and re-solve if over budget; never opportunistically remove the
   final selected file.
6. Preserve citations, redaction, and source hashes for generated spans.

Stop on selected-file loss or protected-owner token loss during final rendering.

## Phase 5: E2E Proof And Promotion

1. Add `agentpack-v1` and `agentpack-v2` E2E strategies.
2. Support reproducible `repo_url` and `base_commit` materialization while
   retaining local repository compatibility.
3. Commit 21 reviewed cases: one single-owner, one source-plus-test, and one
   config/release or cross-package case per release repository.
4. Run three paired trials per case with identical model, prompt, timeout, and
   checkout, using repository-native validation.
5. Report exact McNemar significance with the standard library.

Promote V2 only when every release run improves average recall by at least two
percentage points, does not reduce average or aggregate token precision,
increases F1, does not increase total packed tokens, and causes no repository
regression. E2E task success must improve by at least five percentage points with
exact paired McNemar `p < 0.05`, no validation or repository regression, and no
more than five percent median token growth.

If either gate fails, publish only diagnostic and evaluation changes and retain
V1 as the production default.

## Validation

```bash
pytest tests/test_selection_models.py tests/test_ownership.py tests/test_selection_v2.py tests/test_span_packer.py -q
pytest tests/test_context_pack.py tests/test_benchmark.py tests/integration/test_pack_pipeline.py -q
python -m ruff check src/agentpack tests
python -m mypy src/agentpack/analysis src/agentpack/core/selection_models.py src/agentpack/core/selection_v2.py
pytest -q
```

Run V1 and V2 three times against the identical frozen lock before any promotion
decision. Retain V1 internally for at least one release after promotion.
