# AgentPack Dashboard UI System

The dashboard is a context cockpit, not a generic analytics template. It should help a developer see how AgentPack selected context, attached memory, mapped risk, reviewed work, and prepared learning material.

## Stack

- React + Vite for the packaged dashboard app.
- Radix primitives for accessible interaction behavior.
- Local shadcn-style components under `src/components/ui`.
- AgentPack cockpit components under `src/components/cockpit`.
- React Flow for AST, task, memory, review, and test graph visualization.
- TanStack Table through `DataTable` for project, review, risk, memory, and learning tables.

Snow Dashboard UI Kit is a visual benchmark for spacing, density, sidebars, status chips, cards, tables, and dashboard polish. It is not the product model and should not be copied wholesale.

## Component Boundary

Use `src/components/ui` for reusable primitives:

- `Button`
- `Badge`
- `Card`
- `Input`
- `Tabs`
- `Select`
- `Dialog`
- `Tooltip`
- `DataTable`

Use `src/components/cockpit` for AgentPack-specific composition:

- `AppShell`
- `MetricCard`
- `InspectorPanel`

Keep custom code only where AgentPack is unique:

- AST and memory graph nodes.
- Context health and token-savings summaries.
- PR review evidence and commands.
- Learning prep and weak-spot surfaces.

## Token Rules

Colors must come from `src/styles/tokens.css`. Add product concepts as tokens before hardcoding colors in a component. Current product tokens include context, memory, risk, review, learning, success, and warning states.

Prefer semantic classes such as `ui-badge-risk`, `cockpit-metric-memory`, or `flow-node procedure` over one-off color declarations in screen components.

## Migration Rules

- New dashboard screens should use the UI primitives by default.
- Table-like views should use `DataTable`.
- Navigation, topbar, and inspector changes should go through cockpit components.
- Graph canvas behavior stays in React Flow, but toolbars, filters, legends, empty states, and inspector surfaces should use standard primitives.
- Avoid page-local card, button, badge, input, or table styles unless the UI component layer is missing a required variant.

## Verification

For dashboard UI changes, run:

```bash
npm run typecheck
npm run build
```

From the repo root, also run the focused dashboard Python tests when data contracts or packaging change.
