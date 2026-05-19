# AgentPack

[![PyPI version](https://img.shields.io/pypi/v/agentpack-cli.svg)](https://pypi.org/project/agentpack-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/agentpack-cli.svg)](https://pypi.org/project/agentpack-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/vishal2612200/agentpack/actions/workflows/ci.yml/badge.svg)](https://github.com/vishal2612200/agentpack/actions/workflows/ci.yml)

> **Status: alpha (v0.3.1).** Works, tested, used in real sessions. Python and JavaScript/TypeScript are the best-supported languages. Public benchmark proof exists for the current suite, but broader repo coverage is still growing. API may change before 1.0.
>
> **Platform note:** macOS and Linux are fully supported. Windows support is not yet implemented (git hooks use POSIX shell; the Claude Code session hooks use `python3`/`rm -f`). Contributions welcome.

**Local context engine for AI coding agents.**

AgentPack builds task-focused context packs for Claude Code, Cursor, Windsurf, Codex, Antigravity, CI jobs, and any LLM workflow that can read markdown. It scans your repo locally, ranks files for the task, compresses the result into a token budget, and keeps the pack fresh through CLI commands, MCP tools, hooks, and agent integrations.

AgentPack is useful when a repo is too large to paste, but a blank agent session wastes time rediscovering the same code structure. It is a context preparation tool, not a coding agent.

## Contents

- [Features](#features)
- [Install](#install)
- [Quickstart](#quickstart)
- [Quality Bar](#quality-bar)
- [Debugging Selection](#debugging-selection)
- [Supported Integrations](#supported-integrations)
- [Commands](#commands)
- [Architecture](#architecture)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Development](#development)

## Features

- **Task-focused packing**: ranks files from git changes, task terms, symbols, imports, related tests, configs, churn, and repo history.
- **Budget-aware compression**: emits `full`, `diff`, `symbols`, `skeleton`, or `summary` views instead of all-or-nothing file dumps.
- **Semantic repo map**: adds a compact module-level map before file context so agents orient faster.
- **Freshness and deltas**: records task source, git state, snapshot hashes, selected-file deltas, and stale-context warnings.
- **Agent integrations**: installs Claude Code, Cursor, Windsurf, Codex, Antigravity, VS Code tasks, git hooks, and MCP configuration.
- **Local and measurable**: no API calls for scan, summarize, rank, pack, stats, or benchmark; quality is measured with expected-file evals.

## Install

```bash
pipx install agentpack-cli
agentpack --version
```

Requires Python 3.10+. The PyPI package is `agentpack-cli`; the command is `agentpack`. Use `pipx` for normal installs because many macOS/Linux Python distributions block global `pip install` with PEP 668's `externally-managed-environment` error. If you prefer `pip`, install inside a virtual environment.

Install `pipx` with your OS package manager first if needed:

```bash
# macOS
brew install pipx

# Ubuntu/Debian
sudo apt install pipx

# Fedora
sudo dnf install pipx

# Arch
sudo pacman -S python-pipx

# Then ensure pipx apps are on PATH
pipx ensurepath
```

JavaScript-heavy teams can install the npm wrapper:

```bash
npm install -g @vishal2612200/agentpack
agentpack --version
```

The npm package is a Node launcher around the Python implementation. It requires Node.js 18+ and Python 3.10+, then installs the matching core `agentpack-cli` package into a per-version virtual environment on first run. The Python package remains the source of truth; npm is the convenience install path for JavaScript-heavy teams. Use the PyPI extras below when you need optional `watch` or `mcp` dependencies.

## Quickstart

```bash
cd your-project
agentpack init --agent codex       # or claude, cursor, windsurf, antigravity
printf '%s\n' "fix auth token expiry" > .agentpack/task.md
agentpack pack
```

This creates `.agentpack/` state, installs the requested agent integration, generates a ranked context pack, and writes the adapter output for that agent. For active local work, keep context fresh with:

```bash
agentpack watch
```

For a guided setup that explains each next step:

```bash
agentpack quickstart --task "fix auth token expiry"
```

## Project Scope

**AgentPack is:**

- A local context engine for building task-focused packs for AI coding agents.
- A CLI, MCP server, hook runner, and integration layer.
- A summary cache, import graph, ranking engine, semantic repo map, and token-budget selector.
- An eval harness for measuring whether selected files match files you actually changed.

**AgentPack is not:**

- A coding agent.
- A hosted service.
- A semantic code search engine.
- A replacement for normal source inspection on critical changes.
- Proven across a large public benchmark suite yet.

## Quality Bar

AgentPack is best treated as a **ranked starting map**. It should reduce repeated orientation work, but the agent and reviewer still own correctness.

| Signal | What good looks like |
|---|---|
| Token reduction | 90-99% smaller than raw repo text on large repos |
| Pack size | Usually 8k-25k tokens for a specific task |
| Pack time | Seconds on a warm cache; first summarize pass is slower |
| Recall | Expected files appear near the top; validate with `agentpack benchmark --misses` |
| Precision | Good enough to reduce exploration; summaries and repo maps may still include noise |
| Freshness | Stale packs are clearly marked by task, git, and snapshot checks |

Use real repo evals instead of trusting compression numbers:

```bash
agentpack benchmark --init
# add historical tasks and files actually changed
agentpack benchmark --compare --misses --public-table
agentpack benchmark --public-repos --prove-targets --misses --public-table
agentpack benchmark --results-template
```

For public proof, use several real repositories or anonymized historical task
sets and publish the generated table from `benchmarks/results/*-public.md`.
This repo includes a curated public smoke suite in
`benchmarks/public-repos.toml`; it evaluates real commits from Pallets Click,
ItsDangerous, and MarkupSafe by checking out each commit's parent and scoring
against files actually changed by the commit. Synthetic fixtures are useful
regression tests, but should not be presented as market proof.

## Debugging Selection

When AgentPack misses a file, the next command should explain the miss:

```bash
agentpack benchmark --misses
agentpack explain --task "fix billing webhook" --file lib/billing/webhook.ts
agentpack explain --task "fix billing webhook" --omitted
agentpack explain --task "fix billing webhook" --budget-plan
```

`benchmark --misses` reports each expected file that was not selected, including whether it was ignored, scored too low, excluded by summary floor, cut by budget, or absent from the scan. `explain --file` shows the exact score signals for one file. `explain --budget-plan` shows how the token budget was spent across full, diff, symbols, skeleton, and summary modes.

This is the core reliability loop: pack, measure recall, inspect misses, then tune task wording, `.agentignore`, or scoring weights.

## MCP-First Workflow

For MCP-capable agents, the preferred workflow is pull-based:

1. Call `start_task(task)` when a new task begins. AgentPack writes `.agentpack/task.md`, packs context, and returns ranked markdown.
2. Call `get_context()` when you need the latest cached pack; it tells you if the pack is stale.
3. Call `get_delta_context()` after edits or hook hints to see what changed without loading the full pack.
4. Call `explain_file(path)` or `get_related_files(path)` when a file looks relevant or suspicious.

The CLI remains the setup/debug/release path. MCP is the best interactive path because the agent can ask for only the context it needs instead of relying on one static startup blob.

## Before / After Agent Behavior

Without AgentPack:

```text
User: fix auth token expiry
Agent: rg "auth"; opens router; opens middleware; opens tests; opens config;
       asks for more files; eventually finds token/session code.
Cost: repeated repo exploration and many unrelated file reads.
```

With AgentPack:

```text
User: fix auth token expiry
Agent: calls start_task("fix auth token expiry")
AgentPack: returns ranked files with reasons:
  1. src/auth/token.py — filename/content match, changed dependency
  2. src/auth/session.py — related implementation
  3. tests/test_auth.py — paired test
Agent: verifies those files, edits, runs tests, checks misses if needed.
Cost: starts from a measured map, then still verifies source normally.
```

## When it helps

| Workflow | Value |
|---|---|
| Claude API calls without tool use | **High** — pack is the only context the model sees |
| CI: generate pack per PR, attach as artifact | **High** — reviewers get instant focused context |
| Cursor / Windsurf / Codex / Antigravity sessions | **Medium** — context auto-injected on startup, repacked on commit |
| Large repos (>50k tokens) where exploration is slow | **Medium** — summary cache eliminates repeated file reads |
| Claude Code interactive session, small repo | **Low** — Claude reads files on demand already |

---

## How it compares to alternatives

**The honest version.**

### repomix / gitingest / code2prompt

These are repo dumpers. They pack a repo (or subset) into a file and hand it to you. They do that job well.

What they don't do: decide what's relevant to *your task*. You specify the scope — files, globs, directories — and they package your decision. If you want "only the files that matter for fixing this auth bug", you have to figure that out yourself. On a 200-file repo, that's 80% of the work.

AgentPack does that selection automatically. You give it a task string; it uses task classification, git diff, import graph traversal, semantic summaries, and keyword scoring to rank every file, then cuts to fit your token budget. You don't touch globs.

The other difference: all three pack uniformly (full content or nothing). AgentPack is selective by inclusion mode — changed files can be full source, relevant diff hunks, symbol bodies, interface skeletons, or summaries; unrelated files get dropped. A repomix dump of a 50k-token repo stays 50k tokens. An agentpack of the same repo for a specific task is typically 8k–20k.

**Use repomix/gitingest if:** you want to dump an entire small repo into a chat UI for a one-shot question. Zero setup, great for "explain this codebase."

**Use agentpack if:** you're running repeated tasks on a large repo and want automatic, task-driven file selection every time.

### aider

Different category. Aider is an interactive pair programmer — it reads, edits, and commits files directly. Its repo-map is genuinely smart. If you want an AI coding assistant making actual edits, aider is excellent.

AgentPack is not a coding assistant. It's a context preparation tool. The output is a markdown file you can pass as context.

**Use aider if:** you want interactive, supervised AI coding sessions in a terminal.

**Use agentpack if:** you're working on large repos and want automatic, task-driven file selection — CI, scripts, batch workflows, or interactive sessions.

### Claude Code / Cursor / Windsurf / Codex (agentic IDEs)

These tools have native file access via tool calls. Claude reads exactly the files it needs, on demand, per turn. Pre-packing context adds overhead without much benefit on small-to-medium repos.

AgentPack's value here is different: `agentpack init --agent <x>` configures your agent to read or inject a ranked context pack and auto-repack when the repo changes. On large repos where tool-call exploration piles up across turns, this front-loads the cost once instead of paying per-turn.

### Where AgentPack Wins

| Scenario | repomix | gitingest | code2prompt | aider | agentpack |
|---|---|---|---|---|---|
| API call without tool use | ✓ dump | ✗ | ✓ | ✗ | ✓ task-filtered |
| CI per-PR context | ✓ dump | ✗ | ✓ | ✗ | ✓ task-filtered |
| Auto task inference from git | ✗ | ✗ | ✗ | partial | ✓ |
| Relevance ranking by task | ✗ | ✗ | ✗ | ✗ | ✓ |
| Import graph traversal | ✗ | ✗ | ✗ | ✓ | ✓ |
| Monorepo workspace hints | ✗ | ✗ | ✗ | manual | ✓ |
| Token budget enforcement | manual | manual | manual | ✓ | ✓ |
| Cursor / Windsurf / Codex / Antigravity install | ✗ | ✗ | ✗ | ✗ | ✓ |
| Zero API calls | ✓ | ✓ | ✓ | ✗ | ✓ |
| Interactive coding sessions | ✗ | ✗ | ✗ | ✓✓ | ✗ |
| Any LLM | ✓ | ✓ | ✓ | ✓ | partial* |

_*`--agent generic` outputs standard markdown. Claude adapter has richer instructions._

### What AgentPack Does Not Do Well

- **Interactive sessions on small repos**: if your whole repo is <20k tokens, a simple repo dump may be enough
- **One-shot public repo questions**: gitingest's "replace hub with ingest" is faster for quick read-only exploration
- **Guaranteed source-of-truth selection**: AgentPack ranks likely files; it can miss task-critical files. Use `agentpack benchmark --misses`, `agentpack explain`, and normal `rg`/agent file reads for correctness.
- **Deep semantic understanding**: keyword/concept scoring, imports, symbols, and path roles help, but they are not an LLM-level code understanding system
- **Public proof without real cases**: bundled fixtures are smoke tests. Strong claims need historical tasks from real repos and published results.

---

## Supported Integrations

| Agent | Automation level | Method |
|---|---|---|
| Claude Code (hook) | Highest | `init` writes `CLAUDE.md`, `.claude/settings.json` hooks, and `.mcp.json` |
| Codex | Medium | `init` writes `AGENTS.md`, `.codex/hooks.json` + git hooks |
| Cursor | Medium | `init` writes `.cursorrules`, `.cursor/rules/agentpack.mdc`, VS Code task + git hooks |
| Windsurf | Medium | `init` writes `.windsurfrules`, VS Code task + git hooks |
| Antigravity | Medium | `init` writes `GEMINI.md`, VS Code task + git hooks |
| Generic | Basic | `watch` mode + read `context.md` |

### Integration limitations

- AgentPack cannot intercept prompts inside IDEs — Cursor/Windsurf rely on rules being followed.
- Claude wrapper (`agentpack claude`) is the most deterministic integration.
- If the task changes drastically mid-session, context needs one refresh cycle.
- AgentPack-selected files are ranked starting points, not absolute truth.

---

## Agent setup

`agentpack init` is the normal one-command project setup. It creates `.agentpack/` state and installs the detected agent integration. Re-run it any time; integration writes are idempotent and never clobber unrelated config.

Use `--agent` explicitly to override detection. `agentpack install` remains available when you only want to repair or reconfigure agent files without reinitializing project state.

### Claude Code

```bash
agentpack init --agent claude
```

Configures:
- `CLAUDE.md` — tells Claude to read the context pack before each task
- `.claude/settings.json` — two hooks:
  - `SessionStart`: clears injection sentinel so first prompt gets context
  - `UserPromptSubmit`: runs `agentpack hook` — detects repo changes via `root_hash`, detects clear task switches, updates `.agentpack/task.md`, and triggers background repack using your prompt as task. With MCP: emits Option-B hint (~100 tokens, task + top files). Without MCP: emits capped fallback (top 8 files, ≤3k chars)

After this, context is injected automatically into every Claude Code session. No `/agentpack` command needed — it just happens.

### Cursor

```bash
agentpack init --agent cursor
```

Configures:
- `.cursorrules` — rule: write current task, run `agentpack pack --task auto`, then read `.agentpack/context.md`
- `.cursor/rules/agentpack.mdc` — `alwaysApply: true` rule (Cursor v0.43+)
- `.git/hooks/post-commit`, `post-merge`, `post-checkout` — background repack on tree change
- `.vscode/tasks.json` — "AgentPack: Repack context" in Command Palette + `runOn: folderOpen`

### Windsurf

```bash
agentpack init --agent windsurf
```

Configures:
- `.windsurfrules` — rule: write current task, run `agentpack pack --task auto`, then read `.agentpack/context.md`
- `.git/hooks/post-commit`, `post-merge`, `post-checkout` — background repack on tree change
- `.vscode/tasks.json` — "AgentPack: Repack context" in Command Palette + `runOn: folderOpen`

### Codex

```bash
agentpack init --agent codex
```

Configures:
- `AGENTS.md` — tells Codex to write current task, repack, and read the context pack before each task
- `.codex/hooks.json` — Codex app lifecycle hooks for prompt-time AgentPack refresh hints
- `.git/hooks/post-commit`, `post-merge`, `post-checkout` — background repack on tree change

### Antigravity

```bash
agentpack init --agent antigravity
```

Configures:
- `GEMINI.md` — registers the agentpack skill reference and task-switch protocol
- `.git/hooks/post-commit`, `post-merge`, `post-checkout` — background repack on tree change
- `.vscode/tasks.json` — "AgentPack: Repack context" in Command Palette + `runOn: folderOpen`

`agentpack pack` writes `.agent/skills/agentpack/SKILL.md`, which Antigravity can activate automatically for coding tasks.

### Auto-repack comparison

| Mechanism | Claude Code | Cursor | Windsurf | Codex | Antigravity |
|---|---|---|---|---|---|
| Config file patched | `CLAUDE.md` + `.claude/settings.json` | `.cursorrules` + `.cursor/rules/*.mdc` | `.windsurfrules` | `AGENTS.md` + `.codex/hooks.json` | `GEMINI.md` + generated `.agent/skills/agentpack/SKILL.md` after pack |
| Auto-inject on startup | ✅ `UserPromptSubmit` hook | ✅ `alwaysApply` | ✅ rules file | ✅ `AGENTS.md` | ✅ Skill auto-activation |
| Auto-repack when stale | ✅ hook (content hash via `root_hash`, ~1ms when fresh) | ✅ git hooks | ✅ git hooks | ✅ git hooks | ✅ git hooks |
| Manual repack shortcut | ✅ `/agentpack` slash cmd | ✅ VS Code task | ✅ VS Code task | `agentpack pack` | ✅ VS Code task |

---

## The summary cache — the core feature

Run once, reuse forever:

```bash
agentpack summarize
```

Builds an offline summary of every file — no API calls, no network. Each summary captures:
- What the file does and its responsibility
- Exported classes, functions, signatures with extracted bodies
- Import dependencies
- Likely side effects, public API shape, error paths, and test hints

Summaries are stored in `.agentpack/cache/` keyed by file hash. Only changed files are re-summarized on the next pack.

**Team tip:** commit the cache so every developer and CI job gets summaries for free:

```bash
agentpack init --share-cache
git add .agentpack/cache/
git commit -m "chore: add agentpack summary cache"
```

---

## Honest token framing

AgentPack's pack is typically 10,000–25,000 tokens. Comparing that to "raw repo size" (200k–2M tokens) is misleading — nobody dumps the whole repo into Claude.

The real comparison for a piped/API workflow: **what would you manually copy-paste** to give Claude enough context? For a typical bug fix touching 3 files with 10 relevant dependencies, that's ~30,000–80,000 tokens assembled by hand. AgentPack gets you there in one command.

Token counts use tiktoken `cl100k_base` — a close approximation to Claude's actual billing, but not exact.

---

## CI/CD: pack per PR

### AgentPack's Own CI

agentpack uses two workflows:

- **`ci.yml`** — runs tests (Python 3.10–3.13) + ruff lint + 80% coverage gate on every push and PR to `main`
- **`publish.yml`** — runs on every `v*` tag push; requires tag from a `release/*` branch and a CHANGELOG.md entry for the version before building and publishing to PyPI (trusted publishing)

### Add context packing to your repo

Add to `.github/workflows/agentpack-context.yml`:

```yaml
name: AgentPack context pack

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  pack:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: python -m pip install agentpack-cli

      - name: Generate context pack
        run: |
          agentpack init --yes
          agentpack pack --agent claude \
            --task "${{ github.event.pull_request.title }}" \
            --since origin/${{ github.base_ref }} \
            --mode balanced

      - name: Upload context pack
        uses: actions/upload-artifact@v4
        with:
          name: agentpack-context
          path: .agentpack/context.claude.md
          retention-days: 7
```

Reviewers download the artifact and open it in their agent of choice. No repo clone needed — the pack contains full content for changed files and summaries for dependencies.

---

## Commands

Most users only need four commands:

```bash
agentpack init --agent codex
printf '%s\n' "describe the change" > .agentpack/task.md
agentpack pack
agentpack watch
agentpack doctor --agent all
```

Command map:

| Command | Use when |
|---|---|
| `agentpack init` | Set up `.agentpack/` and install one agent integration for a repo |
| `agentpack install` | Refresh or add an agent integration without changing project state |
| `agentpack repair` | Restore missing or drifted integration files |
| `agentpack pack` | Generate a ranked context pack for one task |
| `agentpack watch` | Keep the context pack fresh while you work |
| `agentpack doctor` | Audit hooks, agent files, CLI path, and repo health |
| `agentpack explain` | Understand why a file was selected or omitted |
| `agentpack benchmark` | Measure recall, precision, and misses against real tasks |
| `agentpack tune` | Suggest fixes from recent pack metrics and benchmark misses |
| `agentpack status` | Inspect current pack freshness and metadata |
| `agentpack diff` | Show what changed between context snapshots |
| `agentpack monitor` | Review recent pack runs and quality signals |
| `agentpack scan` | Inspect packable, ignored, binary, and largest files |
| `agentpack global-install` | Install opt-in global hooks for initialized repos |

### `agentpack global-install`

Install once — works in every repo from that point on. The recommended first step.

```bash
agentpack global-install                       # auto-detect IDE
agentpack global-install --agent claude        # Claude Code
agentpack global-install --agent cursor        # Cursor
agentpack global-install --agent windsurf      # Windsurf
agentpack global-install --agent codex         # Codex
agentpack global-install --agent antigravity   # Antigravity
```

What it does:
- **Git template hooks** (`~/.git-templates/hooks/`) — git copies these into every repo on `git init` / `git clone`. On `post-commit`, `post-merge`, `post-checkout`: silently repacks **only if `.agentpack/config.toml` exists** — no-op in repos that haven't opted in.
- **Shell cd hook** (`~/.zshrc` or `~/.bashrc`) — on `cd`, repacks if stale **only in opted-in repos**. Never touches repos without `.agentpack/config.toml`. Never auto-inits.
- **Agent config** — same agent-specific files that `agentpack init --agent <x>` or `agentpack install --agent <x>` writes for the current project.

All changes are idempotent, reversible, and non-destructive. Existing hooks and rc files are appended to, never overwritten. Repos you haven't explicitly run `agentpack init` in are never touched.

Options:

| Flag | Default | Description |
|---|---|---|
| `--agent` | `auto` | Target agent (`auto` \| `claude` \| `cursor` \| `windsurf` \| `codex` \| `antigravity`) |
| `--no-pipx` | — | Skip pipx install (if agentpack already installed) |
| `--no-shell-hook` | — | Skip shell rc patching |
| `--no-git-template` | — | Skip git template hooks |
| `--dry-run` | off | Show what would be changed without touching anything |

Preview before committing:

```bash
agentpack global-install --dry-run
```

---

### `agentpack global-uninstall`

Remove all global hooks — git templates and shell rc. Per-project `.agentpack/` directories are untouched.

```bash
agentpack global-uninstall
agentpack global-uninstall --no-shell-hook    # remove only git template hooks
agentpack global-uninstall --no-git-template  # remove only shell hook
```

---

### `agentpack doctor`

Diagnose your agentpack installation — checks CLI, git template hooks, git config, shell hook, per-repo state, and agent config.

```bash
agentpack doctor
agentpack doctor --agent codex
agentpack doctor --agent all
```

Example output:

```
CLI
  ✓ agentpack found at /usr/local/bin/agentpack (0.1.x)

Git template hooks (~/.git-templates/hooks/)
  ✓ post-commit
  ✓ post-merge
  ✓ post-checkout

git config init.templateDir
  ✓ init.templateDir = /Users/you/.git-templates

Shell cd hook
  ✓ Hook present in /Users/you/.zshrc

Per-repo state
  ✓ .agentpack/config.toml present
  ✓ context pack present (age: 2m)

Agent config
  ✓ CLAUDE.md (agentpack configured)
  - .cursorrules not present (optional)
  ✓ Claude hooks present (local): .claude/settings.json
  ! ~/.claude/settings.json has no agentpack hooks — run: agentpack install --agent claude --global
  ! Hooks local-only — context won't auto-inject in other repos. Run: agentpack install --agent claude --global

Slash command (/agentpack)
  ✓ Slash command installed (local): .claude/commands/agentpack.md
  - Slash command not installed globally — run: agentpack install --agent claude --global

Some checks failed. Run the suggested commands above to fix.
```

The new checks in `doctor`:
- **Agent matrix audit**: `--agent all` checks Claude, Cursor, Windsurf, Codex, Antigravity, and Generic in one pass, including Codex `.codex/hooks.json` lifecycle hooks.
- **Local vs global hooks**: warns when Claude hooks are only in the per-project `.claude/settings.json` — context won't auto-inject in other repos
- **Slash command presence**: checks both local (`.claude/commands/`) and global (`~/.claude/commands/`) installations
- **Source checkout mismatch**: warns when you're inside an AgentPack source checkout but the `agentpack` executable imports the installed site-packages copy. Use `PYTHONPATH=src python -m agentpack.cli ...` or `pip install -e .` for local development.

---

### `agentpack init`

Initialize AgentPack in the current directory.

```bash
agentpack init                  # interactive mode picker
agentpack init --yes            # non-interactive, use defaults (good for CI)
agentpack init --agent codex    # force an agent integration
agentpack init --share-cache    # commit cache/ to git for team sharing
```

Creates:
```
.gitignore                # patched idempotently with AgentPack generated artifacts
.agentignore              # gitignore-style file exclusion rules
.agentpack/
  config.toml             # configuration (safe to commit)
  .gitignore              # excludes cache/, snapshots/, context.* by default
  cache/                  # offline summary cache
  snapshots/              # file hash snapshots
```

Also installs the detected agent integration:
- Claude: `CLAUDE.md`, `.claude/settings.json` hooks, `.mcp.json`
- Cursor: `.cursorrules`, `.cursor/rules/agentpack.mdc`, git hooks, VS Code task
- Windsurf: `.windsurfrules`, git hooks, VS Code task
- Codex: `AGENTS.md`, `.codex/hooks.json`, git hooks
- Antigravity: `GEMINI.md`, git hooks, VS Code task
- Generic: no agent-specific files

---

### `agentpack install`

Install or refresh one agent integration without reinitializing project state.

```bash
agentpack install                      # auto-detect IDE
agentpack install --agent claude       # CLAUDE.md + .claude/settings.json hooks
agentpack install --agent cursor       # .cursorrules + .mdc + git hooks + VS Code tasks
agentpack install --agent windsurf     # .windsurfrules + git hooks + VS Code tasks
agentpack install --agent codex        # AGENTS.md + .codex/hooks.json + git hooks
agentpack install --agent antigravity  # GEMINI.md + git hooks + VS Code tasks
```

All installs are idempotent — safe to re-run, merge with existing config, never duplicate.

---

### `agentpack repair`

Repair missing or drifted integration files. It uses the same installer contract as `init` and `install`, but is named for the "make this repo healthy again" workflow.

```bash
agentpack repair                 # repair auto-detected agent
agentpack repair --agent codex   # AGENTS.md + .codex/hooks.json + git hooks
agentpack repair --agent all     # repair every supported integration
```

---

### `agentpack summarize`

Build or refresh the offline summary cache. **No API calls, ever.**

```bash
agentpack summarize              # build summaries for all files not yet cached
agentpack summarize --refresh    # force rebuild all
```

Summaries are built with parallel AST/regex analysis — no network, no tokens spent. Run once after `init`. After that, pack automatically rebuilds summaries only for changed files (hash-keyed cache).

---

### `agentpack pack`

Generate a context pack. Task text lives in `.agentpack/task.md`; inline task strings are no longer supported on `pack`. `--task auto` remains for old hooks and scripts, and is the default when the flag is omitted.

```bash
printf '%s\n' "fix auth session bug" > .agentpack/task.md
agentpack pack                                # auto-detects your IDE
agentpack pack --agent claude                 # explicit agent
agentpack pack --workspace apps/web

# Only include changes since a git ref
printf '%s\n' "review these changes" > .agentpack/task.md
agentpack pack --since main

# Watch mode — re-packs on every file change
printf '%s\n' "refactor auth" > .agentpack/task.md
agentpack pack --session
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--agent` | `auto` | Target agent (`auto` \| `claude` \| `cursor` \| `windsurf` \| `codex` \| `antigravity` \| `generic`). `auto` detects the active IDE from env and project files. |
| `--task` | `auto` | Backward-compatible task source. Only `auto` is supported; write task text to `.agentpack/task.md`. |
| `--mode` | `balanced` | Budget mode: `minimal`, `balanced`, `deep` |
| `--budget` | 0 (uses config default 25000) | Token budget |
| `--workspace` | — | Restrict packing to a monorepo workspace and write `.agentpack/workspaces/<workspace>/context.md` |
| `--since` | — | Only include files changed since this git ref |
| `--session` | off | Re-pack on every file change (watch mode) |
| `--refresh` | off | Force rebuild summaries before packing |

**Budget modes:**

| Mode | What's included |
|------|----------------|
| `minimal` | Changed files + direct configs, with a small summary cap |
| `balanced` | Changed files + deps + reverse deps + tests + capped summaries |
| `deep` | Everything in balanced + docs + more full-content files, uncapped summaries |

`pack` also prints diagnostics when the pack looks noisy: very short task text, no changed files, mostly filename matches, mostly summaries, many symbol matches, weak summaries excluded by the score floor, or summaries excluded by the mode cap.

AgentPack uses budget-aware compression when building context:

| Include mode | Used for |
|--------------|----------|
| `full` | Small or highly relevant changed files |
| `diff` | Large changed files where the edit hunk is more useful than the whole file |
| `symbols` | Focused implementation bodies under budget pressure |
| `skeleton` | Imports plus public class/function signatures |
| `summary` | Lower-priority supporting files |

This keeps unrelated dirty files from consuming the whole context budget while preserving changed-file recall.

---

### `agentpack quickstart`

Show the shortest useful path for the current repo.

```bash
agentpack quickstart
agentpack quickstart --task "fix auth token expiry"
agentpack quickstart --task "fix auth token expiry" --write
```

`quickstart` does not guess at magic. It checks whether `.agentpack/config.toml`, `.agentpack/task.md`, and context packs exist, then prints the next few commands. With `--write`, it writes the supplied task into `.agentpack/task.md`.

---

### `agentpack watch`

Watch for file and task changes, refresh context automatically.

```bash
agentpack watch                        # refresh context on source/task changes
agentpack watch --debounce 3.0         # wait 3s after last change before refresh
```

Uses `watchdog` if installed, falls back to polling. Context is refreshed whenever source files or `.agentpack/task.md` change.

Install watchdog for better performance:
```bash
pipx inject agentpack-cli watchdog
```

---

### `agentpack claude`

Launch Claude CLI with an up-to-date context.

```bash
agentpack claude
```

Requires an initialized project (`agentpack init`). Refreshes context, prints the context path, then launches `claude` if found. Transparent about what it does — no fake prompt injection.

---

### `agentpack mcp`

Run AgentPack as an MCP server — exposes context packing as tools that Claude Code (and any MCP-compatible agent) can call directly.

```bash
pipx inject agentpack-cli "agentpack-cli[mcp]"
agentpack mcp
```

Register in Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "agentpack": {
      "command": "agentpack",
      "args": ["mcp"]
    }
  }
}
```

**Tools exposed:**

| Tool | Description |
|---|---|
| `start_task(task, mode, budget, max_tokens)` | Recommended MCP-first entry point. Writes `.agentpack/task.md`, generates a ranked pack, and returns packed markdown. |
| `pack_context(task, mode, budget, max_tokens)` | Generate a ranked context pack. If `task` is provided, writes it to `.agentpack/task.md`; if omitted, reads `task.md` or infers from git. |
| `get_context()` | Return the latest pre-built pack instantly (no repack). Prepends a freshness/staleness header so you know if it's stale. |
| `refresh()` | Refresh using the current `task.md` or git-inferred task. |
| `explain_file(path, task)` | Show score, inclusion mode, reasons, symbols, imports, and importers for one file. |
| `get_related_files(path, depth)` | Return import-graph neighbours and related tests for a file. |
| `get_delta_context(max_files)` | Return the latest selected-file delta plus top current selected files. Useful for cheap prompt-time refresh checks. |
| `get_stats()` | Return latest pack stats, savings, selection quality, excluded files, and benchmark-style signals. |

**Staleness detection:** `get_context()` compares the snapshot hash from when the pack was built against the current repo snapshot. If files changed since last pack, it prepends:
```
> **Stale context** — repo changed since last pack (generated: ...). Run pack_context() to refresh.
```

**Smart truncation:** `start_task()` and `pack_context()` keep headers intact and trim file content blocks to fit the token budget, appending a note about how many files were omitted.

Zero API calls — all analysis is offline. Summary cache keyed by file hash: cold run parallelises AST parsing across CPU cores; warm cache hits are instant.

---

### `agentpack explain`

Debug file selection — show which files would be selected, why, and what was excluded — without writing a context pack.

```bash
agentpack explain --task "fix auth session bug"
agentpack explain --task auto
agentpack explain --file src/auth/session.py   # per-file score breakdown
agentpack explain --omitted                    # top-10 excluded files
agentpack explain --budget-plan                # modes, token costs, value/token
```

Per-file breakdown (`--file`):

```
src/auth/session.py
  selected:  yes
  score:     310
  include:   full
  tokens:    4,200

  signals:
    +100  modified
    +80   filename keyword match
    +60   content keyword match (6)
    +50   direct dependency of changed file
    +35   has related tests

  symbols: create_session, revoke_session, validate_session
```

Use `--omitted` to see what was left out and why. Use `--file` when a file you expected isn't showing up. Use `--budget-plan` to inspect how the compression planner spent the token budget.

---

### `agentpack benchmark`

Measure token efficiency, file selection quality, and speed across tasks.

```bash
agentpack benchmark --task "fix auth token expiry"         # single task
agentpack benchmark --task "fix auth bug" --compare        # compare minimal/balanced/deep
agentpack benchmark --init                                 # scaffold .agentpack/benchmark.toml
agentpack benchmark --results-template                     # scaffold publishable results note
agentpack benchmark                                        # run all cases in benchmark.toml
agentpack benchmark --sample-fixtures                      # source checkout demo evals
agentpack benchmark --public-repos                         # real public commit evals
agentpack benchmark --misses                               # explain expected-file misses
agentpack benchmark --prove-targets                        # fail if recall/token precision targets miss
agentpack benchmark --public-table                         # write benchmarks/results/*-public.md
```

Output per case:

```
fix auth token expiry  mode=balanced

   packed tokens     29,357
   raw tokens       187,998
   saving             84.4%
   files selected       234
   changed covered    2/2  (100%)
   total time          0.45s

   phase    time
   scan     0.257s
   rank     0.027s
   select   0.009s

  top files: src/auth/token.py, src/auth/session.py, ...
```

**Compare mode** shows all three modes side-by-side:

```
Mode comparison: fix auth token expiry

   mode        tokens   saving   files   time
   minimal     29,882    84.1%     253   0.34s
   balanced    29,882    84.1%     253   0.24s
   deep         7,563    96.0%      43   0.24s
```

**With expected files** (add to `benchmark.toml`), you get precision/recall/F1:

```toml
[[cases]]
task = "fix auth token expiry"
mode = "balanced"
task_type = "backend-api"
workspace = "apps/api" # optional, for monorepos
expected_files = [
  "src/auth/token.py",
  "src/auth/session.py",
]
```

```
  precision 100.0%  recall 100.0%  F1 100.0%
  hit: src/auth/session.py, src/auth/token.py
```

Use `--misses` when recall is low. It prints each expected file that was not selected with status, rank, score, and scoring reasons, which helps separate ignored files, budget cuts, low scores, and missing dependency signals.

Use `--prove-targets` in CI or release prep when benchmark cases have `expected_files`. By default it requires average recall >=60% and token precision >=50%; tune with `--min-recall` and `--min-token-precision`.

Use `--public-repos` from an AgentPack source checkout to run the committed
real-repo smoke suite:

```bash
agentpack benchmark --public-repos --prove-targets --misses --public-table
```

Use `--public-table` after adding real historical tasks to write a publishable Markdown table with per-repo/task recall, token precision, rank@K, pack size, and miss count. This is the recommended artifact for README claims, release notes, and external benchmarks.

Add `task_type` to group results by workflow area. Benchmark summaries report average precision, recall, F1, and token noise by type, so a repo can show "backend-api is good, frontend-web is noisy" instead of hiding that under one aggregate.

---

### `agentpack scan`

Scan the repo and report file statistics.

```bash
agentpack scan
agentpack scan --largest 20
agentpack scan --ignored-summary
```

```
Files discovered:     1,248
Files ignored/binary:   230
Files scanned:          210
Raw estimated tokens: 940,000
Tokens after ignore:  210,000
```

Use `--largest` to find high-token files still entering packs. Use `--ignored-summary` when repo counts look surprising; it groups ignored and binary files by common directories or file extensions.

---

### `agentpack stats`

Show session state, token statistics, and selection accuracy for the last pack.

```bash
agentpack stats
```

When a session is active, shows session panel (agent, mode, started, refresh count) above token stats. Also lists top included files from the latest pack and avg recall/precision/F1 over the last 10 runs.

Newer metrics include token-weighted precision. File precision answers "how many selected files were later changed"; token precision answers "how many selected tokens were spent on files later changed." Context precision also credits obvious read-only support context, such as paired tests beside changed source files. `stats` breaks token precision down by inclusion mode (`full`, `symbols`, `summary`) so summary noise is visible. In monorepos, it also reports selected-file distribution by workspace when workspace metadata exists.

To build a real usefulness signal for your repo:

```bash
agentpack benchmark --sample-fixtures

agentpack benchmark --init
# edit .agentpack/benchmark.toml with real tasks + files you actually changed
agentpack benchmark --compare --misses --prove-targets
```

`--sample-fixtures` runs bundled FastAPI, Next.js, mixed Python/TypeScript, Django REST-style, Go service, and Rails-style fixture evals from an AgentPack source checkout. It is a smoke test, not a claim about your repo.

For an 8+ usefulness signal, use `benchmark.toml` with real third-party or customer-style repos: 5-20 historical tasks, `task_type` labels, the files actually changed for each task, and `--compare` results for recall, F1, rank@K, and token noise. That is better than trusting generic benchmarks because it tells you whether AgentPack selects the files that matter in code the package has never seen.

See [benchmarks/README.md](benchmarks/README.md) for the public smoke-suite fixtures, quality gates, and the recommended miss-debugging workflow.

---

### `agentpack tune`

Turn noisy `stats` and `benchmark --misses` output into next actions.

```bash
agentpack tune
agentpack tune --write
agentpack tune --no-benchmark
```

`tune` reads `.agentpack/metrics.jsonl` and, when present, `.agentpack/benchmark_results.jsonl`. It flags low token precision, zero-value summaries, repeated noisy paths, support-context gaps, and benchmark miss patterns. `--write` saves the same guidance to `.agentpack/tuning.md`.

This command does not pretend a pack is correct. It gives the next thing to inspect: lower mode, explain noisy files, adjust `.agentignore`, add benchmark cases, or inspect budget/score misses.

---

### `agentpack status`

Check whether the context pack is stale.

```bash
agentpack status
agentpack status --deep
# Context pack is up to date.
#   Task: fix auth session bug
#   Generated: 2026-04-29T12:00:00Z
```

`--deep` also prints the active agent, CLI path, current task, and integration health for the detected agent.

---

### `agentpack diff`

Show changes since last snapshot.

```
Added:    3 files
Modified: 7 files
Deleted:  1 file
Unchanged: 202 files
```

---

### `agentpack monitor`

Show pack performance across runs — timing per phase, token savings trend.

```bash
agentpack monitor           # last 20 runs
agentpack monitor --last 5
agentpack monitor --clear
```

---

## How it works

```
1. Scan repo  →  apply .agentignore  →  skip generated AgentPack outputs  →  hash files
2. Build offline summaries  →  role, imports, symbols, side effects, public API, errors, test hints
3. Build import dependency graph  →  Python/JS/TS full, Go/Rust/Java/Kotlin best-effort
4. Detect changed files  →  snapshot diff + git working tree + staged + optional --since ref
5. Classify task  →  bugfix / feature / docs / release / infra / audit / test / ui / refactor
6. Extract weighted task terms  →  literals, variants, concept synonyms, changed-file identifiers
7. Score every file  →  changes, task terms, symbols, content, deps, tests, configs, churn
8. Apply history learning  →  gently downrank files that were repeatedly selected as noise
9. Build semantic repo map  →  compact module/group map reserved inside the token budget
10. Select by value per token  →  full / diff / symbols / skeleton / summary / omit
11. For large diffs  →  score hunks against task keywords and keep the most relevant hunks
12. Redact secrets at materialization  →  before content reaches any renderer or adapter
13. Render context  →  freshness, task class, repo map, delta since last pack, receipts, files
14. Persist state  →  adapter output, canonical .agentpack/context.md, snapshot, metadata, metrics
```

---

## File scoring

| Signal | Points |
|--------|-------:|
| Modified file | +100 |
| Staged file | +90 |
| Filename/path keyword match | +80 |
| Symbol keyword match | +70 |
| Content keyword match | +60 |
| Direct dependency of changed file | +50 |
| Reverse dependency | +40 |
| Has related tests | +35 |
| Knowledge/architecture doc (DECISIONS.md, ADR-*.md, ARCHITECTURE.md, docs/adr/, docs/decisions/, docs/rfcs/) | +30 |
| Config file | +25 |
| Recently modified | +20 |
| High churn (top 10% by commit frequency) | +15 |
| Large unrelated file | −50 |
| Ignored/binary | −100 |

Keyword scoring uses weighted concept synonym expansion — literal task terms are strongest, normalized variants are slightly weaker, and broad concept synonyms are weaker again. "rate limiting" still expands to `throttle`, `leaky`, `bucket`, `quota`, but broad expansions no longer dominate literal task terms. Matching is token-based, so `task` does not accidentally match every `tasks.py`.

---

## Configuration

`.agentpack/config.toml`:

```toml
[project]
root = "."
ignore_file = ".agentignore"

[context]
default_budget = 25000
default_mode = "balanced"
max_file_tokens = 4000
min_summary_score = 60
max_summary_files_minimal = 15
max_summary_files_balanced = 40
max_summary_files_deep = 0
include_tests = true
include_configs = true
include_receipts = true

[hooks]
task_switch_detection = true
task_switch_min_terms = 1

[agents.claude]
output = ".agentpack/context.claude.md"
patch_claude_md = true

[agents.generic]
output = ".agentpack/context.md"
```

---

## Configurable scoring weights

```toml
# .agentpack/config.toml
[scoring]
modified                  = 100
staged                    = 90
filename_keyword          = 80
symbol_keyword            = 70
content_keyword_per_hit   = 10
content_keyword_max       = 60
direct_dep                = 50
reverse_dep               = 40
related_test              = 35
knowledge_file            = 30   # DECISIONS.md, ADR-*.md, ARCHITECTURE.md, docs/adr/ etc.
config_file               = 25
recently_modified         = 20
churn_high                = 15   # top 10% by commit frequency
large_unrelated_penalty   = -50
ignored_penalty           = -100
```

---

## .agentignore

Works like `.gitignore`. Default rules exclude:

- `node_modules/`, `.venv/`, `__pycache__/`
- `dist/`, `build/`, `.next/`, `coverage/`
- `*.lock`, `*.log`, `*.min.js`, `*.map`
- `.env`, `.env.*`, `*.pem`, `*.key`
- `*.csv`, `*.jsonl`, `*.parquet`

---

## Git integration

```
.agentignore              ✓ commit
.agentpack/config.toml    ✓ commit
.agentpack/cache/         ✓ commit if --share-cache (recommended for teams)
.agentpack/.gitignore     ✗ gitignored
.agentpack/snapshots/     ✗ gitignored
.agentpack/context.*      ✗ gitignored
.agentpack/task.md        ✗ gitignored (local current task)
.agent/skills/agentpack/  ✗ gitignored (generated Antigravity context)
```

---

## Architecture

### Data flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        agentpack pack                               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │              SCAN LAYER                  │
          │                                         │
          │  pathlib.rglob()  ──▶  .agentignore     │
          │       │                 (pathspec)       │
          │       ▼                                  │
          │  FileInfo[]  (path, hash, tokens, lang) │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │       SUMMARY + ANALYSIS LAYER           │
          │                                         │
          │  Summary cache  ── role, imports,       │
          │  (offline)        symbols, side effects, │
          │                   public API, errors     │
          │                                         │
          │  Import graph  ──  Python AST           │
          │  (6 languages)  ─  JS/TS regex          │
          │                 ─  Go regex              │
          │                 ─  Rust regex            │
          │                 ─  Java/Kotlin regex     │
          │                                         │
          │  Symbol extract  ── Python AST (full)   │
          │    (body via       ── JS/TS (functions, │
          │  ast.get_source_segment)   classes,     │
          │                    ── arrow fns w/ =>)  │
          │                                         │
          │  Test detection  ── name heuristics     │
          │  Task keywords   ── stopwords + variants│
          │                  ── concept synonyms    │
          │                  ── content enrichment  │
          │  Task class      ── bugfix/docs/release │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │           CHANGE DETECTION               │
          │                                         │
          │  Snapshot diff  (merkle root hash)      │
          │       +                                 │
          │  git diff / git diff --cached           │
          │       +                                 │
          │  git diff <ref> HEAD  (--since flag)    │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │              RANKING                     │
          │                                         │
          │  Score each file (configurable weights) │
          │  +100 modified  +80 filename match      │
          │   +70 symbol    +60 content match       │
          │   +50 dep       +40 rev-dep             │
          │   +35 test      +25 config  +20 recent  │
          │   -50 large unrelated                   │
          │  History noise penalty from metrics     │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │             REPO MAP                     │
          │                                         │
          │  Compact semantic map grouped by module │
          │  Reserved inside the context budget     │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │         BUDGET SELECTION                 │
          │                                         │
          │  Sort by changed/task/value-per-token   │
          │                                         │
          │  changed + small  ──▶  full content     │
          │  changed + large  ──▶  task-scored diff │
          │  task symbols     ──▶  symbol bodies    │
          │  interface view   ──▶  skeleton         │
          │  low context      ──▶  summary/omit     │
          │  budget fallback  ──▶  downgrade first  │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │              RENDERING                   │
          │                                         │
          │  Claude adapter      ──▶  context.claude.md │
          │  Cursor adapter      ──▶  context.md        │
          │  Windsurf adapter    ──▶  context.md        │
          │  Codex adapter       ──▶  context.md        │
          │  Antigravity adapter ──▶  .agent/skills/agentpack/SKILL.md │
          │  Generic adapter     ──▶  context.md        │
          │                                         │
          │  Freshness + task class + repo map      │
          │  Delta since last pack                  │
          │  Context receipts (why each file in/out)│
          │  Secret redaction (AWS/GH/OpenAI tokens)│
          └─────────────────────────────────────────┘
```

### Package layout

```
src/agentpack/
  cli.py                       # Typer CLI entry point (thin — delegates to commands/)

  data/
    agentpack.md               # bundled /agentpack slash command for Claude CLI

  application/
    pack_service.py            # PackPlanner: shared scan→summarize→graph→rank→repo_map→select pipeline
                               # PackService: materializes plan → writes context file
                               # AdapterRegistry: maps agent names to adapter instances
                               # PackRequest / PackResult / PackPlan DTOs

  domain/  (via core/models.py)
    FileInfo, ScanResult       # scan output (packable / ignored / binary)
    Symbol, FileSummary        # summary cache objects (role, side_effects, public_api, errors, tests)
    SelectedFile, Receipt      # selection output with redaction_warnings
    ContextPack                # final artifact with freshness, repo_map, delta_summary, redaction_warnings
    DependencyNode             # typed graph node (path, imports, imported_by, tests)
    DependencyGraph            # typed graph container (nodes dict + dict-like accessors)

  core/
    models.py                  # Pydantic domain models (see above)
    config.py                  # TOML config + ScoringWeights
    ignore.py                  # .agentignore / gitignore-style matching
    scanner.py                 # rglob → ScanResult (packable/ignored/binary split)
    snapshot.py                # JSON snapshots + merkle root hash
    diff.py                    # added / modified / deleted / unchanged diff
    git.py                     # subprocess git + task inference from branch/commits
    merkle.py                  # root hash: sort(path:hash) → sha256
    cache.py                   # summary cache keyed path+hash+provider+version
    context_pack.py            # select_files: full/diff/symbols/skeleton/summary + hunk scoring + redaction
    token_estimator.py         # tiktoken cl100k_base (approximate)
    redactor.py                # redact_secrets: fires at content materialization
    bootstrap.py               # is_initialized, bootstrap_if_needed

  analysis/
    dependency_graph.py        # build(): returns typed DependencyGraph over packable files
    python_imports.py          # ast-based import extraction
    js_ts_imports.py           # regex import extraction (ESM + CJS)
    go_imports.py              # Go import / import(...) blocks
    rust_imports.py            # use, mod, extern crate
    java_imports.py            # Java import + Kotlin import
    symbols.py                 # AST symbols + body via ast.get_source_segment
    tests.py                   # source → test file mapping heuristics
    ranking.py                 # keyword extraction, concept synonyms, scoring
    monorepo.py                # workspace detection + workspace ownership helpers
    repo_map.py                # compact semantic repo map reserved inside token budget
    task_classifier.py         # coarse task class for freshness/rendering/scoring context

  summaries/
    offline.py                 # zero-API: AST/regex → imports, symbols, role, side effects, API, errors
    base.py                    # cache-or-build orchestration (parallel, ThreadPool+ProcessPool)

  adapters/                    # context rendering only — no installation logic
    base.py                    # abstract BaseAdapter (output_path + render + write)
    claude.py                  # renders context.claude.md via render_claude()
    cursor.py                  # renders context.md via render_generic()
    windsurf.py                # renders context.md
    codex.py                   # renders context.md
    antigravity.py             # renders .agent/skills/agentpack/SKILL.md (SKILL.md frontmatter + body)
    generic.py                 # renders context.md (any LLM)
    detect.py                  # detect_agent(): infers active IDE from env vars + project files

  installers/                  # repo/tool configuration — separate from rendering
    claude.py                  # ClaudeInstaller: CLAUDE.md + .claude/settings.json
    cursor.py                  # CursorInstaller: .cursorrules + .mdc + auto-repack
    windsurf.py                # WindsurfInstaller: .windsurfrules + auto-repack
    codex.py                   # CodexInstaller: AGENTS.md + .codex/hooks.json + git hooks
    antigravity.py             # AntigravityInstaller: GEMINI.md + auto-repack

  integrations/                # system/tool integration (not core domain)
    agents.py                  # shared agent install/check/repair contract for all supported agents
    git_hooks.py               # install/remove .git/hooks post-commit/merge/checkout
    vscode_tasks.py            # install/remove .vscode/tasks.json entries
    global_install.py          # global: git template hooks + shell rc hook

  renderers/
    markdown.py                # renders pre-redacted ContextPack to markdown, including freshness/map/delta
    compact.py                 # compact protocol format for session context files
    receipts.py                # context receipt formatter

  mcp_server.py                # MCP tools: start_task, pack_context, get_context, explain, related, stats, delta

  session/
    state.py                   # SessionState dataclass + load/save/create/stop helpers
    __init__.py                # re-exports from state.py

  commands/                    # CLI only — parse args, call services/installers
    pack.py                    # agentpack pack → PackService.run()
    install.py                 # agentpack install / global-install → installers/
    repair.py                  # agentpack repair → shared integration repair
    init.py                    # agentpack init
    quickstart.py              # agentpack quickstart — guided first-run commands
    scan.py                    # agentpack scan
    diff.py                    # agentpack diff
    status.py                  # agentpack status
    stats.py                   # agentpack stats
    summarize.py               # agentpack summarize
    monitor.py                 # agentpack monitor
    explain.py                 # agentpack explain
    doctor.py                  # agentpack doctor
    tune.py                    # agentpack tune — tuning suggestions from metrics + benchmark misses
    hook_cmd.py                # agentpack hook — Claude prompt hook + stale detection
    mcp_cmd.py                 # agentpack mcp — MCP server entrypoint
    watch.py                   # agentpack watch — file watcher with debounce
    claude_cmd.py              # agentpack claude — refresh + launch claude
    benchmark.py               # agentpack benchmark — token efficiency, recall, miss diagnostics
```

### Key architectural properties

- **Redaction at materialization**: secrets are stripped inside `select_files()` before content reaches any renderer or adapter. Every output format gets redacted content automatically — no per-renderer redaction needed.
- **`ScanResult` splits cleanly**: `scan()` returns `ScanResult(packable, ignored, binary)` — downstream code only processes `packable` files, eliminating `if f.ignored or f.binary` guards throughout.
- **`PackPlanner` owns shared planning**: `PackPlanner.plan()` runs scan → summarize → graph → changes → rank → repo map → select and returns a `PackPlan`. Both `pack` and `explain` use the same planner — no duplicated pipeline logic, no drift.
- **`PackService` materializes a plan**: takes a `PackPlan`, computes delta since the previous pack, builds the `ContextPack` artifact, delegates rendering to `AdapterRegistry`, persists snapshot + metadata + metrics.
- **Mode selection is value-aware**: changed files can be `full`, `diff`, `symbols`, `skeleton`, or `summary`. Large diffs keep task-relevant hunks first, and tight budgets downgrade files before dropping them.
- **Repo maps are first-class context**: `analysis/repo_map.py` builds a compact semantic map before file context, and its token cost is reserved before file selection.
- **Metrics feed history learning**: selection accuracy records hit/noise paths, token precision, mode counts, and mode tokens. Later packs gently penalize repeated noisy paths unless they are currently changed.
- **Git history feeds recall**: files that historically changed in the same commits as live changed files receive a small boost, helping related tests, schemas, services, and configs surface without forcing full-content inclusion.
- **Second-pass expansion is guarded**: after first scoring, strong seeds can lift two-hop import, reverse-import, config, and related-test neighbours only when they share task or domain signal.
- **Co-change is guarded by precision history**: one-off co-change neighbors are ignored, and paths repeatedly measured as noise do not get revived by history boosts.
- **Precision guardrails adapt to bad history**: when summary token precision stays near zero, later packs raise the summary score floor, cap summaries more aggressively, and suppress summaries entirely for no-live-change packs. Weak filename-only matches are also damped unless other signals confirm them.
- **`AdapterRegistry` maps agent → adapter**: adding a new agent output format requires one entry in `AdapterRegistry.get()`, not changes to `PackService`.
- **`detect_agent()` runs at invocation time**: `--agent auto` (the default) calls `detect_agent()` fresh on every `pack` run and git hook execution — so context is always written for the active IDE, even when switching between agents or running in CI.
- **`DependencyGraph` is typed**: `dependency_graph.build()` returns `DependencyGraph(nodes: dict[str, DependencyNode])` — no more `dict[str, dict]` with stringly-typed keys like `"imported_by"`. Typos are caught at the model layer.
- **`integrations/` vs `core/`**: git hooks, shell rc patching, and VS Code tasks are infrastructure concerns — they live in `integrations/`, not `core/`. `core/` is pure domain logic.
- **Adapters render; installers configure**: `adapters/` knows how to write a context file for an agent. `installers/` knows how to configure the agent's tool (CLAUDE.md, .cursorrules, settings.json). They are separate concerns and separate classes.
- **Agent integration contract is shared**: `integrations/agents.py` defines install, audit, and repair behavior for Claude, Cursor, Windsurf, Codex, Antigravity, and Generic. `install`, `repair`, `doctor --agent all`, and release verification use the same contract.
- **MCP is the interactive path**: `start_task()` writes task state and returns a fresh pack, while `get_context()`, `get_delta_context()`, `explain_file()`, and `get_related_files()` let agents pull follow-up context on demand.

---

## Principles

- **Local-first**: `init`, `scan`, `diff`, `pack`, `stats`, `summarize` make zero API calls — ever. No optional LLM paths, no per-file costs.
- **Non-destructive**: never overwrites user files; config patching only touches agentpack-managed blocks
- **Agent-neutral**: architecture is generic; Claude Code is the primary target (deepest integration); Cursor, Windsurf, Codex, and Antigravity are supported but less battle-tested
- **No daemons**: file watching is opt-in via `agentpack watch`; git hooks run in the background and are opt-in via `install`
- **Measurable**: `benchmark`, `stats`, receipts, and `--misses` are first-class because compression without recall is not enough
- **Honest**: packed token count reflects real content, and raw-repo savings are presented separately from practical usefulness

---

## Known limitations

- **Windows**: not supported. Git hooks use POSIX shell (`#!/bin/sh`, `>/dev/null 2>&1 &`). The Claude Code session hooks use `python3` and `rm -f`. Contributions welcome.
- **Monorepos**: workspace-aware ranking supports npm/pnpm, Cargo, and `go.work` layouts. `--workspace` creates filtered per-workspace outputs. Package dependency hints currently come from npm/pnpm `package.json`; Cargo/Go workspace membership is detected, but package-manager dependency edges for Cargo/Go are not yet modeled.
- **Public benchmark proof**: `benchmarks/public-repos.toml` is a curated smoke suite over real public commits, and `benchmarks/results/2026-05-15-public.md` records the current proof run. Treat it as a floor, not a leaderboard; expand cases before broad external claims.
- **Symbol extraction**: Python (AST, full) and JavaScript/TypeScript (regex, arrow functions + classes) are well-supported. Go, Rust, Java, Kotlin have import graph traversal but no symbol extraction — they fall back to file-level summaries.
- **Selection recall**: ranking is heuristic. It can miss files when task language differs from code language, when repos have unusual architecture, or when important files are only connected at runtime.
- **Secret redaction**: covers AWS keys, GitHub tokens, OpenAI/Anthropic keys, JWTs, and private key blocks. Not a substitute for a dedicated secrets scanner on sensitive repos.
- **Token estimates**: uses tiktoken `cl100k_base` — approximate, not exact for Claude's billing.
- **Large repos (>5k files)**: global auto-bootstrap is skipped for repos over 5,000 files to avoid hangs. Run `agentpack init` explicitly in large codebases.

---

## Roadmap

Post-0.3 release focus: broader real-repo proof, npm publish reliability, and continued ranking precision.

- Expand the public real-repo suite beyond the current curated Pallets smoke set.
- Keep recall gains measured with `--prove-targets`; target 60%+ recall, 50%+ token precision, and task packs under 25k tokens.
- Extend second-pass expansion with framework route/service/schema pairs once benchmark misses prove the pattern.
- Make npm publishing reliable by adding `NPM_TOKEN` and rerunning the npm release workflow.
- Keep integration contracts stable across Claude, Cursor, Windsurf, Codex, Antigravity, and Generic before any 1.0 work.

---

## Optional dependencies

```bash
pipx inject agentpack-cli watchdog              # faster file watching for agentpack watch
pipx inject agentpack-cli "agentpack-cli[mcp]"  # expose agentpack as MCP server tools
pipx inject agentpack-cli "agentpack-cli[all]"  # watch + mcp
```

---

## Development

Clone and run locally:

```bash
git clone https://github.com/vishal2612200/agentpack.git
cd agentpack
python -m pip install -e ".[dev,watch,mcp]" build
pytest
```

Useful checks before opening a PR:

```bash
pytest
python -m ruff check src tests
python -m build
npm test --prefix npm
(cd npm && npm pack --dry-run)
pytest tests/test_agent_integration_matrix.py -q
agentpack benchmark --sample-fixtures --misses
agentpack doctor
```

For npm publish, configure GitHub secret `NPM_TOKEN`. The token must publish to the npm scope in `npm/package.json` (`@vishal2612200` today): use a token from that npm user, or create an npm org with that scope and grant the token owner publish access. If `npm publish` reaches the registry and then fails with `E404 Not Found - PUT ... @scope/package`, the token is authenticated but does not own or have write access to that scope. `agentpack doctor` warns locally when neither `NPM_TOKEN` nor `NODE_AUTH_TOKEN` is present, and the npm publish workflow fails early when the secret or scope access is wrong.

Good contribution areas:

- More real-world benchmark fixtures and public repo eval cases
- Windows support for hooks and session integrations
- Better symbol extraction for Go, Rust, Java, and Kotlin
- More precise import/dependency resolution for framework-heavy repos
- Ranking regressions with `expected_files` cases that reproduce misses
- npm wrapper improvements that preserve the Python CLI as the source of truth

Please include tests for ranking changes. A good ranking PR usually adds one focused unit test and one scenario in `tests/test_ranking_evals.py`.

---

## License

MIT
