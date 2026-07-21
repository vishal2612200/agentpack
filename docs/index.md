# AgentPack Docs

AgentPack is a local context engine for AI coding agents. It ranks relevant repository files and builds compact task-focused context packs for Claude Code, Codex, Cursor, Windsurf, Antigravity, MCP tools, CI jobs, and markdown-based LLM workflows.

Use these docs when you want local/offline repo analysis, MCP-first routing, CI-friendly context packs, and benchmarkable file-selection quality without hosted indexing or embeddings.

## Get started

- [Commands](commands.md): CLI reference and common workflows.
- [Configuration](configuration.md): config, scoring weights, `.agentignore`, and git integration.
- [How AgentPack works](how-agentpack-works.md): route, pack, retrieve, learn, and benchmark flow.
- [Demo assets](demo.md): generated README GIF/MP4 and regeneration command.

Core onboarding uses the four-command project loop:

```bash
agentpack work "describe the change"
agentpack learn --json
agentpack finish
agentpack doctor
```

`route`, `pack`, `benchmark`, and the other specialized commands remain
available for deeper context, release, learning, and diagnostic workflows.

## Agents and IDEs

- [Integrations](integrations.md): setup paths for Claude Code, Codex, Cursor, Windsurf, Antigravity, and generic agents.
- [Agent and IDE plugins](agent-plugins.md): thin plugin/rule distribution layer for Codex, Cursor, Windsurf, Copilot, Cline, Kiro, OpenCode, and more.
- [Codex plugin](codex-plugin.md): packaged Codex plugin skeleton and `$agentpack-*` skills.
- [Claude Code context engine](claude-code-context-engine.md): Claude Code setup and MCP-first context.
- [Cursor context packing](cursor-context-packing.md): Cursor setup and context workflows.
- [MCP context engine](mcp-context-engine.md): MCP tools for fresh task context.
- [AgentPack for AI agents](agentpack-for-ai-agents.md): short guide for agent maintainers.

## Guides

- [AI coding agent context packing](ai-coding-agent-context.md): why ranked task context helps agent workflows.
- [Reduce Claude Code token usage](reduce-claude-code-token-usage.md): token-focused usage guide.
- [Agent behavior before and after](examples/agent-behavior-before-after.md): concrete cold-start examples.

## Evidence

- [Benchmarking](benchmarking.md): quality bar, release gate, sample fixtures, and public artifacts.
- [Benchmark learnings](benchmark-learnings.md): current tuning decisions and known bottlenecks.

## Comparisons

- [AgentPack vs Repomix](agentpack-vs-repomix.md)
- [AgentPack vs Augment Context Engine](agentpack-vs-augment-context-engine.md)

## Trust and limits

- [Privacy](privacy.md)
- [Threat model](threat-model.md)
- [Data flow](data-flow.md)
- [Limitations](limitations.md)

## Development

- [Architecture](architecture.md)
- [Development](development.md)
- [Optional guarded loop](runtime-loop.md)
