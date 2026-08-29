# AgentPack design and folder structure

AgentPack is local-first. CLI, MCP, dashboard, and CI share core project
identity, configuration, context, and evidence services. Dashboard data is
bounded and cached; repository scans happen only in project-scoped flows.

## Repository map

```text
src/agentpack/
├── cli.py                 CLI commands and entrypoint
├── core/                  config, identity, project index, cache, redaction
├── application/           orchestration shared by CLI and integrations
├── router/                task routing, selection, and context scoring
├── session/               task identity, events, handoffs, and references
├── dashboard/             HTTP server, contracts, project state, Atlas
│   ├── server.py          authenticated local API and request scoping
│   ├── portfolio.py       cached portfolio, relations, and bounded summaries
│   ├── github.py          explicit read-only GitHub evidence refresh
│   ├── project_overview.py project drill-down and project mutations
│   └── terminal.py        project-owned terminal sessions
├── architecture/          deterministic semantic graph and impact evidence
├── learning/              competency recommendations and proof sessions
└── data/dashboard_app/    packaged dashboard assets served by CLI

frontend/dashboard/
├── src/DashboardWorkspace.tsx  dashboard shell and view routing
├── src/components/dashboard/   portfolio and project views
├── src/data/                   API loaders, schema, generated contracts
└── src/styles/                 dashboard CSS

tests/                    unit, contract, integration, and browser tests
docs/
├── dashboard-v2.md       dashboard API and compatibility contract
└── schemas/              machine-readable API schemas
```

## Data flow

```text
CLI/MCP/browser
      ↓
dashboard server / application services
      ↓
core identity + project index + project-scoped readers
      ↓
bounded local caches and redacted evidence
```

`DashboardServerState.launch_root` identifies server origin only. Requests
resolve `project_id` and `workspace_id` against registered project index;
they never mutate server root. Portfolio reads use index rows and cached
status. Project drill-down may scan selected workspace. GitHub reads happen
only after explicit refresh and use installed `gh`.

Keep new dashboard contracts in Python models, update
`docs/schemas/dashboard-v2.schema.json`, regenerate TypeScript definitions,
and rebuild `src/agentpack/data/dashboard_app` before commit.
