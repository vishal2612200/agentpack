# Demo Assets

The README demo is a generated terminal animation, not a manual screen recording.
It shows the intended AgentPack loop: refresh context, route the task, inspect
ranked files, surface skill recommendations, preflight review output, record
learning, inspect advisory memory timeline rows, then run a focused test.

Regenerate the GIF and MP4 from the repository root:

```bash
python tools/render_demo_assets.py
```

Outputs:

- [`docs/assets/agentpack-demo.gif`](assets/agentpack-demo.gif)
- [`docs/assets/agentpack-demo.mp4`](assets/agentpack-demo.mp4)

Related static assets:

- [`docs/assets/agentpack-route-demo.svg`](assets/agentpack-route-demo.svg)
- [`docs/assets/agentpack-social.svg`](assets/agentpack-social.svg)
- [`docs/assets/icon.svg`](assets/icon.svg)

Keep the demo scoped to AgentPack's real promise: local preflight context for
coding agents. It should not imply that AgentPack replaces source inspection,
runtime evidence, review judgment, or tests.
