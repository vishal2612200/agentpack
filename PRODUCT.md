# Product

## Register

product

## Users

AgentPack serves three overlapping developer audiences:

- Agent-using developers working locally with Codex, Claude Code, Cursor, Windsurf, MCP-capable hosts, and similar coding-agent tools.
- Engineering teams that need repeatable local preflight, context freshness, review evidence, and workflow guardrails across multiple agents and repos.
- Tool builders integrating AgentPack into editor plugins, MCP hosts, CI workflows, slash commands, and other developer-control surfaces.

Users are usually in the middle of a concrete coding task. They need to orient an agent quickly, understand what context is fresh or stale, inspect why files were selected, and run the next safe command without losing trust in the source of truth.

## Product Purpose

AgentPack is a local-first context engine and developer control plane for AI coding agents. It ranks relevant repo files, builds compact task-focused context, surfaces skills/rules/tests, records local evidence, and exposes the same state through CLI, MCP, dashboard, and integration surfaces.

Success means a developer or team can start an agent closer to the right files, understand why AgentPack made a recommendation, verify freshness and safety before edits, and keep source code, diffs, tests, runtime evidence, and PR review as the final authority.

## Brand Personality

Bold developer brand meets power-user control:

- Direct, technical, and evidence-oriented.
- Local-first and transparent rather than magical or cloud-dependent.
- Dense enough for expert workflows, but calm enough that warnings, risks, and next actions are easy to scan.
- Opinionated about guardrails without pretending to be the agent.

## Anti-references

AgentPack should not feel like:

- Generic SaaS fluff with vague productivity claims.
- A cloud-indexed black box or hidden repo-intelligence service.
- Another autonomous coding agent.
- Decorative AI-dashboard chrome that makes simple state harder to read.
- A toy terminal skin that sacrifices accuracy, copyability, or keyboard workflow.

## Design Principles

1. Start from evidence, not vibes.
   Every recommendation should expose source, freshness, reason, command, or verification path.

2. Keep power visible, but controlled.
   Advanced commands, MCP state, terminal execution, and repair flows should be available without hiding risk or side effects.

3. Local-first trust.
   The UI should make clear what is checked locally, what the host must prove, and where live/runtime evidence is still missing.

4. Dense when useful, never noisy by default.
   AgentPack users value compact context, task maps, tables, and command surfaces, but hierarchy must keep urgent action distinct from advisory data.

5. The product supports the developer; it does not perform theater.
   Avoid decorative intelligence cues. Favor clear status, exact paths, runnable commands, and concise explanations.

## Accessibility & Inclusion

Use WCAG AA as the baseline. Product surfaces should be keyboard-first, readable at dense information levels, and safe for reduced-motion users. Color must not be the only status cue; warnings, risks, repair paths, and terminal states need text labels. Motion should communicate state changes only and respect `prefers-reduced-motion`.
