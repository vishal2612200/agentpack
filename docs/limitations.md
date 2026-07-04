# Limitations

AgentPack is a ranked context map, not a correctness oracle. This page keeps the product boundary and known limits explicit.

## Project Scope

**AgentPack is:**

- A local context engine for building task-focused packs for AI coding agents.
- A CLI, MCP server, hook runner, and integration layer.
- A summary cache, import graph, ranking engine, semantic repo map, and token-budget selector.
- An eval harness for measuring whether selected files match files you actually changed.

**AgentPack is not:**

- A coding agent.
- A hosted service.
- A semantic code search engine.
- A replacement for normal source inspection on critical changes.
- Proven across a large public benchmark suite yet.

## When it helps

| Workflow | Value |
|---|---|
| Claude API calls without tool use | **High** — pack is the only context the model sees |
| CI: generate pack per PR, attach as artifact | **High** — reviewers get instant focused context |
| Cursor / Windsurf / Codex / Antigravity sessions | **Medium** — context auto-injected on startup, repacked on commit |
| Large repos (>50k tokens) where exploration is slow | **Medium** — summary cache eliminates repeated file reads |
| Claude Code interactive session, small repo | **Low** — Claude reads files on demand already |

---

## How it compares to alternatives

**The honest version.**

### repomix / gitingest / code2prompt

These are repo dumpers. They pack a repo (or subset) into a file and hand it to you. They do that job well.

What they don't do: decide what's relevant to *your task*. You specify the scope — files, globs, directories — and they package your decision. If you want "only the files that matter for fixing this auth bug", you have to figure that out yourself. On a 200-file repo, that's 80% of the work.

AgentPack does that selection automatically. You give it a task string; it uses task classification, git diff, import graph traversal, semantic summaries, and keyword scoring to rank every file, then cuts to fit your token budget. You don't touch globs.

The other difference: all three pack uniformly (full content or nothing). AgentPack is selective by inclusion mode — changed files can be full source, relevant diff hunks, symbol bodies, interface skeletons, or summaries; unrelated files get dropped. A repomix dump of a 50k-token repo stays 50k tokens. An agentpack of the same repo for a specific task is typically 8k–20k.

**Use repomix/gitingest if:** you want to dump an entire small repo into a chat UI for a one-shot question. Zero setup, great for "explain this codebase."

**Use agentpack if:** you're running repeated tasks on a large repo and want automatic, task-driven file selection every time.

### aider

Different category. Aider is an interactive pair programmer — it reads, edits, and commits files directly. Its repo-map is genuinely smart. If you want an AI coding assistant making actual edits, aider is excellent.

AgentPack is not a coding assistant. It's a context preparation tool. The output is a markdown file you can pass as context.

**Use aider if:** you want interactive, supervised AI coding sessions in a terminal.

**Use agentpack if:** you're working on large repos and want automatic, task-driven file selection — CI, scripts, batch workflows, or interactive sessions.

### Claude Code / Cursor / Windsurf / Codex (agentic IDEs)

These tools have native file access via tool calls. Claude reads exactly the files it needs, on demand, per turn. Pre-packing context adds overhead without much benefit on small-to-medium repos.

AgentPack's value here is different: `agentpack init --agent <x>` configures your agent to read or inject a ranked context pack and auto-repack when the repo changes. On large repos where tool-call exploration piles up across turns, this front-loads the cost once instead of paying per-turn.

| Native agent search | AgentPack |
|---|---|
| Discovers files during the session | Pre-ranks files before the session |
| Uses model/tool calls to explore | Uses deterministic local repo analysis |
| May repeat orientation across turns | Reuses cached summaries and pack metadata |
| Hard to measure selection misses | Reports omitted files, misses, recall, and token precision |
| Best for interactive exploration | Best for CI, batch tasks, large repos, and repeated workflows |

### Where AgentPack Wins

| Scenario | repomix | gitingest | code2prompt | aider | agentpack |
|---|---|---|---|---|---|
| API call without tool use | ✓ dump | ✗ | ✓ | ✗ | ✓ task-filtered |
| CI per-PR context | ✓ dump | ✗ | ✓ | ✗ | ✓ task-filtered |
| Auto task inference from git | ✗ | ✗ | ✗ | partial | ✓ |
| Relevance ranking by task | ✗ | ✗ | ✗ | ✗ | ✓ |
| Import graph traversal | ✗ | ✗ | ✗ | ✓ | ✓ |
| Monorepo workspace hints | ✗ | ✗ | ✗ | manual | ✓ |
| Token budget enforcement | manual | manual | manual | ✓ | ✓ |
| Cursor / Windsurf / Codex / Antigravity install | ✗ | ✗ | ✗ | ✗ | ✓ |
| Zero API calls | ✓ | ✓ | ✓ | ✗ | ✓ |
| Interactive coding sessions | ✗ | ✗ | ✗ | ✓✓ | ✗ |
| Any LLM | ✓ | ✓ | ✓ | ✓ | partial* |

_*`--agent generic` outputs standard markdown. Claude adapter has richer instructions._

### What AgentPack Does Not Do Well

