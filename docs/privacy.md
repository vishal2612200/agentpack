# Privacy

AgentPack is built for private source repositories. Its default scan, route, pack, stats, and benchmark workflows are local-only.

## Direct Answers

| Question | Answer |
|---|---|
| Does AgentPack upload code? | No, not for scan, summarize, rank, route, pack, stats, or benchmark. |
| Where is data stored? | Local files under `.agentpack/`, plus the private global learner profile at `$AGENTPACK_HOME/learner-profile.json` and repo-local agent config created by installers. |
| What files are ignored? | `.gitignore`, `.agentignore`, generated/vendor defaults, and configured ignore rules. |
| Can secrets leak into packs? | Secret-like strings are redacted in generated packs, but users should still configure `.agentignore` and review outputs before sharing. |
| Does MCP expose repo data? | MCP tools expose local repo context only to the configured local agent/client that connects to them. |
| How do I audit output? | Read `.agentpack/context.md`, `.agentpack/context.<agent>.md`, `.agentpack/pack_metadata.json`, and context receipts. |

## Local Files

Common generated files:

- `.agentpack/context.md`
- `.agentpack/context.claude.md`
- `.agentpack/pack_metadata.json`
- `.agentpack/cache/`
- `.agentpack/snapshots/`
- `.agentpack/benchmark_results.jsonl`
- `.agentpack/benchmark-report.md`
- `.agentpack/benchmark-report.json`
- `.agentpack/learning-sessions.jsonl`
- `$AGENTPACK_HOME/learner-profile.json`

Generated context can contain source excerpts. Do not paste it into public issues, chat tools, or logs unless you have reviewed it.

Learning answers, rubric evidence, artifact paths, and verification summaries stay
in the owning project's `.agentpack/learning-sessions.jsonl`. They are excluded
from `--team-export`. The global learner profile stores only schema version,
role, target level, and update time; AgentPack writes it with user-only
permissions where the platform supports them. Learning observability events are
bounded to IDs, competency status, and score and never include the full answer.

## Network Behavior

The core local commands do not need cloud indexing, embeddings, or model API calls:

- `agentpack route`
- `agentpack pack`
- `agentpack stats`
- `agentpack benchmark`
- `agentpack explain`
- `agentpack diagnose-selection`

Commands that intentionally interact with external systems are explicit, such as cloning public benchmark repos, package release workflows, or any external agent command the user provides.

## Anonymous Benchmark Reports

Use this when asking for community validation without exposing source code:

```bash
agentpack benchmark capture --since main --task "describe task" --anonymous-report
```

This writes:

- `.agentpack/benchmark-report.md`
- `.agentpack/benchmark-report.json`

The report contains aggregate counts and percentages only: repo type, language mix, cases, recall when measured, token precision when measured, miss count, and a `no_source_code_uploaded` flag.

## Redaction Boundary

AgentPack redacts common secret patterns in generated packs. Redaction is defense-in-depth, not a replacement for:

- keeping secrets out of source
- adding sensitive files to `.agentignore`
- reviewing generated context before sharing
- running a dedicated secret scanner in CI
