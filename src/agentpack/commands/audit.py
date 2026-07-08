from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

import typer

from agentpack.commands._shared import _atomic_write, _now_iso, _root, console

_ACTIVE_PROMPT_PATH = Path(".agentpack/audit.prompt.md")
_ACTIVE_REPORT_PATH = Path(".agentpack/audit-report.md")
_ACTIVE_ATLAS_PATH = Path(".agentpack/audit-atlas.json")
_ACTIVE_FINDINGS_PATH = Path(".agentpack/audit-findings.json")
_AUDIT_RUNS_DIR = Path(".agentpack/audits")
_SKIP_DIRS = {
    ".agentpack",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
_INFRA_CONFIG_PATTERNS = (
    ("container", "Dockerfile", "container image build path"),
    ("container", "**/Dockerfile", "container image build path"),
    ("container", "docker-compose.yml", "local or deployment compose topology"),
    ("container", "docker-compose.yaml", "local or deployment compose topology"),
    ("container", "compose.yml", "local or deployment compose topology"),
    ("container", "compose.yaml", "local or deployment compose topology"),
    ("ci-cd", ".github/workflows/*.yml", "GitHub Actions workflow"),
    ("ci-cd", ".github/workflows/*.yaml", "GitHub Actions workflow"),
    ("ci-cd", ".gitlab-ci.yml", "GitLab pipeline"),
    ("ci-cd", ".circleci/config.yml", "CircleCI pipeline"),
    ("cloud", "serverless.yml", "serverless deployment config"),
    ("cloud", "serverless.yaml", "serverless deployment config"),
    ("cloud", "template.yml", "CloudFormation or SAM template"),
    ("cloud", "template.yaml", "CloudFormation or SAM template"),
    ("cloud", "samconfig.toml", "SAM deployment config"),
    ("cloud", "copilot/**/*.yml", "AWS Copilot service config"),
    ("cloud", "copilot/**/*.yaml", "AWS Copilot service config"),
    ("orchestration", "k8s/**/*.yml", "Kubernetes manifest"),
    ("orchestration", "k8s/**/*.yaml", "Kubernetes manifest"),
    ("orchestration", "kubernetes/**/*.yml", "Kubernetes manifest"),
    ("orchestration", "kubernetes/**/*.yaml", "Kubernetes manifest"),
    ("orchestration", "helm/**/Chart.yaml", "Helm chart"),
    ("iac", "infra/**/*.tf", "Terraform infrastructure code"),
    ("iac", "infrastructure/**/*.tf", "Terraform infrastructure code"),
    ("iac", "terraform/**/*.tf", "Terraform infrastructure code"),
    ("iac", "ops/**/*.tf", "Terraform infrastructure code"),
    ("deployment", "vercel.json", "Vercel deployment config"),
    ("deployment", "netlify.toml", "Netlify deployment config"),
    ("deployment", "fly.toml", "Fly.io deployment config"),
    ("deployment", "render.yaml", "Render deployment config"),
    ("runtime", "Procfile", "process runtime declaration"),
    ("env", ".env.example", "documented environment contract"),
    ("env", ".env.sample", "documented environment contract"),
    ("package", "package.json", "JavaScript package/runtime scripts"),
    ("package", "pyproject.toml", "Python package/tooling config"),
    ("package", "go.mod", "Go module config"),
    ("package", "Cargo.toml", "Rust package config"),
)


def register(app: typer.Typer) -> None:
    @app.command("audit")
    def audit(
        scope: str = typer.Argument(..., help="Folder, module, or flow to audit."),
        lens: str = typer.Option(
            "mixed",
            "--lens",
            help="Audit lens: performance|refactor|reliability|testability|infra-config|mixed.",
        ),
        passes: int = typer.Option(4, "--passes", min=1, help="Maximum exploration passes."),
        max_files: int = typer.Option(25, "--max-files", min=1, help="Maximum files to inspect deeply."),
        minutes: int = typer.Option(45, "--minutes", min=1, help="Timebox in minutes."),
    ) -> None:
        """Prepare a loop-based codebase audit atlas scaffold."""
        scope_text = scope.strip()
        if not scope_text:
            console.print("[red]Audit scope is required.[/]")
            raise typer.Exit(2)

        lens_text = lens.strip() or "mixed"
        root = _root()
        infra_config_signals = _discover_infra_config_signals(root, scope_text)
        run_id = _run_id()
        scope_slug = _scope_slug(scope_text)
        run_dir = _AUDIT_RUNS_DIR / scope_slug / run_id
        created_at = _now_iso()
        atlas = _atlas_payload(
            scope=scope_text,
            lens=lens_text,
            passes=passes,
            max_files=max_files,
            minutes=minutes,
            run_id=run_id,
            run_dir=run_dir,
            created_at=created_at,
            infra_config_signals=infra_config_signals,
        )
        findings: list[dict] = []
        runbook = _render_runbook(atlas)
        report = _render_report(atlas)

        artifacts = {
            run_dir / "runbook.md": runbook,
            run_dir / "report.md": report,
            run_dir / "atlas.json": json.dumps(atlas, indent=2) + "\n",
            run_dir / "findings.json": json.dumps(findings, indent=2) + "\n",
            _ACTIVE_PROMPT_PATH: runbook,
            _ACTIVE_REPORT_PATH: report,
            _ACTIVE_ATLAS_PATH: json.dumps(atlas, indent=2) + "\n",
            _ACTIVE_FINDINGS_PATH: json.dumps(findings, indent=2) + "\n",
        }
        for rel_path, content in artifacts.items():
            abs_path = root / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(abs_path, content)

        console.print(f"[green]✓[/] Audit run id: [bold]{run_id}[/]")
        console.print(f"[green]✓[/] Audit run dir: [bold]{run_dir}[/]")
        console.print(f"[green]✓[/] Audit runbook: [bold]{_ACTIVE_PROMPT_PATH}[/]")
        console.print(f"[green]✓[/] Audit report: [bold]{_ACTIVE_REPORT_PATH}[/]")
        console.print(f"[green]✓[/] Audit atlas: [bold]{_ACTIVE_ATLAS_PATH}[/]")
        console.print(f"[green]✓[/] Audit findings: [bold]{_ACTIVE_FINDINGS_PATH}[/]")
        console.print("Use the runbook from your agent host; update atlas before each next frontier.")


def _run_id() -> str:
    stamp = re.sub(r"[^0-9A-Za-z]+", "", _now_iso())[:20]
    return f"{stamp}-{uuid4().hex[:8]}"


def _scope_slug(scope: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "-", scope.strip().lower()).strip("-")
    return slug[:80] or "root"


def _atlas_payload(
    *,
    scope: str,
    lens: str,
    passes: int,
    max_files: int,
    minutes: int,
    run_id: str,
    run_dir: Path,
    created_at: str,
    infra_config_signals: list[dict[str, str]],
) -> dict:
    return {
        "schema_version": 1,
        "created_at": created_at,
        "run_id": run_id,
        "scope": scope,
        "lens": lens,
        "budget": {"passes": passes, "max_files": max_files, "max_minutes": minutes},
        "frontier": [{"area": scope, "reason": f"initial {lens} audit scope"}],
        "explored": {},
        "hypotheses": [],
        "findings": [],
        "rejected": [],
        "loop_log": [],
        "project_usage_signals": {
            "infrastructure_config": infra_config_signals,
        },
        "artifacts": {
            "run_dir": str(run_dir),
            "runbook": str(run_dir / "runbook.md"),
            "report": str(run_dir / "report.md"),
            "atlas": str(run_dir / "atlas.json"),
            "findings": str(run_dir / "findings.json"),
            "active_runbook": str(_ACTIVE_PROMPT_PATH),
            "active_report": str(_ACTIVE_REPORT_PATH),
            "active_atlas": str(_ACTIVE_ATLAS_PATH),
            "active_findings": str(_ACTIVE_FINDINGS_PATH),
        },
    }


def _render_runbook(atlas: dict) -> str:
    budget = atlas["budget"]
    infra_signal_count = len(atlas["project_usage_signals"]["infrastructure_config"])
    return f"""# Codebase Audit Atlas: {atlas["scope"]}

Use the `auditing-codebase-atlas` skill. This is a loop-based audit scaffold, not an implementation task.

## Audit Setup
- Scope: `{atlas["scope"]}`
- Lens: `{atlas["lens"]}`
- Budget: {budget["passes"]} passes, {budget["max_files"]} files, {budget["max_minutes"]} minutes
- Report: `{_ACTIVE_REPORT_PATH}`
- Atlas: `{_ACTIVE_ATLAS_PATH}`
- Findings: `{_ACTIVE_FINDINGS_PATH}`
- Initial infrastructure/config signals: {infra_signal_count}

## Required Loop
1. Build or update the atlas.
2. Pick one highest-value frontier.
3. Form 1-3 hypotheses, not findings.
4. Inspect a bounded code path with direct source evidence.
5. Classify road: `unknown`, `trail`, `county-road`, `highway`, `expressway-candidate`, or `do-not-touch-yet`.
6. Promote findings only after the evidence gate passes.
7. Update atlas before the next frontier.
8. Stop by budget or stop rule; do not pad findings.

## Evidence Gate
A finding must include files/lines, current behavior, call path or user/job/render/test path, impact, smallest safe change, validation, risk/rollback, confidence, and uncertainty.

Performance claims need measurement or validation plan. Static risks must be labeled `static-risk`; do not claim speedup before measuring.

Infrastructure/config findings need the config file path, consumer or runtime path, deploy/user impact, rollback path, and validation command. Start from actual project usage signals; do not invent provider facts from filenames alone.

## Infrastructure / Config Review
- Check project usage signals first: containers, CI/CD, IaC, orchestration, env contracts, deployment manifests, package/runtime scripts, migrations, queues, schedulers, secrets, IAM/permissions, monitoring, and logging.
- Classify config as `finding` only when code path or deploy path proves it matters.
- Label unproven config risks as `hypothesis` or `static-risk`.
- Include config review output in `{_ACTIVE_REPORT_PATH}` for developer review.

## Output Contract

```md
# Codebase Audit Atlas: {atlas["scope"]}

## Executive Summary
- Best first upgrade:
- Strong findings:
- Main unknowns:
- Validation limits:
- Stop reason:

## Coverage Map
| Area | Road class | Confidence | Why it matters | Next action |
|---|---|---|---|---|

## Project Usage Signals
| Category | Path | Audit focus |
|---|---|---|

## Infrastructure / Config Review
| Area | Files | Usage evidence needed | Risk to check | Validation / rollback |
|---|---|---|---|---|

## Findings
### <P1/P2/P3>: <action-oriented title>
- Class: expressway-candidate
- Type: performance / refactor / reliability / testability / infra-config
- Files:
- Evidence:
- Impact:
- Smallest safe change:
- Validation:
- Risk / rollback:
- Confidence:

## Hypotheses, Not Findings
| Area | Hypothesis | Missing evidence | Next pass |
|---|---|---|---|

## Rejected / No Action
| Area | Why rejected |
|---|---|

## Loop Log
| Pass | Frontier | Reason selected | Learned | Atlas update |
|---:|---|---|---|---|

## Next Pass Plan
- Objective:
- Frontier:
- Commands:
- Stop rule:
```
"""


def _render_report(atlas: dict) -> str:
    budget = atlas["budget"]
    infra_rows = _infra_signal_rows(atlas["project_usage_signals"]["infrastructure_config"])
    return f"""# Developer Review Report: {atlas["scope"]}

## Executive Summary
- Best first upgrade:
- Strong findings:
- Main unknowns:
- Validation limits:
- Stop reason:

## Scope & Budget
- Scope: `{atlas["scope"]}`
- Lens: `{atlas["lens"]}`
- Budget: {budget["passes"]} passes, {budget["max_files"]} files, {budget["max_minutes"]} minutes
- Run id: `{atlas["run_id"]}`
- Atlas: `{_ACTIVE_ATLAS_PATH}`
- Findings: `{_ACTIVE_FINDINGS_PATH}`

## Project Usage Signals
Initial scan found these infrastructure/config files. Treat them as audit starting points, not proven findings.

{infra_rows}

## Infrastructure / Config Review
| Area | Files | Usage evidence needed | Risk to check | Validation / rollback |
|---|---|---|---|---|
| CI/CD | | workflow trigger, deploy target, required checks | slow or flaky gates, missing cache, unsafe secrets | rerun workflow, dry-run deploy, revert workflow/config |
| Runtime / deploy | | service entrypoint, process manager, container or host path | env drift, slow startup, resource mismatch | local run, smoke test, deploy preview, rollback config |
| IaC / permissions | | stack owner, consumer service, IAM/resource reference | overbroad permission, missing dependency, drift | plan/diff, policy simulation, rollback stack change |
| Observability | | logs/metrics path, alert owner, incident path | blind spots, noisy alerts, missing correlation IDs | log query, metric check, alert test |

## Coverage Map
| Area | Road class | Confidence | Why it matters | Next action |
|---|---|---|---|---|

## Findings
### <P1/P2/P3>: <action-oriented title>
- Class: expressway-candidate
- Type: performance / refactor / reliability / testability / infra-config
- Files:
- Evidence:
- Impact:
- Smallest safe change:
- Validation:
- Risk / rollback:
- Confidence:

## Hypotheses, Not Findings
| Area | Hypothesis | Missing evidence | Next pass |
|---|---|---|---|

## Rejected / No Action
| Area | Why rejected |
|---|---|

## Loop Log
| Pass | Frontier | Reason selected | Learned | Atlas update |
|---:|---|---|---|---|

## Next Pass Plan
- Objective:
- Frontier:
- Commands:
- Stop rule:
"""


def _infra_signal_rows(signals: list[dict[str, str]]) -> str:
    if not signals:
        return "No common infrastructure/config files were found in the initial bounded scan. Re-check manually if the project uses a custom layout."

    rows = ["| Category | Path | Audit focus |", "|---|---|---|"]
    for signal in signals:
        rows.append(f"| {signal['category']} | `{signal['path']}` | {signal['reason']} |")
    return "\n".join(rows)


def _discover_infra_config_signals(root: Path, scope: str) -> list[dict[str, str]]:
    candidates: list[Path] = [root]
    scope_path = root / scope
    if scope_path.exists() and scope_path.is_dir():
        candidates.append(scope_path)

    signals: list[dict[str, str]] = []
    seen: set[str] = set()
    for base in candidates:
        for category, pattern, reason in _INFRA_CONFIG_PATTERNS:
            for path in base.glob(pattern):
                if len(signals) >= 80:
                    return signals
                if not path.is_file() or _has_skipped_part(path):
                    continue
                rel_path = path.relative_to(root).as_posix()
                if rel_path in seen:
                    continue
                seen.add(rel_path)
                signals.append({"category": category, "path": rel_path, "reason": reason})
    return signals


def _has_skipped_part(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)
