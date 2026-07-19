# AgentPack

<p align="center">
  <img src="docs/assets/agentpack-symbol.png" alt="AgentPack symbol: a compact map pack for coding agents" width="160">
</p>

<p align="center">
  <strong>Make AI coding work easier to understand, verify, and continue.</strong>
</p>

<p align="center">
  AgentPack is the local, agent-neutral reliability layer for AI software development.
</p>

<p align="center">
  It gives the coding agents you already use task-aware project context, visible workflow state,<br>
  validation guidance, review evidence, and continuity across sessions.
</p>

<p align="center">
  <a href="#get-started"><strong>Get started</strong></a>
  &nbsp;&middot;&nbsp;
  <a href="docs/index.md">Technical docs</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/agentpack-cli/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/agentpack-cli.svg?cacheSeconds=300"></a>
  <a href="https://www.npmjs.com/package/@vishal2612200/agentpack"><img alt="npm version" src="https://img.shields.io/npm/v/@vishal2612200/agentpack.svg?cacheSeconds=300"></a>
  <a href="https://github.com/vishal2612200/agentpack/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/vishal2612200/agentpack?label=release"></a>
  <a href="https://github.com/vishal2612200/agentpack/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/vishal2612200/agentpack/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0.en.html"><img alt="License: AGPL v3" src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg"></a>
</p>

<p align="center">
  <img src="docs/assets/dashboard/workspace-desktop.png" alt="AgentPack workspace showing repository context, active work, proof, and project state" width="1100">
</p>

<p align="center"><sub>One local workspace for repository context, active work, review evidence, and continuity.</sub></p>

## The Missing Project Layer

AI coding models can generate code, but a software project is more than the
files in a repository. The work also depends on the current task, ownership
boundaries, project rules, prior decisions, expected tests, current changes,
and review state. Those details do not automatically follow work between
agents or sessions.

AgentPack keeps that project evidence in one local system and presents a
task-relevant view around the agent you choose. It prepares context, tracks
workflow state, exposes validation and review evidence, and carries structured
handoffs forward. It is not a coding agent, a hosted context index, or a
correctness oracle.

## One Product Across the Workflow

| Moment | What AgentPack contributes |
|---|---|
| **Prepare** | Ranks likely relevant files and tests, applies repository rules and matching skills, surfaces commands and warnings, maps semantic relationships, and records why candidates were included or omitted. |
| **Coordinate** | Keeps task and session state, freshness, token guidance, overlap warnings, dashboard visibility, and host-neutral handoff and resume state together. |
| **Verify** | Provides focused validation guidance, citation-backed staged review, and a PR comment loop that validates, plans, fixes, verifies, and prepares cited replies. |
| **Continue** | Records local advisory memory and observer signals, captures learning feedback, audits agent skills, and generates balanced trigger and non-trigger evaluation sets. |

AgentPack does not edit source code or replace the developer's chosen agent.
The agent still reads the code, makes the change, runs the checks, and exercises
judgment. AgentPack makes the surrounding project state explicit and
inspectable.

## For Developers and Teams

**Developers** get a concrete starting map, focused validation guidance, and a
structured way to carry task state into the next session without replacing
normal search, source inspection, or tests.

**Technical leads and teams** can inspect consistent local task context,
workflow state, overlap warnings, and cited review artifacts across supported
agent entry points. Coordination remains advisory; AgentPack does not claim
hard governance or enforcement across hosts.

## Works With Existing Agents

AgentPack supports Codex, Claude Code, Cursor, Windsurf, Antigravity, MCP
clients, CI, and generic Markdown workflows. Integration depth varies by host;
each surface is an entry point into the same local CLI and MCP engine rather
than a separate product.

See [integrations](docs/integrations.md) for setup, capabilities, and explicit
advisory-versus-enforced status by host.

## Local and Auditable

- Core scan, route, pack, stats, explain, and benchmark operations do not require hosted indexing, embeddings, or model API calls.
- Generated context, receipts, task state, and memory are stored locally under `.agentpack/` so they can be inspected or removed.
- Explicit GitHub operations, optional issue enrichment, and workflows that invoke external agents can use the network.
- Generated context, memory, observer state, and integration hints are advisory. Source files, diffs, tests, runtime evidence, and PR state remain the source of truth.

Read the [privacy model](docs/privacy.md), [technical architecture](docs/architecture.md),
[data flow](docs/data-flow.md), and [known limitations](docs/limitations.md).

## Proof, Not Promises

AgentPack's current public benchmark measures file selection against files
changed in historical public commits. It is evidence for the quality of a
ranked starting map, not a measurement of downstream agent outcomes.

| Public file-selection signal | Current result |
|---|---:|
| Historical commit cases | 107 |
| Average recall | 67.2% |
| Average token precision | 50.6% |

Source: [`benchmarks/results/2026-07-06-public.md`](benchmarks/results/2026-07-06-public.md).
Methodology: [benchmarking guide](docs/benchmarking.md).

These results do not establish reduced tool calls, lower cost, faster
completion, or improved task success. No public AgentPack-versus-no-AgentPack
E2E outcome report is published yet; progress is tracked in the
[E2E A/B status](benchmarks/results/e2e-ab-status.md).

## Get Started

AgentPack requires Python 3.10 or newer. Install the CLI with `pipx`, then
activate it inside a repository:

```bash
pipx install agentpack-cli
agentpack quickstart
agentpack start "fix auth token expiry"
agentpack next
```

`quickstart` initializes the local project layer, `start` records the active
task, and `next` asks AgentPack for the current safe action. MCP-capable agents
can use the same local state directly after integration setup.

<p align="center">
  <img src="docs/assets/agentpack-demo.gif" alt="Terminal demo of AgentPack context, review, learning, memory, and validation workflows" width="840">
</p>

<p align="center">
  <a href="docs/assets/agentpack-demo.mp4">Watch the MP4 demo</a>
</p>

## Technical Docs

Start with the [technical documentation](docs/index.md), then use the
[architecture](docs/architecture.md), [command reference](docs/commands.md),
[integration guide](docs/integrations.md), [privacy model](docs/privacy.md),
and [limitations](docs/limitations.md) for implementation detail.

## Status

Alpha: `0.4.0`.

Python and JavaScript/TypeScript currently have the strongest support. APIs may
change before 1.0. Platform targets are macOS, Linux, and Windows PowerShell
with Git for Windows.

PyPI package: `agentpack-cli`. npm package: `@vishal2612200/agentpack`. CLI
command: `agentpack`. This project is unrelated to AgentPack dataset papers or
other repositories with the same name.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, validation, and pull request
expectations. Community behavior is covered by
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

GNU Affero General Public License v3.0
