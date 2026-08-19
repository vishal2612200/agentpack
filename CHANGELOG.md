# Changelog

All notable changes to agentpack-cli are documented here.

Format: `## [version] — YYYY-MM-DD` followed by categorised entries.

---

## [Unreleased]

## [0.4.4] — 2026-08-19

### Added
- Added bounded MCP response contracts, workspace-scoped request reuse, and
  route phase telemetry for more predictable context-engine behavior.

### Changed
- Reduced MCP token-heavy response fan-out while preserving detailed retrieval
  through explicit follow-up tools.
- Added session and observer event fingerprints to route-cache invalidation and
  cross-process locking for JSONL telemetry writes.

### Fixed
- Prevented MCP caches from crossing workspace boundaries or reusing stale
  route state after session and observer events.
- Filtered Android build artifacts from context selection and preserved the
  `mcp<2` compatibility cap for the FastMCP runtime.

## [0.4.3] — 2026-08-12

### Added
- Added core-first architecture map controls and deterministic route-performance coverage.
- Added release evidence validation and blocking tag-workflow quality gates.

### Changed
- Bounded warm route planning, tightened explicit PR-target routing, and split E2E benchmark commands into a focused module.
- Expanded README installation guidance for pipx, pip, and the npm wrapper.

### Fixed
- Repaired malformed and stale managed Git hooks while preserving unrelated hook commands.
- Fixed dashboard startup and client-disconnect handling, and restored graph identity retrieval for memory episodes.
- Updated cryptography to 50.0.0 and PostCSS to 8.5.23 in tracked lockfiles.

### Validation
- PR #107 remediation and security follow-up #109 passed repository CI gates required for merge.
- Release metadata is synchronized across Python, npm, and Codex plugin manifests.

## [0.4.1] — 2026-07-21

### Added
- Added graph, history, and host-neutral handoff surfaces so work can move between supported agent sessions with bounded project state.
- Added typed Dashboard v2 contracts, impact-aware workspace navigation, action inspection, agent operation states, browser acceptance coverage, and v1-compatible request behavior.
- Added cited PR comment resolution with validated snapshots, plans, fixes, replies, idempotent retries, and Codex/Claude plugin distribution.
- Added skill review and evaluation generation for auditing `SKILL.md` trigger behavior with deterministic findings.
- Added evidence-backed local and cross-project learning recommendations, interactive topic sessions, scored mastery, and a read-only dashboard learning view.
- Added a project-level dashboard with outcomes, milestones, confirmed initiatives, independent health dimensions, risks, decisions, unified activity, worktree filtering, and deterministic Summary/Engineering status briefs.
- Added revisioned project profile APIs and idempotent append-only project events while preserving existing dashboard and command contracts.
- Added an evidence-ranked Now section, a keyboard command palette, independent runtime evidence, and a redacted seven-day last-known project snapshot with read-only recovery behavior.

### Changed
- Made the repository the primary dashboard workspace and unified graph, map, table, task, session, and handoff navigation around that project identity.
- Reframed the primary CLI surface around `work`, `learn`, `finish`, and `doctor` while retaining every existing command.
- Made Overview, Roadmap, Work, Health, and Knowledge the primary dashboard navigation; Activity opens from Overview or the command palette, while task-scoped context and diagnostic tools remain under Explore.
- Refreshed the public product narrative, badges, integration guidance, dashboard documentation, and packaged agent instructions to match the reliability-layer positioning.
- Updated GitHub Actions dependencies and synchronized initialization ignore rules for generated review artifacts.

### Fixed
- Stopped treating queued or unscored learning sessions as weak spots.
- Resolved repository-local Git hooks through the common Git directory so `work` and setup flows run from linked worktrees.
- Scoped project event mutations to the selected worktree and isolated last-known dashboard cache entries by project id.
- Rebuilt the bundled dashboard assets so the served package matches the current source.

### Documentation
- Added the Dashboard v2 API and migration guidance, project examples, responsive dashboard screenshots, and runtime-loop documentation for learning, project state, and handoffs.
- Updated command, integration, Codex plugin, Claude skill, and README documentation for the current workflow and review-resolution surfaces.

## [0.4.0] — 2026-07-15

### Added
- Added deterministic architecture snapshots, PR context, and CI checks so changed ownership and dependency context can be inspected and validated with stable artifacts.
- Added an optional tree-sitter backend for Java, Ruby, and PHP symbol and import extraction while retaining the established fallback behavior.
- Added typed ownership evidence, comparative owner features, and calibration reporting to strengthen context-selection diagnostics.
- Added the Anchor -> Judge -> Critic -> Actor review pipeline: candidate findings require a per-finding critic verdict before the Actor can publish approved inline comments.
- Added graph observability and a context cockpit for inspecting selection state.

### Changed
- Improved review output so non-inline findings remain visible in the review body and inline comments can include AgentPack badges.
- Improved task maps with risk-aware context selection and strengthened dashboard graph observability.

### Fixed
- Stabilized benchmark release-gate execution and allowed confirmed dirty task targets during guard refreshes.

### Documentation
- Refreshed distribution, trust, and contributor guidance for the packaged plugin and project listings.

## [0.3.39] — 2026-07-06

### Added
- Broadened the scoped `mypy` gate beyond the initial three-file set to cover the entire `src/agentpack/analysis/` package (import extraction, dependency graph, ranking, monorepo, and task classification), with behavior-preserving type fixes in `analysis/ranking.py` and `analysis/task_classifier.py` and a `tomllib`/`tomli` import override for the optional TOML backends.
- Added a Rust symbol extraction slice: `extract_rust_symbols` pulls free functions (including `async`/`const fn`), `impl`/`trait` methods qualified by their owning type, and `struct`/`enum`/`trait` as class-like constructs, wired through `extract_symbols` and the offline summary path (`_rust_summary`). Documented the supported slice and its regex-based limits in `docs/limitations.md`.
- Added JSON-output smoke coverage for scriptable `route`, `next`, and release-check flows.

### Changed
- Improved the release benchmark and memory-gate diagnostics with stronger precision accounting, activation analysis, and regression coverage for context selection.
- Tightened ranked carrier compaction for lower-action support files, clearing the official public release gate at 107 scored cases, 67.2% recall, and 50.6% average token precision.

### Fixed
- Forced generated guard fallback commands and repair output to use global context unless thread mode is explicit, avoiding stale ambient Codex session missing-task failures when `.agentpack/task.md` is the intended source.

## [0.3.38] — 2026-07-04

### Fixed
- Hardened guard and review checks so default guard refreshes use global context unless scoped threading is explicit, stale active review preflight is blocked before artifact validation, and review prompts steer agents toward JSON authoring instead of fragile multiline TOON.
- Canonicalized schema-valid review JSON into checked TOON handoff files, preserving `understanding.toon` as the Stage 2 input while making Stage 1/Stage 2 easier for agents to write and validate.
- Added guard/review git preflight metadata so agents decide whether to sync, inspect dirty state, or proceed before refreshing context or starting edits.
- Skipped expensive prompt-hook work for chat-only prompts and ignored stale review state from other branches, reducing UserPromptSubmit timeout noise and unrelated missing-artifact failures.

## [0.3.37] — 2026-07-03

