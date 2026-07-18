# Agent And IDE Plugins

AgentPack plugins make coding agents start with the right files.

This layer is intentionally thin. Plugins, IDE extensions, and agent rules should call the existing local AgentPack CLI or MCP tools. The layer does not reimplement ranking, scanning, packing, MCP, or benchmarking.

AgentPack remains a local context engine, not a coding agent.

## Current Distribution Paths

| Host | Current path | What it does |
|---|---|---|
| Codex | `.codex-plugin/` and `skills/` | Adds `$agentpack-*` skills for local routing, packing, refresh, PR review, skill review/eval generation, comment resolution, and learning |
| Codex repo setup | `agentpack init --agent auto` or `agentpack init --agent codex` | Auto-detects Codex or explicitly writes `AGENTS.md`, `.codex/hooks.json`, git hooks, MCP config, enables `agentpack@local`, and refreshes the local plugin cache package |
| Claude Code | `agentpack init --agent claude` | Writes `CLAUDE.md`, Claude hooks, and MCP config |
| Cursor | `.cursorrules`, `.cursor/rules/agentpack.mdc`, and `native-integrations/cursor-extension/` | Portable Cursor rules, repo installer, VS Code task, git hooks, and extension skeleton |
| Windsurf | `.windsurf/rules/agentpack.md` plus `native-integrations/windsurf-extension/` | Portable Windsurf rule, repo installer, VS Code task, git hooks, and extension skeleton |
| GitHub Copilot | `.github/copilot-instructions.md` | Portable Copilot instruction file |
| Cline | `.clinerules/agentpack.md` | Portable Cline rule file |
| Kiro | `.kiro/steering/agentpack.md` | Portable Kiro steering file |
| OpenCode | `.opencode/agentpack.md` | Portable OpenCode rule file |
| Antigravity | `agentpack init --agent antigravity` | Writes `GEMINI.md`, VS Code task, git hooks, and generated skill guidance |
| Generic agents | `agentpack init --agent generic` | Uses `.agentpack/context.md` directly |

## Shared Plugin Contract

Every host integration should follow the same flow:

1. Understand the user task.
2. Route first when read-only context is enough.
3. Pack only when full context is needed.
4. Read `.agentpack/context.md` or use MCP context tools.
5. Treat selected files as a map, not proof.
6. Use normal repo search when output looks incomplete.
7. Run relevant checks after editing.
8. Suggest benchmark capture after completed edits.

## Host Commands

Use these local commands from any agent or IDE:

```bash
agentpack route --task "<task>"
agentpack review "<review context>"
agentpack resolve --pr <number>
agentpack task set "<task>"
agentpack pack --task auto
agentpack upgrade --agent auto
agentpack doctor --agent <agent>
agentpack benchmark capture --since main --task "<task>"
agentpack benchmark --misses
```

Use `<agent>` values such as `codex`, `claude`, `cursor`, `windsurf`, `antigravity`, or `auto`.
`auto` detects the active host and does not default to Codex.

`agentpack review` prepares the local two-stage PR review bundle. It writes a
preflight file, a runbook, stage prompts, and branch-scoped
`*_understanding.toon` / `*_findings.toon` outputs. The optional review context
is a lens, not source evidence; reviewers still need `gh pr view`, `git diff`,
and direct code reads.

### Scriptable JSON Routing

When integrating AgentPack with scripts or custom agent tooling, you can request routing output in JSON format:

```bash
agentpack route --task "refactor plugin loader" --json
```

This output is intended for scripts and tools, not human reading. A typical JSON response contains the following structure:

```json
{
  "task": "refactor plugin loader",
  "recommended_interaction_mode": "agent",
  "current_agent": "codex",
  "task_mode": "broad_feature",
  "selected_files": [
    {
      "path": "src/agentpack/plugins/loader.py",
      "score": 950.0,
      "include_mode": "summary",
      "reasons": [
        "task-specific route seed"
      ]
    }
  ],
  "selected_skills": [],
  "applied_rules": [],
  "suggested_commands": [
    {
      "command": "agentpack doctor --agent codex",
      "reason": "Verify active plugin source",
      "source": "plugin_loader"
    }
  ],
  "safety_warnings": []
}
```

## Codex Plugin

Codex is the first concrete plugin package in this repo:

```text
.codex-plugin/plugin.json
skills/
```

Codex setup installs the package under
`~/.codex/plugins/cache/local/agentpack/<version>/`, enables
`agentpack@local`, and disables older enabled AgentPack marketplace copies so
new skills such as `$agentpack-review` come from the same version as the local
CLI. Run `agentpack doctor --agent codex` after upgrades to verify the active
plugin source.

See [`codex-plugin.md`](codex-plugin.md).

## Cursor And Windsurf

Cursor and Windsurf already have installable repo rules through `agentpack init`, plus native extension skeletons under:

```text
native-integrations/cursor-extension/
native-integrations/windsurf-extension/
```

Those skeletons stay advisory, not enforced, until host APIs can guarantee activation before edits and block edits when the installed AgentPack readiness check fails.

See [`native-integrations/README.md`](https://github.com/vishal2612200/agentpack/blob/main/native-integrations/README.md).

## Portable Rule Files

Like Ponytail's portability pattern, AgentPack keeps tiny host-native rule files in the repo so users can copy the one their IDE understands:

```text
agent-rules/agentpack.md
.cursor/rules/agentpack.mdc
.cursorrules
.windsurf/rules/agentpack.md
.github/copilot-instructions.md
.clinerules/agentpack.md
.kiro/steering/agentpack.md
.opencode/agentpack.md
```

These files all say the same thing: route or pack first when repo context is unclear, then verify with normal code search and tests.

## Boundary

Do not add remote service dependencies, hidden file mutations, LLM API calls, or autonomous agent behavior to plugin layers. Distribution surfaces should make AgentPack easier to invoke from existing developer workflows.
