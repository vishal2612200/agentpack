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

1. Resolve the immutable PR context before reading the diff or code. Prefer MCP:

```text
agentpack_get_pr_context(pr="$ARGUMENTS", focus="$ARGUMENTS", format="toon")
```

`agentpack_get_pr_context` is host-neutral. Codex and Claude hooks may offer it
automatically when review intent is detected. Cursor, Windsurf, Antigravity, and
generic hosts must call it explicitly before deep review. The response carries
verified base/head SHAs and `context_status`; when status is `degraded`, review
source code directly but do not make architecture claims without citations.

Then refresh task context as needed. If MCP is unavailable, run:

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
7. The Anchor role starts from `.agentpack/review-understanding.template.toon` and writes the compatible run-scoped understanding TOON declared by `agentpack review`.
8. Run `agentpack review --check`; do not start Judge unless Anchor validates.
9. Judge must read that understanding TOON from disk, start from `.agentpack/review-findings.template.toon`, and write the candidate findings TOON at the declared path.
10. Run `agentpack review --check`; Critic reads both canonical handoffs, starts from `.agentpack/review-critique.template.toon`, and writes exactly one accept, reject, or downgrade decision for every Judge finding.
11. Run `agentpack review --check` to generate `approved-findings.toon`. `--dry-run-post` and `--post-inline-comments` consume only that approved artifact. Actor is publish-only and never edits or pushes a PR branch. Do not produce a final review summary unless Critic validates and any intended PR-bound inline post succeeds.
12. If an older model emits valid JSON or fenced output instead of TOON, rerun `agentpack review --check`; AgentPack canonicalizes schema-valid output to TOON and writes a repair guide for invalid output.
13. Resume an interrupted run only with `agentpack review --resume <run_id>`.
14. In the final response, report approved findings first with file evidence, then state inline-post status and validation exactly: dry-run passed, posted, failed, or not run.
