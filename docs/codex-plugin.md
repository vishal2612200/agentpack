# Codex Plugin

AgentPack can be packaged as a thin Codex plugin so Codex starts with ranked repo context.

The plugin does not reimplement ranking, scanning, packing, MCP, or benchmarking. It delegates to the existing local AgentPack CLI and MCP behavior.

AgentPack remains a local context engine, not a coding agent.

This is the first concrete packaged plugin. The broader plugin and IDE distribution plan covers Cursor, Windsurf, Claude Code, Antigravity, and generic agents in [`agent-plugins.md`](agent-plugins.md).

## What It Adds

- `$agentpack` checks whether context exists and suggests the next step.
- `$agentpack-route <task>` runs read-only task routing.
- `$agentpack-pack <task>` writes the task and builds `.agentpack/context.md`.
- `$agentpack-refresh [task]` refreshes stale context through the Codex guard path.
- `$agentpack-review [reviewer context]` runs the local `agentpack review` wrapper, then uses the generated runbook plus staged understanding and judge prompts to inspect the current PR or diff.
- `$agentpack-resolve [PR and context]` runs the local `agentpack resolve` wrapper, then validates, fixes, verifies, and posts cited replies for PR comments.
- `$agentpack-skill-review <skill path or name>` audits a `SKILL.md` and creates a balanced trigger/non-trigger eval workspace.
- `$agentpack-learn <statement>` turns current local AgentPack session context into an interactive learning prompt.

## Install

This repo can act as a local Codex plugin package because it includes:

```text
.codex-plugin/plugin.json
skills/
```

Install or load it through Codex's local plugin workflow, pointing Codex at this repository as the plugin source.

Install AgentPack locally first:

```bash
pipx install agentpack-cli
agentpack --version
```

Inside a project repo, initialize normal AgentPack files:

```bash
agentpack init --agent auto
```

When auto-detection resolves to Codex, this writes `AGENTS.md`,
`.codex/hooks.json`, git hooks, Codex MCP config, and a local Codex plugin
package under `~/.codex/plugins/cache/local/agentpack/<version>/`.

Codex install, repair, and upgrade also enable the local plugin entry:

```toml
[plugins."agentpack@local"]
enabled = true
```

If an older marketplace copy such as `agentpack@awesome-codex-plugins` is
already enabled, AgentPack disables that stale entry so Codex loads the local
bundle that matches the installed CLI. This matters for newly added skills such
as `$agentpack-review`; copying the cache package alone is not enough if Codex
is still pointed at an older plugin source.

Auto-detection does not default to Codex; pass `--agent codex` only when you
want to force Codex setup.

For existing repos after upgrading AgentPack:

```bash
agentpack upgrade --agent auto
```

The plugin commands stay thin and call the same local engine.

To verify the active Codex surface:

```bash
agentpack doctor --agent codex
```

The Codex audit should report the local plugin as enabled, for example
`agentpack@local, cache <version>`, plus the MCP server and repo hooks.

## Codex Workflow

Start read-only:

```text
$agentpack-route fix auth token expiry
```

Build context when Codex needs more than a route:

```text
$agentpack-pack fix auth token expiry
```

Then Codex should read `.agentpack/context.md`, inspect selected files, and verify with normal repo search before editing.

After edits:

```text
$agentpack-review focus on backward compatibility
```

Review should use `.agentpack/review.prompt.md`, inspect `gh pr view`, `git
diff`, and exact changed code, then report only grounded findings plus exact
validation status. The reviewer context is only a prioritization lens; it must
not replace source evidence.

The workflow writes:

- `.agentpack/review-preflight.json`
- `.agentpack/review.prompt.md`
- `.agentpack/review-understanding.prompt.md`
- `.agentpack/review-judge.prompt.md`
- `.agentpack/reviews/<branch-prefix>/<run_id>/preflight.json`
- `.agentpack/reviews/<branch-prefix>/<run_id>/runbook.md`
- `.agentpack/reviews/<branch-prefix>/<run_id>/understanding.prompt.md`
- `.agentpack/reviews/<branch-prefix>/<run_id>/judge.prompt.md`
- `.agentpack/reviews/<branch-prefix>/<run_id>/understanding.toon`
- `.agentpack/reviews/<branch-prefix>/<run_id>/findings.toon`

The run-scoped preflight artifact remains JSON, while the LLM-stage artifacts are `understanding.toon` and
`findings.toon`; only their location moved out of the repo root and into the
review run directory. This replaces the legacy root outputs
`<branch-prefix>_understanding.toon` and `<branch-prefix>_findings.toon`.

The understanding stage records the factual model of the PR. The judge stage
uses that model plus direct repository reads to produce evidence-backed
findings. Fresh runs are the default, and interrupted work is resumed only with
`agentpack review --resume <run_id>`, so an abandoned partial review does not
silently become the next run's input.

For learning from the current local context:

```text
$agentpack-learn explain the router scoring changes from this session
```

The learning command keeps a stable prompt prefix for caching and appends the user learning statement at the end.

## Rules For Codex

Before making code changes:

1. Understand the task.
2. Use AgentPack route or pack when repo context is unclear.
3. Read `.agentpack/context.md` if it exists.
4. Treat selected files as starting points, not the full truth.
5. Use normal repo search when AgentPack output seems incomplete.
6. Prefer the smallest correct diff.
7. Run relevant checks after editing.

## Local-First Boundary

The plugin calls local AgentPack commands. It does not upload code, call LLM APIs, or turn AgentPack into an autonomous agent.

Generated files live under `.agentpack/`. Review packs before sharing them outside your machine.
