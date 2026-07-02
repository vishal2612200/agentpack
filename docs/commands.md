# Commands

Full command reference for the `agentpack` CLI. Start with the core commands,
then use the advanced map when you need review, release, learning, diagnostics,
or benchmark workflows.

## Commands

Most users should start with five commands:

```bash
agentpack init --agent auto
agentpack route --task "describe the change"
agentpack pack --task "describe the change"
agentpack doctor
agentpack benchmark --release-gate
```

Core command map:

| Command | Use when |
|---|---|
| `agentpack init` | Set up `.agentpack/` and install one agent integration for a repo |
| `agentpack route` | Route a task to files, rules, skills, commands, and safety warnings without writing context |
| `agentpack pack` | Generate a ranked context pack for one task |
| `agentpack doctor` | Audit MCP, hooks, agent files, CLI path, and repo health |
| `agentpack benchmark` | Measure recall, precision, and misses against real tasks |

Advanced command map:

| Command | Use when |
|---|---|
| `agentpack install` | Refresh or add an agent integration without changing project state |
| `agentpack upgrade` | Refresh the auto-detected IDE/agent integration after package upgrade |
| `agentpack repair` | Restore missing or drifted integration files |
| `agentpack work` | Convenience wrapper for init, task, context refresh, and next steps |
| `agentpack work --run` | Advanced optional proof harness around a configured external runner |
| `agentpack start` | Write a task and run the default guard/refresh workflow |
| `agentpack review` | Prepare the full two-stage PR review bundle for the current branch or PR |
| `agentpack finish` | Run finish checks, capture benchmark evidence, and mark state done |
| `agentpack learn` | Generate developer learning notes, skill progress, and future-agent lessons from task context and git changes |
| `agentpack task` | Show, set, or clear global/thread-scoped task files |
| `agentpack next` | Recommend the next AgentPack action from repo/task/context state |
| `agentpack retrieve` | Retrieve file or symbol context from the latest pack registry |
| `agentpack toon-validate` | Validate TOON syntax for agent-facing artifacts |
| `agentpack learn` | Generate local learning notes, skill evidence, future-agent lessons, selected-file miss feedback, and local feedback signals |
| `agentpack perf` | Show runtime scorecard and optional recent history from pack, retrieval, and output-compression events |
| `agentpack wrap` | Pack fresh task context, then launch a coding agent binary |
| `agentpack compress-output` | Summarize noisy command output while preserving failures, paths, and diffs |
| `agentpack memory` | Show local cross-agent task memory from events and learning artifacts |
| `agentpack skills scan` | Print discovered local/global skills and rules |
| `agentpack skills index` | Write `.agentpack/skills_index.json` metadata for faster routing |
| `agentpack skills recommend` | Explain task-specific skill recommendations and confidence |
| `agentpack skills feedback` | Record local skill outcome feedback for future routing boosts |
| `agentpack watch` | Keep the context pack fresh while you work |
| `agentpack diagnose-selection` | Explain latest selection noise and write tuning advice |
| `agentpack ignore suggest|apply` | Suggest or apply safe `.agentignore` additions |
| `agentpack explain` | Understand why a file was selected or omitted |
| `agentpack eval` | Run deterministic failure evals with tests, diff limits, and taxonomy labels |
| `agentpack tune` | Suggest fixes from recent pack metrics and benchmark misses |
| `agentpack status` | Inspect current pack freshness and metadata |
| `agentpack dashboard` | Generate a local HTML dashboard for context, skills, learning, and quality |
| `agentpack threads` | List, archive, prune, and inspect thread-scoped contexts |
| `agentpack state` | Show or update task execution state |
| `agentpack diff` | Show what changed between context snapshots |
| `agentpack monitor` | Review recent pack runs and quality signals |
| `agentpack scan` | Inspect packable, ignored, binary, and largest files |
| `agentpack dev-check` | Run docs, lint, pytest, and npm wrapper checks |
| `agentpack verify-wheel` | Install a wheel in a temp venv and run benchmark gate |
| `agentpack release-check` | Run the local release gate |
| `agentpack release prepare` | Run release-check, public table benchmark, and wheel verification |
| `agentpack ci init` | Generate a GitHub Actions workflow for AgentPack checks |
| `agentpack global-install` | Install opt-in global hooks for initialized repos |
| `agentpack global-repair-hooks` | Repair stale global template hooks and current repo git hooks |

### `agentpack learn`

Generate local learning notes from the latest task, pack metadata, git diff, or recent task memory. Lessons are generated on demand; `agentpack work` only records cheap task facts.

```bash
agentpack learn --since main
agentpack learn "quiz me on last task" --json
agentpack learn "interview me on this PR"
agentpack learn --json
agentpack learn feedback helpful --target card:1
agentpack learn feedback not-helpful --note "too generic"
```

Writes `.agentpack/learning.md`, `.agentpack/agent-lessons.md`, and
`.agentpack/skills-progress.json`. Missed selected files are appended to
`.agentpack/ranking-feedback.jsonl`; later packs give overlapping tasks a small
boost for those missed paths. Explicit feedback writes
`.agentpack/learning-feedback.jsonl`. Future packs inject bounded agent lessons
when `[learning].inject_agent_lessons = true`. Quoted learning requests switch
the output into Task Coach mode (`quiz`, `interview`, `failure`, `review`, or
`system-design`) and can use recent `task_memory` events from
`.agentpack/session-events.jsonl`.

### `agentpack retrieve`

Retrieve content from the latest `.agentpack/pack-registry.json`.

```bash
agentpack retrieve src/app.py
agentpack retrieve --block-id src__app.py__run:abc123def456
agentpack retrieve src/app.py --mode skeleton
agentpack retrieve src/app.py --mode full --allow-stale
```

Use `--block-id` for exact file or symbol blocks printed by pack-registry
retrieval output. Full-file retrieval refuses stale hashes unless
`--allow-stale` is passed.

### `agentpack perf`

Show a local runtime scorecard from `.agentpack/session-events.jsonl`.

```bash
agentpack perf
agentpack perf --history 10
agentpack perf --history 10 --json
agentpack perf --measure-pack --repeat 3
agentpack perf --measure-pack --task "share broad repo context for review" --mode deep
```

`--measure-pack` runs planner-only pack profiles with broad context disabled
and enabled, then reports average latency, selected file count, repo-map tokens,
broad-context tokens, module count, and inferred context intent. It does not
write `.agentpack/context.md`.

### `agentpack wrap`

Write/refresh task context, then launch a coding agent.

```bash
agentpack wrap codex --task "fix auth retry" --dry-run
agentpack wrap codex --task "fix auth retry" --dry-run --print-env
agentpack wrap claude --task "update docs" -- --model opus
```

`--dry-run` prints the launch command without starting the agent. `wrap` passes
`AGENTPACK_ROOT`, `AGENTPACK_CONTEXT`, and `AGENTPACK_TASK` to the launched
process and warns when expected local agent setup files are absent.

### `agentpack compress-output`

Summarize noisy output while preserving failures, paths, diffs, and repeated
lines.

```bash
pytest -q 2>&1 | agentpack compress-output --kind pytest
agentpack compress-output test-output.txt --kind npm
git diff | agentpack compress-output --kind git-diff
rg "TODO" src | agentpack compress-output --kind rg
```

Specialized kinds currently cover test logs (`pytest`, `npm`, `vitest`, `jest`),
diffs (`git-diff`, `diff`, `patch`), search output (`rg`, `grep`, `search`),
and listings (`ls`, `find`, `tree`). Unknown kinds use the generic fallback.

### `agentpack memory`