- **Interactive sessions on small repos**: if your whole repo is <20k tokens, a simple repo dump may be enough
- **One-shot public repo questions**: gitingest's "replace hub with ingest" is faster for quick read-only exploration
- **Native IDE flows that already find files cheaply**: AgentPack helps most when exploration cost repeats or needs to be measured
- **Guaranteed source-of-truth selection**: AgentPack ranks likely files; it can miss task-critical files. Use `agentpack benchmark --misses`, `agentpack explain`, and normal `rg`/agent file reads for correctness.
- **Deep semantic understanding**: keyword/concept scoring, imports, symbols, and path roles help, but they are not an LLM-level code understanding system
- **Public proof without real cases**: bundled fixtures are smoke tests. Strong claims need historical tasks from real repos and published results.

---

## Known limitations

- **Windows**: supported with PowerShell plus Git for Windows. AgentPack installs cross-platform Git hook launchers and a PowerShell profile hook for opted-in repos. `cmd.exe` is not a first-class workflow yet.
- **Monorepos**: workspace-aware ranking supports npm/pnpm, Cargo, and `go.work` layouts. `--workspace` creates filtered per-workspace outputs. Package dependency hints cover npm/pnpm `package.json`, common Cargo path dependencies, and common Go module `require`/local `replace` edges. More advanced Cargo workspace dependency inheritance and generated Go module layouts may still need direct validation.
- **Multi-thread coordination**: thread mode warns about overlapping active threads but does not enforce locks, merge ownership, or branch policy. Use one branch/worktree per active agent when edits may collide.
- **Public benchmark evidence**: `benchmarks/public-repos.toml` is a curated public-commit suite. The current public evidence table is `benchmarks/results/2026-06-25-public.md` (`65.7%` recall, `51.4%` token precision over `107` scored public cases). Older dated tables are historical only. Treat every table as scoped evidence for those cases, not a leaderboard or broad success claim. The synthetic sample-fixture suite is useful for regression smoke, but it is not currently a release quality gate.
- **Symbol extraction**: Python (AST, full) and JavaScript/TypeScript (regex, arrow functions + classes) are strongest. Go has lightweight function, method, struct, and interface extraction. Rust has lightweight regex extraction of free functions (including `async`/`const fn`), `impl`/`trait` methods (qualified by the owning type, so `impl Trait for Type` attributes methods to the concrete type), and `struct`/`enum`/`trait` as class-like constructs; being regex-based it does not resolve macro-generated items and treats a multi-line signature or a body-less declaration that precedes another braced block as best-effort for its end line. Java and Kotlin have import graph traversal but no symbol extraction yet — they fall back to file-level summaries.
- **Selection recall**: ranking is heuristic. It can miss files when task language differs from code language, when repos have unusual architecture, or when important files are only connected at runtime.
- **Pack registry retrieval**: retrieval expands content from the latest local pack registry. If a file changed after packing, AgentPack refuses full retrieval unless explicitly allowed. Symbol blocks exist only when the latest pack captured symbols. It is not a long-term content archive.
- **Learning output**: `agentpack learn` is deterministic and evidence-based. It can identify misses, concepts, repo lessons, and bounded future ranking hints, but it is not a human-quality tutor or reviewer.
- **Wrapper mode**: `agentpack wrap` launches local agent binaries after packing context. It does not proxy LLM API traffic or rewrite provider requests.
- **Output compression**: `agentpack compress-output` is intentionally narrow. It preserves obvious failures, paths, diffs, and repeated lines, but raw logs remain the source of truth for hard debugging.
- **Secret redaction**: covers AWS keys, GitHub tokens, OpenAI/Anthropic keys, JWTs, and private key blocks. Not a substitute for a dedicated secrets scanner on sensitive repos.
- **Token estimates**: uses tiktoken `cl100k_base` — approximate, not exact for Claude's billing.
- **Large repos (>5k files)**: global auto-bootstrap is skipped for repos over 5,000 files to avoid hangs. Run `agentpack init` explicitly in large codebases.
- **Native hard enforcement**: tracked skeletons exist under `native-integrations/`, but all hosts remain `advisory` until their native APIs can guarantee mandatory pre-edit/pre-tool execution and block failed readiness checks.

---

## Roadmap

Post-0.3 release focus: broader real-repo proof, npm publish reliability, and continued ranking precision.

- Expand the public real-repo suite beyond the current curated Pallets smoke set.
- Keep recall gains measured with `--prove-targets`; target 65%+ recall, 51%+ token precision, and task packs within their configured budget for the next benchmark release.
- Extend second-pass expansion with framework route/service/schema pairs once benchmark misses prove the pattern.
- Make npm publishing reliable by adding `NPM_TOKEN` and rerunning the npm release workflow.
- Keep integration contracts stable across Claude, Cursor, Windsurf, Codex, Antigravity, and Generic before any 1.0 work.

---

## Principles

- **Local-first**: `init`, `scan`, `diff`, `pack`, `stats`, `summarize` make zero API calls — ever. No optional LLM paths, no per-file costs.
- **Non-destructive**: never overwrites user files; config patching only touches agentpack-managed blocks
- **Agent-neutral**: architecture is generic; Claude Code is the primary target (deepest integration); Cursor, Windsurf, Codex, and Antigravity are supported but less battle-tested
- **No daemons**: file watching is opt-in via `agentpack watch`; git hooks run in the background and are opt-in via `install`
- **Measurable**: `benchmark`, `stats`, receipts, and `--misses` are first-class because compression without recall is not enough
- **Honest**: packed token count reflects real content, and raw-repo savings are presented separately from practical usefulness

---