### Added
- Added a GitHub-ready release notes artifact step to `agentpack release prepare`, including release metadata, validation evidence, the matching changelog entry, and the publish command.
- Included the locked Python dependency graph in the packaged Codex plugin snapshot so plugin trust scans see a lockfile in the distributable surface.

## [0.3.36] — 2026-07-03

### Documentation
- Refreshed the README demo GIF/MP4 and packaged plugin screenshot so public assets show the full AgentPack workflow: route, skill recommendations, review preflight, learning capture, advisory memory timeline, and focused validation.
- Added docs social preview and icon assets, then wired MkDocs logo, favicon, and social preview metadata to those checked-in assets.
- Updated demo documentation so generated media and static distribution assets are discoverable and reproducible.

### Validation
- Added docs/plugin tests that keep route-demo, social preview, icon, GIF, MP4, and packaged Codex plugin assets aligned with the full workflow.
- Ran `python -m agentpack.cli release-check --profile docs --json`.
- Ran `python -m py_compile tools/render_demo_assets.py`, `git diff --check`, SVG parse checks, a generated GIF frame inspection, and `mkdocs build`.

## [0.3.35] — 2026-07-03

### Added
- Added `agentpack review --check --dry-run-post` so inline GitHub review payloads can be validated and hashed before any live post.
- Added MCP `validate_toon(..., return_canonical=true)` for agents that need canonical TOON returned without rewriting files.
- Added route `selection_explanations` and `omitted_files` so `route`, MCP route results, and generated prompts explain both why files were selected and why plausible candidates were skipped.
- Added learning/session context surfaces for local task memory, dashboard learning rows, and AgentPack learn guidance.

### Changed
- Simplified first-run developer UX across `agentpack --help`, `quickstart`, and `next` so new users get one primary path before optional diagnostics.
- Clarified `doctor` versus `repair`: `doctor` diagnoses and can apply safe local repairs with `--fix`, while `repair` remains the explicit mutation command.
- Improved `doctor`, `guard`, and `review --check` action output with what failed, why it matters, the exact repair command, and whether it is safe to continue.
- Improved AgentPack tooling route classification and active-agent detection for Codex versus Antigravity project markers.

### Fixed
- Fixed review inline posting so live GitHub comments require a matching fresh dry-run payload instead of posting unchecked findings.
- Tightened review TOON schema validation for enum values, nested shapes, location/evidence fields, and citation support.
- Fixed Python 3.10 docs-link test collection by falling back to `tomli` when stdlib `tomllib` is unavailable.

### Documentation
- Updated README current-release notes for onboarding, repair guidance, review posting safety, route explainability, and refreshed agent integration behavior.
- Updated command docs and packaged skills for review dry-run payload shape, cleanup guidance, MCP/TOON output, and route explanation fields.

### Validation
- PR #48 passed GitHub Actions across Python 3.10, 3.11, 3.12, 3.13, and 3.14 plus `dev-check`, `pack`, `scan`, `npm-wrapper`, `agent-integration-matrix`, and `plugin-scanner`.
- Ran `python -m pytest tests/ -q -m "not slow"` locally with `1335 passed, 2 deselected`.
- Ran focused review, TOON, MCP, route, quickstart, doctor, guard, and command-surface tests during the release tranche.
- Ran `ruff` and `git diff --check`.

## [0.3.34] — 2026-06-29

### Added
- Added a bounded MCP runtime diagnostic that checks whether the `agentpack` command resolves, whether `mcp.server.fastmcp` imports, and whether `agentpack mcp` starts without leaving a long-running process behind.
- Added explicit MCP runtime reporting to `agentpack install --agent codex|claude`, `agentpack repair --agent codex|claude`, and `agentpack doctor --agent <agent>` so setup now distinguishes registration, local server readiness, and live host exposure.
- Added exact remediation text for missing MCP extras, including `pipx inject agentpack-cli "agentpack-cli[mcp]"` for installed CLI users and `python -m pip install -e ".[mcp]"` for source checkouts.
- Added generated-agent guidance for Codex, Claude, Cursor, Windsurf, and Antigravity that tells agents to prove MCP exposure with readiness first, run only a bounded `agentpack mcp` diagnostic when tools are absent, and then continue with CLI/direct-search fallback.

### Changed
- Clarified `agentpack mcp` as a low-level stdio server entrypoint launched by agent hosts from MCP config, not a daily developer command.
- Split `agentpack doctor` MCP output into `MCP registration`, `MCP runtime`, and `Live host exposure` sections so a locally runnable server is not confused with tools being exposed inside the current host session.
- Made hook messaging less noisy by deduplicating MCP fallback and review-preflight reminders per task/session while resetting those reminders on `SessionStart`.
- Updated deploy-task hook guidance to say AgentPack is a guardrail and context frame, while rendered deploy configuration and live platform state remain the source of truth for production deployment checks.
- Updated review-task guidance so staged review workflows stay explicit about preflight, understanding, and findings artifacts instead of relying on inline prompt discipline.
- Restored `agentpack pack --task "<task>"` as a supported one-command path that writes the task into the active task file before packing, while preserving `--task auto` as the default read-existing-context mode.

### Fixed
- Fixed MCP fallback UX where configured integrations could still leave agents without callable tools and no clear diagnostic path.
- Fixed review context anchoring so PR review workflows prefer the actual PR target and diff instead of silently anchoring to the checked-out branch or `HEAD~1` fallback.
- Fixed TOON citation validation for evidence strings with prose before a `path:line` token by avoiding path parsing that absorbs leading sentence text into nonexistent filenames.
- Reduced unrelated dirty-tree and wrong-task context noise by making stale reasons explicit and by routing agents back to direct `rg`, PR diff inspection, and focused validation when AgentPack context is missing, stale, or wrong-worktree.

### Documentation
- Expanded MCP setup docs with the supported repair/doctor/restart/readiness workflow and bounded manual diagnostic behavior.
- Updated packaged skills, Codex plugin skills, and slash-command docs to document `pack --task "<task>"`, MCP readiness checks, and CLI fallback expectations.
- Updated README current-release notes for the 0.3.34 MCP reliability, review anchoring, deploy routing, and citation-validation fixes.

### Validation
- Ran focused MCP, install, repair, doctor, hook, and generated-instruction tests.
- Ran `PYTHONPATH=src python -m pytest -q -m 'not slow'` with `1278 passed, 2 deselected`.
- Ran `ruff`, `compileall`, `git diff --check`, and a bounded local source MCP stdio probe.

## [0.3.33] — 2026-06-28

### Documentation
- Sync the root README with the current release line, including the latest proof table, feature summary, and package status.

## [0.3.32] — 2026-06-28

### Added
- Added grounded broad-context packing for review, sharing, audit, and repo-overview tasks, including citation-backed broad context and safety gates to keep expanded context scoped.
- Added memory and evaluation support for grounded context workflows, including episodic learning records, learning quality checks, and A/B evaluation plumbing.
- Added runtime/performance reporting surfaces for recent AgentPack pack, retrieval, and output-compression activity.

### Changed
- Lightened the main AgentPack CI release gate by using the CI release-check profile for push validation.
- Strengthened packaged Codex plugin trust signals with published documentation URLs, packaged Dependabot coverage, and the packaged HOL plugin-scanner workflow.
- Improved review prompts and staged review artifacts so review runs must first produce file-grounded understanding before findings.

