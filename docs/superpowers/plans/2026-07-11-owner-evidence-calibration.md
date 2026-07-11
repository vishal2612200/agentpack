# Action-Owner Evidence Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise strong action-owner recall from 49.61% to at least 65% while maintaining at least 95% leave-one-repository-out precision, then test one conservative owner-over-carrier replacement without a global allocator.

**Architecture:** Replace independent candidate classification with deterministic comparative ownership evidence. Runtime features remain label-free; audited labels are benchmark-only. V1 remains production unless every evidence and frozen-suite gate passes.

**Tech Stack:** Python 3.10+, dataclasses, Pydantic, standard library, existing benchmark and ranking pipeline.

## Global Constraints

- Add no model, solver, or runtime dependency.
- Keep PackRequest, SelectedFile, ContextPack, MCP, and configuration contracts unchanged.
- Do not change ranking, candidate expansion, representations, compaction, or budgets.
- Runtime code must never read benchmark labels.
- V1 remains production throughout this plan.

---

### Task 1: Comparative Owner Features

- [x] Add typed owner case context and feature vectors.
- [x] Extract task objects from the existing keyword plan.
- [x] Count competing definition, literal-definition, and entrypoint anchors across the top 50 candidates.
- [x] Keep structural corroboration and penalties independent and deterministic.
- [x] Cover the observed Vite and MarkupSafe false positives.
- [x] Commit `feat(selection): add comparative owner features`.

### Task 2: Deterministic Owner Rule

- [x] Classify strong owners only from direct anchors plus comparative structural corroboration.
- [x] Preserve independent support, carrier, and protection evidence.
- [x] Emit stable comparative owner and penalty codes.
- [x] Commit `feat(selection): calibrate comparative owner evidence`.

### Task 3: Calibration Report

- [ ] Add repository identity and feature data to benchmark JSONL.
- [ ] Add `benchmark --owner-evidence-report <jsonl>`.
- [ ] Report micro, per-repository, strength-level, path-family, availability, false-positive, missed-owner, and protection metrics.
- [ ] Require strong-owner recall >=65%, leave-one-repository-out precision >=95%, every repository precision >=90%, no repository recall regression, zero protection errors, and deterministic V1 output.
- [ ] Commit `test(benchmark): add owner evidence calibration report`.

### Task 4: Conservative Replacement Treatment

- [ ] Proceed only if Task 3 gates pass.
- [ ] Permit at most one same-family, same-scope, token-neutral strong-owner replacement per case.
- [ ] Preserve file count, protected incumbents, representations, ranking, and budgets.
- [ ] Expose only through hidden `--compare-owner-selection`.
- [ ] Commit `perf(selection): add conservative owner replacement experiment`.

### Task 5: Frozen Validation And Decision

- [ ] Run focused tests, Ruff, mypy, and the known repository-wide checks.
- [ ] Run V1 and treatment three times against the identical lock.
- [ ] Ship treatment only for >=2 percentage-point recall gain, no precision/F1/repository/case regression, no token increase, unchanged candidate R@50 and file count, zero protected replacement, p95 <20 ms, and planning overhead <5%.
- [ ] If any gate fails, remove treatment and retain calibration/reporting only.
