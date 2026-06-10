# AgentPack

[![PyPI version](https://img.shields.io/pypi/v/agentpack-cli.svg)](https://pypi.org/project/agentpack-cli/)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/agentpack-cli?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/agentpack-cli)
[![npm version](https://img.shields.io/npm/v/@vishal2612200/agentpack.svg)](https://www.npmjs.com/package/@vishal2612200/agentpack)
[![npm downloads](https://img.shields.io/npm/dm/@vishal2612200/agentpack.svg)](https://www.npmjs.com/package/@vishal2612200/agentpack)
[![Python versions](https://img.shields.io/pypi/pyversions/agentpack-cli.svg)](https://pypi.org/project/agentpack-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/vishal2612200/agentpack/actions/workflows/ci.yml/badge.svg)](https://github.com/vishal2612200/agentpack/actions/workflows/ci.yml)

**Local MCP context router for AI coding agents.**

Claude Code, Codex, Cursor, and other coding agents can waste tool calls rediscovering your repo before they make the edit you asked for.

AgentPack gives them a ranked map of likely relevant files, tests, rules, and skills for each task. It analyzes your repo locally and packages compact context for CLI and MCP workflows.

No cloud indexing. No embeddings required. No API calls for scan, summarize, rank, pack, stats, or benchmark. AgentPack is a context preparation tool, not a coding agent.

Try the read-only task router without writing context files:

```bash
pipx run --spec agentpack-cli agentpack route --task "fix auth token expiry"
```

![AgentPack route demo](docs/assets/agentpack-route-demo.svg)

> **Status: alpha (v0.3.16).** Works, tested, and used in real sessions. Python and JavaScript/TypeScript are the best-supported languages. Current benchmarks are useful regression checks, not broad proof that AgentPack improves coding-agent success. API may change before 1.0.
>
> **Platform note:** macOS, Linux, and Windows are supported. Windows support targets PowerShell plus Git for Windows. `cmd.exe` and bare Git setups are not a supported path yet.
>
> **Name note:** PyPI package is `agentpack-cli`, npm package is `@vishal2612200/agentpack`, and the command is `agentpack`. This project is unrelated to AgentPack dataset papers or other repos with the same name.

## What's New in 0.3.16

`0.3.16` bundles `watchdog` in normal installs so `agentpack watch` uses native
filesystem events by default instead of polling after `pipx`, `pip`, or npm
wrapper installation.

## What's New in 0.3.15

AgentPack Router now recommends skills with stronger local signals: richer
frontmatter, confidence thresholds, negative triggers, diversity-aware ranking,
and a pull-based MCP `get_skill` flow. Skill benchmark cases can declare
`expected_skills` and `avoid_skills`, and `agentpack skills feedback` records
recommended, used, ignored, and outcome signals for future routing.

## What's New in 0.3.14

AgentPack Learn now covers both sides of AI-assisted development: the coding
agent gets compact future-agent lessons, and the developer gets task-specific
learning notes, skill evidence, and practice follow-up.

- `agentpack learn --provider-command` adds an opt-in local provider bridge:
  AgentPack sends a bounded, redacted report JSON on stdin and accepts
  LearningReport-compatible JSON fields on stdout. No hosted service is called
  unless your command does it.
- `agentpack learn --dashboard` writes a static
  `.agentpack/learning-dashboard.html` for IDE/browser review.
- `agentpack learn --team-export` writes `.agentpack/team-lessons.md`, a
  shareable lesson file that omits personal skill history.
- `agentpack learn --feedback`, `--skills`, and `--drills` close the loop from
  a task summary to skill memory and next-practice prompts.
- `agentpack dev-check` and `agentpack release-check` now print bounded failure
  excerpts, so CI shows the failing test instead of only a red stage name.

## Before vs After

Without AgentPack, a cold coding-agent session often starts with manual repo orientation:

```text
Task: fix auth token expiry

Agent:
- searches for auth files
- opens nearby middleware and config
- may miss related tests
- spends early turns building a repo map
```

With AgentPack:

```bash
agentpack route --task "fix auth token expiry"
```

```text
Task:
fix auth token expiry

Relevant files:
- tests/test_auth.py
- src/app/auth.py
- src/app/users.py

Suggested commands:
- pytest tests/test_auth.py -q
```

## Features

- **Task-focused packing**: ranks files from git changes, task terms, symbols, imports, related tests, configs, churn, repo history, and deterministic offline summaries.
- **Budget-aware compression**: emits `full`, `diff`, `symbols`, `skeleton`, or `summary` views instead of all-or-nothing file dumps.
- **Rendered-token accounting**: budgets against the actual markdown context, not only file payloads.
- **Reserve buckets**: changed files, tests, docs, and dependencies each get protected selection capacity so one dirty area cannot starve the others.
- **Execution state**: optional task state files and git-derived fallback status show whether work is planned, in progress, blocked, done, committed, or committed but not pushed.
- **Thread-scoped context**: explicit `--thread <id>` or `--thread auto` isolates task/context files for multiple agents in one repo and warns on same-branch file overlap.
- **Task router**: MCP and CLI surfaces route a task to relevant files, scoped rules, installed skills, suggested commands, and safety warnings without executing skills automatically.
- **Learning layer**: turns task diffs into developer learning notes, skill evidence, and compact lessons future agents can reuse.
- **Agent integrations**: installs Claude Code, Cursor, Windsurf, Codex, Antigravity, VS Code tasks, git hooks, and MCP configuration.
- **Local and measurable**: no API calls for scan, summarize, rank, pack, stats, or benchmark; quality is measured with expected-file evals.

## Benchmark Proof

Latest public release gate: 8 real commits from Pallets Click, ItsDangerous, and MarkupSafe, scored against files actually changed by each commit.

| Metric | Result |
|---|---:|
| Avg recall | 79.2% |
| Avg token precision | 51.2% |
| Pack p50 | 1,450 tokens |
| Pack p95 | 3,805 tokens |

Full table: [`benchmarks/results/2026-05-27-public.md`](benchmarks/results/2026-05-27-public.md). This is public smoke proof, not a claim of universal ranking quality; expand cases for your own repo with `agentpack benchmark capture`.

## Use Cases

- [Claude Code context engine](docs/claude-code-context-engine.md)
- [MCP context engine](docs/mcp-context-engine.md)
- [Cursor context packing](docs/cursor-context-packing.md)
- [AI coding agent context packing](docs/ai-coding-agent-context.md)
- [Reduce Claude Code token usage](docs/reduce-claude-code-token-usage.md)
- [AgentPack vs Repomix](docs/agentpack-vs-repomix.md)
- [AgentPack vs Augment Context Engine](docs/agentpack-vs-augment-context-engine.md)
- [Docs index](docs/index.md)

## Install

```bash
pipx install agentpack-cli
agentpack --version
```

Requires Python 3.10+. The PyPI package is `agentpack-cli`; the command is `agentpack`. Use `pipx` for normal installs because many macOS/Linux Python distributions block global `pip install` with PEP 668's `externally-managed-environment` error. If you prefer `pip`, install inside a virtual environment.

Install `pipx` first if needed:

```bash
# macOS
brew install pipx

# Ubuntu/Debian
sudo apt install pipx

# Fedora
sudo dnf install pipx

# Arch
sudo pacman -S python-pipx

pipx ensurepath
```

For JavaScript/TypeScript projects you can use the npm wrapper:

```bash
npx @vishal2612200/agentpack --version
npx @vishal2612200/agentpack init --yes
npx @vishal2612200/agentpack pack
```

## Quickstart

```bash
cd your-repo
agentpack init --yes
agentpack work "fix auth token expiry"
```

Then read `.agentpack/context.md` or the agent-specific context file listed in the output. AgentPack also integrates with MCP so agents can call tools directly instead of reading markdown artifacts.

A good explicit workflow is:

```bash
agentpack task set "fix billing webhook retry handling in app/api/billing/route.ts" --guard
agentpack next --fix-all-safe
agentpack status
agentpack finish --since main
```

Use `agentpack quickstart --task "..." --write` when you want AgentPack to print the next commands and write the task file for you. Use `agentpack start "..." --pack-only` when you want only a fresh pack and not the guard path.

### Learn from AI-assisted work

Generate local post-task learning artifacts from `.agentpack/task.md` and git changes:

```bash
agentpack learn
agentpack learn --today
agentpack learn --since main
agentpack learn --json
agentpack learn --llm-prompt --pr-comment
agentpack learn --provider-preview
agentpack learn --provider-command "python scripts/learn_provider.py"
agentpack learn --dashboard --team-export
agentpack learn --skills
agentpack learn --drills
agentpack learn --ci
agentpack learn --feedback helpful --feedback-target "skill:CLI design" --feedback-note "Useful review prompts"
```

AgentPack writes developer notes to `.agentpack/learning.md` or `.agentpack/daily-summary.md`, updates a local skill memory in `.agentpack/skills-progress.json`, writes ranked `.agentpack/agent-lessons.md` for future coding agents, and can emit `.agentpack/learning.prompt.md`, `.agentpack/pr-learning-comment.md`, `.agentpack/learning-dashboard.html`, or `.agentpack/team-lessons.md`. Learn is local-first by default: `--provider-preview` shows the bounded payload for optional external refinement without making a network call, `--provider-command` runs only the local command you provide, and feedback stays in `.agentpack/learning-feedback.jsonl`.

Use `--dashboard` when a developer wants an IDE-friendly review surface. Use
`--team-export` when the useful lesson should be shared without publishing a
personal skill history. Use `--ci` to fail a workflow when the generated
learning is too generic or lacks changed-file evidence.

## Agent Setup

AgentPack can install repo-local instructions and hooks for the coding agent you use. The installer is idempotent and merges with existing config where possible.

```bash
agentpack init --agent claude
agentpack init --agent codex
agentpack init --agent cursor
agentpack init --agent windsurf
agentpack init --agent antigravity
agentpack init --agent generic
```

What each integration writes:

| Agent | Files |
|---|---|
| Claude Code | `CLAUDE.md`, `.claude/settings.json`, `.mcp.json` |
| Codex | `AGENTS.md`, `.codex/hooks.json`, git hooks |
| Cursor | `.cursorrules`, `.cursor/rules/agentpack.mdc`, `.vscode/tasks.json`, git hooks |
| Windsurf | `.windsurfrules`, `.vscode/tasks.json`, git hooks |
| Antigravity | `GEMINI.md`, `.vscode/tasks.json`, git hooks |
| Generic | no agent-specific files; use `context.md` directly |

MCP-capable agents should prefer AgentPack MCP tools over reading markdown files directly. The markdown context files remain useful for manual review, CI, non-MCP agents, and logs.

Generated instructions keep thread mode explicit. They recommend `AGENTPACK_THREAD_ID=<stable-id> agentpack ... --thread auto` when you want scoped state, but plain installed hooks continue using global `.agentpack/task.md` and `.agentpack/context.md`.

## Configuration Snapshot

`agentpack init` creates `.agentpack/config.toml`. Most projects can use the defaults:

```toml
[context]
default_mode = "balanced"
default_budget = 40000

[context_lite]
budget = 8000

[agents.generic]
output = ".agentpack/context.md"
```

Use `agentpack pack --mode lite` when you want a cheap ranked map before deeper file reads. Use `minimal`, `balanced`, or `deep` when you want progressively more file content in the generated pack.

Use `.agentignore` to remove generated output, vendored code, large exports, or files that repeatedly appear as ranking noise. AgentPack imports obvious generated/noisy entries from gitignore sources during init, but repository-specific outputs should still be added by hand.

Use scoring weights only after measuring a real miss:

```bash
agentpack benchmark --misses
agentpack explain --file path/to/missed_file.py
agentpack diagnose-selection
agentpack ignore suggest
```

Configuration detail: [`docs/configuration.md`](docs/configuration.md).

## Common Workflows

### Debug Selection

```bash
agentpack explain --task auto
agentpack explain --file src/auth/session.py
agentpack explain --omitted
agentpack stats
agentpack diagnose-selection
```

### MCP-First Agent Flow

1. Call `start_task(task)` when a new task begins. AgentPack writes `.agentpack/task.md`, packs context, and returns ranked markdown.
2. Call `get_context()` when you need the latest pack. It blocks for one refresh if `.agentpack/task.md` or the repo snapshot changed since the last pack.
3. Use `route_task(task)` for a lightweight route: relevant files, rules, skills, commands, and safety warnings without writing a full context file.

### Multiple Agent Threads

Plain `agentpack pack`, `agentpack status`, and `agentpack guard` keep legacy global behavior and ignore ambient host thread env vars. Use thread mode only when you explicitly ask for it:

```bash
agentpack pack --thread codex-local
AGENTPACK_THREAD_ID=codex-local agentpack pack --thread auto
agentpack threads --active
agentpack state show --thread codex-local
```

Thread mode writes under `.agentpack/threads/<id>/` and appends `.agentpack/thread_index.jsonl`. Same-worktree, same-branch overlap is warning-only; separate worktrees or branches remain the safest workflow for concurrent edits.

### Release Validation

```bash
agentpack dev-check
agentpack release-check
agentpack verify-wheel
agentpack release prepare
agentpack ci init
```

The Makefile remains maintainer convenience, but the CLI commands above are the
package-user source of truth. `agentpack benchmark --release-gate` runs the
public proof path: public repos, proved target files, misses, and public table
output. `--sample-fixtures` remains a regression smoke path, not the release
gate.

## Commands

| Command | Purpose |
|---|---|
| `agentpack init --yes` | Create local config and ignore files |
| `agentpack work "task"` | Initialize if needed, start task, refresh context, show next steps |
| `agentpack start "task"` | Write task and run the guard/refresh workflow |
| `agentpack finish --since main` | Diagnose, capture benchmark case, run checks, mark done |
| `agentpack learn` | Generate developer learning notes, skill memory, feedback-aware drills, and future-agent lessons |
| `agentpack task show|set|clear` | Manage global or thread-scoped task files |
| `agentpack pack` | Generate a ranked context pack for `.agentpack/task.md` |
| `agentpack next --fix-all-safe` | Ask AgentPack what command or safe repair should happen next |
| `agentpack guard --repair-stale --refresh-context` | Check freshness, repair stale rules, refresh context |
| `agentpack status` | Show context freshness and git/task state |
| `agentpack stats` | Show pack size, token savings, and top files |
| `agentpack explain --task auto` | Debug selected and omitted files |
| `agentpack diagnose-selection` | Turn latest pack/benchmark signals into concrete tuning actions |
| `agentpack ignore suggest|apply` | Suggest or apply `.agentignore` improvements |
| `agentpack route --task "..."` | Get lightweight task routing without a full context file |
| `agentpack threads [--active] [--conflicts]` | Inspect thread-scoped context state |
| `agentpack state show|set|done` | Read or update execution state files |
| `agentpack benchmark capture --since <ref>` | Add a benchmark case from changed files |
| `agentpack benchmark --release-gate` | Run public benchmark evidence path |
| `agentpack dev-check` | Run docs, lint, pytest, and npm wrapper checks |
| `agentpack verify-wheel` | Install built wheel in a temp venv and run benchmark gate |
| `agentpack release-check` | Run version, changelog, tests, build, and benchmark checks |
| `agentpack release prepare` | Run full release prep, public table, and wheel verification |
| `agentpack ci init` | Generate a GitHub Actions workflow for AgentPack checks |

Full command reference: [`docs/commands.md`](docs/commands.md).

## Make Shortcuts

Run `make help` for the current list. The main targets are:

| Target | Wraps |
|---|---|
| `make test` | `pytest -q` |
| `make lint` | `python -m ruff check src tests` |
| `make npm-test` | npm wrapper/version tests |
| `make docs-check` | README/docs link smoke + `git diff --check` |
| `make benchmark` | `agentpack benchmark --release-gate --no-public-table` |
| `make benchmark-publish` | `agentpack benchmark --release-gate` |
| `make release-fast` | `agentpack release-check --skip-benchmark --skip-build` |
| `make release` | `agentpack release-check` |
| `make verify-wheel` | build wheel, install in temp venv, run benchmark gate |

`make context` uses legacy global context. For scoped context, run:

```bash
THREAD=codex-local make context-thread
AGENTPACK_THREAD_ID=codex-local make context-thread
```

## Benchmark Proof

AgentPack is best treated as a ranked starting map. It can reduce repeated orientation work, but the agent and reviewer still own correctness.

Use real repo evals instead of trusting compression numbers:

```bash
agentpack benchmark --release-gate
```

Current benchmark evidence is documented in [`benchmarks/README.md`](benchmarks/README.md) and the generated tables under `benchmarks/results/`. Treat these as scoped evidence for the included cases, not a universal performance claim.

## What A Pack Contains

Rendered packs are meant to be readable by humans and directly useful to agents. A typical pack includes:

- freshness metadata with task source, generated time, git branch, SHA, and snapshot hash
- execution state with task status, checklist counts, git dirty/ahead/behind counts, and Docker/Compose availability
- concurrent-context warnings when another active thread overlaps files on the same branch and worktree
- token stats and largest token consumers
- semantic repo map grouped by subsystem
- selected-file table with include mode and reason
- compressed context receipts showing why files were included or excluded
- file context in `full`, `diff`, `symbols`, `skeleton`, or `summary` mode

The pack is a starting map. Agents should still verify the selected files against actual code before editing.

## Local Files

AgentPack writes local artifacts under `.agentpack/`:

| Path | Purpose |
|---|---|
| `.agentpack/task.md` | current global task summary |
| `.agentpack/task_state.md` | optional global execution state |
| `.agentpack/context.md` | generic/Codex/Cursor/Windsurf fallback context |
| `.agentpack/context.claude.md` | Claude-flavored fallback context |
| `.agentpack/learning.md` | local post-task developer learning notes |
| `.agentpack/daily-summary.md` | local daily learning rollup from `agentpack learn --today` |
| `.agentpack/skills-progress.json` | local skill evidence map from task work |
| `.agentpack/agent-lessons.md` | compact repo-specific lessons injected into future packs |
| `.agentpack/learning.prompt.md` | optional source-backed prompt for external LLM refinement |
| `.agentpack/pr-learning-comment.md` | optional PR-comment-ready learning summary |
| `.agentpack/learning-dashboard.html` | optional static dashboard from `agentpack learn --dashboard` |
| `.agentpack/team-lessons.md` | optional shared lesson export from `agentpack learn --team-export` |
| `.agentpack/learning-feedback.jsonl` | optional local helpful/not-helpful feedback records |
| `.agentpack/pack_metadata.json` | freshness and pack metadata |
| `.agentpack/cache/` | offline file summaries keyed by hash |
| `.agentpack/snapshots/` | repo snapshot hashes |
| `.agentpack/thread_index.jsonl` | append-only thread activity index |
| `.agentpack/threads/<id>/` | scoped task, state, context, and metadata for explicit thread mode |

Generated context, cache, snapshots, and thread state are local operational files. The root `.agentpack/config.toml` and `.agentignore` are usually worth committing; generated context artifacts usually are not.

## Troubleshooting

If AgentPack selects too much:

```bash
agentpack diagnose-selection
agentpack stats
agentpack explain --omitted
agentpack explain --budget-plan
agentpack ignore suggest
```

If a required file is missing:

```bash
agentpack explain --file path/to/file.py
agentpack benchmark --misses
agentpack benchmark capture --since HEAD~1 --task "describe the task"
```

If context looks stale:

```bash
agentpack status --deep
agentpack guard --agent auto --repair-stale --refresh-context
```

If multiple agents are editing the same project:

```bash
agentpack threads --active
agentpack threads --conflicts
agentpack state show --thread codex-local
```

If package release checks fail:

```bash
agentpack release-check --json
agentpack release-check --skip-benchmark
```

Use the skip flags only for local iteration. The final release proof should include the build and public benchmark gate.

## Documentation

- [`docs/architecture.md`](docs/architecture.md): pipeline, data flow, package layout, thread/execution state, rendered-budget accounting.
- [`docs/commands.md`](docs/commands.md): full CLI command reference.
- [`docs/configuration.md`](docs/configuration.md): config, scoring weights, `.agentignore`, and git integration.
- [`docs/integrations.md`](docs/integrations.md): agent setup, MCP workflow, hooks, and native integration status.
- [`docs/benchmarking.md`](docs/benchmarking.md): quality bar, release gate, sample fixtures, and public artifacts.
- [`docs/development.md`](docs/development.md): local development, release checklist, naming/ranking, and contribution notes.
- [`docs/limitations.md`](docs/limitations.md): project scope, honest token framing, known limitations, and roadmap.

## Limitations

- AgentPack prepares context; it does not edit code, run tests for the agent, or prove correctness.
- Ranking is deterministic and local, but it can still miss files. Use `agentpack explain`, `agentpack benchmark --misses`, and normal code review.
- Thread coordination is warning-based, not locking-based. It helps agents notice collisions but does not enforce ownership.
- Language support is strongest for Python and JavaScript/TypeScript. Other languages still benefit from filenames, git signals, and summaries.
- Token estimates are approximate, even with rendered-budget accounting. Treat them as operational guidance, not provider billing truth.

More detail: [`docs/limitations.md`](docs/limitations.md).

## License

MIT