### Fixed
- Hardened stale-context fallback behavior and routing defaults so agents get clearer provenance and safer guidance when MCP or context artifacts are unavailable.
- Surfaced review-context preflight guidance in prompt hooks so review requests point at the right changed-file workflow.
- Fixed the npm launcher to pass the full Python descriptor through virtualenv setup, preserving the selected interpreter metadata.
- Improved light benchmark fixture recall by keeping correlated source/deploy context under strict summary selection.
- Retuned synthetic fixture prompts to include concrete owner terms before release validation.

## [0.3.31] — 2026-06-25

### Added
- Added TOON structured rendering/parsing support so AgentPack can emit lower-token structured context and prompt payloads.
- Added routed task-mode and reviewer guidance for coding, PR review, release/docs, integration, and debugging workflows.
- Added Codex local plugin activation docs and packaged plugin metadata so Codex installs can expose AgentPack surfaces more reliably.

### Changed
- Enriched pack sufficiency receipts with clearer freshness, omitted-file, and verifier hints for follow-up agents and reviewers.
- Hardened distribution surfaces across agent rules, workflows, plugin assets, and HOL registry metadata.
- Optimized bundled SVG/PNG assets for smaller documentation and plugin payloads.

### Contributors
- `caioribeiroclw-pixel` / Caio Ribeiro contributed the pack sufficiency receipt work in PR #13.
- `imgbot` contributed asset optimization in PR #12.
- `yashkumar2603`, `heyvishalsharma`, and `vishal2612200` reviewed or commented on release changes in PR #13.

## [0.3.30] — 2026-06-23

### Added
- Added `agentpack review` as a local two-stage PR review workflow that writes preflight, understanding, and judgment artifacts for the current branch or PR.
- Added the `/agentpack-review` Claude slash command and packaged skill/docs updates so the same review entrypoint is available across local and packaged surfaces.
- Added publishable handoff receipts and persisted understanding/findings outputs so review runs leave stable artifacts for follow-up and distribution.

### Changed
- Tightened PR-scoped routing and handoff selection so review context prefers the active diff and preserves higher-risk omitted-file signals.

### Fixed
- Reduced prompt-submit latency by limiting prompt hooks to active task flows instead of firing on every prompt.

## [0.3.29] — 2026-06-21

### Added
- Added the context-aware `/agentpack-learn` command and packaged Codex skill so agents can explain recent local AgentPack session context without losing prompt-cache friendliness.
- Added structured issue-reference memory for task, branch, commit, GitHub PR, and optional Jira metadata, including title/state/label details where available.

### Changed
- Made AgentPack routing use recent issue references as low-noise hints when the current task omits explicit ticket or PR context.
- Extended local memory output to expose recent issue references and enriched details for future handoffs and diagnostics.

### Fixed
- Registered AgentPack MCP in Codex's host config during install, repair, and upgrade so upgraded repos are less likely to need manual MCP setup.

## [0.3.28] — 2026-06-20

### Fixed
- Repaired the doctor release-hygiene regression test fixture so CI validates advisory release-noise warnings without unrelated missing-init/MCP failures.
- Supersedes the failed `0.3.27` publish attempt, where npm completed but PyPI was blocked by the CI fixture failure.

## [0.3.27] — 2026-06-20

### Added
- Added prompt-quality routing hints so short, vague, repeated, or simple-question tasks produce Ask/Chat, file-context, output-length, and spec guidance before agents enter heavy coding workflows.
- Added generated agent-rule guidance for concise outputs, file references, acceptance criteria, constraints, and validation expectations.

### Changed
- Made `agentpack upgrade` the post-upgrade repair path for existing repo integrations and already-installed global AgentPack git/shell hooks.
- Kept release hygiene findings advisory in `agentpack doctor` so local generated artifacts do not fail otherwise healthy installations.
- Suppressed weak external side-effect skill warnings unless the route has strong task-specific evidence.

### Fixed
- Fixed rich CLI help parsing so doctor detects the installed command surface reliably.
- Avoided false external skill matches from conversational prompt terms such as `can` and `you`.

## [0.3.26] — 2026-06-19

### Added
- Added MCP `readiness()` proof output so hosts can confirm live AgentPack MCP tool exposure from inside the active agent session.
- Added command-surface-aware refresh guidance so generated agent instructions and fallback commands avoid unavailable CLI commands.
- Added task-mode routing for small edits, PR review, runtime debugging, integration readiness, release/docs work, and broad feature work.
- Added runtime/integration evidence checklists and E2E benchmark noise metrics.

### Changed
- Expanded context freshness provenance with task, cwd, git/worktree path, branch, timestamp, AgentPack version, and source command.
- Prioritized PR/diff files ahead of generic metadata while suppressing unrelated generated/noisy files.
- Kept benchmark and native-integration docs scoped to current evidence and advisory host limits.

### Fixed
- Preserved changed workflow files in PR review routing even though unrelated workflow metadata remains noisy by default.
- Replaced remaining static `agentpack guard` refresh instructions with portable pack/doctor guidance where command-surface detection is unavailable.

## [0.3.25] — 2026-06-14

### Added
- Added `agentpack upgrade --agent auto` to refresh the detected IDE/agent integration after package upgrades.
- Packaged AgentPack's Codex plugin assets inside the Python wheel so Codex setup can refresh the local plugin cache when Codex is detected or explicitly selected.

---

## [0.3.24] — 2026-06-14

### Added
- Added thin AgentPack plugin/rule distribution surfaces for Codex, Cursor, Windsurf, Copilot, Cline, Kiro, OpenCode, and generic agent workflows.
- Added a sharper product README story and AgentPack symbol around the local preflight map for existing coding agents.
- Added `release-check --profile docs` and `make release-docs` for docs/plugin-only releases without package build or public benchmark runs.

### Changed
- Reorganized docs navigation around getting started, agents and IDEs, guides, evidence, trust, and development.
- Kept benchmark claims scoped as file-selection evidence instead of broad agent-success claims.

---

## [0.3.23] — 2026-06-14

### Added
- Added guarded-loop runner adapters for Claude, Codex, and Cursor plus `agentpack loop-smoke` for deterministic and real-agent smoke checks.
- Added loop phase tracking, runner JSON contracts, diff snapshots, acceptance evidence, risk reviews, rollback patches, handoff notes, and loop metrics.

### Changed
- Repositioned Ralph Loop as an optional proof harness around existing agents, keeping AgentPack's default workflow focused on local context and workflow evidence.
- Updated the dashboard and docs to use "Guarded Loop" / optional-loop language instead of presenting the loop as the core product path.

### Fixed
- Run loop runners from the fixture or repo root and write an explicit runner prompt contract before invoking adapter commands.
- Block repeated no-progress verification failures with diagnosis artifacts instead of burning all loop iterations.

---

## [0.3.22] — 2026-06-13

### Added
- Added benchmark intent diagnostics and label-audit diagnostics to make recall and precision failures easier to inspect by task family.
- Added a 2026-06-13 expanded public-suite baseline showing 66.0% recall and 51.1% token precision across 108 scored public cases, plus sensitive Click and NestJS regression-slice readouts.

### Changed
- Improved search and AI-discovery readiness with aligned package metadata, project URLs, README positioning, MkDocs site metadata, intent-page expansions, and AI-readable `llms.txt` summaries.
- Recovered maintenance-context files under strict caps, clearing the 65% recall target while keeping token precision above the 51% release floor.
- Documented that config/build recall and NestJS token precision remain the main follow-up risks.

