---
description: Resolve all actionable PR review comments with cited fixes, validation, and replies.
---

# AgentPack PR comment resolution

Use this command to validate, plan, fix, verify, and reply to inline or review comments on a PR.

Examples:

```text
/agentpack-resolve
/agentpack-resolve pr 123
/agentpack-resolve PR #123
/agentpack-resolve PR #123 focus on backward compatibility
```

1. Bind to the requested PR and fetch its latest head, inline review threads, and top-level PR comments. The slash command owns the full loop; the internal `--check` and `--reply` commands are agent steps, not extra user commands.
2. Read `.agentpack/resolve.prompt.md` and `.agentpack/resolve-comments.json` completely.
3. Write every comment's disposition to the declared `plan.toon`: `fix`, `no-action`, `stale`, `duplicate`, or `blocked`, with evidence citations for claims.
4. Run `agentpack resolve --check`; do not edit code until the plan passes.
5. Apply all validated fixes in one pass, run targeted and relevant project checks, then write one cited reply record per comment to `replies.toon`.
6. Run `agentpack resolve --reply`; it refuses missing citations, invalid replies, duplicate snapshot ids, and a changed PR head before posting.
7. Start a fresh `agentpack resolve --pr <number>` pass after replies. Continue until no actionable unresolved comment remains or the configured iteration limit is reached.
8. Reply format: short bold outcome, one concise explanation, `Suggested fix:` when relevant, exact `path:line` citations, and validation status. Do not mark threads resolved merely because a reply was posted.

If any comment cannot be verified or fixed, mark it `blocked` and explain the blocker with evidence. Do not silently defer actionable comments to a later round.
