# Runtime Loop

AgentPack remains a local repo context router. The runtime loop adds bounded
features around that router without becoming a provider proxy.

| Need | AgentPack surface |
|---|---|
| Inspect risk, tests, impact, retrieve refs, and memory influence for latest pack | MCP `get_task_map` and `agentpack dashboard` |
| Retrieve selected, symbol, or omitted context after a pack | `agentpack retrieve` and MCP `retrieve_context` |
| Record cheap task memory while work starts or finishes | `agentpack work` and `agentpack finish` |
| Recommend three project-grounded topics and coach one | `agentpack learn [<request>]`, `agentpack learn --global`, and `@agentpack-learn` |
| Record learning feedback | `agentpack learn feedback helpful|not-helpful` |
| Inspect advisory observer relationships | `.agentpack/observer-brief.md` and `agentpack dashboard` |
| Track local token and retrieval activity | `agentpack perf --history N` and `agentpack stats` |
| Launch an agent after context refresh | `agentpack wrap` |
| Run an optional guarded proof harness around an external agent | `agentpack work --run` and `agentpack finish` |
| Summarize noisy logs without losing failures | `agentpack compress-output --kind pytest|git-diff|rg|ls` |
| Inspect recent local task memory | `agentpack memory` |

## Project Work-Learn Layer

The four-command loop feeds one project model rather than a set of isolated
task reports:

```text
Project -> Outcomes -> Initiatives -> Tasks -> Evidence
```

`agentpack work` and `agentpack finish` keep task and validation evidence
current. `agentpack learn` turns bounded work and mastery history into the next
evidence-backed topics. `agentpack doctor` checks the local installation and
integration surface. Existing specialized commands remain available.

Shared project definitions live under `[project]` in committed
`.agentpack/config.toml`. Dynamic outcome and milestone statuses, risks,
decisions, and initiative confirmations stay local as append-only `project_*`
session events. Dashboard reads do not write events or affect learning
recommendation cooldowns.

Project views aggregate bounded AgentPack artifacts from accessible worktrees in
the same Git repository. Health dimensions remain independent: missing evidence
is `unknown`, stale evidence is `stale`, and no composite score is calculated.
The dashboard can copy or download deterministic Summary and Engineering status
briefs without creating repository files.

## Compress, Cache, Retrieve

The runtime loop keeps the first context pack small and reversible:

- **Compress** repo files into budget-aware pack views and compress noisy command output into failure-focused summaries.
- **Cache** snapshots, summaries, pack metadata, registry records, session events, and learning feedback locally under `.agentpack/`.
- **Map** selected and omitted files into Task Map v1 rows: why selected, advisory risk, related tests, likely impact, and retrieve refs.
- **Visualize** the same local evidence through project Overview, Roadmap, Work, Health, and Knowledge views, with Activity available from Overview and task-scoped graph and context tools under Explore.
- **Retrieve** precise file, symbol, or omitted context from the latest pack registry through `agentpack retrieve` or MCP `retrieve_context`.

Rendered packs are prompt-cache friendly by default. Every markdown and compact
context artifact starts with the same stable instructions and mode legend before
task text, timestamps, git state, freshness JSON, selected files, or command
output. Providers with automatic prompt-prefix caching can reuse that prefix
across refreshes without users selecting a separate render mode.

## Compressor Types

| Compressor | What it compresses | User-facing surface |
|---|---|---|
| Context mode compression | Repo files into `full`, `diff`, `symbols`, `skeleton`, or `summary` views | `agentpack pack` |
| Diff hunk compression | Large changed-file diffs into task-relevant hunks | `agentpack pack` |
| Rendered-budget compression | Receipts, repo map, delta, runtime detail, conflicts, omitted files, then selected files | `agentpack pack` |
| Test log compression | Failures, assertions, and test summaries from noisy test output | `agentpack compress-output --kind pytest|test|npm|vitest|jest` |
| Diff output compression | Diff headers and hunks from patch output | `agentpack compress-output --kind git-diff|diff|patch` |
| Search output compression | File/line matches from grep-style output | `agentpack compress-output --kind rg|grep|search` |
| Listing compression | Head/tail samples from long listing or tree output | `agentpack compress-output --kind ls|find|tree` |
| Generic output compression | Failure lines, paths, diffs, repeated lines, or edge samples for unknown output | `agentpack compress-output --kind auto` |

## Boundaries

