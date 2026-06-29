---
title: Claude Code Context Engine
description: Use AgentPack as a local context engine for Claude Code so Claude starts with ranked files, likely tests, repo rules, and compact task-focused context packs.
---

# Claude Code Context Engine

AgentPack is a local context engine for Claude Code workflows. It gives Claude a ranked starting map before edits: relevant files, likely tests, repo rules, skills, commands, and safety warnings for the current task.

Use it when Claude Code spends early turns grepping the repo, opening nearby files, and rebuilding context that could have been prepared locally first.

## Quick route

Run a read-only route when you want Claude to see likely files without writing context artifacts:

```bash
pipx run --spec agentpack-cli agentpack route --task "fix auth token expiry"
```

`agentpack route` returns a lightweight task route for debugging, demos, or MCP-style routing.

### Scriptable routing with JSON

For scriptable usage or MCP-style routing where tools parse the results programmatically, you can run the route command with the `--json` flag:

```bash
agentpack route --task "fix auth token expiry" --json
```

This output is intended for scripts and tools, not human reading. Below is a sample of the JSON structure returned:

```json
{
  "task": "fix auth token expiry",
  "recommended_interaction_mode": "agent",
  "current_agent": "claude",
  "task_mode": "broad_feature",
  "selected_files": [
    {
      "path": "src/auth/middleware.py",
      "score": 950.0,
      "include_mode": "summary",
      "reasons": [
        "task-specific route seed"
      ]
    },
    {
      "path": "tests/test_auth.py",
      "score": 900.0,
      "include_mode": "summary",
      "reasons": [
        "Likely test file for auth"
      ]
    }
  ],
  "selected_skills": [],
  "applied_rules": [],
  "suggested_commands": [],
  "safety_warnings": []
}
```

## Claude Code setup

Install AgentPack and configure the repository:

```bash
pipx install agentpack-cli
agentpack init --agent claude
```

This writes Claude Code instructions and MCP configuration for the repo. MCP-capable sessions should prefer AgentPack MCP tools, especially `route_task(task)` and `get_context()`, over static markdown when available.

## Pack workflow

When you want a durable markdown context file:

```bash
agentpack task set "fix auth token expiry"
agentpack pack --task auto
```

AgentPack writes `.agentpack/context.md` and Claude-specific context output with selected files, omitted-file receipts, task freshness, token stats, and suggested checks.

## Before and after

Without a context engine, a Claude Code session may start by searching:

```text
auth
token
expiry
middleware
tests
```

With AgentPack:

```bash
agentpack route --task "fix auth token expiry"
```

Claude receives a ranked map of likely implementation files, tests, configs, and repo rules before editing begins.

## Why use AgentPack with Claude Code?

- Reduce repeated repo orientation work.
- Surface likely tests before edits.
- Keep context selection local and explainable.
- Refresh stale task context through CLI, guard, or MCP flows.
- Use benchmark cases to measure whether expected files are selected.

## Boundaries

AgentPack is not a coding agent and does not replace Claude's source inspection. It prepares better context so Claude starts closer to the right part of the repo.

Core scan, summarize, rank, pack, stats, and benchmark workflows run locally. They do not require hosted LLM calls, embeddings, or a vector database.
