from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from agentpack.commands._shared import console, _root


@dataclass
class StageResult:
    name: str
    command: str
    status: str
    duration_s: float
    returncode: int = 0
    detail: str = ""
    output_excerpt: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "status": self.status,
            "duration_s": round(self.duration_s, 3),
            "returncode": self.returncode,
            "detail": self.detail,
            "output_excerpt": self.output_excerpt,
        }


def register(app: typer.Typer) -> None:
    @app.command("release-check")
    def release_check(
        skip_benchmark: bool = typer.Option(False, "--skip-benchmark", help="Skip public benchmark release gate."),
        skip_build: bool = typer.Option(False, "--skip-build", help="Skip wheel/sdist build."),
        profile: str = typer.Option(
            "auto",
            "--profile",
            help="Release profile: auto, full, ci, fast, or docs. auto uses docs profile only for docs/plugin-only diffs.",
        ),
        tag: str | None = typer.Option(None, "--tag", help="Verify a release tag such as v1.2.3 matches package versions."),
        check_release_branch: bool = typer.Option(False, "--check-release-branch", help="Require HEAD to be present on an origin/release/* branch."),
        check_registry: bool = typer.Option(False, "--check-registry", help="Fail if this version already exists on PyPI or npm."),
        check_pypi_registry: bool = typer.Option(False, "--check-pypi-registry", help="Fail if this version already exists on PyPI."),
        check_npm_registry: bool = typer.Option(False, "--check-npm-registry", help="Fail if this version already exists on npm."),
        require_release_evidence: bool = typer.Option(False, "--require-release-evidence", help="Require current deterministic and E2E benchmark evidence."),
        allow_release_evidence_bypass: bool = typer.Option(
            False,
            "--allow-release-evidence-bypass",
            help="Explicitly bypass release evidence and benchmark checks for this tagged release.",
        ),
        release_evidence_bypass_reason: str | None = typer.Option(
            None,
            "--release-evidence-bypass-reason",
            help="Required audit reason when --allow-release-evidence-bypass is used.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    ) -> None:
        """Run release readiness checks without mutating tracked files."""
        root = _root()
        stages: list[StageResult] = []
        if allow_release_evidence_bypass:
            skip_benchmark = True
        release_profile = _resolve_release_profile(root, profile, skip_build=skip_build, skip_benchmark=skip_benchmark)
        if release_profile == "docs":
            skip_build = True
            skip_benchmark = True
        elif release_profile in {"ci", "fast"}:
            skip_build = True
            skip_benchmark = True
        expected_version = _version_from_tag(tag) if tag else None
        stages.append(_check_changelog(root, expected_version=expected_version))
        stages.append(_run_stage(root, "version-sync", ["node", "npm/test/version-sync.test.js"]))
        if tag:
            stages.append(_check_tag_version(root, tag))
        if allow_release_evidence_bypass:
            stages.append(_check_release_evidence_bypass(tag=tag, reason=release_evidence_bypass_reason))
        elif require_release_evidence:
            stages.append(_check_release_evidence(root, tag=tag))
        if check_release_branch:
            stages.append(_check_release_branch(root))
        if check_registry or check_pypi_registry:
            stages.append(_check_pypi_version_available(root))
        if check_registry or check_npm_registry:
            stages.append(_check_npm_version_available(root))
        stages.append(_check_pytest_plugin_dependencies(root))
        stages.append(_run_stage(root, "ruff", [sys.executable, "-m", "ruff", "check", "src", "tests"]))
        stages.append(_run_stage(root, "mypy", [sys.executable, "-m", "mypy"]))
        pytest_args = _pytest_args_for_profile(root, release_profile)
        with tempfile.TemporaryDirectory(prefix="agentpack-coverage-") as coverage_dir:
            pytest_env = {"COVERAGE_FILE": str(Path(coverage_dir) / ".coverage")} if release_profile != "docs" else None
            stages.append(_run_stage(root, "pytest", [sys.executable, "-m", "pytest", *pytest_args], env=pytest_env))
        if release_profile != "docs":
            stages.append(_run_stage(root, "npm-launcher-tests", ["node", "npm/test/launcher.test.js"]))
        if not skip_build:
            with tempfile.TemporaryDirectory(prefix="agentpack-build-") as out_dir:
                stages.append(_run_stage(root, "build", [sys.executable, "-m", "build", "--outdir", out_dir]))
        if not skip_benchmark:
            stages.append(_run_stage(root, "benchmark-release-gate", [sys.executable, "-m", "agentpack.cli", "benchmark", "--release-gate", "--no-public-table"]))

        failed = [stage for stage in stages if stage.status != "passed"]
        if json_output:
            typer.echo(
                json.dumps(
                    {"passed": not failed, "profile": release_profile, "stages": [stage.as_dict() for stage in stages]},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            for stage in stages:
                marker = "[green]✓[/]" if stage.status == "passed" else "[red]✗[/]"
                console.print(f"{marker} {stage.name}: {stage.status} ({stage.duration_s:.2f}s)")
                if stage.detail and stage.status != "passed":
                    console.print(f"  {stage.detail}")
                if stage.output_excerpt and stage.status != "passed":
                    console.print(stage.output_excerpt)
                if stage.status != "passed":
                    console.print(f"  rerun: [bold]{stage.command}[/]")
        if failed:
            raise typer.Exit(1)


def _check_release_evidence_bypass(*, tag: str | None, reason: str | None) -> StageResult:
    started = time.perf_counter()
    errors: list[str] = []
    clean_reason = reason.strip() if reason else ""
    if not tag:
        errors.append("release evidence bypass requires an explicit --tag")
    if not clean_reason:
        errors.append("release evidence bypass requires --release-evidence-bypass-reason")
    detail = "; ".join(errors) if errors else f"EXPLICIT BYPASS: release evidence and benchmark checks skipped; reason: {clean_reason}"
    return StageResult(
        name="release-evidence",
        command="explicit release evidence bypass",
        status="passed" if not errors else "failed",
        duration_s=time.perf_counter() - started,
        returncode=0 if not errors else 1,
        detail=detail,
    )


def _check_release_evidence(root: Path, *, tag: str | None) -> StageResult:
    started = time.perf_counter()
    version = _version_from_tag(tag) if tag else _project_name_version(root)[1]
    results_root = root / "benchmarks" / "results"
    public_reports = sorted(results_root.glob("*-public.md"))
    e2e_reports = sorted(results_root.glob(f"*-v{version}-e2e-ab.md")) if version else []
    errors: list[str] = []
    if not tag:
        errors.append("release evidence requires an explicit --tag so ancestry is checked against immutable release target")

    matching_public = [path for path in public_reports if _report_version(path) == version]
    if len(matching_public) != 1:
        errors.append(f"expected one tracked deterministic report for version {version}; found {len(matching_public)}")
    else:
        public = matching_public[0]
        _validate_report_commit(root, public, tag, errors)
        if not _is_tracked(root, public):
            errors.append(f"deterministic report is not tracked: {public.relative_to(root)}")

    if len(e2e_reports) != 1:
        errors.append(f"expected one tracked E2E report named *-v{version}-e2e-ab.md; found {len(e2e_reports)}")
    else:
        e2e = e2e_reports[0]
        if not _is_tracked(root, e2e):
            errors.append(f"E2E report is not tracked: {e2e.relative_to(root)}")
        _validate_report_commit(root, e2e, tag, errors)
        _validate_e2e_report(e2e, version, errors)

    detail = "; ".join(errors)
    return StageResult(
        name="release-evidence",
        command="validate tracked benchmark reports and release ancestry",
        status="passed" if not errors else "failed",
        duration_s=time.perf_counter() - started,
        returncode=0 if not errors else 1,
        detail=detail,
    )


def _report_version(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r"^- agentpack version(?:/commit)?:\s*([^\s]+)", text, flags=re.MULTILINE)
    return match.group(1).strip().lstrip("v") if match else ""


def _report_metadata(path: Path, key: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(rf"^- {re.escape(key)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _validate_report_commit(root: Path, report: Path, tag: str | None, errors: list[str]) -> None:
    commit = _report_metadata(report, "tested commit")
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit):
        errors.append(f"{report.name}: missing valid tested commit")
        return
    target = tag or "HEAD"
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, target],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        errors.append(f"{report.name}: tested commit {commit} is not an ancestor of {target}")


def _is_tracked(root: Path, path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path.relative_to(root))],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _validate_e2e_report(path: Path, version: str, errors: list[str]) -> None:
    if _report_version(path) != version:
        errors.append(f"{path.name}: version does not match {version}")
    required = {
        "baseline": "no-context",
        "treatment": "agentpack",
    }
    for key, expected in required.items():
        if _report_metadata(path, key) != expected:
            errors.append(f"{path.name}: {key} must be {expected}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        errors.append(f"{path.name}: unreadable report")
        return
    manifest_match = re.search(r"<!--\s*agentpack-e2e-manifest:\s*(\{.*?\})\s*-->", text)
    if not manifest_match:
        errors.append(f"{path.name}: missing machine-readable E2E manifest")
        manifest: dict[str, Any] = {}
    else:
        try:
            parsed = json.loads(manifest_match.group(1))
            manifest = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            manifest = {}
            errors.append(f"{path.name}: malformed machine-readable E2E manifest")
    if manifest.get("schema_version") != 1:
        errors.append(f"{path.name}: unsupported or missing E2E manifest schema")
    if manifest.get("baseline") != "no-context" or manifest.get("treatment") != "agentpack":
        errors.append(f"{path.name}: manifest baseline/treatment mismatch")
    run_ids: list[Any] = list(manifest["run_ids"]) if isinstance(manifest.get("run_ids"), list) else []
    commits: list[Any] = list(manifest["tested_commits"]) if isinstance(manifest.get("tested_commits"), list) else []
    if len(run_ids) != 1 or not str(run_ids[0]).startswith("e2e-"):
        errors.append(f"{path.name}: evidence must come from one immutable E2E run")
    if len(commits) != 1 or not re.fullmatch(r"[0-9a-fA-F]{7,64}", str(commits[0] or "")):
        errors.append(f"{path.name}: manifest must contain one valid tested commit")
    categories = [item.strip() for item in _report_metadata(path, "risk categories").split(",") if item.strip()]
    cases = _parse_int_metadata(path, "cases")
    trials = _parse_int_metadata(path, "trials per strategy")
    total_runs = _parse_int_metadata(path, "total runs")
    if len(categories) < 5:
        errors.append(f"{path.name}: requires at least five risk categories")
    if cases < 5:
        errors.append(f"{path.name}: requires at least five cases")
    if trials < 3:
        errors.append(f"{path.name}: requires at least three trials per strategy")
    if total_runs < 30:
        errors.append(f"{path.name}: requires at least 30 total runs")
    if manifest.get("total_runs") != total_runs:
        errors.append(f"{path.name}: manifest total_runs does not match report")
    if manifest.get("trials_per_strategy") != trials:
        errors.append(f"{path.name}: manifest trial count does not match report")
    if sorted(str(item) for item in manifest.get("risk_categories", [])) != sorted(categories):
        errors.append(f"{path.name}: manifest risk categories do not match report")
    trial_rows = re.findall(r"^\| `[^`]+` \| [^|]+ \| (\d+) \| (\d+) \|$", text, flags=re.MULTILINE)
    if len(trial_rows) < 5 or any(int(base) < 3 or int(treatment) < 3 for base, treatment in trial_rows):
        errors.append(f"{path.name}: trial matrix requires five cases with three baseline and treatment trials")
    for metric in ("task success", "expected file touched", "tokens", "token cost", "duration"):
        metric_row = re.search(rf"^\| {re.escape(metric)} \| ([^|]+) \| ([^|]+) \|", text, flags=re.MULTILINE)
        if not metric_row or not any(char.isdigit() for char in metric_row.group(1) + metric_row.group(2)):
            errors.append(f"{path.name}: missing metric {metric}")
    if "agent/model configuration: unspecified" in text:
        errors.append(f"{path.name}: missing agent/model configuration")


def _parse_int_metadata(path: Path, key: str) -> int:
    value = _report_metadata(path, key)
    try:
        return int(value)
    except ValueError:
        return 0


_DOCS_TESTS = [
    "tests/test_docs_links.py",
    "tests/test_codex_plugin.py",
    "tests/test_native_integrations.py",
]


def _resolve_release_profile(root: Path, profile: str, *, skip_build: bool, skip_benchmark: bool) -> str:
    normalized = profile.strip().lower()
    if normalized not in {"auto", "full", "ci", "fast", "docs"}:
        raise typer.BadParameter("profile must be one of: auto, full, ci, fast, docs")
    if normalized != "auto":
        return normalized
    if skip_build and skip_benchmark:
        return "full"
    changed = _changed_files(root)
    if changed and all(_is_docs_or_plugin_path(path) for path in changed):
        return "docs"
    return "full"


def _pytest_args_for_profile(root: Path, profile: str) -> list[str]:
    if profile == "docs":
        existing = [path for path in _DOCS_TESTS if (root / path).exists()]
        return [*existing, "-q"] if existing else ["tests/test_docs_links.py", "-q"]
    if profile == "ci":
        return ["tests/", "-q", "-m", "not slow"]
    return ["tests/", "-q", "--cov", "--cov-report=term-missing", "-m", "not slow"]


def _changed_files(root: Path) -> list[str]:
    ci_changed = _github_event_changed_files(root)
    if ci_changed:
        return ci_changed
    tracked = _git_lines(root, ["git", "diff", "--name-only", "HEAD", "--"])
    untracked = _git_lines(root, ["git", "ls-files", "--others", "--exclude-standard"])
    return sorted(set(tracked + untracked))


def _github_event_changed_files(root: Path) -> list[str]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if not event_path:
        return []
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    base = ""
    head = ""
    if event_name == "push":
        base = str(payload.get("before") or "")
        head = str(payload.get("after") or os.environ.get("GITHUB_SHA") or "")
    elif event_name == "pull_request":
        pull_request = payload.get("pull_request")
        if isinstance(pull_request, dict):
            base_ref = pull_request.get("base")
            head_ref = pull_request.get("head")
            if isinstance(base_ref, dict) and isinstance(head_ref, dict):
                base = str(base_ref.get("sha") or "")
                head = str(head_ref.get("sha") or "")
    if not base or not head or set(base) == {"0"}:
        return []
    return _git_lines(root, ["git", "diff", "--name-only", base, head, "--"])


def _git_lines(root: Path, command: list[str]) -> list[str]:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _is_docs_or_plugin_path(path: str) -> bool:
    if path in {
        "README.md",
        "CHANGELOG.md",
        "SECURITY.md",
        "mkdocs.yml",
        "llms.txt",
        "llms-full.txt",
        ".cursorrules",
        ".github/copilot-instructions.md",
    }:
        return True
    prefixes = (
        "docs/",
        "benchmarks/results/",
        ".codex-plugin/",
        "skills/",
        "agent-rules/",
        ".cursor/",
        ".windsurf/",
        ".clinerules/",
        ".kiro/",
        ".opencode/",
        "native-integrations/",
    )
    if path.startswith(prefixes):
        return True
    return path in set(_DOCS_TESTS)


def _load_pyproject(root: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]

    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def _project_name_version(root: Path) -> tuple[str, str]:
    project = _load_pyproject(root).get("project", {})
    return str(project.get("name", "")), str(project.get("version", ""))


def _version_from_tag(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def _check_changelog(root: Path, *, expected_version: str | None = None) -> StageResult:
    started = time.perf_counter()
    _name, current = _project_name_version(root)
    current = expected_version or current
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8") if (root / "CHANGELOG.md").exists() else ""
    ok = bool(current and (f"## [{current}]" in changelog or f"## {current}" in changelog))
    return StageResult(
        name="changelog",
        command="grep CHANGELOG.md",
        status="passed" if ok else "failed",
        duration_s=time.perf_counter() - started,
        returncode=0 if ok else 1,
        detail="" if ok else f"Missing CHANGELOG.md entry for {current or 'unknown version'}",
    )


def _check_tag_version(root: Path, tag: str) -> StageResult:
    started = time.perf_counter()
    tag_version = _version_from_tag(tag)
    _project_name, project_version = _project_name_version(root)
    init_text = (root / "src" / "agentpack" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    init_version = init_match.group(1) if init_match else ""
    package_json = json.loads((root / "npm" / "package.json").read_text(encoding="utf-8"))
    npm_version = str(package_json.get("version", ""))
    ok = bool(tag_version and tag_version == project_version == init_version == npm_version)
    detail = (
        f"tag={tag_version} pyproject={project_version} __init__={init_version} npm={npm_version}"
        if not ok
        else f"tag version {tag_version}"
    )
    return StageResult(
        name="tag-version",
        command=f"check release tag {tag}",
        status="passed" if ok else "failed",
        duration_s=time.perf_counter() - started,
        returncode=0 if ok else 1,
        detail=detail,
    )


def _check_release_branch(root: Path) -> StageResult:
    started = time.perf_counter()
    command = ["git", "branch", "-r", "--contains", "HEAD"]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    branches = result.stdout.splitlines()
    release_branches = [branch.strip() for branch in branches if re.search(r"origin/release/", branch)]
    ok = result.returncode == 0 and bool(release_branches)
    detail = f"release branch: {release_branches[0]}" if ok else "HEAD is not contained in an origin/release/* branch"
    return StageResult(
        name="release-branch",
        command=" ".join(command),
        status="passed" if ok else "failed",
        duration_s=time.perf_counter() - started,
        returncode=0 if ok else 1,
        detail=detail,
        output_excerpt=_output_excerpt(result.stdout + result.stderr) if not ok else "",
    )


def _check_pytest_plugin_dependencies(root: Path) -> StageResult:
    started = time.perf_counter()
    pyproject = _load_pyproject(root)
    pytest_options = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
    optional_dependencies = pyproject.get("project", {}).get("optional-dependencies", {})
    dev_dependencies = [str(item).lower() for item in optional_dependencies.get("dev", [])]
    uses_asyncio_plugin_config = any(str(key).startswith("asyncio_") for key in pytest_options)
    has_pytest_asyncio = any(item.split(";", 1)[0].strip().startswith("pytest-asyncio") for item in dev_dependencies)
    ok = not uses_asyncio_plugin_config or has_pytest_asyncio
    detail = "" if ok else "pyproject.toml configures pytest-asyncio options but dev dependencies do not include pytest-asyncio"
    return StageResult(
        name="pytest-plugin-deps",
        command="check pyproject.toml pytest plugin dependencies",
        status="passed" if ok else "failed",
        duration_s=time.perf_counter() - started,
        returncode=0 if ok else 1,
        detail=detail,
    )


def _check_pypi_version_available(root: Path) -> StageResult:
    started = time.perf_counter()
    name, version = _project_name_version(root)
    url = f"https://pypi.org/pypi/{urllib.parse.quote(name, safe='')}/{urllib.parse.quote(version, safe='')}/json"
    exists, detail = _registry_url_exists(url)
    ok = not exists
    return StageResult(
        name="pypi-version-available",
        command=f"GET {url}",
        status="passed" if ok else "failed",
        duration_s=time.perf_counter() - started,
        returncode=0 if ok else 1,
        detail=f"{name}=={version} is available on PyPI" if ok else detail or f"{name}=={version} already exists on PyPI",
    )


def _check_npm_version_available(root: Path) -> StageResult:
    started = time.perf_counter()
    package_json = json.loads((root / "npm" / "package.json").read_text(encoding="utf-8"))
    name = str(package_json["name"])
    version = str(package_json["version"])
    package_path = urllib.parse.quote(name, safe="")
    version_path = urllib.parse.quote(version, safe="")
    url = f"https://registry.npmjs.org/{package_path}/{version_path}"
    exists, detail = _registry_url_exists(url)
    ok = not exists
    return StageResult(
        name="npm-version-available",
        command=f"GET {url}",
        status="passed" if ok else "failed",
        duration_s=time.perf_counter() - started,
        returncode=0 if ok else 1,
        detail=f"{name}@{version} is available on npm" if ok else detail or f"{name}@{version} already exists on npm",
    )


def _registry_url_exists(url: str) -> tuple[bool, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=8):
            return True, "version already exists in package registry"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, ""
        return True, f"registry lookup failed with HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return True, f"registry lookup failed: {exc.reason}"


def _run_stage(root: Path, name: str, command: list[str], *, env: dict[str, str] | None = None) -> StageResult:
    started = time.perf_counter()
    try:
        run_env = {**os.environ, **env} if env else None
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, env=run_env)
    except OSError as exc:
        return StageResult(name=name, command=" ".join(command), status="failed", duration_s=time.perf_counter() - started, returncode=1, detail=str(exc))
    combined_output = (result.stdout + "\n" + result.stderr).strip()
    output = combined_output.splitlines()
    return StageResult(
        name=name,
        command=" ".join(command),
        status="passed" if result.returncode == 0 else "failed",
        duration_s=time.perf_counter() - started,
        returncode=result.returncode,
        detail=output[-1] if output else "",
        output_excerpt=_output_excerpt(combined_output) if result.returncode != 0 else "",
    )


def _output_excerpt(output: str, *, max_lines: int = 80) -> str:
    lines = output.splitlines()
    if len(lines) <= max_lines:
        excerpt = lines
    else:
        excerpt = ["... output truncated to final failing lines ...", *lines[-max_lines:]]
    return "\n".join(f"  {line}" for line in excerpt)