AgentPack does not proxy LLM traffic, rewrite provider requests, or replace raw
logs as source of truth. Retrieval uses the latest local pack registry, supports
symbol-level and omitted-file block IDs when the latest pack contains them, and
refuses stale full-file reads unless explicitly allowed. Task-map risk levels
are advisory routing hints, not proof that a file is safe or unsafe.

`agentpack work --run` is optional. It is a guarded proof harness around an
external coding agent, not the main AgentPack workflow and not a fully
autonomous coding agent. AgentPack owns context refresh, phase tracking, diff snapshots,
verification gates, repeated-failure detection, risk review, rollback patches,
acceptance evidence, handoff notes, progress files, and finish blockers. The
configured runner still owns code generation. Runner commands may emit a final
JSON line with `status`, `summary`, `files_changed`, and `blocker`; AgentPack
uses that contract to stop cleanly on `blocked` or `no_change`.

Loop diagnostics live in `.agentpack/loop_diagnosis.md`. Handoffs live in
`.agentpack/loop_handoff.md`, acceptance evidence in
`.agentpack/loop_acceptance.md`, risk notes in `.agentpack/loop_risk_review.md`,
and rollback patches in `.agentpack/loop_rollback/`. Use these files, plus
`.agentpack/loop_events.jsonl` and `.agentpack/loop_failures.jsonl`, to inspect
why a loop stopped before rerunning the agent.

Every loop writes `.agentpack/loop_runner_prompt.md` for provider-safe runner
instructions: read context, keep edits scoped, avoid commits/pushes/destructive
commands, run no hidden approval flow, and emit the final JSON contract.
Historical outcomes are appended to `.agentpack/loop_metrics.jsonl`; inspect
them with `agentpack loop-metrics` or the dashboard cockpit.

`agentpack work` also appends bounded `task_memory` facts to
`.agentpack/session-events.jsonl` and mirrors an advisory observer event to
`.agentpack/observer-events.jsonl`. This stays on the fast path: no provider
calls, no dashboard rendering, and no generated lesson. `agentpack learn` and
the plugin read task facts later when the developer explicitly asks to learn,
quiz, interview, or debug from recent work.

Task starts also write `.agentpack/task-starts.jsonl`. That record is the map
before the drive: task text, agent/thread identity, branch, git SHA, dirty-file
baseline, selected files, context-pack hash, and symbol/node references from the
latest pack registry. Later task events and episodes can refer back to that
start snapshot without treating it as truth after the repo changes.

AgentPack's memory graph is append-only and advisory:

- **Node refs** identify code locations using file path, symbol, source hash,
  content hash, and a stable `node_id` where the pack registry captured a
  symbol.
- **Task events** are bounded travel-log facts about reads, edits, decisions,
  failures, and validation.
- **Episodes** summarize completed work with changed files, checks, touched
  nodes, final hashes, and outcome.
- **Procedures** in `.agentpack/procedures.jsonl` are reusable playbooks linked
  to validated episodes. They can suggest a route when task terms and current
  code locations match prior work.
- **Memory edges** in `.agentpack/memory-edges.jsonl` connect nodes, episodes,
  and procedures with provenance, relationship hash, confidence, source hash,
  and a visible reason.
- `agentpack memory --timeline` joins task starts, episodes, procedures, and
  edges into timestamped rows for ordering, version analysis, stale-path checks,
  and relationship inspection.

The trust order is deliberate: live source and tests outrank current diff,
task-start snapshots, episodic memory, and procedures. Memory can boost ranking
or explain why context was included, but it cannot replace `rg`, `git diff`,
direct file reads, tests, or PR evidence. Set `AGENTPACK_MEMORY_FEEDBACK=off` or
`[context].memory_feedback = "off"` to disable memory-based ranking.

The observer layer relates route selections, task memory, learning output, and
review outcomes into a small local brief at `.agentpack/observer-brief.md`.
Those relationships are hypotheses: they can suggest files that prior similar
tasks changed, call out selected-file misses, or highlight repeated learning
concepts. They do not replace `rg`, `git diff`, direct file reads, tests, or PR
review evidence.

`agentpack learn` ranks bounded `now`, `weak_spot`, and `breadth` topics against
seven stable engineering competencies. Starting a topic appends a queued session
to `.agentpack/learning-sessions.jsonl`; queued sessions remain unassessed until
the host agent evaluates every rubric point and submits structured proof. Changed
files and concepts are exposure only. AgentPack derives mastery across readable
registered projects while answers remain project-local. The dashboard reads the
same recommendation and competency contract without recording an impression, so
viewing it does not affect cooldown ranking.