Show local cross-agent task memory from AgentPack events and learning output.
When task, branch, commit, or GitHub PR metadata contains issue references,
memory JSON includes recent refs plus any fetched title/state/label details.
GitHub enrichment uses `gh` when available. Jira enrichment is optional and
uses `JIRA_BASE_URL` plus either `JIRA_BEARER_TOKEN` or `JIRA_EMAIL` with
`JIRA_API_TOKEN`.

```bash
agentpack memory
agentpack memory --json
agentpack memory --prune --dry-run
agentpack memory --prune --max-events 2000 --max-episodes 1000
```

`--prune` keeps the newest session events and episodic cases according to
runtime retention limits. Use `--dry-run` before writing. `agentpack doctor`
also reports memory row counts against those limits.

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
- **Git template hooks** (`~/.git-templates/hooks/`) — git copies these into every repo on `git init` / `git clone`. On `post-commit`, `post-merge`, `post-checkout` they call AgentPack's cross-platform `GitAutoRepack` hook runner and always exit cleanly. Repacking still happens only in opted-in repos; fresh clones without `.agentpack/config.toml` remain a safe no-op.
- **Shell cd hook** (`~/.zshrc`, `~/.bashrc`, or the PowerShell profile on Windows) — on `cd` or prompt refresh, repacks if stale **only in opted-in repos**. Never touches repos without `.agentpack/config.toml`. Never auto-inits.
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

If you installed an older AgentPack build and want to refresh copied git hooks after an upgrade, run:

```bash
agentpack global-repair-hooks
```

That repairs `~/.git-templates/hooks/`, reasserts `git config --global init.templateDir`, and updates the current repo's `.git/hooks/` to the safe `GitAutoRepack` path.

### `agentpack global-repair-hooks`

Refresh AgentPack's global git template hooks and the current repo's local git hooks after an upgrade.

```bash
agentpack global-repair-hooks
```

Use this when:
- old template hooks were copied before the `GitAutoRepack` runner existed
- a stale hook script still shells out directly instead of calling `agentpack hook`
- you want new clones and the current repo to pick up the latest non-destructive hook behavior immediately

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
agentpack doctor --fix
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

Slash commands (/agentpack, /agentpack-review, /agentpack-learn)
  ✓ Slash command installed (local): .claude/commands/agentpack.md
  ✓ Slash command installed (local): .claude/commands/agentpack-review.md
  ✓ Slash command installed (local): .claude/commands/agentpack-learn.md
  - Slash command not installed globally: ~/.claude/commands/agentpack.md — run: agentpack install --agent claude --global
  - Slash command not installed globally: ~/.claude/commands/agentpack-review.md — run: agentpack install --agent claude --global
  - Slash command not installed globally: ~/.claude/commands/agentpack-learn.md — run: agentpack install --agent claude --global

Some checks failed. Run the suggested commands above to fix.
```

The new checks in `doctor`:
- **Agent matrix audit**: `--agent all` checks Claude, Cursor, Windsurf, Codex, Antigravity, and Generic in one pass, including Codex `.codex/hooks.json` lifecycle hooks.
- **Local vs global hooks**: warns when Claude hooks are only in the per-project `.claude/settings.json` — context won't auto-inject in other repos
- **Slash command presence**: checks both local (`.claude/commands/`) and global (`~/.claude/commands/`) installations
- **Source checkout mismatch**: warns when you're inside an AgentPack source checkout but the `agentpack` executable imports the installed site-packages copy. Use `PYTHONPATH=src python -m agentpack.cli ...` or `pip install -e .` for local development.
- **Concurrent thread warning**: warns when active thread records overlap in the same worktree and branch.

`--fix` performs only safe AgentPack-managed repairs: refresh stale generated
rules/hooks and sync imported `.agentignore` blocks. It does not delete user
configuration, force thread mode, or run destructive git operations.

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

### `agentpack work --run`

Run the optional guarded loop with a generic local runner after preparing fresh
context. Keep this as an advanced verification path, not the main quickstart.
It is a proof harness around existing agents, not AgentPack's default workflow
and not an autonomous coding product.

Plain `agentpack work "task"` stays on the fast path: it writes the task,
refreshes context, records a bounded `task_memory` event, and returns. It does
not generate lessons, render dashboards, call providers, or block the coding
agent on coach output.

```bash
agentpack work "fix auth token expiry" --run --runner "claude < .agentpack/context.claude.md" --verify "pytest -q"
agentpack work "fix auth token expiry" --run --dry-run --runner "python scripts/agent.py" --verify "pytest -q"
```

New initialized repos include `[loop]` config so teams can opt in without extra
schema work. The runner is empty by default and must be set in
`.agentpack/config.toml` or passed with `--runner`; AgentPack never guesses which
coding agent to launch.

```toml
[loop]
enabled = true
runner = "claude < .agentpack/context.claude.md"
runner_adapter = ""
max_iterations = 10
verification_commands = ["pytest -q"]
require_verification = true
require_progress_update = true
require_clean_tree = true
```

Each iteration refreshes context, runs the configured shell command, runs the
verification commands, records progress in `.agentpack/progress.md`, and writes
structured events to `.agentpack/loop_events.jsonl`. When verification passes,
the loop stops at `ready_to_finish`; `agentpack finish` then enforces the final
completion checks. AgentPack does not auto-push or run destructive git commands.

Runner output can stay plain text, but AgentPack will read a JSON object from
the last output line when present:

```json
{"status":"changed","summary":"patched auth expiry","files_changed":["src/auth.py"],"blocker":""}
```

Supported statuses are `changed`, `no_change`, and `blocked`. A blocked or
no-change contract stops the loop with a diagnosis instead of burning
iterations.

The loop records phase history for `prepare_context`, `run_agent`,
`collect_diff`, `run_verification`, `diagnose_failure`,
`decide_continue_or_block`, and `finish_gate`. It also captures a dirty-diff
snapshot after each runner pass. If verification keeps failing and the diff does
not change, the loop blocks early and writes `.agentpack/loop_diagnosis.md`.
Blocked loops also write `.agentpack/loop_handoff.md`; passing loops write
`.agentpack/loop_acceptance.md`; each run writes `.agentpack/loop_risk_review.md`
and rollback patches under `.agentpack/loop_rollback/` when there was a dirty
baseline before an iteration.

Use `--runner-adapter claude|codex|cursor` when you want AgentPack to resolve a
known local runner command. Adapters are intentionally conservative: if the
matching executable is missing, AgentPack fails instead of guessing.

Compatibility matrix:

| Adapter | Local executable | Command shape | Notes |
|---|---|---|---|
| `claude` | `claude` | `claude --print --permission-mode acceptEdits "$(cat .agentpack/loop_runner_prompt.md)"` | Requires local Claude CLI auth and trusted temp repo. |
| `codex` | `codex` | `codex exec --ignore-user-config --sandbox workspace-write "$(cat .agentpack/loop_runner_prompt.md)"` | Requires local Codex CLI auth; ignores user config drift for reproducibility. |
| `cursor` | `cursor-agent` | `cursor-agent --print --force "$(cat .agentpack/loop_runner_prompt.md)"` | Requires Cursor agent CLI auth. |
| custom | any shell command | value passed to `--runner` | Best for deterministic scripts and CI smoke tests. |

---

### `agentpack loop-smoke`

Run a guarded-loop smoke test in a temporary fixture repo.

```bash
agentpack loop-smoke
agentpack loop-smoke --runner "my-agent-command"
agentpack loop-smoke --runner-adapter claude --json
```

Without `--runner`, AgentPack uses a deterministic local runner so CI can prove
the loop mechanics. With `--runner` or `--runner-adapter`, the same fixture tests
whether a real local runner can edit code and satisfy verification.

---

### `agentpack loop-rollback`

Restore the tracked worktree to the last recorded loop baseline or reverse the
current tracked diff when no baseline patch exists.

```bash
agentpack loop-rollback
agentpack loop-rollback --iteration 2
agentpack loop-rollback --json
```

Rollback is patch-based and only covers tracked git diff content.

---

### `agentpack loop-metrics`

Summarize historical loop outcomes from `.agentpack/loop_metrics.jsonl`.

```bash
agentpack loop-metrics
agentpack loop-metrics --json
```

The dashboard also shows run count, ready count, blocked count, and average
iterations.

---

### `agentpack install`

Install or refresh one agent integration without reinitializing project state.

```bash
agentpack install                      # auto-detect IDE
agentpack install --agent claude       # CLAUDE.md + .claude/settings.json hooks
agentpack install --agent cursor       # .cursorrules + .mdc + git hooks + VS Code tasks
agentpack install --agent windsurf     # .windsurfrules + git hooks + VS Code tasks
agentpack install --agent codex        # AGENTS.md + hooks + MCP config + agentpack@local + git hooks
agentpack install --agent antigravity  # GEMINI.md + git hooks + VS Code tasks
```

All installs are idempotent — safe to re-run, merge with existing config, never duplicate.
Claude installs also refresh `/agentpack`, `/agentpack-review`, and `/agentpack-learn`.
Codex installs refresh the local plugin cache, enable `agentpack@local`, and
disable stale enabled AgentPack marketplace entries so Codex loads the same
version as the installed CLI.
The review slash command runs the local two-stage review bundle; the learning
slash command uses current local AgentPack session context and keeps the user
learning statement at the end for prompt caching.

---

### `agentpack upgrade`

Refresh the current repo's auto-detected IDE or agent integration after
upgrading `agentpack-cli`. This is the post-upgrade repair path: it rewrites
stale AgentPack rule blocks, refreshes agent hooks/tasks/plugin cache, and
updates already-installed global AgentPack git/shell hooks. It does not opt a
machine into new global automation unless AgentPack hooks were already present.

```bash
agentpack upgrade                 # auto-detect the current IDE/agent
agentpack upgrade --agent codex   # AGENTS.md + hooks + MCP config + agentpack@local + local plugin cache
agentpack upgrade --agent cursor  # Cursor rules/hooks
agentpack upgrade --agent all     # refresh every supported repo integration
agentpack upgrade --no-repair-existing-global-hooks
```

`--agent auto` does not default to Codex. It uses the same host detection as
`agentpack init`. The Codex plugin package is installed only when the resolved
agent is `codex` or when `--agent codex` is passed explicitly. Codex doctor
checks that `agentpack@local` is enabled and that older AgentPack plugin sources
are not still active.

---

### `agentpack repair`

Repair missing or drifted integration files. It uses the same installer contract as `init` and `install`, but is named for the "make this repo healthy again" workflow.

```bash
agentpack repair                 # repair auto-detected agent
agentpack repair --agent codex   # AGENTS.md + hooks + MCP config + agentpack@local + git hooks
agentpack repair --agent all     # repair every supported integration
```

---

### `agentpack guard`

Run the pre-edit safety gate an agent can execute instead of only reading instructions.

```bash
agentpack guard                                      # check current agent + context
agentpack guard --refresh-context                   # refresh stale/missing context
agentpack guard --agent codex --repair-stale        # repair stale Codex rules/hooks
agentpack guard --agent auto --repair-stale --refresh-context
agentpack guard --thread codex-local --refresh-context
```

This is the strongest non-native enforcement AgentPack can provide: tools that run commands get a failing exit code when context is unsafe, and an automatic repair/refresh path when allowed.

---

### `agentpack migrate`

Repair stale AgentPack integrations across existing repos after upgrading.

```bash
agentpack migrate --path . --agent auto
agentpack migrate --path ~/src --discover --agent all
agentpack migrate --path ~/src --discover --agent codex --refresh-context
agentpack migrate --path ~/src --discover --dry-run
```

Use this when older repos still have stale `.cursorrules`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.windsurfrules`, VS Code tasks, or hook files. `--discover` scans nested repo folders, `--dry-run` reports without writing, and `--refresh-context` regenerates packs after repair.

