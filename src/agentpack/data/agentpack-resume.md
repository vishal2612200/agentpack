---
description: Claim and resume a pending AgentPack handoff.
---

# AgentPack Resume

Resume the handoff named by the user. Prefer MCP `accept_handoff(name="$ARGUMENTS")` when available. Otherwise run:

```bash
agentpack handoff resume $ARGUMENTS
```

When no name is provided, allow AgentPack to select the only pending handoff or show its interactive picker. Follow the returned next action and fresh context. Do not reapply or reconstruct the patch manually.
