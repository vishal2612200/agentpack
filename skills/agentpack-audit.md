---
name: agentpack-audit
description: Prepare a loop-based AgentPack codebase audit atlas and developer report for a folder, module, or flow.
---

# AgentPack Audit

Use when the user invokes `@agentpack-audit <scope>` in Codex or `/agentpack-audit <scope>` in Claude Code.

Do not claim performance improvement unless measurement ran or a concrete validation plan is provided. Do not claim infrastructure/config risk from filenames alone; prove project usage first.

## Steps

1. Prepare the audit scaffold:

```bash
agentpack audit $ARGUMENTS
```

If arguments are empty, stop and ask for a scope.
2. Read `.agentpack/audit.prompt.md` and follow it completely.
3. Use the `auditing-codebase-atlas` pattern: frontier, explored areas, hypotheses, findings, rejected ideas, and loop log.
4. Do not produce a one-shot generic cleanup list. Promote findings only after the evidence gate passes.
5. Treat performance claims as `static-risk` until measured or backed by a concrete validation plan.
6. For infrastructure/config, start from actual project usage signals and prove the consumer/runtime/deploy path before promoting a finding.
7. Update `.agentpack/audit-report.md`, `.agentpack/audit-atlas.json`, and `.agentpack/audit-findings.json` before reporting.
8. Use direct `rg`, code reads, tests, profiles, logs, dry-run deploys, or plan commands as source of truth. AgentPack context is a starting map only.
9. Final response: report findings first, then `Infrastructure / Config Review`, `Hypotheses, Not Findings`, rejected/no-action areas, stop reason, and next pass plan.