---

### `agentpack summarize`

Build or refresh the offline summary cache. **No API calls, ever.**

```bash
agentpack summarize              # build summaries for all files not yet cached
agentpack summarize --refresh    # force rebuild all
```

Summaries are built with parallel AST/regex analysis — no network, no tokens spent. Run once after `init`. After that, pack automatically rebuilds summaries only for changed files (hash-keyed cache).

---

### `agentpack start`

Write a task and run the recommended context refresh workflow.

```bash
agentpack start "fix auth session bug"
agentpack start "fix auth session bug" --pack-only
agentpack start "fix auth session bug" --thread codex-local
AGENTPACK_THREAD_ID=codex-local agentpack start "fix auth session bug" --thread auto
```

By default, `start` writes the task and runs
`guard --agent auto --repair-stale --refresh-context`. Use `--pack-only` when
you only want a fresh pack. Thread mode is still explicit: pass `--thread <id>`
or `--thread auto` to write under `.agentpack/threads/<id>/`.

---

### `agentpack work`

The highest-level everyday entrypoint. It initializes AgentPack when needed,
writes the task, refreshes context, and prints next-step diagnostics.

```bash
agentpack work "fix auth session bug"
agentpack work "fix auth session bug" --thread codex-local
agentpack work "fix auth session bug" --pack-only --workspace apps/web
agentpack work "fix auth session bug" --no-init --no-next
agentpack work "fix auth session bug" --json
```

`work` composes existing commands instead of inventing a separate path:
`init --yes` when missing, then `start`, then `next`. It does not silently use
ambient thread env vars; pass `--thread auto` when you want documented thread
env resolution.

---

### `agentpack finish`

Finish a task with the common release-quality housekeeping in one command.

```bash
agentpack finish --since main
agentpack finish --since HEAD~1 --task "fix auth session bug"
agentpack finish --thread codex-local --archive-thread
agentpack finish --skip-checks --skip-benchmark-capture
agentpack finish --allow-high-risk
agentpack finish --json
```

By default, `finish` writes a selection diagnosis, optionally captures a
benchmark case when `--since` is supplied, runs `dev-check`, and marks task
state `done`. With `--thread` it writes scoped state; with `--archive-thread` it
also appends a done row to the thread index.

When a Ralph Loop state applies, `finish` also requires a passing loop
verification and a post-run source diff. Dirty files that existed before loop
initialization do not satisfy this gate. Use `--allow-empty-capture` only for
intentional no-op work.

`finish` also blocks high-risk loop diffs until a reviewer inspects
`.agentpack/loop_risk_review.md`; pass `--allow-high-risk` only after that
review.

Runner examples:

```bash
agentpack work "fix auth" --run --runner-adapter claude --verify "pytest -q"
agentpack work "fix auth" --run --runner-adapter codex --verify "pytest -q"
agentpack work "fix auth" --run --runner "python scripts/local_agent.py" --verify "pytest -q"
agentpack work "fix auth" --run --acceptance "login works" --acceptance "expired token is rejected" --verify "pytest -q"
```

---

### `agentpack learn`

Create local learning artifacts from the current task and git changes. The
output is designed for both the developer and future coding agents: developer
notes explain what changed and what to practice next, while agent lessons
capture compact repo-specific rules that can be injected into later context
packs.