---

## [0.3.21] — 2026-06-12

### Added
- Added expanded public-suite benchmark diagnostics for expected files, selected paths, selected token counts, failure types, reason-family precision, family waste, and JSONL persistence.
- Added public benchmark filtering by repository and task type so tuning can validate targeted slices before running the full suite.
- Added AgentPack-vs-no-AgentPack E2E reporting fields for task success, tool calls, token cost, and time-to-first-correct-file.
- Added privacy, threat-model, data-flow, and benchmark-learning docs for release trust.

### Changed
- Removed the public `minimal` mode surface; legacy `minimal` requests now map to `balanced`.
- Improved balanced-mode selection with narrower low-budget summary caps, root Go source support, same-package paired-test overflow, and same-playground test overflow.
- Corrected sampled public-suite methodology by filtering expected files against the parent checkout so files added by the target commit are not counted as selectable misses.
- Simplified the README around the core workflow: route, pack, agent acts, benchmark captures miss.
- Documented the next benchmark release target: move the expanded 109-case public suite from the current 57.0% recall / 50.6% token precision baseline to 65%+ recall while holding token precision at 50%+ and publishing per-language slice results.

---

## [0.3.20] — 2026-06-11

### Added
- Added a dedicated "How AgentPack works" guide covering scan, rank, compress, cache, retrieve, route, stable-prefix caching, skill keyword quality, and the future hybrid BM25 plus semantic-search direction.
- Added regression coverage for persisted skill-routing benchmark metrics so weak skill keyword changes are easier to catch.

### Changed
- Polished the local dashboard with a calmer visual system and a compact quality summary strip.
- Expanded benchmark documentation for `expected_skills`, `avoid_skills`, `skill_recall_at_3`, `skill_precision_at_3`, `skill_mrr`, and `skill_noise_rate`.

---

## [0.3.19] — 2026-06-11

### Added
- Added prompt-cache friendly stable prefixes to markdown and compact context renderers so provider prefix caches can reuse invariant instructions across context refreshes.

### Changed
- Documented AgentPack's compress/cache/retrieve model and automatic prompt-prefix cache alignment in the README, runtime loop docs, and architecture docs.

---

## [0.3.18] — 2026-06-10

### Added
- Added runtime-loop context retrieval, pack registry, session events, output compression, memory, perf, and wrap commands.
- Added symbol-level registry records and MCP retrieval/compression surfaces.
- Added selected-file miss feedback so `agentpack learn` can feed bounded ranking boosts into later packs.

### Changed
- Updated runtime-loop documentation, generated artifact ignores, and benchmark notes.

---

## [0.3.17] — 2026-06-10

### Added
- Added the AgentPack Dashboard as a local static control plane for context health, skill recommendations, learning artifacts, benchmark summaries, active threads, and Ralph Loop state.
- Added the Ralph Loop protocol to automate runner and verification cycles behind existing workflow commands.
- Added automatic freshness-aware skills index sync and dashboard inventory visibility for discovered skill directories, domains, side effects, and metadata quality.

### Changed
- `agentpack skills index` now writes a metadata-only v2 index document with source fingerprints while routing, dashboard, and `next` lazily refresh stale indexes.

---

## [0.3.16] — 2026-06-10

### Changed
- Bundled `watchdog` in the default Python package dependencies so normal `pipx`, `pip`, and npm wrapper installs use native filesystem watching for `agentpack watch`.

---

## [0.3.15] — 2026-06-09

### Added
- Added richer skill metadata, confidence thresholds, negative triggers, weighted path scoring, and diversity-aware skill selection.
- Added `agentpack skills recommend`, `agentpack skills feedback`, MCP `get_skill`, baseline skill guidance, and skill benchmark metrics for expected and avoided skills.

### Fixed
- Kept route and explain payloads metadata-only by stripping raw skill bodies until `get_skill` is called.
- Stabilized incremental scan fingerprints so generated AgentPack output path churn does not force avoidable full scans.

---

## [0.3.14] — 2026-06-09

### Added
- Added feedback-aware `agentpack learn` provider command mode, static dashboard export, and team lesson export.
- Added skill-memory and practice-drill surfaces for developer learning follow-up.
- Added bounded failing-output excerpts to `dev-check` and `release-check` so CI shows the actual failing test name.

### Changed
- `agentpack learn` now keeps generated dashboard and team lesson artifacts ignored by default and omitted from future packs.

---

## [0.3.13] — 2026-06-09

### Added
- Added `agentpack learn`, a local learning layer that writes developer learning notes, skill evidence, future-agent lessons, LLM prompt artifacts, PR-comment summaries, and feedback records from task-scoped git changes.
- Added true `agentpack learn --today` calendar-day aggregation for committed and dirty files.

---

## [0.3.12] — 2026-05-27

### Added
- Added thread-scoped task, context, metadata, and execution-state tracking for concurrent agent work, including overlap warnings from `.agentpack/thread_index.jsonl`.
- Added rendered-token budget accounting, largest token consumer reporting, compressed receipts, and reserve buckets for changed files, tests, docs, and dependencies.
- Added CLI automation commands for repeated developer workflows: `work`, `finish`, `start`, `task`, `next`, `threads`, `state`, `diagnose-selection`, `dev-check`, `verify-wheel`, `release prepare`, and `ci init`.
- Added benchmark capture helpers, release gate wrappers, wheel verification, CI scaffolding, and a public benchmark proof artifact.
- Added enforce-lite skill recommendations so safe always-recommended coding skills can be selected for coding tasks without enabling external side-effect skills.

### Changed
- Split the oversized README into focused documentation pages while keeping the root README as a compact package landing page.
- Agent integrations now document explicit thread mode and avoid silently adopting ambient host session ids unless `--thread auto` is used.
- Release and PR automation now use current task-file based pack semantics.

---

## [0.3.11] — 2026-05-25

### Added
- Added AgentPack Router, a read-only task router available through MCP `route_task`, `get_skills`, and `explain_route`.
- Added CLI debug/admin surfaces: `agentpack route`, `agentpack skills scan`, and `agentpack skills index`.
- Added deterministic skill/rule discovery for Claude, Codex, Cursor, AgentPack project skills, and root agent rule files.

### Changed
- Agent installer guidance now prefers `route_task` before full context packing when MCP is available.

### Security
- Skill index files now store metadata only and omit raw skill/rule bodies.
- External side-effect skills are warned and excluded from selection by default.

---

## [0.3.10] — 2026-05-25

### Added
- Added `agentpack eval`, a local deterministic eval harness for real agent failures with TOML cases, command checks, diff limits, required/forbidden file checks, golden-file comparisons, JSONL results, Markdown reports, and failure taxonomy labels.
- Added `agentpack eval --watch --until-pass`, variant attribution via `--variant` and `--compare-variants`, and `--ci-template` for GitHub Actions eval runs.
- Added patch-based replay: `--capture` stores patch artifacts and context metadata, while `--replay` applies captured patches in isolated git worktrees before running checks.
- Added check retries with flaky-pass recording and tuning suggestions from recent eval failures.
- Added the Pepy total PyPI downloads badge to the README.

### Security
- Captured eval patch artifacts are now scanned with AgentPack's local secret redactor before being written; cases record `patch_redaction_warnings` when a secret is replaced.

