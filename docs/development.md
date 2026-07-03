# Development

Local development, release checks, naming guidance, and contribution notes for AgentPack maintainers.

## Development

## Public Naming And Ranking

AgentPack works better when public surfaces carry domain context. Prefer domain-revealing names for files, exported functions/classes, CLI commands, tests, and config/env identifiers.

- `verify_otp` is better than `handle`
- `StripeWebhookHandler` is better than `Processor`
- `session_token_expiry_test` is better than `test_flow`

This is guidance, not a lint rule. Local variable names are out of scope for AgentPack ranking.

Clone and run locally:

```bash
git clone https://github.com/vishal2612200/agentpack.git
cd agentpack
python -m pip install -e ".[dev,mcp]" build
pytest
```

Useful checks before opening a PR:

```bash
agentpack dev-check
agentpack release-check --skip-benchmark --skip-build
pytest tests/test_agent_integration_matrix.py -q
agentpack benchmark --release-gate --no-public-table
agentpack doctor
```

`agentpack dev-check` runs docs-link tests, `ruff`, scoped `mypy`, non-slow
pytest, and npm wrapper tests. The mypy scope starts narrow and is expanded as
typed modules are cleaned up.

The CLI is the package-user source of truth for repeated workflows:

```bash
agentpack work "describe the task"
agentpack next --fix-all-safe
agentpack diagnose-selection
agentpack ignore suggest
agentpack benchmark capture --since HEAD~1 --task "describe the task"
agentpack finish --since main
agentpack verify-wheel
agentpack release prepare
agentpack release prepare --notes-path /tmp/agentpack-release-notes.md
agentpack ci init
```

The Makefile remains maintainer convenience:

```bash
make help
make release-fast
make release
make verify-wheel
THREAD=codex-local make context-thread
```

The underlying release command wraps the public release gate:

```bash
agentpack release-check
agentpack release-check --profile auto
agentpack release-check --skip-benchmark --json
```

`release-check` verifies version/changelog sync, runs tests, builds into a
temporary directory, and runs `agentpack benchmark --release-gate` for full
release gates. The default `--profile auto` keeps that full path for code
changes, but uses the docs/plugin profile for docs, agent-rule, plugin, and
native-integration-only diffs. It does not mutate tracked files.

`make verify-wheel` is the packaged-CLI smoke: it builds the project, installs
the latest wheel into a temporary venv, then runs
`agentpack benchmark --release-gate --no-public-table` from that installed
command.

`agentpack release prepare` adds the higher-level release pipeline on top of
the gate: it runs `release-check`, writes the public benchmark table, verifies
the built wheel, then emits GitHub-ready release notes at
`dist/github-release-notes-<tag>.md` unless `--notes-path` is provided.

For npm publish, configure GitHub secret `NPM_TOKEN`. The token must publish to the npm scope in `npm/package.json` (`@vishal2612200` today): use a token from that npm user, or create an npm org with that scope and grant the token owner publish access. If `npm publish` reaches the registry and then fails with `E404 Not Found - PUT ... @scope/package`, the token is authenticated but does not own or have write access to that scope. `agentpack doctor` warns locally when neither `NPM_TOKEN` nor `NODE_AUTH_TOKEN` is present, and the npm publish workflow fails early when the secret or scope access is wrong.

Good contribution areas:

- More real-world benchmark fixtures and public repo eval cases
- Better Windows ergonomics beyond the supported PowerShell + Git for Windows path
- Better symbol extraction for Go, Rust, Java, and Kotlin
- More precise import/dependency resolution for framework-heavy repos
- Ranking regressions with `expected_files` cases that reproduce misses
- npm wrapper improvements that preserve the Python CLI as the source of truth

Please include tests for ranking changes. A good ranking PR usually adds one focused unit test and one scenario in `tests/test_ranking_evals.py`.

---

## Optional dependencies

```bash
pipx inject agentpack-cli "agentpack-cli[mcp]"  # expose agentpack as MCP server tools
pipx inject agentpack-cli "agentpack-cli[all]"  # default deps + mcp
```

---
