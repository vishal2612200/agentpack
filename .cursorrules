<!-- agentpack:rule:start -->
At the start of every coding task:
1. Write a one-line task summary to `.agentpack/task.md` (overwrite the whole file).
2. Run `agentpack guard --agent cursor --repair-stale --refresh-context --thread global`.
3. Prefer AgentPack MCP if available. MCP is the active path. Call `agentpack_readiness()` to prove live tool exposure, then `agentpack_route_task(task="<task>")` to get files, rules, skills, commands, and safety warnings.
4. Call `agentpack_pack_context(task="<task>")` only when full packed context is needed, or `agentpack_get_context()` for current context.
5. If MCP is unavailable, read `.agentpack/context.md`. Treat it as fallback; if its `agentpack:freshness` block says `refresh_required: true` or the task does not match, rerun the guard command before using selected files.
6. Use files listed in context as starting points, but verify with actual code before editing.
7. Use JSON programmatically for configs, storage, hooks, and tool protocols. Use TOON for agent-facing structured context or prompt payloads unless an external contract requires JSON.
When the user switches to a different coding task, update `.agentpack/task.md`, then call MCP again or rerun the refresh command before editing.
If context is missing: agentpack guard --agent cursor --repair-stale --refresh-context --thread global.
If AgentPack tools are unavailable or context looks stale/wrong-worktree, do not trust old pack output. Use direct `rg`, PR diff inspection, and target-file reads, then run focused validation.
Prefer AgentPack MCP when tools are exposed. First call readiness (`agentpack_readiness()` or `mcp__agentpack__readiness()`) to prove live tool exposure.
If MCP tools are unavailable: run `agentpack mcp` once with a short timeout. If it exits with command/import error, report the setup issue and fall back to CLI/direct search. If it waits until timeout, the local MCP server is runnable but the host did not expose tools; fall back to CLI/direct search and suggest `agentpack repair --agent cursor` plus host restart. Do not keep `agentpack mcp` running manually.
CLI fallback: `agentpack guard --agent cursor --repair-stale --refresh-context --thread global`, `agentpack route --task "<task>"`, `agentpack pack --task auto`, then `rg` / direct file reads.
Prompt hygiene: for agent-mode coding work, prefer `Task`, `Files`, `Acceptance criteria`, `Constraints`, `Validation`, and `Output` sections. For short/simple questions, use Ask/Chat mode instead of agent mode. Keep routine responses concise unless the user asks for detail.
For multiple agent threads in one repo, keep legacy global mode unless a thread is explicit. Use `AGENTPACK_THREAD_ID=<stable-id> agentpack guard --agent cursor --repair-stale --refresh-context --thread auto` or pass `thread_id` to AgentPack MCP tools to use `.agentpack/threads/<id>/...` and get overlap warnings.
<!-- agentpack:rule:end -->
