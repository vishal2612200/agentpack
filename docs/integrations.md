# Integrations

AgentPack can be used directly from the CLI, as an MCP server, through generated instructions and hooks, or through thin plugin/IDE integration layers for common coding agents.

## MCP-First Workflow

For MCP-capable agents, the preferred workflow is pull-based:

1. Call `start_task(task)` when a new task begins. AgentPack writes `.agentpack/task.md`, packs context, and returns ranked markdown.
2. Call `get_context()` when you need the latest pack. It blocks for one refresh if `.agentpack/task.md` or the repo snapshot changed since the last pack, and otherwise prepends a freshness header.
3. Call `get_delta_context()` after edits or hook hints to see what changed without loading the full pack.
4. Call `explain_file(path)` or `get_related_files(path)` when a file looks relevant or suspicious.

The CLI remains the setup/debug/release path. MCP is the best interactive path because the agent can ask for only the context it needs instead of relying on one static startup blob.

Markdown context files are fallback artifacts for CI, logs, manual review, and non-MCP agents. Every rendered pack includes a machine-readable `agentpack:freshness` comment; agents should treat `active_context: mcp` as the preferred path and refresh before using markdown when `refresh_required: true`.

Do not run `agentpack mcp` as a daily developer command. Agent hosts launch it from MCP config written by install/repair flows. Use bounded manual execution only to diagnose command/import/runtime failures.

Supported setup path:

```bash
agentpack repair --agent codex
agentpack doctor --agent codex
# restart Codex
# call agentpack_readiness() from the host
```

For non-MCP agents, use `doctor` to verify the installed command surface and exact repair path:

```bash
agentpack doctor --agent auto
```

Generated agent rules use commands available in the installed AgentPack version. Older installs fall back to `agentpack pack --task auto` instead of referencing newer commands they do not support.

### Thread IDs

Generated instructions and hooks keep legacy global behavior by default. They do
not force thread mode from ambient host session variables.

For multiple agents in one repo, configure a stable thread id explicitly:

```bash
AGENTPACK_THREAD_ID=codex-local agentpack pack --agent auto --task auto
```

For MCP tools, pass `thread_id` explicitly or set `AGENTPACK_THREAD_ID` and use
`thread_id="auto"`. Thread mode writes `.agentpack/threads/<id>/...` and warns
about same-worktree, same-branch file overlap. Without `--thread`, global
`.agentpack/task.md` and `.agentpack/context.md` remain unchanged.

## Supported Integrations

| Agent | Automation level | Method |
|---|---|---|
| Claude Code (hook) | Highest | `init` writes `CLAUDE.md`, `.claude/settings.json` hooks, and `.mcp.json` |
| Codex | Medium | `init` writes `AGENTS.md`, `.codex/hooks.json`, Codex `[mcp_servers.agentpack]` config + git hooks; optional thin plugin in [`docs/codex-plugin.md`](codex-plugin.md) |
| Cursor | Medium | `init` writes `.cursorrules`, `.cursor/rules/agentpack.mdc`, VS Code task + git hooks |
| Windsurf | Medium | `init` writes `.windsurfrules`, VS Code task + git hooks |
| Antigravity | Medium | `init` writes `GEMINI.md`, VS Code task + git hooks |
| Generic | Basic | `watch` mode + read `context.md` |

### Integration limitations