### Documentation
- Documented deterministic eval workflows, replay datasets, attribution comparisons, CI usage, and the executable trust boundary for eval TOML files.

---

## [0.3.9] — 2026-05-23

### Added
- Added `agentpack global-repair-hooks` to refresh `~/.git-templates/hooks/`, reassert the global `init.templateDir`, and repair the current repo's `.git/hooks/` after an upgrade.

### Fixed
- Global git template hook installs now update stale marker-managed hooks instead of leaving legacy shell snippets in place.
- AgentPack-managed git template hooks now end with `exit 0`, so non-AgentPack repos and fresh clones succeed cleanly even when the hook runner decides no repack is needed.
- README and npm README now document the repaired `GitAutoRepack` path and the follow-up repair command for older copied hooks.

---

## [0.3.8] — 2026-05-23

### Fixed
- `agentpack init --force` now backs up an existing `.agentignore` consistently even when the synced ignore content is already up to date, removing environment-dependent release test behavior.
- Added a focused regression test for the unchanged-content backup path so CI and local runs exercise the same force-mode semantics.

---

## [0.3.7] — 2026-05-23

### Fixed
- Made the `agentpack init --force` backup regression test deterministic by seeding a repo-local `.gitignore`, avoiding environment-dependent `.agentignore` import behavior in release CI.
- Recovery release after the failed PyPI-only `0.3.6` publish workflow, keeping npm and PyPI version lines aligned again.

---

## [0.3.6] — 2026-05-22

### Added
- Added `agentpack ignore sync`, a shared `.agentignore` sync path that imports safe noisy rules from root and nested `.gitignore` files, `.git/info/exclude`, and the configured global Git ignore file.
- `agentpack doctor` now warns when the imported `.agentignore` block is stale, and `init` now prints imported ignore-rule summaries while reusing the same sync engine.
- Expanded `.agentignore` defaults for common generated noise such as Serverless, caches, temp directories, and snapshot artifacts.

### Changed
- Broad no-live-change packs now cap weak filename/meta-only matches, compress them to summaries, and suppress repeat noisy paths more aggressively when recent metrics already proved them unhelpful.
- Pack and stats guidance now push users toward concrete task wording and `agentpack ignore sync` when recent precision metrics show noisy context selection.
- Published package metadata no longer includes Claude-specific keywords in Python or npm package manifests.

---

## [0.3.5] — 2026-05-20

### Added
- Hybrid task-context auto-refresh: MCP `get_context()` now blocks for a fresh pack when `.agentpack/task.md` differs from the packed task, while hook prompts continue to trigger background repacks.
- Shared task-freshness helpers and task hashes in pack metadata so stale task state is reported consistently across MCP, status, doctor, and rendered context.
- MCP `get_context()` also auto-refreshes when the repo snapshot changed, and Claude prompt hooks block once on task switches so first-turn hints are fresh.
- MCP `get_context()` now refreshes when pack metadata or snapshot state is missing instead of returning cached context with only a stale header.
- Agent installer rules now prefer MCP as the active context path and treat markdown files as fallback artifacts.
- Added `agentpack guard`, an executable pre-edit gate that checks context freshness and agent integration health, with optional stale-rule repair and context refresh.
- Added `agentpack migrate` to scan exact or nested repo paths and repair stale AgentPack integrations across existing repos.
- Added tracked native enforcement skeletons/stubs for Cursor, Windsurf, Claude, and Codex under `native-integrations/`, plus a machine-readable status index.
- `agentpack pack` now opportunistically self-heals stale AgentPack rule blocks for the active agent, helping old installs upgrade when they still call `pack`.

### Changed
- Rendered context now includes a loud stale-task warning as a last-resort guardrail when static markdown is read after the task changes.
- Rendered markdown now includes a machine-readable JSON `agentpack:freshness` block with active/fallback context mode, task hashes, snapshot hash, refresh commands, and the guard command.
- Non-MCP installer rules and VS Code tasks now run `agentpack guard --repair-stale --refresh-context` as the concrete fallback before trusting markdown context.
- Changed-file selection caps unrelated dirty files when many files are dirty, keeping safety context without letting unrelated edits dominate the pack.

---

## [0.3.4] — 2026-05-20

### Fixed
- Removed an unused import in `agentpack hook` so the release workflow `ruff check src/ tests/` lint step passes again.

---

## [0.3.3] — 2026-05-20

### Added
- Native Windows support target for PowerShell plus Git for Windows across the npm wrapper, Git hook launchers, and global shell integration.

### Changed
- Repo-local and global Git hooks now delegate to cross-platform Python launchers instead of POSIX-only background shell snippets.
- npm wrapper no longer blocks `win32`, prefers the Windows `py -3` launcher, and uses Windows cache locations when appropriate.
- README and npm docs now describe Windows as a supported platform with scoped expectations.

---

## [0.3.2] — 2026-05-20

### Added
- Public naming-signal analysis for files, exported symbols, tests, and env/config identifiers so offline summaries can capture domain-revealing names as structured ranking hints.
- Focused tests covering naming classification, summary population, and ranking receipts for strong and weak public names.

### Changed
- Aligned GitHub, PyPI, and npm discovery copy, keywords, and README openings around AgentPack's local context engine positioning, including clearer npm wrapper framing.
- Ranking and offline summaries now use public naming signals from files, exported symbols, tests, and env/config identifiers, with small receipts-driven bonuses for domain-revealing names and a light penalty for vague public APIs.
- README architecture and development guidance now document naming-signal flow and public naming advice for ranking.

---

## [0.3.1] — 2026-05-19

### Fixed
- Recommended `pipx install agentpack-cli` for normal installs so new users avoid PEP 668 `externally-managed-environment` errors from system-managed Python.
- Removed the `agentpack global-install` fallback to `pip install --user`; failed `pipx` installs now give OS package-manager guidance for installing `pipx`.
- Updated optional `watch` and `mcp` dependency guidance to use `pipx inject` instead of global `pip install`.

---

## [0.3.0] — 2026-05-17

### Added
- Structured deterministic summary fields for domain, role, entrypoints, definitions, calls, env reads, file reads/writes, external systems, side effects, ranking keywords, and related hints.
- Deterministic role/domain and entrypoint inference for common Python and JavaScript/TypeScript patterns, including FastAPI routes, Django URLs/views, Typer/Click commands, Celery tasks, Express/Next.js routes, and React components.
- Offline side-effect extraction for env/config reads, file I/O, Redis/cache, HTTP clients, Stripe, cloud services, DB/ORM usage, queues, email, and observability tools.
- Focused tests for structured summary extraction, cache schema invalidation/backward compatibility, ranking boosts, and repo-map rendering.
- Expanded npm package documentation with first-project setup, daily workflow, MCP usage, cache controls, troubleshooting, and privacy notes.

### Changed
- Offline summaries now render compact structured text while keeping richer fields in the cache for ranking.
- Ranking now boosts matches against structured summary fields such as entrypoints, role/domain, definitions, env reads, external systems, and side effects, with receipts explaining each boost.
- Repo maps now include domain/role/entrypoint hints so selected areas show what each file is likely responsible for.
- Summary schema version bumped to v2, invalidating stale shallow summaries while old cached records still load safely when requested explicitly.
- npm publishing now verifies registry identity and scoped-package access before `npm publish`, turning the scoped-package `E404` into an actionable release error.
- Main README install/status copy now reflects the published npm wrapper, optional extras boundary, and current alpha version.

