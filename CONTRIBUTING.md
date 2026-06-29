# Contributing

Thanks for helping improve AgentPack. Keep changes small, evidence-backed, and
easy to review.

## Development Setup

```bash
python -m pip install -e ".[dev]"
agentpack doctor
```

For npm wrapper changes:

```bash
npm install --prefix npm
```

## First Contribution Quickstart

Here's a terminal transcript showing how to make your first contribution:

```bash
# Clone the repository
gh repo clone agentpack/agentpack
cd agentpack

# Install development dependencies
python -m pip install -e ".[dev]"

# Run a focused test to verify setup
python -m pytest tests/test_docs_links.py -q

# Create a branch for your changes
git checkout -b fix-docs-link

# Make your changes (e.g. edit a documentation file)
# Then run the narrow test again to verify
python -m pytest tests/test_docs_links.py -q

# Commit and push your changes
git add .
git commit -m "Fix broken documentation link"
git push -u origin fix-docs-link

# Open a pull request
gh pr create --fill
```

## Finding A First Issue

Start with issues labeled `good first issue`. These should have a narrow scope,
clear files or docs areas, and acceptance criteria that can be verified without
knowing the whole codebase.

Issues labeled `first-timers-only` are reserved for people making their first
open-source contribution. If you have already contributed to OSS, please leave
those for new contributors and pick another `good first issue` or `help wanted`
task.

Useful labels:

| Label | Meaning |
|---|---|
| `good first issue` | Small, well-scoped task for a first contribution |
| `help wanted` | Maintainers want community help or feedback |
| `first-timers-only` | Reserved for someone making their first OSS contribution |
| `docs` / `documentation` | Documentation-only or documentation-heavy work |
| `benchmark` | Benchmark cases, result docs, or evidence tooling |
| `cli` | Command-line behavior, flags, help text, or output contracts |
| `python` | Python package implementation work |
| `testing` | Tests, fixtures, release checks, or validation coverage |

If you are unsure where to start, comment on a `help wanted` issue with what you
want to work on. Maintainers should confirm scope before larger changes.

## Before Opening a PR

Run the narrowest relevant tests first, then broaden when the change touches
shared behavior.

Common checks:

```bash
python -m pytest tests/test_docs_links.py -q
python -m ruff check src tests
python -m mypy
python -m pytest -q -m "not slow"
```

For release-facing changes:

```bash
python -m agentpack.cli release-check --profile docs --json
```

## Contribution Guidelines

- Prefer focused changes over broad rewrites.
- Keep generated `.agentpack/` artifacts out of commits unless a test fixture
  explicitly needs them.
- Add tests for behavior changes and regressions.
- Keep public benchmark claims tied to dated result files.
- Do not claim native hard enforcement unless the host provides a mandatory
  pre-edit or pre-tool blocking API.
- Use `agentpack route --task "<task>" --json` when another tool needs
  machine-readable routing output.
- For benchmark changes, keep claims tied to dated result files and explain what
  the benchmark proves and does not prove.
- For CLI changes, preserve existing flags unless there is a documented
  compatibility reason to change them.
- For type-safety changes, expand `mypy` coverage module by module with focused
  tests.

## Pull Requests

Include:

- Problem
- Solution
- Key files changed
- Validation performed
- Risk and rollback notes

If validation is not run, say so directly.
