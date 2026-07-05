<!-- agentpack:start -->
## AgentPack

AgentPack MCP server is available. For coding tasks in this repository, call the MCP tool
before editing files to get task-relevant context without loading the entire codebase.
Prefer MCP over reading `.agentpack/context*.md` directly because MCP auto-refreshes stale task
and repo-snapshot context before returning.

```
mcp__agentpack__route_task(task="<what you're working on>")
```

When full packed context is needed, call:

```
mcp__agentpack__pack_context(task="<what you're working on>", budget=4000)
```

Executable fallback:

```bash
agentpack guard --agent claude --repair-stale --refresh-context --thread global
```

Other tools:
- `mcp__agentpack__readiness()` — proves this host exposes AgentPack MCP tools
- `mcp__agentpack__route_task(task)` — files, rules, skills, commands, and safety warnings
- `mcp__agentpack__explain_file(path)` — score breakdown + symbols for a file
- `mcp__agentpack__get_related_files(path)` — import-graph neighbours
- `mcp__agentpack__get_stats()` — token/saving stats for the latest pack
- `mcp__agentpack__get_context()` — read the latest pack; auto-refreshes when task.md or repo snapshot changed
- `mcp__agentpack__refresh()` — refresh using current task.md

If MCP is not available, fall back to the CLI:

```bash
printf '%s
' "<task>" > .agentpack/task.md
agentpack pack --agent claude --task auto
```

Then read `.agentpack/context.claude.md`.

Use JSON programmatically for configs, storage, hooks, and tool protocols. Use TOON for agent-facing structured context or prompt payloads unless an external contract requires JSON.

If AgentPack tools are unavailable or context looks stale/wrong-worktree, do not trust old pack output. Use direct `rg`, PR diff inspection, and target-file reads, then run focused validation.

Prefer AgentPack MCP when tools are exposed. First call readiness (`agentpack_readiness()` or `mcp__agentpack__readiness()`) to prove live tool exposure.
If MCP tools are unavailable: run `agentpack mcp` once with a short timeout. If it exits with command/import error, report the setup issue and fall back to CLI/direct search. If it waits until timeout, the local MCP server is runnable but the host did not expose tools; fall back to CLI/direct search and suggest `agentpack repair --agent claude` plus host restart. Do not keep `agentpack mcp` running manually.
CLI fallback: `agentpack guard --agent claude --repair-stale --refresh-context --thread global`, `agentpack route --task "<task>"`, `agentpack pack --task auto`, then `rg` / direct file reads.

Prompt hygiene: for agent-mode coding work, prefer `Task`, `Files`, `Acceptance criteria`, `Constraints`, `Validation`, and `Output` sections. For short/simple questions, use Ask/Chat mode instead of agent mode. Keep routine responses concise unless the user asks for detail.
For multiple agent threads in one repo, stay in legacy global mode unless a thread is explicit. Use
`AGENTPACK_THREAD_ID=<stable-id> agentpack guard --agent claude --repair-stale --refresh-context --thread auto`
or pass `thread_id` to AgentPack MCP tools to use `.agentpack/threads/<id>/...` and get overlap warnings.
<!-- agentpack:end -->