---

## [0.2.2] — 2026-05-15

### Added
- `agentpack benchmark --public-table` writes a publishable Markdown benchmark table under `benchmarks/results/`, with per-repo/task recall, token precision, rank@K, pack size, runtime, and miss counts.
- `agentpack benchmark --public-repos` runs the committed public real-repo smoke suite from `benchmarks/public-repos.toml`; the current proof artifact covers eight Pallets commits at 91.7% recall and 55.2% token precision.
- MCP `start_task(task, ...)` tool for the recommended MCP-first workflow: write `.agentpack/task.md`, pack context, and return ranked markdown in one call.
- Before/after agent behavior examples showing cold repo exploration versus MCP-first AgentPack context pulls.
- MCP-first end-to-end test fixture covering `start_task`-style packing, cached context freshness, related files, and file explanations.

### Changed
- Ranking now applies a guarded second-pass recall expansion around strong seeds, boosting two-hop import/reverse-import/test neighbours only when they share task/domain, config, or test-pair signal.
- Dependency graph construction now resolves cached relative imports before rendering related-file output.
- README and benchmark docs now point public benchmark claims to generated real-task tables instead of anecdotal compression stats.

---

## [0.2.1] — 2026-05-15

### Changed
- `agentpack pack --task "<task>"` is no longer supported. Task text now belongs in `.agentpack/task.md`; run `agentpack pack` or `agentpack pack --task auto` to build context from that file.
- Claude hook repacks now write the prompt-derived task into `.agentpack/task.md` and call `agentpack pack --task auto`, keeping hook behavior aligned with the task-file workflow.
- README, slash-command guidance, installer messages, doctor output, and generated agent rules now point users to `.agentpack/task.md` instead of inline `pack --task` strings.

---

## [0.2.0] — 2026-05-15

### Added
- Public benchmark evidence notes under `benchmarks/`, with source-checkout fixture coverage and quality gates for recall/token precision.
- More source-checkout benchmark fixtures: Django REST-style pagination/serializer, Go service readiness/deploy, and Rails-style mailer/job flows.
- `agentpack tune` suggests concrete tuning actions from recent precision metrics and benchmark misses, with optional `.agentpack/tuning.md` output.
- `agentpack benchmark --results-template` creates a publishable benchmark result note under `benchmarks/results/`.
- Selection accuracy now records support-context precision, separating edited-file hits from useful paired tests or adjacent support files.
- `agentpack doctor` and the npm publish workflow now warn clearly when `NPM_TOKEN`/`NODE_AUTH_TOKEN` is missing.
- Monorepo workspace detection for npm/pnpm/Cargo/go.work layouts, with workspace-aware ranking boosts.
- `agentpack pack --workspace <path>` writes a filtered per-workspace context under `.agentpack/workspaces/<workspace>/context.md`.
- `agentpack benchmark --prove-targets` checks recall and token precision against configurable quality gates.

### Changed
- Ranking now gives a small recall boost to files that historically changed in the same commits as live changed files.
- Ranking now expands recall around strong seed files through import, reverse-import, and related-test neighbors.
- Monorepo ranking now uses package.json workspace dependency edges to lift shared packages and dependents near active workspaces.
- `agentpack stats` now reports workspace distribution and benchmark proof status when that data exists.
- README now promotes `benchmark --misses` and `explain --file/--omitted/--budget-plan` as the primary miss-debugging loop.
- Packs now tighten summary inclusion when recent summary token precision is near zero.
- Weak filename-only matches are downranked unless backed by symbols, content, git history, dependencies, tests, configs, or live changes.
- `agentpack stats` now warns on session/pack agent mismatch, recommends minimal mode when token precision is low, and lists repeated noisy paths.
- No-live-change packs now suppress summaries entirely when recent summary token precision is near zero, and damp uncorroborated filename-only matches more aggressively.
- `agentpack stats` now treats `auto`/`generic` agent sessions as resolver modes instead of false mismatches, clarifies ignored/binary vs packable file counts, and suggests `explain --file` for noisy paths.
- Co-change recall boosts now require repeated co-change history and skip paths already proven noisy by recent metrics.
- `agentpack scan` now supports `--largest N` and `--ignored-summary` for large-repo ignore diagnosis.
- CI now runs an explicit all-agent integration matrix so hook/config drift fails before release tagging.

---

## [0.1.30] — 2026-05-15

### Added
- Shared agent integration contract for installing and auditing Claude, Cursor, Windsurf, Codex, Antigravity, and Generic.
- `agentpack repair` for idempotently repairing one integration or all integrations.
- `agentpack doctor --agent all` for a full agent integration audit.
- `agentpack status --deep` for CLI, task, active agent, and integration health output.
- Release wheel verification now runs an all-agent `init`/`install` matrix before PyPI publish.
- Budget-aware context compression with `diff` and `skeleton` include modes for high-signal context at lower token cost.
- `agentpack explain --budget-plan` to show selected modes, token costs, and score-per-token value.
- Semantic repo maps, task classification, and per-pack selected-file delta summaries in rendered context.
- Hunk-level diff selection that prefers changed hunks matching task keywords.
- MCP `get_delta_context()` plus hook delta hints for cheap refresh checks.
- Richer offline summaries with role, side effects, public API, error paths, and test hints.
- History-based noise learning from selection accuracy metrics.
- Compression quality metrics with mode counts, mode tokens, and compression ratio.

### Changed
- `agentpack install` now uses the shared integration contract and is documented as the single-agent refresh command.
- Changed files are no longer always emitted as full source; large dirty files without task-specific signal are compressed to diffs, skeletons, or summaries.
- The selector now downgrades high-value files to skeleton or summary before dropping them when the token budget is tight.

---

## [0.1.29] — 2026-05-15

### Fixed
- `agentpack install --agent generic` and dry-run `global-install --agent generic` now succeed as explicit no-ops, matching `agentpack init --agent generic` and the documented agent list.

---

## [0.1.28] — 2026-05-15

### Fixed
- Codex setup now writes `.codex/hooks.json` with AgentPack `SessionStart` and `UserPromptSubmit` lifecycle hooks, so Codex Settings > Hooks shows installed AgentPack hooks after refresh.

---

## [0.1.27] — 2026-05-15

### Changed
- `agentpack init` now installs the detected agent integration during first-time setup, so one command creates config/session/task files plus the relevant Claude, Cursor, Windsurf, Codex, or Antigravity repo files.
- README setup docs now describe `agentpack init` as the primary project setup command and `agentpack install` as an idempotent repair/reconfigure command.

### Fixed
- `agentpack init --agent codex` now creates `AGENTS.md` and git auto-repack hooks instead of only writing `.agentpack/` state.
- `agentpack init --agent cursor`, `--agent windsurf`, and `--agent antigravity` now install their rules, VS Code task, and git auto-repack hooks.
- `agentpack init --agent claude` now installs `CLAUDE.md`, local Claude hooks, and the AgentPack MCP config.

---

## [0.1.26] — 2026-05-13

### Changed
- Claude hook context now treats a clearly different coding prompt as a task switch, updates `.agentpack/task.md`, and repacks even when repo files have not changed.
- Task-switch detection is configurable via `[hooks] task_switch_detection` and `task_switch_min_terms` in `.agentpack/config.toml`.
- Cursor, Windsurf, Codex, Antigravity, and generated context instructions now include an explicit task-switch protocol: write `.agentpack/task.md`, repack, then read the fresh context before editing.

