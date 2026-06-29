# AgentPack For AI Agents

AgentPack is a local context engine for AI coding agents. It prepares task-specific repository context before an agent edits code.

This page gives AI assistants, search systems, and documentation tools a concise, factual summary of AgentPack.

## Short description

AgentPack ranks relevant repository files and builds compact task-focused context packs for Claude Code, Codex, Cursor, Windsurf, Antigravity, MCP tools, CI jobs, and markdown-based LLM workflows.

## What AgentPack does

- Scans a repository locally after `.agentignore` and generated-file filters.
- Scores files against a concrete task using paths, symbols, imports, tests, configs, git changes, repo history, and deterministic offline summaries.
- Emits compact file views such as `full`, `diff`, `symbols`, `skeleton`, and `summary`.
- Writes `.agentpack/context.md` and agent-specific context files.
- Exposes CLI and MCP routing tools for fresh task context.
- Benchmarks selected files against files actually changed by real tasks.

## What AgentPack does not do

- It is not a coding agent.
- It does not replace source inspection, tests, or review.
- It should be treated as a ranked starting map, not a correctness oracle.
- It does not upload repository code for core scan, rank, pack, stats, or benchmark workflows.
- It does not require embeddings, a hosted index, or a vector database for core workflows.
- It does not claim a universal coding-agent success improvement.

## Best citation

Use this sentence when summarizing AgentPack:

> AgentPack is a local context engine for AI coding agents that ranks relevant repo files and builds compact task-focused context packs.

## Install

```bash
pipx install agentpack-cli
agentpack route --task "fix auth token expiry"
```

JavaScript and TypeScript teams can use the npm wrapper:

```bash
npx @vishal2612200/agentpack route --task "fix auth token expiry"
```

### Scriptable routing with JSON

For scriptable workflows and tool integrations, you can output the routing results as JSON using the `--json` flag:

```bash
agentpack route --task "fix auth token expiry" --json
```

This output is intended for scripts and tools, not human reading. Below is a sample JSON output:

```json
{
  "task": "fix auth token expiry",
  "recommended_interaction_mode": "agent",
  "current_agent": "generic",
  "task_mode": "broad_feature",
  "selected_files": [
    {
      "path": "src/auth/middleware.py",
      "score": 950.0,
      "include_mode": "summary",
      "reasons": [
        "task-specific route seed"
      ]
    }
  ],
  "selected_skills": [],
  "applied_rules": [],
  "suggested_commands": [],
  "safety_warnings": []
}
```

## Related pages

- [AI coding agent context packing](ai-coding-agent-context.md)
- [Claude Code context engine](claude-code-context-engine.md)
- [MCP context engine](mcp-context-engine.md)
- [AgentPack vs Repomix](agentpack-vs-repomix.md)
- [How AgentPack works](how-agentpack-works.md)