```bash
agentpack learn
agentpack learn --today
agentpack learn --since HEAD~1
agentpack learn --output .agentpack/review.md
agentpack learn --json
agentpack learn --llm-prompt
agentpack learn --pr-comment
agentpack learn --provider-preview
agentpack learn --provider-command "python scripts/learn_provider.py"
agentpack learn --dashboard
agentpack learn --team-export
agentpack learn --ci
agentpack learn --skills
agentpack learn --drills
agentpack learn --feedback helpful --feedback-target "skill:CLI design" --feedback-note "Useful cards"
agentpack learn --rename-skill "CLI design=>CLI workflow design"
agentpack learn --suppress-skill "generic development"
```

Default outputs:

- `.agentpack/learning.md`
- `.agentpack/daily-summary.md` with `--today`
- `.agentpack/skills-progress.json`
- `.agentpack/agent-lessons.md`
- `.agentpack/learning.prompt.md` with `--llm-prompt`
- `.agentpack/pr-learning-comment.md` with `--pr-comment`
- `.agentpack/learning-dashboard.html` with `--dashboard`
- `.agentpack/team-lessons.md` with `--team-export`
- `.agentpack/learning-feedback.jsonl` with `--feedback`
- `.agentpack/learning-sessions.jsonl` for on-demand coach questions

The command reads `.agentpack/task.md`, changed files, and bounded redacted
diffs. It does not call a hosted service by default. The human-facing summary
explains changed files, concepts, decisions, risks, tests, learning cards, quiz
questions, skill evidence, and next practice. Summary, decision, risk, and test
claims include `claim_citations` so top-level learning statements are traceable
to changed-file evidence. The normal `learn` flow validates those citations
against the current checkout before writing JSON or Markdown output, so provider
or generated citations can surface missing files, invalid line ranges, stale
hashes, and bad external support hashes. Agent lessons are compact repo-specific
rules ranked for future AgentPack context packs when
`learning.inject_agent_lessons = true`.

`--today` uses calendar-day aggregation: committed files since local midnight
plus current dirty files. `--llm-prompt` writes a source-backed prompt for
external LLM refinement without sending data anywhere. `--pr-comment` writes a
short Markdown summary suitable for pasting into a pull request.
`--provider-preview` prints the bounded provider payload without making a
network call. `--provider-command` runs a local JSON-in/JSON-out command to
enrich the report; AgentPack sends the bounded report JSON on stdin and accepts
LearningReport-compatible JSON fields on stdout. This keeps hosted model,
company LLM gateway, or custom rules-engine integration behind an explicit local
command boundary. `--dashboard` writes a static HTML learning dashboard for
IDE/browser review, including recent task memory and queued weak spots from
on-demand quiz/interview sessions. `--team-export` writes a shareable lessons file that omits
personal skill history. `--ci` prints a quality report and exits non-zero when
learning is too generic or lacks changed-file evidence. `--skills` and
`--drills` turn the local skill map into a quick progress view and
next-practice list.

Feedback can be broad (`--feedback helpful`) or targeted. Supported targets are
`skill:<name>`, `lesson:<text>`, `rename:<old=>new>`, and `merge:<old=>new>`.
Targeted not-helpful feedback suppresses noisy skills or lessons in future
reports; targeted helpful feedback raises confidence for matching future output.

---

### `agentpack task`

Show, set, or clear task files without hand-editing `.agentpack/task.md`.

```bash
agentpack task show
agentpack task show --thread codex-local --json
agentpack task set "fix billing webhook retry" --guard
agentpack task set "fix billing webhook retry" --pack --mode deep
agentpack task clear
```

Global mode writes `.agentpack/task.md`. Thread mode writes
`.agentpack/threads/<id>/task.md`. `task set --pack` delegates to `pack`;
`task set --guard` delegates to the guard/refresh workflow.

---

### `agentpack pack`

Generate a context pack. `agentpack pack --task "<task>"` writes the task into `.agentpack/task.md` and packs it. `--task auto` reads `.agentpack/task.md`, then falls back to git context, and is the default when the flag is omitted.

```bash
printf '%s\n' "fix auth session bug" > .agentpack/task.md
agentpack pack                                # auto-detects your IDE
agentpack pack --task "fix auth session bug"  # write task + pack in one command
agentpack pack --agent claude                 # explicit agent
agentpack pack --workspace apps/web
agentpack pack --thread codex-local           # scoped task/context for one agent thread
AGENTPACK_THREAD_ID=codex-local agentpack pack --thread auto

# Only include changes since a git ref
printf '%s\n' "review these changes" > .agentpack/task.md
agentpack pack --since main

# Broad curated context stays inside pack; no separate bundle command.
printf '%s\n' "share broad repo context for review" > .agentpack/task.md
agentpack pack --mode deep

# Watch mode — re-packs on every file change
printf '%s\n' "refactor auth" > .agentpack/task.md
agentpack pack --session
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--agent` | `auto` | Target agent (`auto` \| `claude` \| `cursor` \| `windsurf` \| `codex` \| `antigravity` \| `generic`). `auto` detects the active IDE from env and project files. |
| `--task` | `auto` | Task text to write before packing, or `auto` to read `.agentpack/task.md` / git context. |
| `--mode` | `balanced` | Budget mode: `lite`, `balanced`, `deep` |
| `--budget` | 0 (uses config default 40000) | Token budget |
| `--workspace` | — | Restrict packing to a monorepo workspace and write `.agentpack/workspaces/<workspace>/context.md` |
| `--since` | — | Only include files changed since this git ref |
| `--session` | off | Re-pack on every file change (watch mode) |
| `--refresh` | off | Force rebuild summaries before packing |
| `--thread` | — | Use `.agentpack/threads/<id>/task.md`, `context.md`, `context.claude.md`, `task_state.md`, and `pack_metadata.json` instead of global task/context files. `--thread auto` resolves documented thread env vars. |

**Budget modes:**

| Mode | What's included |
|------|----------------|
| `lite` | Cheap ranked map before deeper file reads |
| `balanced` | Changed files + deps + reverse deps + tests + capped summaries |
| `deep` | Everything in balanced + docs + more full-content files, uncapped summaries |

When the task asks for review, sharing, audit, or a repository overview, `pack`
adds curated broad repo context to the same `.agentpack/context.md` artifact:
module summaries, semantic clusters from cached file summaries, entrypoints,
config/docs/test inventory, sharing receipts, freshness metadata, and redaction
warnings. This is a curated context artifact, not an exact whole-repo dump.
Packs also write a colocated `citations.json` manifest, usually
`.agentpack/citations.json`, with selected-file citations, source hashes,
receipt provenance, diff-hunk line citations, and broad-context summary
sources. Markdown renderers show compact source anchors; the JSON manifest is
the machine-readable contract. Validators reject citation ranges outside the
current file, reject hash-bearing citations when the source file has changed,
verify optional support snippets against the cited span, and require URL plus
retrieval provenance for external citations. External citations with
`support_text` must also include a matching `sha256:<normalized-support-text>`
`content_hash`. Callers that are allowed to use network can pass
`verify_external_content=True` to the validator to re-fetch the URL and confirm
the current response still contains the cited support text; normal AgentPack
commands keep this off to preserve local-first behavior.

`balanced` is the standard mode for normal agent work and benchmark claims.

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

**Multi-thread execution context:** omit `--thread` for the legacy global `.agentpack/task.md` and `.agentpack/context.md` flow. Plain `pack`, `status`, and `guard` do not silently adopt `CODEX_THREAD_ID`, `CLAUDE_SESSION_ID`, or other host session env vars. Pass `--thread <id>` or `--thread auto` when multiple agents are changing the same project so each thread gets isolated task/context state under `.agentpack/threads/<id>/`. Thread ids are sanitized to letters, numbers, `_`, `.`, and `-`.