### Fixed
- `agentpack status` and `agentpack stats` now report stale context when `.agentpack/task.md` differs from the packed task, not only when source files changed.

---

## [0.1.25] — 2026-05-13

### Added
- `agentpack explain --why-noisy` reports broad task terms, noisy selection signals, and concrete wording advice.
- `agentpack doctor` now includes a release hygiene check for generated local artifacts that should not be staged.
- Pack metadata now stores structured selected-file details so stats does not depend on parsing rendered markdown.

### Changed
- `agentpack init` now patches the repo `.gitignore` idempotently with AgentPack generated artifacts, keeping `.agentpack/config.toml` trackable while ignoring local context, cache, snapshot, session, task, metrics, and generated Antigravity skill files.
- Pack diagnostics now adapt to strong changed-file signal and avoid noisy filename/summary warnings when live edits are already ranked near the top.
- `agentpack stats` now presents pack quality guidance as calmer advice and shows both configured and last resolved agent when they differ.
- Generated adapter output paths are skipped consistently across scan, diff, status, stats, summarize, pack planning, explain, MCP, and benchmark flows.

### Fixed
- Manual `agentpack pack` refreshes active session timestamps and counters, preventing stale session warnings immediately after a successful pack.
- Antigravity-generated AgentPack skill output no longer pollutes future packs as a changed input.
- `agentpack doctor` now reads the latest context path from pack metadata instead of reporting stale `context.claude.md` age.

---

## [0.1.24] — 2026-05-13

### Added
- Context packs now render freshness metadata, including generated time, git branch/SHA, task source, changed-file source, snapshot hash, and dirty-file count.
- Benchmark cases can declare `task_type`, and benchmark summaries now report precision/recall/F1 grouped by task type.
- Miss diagnostics now include the changed-file basis used when expected files were missed.

### Changed
- Broad generic task terms are downweighted so words like "fix", "release", "task", and "implementation" no longer dominate concrete domain/file terms.
- Broad tasks tighten weak summary inclusion by raising the summary floor and reducing summary caps.

### Fixed
- MCP `get_context` now marks context stale when `.agentpack/task.md` differs from the packed task or git HEAD changed after the pack.

---

## [0.1.23] — 2026-05-12

### Added
- npm wrapper package scaffold under `npm/`, publishing `@vishal2612200/agentpack` as a Node launcher for the Python `agentpack-cli`.
- npm CI checks for launcher behavior, package contents, and version sync with the Python package.
- npm publish workflow for tagged releases, using `NPM_TOKEN` and provenance metadata.

---

## [0.1.22] — 2026-05-12

### Added
- `agentpack benchmark --misses` now reports expected files that were not selected, including status, rank, score, and scoring reasons.
- Sample fixture benchmark output can be paired with `--misses` for repeatable smoke checks across bundled FastAPI, Next.js, and mixed Python/TypeScript fixtures.
- README now documents AgentPack as an open-source library with clearer expectations, limitations, eval workflow, development commands, and contribution targets.

### Changed
- Ranking now expands Kundali/astrology/chart compatibility terms to catch product-domain files whose names differ from task text.
- Ranking now boosts matching implementation roles such as services, controllers, schemas, handlers, repositories, and clients.
- Full-stack tasks now get a cross-layer relatedness boost so matching backend implementation files can surface when UI pages, routes, or controllers match the same domain.
- Default scoring config includes `implementation_role` and `cross_layer_related` weights.

### Fixed
- Low-recall benchmark runs are easier to diagnose: misses now distinguish ignored files, summary-floor exclusions, budget cuts, missing files, and low-score selections.
- Added Kundali-style regression coverage for backend astrology services, schemas, and Python handlers so similar full-stack recall gaps are less likely to regress.

---

## [0.1.21] — 2026-05-12

### Added
- Pack diagnostics: `agentpack pack` now warns when a pack looks noisy, such as broad filename matching, no changed files, mostly-summary output, or many weak summaries excluded.
- Token-weighted selection accuracy: metrics now store selected token counts and `agentpack stats` reports token precision overall and by inclusion mode (`full`, `symbols`, `summary`).
- Mode-aware summary caps: `minimal` and `balanced` modes now cap unchanged summaries by default to keep packs focused and predictable.
- `agentpack doctor` warns when running from a source checkout but the `agentpack` binary imports an installed package instead of local `src/`.

### Changed
- Keyword ranking now weights literal task terms higher than variants and concept expansions, reducing broad synonym noise while keeping useful concept matches.
- Keyword matching now uses whole identifier/path tokens instead of substring matching, so broad fragments no longer match unrelated names.
- `agentpack stats` now compares packed context against raw full contents for the top included files instead of a misleading arbitrary "manual 20 files" estimate.
- `agentpack stats` reads the last pack metadata path for the top-included table, so it reflects the actual latest pack.

### Fixed
- Weak unchanged summaries no longer fill the token budget just because they had any positive score.
- Selection accuracy metrics now distinguish file-level precision from token-level precision, making summary noise visible instead of over-penalizing useful full/symbol context.

---

## [0.1.20] — 2026-05-11

### Added
- `git.staged_files()` — reads git index only (`--cached`), strongest live signal.
- `git.infer_task_with_source()` — returns `(task, source_label)` with 9-level priority chain: `task.md` → `branch+staged` → `staged` → `branch+unstaged` → `branch+commit` → `branch` → `unstaged` → `commits` → `recently_modified` → `fallback`.
- Source label logged on auto-inference: `Auto task (branch+staged): feat add-rate-limiting: payments`.

### Fixed
- `--task auto` previously used recently-modified files (noisy git log history) as primary signal. Now uses staged files first — "what you're about to commit" is the strongest same-session signal.
- Hook `_resolve_task`: slash commands (`/caveman`, `/agentpack`) and non-coding prompts no longer pollute pack task keywords. Fall back to `"auto"` → git priority chain instead.
- Hook display task was read from stale `pack_metadata.json` — showed previous session's task. Now uses live `infer_task_with_source()` so displayed task reflects current branch/staged state.

---

## [0.1.19] — 2026-05-10

### Fixed
- Hook hint showed stale last-commit message as task. Now reads `task.md` first (if user has written a real task), falls back to pack_metadata task. Prompt text used only for repack keyword — not displayed.
- `excluded_paths` showed irrelevant low-score files (LICENSE, __init__.py). Now only surfaces files excluded due to budget exhaustion — files that scored well but didn't fit. Actual blind-spot signal.
- `SessionStart` hook was raw shell (`rm -f ... && agentpack pack`). Now delegates to `agentpack hook --event SessionStart` — consistent with UserPromptSubmit, versioned, testable.

### Added
- `selected_hints` in `metrics.jsonl`: top-8 files with their first scoring reason (modified / keyword match / dependency / etc). Hook hint now shows `src/auth.py — modified` instead of bare path.
- `agentpack hook --event SessionStart` handler: clears `.mcp_reminded` and `.context_injected` sentinels so first prompt gets fresh context.
- `agentpack doctor` detects stale hooks: warns when old inline-Python injection hooks or old md5-based MCP reminder hooks are present, with upgrade command.
- Background repack passes `--since HEAD~1` when repo has changed — focuses changed-file scoring on recent diff rather than full git history.
- `_resolve_task()` in hook: merges `task.md` + prompt — `task.md` wins if user has written a real task, otherwise prompt text drives repack keywords.

