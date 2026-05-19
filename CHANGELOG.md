# Changelog

All notable changes to agentpack-cli are documented here.

Format: `## [version] — YYYY-MM-DD` followed by categorised entries.

---

## [Unreleased]

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
