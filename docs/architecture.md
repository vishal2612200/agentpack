# Architecture

AgentPack is a local context-preparation pipeline. It scans a repository, scores files for a task, renders a budgeted markdown pack, and writes agent-specific artifacts without calling remote APIs.

## Product Direction

AgentPack is moving from "generate a context file" toward a small local control
plane for developer-agent work. The control plane has one job: make the next
safe action obvious from current repo state.

The problem is not only file selection. In real usage, agents fail when task
state is stale, two chat sessions share one context, a completed task is reused,
or an agent spends a large token budget reading context that has not changed.
Those are workflow problems, so the implementation keeps setup, task, context,
thread, token, and integration health in one shared snapshot.

The architecture intentionally keeps hard boundaries:

- `pack` and `route` find likely context, but never replace direct source inspection.
- `next`, `quickstart`, `status`, `guard`, and MCP readiness read the same control-plane snapshot.
- thread-scoped task/context files prevent cross-chat collisions while preserving `--thread global` for legacy workflows.
- token contracts help agents choose `get_delta_context()` or `get_context()` before reaching for full repacks.
- observer and learning data are advisory priors, not proof.

## Core Model: Compress, Cache, Retrieve

AgentPack works by combining three local techniques:

- **Compress**: rank task-relevant files and render them as `full`, `diff`, `symbols`, `skeleton`, or `summary` views so agents start with a small, useful pack instead of a full repo dump.
- **Cache**: reuse snapshots, file hashes, offline summaries, pack metadata, session events, and benchmark metrics so context refreshes are fast and measurable.
- **Retrieve**: write a pack registry with stable block IDs so CLI and MCP clients can fetch selected, omitted, or symbol-level context later without stuffing everything into the first prompt.