`--thread auto` resolves in this order: `AGENTPACK_THREAD_ID`, `CODEX_THREAD_ID`, `CLAUDE_SESSION_ID`, `CURSOR_SESSION_ID`. A concrete `--thread <id>` wins over env vars.

Each thread pack appends `.agentpack/thread_index.jsonl` with task hash, branch, worktree, selected files, dirty files, status, and timestamp. If another active thread from the last 24 hours is on the same branch and worktree with overlapping selected/dirty files, the rendered context includes `## Concurrent Context` as a warning. It does not block edits; it tells the agent to coordinate or move to a separate branch/worktree.

Rendered packs now include `## Execution State` after freshness. AgentPack reads optional `.agentpack/threads/<id>/task_state.md` first, then `.agentpack/task_state.md`, parsing `Status:`, `Summary:`, and checklist counts from `- [x]`, `- [ ]`, and `- [!]`. If no task state exists, it derives status from git and reports lightweight Docker/Compose availability without mutating containers or using the network.

---

### `agentpack next`

Ask AgentPack what repeated setup or repair step should happen next.

```bash
agentpack next
agentpack next --json
agentpack next --fix
agentpack next --fix-all-safe
```

`next` checks for an uninitialized repo, missing task, stale context, active
thread conflicts, and noisy recent pack diagnostics. With `--fix`, it only runs
safe refresh work for stale context; it does not initialize projects, delete
files, force thread mode, or change git state.
`--fix-all-safe` can initialize a missing `.agentpack/config.toml`, refresh stale
context, and write `.agentpack/selection_diagnosis.md`. It still does not apply
ignore suggestions, delete thread directories, resolve thread conflicts, or
touch git history.

---

### `agentpack threads`

Inspect and manage thread-scoped context records from `.agentpack/thread_index.jsonl`.

```bash
agentpack threads
agentpack threads --active
agentpack threads --conflicts
agentpack threads --json
agentpack threads archive codex-local --summary "Release docs done"
agentpack threads prune --older-than 7d          # dry-run
agentpack threads prune --older-than 7d --yes    # delete old scoped dirs
```

`--active` keeps rows seen in the last 24 hours whose status is not `done`.
`--conflicts` shows same-worktree, same-branch overlaps using the same warning
logic as `pack --thread <id>`. Archive is non-destructive: it appends a `done`
row and writes scoped `task_state.md`; it does not delete context. Prune deletes
only `.agentpack/threads/<id>/` directories and only when `--yes` is present.

---

### `agentpack state`

Show or update optional execution state files.

```bash
agentpack state show
agentpack state show --thread codex-local --json
agentpack state set in_progress --summary "Rendered budget done; thread state pending."
agentpack state done --thread codex-local --summary "Release prep completed."
```

By default, `state` writes `.agentpack/task_state.md`. With `--thread`, it writes
`.agentpack/threads/<id>/task_state.md`. Updates preserve existing checklist
lines while replacing `Status:` and `Summary:`.

Valid statuses are `planned`, `in_progress`, `blocked`, and `done`.

---

### `agentpack route`

Route a task without writing context files. This is the CLI debug/admin surface for the same router used by MCP `route_task`.

```bash
agentpack route --task "fix flaky payment webhook test"
agentpack route --task "fix flaky payment webhook test" --json
agentpack route --task "fix flaky payment webhook test" --format json
pipx run --spec agentpack-cli agentpack route --task "fix auth token expiry"
```

Output includes relevant files, applied rules, recommended skills, suggested commands, safety warnings, and an agent prompt. It uses the existing AgentPack file ranker in memory and does not write `.agentpack/context.md`. `--json` is the stable machine-readable alias; `--format json` remains supported.

---

### `agentpack toon-validate`

Validate TOON syntax for agent-facing artifacts. Review artifacts can also be checked against the review-specific schemas.

```bash
agentpack toon-validate .agentpack/reviews/pr-123/run/understanding.toon
agentpack toon-validate .agentpack/reviews/pr-123/run/findings.toon --format json
agentpack toon-validate .agentpack/reviews/pr-123/run/understanding.toon --schema review-understanding
agentpack toon-validate .agentpack/reviews/pr-123/run/findings.toon --schema review-findings --allow-json --write-canonical
```

By default the validator requires `@format toon` as the first non-empty line. Use `--allow-missing-format` only for legacy files.
Use `--schema review-understanding` or `--schema review-findings` for review-stage files. With `--allow-json --write-canonical`, valid JSON that matches the selected schema is rewritten as canonical TOON; malformed output still fails. MCP-only agents can call `validate_toon(..., return_canonical=true)` to receive canonical TOON in the response without using the CLI rewrite path.

---

### `agentpack skills`

Inspect or index installed skills and rule files.

```bash
agentpack skills scan
agentpack skills index
agentpack skills recommend --task "fix flaky payment webhook test" --explain
agentpack skills feedback --task "fix auth" --used-skill pytest-debugging --tests-passed --user-feedback helpful
```

`scan` prints discovered artifacts. `index` writes `.agentpack/skills_index.json` with metadata only; raw skill and rule bodies are omitted from the index. `recommend` runs the route planner and prints confidence-based skill recommendations with load paths and reasons. `feedback` appends a local `.agentpack/skill_feedback.jsonl` record; repeated helpful use gives that skill a small future boost.

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

### `agentpack review`

Prepare the full two-stage PR review bundle for the current branch or checked-out PR.

```bash
agentpack review
agentpack review "focus on backward compatibility"
agentpack review --pr 123 "focus on backward compatibility"
agentpack review --check
agentpack review --check --dry-run-post
agentpack review --check --post-inline-comments
agentpack review --resume <run_id>
```

Writes:

- `.agentpack/review-preflight.json`
- `.agentpack/review.prompt.md`
- `.agentpack/review-understanding.prompt.md`
- `.agentpack/review-judge.prompt.md`
- `.agentpack/review-understanding.template.toon`
- `.agentpack/review-findings.template.toon`
- `.agentpack/reviews/<branch-prefix>/<run_id>/preflight.json`
- `.agentpack/reviews/<branch-prefix>/<run_id>/runbook.md`
- `.agentpack/reviews/<branch-prefix>/<run_id>/context.md`
- `.agentpack/reviews/<branch-prefix>/<run_id>/citations.json`
- `.agentpack/reviews/<branch-prefix>/<run_id>/understanding.prompt.md`
- `.agentpack/reviews/<branch-prefix>/<run_id>/judge.prompt.md`
- `.agentpack/reviews/<branch-prefix>/<run_id>/understanding.template.toon`
- `.agentpack/reviews/<branch-prefix>/<run_id>/findings.template.toon`
- `.agentpack/reviews/<branch-prefix>/<run_id>/understanding.toon`
- `.agentpack/reviews/<branch-prefix>/<run_id>/findings.toon`
- `.agentpack/reviews/<branch-prefix>/<run_id>/inline-review-payload.json` when inline PR comment payloads are built
- `.agentpack/reviews/<branch-prefix>/<run_id>/inline-review-dry-run.json` when `--dry-run-post` validates the payload without calling GitHub
- `.agentpack/reviews/<branch-prefix>/<run_id>/posted-review.json` when inline PR comments are posted or intentionally skipped because there are no findings

`review` stays local-first. It does not replace `gh pr view`, `git diff`, or
direct code inspection. Instead it captures the latest available PR metadata,
selects a diff base, lists changed files and related tests, records stale/dirty
warnings, writes run-scoped broad context, and renders the full
understanding-plus-judge prompt bundle for a host agent to perform the actual
review. Review context is written under the review run directory instead of
overwriting the active `.agentpack/context.md` pack.

