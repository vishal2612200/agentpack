# How AgentPack Works

AgentPack is a local context router. It does not upload your repo or require embeddings to build a pack. The default path is deterministic and offline: scan the working tree, rank likely-relevant files, compress them into a budget, cache expensive local work, and let agents retrieve more detail when needed.

## Why These Layers Exist

AgentPack is designed around a simple constraint: agents should not start by
rediscovering the project from scratch, but they also should not trust stale
generated context as truth.

That is why the system separates four responsibilities:

- **Orientation**: route, pack, and explain point at likely files, tests, skills, and commands.
- **State control**: `next`, `status`, `guard`, and MCP readiness decide whether the current task/context/session is safe to use.
- **Token control**: pack metadata records a token contract so agents can prefer delta or targeted retrieval when full context is unnecessary.
- **Learning control**: memory, learn, review, and observer flows record bounded local evidence for future orientation without making it authoritative.

The result should feel less like "ask the model to remember everything" and
more like a local flight checklist for each task: what is the task, which
session owns it, what context is fresh, what changed, what is worth reading, and
what proof still needs to be checked.

## Pipeline

1. **Scan**

   AgentPack reads packable files after `.agentignore` and generated-file filters. It records paths, sizes, language hints, imports, symbols, test relationships, git state, and lightweight repo-map signals.

2. **Rank**

   The ranker scores files against the active task using filename/path matches, symbols, imports, related tests, changed files, repo history, offline summaries, and configuration signals. This produces a prioritized map, not a claim that the top file is always sufficient.

3. **Compress**

   AgentPack chooses a render mode for each selected file:

   | Mode | Use |
   |---|---|
   | `full` | Small or highly relevant files where the body matters |
   | `diff` | Changed files where the current patch is the useful context |
   | `symbols` | Files where signatures and structure are enough to orient |
   | `skeleton` | Large files where names, classes, functions, and calls are enough |
   | `summary` | Low-priority or very large files that still need a breadcrumb |

   The pack is budget-aware: changed files, tests, docs, and direct dependencies get reserve buckets before lower-confidence context.

4. **Cache**

   AgentPack caches local summaries, repo snapshots, pack metadata, and skill indexes under `.agentpack/`. Cache keys include file hashes, schema or generator versions, and source fingerprints so stale context can be detected and refreshed.

5. **Retrieve**

   Packs include block IDs and receipts. Agents can use the generated context as a compact map, then read exact files or use registry-backed retrieval when a summary or skeleton is not enough.

6. **Route**

   `agentpack route --task "..."` and the MCP router return likely files, scoped rules, installed skills, commands, and safety warnings without writing a full context pack. Skill routing uses explicit metadata first, then local text signals such as BM25-style domain scoring and dynamic keyphrase triggers. When local observer history exists, route output may include advisory priors from similar previous tasks; those priors are only a starting hypothesis and must be verified from source.

7. **Observe**

   AgentPack mirrors bounded local events from task memory, route, learn, and review flows into `.agentpack/observer-events.jsonl`. It derives `.agentpack/observer-brief.md` and dashboard cards that explain relationships such as "this file was changed in similar work but was not selected last time." The observer layer is deliberately local and advisory; direct code, diffs, tests, and PR evidence remain the source of truth.

8. **Remember**

   AgentPack records an append-only memory graph under `.agentpack/`: task-start snapshots, node refs, task events, episodes, procedures, and memory edges. This makes the first context pack the map before work starts, while later events become the travel log. Retrieval requires provenance, source hashes, confidence, and visible reasons; stale or failed memory can warn, but only validated current memories can boost future ranking.

9. **Visualize**

   `agentpack dashboard` turns the same local artifacts into a served context cockpit at `127.0.0.1:8765`. The loopback-only Python server provides the normalized snapshot, task-scoped graph, and PTY-backed AgentPack command runner. The view uses packaged assets and keeps graph nodes tied to source paths, retrieve refs, risks, tests, memory evidence, integrations, and suggested next actions.

10. **Measure**

   `agentpack benchmark` scores expected-file recall, token precision, pack size, misses, and skill routing metrics. Benchmark cases can include `expected_skills` and `avoid_skills` to catch weak skill keywords or noisy skill recommendations.

## Stable Prefix Caching

Rendered packs keep stable instructions before volatile data such as timestamps, git SHAs, task text, and selected-file tables. This does not create a provider cache by itself, but it makes repeated prompts friendlier to provider prompt-prefix caching because the beginning of the prompt remains byte-stable across refreshes.

The practical rule is:

- stable instructions first
- volatile task and repo state later
- file blocks in deterministic order
- no random IDs or timestamps in the prefix

This can reduce cost on providers that discount cached prefix reads, while keeping AgentPack provider-agnostic.

## Skill Keyword Quality

Skill discovery stores triggers in `.agentpack/skills_index.json`. AgentPack now prefers description-backed keyphrases over generic single words. For example:

| Weak trigger | Better trigger |
|---|---|
| `any` | `manual-pack` |
| `another` | `transferable-skill` |
| `actionable` | `code-quality-check` |
| `building` | `graphql-schema` |

Use benchmark cases to keep this quality from regressing:

```toml
[[cases]]
task = "review this PR for SQL injection, XSS, and code quality"
expected_skills = ["code-reviewer"]
avoid_skills = ["frontend-review"]

[[cases]]
task = "translate my retail operations experience into a software resume"
expected_skills = ["Career Changer Translator"]
avoid_skills = ["generic-writing"]
```

Then run:

```bash
agentpack benchmark --misses
```

The output and `.agentpack/benchmark_results.jsonl` include `skill_recall_at_3`, `skill_precision_at_3`, `skill_mrr`, `skill_noise_rate`, and `selected_skills`.

## Hybrid Search Direction

The default router should stay dependency-free. A good future shape is hybrid retrieval:

- BM25/keyphrase matching for exact terms such as `graphql`, `sql injection`, or `agentpack`
- optional semantic search when an embedding provider or local vector index is configured
- reciprocal-rank or weighted fusion to merge lexical and semantic candidates
- deterministic fallback to the current local BM25/keyphrase path when embeddings are unavailable

That gives better intent matching without bloating normal installs.
