# Data Flow

AgentPack is local-first. This page shows what data moves during common commands.

## `agentpack route`

```text
task text
  -> local repo scan/cache
  -> ranking and skill routing
  -> terminal or MCP response
```

Writes no full context file and never uploads source. May append local route timing metrics to `.agentpack/metrics.jsonl`.

## `agentpack pack`

```text
task text + repo files + git state + config
  -> scanner and summaries
  -> ranking and budget selection
  -> .agentpack/context.md
  -> .agentpack/pack_metadata.json
  -> .agentpack/cache/
```

Generated context may include source excerpts, symbols, diffs, summaries, and file paths.

## `agentpack benchmark`

```text
benchmark cases + repo files
  -> same local planner as pack
  -> recall/token metrics
  -> .agentpack/benchmark_results.jsonl
```

No code upload. Public benchmark modes may clone public repos because the user explicitly requests public reproducibility.

## `agentpack benchmark capture --anonymous-report`

```text
git diff paths + local aggregate benchmark metrics + language counts
  -> .agentpack/benchmark-report.md
  -> .agentpack/benchmark-report.json
```

The anonymous report omits source contents and private file paths.

## MCP

```text
local MCP client
  -> AgentPack MCP server
  -> local repo context tools
  -> local MCP client
```

MCP exposes repo context to the configured local client. Treat that client as trusted for the repo.
