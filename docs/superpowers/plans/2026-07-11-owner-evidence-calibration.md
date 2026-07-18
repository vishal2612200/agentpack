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

- [x] Add repository identity and feature data to benchmark JSONL.
- [x] Add `benchmark --owner-evidence-report <jsonl>`.
- [x] Report micro, per-repository, strength-level, path-family, availability, false-positive, missed-owner, and protection metrics.
- [x] Evaluate strong-owner recall >=65%, leave-one-repository-out precision >=95%, every repository precision >=90%, no repository recall regression, zero protection errors, and deterministic V1 output.
- [x] Stop after the first frozen run: audited strong-owner precision was 98.4%, but recall was 39.0%; ItsDangerous and Spring PetClinic recall regressed versus the legacy rule.
- [x] Commit `test(benchmark): add owner evidence calibration report`.

### Task 4: Conservative Replacement Treatment

- [x] Do not proceed because Task 3 gates failed.
- [ ] Permit at most one same-family, same-scope, token-neutral strong-owner replacement per case.
- [ ] Preserve file count, protected incumbents, representations, ranking, and budgets.
- [ ] Expose only through hidden `--compare-owner-selection`.
- [ ] Commit `perf(selection): add conservative owner replacement experiment`.

### Task 5: Frozen Validation And Decision

- [x] Run focused tests, Ruff, mypy, and the known repository-wide checks.
- [x] Stop frozen validation after the first V1 evidence run failed the Task 3 calibration gate; no treatment exists to repeat.
- [x] Do not ship a treatment because owner recall and per-repository non-regression gates failed.
- [x] Retain calibration/reporting only and leave V1 production selection unchanged.

Validation notes:

- 284 focused tests passed and changed-file Ruff passed.
- Mypy remains blocked by the existing duplicate `agent_lessons` definition in `core/models.py`.
- Full pytest remains blocked by unchanged main-branch dashboard, license, performance, and browser-smoke failures.
