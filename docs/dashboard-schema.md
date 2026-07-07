# Dashboard Cockpit Schema

`agentpack dashboard` writes three local artifacts:

- `.agentpack/index.html` - bundled cockpit shell with embedded data for `file://` use and clean local serving at `/`.
- `.agentpack/dashboard-data.json` - normalized project, context, task-map, learning, observer, loop, and action snapshot.
- `.agentpack/dashboard-graph.json` - task-scoped decision graph consumed by the cockpit.

Both JSON files are versioned with `schema_version`. Additive fields are allowed
within the same schema version. Removing or renaming existing fields requires a
schema version bump and frontend compatibility handling.

## Graph Contract

`dashboard-graph.json` contains:

- `root_id`: the active task node, currently `task:active`.
- `summary`: node and edge counts, selected/omitted counts, memory count, high-risk count, truncation metadata, and the node cap used by the builder.
- `nodes`: typed graph nodes for `task`, `file`, `symbol`, `test`, `episode`, `procedure`, and `action`.
- `edges`: typed relationships for file/symbol containment, selection, omission, tests, memory influence, procedure applicability, breakage risk, and retrieval.

Node IDs are stable within a generated dashboard:

- `task:active`
- `file:<repo-relative-path>`
- `symbol:<stable-symbol-node-id>` when selected context contains AST/symbol metadata
- `test:<repo-relative-path-or-command>`
- `episode:<memory-id>`
- `procedure:<memory-id>`
- `action:<scope>:<id>`

The graph is intentionally bounded. When the node cap is reached,
`summary.truncated` is true and `summary.truncated_reason` explains why. The
frontend must treat missing nodes as omitted for display, not as missing source
truth.

## UX Invariants

- Source files, diffs, tests, runtime evidence, and PR review remain more
  authoritative than dashboard memory.
- Risk labels are advisory routing hints.
- Memory edges explain why context may matter; they do not prove a file should
  be edited.
- Every action command should be copyable and inspectable as plain text.
