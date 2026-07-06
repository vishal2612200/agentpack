---
title: Reduce Claude Code Token Usage
description: How AgentPack reduces repeated repo-orientation context in Claude Code by preparing compact task-specific context packs.
---

# Reduce Claude Code Token Usage

AgentPack can reduce context waste in Claude Code workflows by preparing a compact, task-specific context pack instead of dumping broad repo text into the prompt.

The goal is not a magic fixed reduction percentage. The goal is to avoid repeated orientation work and give Claude the files, tests, rules, and commands most likely to matter for one task.

## What it reduces

- repeated file-search context
- unrelated files in prompts
- stale markdown packs
- manual repo maps rebuilt across sessions
- broad copy-paste context for tasks that need a narrow file set

## What it does not claim

AgentPack does not guarantee a fixed percent token reduction for every repo or task. Token savings depend on repo size, task specificity, ignore rules, and the selected context budget.

AgentPack also does not prove correctness. Claude Code and the reviewer still own source inspection, tests, and final review.

## Safer first command

Use the read-only router first:

```bash
pipx run --spec agentpack-cli agentpack route --task "fix auth token expiry"
```

Then use a full pack when you want a markdown context artifact:

```bash
agentpack task set "fix auth token expiry"
agentpack pack --task auto
```

## Current benchmark framing

The current published public release table reports scoped evidence from pinned public commits. It is useful for regression checks, not universal proof.

| Metric | Result |
|---|---:|
| Cases | 107 |
| Avg recall | 67.2% |
| Avg token precision | 50.6% |
| Pack p50 | 315 tokens |
| Pack p95 | 1,137 tokens |

See [`benchmarks/results/2026-07-06-public.md`](https://github.com/vishal2612200/agentpack/blob/main/benchmarks/results/2026-07-06-public.md) for the full table.

## How to tune token use

Start with specific task text:

```bash
agentpack task set "fix expired refresh token handling in auth middleware"
agentpack pack --task auto
```

If AgentPack selects too much, tighten `.agentignore`, lower budgets, or use route-only output. If it misses files, capture the task as a benchmark case:

```bash
agentpack benchmark capture --since main --task "fix expired refresh token handling in auth middleware"
agentpack benchmark --misses
```

That feedback helps teams measure whether packs are getting smaller and more accurate over time.
