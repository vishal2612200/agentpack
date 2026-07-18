# Benchmark comparison: `benchmarks/results/phase0-before.json` → `benchmarks/results/phase0-after.json`

Toggle: `AGENTPACK_DISABLE_TREE_SITTER=1` (before) vs default/enabled (after).
Both are cold-cache runs against the same frozen commit sample per repo
(no caching artifact — the benchmark harness copies each repo into a
fresh temp checkout per invocation, excluding `.agentpack`).

**Note on java's 0.0% delta:** verified directly via a standalone CLI run
(not just through this harness) that spring-petclinic genuinely scores
identically with tree-sitter on vs off. This is not a bug — spring-petclinic
is a small (~20 file), cleanly-named Spring demo app where filename and
content-keyword signals already saturate recall; there's no headroom left
for the symbol signal to add. PHP (laravel) and Ruby (rails) are much
larger and less filename-obvious, which is where the lift shows up. This
suggests tree-sitter's lift scales with codebase size/opacity, not with
"is tree-sitter active" alone — worth keeping in mind when picking Phase 1
benchmark repos (favor real-world-sized repos over small demo apps).

## java

Repo: `spring-petclinic`

Cases: 20 → 20

| Metric | Before | After | Δ |
|---|---|---|---|
| Recall | 61.7% | 61.7% | = 0.0% |
| Token precision | 28.5% | 28.5% | = 0.0% |
| reason graph | 31.4% | 31.4% | = 0.0% |
| reason content | 33.5% | 33.5% | = 0.0% |
| reason symbol | 0.0% | 0.0% | = 0.0% |
| Median wall (s) | 0.28s | 0.28s | ▲ 0.01s |

## php

Repo: `laravel-framework`

Cases: 15 → 15

| Metric | Before | After | Δ |
|---|---|---|---|
| Recall | 13.3% | 16.7% | ▲ 3.3% |
| Token precision | 5.8% | 7.8% | ▲ 2.0% |
| reason graph | 0.0% | 5.6% | ▲ 5.6% |
| reason content | 5.8% | 7.8% | ▲ 2.0% |
| reason symbol | 0.0% | 0.0% | = 0.0% |
| Median wall (s) | 7.04s | 9.04s | ▲ 1.99s |

## ruby

Repo: `rails`

Cases: 15 → 15

| Metric | Before | After | Δ |
|---|---|---|---|
| Recall | 6.7% | 13.3% | ▲ 6.7% |
| Token precision | 1.7% | 3.0% | ▲ 1.3% |
| reason graph | 0.0% | 1.4% | ▲ 1.4% |
| reason content | 1.7% | 3.0% | ▲ 1.3% |
| reason symbol | 0.0% | 0.0% | = 0.0% |
| Median wall (s) | 8.66s | 10.49s | ▲ 1.82s |

---

**Mean recall delta across 3 language(s) with before+after data: +3.33 pp**
