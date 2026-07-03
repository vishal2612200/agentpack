---
description: Pack repo context and immediately start working on the task. Supports session mode (start once, work normally) and manual pack mode. Reads context and begins helping — no manual piping needed.
---

# AgentPack

Pack repo context and immediately start working on the task.

## Usage

```
/agentpack
/agentpack --mode deep
/agentpack init             # interactive: prompts for default mode
/agentpack status
/agentpack stats
/agentpack diff
/agentpack summarize
/agentpack install
/agentpack session start
/agentpack session status
/agentpack session refresh --task "new task"
/agentpack session stop
/agentpack watch
/agentpack claude
/agentpack explain --task auto
/agentpack explain --file src/auth/session.py
/agentpack explain --omitted
```

## Session Mode (recommended)

If a session is already running (`.agentpack/session.json` exists and `"active": true`):

1. If the user gives a new coding task, write it through `agentpack task set "<task>"` so ambient agent sessions use scoped state.
2. Run `agentpack pack --agent claude --task auto` unless watch mode already refreshed after the task write.
3. Read the context path reported by `agentpack status` — context now matches the current task.
4. Proceed with the task using the context you just read.

To start a session:

```bash
agentpack session start                     # creates session + generates initial context
agentpack session start --agent claude      # specify agent

agentpack watch                             # in another terminal — auto-refreshes on changes
```

To check session state:

```bash
agentpack session status    # shows active, agent, mode, last refresh, refresh count
agentpack stats             # shows session panel + token stats + top files
```

To force a refresh:

```bash
agentpack session refresh
agentpack session refresh --task "new task description"
```

To stop:

```bash
agentpack session stop
```

Then use normal prompts — context stays current while `watch` is running.

## Manual Pack Mode (no session)

```bash
printf '%s\n' "<task>" > .agentpack/task.md
agentpack pack --agent claude --task auto --mode balanced
```

Then read `.agentpack/context.claude.md` in full.

## Session Lease Mode

When an agent host exposes `AGENTPACK_THREAD_ID`, `CODEX_THREAD_ID`,
`CLAUDE_SESSION_ID`, `CURSOR_SESSION_ID`, `WINDSURF_SESSION_ID`,
`ANTIGRAVITY_SESSION_ID`, or `GEMINI_SESSION_ID`, AgentPack uses scoped state
by default. Task, context, state, and metadata live under
`.agentpack/threads/<id>/...`; `--thread global` opts into the legacy global
files.

When multiple agents work in the same repo and the host does not expose a
session id, set one explicitly:

```bash
export AGENTPACK_THREAD_ID=codex-local
agentpack start "fix auth session bug"
```

Thread mode writes `.agentpack/threads/<id>/task.md`, `context.md`, `context.claude.md`,
`task_state.md`, and `pack_metadata.json`, then appends `.agentpack/thread_index.jsonl`.
If another active thread on the same branch/worktree overlaps files, the context and terminal
output warn without blocking edits. Use `agentpack threads --active` and
`agentpack state show --thread auto` for coordination.

Completed sessions are terminal: after `agentpack finish`, AgentPack refuses to
reuse that done context for a new task.

## Process

### Step 1: Check agentpack is installed

```bash
if command -v pipx >/dev/null 2>&1; then
  export AGENTPACK_BIN="$(pipx environment --value PIPX_BIN_DIR)/agentpack"
  test -x "$AGENTPACK_BIN" || pipx install agentpack-cli
elif command -v agentpack >/dev/null 2>&1; then
  export AGENTPACK_BIN="$(command -v agentpack)"
else
  python3 -m venv .venv
  "$PWD/.venv/bin/pip" install agentpack-cli
  export AGENTPACK_BIN="$PWD/.venv/bin/agentpack"
fi
```

### Step 2: Initialize if not already done

```bash
test -f .agentpack/config.toml || "$AGENTPACK_BIN" init --yes
```

### Step 3: Determine workflow

**Session active** (`.agentpack/session.json` exists, `"active": true`):
- Update the current session task file if task changed (`agentpack task set "<task>"`)
- Run `"$AGENTPACK_BIN" pack --agent claude --task auto` unless watch already refreshed it
- Read the context path reported by `"$AGENTPACK_BIN" status`
- Proceed immediately

**No session**:
- Run `"$AGENTPACK_BIN" session start` or `"$AGENTPACK_BIN" pack --agent claude --task auto`
- Read the context file
- Proceed

### Step 4: Immediately start working

Using the context you just read:

1. **Orient** — state which files are changed and what the key code areas are (2-3 sentences)
2. **Diagnose or plan** — root cause for bugs, approach for features. Reference specific file:line
3. **Start working** — edit code, fix the issue, implement the feature

Do not say "context pack ready" and stop. Do not tell the user to run more commands.

## Stale pack handling

If `"$AGENTPACK_BIN" status` exits non-zero or context seems unrelated to the task:
- Run `"$AGENTPACK_BIN" session refresh` (if session active)
- Or run `"$AGENTPACK_BIN" pack --agent claude --task auto` (manual mode)
- Re-read the context, then proceed

Do not ask the user — just refresh and proceed.

## Debugging selection

```bash
"$AGENTPACK_BIN" explain --task auto                   # show ranked file list
"$AGENTPACK_BIN" explain --file src/auth/session.py    # per-file score breakdown
"$AGENTPACK_BIN" explain --omitted                     # see what was excluded and why
```

## Subcommand routing

| User types | Action |
|---|---|
| `/agentpack` | check session or pack with `--task auto` + read + work |
| `/agentpack session start` | `agentpack session start` |
| `/agentpack session status` | `agentpack session status` |
| `/agentpack session refresh` | `agentpack session refresh` |
| `/agentpack session stop` | `agentpack session stop` |
| `/agentpack watch` | `agentpack watch` (foreground, Ctrl+C to stop) |
| `/agentpack claude` | `agentpack claude` (refresh + launch claude) |
| `/agentpack init` | `agentpack init` only |
| `/agentpack status` | check staleness |
| `/agentpack stats` | session info + token savings |
| `/agentpack diff` | changed files |
| `/agentpack summarize` | rebuild offline summary cache |
| `/agentpack install` | `agentpack install --agent claude` |
| `/agentpack explain` | show ranked file selection |

## Notes

- All commands are local — no API calls
- `agentpack pack --task "<task>"` writes the task and packs it; `--task auto` reads the current session task file, then in legacy global mode falls back to branch name -> changed file paths -> recent commit
- Changed files are highest priority in context
- Session context files: `.agentpack/context.md` (readable), `.agentpack/context.compact.md` (compact)
- Never overwrite `.agentignore` or `config.toml` without `--force`
