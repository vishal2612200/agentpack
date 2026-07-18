# AgentPack Codex Plugin

Thin Codex plugin for AgentPack ranked repo context.

AgentPack is a local context engine, not a coding agent. This plugin exposes lightweight Codex skills for routing tasks, packing context, refreshing stale packs, reviewing diffs, resolving cited PR comments, and learning from current local session context.

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

Use `$agentpack-review <reviewer context>` to prepare and run the local
two-stage PR review workflow. It writes preflight metadata, a runbook, stage
prompts, and branch-scoped understanding/findings TOON files. The reviewer
context is only a lens; the review still depends on direct `gh pr view`,
`git diff`, code reads, and validation.

Use `$agentpack-resolve PR #123` to snapshot PR comments and run the cited
validate, plan, fix, verify, and reply loop. Replies are posted only after
`agentpack resolve --check` passes and the PR head remains unchanged.

Use `$agentpack-skill-review path/to/SKILL.md` to audit a skill and generate a
balanced trigger/non-trigger eval workspace. The generated runbook leaves model
execution and human review with the host agent, while keeping local manifests,
findings, and candidate evals reproducible.

The plugin delegates to local AgentPack CLI and MCP behavior. It does not upload source code or call hosted model APIs.
