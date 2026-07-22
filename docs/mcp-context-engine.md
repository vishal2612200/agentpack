---
title: MCP Context Engine
description: AgentPack exposes local repo context through MCP so AI coding agents can request fresh task-specific files, rules, skills, commands, and warnings.
---

# MCP Context Engine

AgentPack exposes local repo context through CLI and MCP workflows. The MCP path lets an AI coding agent request task-specific context instead of relying on one large static prompt.

Use AgentPack as an MCP context engine when an agent needs fresh local repository context for a concrete task.

## Core MCP flow

1. `readiness()` proves the current host can call AgentPack MCP tools and returns version/tool status.
2. `start_task(task)` writes the task and creates a ranked context pack.
3. `get_context()` returns the latest pack and refreshes once when the task or repo snapshot changed.
4. `get_task_map()` returns the latest risk-aware task map without loading the full pack.
5. `retrieve_context(...)` restores selected, symbol, or omitted context from pack-registry refs.
6. `route_task(task)` returns a read-only route: relevant files, rules, skills, commands, safety warnings, and an agent prompt.
7. `get_skill(name_or_path)` loads one recommended skill's `SKILL.md` content on demand.

For the learning loop, call `learning_recommendations()`,
`learning_start(topic_id)`, and `learning_complete(session_id, proof)`. These
tools share the CLI/dashboard services. The host agent performs coaching and
rubric evaluation; AgentPack validates deterministic evidence and derives
competency status without a hosted model dependency.

This keeps the first prompt smaller while still letting the agent retrieve more detail when needed.

## Task map and reversible refs

Every fresh pack now records a Task Map v1 in `.agentpack/pack_metadata.json`.
Each row gives a file path, selection reason, advisory risk level, related test
paths, likely impact, and a `retrieve_ref` when the file is recoverable from the
latest pack registry.

Use `get_task_map(format="toon")` as the normal follow-up after readiness when
the context is fresh. Use `retrieve_context(block_id="...")` or
`retrieve_context(targets=[...], kind="omitted")` when the task map points at a
specific selected or omitted file. Full-file retrieval still refuses stale file
hashes unless `allow_stale=true`.

## Local-first design

AgentPack does not need cloud indexing, hosted LLM calls, embeddings, or a vector database for scan, summarize, rank, pack, stats, or benchmark. It uses repo-local signals such as paths, symbols, imports, tests, git changes, summaries, and configured rules.

## Try the router

```bash
agentpack route --task "fix billing webhook retry handling"
```

Use JSON output when wiring results into scripts:

```bash
agentpack route --task "fix billing webhook retry handling" --json
```

Debug skill routing directly:

```bash
agentpack skills recommend --task "fix billing webhook retry handling" --explain
```

## Why MCP helps

MCP is useful when agents otherwise spend turns searching, opening files, and refreshing stale context manually. AgentPack keeps that selection step explicit, local, and measurable.

MCP also lets agents fetch context after the working tree changes. Static markdown can go stale; AgentPack includes freshness metadata and can refresh when task text, git state, or repo snapshots drift.

## What AgentPack can return

An MCP route or context response can include:

- selected files and render modes
- risk-aware task-map rows with tests, impact, and retrieve refs
- omitted files and selection receipts
- likely tests and suggested commands
- repo-local rules and skills
- safety warnings
- token stats and freshness metadata

The coding agent still owns source inspection, edits, and verification.