Compression happens in layers: file-mode selection, task-scored diff hunks, rendered-budget trimming, and command-output adapters for test logs, diffs, search output, listings, and generic noisy output. See [AgentPack runtime loop](runtime-loop.md#compressor-types) for the user-facing compressor types.

Prompt-cache alignment is automatic in every context mode. Renderers put stable
agent instructions first, then append volatile task, freshness, git, selection,
and file-content sections. This preserves existing `lite`, `balanced`, and
`deep` modes while making repeated refreshes friendlier to
provider prefix caches.

## How it works

```
1. Scan repo  →  apply .agentignore  →  skip generated AgentPack outputs  →  hash files
2. Build offline summaries  →  role, imports, symbols, side effects, public API, errors, test hints
3. Build canonical semantic graph  →  cached Tree-sitter records, dependency-aware materialization, two-pass resolution, source-line evidence
4. Query the canonical graph  →  ranking, repo-map, task-map, ownership, and review impact
5. Detect changed files  →  snapshot diff + git working tree + staged + optional --since ref
6. Classify task  →  bugfix / feature / docs / release / infra / audit / test / ui / refactor
7. Extract weighted task terms  →  literals, variants, concept synonyms, changed-file identifiers
8. Score every file  →  changes, task terms, symbols, content, deps, tests, configs, churn
9. Apply history learning  →  gently downrank files that were repeatedly selected as noise
10. Build semantic repo map  →  compact module/group map reserved inside the token budget
11. Select by value per token  →  full / diff / symbols / skeleton / summary / omit
11. For large diffs  →  score hunks against task keywords and keep the most relevant hunks
12. Redact secrets at materialization  →  before content reaches any renderer or adapter
13. Cache pack registry  →  block IDs for selected, omitted, and symbol context
14. Build execution state  →  task_state.md, git summary, Docker/Compose availability
15. Detect concurrent context  →  thread index overlap warning for same branch/worktree
16. Render context  →  stable prefix first, then freshness, execution state, concurrent context, repo map, delta, receipts, files
17. Enforce rendered budget  →  trim receipts, repo map, delta, runtime detail, conflicts, then selected files
18. Persist state  →  global or thread-scoped context, snapshot, metadata, metrics, thread index
```

---

## Architecture

### Data flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        agentpack pack                               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │              SCAN LAYER                  │
          │                                         │
          │  pathlib.rglob()  ──▶  .agentignore     │
          │       │                 (pathspec)       │
          │       ▼                                  │
          │  FileInfo[]  (path, hash, tokens, lang) │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │       SUMMARY + ANALYSIS LAYER           │
          │                                         │
          │  Summary cache  ── role, imports,       │
          │  (offline)        symbols, side effects, │
          │                   public API, naming     │
          │                   signals, errors        │
          │                                         │
          │  Semantic graph  ── Tree-sitter core    │
          │  (two-pass)       ── definitions/scopes │
          │                 ─  imports/calls/refs    │
          │                 ─  inheritance/tests     │
          │                 ─  comments/docs/config  │
          │                                         │
          │  Evidence index ── stable IDs/hashes    │
          │                 ─  source lines         │
          │                 ─  confidence/provenance│
          │                                         │
          │  Naming signals ── public files/symbols │
          │                  ── env/config/test ids │
          │                  ── generic-name hints  │
          │  Test detection  ── name heuristics     │
          │  Task keywords   ── stopwords + variants│
          │                  ── concept synonyms    │
          │                  ── content enrichment  │
          │  Task class      ── bugfix/docs/release │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │           CHANGE DETECTION               │
          │                                         │
          │  Snapshot diff  (merkle root hash)      │
          │       +                                 │
          │  git diff / git diff --cached           │
          │       +                                 │
          │  git diff <ref> HEAD  (--since flag)    │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │              RANKING                     │
          │                                         │
          │  Score each file (configurable weights) │
          │  +100 modified  +80 filename match      │
          │   +70 symbol    +60 content match       │
          │   +50 dep       +40 rev-dep             │
          │   +35 test      +25 config  +20 recent  │
          │   +20 naming    -6 generic public API   │
          │   -50 large unrelated                   │
          │  History noise penalty from metrics     │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │             REPO MAP                     │
          │                                         │
          │  Compact semantic map grouped by module │
          │  Reserved inside the context budget     │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │         BUDGET SELECTION                 │
          │                                         │
          │  Sort by changed/task/value-per-token   │
          │                                         │
          │  changed + small  ──▶  full content     │
          │  changed + large  ──▶  task-scored diff │
          │  task symbols     ──▶  symbol bodies    │
          │  interface view   ──▶  skeleton         │
          │  low context      ──▶  summary/omit     │
          │  budget fallback  ──▶  downgrade first  │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │      EXECUTION + THREAD STATE             │
          │                                         │
          │  task_state.md  ──▶  status/summary     │
          │  git status     ──▶  branch/ahead/dirty │
          │  docker info    ──▶  read-only runtime  │
          │  thread index   ──▶  overlap warnings   │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │              RENDERING                   │
          │                                         │
          │  Claude adapter      ──▶  context.claude.md │
          │  Cursor adapter      ──▶  context.md        │
          │  Windsurf adapter    ──▶  context.md        │
          │  Codex adapter       ──▶  context.md        │
          │  Antigravity adapter ──▶  .agent/skills/agentpack/SKILL.md │
          │  Generic adapter     ──▶  context.md        │
          │                                         │
          │  Freshness + execution state            │
          │  Concurrent context warning             │
          │  Task class + repo map                  │
          │  Delta since last pack                  │
          │  Context receipts (why each file in/out)│
          │  Largest token consumers                │
          │  Secret redaction (AWS/GH/OpenAI tokens)│
          │  Rendered-token budget trimming         │
          └─────────────────────────────────────────┘
```

### Package layout

```
src/agentpack/
  cli.py                       # Typer CLI entry point (thin — delegates to commands/)

  data/
    agentpack.md               # bundled /agentpack slash command for Claude CLI

  application/
    pack_service.py            # PackPlanner: shared scan→summarize→graph→rank→repo_map→select pipeline
                               # PackService: materializes plan → writes context file
                               # AdapterRegistry: maps agent names to adapter instances
                               # PackRequest / PackResult / PackPlan DTOs

  domain/  (via core/models.py)
    FileInfo, ScanResult       # scan output (packable / ignored / binary)
    Symbol, FileSummary        # summary cache objects (role, side_effects, public_api, errors, tests)
    SelectedFile, Receipt      # selection output with redaction_warnings
    ContextPack                # final artifact with freshness, repo_map, delta_summary, redaction_warnings
    DependencyNode             # typed graph node (path, imports, imported_by, tests)
    DependencyGraph            # typed graph container (nodes dict + dict-like accessors)

  core/
    models.py                  # Pydantic domain models (see above)
    config.py                  # TOML config + ScoringWeights
    ignore.py                  # .agentignore / gitignore-style matching
    scanner.py                 # rglob → ScanResult (packable/ignored/binary split)
    snapshot.py                # JSON snapshots + merkle root hash
    diff.py                    # added / modified / deleted / unchanged diff
    git.py                     # subprocess git + task inference + working tree summary
    merkle.py                  # root hash: sort(path:hash) → sha256
    cache.py                   # summary cache keyed path+hash+provider+version
    context_pack.py            # select_files + metadata persistence: full/diff/symbols/skeleton/summary + hunk scoring + redaction
    execution_state.py         # task_state.md parsing + git-derived status + Docker/Compose read-only checks
    thread_context.py          # thread ids, scoped paths, thread index, same-branch/worktree overlap detection
    token_estimator.py         # tiktoken cl100k_base (approximate)
    token_contract.py          # persisted token budget/selection contract for CLI + MCP routing
    redactor.py                # redact_secrets: fires at content materialization
    bootstrap.py               # is_initialized, bootstrap_if_needed

  analysis/
    dependency_graph.py        # build(): returns typed DependencyGraph over packable files
    python_imports.py          # ast-based import extraction
    js_ts_imports.py           # regex import extraction (ESM + CJS)
    go_imports.py              # Go import / import(...) blocks
    rust_imports.py            # use, mod, extern crate
    java_imports.py            # Java import + Kotlin import
    symbols.py                 # Python AST, JS/TS regex, and lightweight Go symbols
    naming_signals.py          # public-name classification for summaries + ranking boosts
    tests.py                   # source → test file mapping heuristics
    ranking.py                 # keyword extraction, concept synonyms, scoring, naming receipts
    monorepo.py                # workspace detection + workspace ownership helpers
    repo_map.py                # compact semantic repo map reserved inside token budget
    task_classifier.py         # coarse task class for freshness/rendering/scoring context

  summaries/
    offline.py                 # zero-API: AST/regex → imports, symbols, role, side effects, API, naming signals, errors
    base.py                    # cache-or-build orchestration (parallel, ThreadPool+ProcessPool)

  adapters/                    # context rendering only — no installation logic
    base.py                    # abstract BaseAdapter (output_path + render + write)
    claude.py                  # renders context.claude.md via render_claude()
    cursor.py                  # renders context.md via render_generic()
    windsurf.py                # renders context.md
    codex.py                   # renders context.md
    antigravity.py             # renders .agent/skills/agentpack/SKILL.md (SKILL.md frontmatter + body)
    generic.py                 # renders context.md (any LLM)
    detect.py                  # detect_agent(): infers active IDE from env vars + project files

  installers/                  # repo/tool configuration — separate from rendering
    claude.py                  # ClaudeInstaller: CLAUDE.md + .claude/settings.json
    cursor.py                  # CursorInstaller: .cursorrules + .mdc + auto-repack
    windsurf.py                # WindsurfInstaller: .windsurfrules + auto-repack
    codex.py                   # CodexInstaller: AGENTS.md + .codex/hooks.json + git hooks
    antigravity.py             # AntigravityInstaller: GEMINI.md + auto-repack

  integrations/                # system/tool integration (not core domain)
    agents.py                  # shared agent install/check/repair contract for all supported agents
    git_hooks.py               # install/remove .git/hooks post-commit/merge/checkout
    vscode_tasks.py            # install/remove .vscode/tasks.json entries
    global_install.py          # global: git template hooks + shell rc hook

  ../native-integrations/       # tracked native-enforcement skeletons and blocked-status stubs
    status.json                 # machine-readable native host enforcement status
    cursor-extension/           # VS Code-style Cursor guard skeleton
    windsurf-extension/         # VS Code-style Windsurf guard skeleton
    claude-native/              # blocked native stub pending mandatory host API
    codex-native/               # blocked native stub pending mandatory host API

  renderers/
    markdown.py                # renders pre-redacted ContextPack, including freshness/execution/concurrency/map/delta
    compact.py                 # compact protocol format for session context files
    receipts.py                # context receipt formatter

  mcp_server.py                # MCP tools: start_task, pack_context, get_context, explain, related, stats, delta

  control_plane/
    models.py                  # typed setup/task/context/thread/token snapshots
    snapshot.py                # cheap snapshot builder; full repo scan only when requested
    planner.py                 # pure next-action planner used by next/quickstart/status/guard/MCP
    renderer.py                # shared human token/action rendering helpers

  session/
    state.py                   # SessionState dataclass + load/save/create/stop helpers
    __init__.py                # re-exports from state.py

  commands/                    # CLI only — parse args, call services/installers
    pack.py                    # agentpack pack → PackService.run()
    install.py                 # agentpack install / global-install → installers/
    repair.py                  # agentpack repair → shared integration repair
    init.py                    # agentpack init
    quickstart.py              # agentpack quickstart — guided first-run commands
    scan.py                    # agentpack scan
    diff.py                    # agentpack diff
    status.py                  # agentpack status, including --thread scoped metadata
    threads.py                 # agentpack threads — list/archive/prune scoped thread records
    state_cmd.py               # agentpack state — show/set/done execution state files
    stats.py                   # agentpack stats
    summarize.py               # agentpack summarize
    monitor.py                 # agentpack monitor
    explain.py                 # agentpack explain
    doctor.py                  # agentpack doctor
    tune.py                    # agentpack tune — tuning suggestions from metrics + benchmark misses
    hook_cmd.py                # agentpack hook — Claude prompt hook + stale detection
    mcp_cmd.py                 # agentpack mcp — MCP server entrypoint
    watch.py                   # agentpack watch — file watcher with debounce
    claude_cmd.py              # agentpack claude — refresh + launch claude
    benchmark.py               # agentpack benchmark — token efficiency, recall, miss diagnostics, release gate
    release_check.py           # agentpack release-check — version/tests/build/benchmark orchestration
```

### Key architectural properties

- **Redaction at materialization**: secrets are stripped inside `select_files()` before content reaches any renderer or adapter. Every output format gets redacted content automatically — no per-renderer redaction needed.
- **`ScanResult` splits cleanly**: `scan()` returns `ScanResult(packable, ignored, binary)` — downstream code only processes `packable` files, eliminating `if f.ignored or f.binary` guards throughout.
- **`PackPlanner` owns shared planning**: `PackPlanner.plan()` runs scan → summarize → graph → changes → rank → repo map → select and returns a `PackPlan`. Both `pack` and `explain` use the same planner — no duplicated pipeline logic, no drift.
- **`PackService` materializes a plan**: takes a `PackPlan`, computes delta since the previous pack, builds the `ContextPack` artifact, delegates rendering to `AdapterRegistry`, persists snapshot + metadata + metrics.
- **The control plane owns "what now?"**: `control_plane.snapshot` builds a cheap setup/task/context/thread/token snapshot. `next`, `quickstart`, `status`, MCP readiness, and guard compatibility helpers use that shared model, while `guard` still asks for a strict file scan before returning success.
- **Agent sessions are scoped by default**: when `AGENTPACK_THREAD_ID`, `CODEX_THREAD_ID`, `CLAUDE_SESSION_ID`, `CURSOR_SESSION_ID`, `WINDSURF_SESSION_ID`, `ANTIGRAVITY_SESSION_ID`, or `GEMINI_SESSION_ID` is present, commands and MCP tools use isolated state under `.agentpack/threads/<id>/`. `--thread global` opts into the legacy `.agentpack/task.md`, `.agentpack/context.md`, and `.agentpack/pack_metadata.json` flow.
- **Concurrent work is warning-based**: thread mode detects active threads from the last 24 hours on the same branch/worktree and warns when selected or dirty files overlap. It does not lock files; separate worktrees/branches remain the safest workflow.
- **Done tasks are terminal**: `finish` marks task state done and archives scoped sessions; later `guard`, `next`, MCP context reads, and refresh flows refuse to reuse completed context for a new task.
- **Execution state is explicit context**: rendered packs include task status, checklist counts, git branch/SHA/ahead/behind/dirty counts, and Docker/Compose availability. `task_state.md` is optional; absent state is derived from git.
- **Mode selection is value-aware**: changed files can be `full`, `diff`, `symbols`, `skeleton`, or `summary`. Large diffs keep task-relevant hunks first, and tight budgets downgrade files before dropping them.
- **Rendered budget is the real budget**: final token accounting measures the markdown artifact, including tables, freshness, receipts, and overhead. Under pressure, AgentPack trims receipts first, then repo map, delta, runtime/concurrent detail, selected files, and only then freshness detail.
- **Token contracts are persisted**: pack metadata records the rendered estimate, budget usage, selected-file mode counts, largest sections, trimmed modes, and recommended next context strategy. CLI and MCP surfaces use this to prefer delta/context reads over unnecessary full repacks.
- **Repo maps are first-class context**: `analysis/repo_map.py` builds a compact semantic map before file context, and its token cost is reserved before file selection.
- **Metrics feed history learning**: selection accuracy records hit/noise paths, token precision, mode counts, and mode tokens. Later packs gently penalize repeated noisy paths unless they are currently changed.
- **Git history feeds recall**: files that historically changed in the same commits as live changed files receive a small boost, helping related tests, schemas, services, and configs surface without forcing full-content inclusion.
- **Second-pass expansion is guarded**: after first scoring, strong seeds can lift two-hop import, reverse-import, config, and related-test neighbours only when they share task or domain signal.
- **Co-change is guarded by precision history**: one-off co-change neighbors are ignored, and paths repeatedly measured as noise do not get revived by history boosts.
- **Precision guardrails adapt to bad history**: when summary token precision stays near zero, later packs raise the summary score floor, cap summaries more aggressively, and suppress summaries entirely for no-live-change packs. Weak filename-only matches are also damped unless other signals confirm them.
- **`AdapterRegistry` maps agent → adapter**: adding a new agent output format requires one entry in `AdapterRegistry.get()`, not changes to `PackService`.
- **`detect_agent()` runs at invocation time**: `--agent auto` (the default) calls `detect_agent()` fresh on every `pack` run and git hook execution — so context is always written for the active IDE, even when switching between agents or running in CI.
- **`DependencyGraph` is typed**: `dependency_graph.build()` returns `DependencyGraph(nodes: dict[str, DependencyNode])` — no more `dict[str, dict]` with stringly-typed keys like `"imported_by"`. Typos are caught at the model layer.
- **`integrations/` vs `core/`**: git hooks, shell rc patching, and VS Code tasks are infrastructure concerns — they live in `integrations/`, not `core/`. `core/` is pure domain logic.
- **Adapters render; installers configure**: `adapters/` knows how to write a context file for an agent. `installers/` knows how to configure the agent's tool (CLAUDE.md, .cursorrules, settings.json). They are separate concerns and separate classes.
- **Agent integration contract is shared**: `integrations/agents.py` defines install, audit, and repair behavior for Claude, Cursor, Windsurf, Codex, Antigravity, and Generic. `install`, `repair`, `doctor --agent all`, and release verification use the same contract.
- **MCP is the interactive path**: `readiness()` reports the recommended next tool, avoid list, and token hint; `start_task(thread_id=...)` writes ambient scoped or explicit global task state and returns a fresh pack, while `get_context(thread_id=...)` auto-refreshes stale task or repo-snapshot context and `get_delta_context()`, `explain_file()`, and `get_related_files()` let agents pull follow-up context on demand.
- **Native enforcement status is explicit**: `native-integrations/status.json` tracks host skeletons and blockers. Entries stay `advisory`, not `enforced`, until a host exposes mandatory pre-edit/pre-tool hooks that can block failed readiness checks.

---

## The summary cache — the core feature

Run once, reuse forever:

```bash
agentpack summarize
```

Builds an offline summary of every file — no API calls, no network. Each summary captures:
- What the file does and its responsibility
- Exported classes, functions, signatures with extracted bodies
- Import dependencies
- Likely side effects, public API shape, error paths, and test hints

Summaries are stored in `.agentpack/cache/` keyed by file hash. Only changed files are re-summarized on the next pack.

**Team tip:** commit the cache so every developer and CI job gets summaries for free:

```bash
agentpack init --share-cache
git add .agentpack/cache/
git commit -m "chore: add agentpack summary cache"
```

---

## Honest token framing

AgentPack's pack is typically 10,000–40,000 tokens. Comparing that to "raw repo size" (200k–2M tokens) is misleading — nobody dumps the whole repo into Claude.

The real comparison for a piped/API workflow: **what would you manually copy-paste** to give Claude enough context? For a typical bug fix touching 3 files with 10 relevant dependencies, that's ~30,000–80,000 tokens assembled by hand. AgentPack gets you there in one command.

Token counts use tiktoken `cl100k_base` — a close approximation to Claude's actual billing, but not exact.

---
