# AgentPack Codex Plugin

Thin Codex plugin for AgentPack ranked repo context.

AgentPack is a local context engine, not a coding agent. This plugin exposes lightweight Codex skills for routing tasks, packing context, refreshing stale packs, reviewing diffs, resolving PR comments, auditing skills, and learning from current local session context.

Use `$agentpack-handoff [name]` to package current work and `$agentpack-resume [name]` to claim it from another real session. Other agents use the same MCP tools or `agentpack handoff resume` CLI.

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
Anchor, Judge, Critic, Actor PR review workflow. It writes preflight metadata, a runbook, stage
prompts, copy-fill TOON templates, and run-scoped understanding/findings/critique TOON
files. The reviewer context is only a lens; the review still depends on direct
`gh pr view`, `git diff`, code reads, and validation. If an older model writes
valid JSON or fenced output, `agentpack review --check` canonicalizes it to
TOON; malformed output gets a local repair guide. For PR-bound reviews,
`agentpack review --check --dry-run-post` validates and writes the inline
review payload from Critic-approved findings without calling GitHub; `--post-inline-comments` performs the
real GitHub review post. Actor never edits or pushes the PR branch.

Use `$agentpack-resolve [PR and context]` to validate, fix, verify, and reply
to review comments with file and test citations. Use `$agentpack-skill-review
<skill path or name>` to audit a skill and generate balanced trigger and
non-trigger eval cases.

The plugin delegates to local AgentPack CLI and MCP behavior. It does not upload source code or call hosted model APIs.
