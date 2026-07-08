# AgentPack Codex Plugin

Thin Codex plugin for AgentPack ranked repo context.

AgentPack is a local context engine, not a coding agent. This plugin exposes lightweight Codex skills for routing tasks, packing context, refreshing stale packs, reviewing diffs, auditing codebase folders, and learning from current local session context.

Install AgentPack first:

```bash
pipx install agentpack-cli
agentpack --version
```

Then initialize a project repo:

```bash
agentpack init --agent codex
```

Codex setup installs this package under
`~/.codex/plugins/cache/local/agentpack/<version>/`, enables
`agentpack@local`, and disables older enabled AgentPack marketplace copies so
the exposed skills match the installed CLI.

Use `@agentpack-review <reviewer context>` to prepare and run the local
two-stage PR review workflow. It writes preflight metadata, a runbook, stage
prompts, copy-fill TOON templates, and run-scoped understanding/findings TOON
files. The reviewer context is only a lens; the review still depends on direct
`gh pr view`, `git diff`, code reads, and validation. If an older model writes
valid JSON or fenced output, `agentpack review --check` canonicalizes it to
TOON; malformed output gets a local repair guide. For PR-bound reviews,
`agentpack review --check --dry-run-post` validates and writes the inline
review payload without calling GitHub; `--post-inline-comments` performs the
real GitHub review post.

Use `@agentpack-audit <scope>` to prepare a loop-based codebase audit atlas and
developer report for refactoring, performance, infrastructure/config,
reliability, or testability exploration. It writes
`.agentpack/audit.prompt.md`, `.agentpack/audit-report.md`,
`.agentpack/audit-atlas.json`, and `.agentpack/audit-findings.json`; agents
must separate hypotheses from findings and keep performance/config claims
behind usage evidence plus measurement or a validation plan.

The plugin delegates to local AgentPack CLI and MCP behavior. It does not upload source code or call hosted model APIs.