- **Advisory vs enforced:** because AgentPack cannot intercept prompts or edits, native integrations are *advisory* — they strongly suggest, but the host is free to proceed without them. An integration only becomes *enforced* if the host exposes a blocking API (e.g. a `preEdit` hook that can cancel an edit when readiness fails). No entry is claimed as hard-enforced without such a blocking host API. See [`native-integrations/README.md`](../native-integrations/README.md#advisory-vs-enforced-examples) for advisory and enforcement examples.
- AgentPack cannot intercept prompts inside IDEs — Cursor/Windsurf rely on rules being followed.
- Claude wrapper (`agentpack claude`) is the most deterministic integration.
- Prompt hooks stay idle until `.agentpack/task.md` contains a real task.
- If the task changes drastically mid-session, prompt hooks can update `.agentpack/task.md` and point the agent at `get_context()` or `agentpack pack`; repo edits still refresh through MCP freshness checks or git hooks.
- AgentPack-selected files are ranked starting points, not absolute truth.
- Plugin and IDE surfaces are distribution layers. They call AgentPack CLI/MCP behavior and do not reimplement context ranking.

For the cross-host plugin/IDE shape, see [`Agent and IDE plugins`](agent-plugins.md).

---

## Agent setup

`agentpack init` is the normal one-command project setup. It creates `.agentpack/` state and installs the detected agent integration. Re-run it any time; integration writes are idempotent and never clobber unrelated config.

Use `--agent` explicitly to override detection. `agentpack install` remains available when you only want to repair or reconfigure agent files without reinitializing project state.

After upgrading an existing AgentPack install, run the post-upgrade refresh:

```bash
agentpack upgrade --agent auto
```

`auto` does not default to Codex. It detects the current IDE/agent from the
environment and repo files, then refreshes that integration. The command also
updates already-installed global AgentPack git/shell hooks, without opting new
machines into global automation.

### Claude Code

```bash
agentpack init --agent claude
```

Configures:
- `CLAUDE.md` — tells Claude to read the context pack before each task
- `.claude/settings.json` — two hooks:
  - `SessionStart`: clears injection sentinel so first prompt gets context
  - `UserPromptSubmit`: runs `agentpack hook` — stays silent until `.agentpack/task.md` has a real task, can detect clear task switches, updates `.agentpack/task.md`, and emits a small hint. With MCP: points the agent at `get_context()` or `pack_context()`. Without MCP: emits a capped fallback (top 8 files, ≤3k chars)

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
- `.codex/hooks.json` — Codex app lifecycle hooks for light prompt-time AgentPack task hints
- `~/.codex/config.toml` or `$CODEX_HOME/config.toml` — registers `[mcp_servers.agentpack]` for Codex-host MCP exposure and enables `agentpack@local`
- `.git/hooks/post-commit`, `post-merge`, `post-checkout` — background repack on tree change

Optional plugin packaging lives in `.codex-plugin/plugin.json` and `skills/`.
It adds `$agentpack-route`, `$agentpack-pack`, `$agentpack-refresh`,
`$agentpack-review`, `$agentpack-skill-review`, and `$agentpack-learn` as thin Codex-facing skills that call the same local
AgentPack CLI/MCP behavior. `agentpack init --agent codex`, `agentpack repair
--agent codex`, and `agentpack upgrade --agent codex` install or refresh the
local plugin package under Codex's plugin cache. They also disable older enabled
AgentPack marketplace copies so Codex loads the local bundle that matches the
installed CLI. See [`Codex plugin`](codex-plugin.md).

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
| Auto-repack when stale | ✅ `get_context()` / `pack_context()` block on demand; git hooks for repo edits | ✅ git hooks | ✅ git hooks | ✅ git hooks | ✅ git hooks |
| Manual repack shortcut | ✅ `/agentpack`, `/agentpack-review`, `/agentpack-learn` slash cmds | ✅ VS Code task | ✅ VS Code task | `agentpack pack` | ✅ VS Code task |

---

## CI/CD: pack per PR

### AgentPack's Own CI

agentpack ships multiple release-related workflows:

- **`ci.yml`** — runs on push and pull requests to `main`.
  - `test`: matrix tests on Python 3.10–3.14 (`pytest ... --cov`), `ruff check src/ tests/`, and scoped `mypy`.
  - `npm-wrapper`: npm wrapper tests plus a dry-run package check.
  - `agent-integration-matrix`: integration checks for supported agents.
- **`agentpack-pr.yml`** — generates and uploads `.agentpack/context.claude.md` for PR review artifacts in `.github/workflows/agentpack-pr.yml`.
- **`publish.yml`** — validates release tag provenance/version/changelog then builds and publishes to PyPI with trusted publishing.
- **`publish-npm.yml`** — publishes the npm package on `v*` tags after the same release checks.

### Add context packing to your repo

Use `.github/workflows/agentpack-pr.yml` as the in-repo PR example and adapt the commands to match your policy:

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
