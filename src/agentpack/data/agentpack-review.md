---
description: Run the full AgentPack PR review flow with an optional reviewer lens.
---

# AgentPack Review

Review the current PR or checked-out branch using the full AgentPack review workflow.

## Usage

```
/agentpack-review
/agentpack-review PR #123 focus on backward compatibility
/agentpack-review focus on backward compatibility
/agentpack-review reviewer is worried about prompt latency
```

## Steps

1. Refresh AgentPack context for this exact review task before reading PR diff or code. Prefer MCP:

```text
agentpack_pack_context(task="review current PR $ARGUMENTS")
```

If MCP is unavailable, run:

```bash
agentpack guard --agent auto --repair-stale --refresh-context
```

If you bypass this refresh, state the bypass reason before continuing.
2. Prepare the full review bundle. If `$ARGUMENTS` names a PR number or PR URL, `agentpack review` must bind metadata, diff, and context to that PR. If the user did not name a PR, `agentpack review` must identify the current PR through `gh`; do not accept silent `HEAD~1` fallback.

```bash
agentpack review "$ARGUMENTS"
```

Use this explicit form when the PR target is known:

```bash
agentpack review --pr <number-or-url> "$ARGUMENTS"
```

3. Read `.agentpack/review.prompt.md` and follow it completely.
4. Treat any non-PR portion of `$ARGUMENTS` only as a reviewer lens. It must not replace the latest PR head, `gh pr view`, `git diff`, or direct code reads.
5. By default, `agentpack review` starts a fresh run under `.agentpack/reviews/<branch-or-pr>/<run_id>/` and refreshes the stable alias files in `.agentpack/`.
6. Do not perform the review inline from this command. If you cannot write the required files, stop and report blocked.
7. Stage 1 starts from `.agentpack/review-understanding.template.toon` and writes the run-scoped understanding TOON declared by `agentpack review`.
8. Run `agentpack review --check`; do not start Stage 2 unless Stage 1 validates.
9. Stage 2 must read that understanding TOON from disk, start from `.agentpack/review-findings.template.toon`, and then write the run-scoped findings TOON declared by `agentpack review`.
10. Run `agentpack review --check --dry-run-post` when you need a controlled inline payload check without calling GitHub. Run `agentpack review --check --post-inline-comments` for real PR-bound review posting. Use `agentpack review --check` only for local fallback reviews. Do not produce a final review summary unless the findings TOON exists, validates, and any intended PR-bound inline post succeeds.
11. If an older model emits valid JSON or fenced output instead of TOON, rerun `agentpack review --check`; AgentPack canonicalizes schema-valid output to TOON and writes a repair guide for invalid output.
12. Resume an interrupted run only with `agentpack review --resume <run_id>`.
13. In the final response, report findings first with file evidence, then state inline-post status and validation exactly: dry-run passed, posted, failed, or not run.
