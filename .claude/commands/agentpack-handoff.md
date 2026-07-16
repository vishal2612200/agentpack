---
description: Package the current task and all Git-visible work for another agent session.
---

# AgentPack Handoff

Create a complete structured handoff for the current task. Inspect the current task, task state, Git diff, and recent validation evidence. Build a JSON report with `task`, `acceptance_criteria`, `summary`, `next_action`, `completed`, `remaining`, `decisions`, `blockers`, `validation`, `changed_files`, and `dirty_files`.

Prefer MCP `create_handoff(report=..., name="$ARGUMENTS")` when available. Otherwise write the report to a temporary JSON file and run:

```bash
agentpack handoff create --input <report.json> --name "$ARGUMENTS"
```

Do not include guessed validation. Use `not_run` with a reason when checks were not run. Report the generated human-readable handoff name.