Review artifacts are claim-grounded. `understanding.toon` and `findings.toon`
must cite repo facts with valid `path:line` evidence. Resume/preflight
validation first canonicalizes safe schema-matching JSON, fenced output, or
missing-format TOON into canonical TOON. If the output is malformed, review
validation writes `<stage>-toon-repair.md` next to the artifact with a copy-fill
template and recovery instructions. It then rejects findings with prose-only
evidence, missing locations, files that do not exist, line ranges outside the
current checkout, or stale hash-bearing source citations. Finding evidence and
Stage 1 resolved-context fields such as `referenced_symbols`, `callers`, and
`local_convention_refs` also have to overlap the cited span enough to catch
arbitrary `path:line` references that do not support the claim. For stricter
meaning-level review, set `AGENTPACK_CITATION_SEMANTIC_COMMAND` to a local
JSON-in/JSON-out command; review validation sends each mechanically supported
claim/citation pair on stdin and rejects it when the command returns
`{"supported": false, "reason": "..."}`.

After Stage 2 validates, `agentpack review --check --dry-run-post` validates the
same inline GitHub review payload and writes it to `inline-review-payload.json`
without calling GitHub. `agentpack review --check --post-inline-comments` posts
validated findings as one GitHub PR review with inline comments. Posting
requires a PR-bound preflight, a GitHub repo slug, a PR head SHA, and finding
locations that map to right-side lines in the PR diff. If any finding cannot be
posted inline, both dry-run and posting fail closed instead of falling back to a
broad summary comment. Successful posts are recorded in `posted-review.json` so
re-running the check does not duplicate PR comments.

The positional argument is optional reviewer context. It shapes prioritization
only; it must not replace code evidence.
Fresh runs are the default. Interrupted work is resumed only when
`--resume <run_id>` is passed explicitly.

---

### `agentpack ignore sync`

Refresh imported generated/noisy rules inside `.agentignore` without touching your manual entries.

```bash
agentpack ignore sync
agentpack ignore sync --dry-run
agentpack ignore sync --check
```

Use this after editing `.gitignore`, nested workspace ignores, or `.git/info/exclude`. `doctor` also warns when the imported `.agentignore` block is stale.

### `agentpack ignore suggest|apply`

Suggest and optionally apply `.agentignore` improvements from repeated noisy
large paths, generated directories, build outputs, and recent pack metrics.

```bash
agentpack ignore suggest
agentpack ignore suggest --json
agentpack ignore apply          # dry-run
agentpack ignore apply --yes    # writes .agentignore
```

`apply` is conservative: without `--yes`, it prints the rules it would add and
the exact command to apply them. Confirmed writes avoid duplicate rules.

---

### `agentpack watch`

Watch for file and task changes, refresh context automatically.

```bash
agentpack watch                        # refresh context on source/task changes
agentpack watch --debounce 3.0         # wait 3s after last change before refresh
```

Default installs include `watchdog` and use native filesystem events. If `watchdog`
is unavailable in an editable checkout or distro-managed environment, watch mode
falls back to polling. Context is refreshed whenever source files or
`.agentpack/task.md` change.

---

### `agentpack claude`

Launch Claude CLI with an up-to-date context.

```bash
agentpack claude
```

Requires an initialized project (`agentpack init`). Refreshes context, prints the context path, then launches `claude` if found. Transparent about what it does — no fake prompt injection.

---

### `agentpack mcp`

Start AgentPack's stdio MCP server. This is a low-level server/debug entrypoint; normal developers should use `agentpack install`, `agentpack repair`, and `agentpack doctor` so the agent host launches MCP from config.

```bash
agentpack repair --agent codex
agentpack doctor --agent codex
# restart Codex, then call agentpack_readiness() from the host
```

Manual `agentpack mcp` execution is diagnostic only. Run it once with a short timeout:

- If it exits with a command/import error, fix setup and fall back to CLI/direct search.
- If it waits until timeout, the local MCP server is runnable but the host did not expose tools; run `agentpack repair --agent <agent>`, restart the host, and fall back to CLI/direct search.
- Do not keep `agentpack mcp` running manually.

If the MCP extra is missing:

```bash
pipx inject agentpack-cli "agentpack-cli[mcp]"
```

For source checkouts:

```bash
python -m pip install -e ".[mcp]"
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
| `readiness()` | Prove the current host can call AgentPack MCP tools; returns server, version, tool list, CLI command surface, and latest context provenance. |
| `route_task(task)` | Read-only task router. Returns relevant files, applied rules, recommended skills, suggested commands, safety warnings, and an agent prompt as JSON. |
| `get_skills()` | Return discovered skill/rule inventory as JSON. |
| `get_skill(name_or_path)` | Return one skill's raw `SKILL.md` content after `route_task` recommends it. |
| `explain_route(task)` | Return route JSON with positive skill score reasons for debugging router choices. |
| `start_task(task, mode, budget, max_tokens, thread_id)` | Recommended MCP-first entry point. Writes global or scoped task.md, generates a ranked pack, and returns packed markdown. |
| `pack_context(task, mode, budget, max_tokens, thread_id)` | Generate a ranked context pack. If `task` is provided, writes global/scoped task.md; if omitted, reads task.md or infers from git. |
| `get_context(thread_id)` | Return the latest global/scoped pack. If task.md or the repo snapshot differs from the packed metadata, it auto-refreshes before returning; otherwise it prepends a freshness header. |
| `refresh()` | Refresh using the current `task.md` or git-inferred task. |
| `explain_file(path, task)` | Show score, inclusion mode, reasons, symbols, imports, and importers for one file. |
| `get_related_files(path, depth)` | Return import-graph neighbours and related tests for a file. |
| `get_delta_context(max_files)` | Return the latest selected-file delta plus top current selected files. Useful for cheap prompt-time refresh checks. |
| `validate_toon(content, path, require_format, schema, allow_json, return_canonical)` | Validate TOON or review-stage JSON fallback. With `return_canonical=true`, includes canonical TOON in the response when validation succeeds. |
| `get_stats()` | Return latest pack stats, savings, selection quality, excluded files, and benchmark-style signals. |

**Live MCP exposure:** CLI `doctor` verifies MCP registration and local runtime readiness. It cannot prove the current agent host actually exposes AgentPack tools; call `readiness()` from that host. If it returns JSON, live exposure is confirmed.

**Staleness detection:** `get_context()` compares the current task file, snapshot hash, and git state against the latest pack metadata. If `.agentpack/task.md` or the repo snapshot changed, it blocks for a fresh pack and prepends:

```
> Context auto-refreshed because .agentpack/task.md differs from the packed task ...
```

If auto-refresh fails, it falls back to the cached context with a loud stale warning and asks the agent to call `pack_context()` again.

Static markdown cannot refresh itself, so rendered packs include a machine-readable fallback header:

```text
<!-- agentpack:freshness
@format toon
@root agentpack_freshness
active_context: mcp
fallback_context: markdown
refresh_required: false
mcp_refresh_tool: agentpack_get_context
cli_refresh_command: agentpack guard --agent auto --repair-stale --refresh-context
-->
```

Claude prompt hooks stay inactive until a real task exists in `.agentpack/task.md`, then emit lightweight freshness hints instead of background repacks. Non-MCP rule files and VS Code folder-open tasks use the installed command surface for refresh/readiness. If you want prompt submit to block for a fresh pack when context is stale, set `blocking_task_refresh = true` under `[hooks]` in `.agentpack/config.toml`.

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
agentpack benchmark --task "fix auth bug" --compare        # compare lite/balanced/deep
agentpack benchmark --init                                 # scaffold .agentpack/benchmark.toml
agentpack benchmark --results-template                     # scaffold publishable results note
agentpack benchmark                                        # run all cases in benchmark.toml
agentpack benchmark --sample-fixtures                      # source checkout demo evals
agentpack benchmark --release-gate                         # public release benchmark gate
agentpack benchmark --public-suite --reproduce v0.3.20      # reproducible public suite
agentpack benchmark --public-repos                         # real public commit evals
agentpack benchmark --misses                               # explain expected-file misses
agentpack benchmark --prove-targets                        # fail if recall/token precision targets miss
agentpack benchmark --public-table                         # write benchmarks/results/*-public.md
agentpack benchmark --from-history 5 --write-cases          # scaffold cases from recent packs
agentpack benchmark capture --since HEAD~1 --task "fix auth bug"
agentpack benchmark capture --since main --task "fix auth bug" --anonymous-report
agentpack benchmark e2e --cases .agentpack/e2e_cases.toml --agent-command 'bash -lc "codex exec --cd {repo} \"$(cat {prompt})\""'
agentpack benchmark e2e-report --baseline no-context --treatment agentpack --markdown
```

