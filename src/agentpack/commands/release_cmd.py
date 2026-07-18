from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import typer

from agentpack.commands._shared import console, _root
from agentpack.commands.verify_wheel import run_verify_wheel
from agentpack.integrations.platform import cli_module_argv

release_app = typer.Typer(help="Release preparation workflows.")


def register(app: typer.Typer) -> None:
    app.add_typer(release_app, name="release")


@release_app.command("prepare")
def prepare_release(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    notes_path: Path | None = typer.Option(
        None,
        "--notes-path",
        help="Write GitHub release notes to this path. Defaults to dist/github-release-notes-<tag>.md.",
    ),
) -> None:
    """Run release-check, publish benchmark evidence, verify the wheel, and write release notes."""
    root = _root()
    stages: list[dict[str, Any]] = []
    tag = _expected_tag(root)
    stages.append(_run("release-check", cli_module_argv("release-check", "--check-release-branch", "--check-registry", "--tag", tag)))
    if stages[-1]["returncode"] == 0:
        stages.append(_run("benchmark-public-table", cli_module_argv("benchmark", "--release-gate")))
    if stages[-1]["returncode"] == 0:
        wheel_result = run_verify_wheel()
        stages.extend({**stage, "name": f"verify-wheel:{stage['name']}"} for stage in wheel_result["stages"])
    release_notes = ""
    if stages[-1]["returncode"] == 0:
        notes_stage = _write_release_notes(root, tag=tag, notes_path=notes_path, completed_stages=stages)
        stages.append(notes_stage)
        if notes_stage["returncode"] == 0:
            release_notes = notes_stage["detail"]
    passed = all(stage["returncode"] == 0 for stage in stages)
    payload = {"passed": passed, "stages": stages, "root": str(root)}
    if release_notes:
        payload["release_notes"] = release_notes
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for stage in stages:
            marker = "[green]✓[/]" if stage["returncode"] == 0 else "[red]✗[/]"
            console.print(f"{marker} {stage['name']}")
            if stage["name"] == "github-release-notes" and stage["returncode"] == 0:
                console.print(f"  notes: [bold]{stage['detail']}[/]")
        if passed:
            console.print("[bold green]Release preparation complete.[/]")
    if not passed:
        raise typer.Exit(1)


def _run(name: str, command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=_root(), capture_output=True, text=True)
    return {
        "name": name,
        "command": " ".join(command),
        "returncode": result.returncode,
        "detail": ((result.stderr or result.stdout).strip().splitlines() or [""])[-1],
    }


def _write_release_notes(root: Path, *, tag: str, notes_path: Path | None, completed_stages: list[dict[str, Any]]) -> dict[str, Any]:
    target = notes_path or root / "dist" / f"github-release-notes-{tag}.md"
    if not target.is_absolute():
        target = root / target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_release_notes(root, tag=tag, completed_stages=completed_stages), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive filesystem failure path
        return {
            "name": "github-release-notes",
            "command": f"write {target}",
            "returncode": 1,
            "detail": str(exc),
        }
    return {
        "name": "github-release-notes",
        "command": f"write {target}",
        "returncode": 0,
        "detail": str(target),
    }


def _render_release_notes(root: Path, *, tag: str, completed_stages: list[dict[str, Any]]) -> str:
    version = tag[1:] if tag.startswith("v") else tag
    changelog = _changelog_entry(root, version)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    commit = _git_value(root, ["git", "rev-parse", "--short", "HEAD"]) or "unknown"
    full_commit = _git_value(root, ["git", "rev-parse", "HEAD"]) or "unknown"
    branch = _git_value(root, ["git", "branch", "--show-current"]) or "unknown"
    package_json = _load_package_json(root)
    npm_name = str(package_json.get("name", "@vishal2612200/agentpack"))
    npm_version = str(package_json.get("version", version))

    stage_lines = []
    for stage in completed_stages:
        status = "passed" if int(stage.get("returncode", 1)) == 0 else "failed"
        stage_lines.append(f"- `{stage.get('name', 'stage')}`: {status} (`{stage.get('command', '')}`)")

    return "\n".join(
        [
            f"# AgentPack {tag}",
            "",
            "## Release Metadata",
            f"- Generated at: `{generated_at}`",
            f"- Branch: `{branch}`",
            f"- Commit: `{commit}` (`{full_commit}`)",
            f"- Tag: `{tag}`",
            f"- PyPI package: `agentpack-cli=={version}`",
            f"- npm package: `{npm_name}@{npm_version}`",
            "",
            "## Release Evidence",
            *stage_lines,
            "",
            "## What's Changed",
            changelog,
            "",
            "## Publish In GitHub",
            "```bash",
            f"gh release create {tag} --repo vishal2612200/agentpack --title \"AgentPack {tag}\" --notes-file <this-file> --latest",
            "```",
            "",
        ]
    )


def _changelog_entry(root: Path, version: str) -> str:
    changelog_path = root / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    heading = re.compile(rf"^##\s+\[?{re.escape(version)}\]?(?:\s|$).*$", re.MULTILINE)
    match = heading.search(changelog)
    if not match:
        raise ValueError(f"Missing CHANGELOG.md entry for {version}")
    next_heading = re.search(r"^##\s+", changelog[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(changelog)
    entry = changelog[match.end() : end].strip()
    return entry or "- No changelog details provided."


def _git_value(root: Path, command: list[str]) -> str:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _load_package_json(root: Path) -> dict[str, Any]:
    package_path = root / "npm" / "package.json"
    if not package_path.exists():
        return {}
    return json.loads(package_path.read_text(encoding="utf-8"))


def _expected_tag(root) -> str:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]

    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return f"v{pyproject['project']['version']}"
