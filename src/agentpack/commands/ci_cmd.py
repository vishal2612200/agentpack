from __future__ import annotations

import json
from typing import Any

import typer

from agentpack.commands._shared import console, _root

ci_app = typer.Typer(help="Generate CI automation for AgentPack workflows.")


def register(app: typer.Typer) -> None:
    app.add_typer(ci_app, name="ci")


@ci_app.command("init")
def init_ci(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing workflow."),
    architecture: bool = typer.Option(False, "--architecture", help="Write the deterministic architecture PR check workflow."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Write a GitHub Actions workflow for AgentPack checks."""
    root = _root()
    filename = "agentpack-architecture.yml" if architecture else "agentpack.yml"
    path = root / ".github" / "workflows" / filename
    payload: dict[str, Any] = {"path": str(path.relative_to(root)), "written": False, "overwritten": False}
    if path.exists() and not force:
        payload["reason"] = "workflow exists; pass --force to overwrite"
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
            return
        console.print(f"[yellow]Workflow already exists:[/] {payload['path']}")
        console.print("Run [bold]agentpack ci init --force[/] to overwrite it.")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    payload["overwritten"] = path.exists()
    path.write_text(_architecture_workflow_yaml() if architecture else _workflow_yaml(), encoding="utf-8")
    payload["written"] = True
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    console.print(f"[green]✓[/] Wrote {payload['path']}")


def _workflow_yaml() -> str:
    return """name: AgentPack

on:
  pull_request:
  push:
    branches: [main]

jobs:
  dev-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: python -m pip install -e ".[dev]"
      - run: python -m agentpack.cli dev-check

  loop-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install -e ".[dev]"
      - run: python -m agentpack.cli loop-smoke --json

  release-gate:
    runs-on: ubuntu-latest
    if: github.event_name == 'push'
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: python -m pip install -e ".[dev]"
      - run: python -m agentpack.cli release-check --profile ci
"""


def _architecture_workflow_yaml() -> str:
    """Run deterministic architecture checks without model credentials or source publishing."""
    return """name: AgentPack architecture check

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read

jobs:
  architecture:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      checks: write
      pull-requests: write
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1
        with:
          python-version: "3.11"
      - run: python -m pip install ".[tree-sitter]"
      - name: Build deterministic architecture artifacts
        id: architecture
        shell: bash
        run: |
          set +e
          mkdir -p .agentpack/raw .agentpack/artifacts
          python -m agentpack.cli architecture diff --base "${{ github.event.pull_request.base.sha }}" --head "${{ github.event.pull_request.head.sha }}" --json > .agentpack/raw/architecture-diff.json
          python -m agentpack.cli architecture check --base "${{ github.event.pull_request.base.sha }}" --head "${{ github.event.pull_request.head.sha }}" --json > .agentpack/raw/architecture-check.json
          status=$?
          python -m agentpack.cli architecture artifacts --diff .agentpack/raw/architecture-diff.json --check .agentpack/raw/architecture-check.json --output-dir .agentpack/artifacts
          echo "status=$status" >> "$GITHUB_OUTPUT"
          exit 0
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
        with:
          name: architecture-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
          path: .agentpack/artifacts/
          if-no-files-found: error
      - name: Publish check run and sticky summary
        if: github.event.pull_request.head.repo.full_name == github.repository
        uses: actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b
        with:
          script: |
            const fs = require('fs');
            const summary = fs.readFileSync('.agentpack/artifacts/architecture-diff.md', 'utf8');
            const conclusion = '${{ steps.architecture.outputs.status }}' === '0' ? 'success' : 'failure';
            await github.rest.checks.create({
              owner: context.repo.owner, repo: context.repo.repo,
              name: 'AgentPack architecture', head_sha: context.payload.pull_request.head.sha,
              status: 'completed', conclusion,
              output: {title: 'AgentPack architecture', summary}
            });
            const marker = '<!-- agentpack-architecture-summary -->';
            const comments = await github.paginate(github.rest.issues.listComments, {
              owner: context.repo.owner, repo: context.repo.repo, issue_number: context.issue.number
            });
            const existing = comments.find(comment => comment.user.type === 'Bot' && comment.body.includes(marker));
            if (existing) {
              await github.rest.issues.updateComment({owner: context.repo.owner, repo: context.repo.repo, comment_id: existing.id, body: summary});
            } else {
              await github.rest.issues.createComment({owner: context.repo.owner, repo: context.repo.repo, issue_number: context.issue.number, body: summary});
            }
      - name: Enforce blocking invariants
        if: steps.architecture.outputs.status != '0'
        run: exit 1
"""