`--release-gate` expands to the intended public release proof path:
`--public-repos --prove-targets --misses --public-table`, using
`benchmarks/public-repos.toml` by default. It accepts `--public-repos-cache`
and `--refresh-public-repos`. `--sample-fixtures` is a source-checkout
regression smoke path, not the release gate. `--public-suite --reproduce
v0.3.20` is the documented one-command reproduction path for the expanded public
suite. Public repo manifests can mix
pinned `[[repos.cases]]` entries with `sample_history = N`; sampled cases use
real commit subjects and changed files from recent first-parent, non-merge
commits, filtered by `include_globs`, `exclude_globs`, and `max_changed_files`.

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

**Compare mode** shows modes side-by-side:

```
Mode comparison: fix auth token expiry

   mode        tokens   saving   files   time
   lite         8,000    95.7%      50   0.18s
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

For agent outcome A/Bs, `benchmark e2e` runs guarded cases across strategies
such as `no-context` and `agentpack`. `benchmark e2e-report` compares task
success, expected-file touch rate, tool calls saved, tokens saved, token cost
saved, time-to-first-correct-file, and duration.

Add `task_type` to group results by workflow area. Benchmark summaries report average precision, recall, F1, and token noise by type, so a repo can show "backend-api is good, frontend-web is noisy" instead of hiding that under one aggregate.

`benchmark capture` reduces benchmark-case bookkeeping after real work:

```bash
agentpack benchmark capture --since main --task "fix billing retry handling"
agentpack benchmark capture --since HEAD~1 --task "smoke capture" --allow-empty
```

It infers `expected_files` from `git diff --name-only <ref> HEAD` and appends a
case to `.agentpack/benchmark.toml`. It refuses empty diffs unless
`--allow-empty` is present. Add `--anonymous-report` to write
`.agentpack/benchmark-report.md` and `.agentpack/benchmark-report.json` with
aggregate language mix, case count, recall/token precision when measured, miss
count, and `no_source_code_uploaded = true`. `--from-history --write-cases` can scaffold cases
from recent AgentPack metrics, but those cases are recall evidence only after
you fill `expected_files`.

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

### `agentpack dashboard`

Generate a static local dashboard from existing `.agentpack/` artifacts.

```bash
agentpack dashboard
agentpack dashboard --open
agentpack dashboard --json
```

The dashboard writes `.agentpack/dashboard.html` by default. It is local-only,
uses inline CSS, and does not load remote scripts or assets. Missing artifacts
render empty states with suggested commands such as `agentpack pack --task auto`,
`agentpack learn`, and `agentpack benchmark --init`.

`--json` prints the normalized dashboard snapshot to stdout instead of writing
HTML. Use it when you want to inspect the underlying project, context, selected
files, skill feedback, learning artifacts, benchmark metrics, and suggested
actions programmatically.

To build a real usefulness signal for your repo:

```bash
agentpack benchmark --sample-fixtures

agentpack benchmark --init
# edit .agentpack/benchmark.toml with real tasks + files you actually changed
agentpack benchmark --compare --misses --prove-targets
```

`--sample-fixtures` runs bundled FastAPI, Next.js, mixed Python/TypeScript, Django REST-style, Go service, and Rails-style fixture evals from an AgentPack source checkout. It is a smoke test, not a claim about your repo.

For an 8+ usefulness signal, use `benchmark.toml` with real third-party or customer-style repos: 5-20 historical tasks, `task_type` labels, the files actually changed for each task, and `--compare` results for recall, F1, rank@K, and token noise. That is better than trusting generic benchmarks because it tells you whether AgentPack selects the files that matter in code the package has never seen.

See [benchmarks/README.md](https://github.com/vishal2612200/agentpack/blob/main/benchmarks/README.md) for the public smoke-suite fixtures, quality gates, and the recommended miss-debugging workflow.

---

### `agentpack diagnose-selection`

Combine latest pack stats, largest token consumers, pack diagnostics, and recent
benchmark misses into concrete selection tuning advice.

```bash
agentpack diagnose-selection
agentpack diagnose-selection --json
agentpack diagnose-selection --write
```

`--write` saves `.agentpack/selection_diagnosis.md`. The output points to
specific actions such as rewrite the task, explain a file, ignore generated
paths, reduce mode, or add a benchmark case.

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

### `agentpack eval`

Run deterministic failure evals. AgentPack does not run the coding agent and
does not use an LLM judge; it verifies the current or replayed worktree with
commands and diff policies.

```bash
agentpack eval --init
# edit .agentpack/evals.toml with real failures and checks
agentpack eval
agentpack eval --case auth-timeout --prove-targets
agentpack eval --capture auth-timeout --failure-class context --check "pytest tests/test_auth.py -q"
agentpack eval --watch --until-pass
agentpack eval --replay --prove-targets
agentpack eval --variant baseline
agentpack eval --variant agentpack
agentpack eval --compare-variants baseline:agentpack
agentpack eval --memory-ab --prove-targets
agentpack eval --memory-ab --memory-ab-checks --prove-targets
agentpack eval --from-episodes
agentpack eval --ci-template
agentpack eval --report
```

`--memory-ab` compares selected-file recall and selection noise with memory
feedback disabled versus enabled. Add `--memory-ab-checks` to run deterministic
eval checks under both memory profiles as a task-success guard. `--from-episodes`
promotes failed episodic memory records into regression eval cases so repeated
agent failures become deterministic harness checks.

Example case:

```toml
[[cases]]
id = "auth-timeout"
task = "fix auth token timeout"
failure_class = "context"
failure_source = "agent_failed"
base_ref = "HEAD"
patch_file = ".agentpack/evals/auth-timeout.patch"
required_changed_files = ["src/auth/token.py"]
forbidden_changed_files = ["src/db/**"]
max_changed_files = 5
max_changed_lines = 250
agent = "codex"
context_file = ".agentpack/context.md"
context_hash = "..."
selected_files = ["src/auth/token.py", "tests/test_auth.py"]
citation_manifest = ".agentpack/citations.json"
min_citation_coverage = 0.75
max_invalid_citations = 0
require_review_citations = true