### Changed
- `installers/claude.py` SessionStart template simplified to `agentpack hook --event SessionStart`. Stale-hook cleanup extended to remove old shell-based session hooks.

---

## [0.1.18] — 2026-05-10

### Added
- `agentpack hook --event UserPromptSubmit` CLI subcommand — replaces the fragile inline Python one-liner in `settings.json`. Hook logic is now versioned, tested, and updatable without touching settings files.
- **Option B hint** (MCP mode): hook emits `~50–150 token` message with last task + top-5 files list instead of "MCP ready" string. Gives Claude routing signal without injecting any file content.
- **Capped fallback** (no MCP): hook emits top-8 files with hard 3000-char cap and nudge to install MCP. Prevents silent no-op for users without MCP configured.
- 14 new tests covering MCP detection, metrics loading, repack trigger, cap enforcement, and output format.

### Changed
- `installers/claude.py` hook template simplified to `agentpack hook --event UserPromptSubmit` (one line). Stale-hook cleanup updated to remove all old inline Python hooks.
- Global `~/.claude/settings.json` and project `.claude/settings.json` updated to use new hook command.

---

## [0.1.17] — 2026-05-10

### Fixed
- `UserPromptSubmit` hook used `md5(entire snapshot.json bytes)` to detect repo changes — `created_at` field updated on every pack caused false "repo changed" signal every prompt. Now reads `root_hash` field (content-addressed, stable when files unchanged).
- `installers/claude.py` hook template had same `md5` bug — `agentpack init` on new repos would install the broken hook. Fixed and added stale-hook cleanup: re-running `agentpack init` now removes old `md5`-based hooks.

### Changed
- `UserPromptSubmit` hook reads `prompt` from stdin (Claude Code passes it as JSON) and passes first 200 chars as `--task` to background repack. Pack keyword scoring now matches the current conversation instead of always inferring from git log.

### Added
- `excluded_files` and `excluded_paths` (top 10 score-too-low) recorded in `metrics.jsonl` after each pack.
- `get_stats` MCP tool surfaces `excluded_files: N (score too low)` and a `### Below-threshold files` section — agents can see what was ranked out of context.

---

## [0.1.15] — 2026-05-09

### Fixed
- `install --agent codex --global` no longer installs per-repo git hooks (matches cursor/windsurf behavior).
- `install --agent codex` hint message now includes `--agent codex` flag.

### Changed
- Agent instructions (AGENTS.md, CLAUDE.md, .cursorrules, .windsurfrules, .cursor/rules/agentpack.mdc) strengthened: agents now write `task.md` at task start so pack targets the right files. Removed stale `agentpack session refresh` references (command removed in v0.1.12).

### Added
- Knowledge/architecture doc surfacing: DECISIONS.md, ADR-*.md, ARCHITECTURE.md, and .md files under `docs/adr/`, `docs/decisions/`, `docs/rfcs/` always score higher (weight: 30) so agents see design rationale and known tradeoffs.
- Test file pairing: test files whose source scores above the median are now boosted even when the source isn't in the changed set — agents see relevant tests alongside relevant source.
- Git churn scoring: files in the top 10% by commit frequency get a churn bonus (weight: 15), surfacing historically risky/hot files. Uses a single `git log` call — no per-file subprocess overhead.

---

## [0.1.14] — 2026-05-09

### Added
- Selection accuracy metrics in `metrics.jsonl`: after each pack, recall/precision/F1 are computed by comparing the previous pack's selected files against files actually changed since then.
- `agentpack stats` surfaces avg recall, precision, F1 over the last 10 runs.

---

## [0.1.13] — 2026-05-08

### Fixed
- Watch refresh loop on Linux: watchdog uses inotify which fires on file reads (IN_ACCESS), not just writes. Replaced `on_any_event` with explicit `on_created`/`on_modified`/`on_deleted`/`on_moved` handlers — read-only events no longer trigger refresh.
- Ignore `*.tsbuildinfo` files (TypeScript incremental build artifacts written on every compile).

---

## [0.1.12] — 2026-05-08

### Fixed
- Watch refresh loop for VSCode users: `.vscode/` was not in `_IGNORE_DIRS` — VSCode writes workspace state every few seconds, triggering endless refreshes. Added `.vscode/`, `.idea/`, `.fleet/` to ignore list.
- Watch refresh loop for Antigravity users: adapter writes `.agent/skills/agentpack/SKILL.md` outside `.agentpack/` — now tracked via `_WRITTEN_PATHS` set populated from `run_refresh()` return value.

### Changed
- Removed `agentpack session` subcommands (`start`, `stop`, `status`, `refresh`) — `agentpack init` already bootstraps the session. Shared logic (`run_refresh`, `_file_hash`, `_now_iso`, `_atomic_write`) moved to `commands/_shared.py`.
- README updated: session commands removed from all examples; primary flow is `init` → edit `task.md` → `watch`.

---

## [0.1.11] — 2026-05-08

### Fixed
- Watch refresh loop: ignore all `.agentpack/` generated files (metrics.jsonl, pack_metadata.json, cache/, snapshots/, .context_injected) — previously only 3 specific paths were blocked.

### Changed
- CI: lint now hard-fails (removed `|| true` from ruff).
- CI: coverage gate added — `core/` + `analysis/` must reach 80% (currently 84%).
- Release process: tags must originate from `release/*` branch; CHANGELOG entry required before publish.

### Added
- `CHANGELOG.md` — all versions back to 0.1.0.
- `.github/PULL_REQUEST_TEMPLATE.md` — checklist: tests, coverage, lint, CHANGELOG, version bump.

---

## [0.1.10] — 2026-05-08

### Fixed
- Watch refresh loop: ignore all `.agentpack/` generated files (metrics.jsonl, pack_metadata.json, cache/, snapshots/, .context_injected) — previously only 3 specific paths were blocked, causing every refresh to trigger another refresh.

---

## [0.1.9] — 2026-04-28

### Changed
- Removed LLM cost path.
- Parallel summarise.
- MCP server improvements.

---

## [0.1.8] — 2026-04-28

### Fixed
- Stale context injection after session resume.
- MCP server stability.

### Changed
- README cleanup.

---

## [0.1.7] — 2026-04-28

### Added
- Security hardening.
- Config explainability output.
- Symbol scoring improvements.

### Fixed
- Watch reliability.

---

## [0.1.6] — 2026-04-28

### Changed
- Version bump.

---

## [0.1.5] — 2026-04-28

### Fixed
- `__version__` in `__init__.py` was stuck at 0.1.0.

---

## [0.1.4] — 2026-04-28

### Fixed
- Trusted publisher mismatch in CI.
- Added tomli to dev dependencies.

---

## [0.1.3] — 2026-04-28

### Fixed
- Python 3.10 support: `tomllib` not in stdlib until 3.11, added `tomli` fallback.

---

## [0.1.2] — 2026-04-28

### Fixed
- Root causes of git hook timeouts.

---

## [0.1.1] — 2026-04-28

### Changed
- Version bump.

---

## [0.1.0] — 2026-04-28

### Added
- Initial release.
- PyPI publish workflow.