[[cases.checks]]
name = "tests"
command = "pytest tests/test_auth.py -q"
timeout_s = 120
retries = 1 # optional, marks pass-after-fail checks as flaky
```

Use `eval` after an agent run: capture the real failure, add deterministic
checks such as tests, typecheck, lint, schema validation, API contract tests,
diff size, forbidden files, or golden outputs, then rerun until the harness
passes. Citation checks can enforce that packed/review context stayed
claim-grounded by reading the `citation_manifest`. The model can propose; the
harness must verify.

For hands-free local iteration, keep `agentpack eval --watch --until-pass`
running in a terminal while the agent or developer edits. It reruns when the
case file, patch artifacts, golden files, or git diff content changes and stops
when all deterministic checks pass. `--capture` stores the current patch under
`.agentpack/evals/<case-id>.patch` plus context metadata; `--replay` checks out
`base_ref` into an isolated git worktree, applies that patch, and runs the same
deterministic checks there. When pack metadata points to a citation manifest,
captured cases automatically add citation coverage and invalid-citation gates.
To measure AgentPack's contribution, run the same case with `--variant baseline`
and then with `--variant agentpack`;
`--compare-variants baseline:agentpack` reports which cases improved, regressed,
stayed unchanged, or still need both sides. Use `--ci-template` to scaffold a
GitHub Actions workflow for `benchmarks/evals.toml`.

Eval files are executable trust boundaries: commands in `checks.command` run
locally and in CI. Review eval TOML from contributors with the same care as
shell scripts or workflow files.

Captured patch artifacts are secret-scanned with the same local redactor used
for context packs before they are written. If a patch line contains a real
secret, the artifact stores `[REDACTED:<type>]` and the case records
`patch_redaction_warnings`. Secret-bearing patches may replay with redacted
values; replace secrets with safe fixture values when exact replay matters.

---

### `agentpack status`

Check whether the context pack is stale.

```bash
agentpack status
agentpack status --deep
agentpack status --thread codex-local
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

### `agentpack release-check`

Run the local release gate without mutating tracked files.

```bash
agentpack release-check
agentpack release-check --skip-benchmark
agentpack release-check --skip-build
agentpack release-check --profile docs
agentpack release-check --json
```

Stages:

- changelog entry check for the current package version
- Python/npm version sync checks
- `pytest -q`
- npm wrapper tests
- `python -m build` into a temporary directory
- `agentpack benchmark --release-gate`

Profiles:

- `--profile auto` is the default. It uses the faster docs profile when the
  current diff only touches docs, plugin/rule files, benchmark result markdown,
  or docs/plugin validation tests. Clean release checkouts still run the full
  profile.
- `--profile docs` runs the docs/plugin validation path and skips package build
  plus public benchmark gate.
- `--profile fast` runs normal tests but skips package build plus public
  benchmark gate.
- `--profile full` always keeps the full release shape unless explicit skip
  flags are passed.

The command exits non-zero if any stage fails and prints exact rerun commands.
Use `--json` for CI wrappers that need stable machine-readable stage results.

For local development, the root `Makefile` wraps this command:

```bash
make release-fast   # release-check --skip-benchmark --skip-build
make release-docs   # release-check --profile docs
make release        # full release-check
make verify-wheel   # build + install wheel in temp venv + benchmark gate
```

---

### `agentpack dev-check`

Run the common local development checks without remembering the Make targets.

```bash
agentpack dev-check
agentpack dev-check --json
```

Stages cover docs link checks, `ruff`, scoped `mypy`, `pytest -q -m "not slow"`, and npm wrapper/version tests.
The command prints each rerun command and exits non-zero on the first failed
stage set.

---

### `agentpack verify-wheel`

Build or use a wheel, install it into a temporary virtual environment, and run
the installed `agentpack` command through the benchmark release gate.

```bash
agentpack verify-wheel
agentpack verify-wheel --wheel dist/agentpack_cli-0.3.22-py3-none-any.whl
agentpack verify-wheel --skip-build --json
```

Use this after `release-check` when you need to prove the packaged CLI behaves
the same as the source checkout.

---

### `agentpack release prepare`

Run the release workflow as package-user CLI automation.

```bash
agentpack release prepare
agentpack release prepare --json
```

It runs `release-check`, writes the public benchmark table, verifies the wheel
in a temporary venv, and prints a release summary. It is the broadest local
pre-publish command; `release-check` remains the non-mutating core gate.

---

### `agentpack ci init`

Generate a GitHub Actions workflow for AgentPack checks.

```bash
agentpack ci init
agentpack ci init --force
agentpack ci init --json
```

The workflow runs `dev-check` on pull requests and
`release-check --profile auto` on pushes to `main`. Auto keeps the full release
gate for code changes, but uses the docs/plugin profile for docs, agent-rule,
plugin, and native-integration-only diffs. Existing workflows are not
overwritten unless `--force` is present.

---

## Debugging Selection

When AgentPack misses a file, the next command should explain the miss:

```bash
agentpack diagnose-selection
agentpack benchmark --misses
agentpack explain --task "fix billing webhook" --file lib/billing/webhook.ts
agentpack explain --task "fix billing webhook" --omitted
agentpack explain --task "fix billing webhook" --budget-plan
```

`benchmark --misses` reports each expected file that was not selected, including whether it was ignored, scored too low, excluded by summary floor, cut by budget, or absent from the scan. `explain --file` shows the exact score signals for one file. `explain --budget-plan` shows how the token budget was spent across full, diff, symbols, skeleton, and summary modes.

This is the core reliability loop: pack, measure recall, inspect misses, then tune task wording, `.agentignore`, or scoring weights.

If top includes look noisy:

1. Rewrite `.agentpack/task.md` with concrete domain nouns, entrypoints, or filenames.
2. Re-pack and re-check `agentpack stats`.
3. If generated output still dominates, run `agentpack ignore suggest`; apply with `agentpack ignore apply --yes` only after reviewing.
4. Use `agentpack explain --file <path>` on repeat offenders before changing scoring.

`.agentignore` is for AgentPack ranking noise, not general git hygiene. `agentpack init` seeds it with safe defaults and imports obvious generated/noisy entries from the root `.gitignore`, nested `.gitignore` files, `.git/info/exclude`, and your global git ignore when they look safe to carry over. You should still add repo-specific outputs such as deploy artifacts, exports, or generated SDK folders when they are not useful context.

When ignore sources change later, re-sync with:

```bash
agentpack ignore sync
agentpack ignore sync --dry-run
agentpack ignore sync --check
agentpack ignore suggest
```

## Task Router

AgentPack Router is the MCP-first path for agents that need a task map before loading full context. It returns:

- files to read first
- repo and tool rules to apply
- installed skills to consider
- commands to consider, never execute automatically
- safety warnings for external side-effect skills
- an agent-ready prompt block

Use MCP when available:

```text
route_task("fix flaky payment webhook test")
```

Use CLI for inspection or scripting:

```bash
agentpack skills scan
agentpack skills index
agentpack skills recommend --task "fix flaky payment webhook test" --explain
agentpack route --task "fix flaky payment webhook test"
agentpack route --task "fix flaky payment webhook test" --json
```

Router reads skills and rules from `skills/`, `.claude-plugin/`, `.claude/skills/`, `~/.claude/skills/`, `~/.codex/skills/`, `~/.agents/skills/`, `.agentpack/skills/`, `.cursor/rules/`, `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md`. Rules are mandatory scoped instructions; skills are optional recommendations. The local `.agentpack/skills_index.json` stores metadata only and omits raw skill/rule bodies.

Safety defaults:

- skills are recommended, not executed
- suggested commands are returned as strings with reasons
- `expected_skills` and `avoid_skills` in benchmark cases report Skill Recall@3, Precision@3, MRR, noise, and skill token cost
- external side-effect skills, such as deploy or cloud mutation checklists, are warned and not selected unless explicitly allowed in config
