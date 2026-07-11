from __future__ import annotations

import json
import hashlib
import math
import random
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import typer
from rich.table import Table
from rich import box

from agentpack.commands._shared import console, _root
from agentpack.commands.pack import _resolve_task
from agentpack.application.pack_service import PackRequest, PackService
from agentpack.core import git
from agentpack.core.config import load_config
from agentpack.core.modes import MODE_HELP, invalid_mode_message, is_requested_mode, normalize_mode
from agentpack.core.token_estimator import estimate_tokens

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class BenchmarkCase:
    task: str
    mode: str = "balanced"
    expected_files: list[str] = field(default_factory=list)
    expected_skills: list[str] = field(default_factory=list)
    avoid_skills: list[str] = field(default_factory=list)
    task_type: str = "general"
    workspace: str | None = None
    budget: int = 0
    action_owner_files: list[str] = field(default_factory=list)
    required_support_files: list[str] = field(default_factory=list)
    incidental_changed_files: list[str] = field(default_factory=list)
    optional_context_files: list[str] = field(default_factory=list)
    repository: str = ""

    def __post_init__(self) -> None:
        self.mode = normalize_mode(self.mode)
        _validate_ownership_partition(self)


@dataclass
class CaseResult:
    case: BenchmarkCase
    packed_tokens: int
    raw_tokens: int           # all files (incl. ignored)
    after_ignore_tokens: int  # packable files only — honest baseline
    saving_pct: float         # vs raw
    saving_pct_honest: float  # vs after_ignore
    selected_paths: list[str]
    selected_tokens: dict[str, int]   # path → token count for noise calc
    changed_covered: int
    changed_total: int
    total_s: float
    phase_times: dict[str, float]
    rank_at_k: int | None = None   # min rank to see all expected_files; None if no expected
    candidate_recall_at_20: float | None = None
    candidate_recall_at_50: float | None = None
    candidate_recall_at_100: float | None = None
    candidate_precision_at_3: float | None = None
    candidate_precision_at_5: float | None = None
    low_budget_extra_file_waste: int | None = None
    precision_delta_if_drop_last_summary: float | None = None
    expected_token_coverage: float | None = None
    selected_family_tokens: dict[str, int] = field(default_factory=dict)
    selected_family_waste_tokens: dict[str, int] = field(default_factory=dict)
    reason_family_precision: dict[str, dict[str, float]] = field(default_factory=dict)
    failure_type_counts: dict[str, int] = field(default_factory=dict)
    noise_pct: float | None = None  # tokens on non-expected / packed; None if no expected
    random_precision: float | None = None
    random_recall: float | None = None
    random_f1: float | None = None
    selected_skills: list[str] = field(default_factory=list)
    skill_recall_at_3: float | None = None
    skill_precision_at_3: float | None = None
    skill_mrr: float | None = None
    skill_noise_rate: float | None = None
    skill_token_cost: int = 0
    missed_expected: list[dict[str, Any]] = field(default_factory=list)
    selected_modes: dict[str, str] = field(default_factory=dict)
    top_candidates: list[dict[str, Any]] = field(default_factory=list)
    selection_diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class FixtureCase:
    fixture: str
    root: Path
    case: BenchmarkCase


@dataclass
class PublicRepoCase:
    commit: str
    task: str
    expected_files: list[str]
    mode: str = "balanced"
    task_type: str = "general"
    workspace: str | None = None
    budget: int = 0
    action_owner_files: list[str] = field(default_factory=list)
    required_support_files: list[str] = field(default_factory=list)
    incidental_changed_files: list[str] = field(default_factory=list)
    optional_context_files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.mode = normalize_mode(self.mode)
        _validate_ownership_partition(self)


def _validate_ownership_partition(case: BenchmarkCase | PublicRepoCase) -> None:
    """Validate optional benchmark-only role labels without inferring runtime ownership."""

    role_lists = (
        case.action_owner_files,
        case.required_support_files,
        case.incidental_changed_files,
    )
    if not any(role_lists) and not case.optional_context_files:
        return
    role_sets = [set(paths) for paths in role_lists]
    duplicates = (role_sets[0] & role_sets[1]) | (role_sets[0] & role_sets[2]) | (role_sets[1] & role_sets[2])
    expected = set(case.expected_files)
    partition = set().union(*role_sets)
    if duplicates or partition != expected:
        raise ValueError(
            "action_owner_files, required_support_files, and incidental_changed_files "
            "must be disjoint and partition expected_files"
        )
    optional = set(case.optional_context_files)
    if optional & expected:
        raise ValueError("optional_context_files must be disjoint from expected_files")


@dataclass
class PublicRepoSpec:
    name: str
    url: str
    ref: str = "main"
    cases: list[PublicRepoCase] = field(default_factory=list)
    sample_history: int = 0
    task_type: str = "general"
    mode: str = "balanced"
    budget: int = 0
    include_globs: list[str] = field(default_factory=list)
    exclude_globs: list[str] = field(default_factory=list)
    max_changed_files: int = 8

    def __post_init__(self) -> None:
        self.mode = normalize_mode(self.mode)


@dataclass(frozen=True)
class ReleaseGateConfig:
    """Committed quality floor for the frozen release benchmark suite."""

    min_recall: float | None = None
    min_token_precision: float | None = None
    min_scored_cases: int | None = None


@dataclass
class E2ECase:
    name: str
    repo: Path
    task: str
    test_command: str
    setup_command: str = ""
    protected_paths: list[str] = field(default_factory=list)
    expected_edit_paths: list[str] = field(default_factory=list)


@dataclass
class E2EResult:
    schema_version: int
    case: str
    strategy: str
    trial: int
    passed: bool
    duration_s: float
    input_tokens: int
    agent_output_tokens: int
    estimated_input_cost_usd: float
    estimated_output_cost_usd: float
    estimated_total_cost_usd: float
    agent_returncode: int
    test_returncode: int
    timed_out: bool
    agent_tool_calls: int
    time_to_first_expected_file_s: float | None
    changed_files: list[str]
    source_files_changed: list[str]
    test_files_changed: list[str]
    protected_files_changed: list[str]
    expected_files_touched: list[str]
    missing_expected_edits: list[str]
    unexpected_files_touched: list[str]
    agentpack_noise: list[str]
    agent_log_path: str
    test_log_path: str
    workdir: str


def _sample_fixture_cases(fixtures_root: Path) -> list[FixtureCase]:
    specs = [
        (
            "py_fastapi_app",
            "fix FastAPI auth token validation",
            ["src/app/auth.py", "tests/test_auth.py"],
            "backend-api",
        ),
        (
            "py_fastapi_app",
            "add user profile API endpoint",
            ["src/app/main.py", "src/app/users.py", "tests/test_users.py"],
            "backend-api",
        ),
        (
            "nextjs_app",
            "fix Next.js auth helper and API client",
            ["src/lib/auth.ts", "src/lib/api.ts"],
            "frontend-web",
        ),
        (
            "nextjs_app",
            "debug dashboard page data loading",
            ["src/app/page.tsx", "src/lib/api.ts"],
            "frontend-web",
        ),
        (
            "mixed_repo",
            "fix TypeScript API serialization utility",
            ["src/ts/api.ts", "src/ts/utils.ts"],
            "typescript",
        ),
        (
            "mixed_repo",
            "fix Python slugify parsing edge case",
            ["src/py/utils.py"],
            "python",
        ),
        (
            "django_rest_app",
            "fix cursor pagination in user list endpoint",
            ["api/views/user_list.py", "api/pagination.py", "tests/test_pagination.py"],
            "backend-api",
        ),
        (
            "django_rest_app",
            "fix validation error in user serializer",
            ["api/serializers/user.py"],
            "backend-api",
        ),
        (
            "go_service",
            "fix kubernetes readiness probe failing on startup",
            ["handler/health.go", "k8s/deployment.yaml"],
            "infrastructure",
        ),
        (
            "go_service",
            "fix Dockerfile build for Go server main deployment",
            ["Dockerfile", "cmd/server/main.go"],
            "infrastructure",
        ),
        (
            "rails_app",
            "fix welcome email not being sent after registration",
            ["app/mailers/user_mailer.rb", "app/jobs/email_job.rb", "spec/mailers/user_mailer_spec.rb"],
            "backend-api",
        ),
    ]

    cases: list[FixtureCase] = []
    for fixture, task, expected_files, task_type in specs:
        fixture_root = fixtures_root / fixture
        if fixture_root.exists():
            cases.append(
                FixtureCase(
                    fixture=fixture,
                    root=fixture_root,
                    case=BenchmarkCase(
                        task=task,
                        mode="balanced",
                        expected_files=expected_files,
                        task_type=task_type,
                    ),
                )
            )
    return cases


def _load_public_repo_specs(path: Path) -> list[PublicRepoSpec]:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    specs: list[PublicRepoSpec] = []
    for raw_repo in data.get("repos", []):
        cases = [
            PublicRepoCase(
                commit=raw_case["commit"],
                task=raw_case["task"],
                expected_files=raw_case.get("expected_files", []),
                mode=normalize_mode(raw_case.get("mode", "balanced")),
                task_type=raw_case.get("task_type", "general"),
                workspace=raw_case.get("workspace"),
                budget=raw_case.get("budget", 0),
                action_owner_files=raw_case.get("action_owner_files", []),
                required_support_files=raw_case.get("required_support_files", []),
                incidental_changed_files=raw_case.get("incidental_changed_files", []),
                optional_context_files=raw_case.get("optional_context_files", []),
            )
            for raw_case in raw_repo.get("cases", [])
        ]
        specs.append(PublicRepoSpec(
            name=raw_repo["name"],
            url=raw_repo["url"],
            ref=raw_repo.get("ref", "main"),
            cases=cases,
            sample_history=int(raw_repo.get("sample_history", 0) or 0),
            task_type=raw_repo.get("task_type", "general"),
            mode=normalize_mode(raw_repo.get("mode", "balanced")),
            budget=int(raw_repo.get("budget", 0) or 0),
            include_globs=raw_repo.get("include_globs", []),
            exclude_globs=raw_repo.get("exclude_globs", []),
            max_changed_files=int(raw_repo.get("max_changed_files", 8) or 8),
        ))
    return specs


def _split_filter_values(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


def _filter_public_repo_specs(
    specs: list[PublicRepoSpec],
    *,
    repo_filter: str = "",
    task_type_filter: str = "",
) -> list[PublicRepoSpec]:
    repo_names = _split_filter_values(repo_filter)
    task_types = _split_filter_values(task_type_filter)
    if not repo_names and not task_types:
        return specs
    filtered: list[PublicRepoSpec] = []
    for spec in specs:
        if repo_names and spec.name not in repo_names:
            continue
        cases = [case for case in spec.cases if not task_types or case.task_type in task_types]
        include_sampled_history = not task_types or spec.task_type in task_types
        sample_history = spec.sample_history if include_sampled_history else 0
        if not cases and sample_history <= 0:
            continue
        filtered.append(
            PublicRepoSpec(
                name=spec.name,
                url=spec.url,
                ref=spec.ref,
                cases=cases,
                sample_history=sample_history,
                task_type=spec.task_type,
                mode=spec.mode,
                budget=spec.budget,
                include_globs=spec.include_globs,
                exclude_globs=spec.exclude_globs,
                max_changed_files=spec.max_changed_files,
            )
        )
    return filtered


def _load_cases(path: Path) -> list[BenchmarkCase]:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cases: list[BenchmarkCase] = []
    for raw in data.get("cases", []):
        cases.append(BenchmarkCase(
            task=raw["task"],
            mode=normalize_mode(raw.get("mode", "balanced")),
            expected_files=raw.get("expected_files", []),
            expected_skills=raw.get("expected_skills", []),
            avoid_skills=raw.get("avoid_skills", []),
            task_type=raw.get("task_type", "general"),
            workspace=raw.get("workspace"),
            budget=raw.get("budget", 0),
            action_owner_files=raw.get("action_owner_files", []),
            required_support_files=raw.get("required_support_files", []),
            incidental_changed_files=raw.get("incidental_changed_files", []),
            optional_context_files=raw.get("optional_context_files", []),
        ))
    return cases


def _scaffold_cases(root: Path) -> Path:
    out = root / ".agentpack" / "benchmark.toml"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        '# AgentPack benchmark cases\n'
        '# Each case runs a pack and measures token savings, speed, and\n'
        '# selection quality. Add expected_files for precision/recall scoring.\n\n'
        '# How to build a useful eval set:\n'
        '# 1. Add 5-20 real tasks from your repo history.\n'
        '# 2. Fill expected_files with files you actually edited for that task.\n'
        '# 3. Run: agentpack benchmark --compare\n'
        '# 4. Tune task text, .agentignore, and scoring weights until recall/token noise look sane.\n\n'
        '[[cases]]\n'
        'task = "fix auth token expiry"\n'
        'mode = "balanced"\n'
        'task_type = "backend-api"\n'
        '# workspace = "apps/api"\n'
        '# budget = 2000\n'
        '# expected_files = [\n'
        '#   "src/auth/token.py",\n'
        '#   "src/auth/session.py",\n'
        '# ]\n\n'
        '# expected_skills = ["pytest-debugging", "auth-flow-review"]\n'
        '# avoid_skills = ["frontend-react-review"]\n\n'
        '[[cases]]\n'
        'task = "add rate limiting to API endpoints"\n'
        'mode = "balanced"\n'
        'task_type = "backend-api"\n',
        encoding="utf-8",
    )
    return out


def _write_results_template(root: Path, date: str | None = None) -> Path:
    stamp = date or datetime.now(timezone.utc).date().isoformat()
    out = root / "benchmarks" / "results" / f"{stamp}.md"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# AgentPack Benchmark Results\n\n"
        f"- date: {stamp}\n"
        "- agentpack version/commit: <version or git sha>\n"
        "- repo/task set: <repo names, anonymized domains, or fixture suite>\n"
        "- cases: <count>\n"
        "- command: `agentpack benchmark --compare --misses`\n\n"
        "| Metric | Value |\n"
        "|---|---:|\n"
        "| avg recall | <percent> |\n"
        "| avg precision | <percent> |\n"
        "| avg token precision | <percent> |\n"
        "| balanced p50 tokens | <tokens> |\n"
        "| balanced p95 tokens | <tokens> |\n"
        "| miss count | <count> |\n\n"
        "## Notes\n\n"
        "- Use historical tasks with `expected_files` set to files actually changed.\n"
        "- Do not mix synthetic fixture smoke results with real repo claims.\n"
        "- Include notable misses and the output from `agentpack benchmark --misses`.\n",
        encoding="utf-8",
    )
    return out


def _public_benchmark_markdown(
    results: list[CaseResult],
    *,
    title: str = "AgentPack Public Benchmark Table",
    suite: str = "historical tasks",
    version: str = "",
    command: str = "agentpack benchmark --misses --public-table",
) -> str:
    """Render benchmark results as publishable Markdown evidence."""
    scored = [result for result in results if result.case.expected_files]
    rows = scored or results
    generated = datetime.now(timezone.utc).date().isoformat()
    version_line = f"- agentpack version/commit: {version}\n" if version else ""
    lines = [
        f"# {title}",
        "",
        f"- date: {generated}",
        f"- suite: {suite}",
        f"- cases: {len(rows)}",
        f"- command: `{command}`",
        "",
    ]
    if version:
        lines.insert(4, version_line.rstrip())

    if scored:
        metrics = [_precision_recall(result) for result in scored]
        avg_p = sum(metric[0] for metric in metrics) / len(metrics)
        avg_r = sum(metric[1] for metric in metrics) / len(metrics)
        avg_f1 = sum(metric[2] for metric in metrics) / len(metrics)
        token_precisions = [
            1 - (result.noise_pct / 100)
            for result in scored
            if result.noise_pct is not None
        ]
        avg_token_precision = sum(token_precisions) / len(token_precisions) if token_precisions else 0.0
        pack_tokens = sorted(result.packed_tokens for result in scored)
        p50_tokens = pack_tokens[len(pack_tokens) // 2]
        p95_tokens = pack_tokens[min(len(pack_tokens) - 1, int((len(pack_tokens) - 1) * 0.95))]
        last_summary_waste, drop_last_delta, waste_cases = _low_budget_waste_summary(scored)
        lines += [
            "| Metric | Value |",
            "|---|---:|",
            f"| avg precision | {avg_p:.1%} |",
            f"| avg recall | {avg_r:.1%} |",
            f"| avg F1 | {avg_f1:.1%} |",
            f"| avg token precision | {avg_token_precision:.1%} |",
            f"| pack p50 tokens | {p50_tokens:,} |",
            f"| pack p95 tokens | {p95_tokens:,} |",
        ]
        if waste_cases:
            lines += [
                f"| low-budget cases with last-summary diagnostic | {waste_cases} |",
                f"| avg last-summary waste | {last_summary_waste:.0f} tokens |",
                f"| avg precision delta if drop last summary | {drop_last_delta:+.1%} |",
            ]
        lines += [
            "",
        ]

    lines += [
        "| Repo / suite | Task | Type | Mode | Budget | Packed tokens | Recall | Cand R@50 | Cand P@3 | Token precision | Rank@K | Time | Misses |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in rows:
        repo, task = _split_public_task(result.case.task)
        _p, recall, _f1 = _precision_recall(result) if result.case.expected_files else (0.0, 0.0, 0.0)
        token_precision = 1 - (result.noise_pct / 100) if result.noise_pct is not None else None
        misses = len(result.missed_expected)
        lines.append(
            "| "
            + " | ".join([
                _md_cell(repo),
                _md_cell(task),
                _md_cell(result.case.task_type),
                result.case.mode,
                f"{result.case.budget:,}" if result.case.budget else "default",
                f"{result.packed_tokens:,}",
                f"{recall:.1%}" if result.case.expected_files else "-",
                f"{result.candidate_recall_at_50:.1%}" if result.candidate_recall_at_50 is not None else "-",
                f"{result.candidate_precision_at_3:.1%}" if result.candidate_precision_at_3 is not None else "-",
                f"{token_precision:.1%}" if token_precision is not None else "-",
                str(result.rank_at_k) if result.rank_at_k is not None else "-",
                f"{result.total_s:.2f}s",
                str(misses),
            ])
            + " |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Use real historical tasks with `expected_files` set to files actually changed.",
        "- Treat small curated suites as smoke proof; expand case counts before broad external claims.",
        "- Keep synthetic fixture smoke results separate from public repo claims.",
        "- Investigate misses with `agentpack benchmark --misses` and `agentpack explain --omitted`.",
    ]
    return "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"


def _write_public_benchmark_table(
    root: Path,
    results: list[CaseResult],
    *,
    suite: str,
    version: str = "",
    command: str = "agentpack benchmark --misses --public-table",
    date: str | None = None,
) -> Path:
    stamp = date or datetime.now(timezone.utc).date().isoformat()
    out = root / "benchmarks" / "results" / f"{stamp}-public.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        _public_benchmark_markdown(results, suite=suite, version=version, command=command),
        encoding="utf-8",
    )
    return out


def _split_public_task(task: str) -> tuple[str, str]:
    if ":" in task:
        prefix, rest = task.split(":", 1)
        if prefix and "/" not in prefix and len(prefix) <= 40:
            return prefix.strip(), rest.strip()
    return "current repo", task


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _git_stdout(cwd: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def _git_lines(cwd: Path, args: list[str]) -> list[str]:
    output = _git_stdout(cwd, args)
    return [line for line in output.splitlines() if line.strip()]


def _run_git(cwd: Path | None, args: list[str]) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _git_commit_exists(cwd: Path, commit: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def _ensure_git_commit(cwd: Path, commit: str) -> None:
    if _git_commit_exists(cwd, commit):
        return
    _run_git(cwd, ["fetch", "--quiet", "--depth", "1", "origin", commit])
    if not _git_commit_exists(cwd, commit):
        raise RuntimeError(f"Unable to fetch public benchmark commit {commit}")


def _ensure_public_repo_clone(
    spec: PublicRepoSpec,
    cache_dir: Path,
    *,
    refresh: bool = False,
    depth: int = 120,
) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in spec.name)
    repo_dir = cache_dir / safe_name
    if refresh and repo_dir.exists():
        shutil.rmtree(repo_dir)
    if not repo_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(None, [
            "clone",
            "--quiet",
            "--depth",
            str(depth),
            spec.url,
            str(repo_dir),
        ])
    else:
        _run_git(repo_dir, ["fetch", "--quiet", "--depth", str(depth), "origin", spec.ref])
    _run_git(repo_dir, ["checkout", "--quiet", spec.ref])
    _run_git(repo_dir, ["reset", "--hard", "--quiet", spec.ref])
    _run_git(repo_dir, ["clean", "-ffd", "--quiet"])
    return repo_dir


def _sample_public_history_cases(source_repo: Path, spec: PublicRepoSpec) -> list[PublicRepoCase]:
    """Create benchmark cases from recent public commits and their real changed files."""
    if spec.sample_history <= 0:
        return []
    candidates = _git_lines(
        source_repo,
        [
            "log",
            "--first-parent",
            "--no-merges",
            "--format=%H%x00%s",
            f"-n{max(spec.sample_history * 4, spec.sample_history)}",
            spec.ref,
        ],
    )
    cases: list[PublicRepoCase] = []
    explicit_commits = {case.commit for case in spec.cases}
    for line in candidates:
        if "\x00" not in line:
            continue
        commit, subject = line.split("\x00", 1)
        if commit in explicit_commits:
            continue
        expected_files = _public_commit_changed_files(
            source_repo,
            commit,
            include_globs=spec.include_globs,
            exclude_globs=spec.exclude_globs,
            max_changed_files=spec.max_changed_files,
        )
        if not expected_files:
            continue
        cases.append(
            PublicRepoCase(
                commit=commit,
                task=subject,
                expected_files=expected_files,
                mode=spec.mode,
                task_type=spec.task_type,
                budget=spec.budget,
            )
        )
        if len(cases) >= spec.sample_history:
            break
    return cases


def _public_commit_changed_files(
    source_repo: Path,
    commit: str,
    *,
    include_globs: list[str],
    exclude_globs: list[str],
    max_changed_files: int,
) -> list[str]:
    try:
        parent = _git_stdout(source_repo, ["rev-parse", f"{commit}^"])
        files = _git_lines(
            source_repo,
            ["diff-tree", "--no-commit-id", "--name-only", "-r", commit],
        )
    except subprocess.CalledProcessError:
        return []
    filtered = [
        path
        for path in files
        if _public_path_allowed(path, include_globs=include_globs, exclude_globs=exclude_globs)
        and _public_path_exists_at_commit(source_repo, parent, path)
    ]
    if not filtered or len(filtered) > max_changed_files:
        return []
    return sorted(filtered)


def _public_path_exists_at_commit(source_repo: Path, commit: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}:{path}"],
        cwd=source_repo,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _public_path_allowed(path: str, *, include_globs: list[str], exclude_globs: list[str]) -> bool:
    if any(part in path for part in ("/vendor/", "/dist/", "/build/", "/node_modules/")):
        return False
    if Path(path).name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "go.sum"}:
        return False
    if exclude_globs and any(fnmatch(path, pattern) for pattern in exclude_globs):
        return False
    return not include_globs or any(fnmatch(path, pattern) for pattern in include_globs)


def _run_public_repo_suite(
    root: Path,
    specs: list[PublicRepoSpec],
    *,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> list[CaseResult]:
    """Run benchmark cases against parent checkouts of real public commits."""
    cache = cache_dir or root / ".agentpack" / "public-repos"
    results: list[CaseResult] = []
    with tempfile.TemporaryDirectory(prefix="agentpack-public-benchmark-") as temp_dir:
        temp_root = Path(temp_dir)
        for spec in specs:
            source_repo = _ensure_public_repo_clone(spec, cache, refresh=refresh).resolve()
            public_cases = [*spec.cases, *_sample_public_history_cases(source_repo, spec)]
            for public_case in public_cases:
                _ensure_git_commit(source_repo, public_case.commit)
                parent = _git_stdout(source_repo, ["rev-parse", f"{public_case.commit}^"])
                _ensure_git_commit(source_repo, parent)
                with tempfile.TemporaryDirectory(
                    prefix=f"{spec.name}-{public_case.commit[:8]}-",
                    dir=temp_root,
                ) as case_dir:
                    work_root = Path(case_dir) / "repo"
                    try:
                        _run_git(
                            source_repo.parent,
                            ["clone", "--quiet", "--shared", "--no-checkout", str(source_repo), str(work_root)],
                        )
                        _run_git(work_root, ["checkout", "--force", "--quiet", parent])
                        _run_git(work_root, ["reset", "--hard", "--quiet", parent])
                        _run_git(work_root, ["clean", "-ffd", "--quiet"])
                    except subprocess.CalledProcessError as exc:
                        stderr = (exc.stderr or "").strip()
                        command = " ".join(str(part) for part in exc.cmd)
                        detail = f": {stderr}" if stderr else ""
                        raise RuntimeError(
                            "Public benchmark checkout failed "
                            f"for repo={spec.name} commit={public_case.commit} parent={parent}; "
                            f"`{command}` exited {exc.returncode}{detail}"
                        ) from exc
                    missing_optional = [
                        path
                        for path in public_case.optional_context_files
                        if not (work_root / path).is_file()
                    ]
                    if missing_optional:
                        raise ValueError(
                            "optional_context_files must exist in the parent checkout "
                            f"for repo={spec.name} commit={public_case.commit}: {missing_optional}"
                        )
                    result = _run_case(
                        work_root,
                        BenchmarkCase(
                            task=public_case.task,
                            repository=spec.name,
                            mode=public_case.mode,
                            expected_files=public_case.expected_files,
                            task_type=public_case.task_type,
                            workspace=public_case.workspace,
                            budget=public_case.budget,
                            action_owner_files=public_case.action_owner_files,
                            required_support_files=public_case.required_support_files,
                            incidental_changed_files=public_case.incidental_changed_files,
                            optional_context_files=public_case.optional_context_files,
                        ),
                    )
                    results.append(result)
    return results


def _resolve_public_repo_lock_specs(
    root: Path,
    specs: list[PublicRepoSpec],
    *,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> list[PublicRepoSpec]:
    cache = cache_dir or root / ".agentpack" / "public-repos"
    locked: list[PublicRepoSpec] = []
    for spec in specs:
        source_repo = _ensure_public_repo_clone(spec, cache, refresh=refresh)
        public_cases = [*spec.cases, *_sample_public_history_cases(source_repo, spec)]
        for public_case in public_cases:
            _ensure_git_commit(source_repo, public_case.commit)
        locked.append(
            PublicRepoSpec(
                name=spec.name,
                url=spec.url,
                ref=spec.ref,
                cases=public_cases,
                sample_history=0,
                task_type=spec.task_type,
                mode=spec.mode,
                budget=spec.budget,
                include_globs=list(spec.include_globs),
                exclude_globs=list(spec.exclude_globs),
                max_changed_files=spec.max_changed_files,
            )
        )
    return locked


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_string_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _write_public_repo_lock(path: Path, specs: list[PublicRepoSpec]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AgentPack public benchmark lock file.",
        "# Generated from a resolved public-repos manifest; sample_history is frozen as explicit cases.",
        "",
    ]
    for spec in specs:
        lines.extend([
            "[[repos]]",
            f"name = {_toml_string(spec.name)}",
            f"url = {_toml_string(spec.url)}",
            f"ref = {_toml_string(spec.ref)}",
            "sample_history = 0",
            f"task_type = {_toml_string(spec.task_type)}",
            f"mode = {_toml_string(spec.mode)}",
            f"budget = {int(spec.budget)}",
            f"include_globs = {_toml_string_list(spec.include_globs)}",
            f"exclude_globs = {_toml_string_list(spec.exclude_globs)}",
            f"max_changed_files = {int(spec.max_changed_files)}",
            "",
        ])
        for case in spec.cases:
            lines.extend([
                "[[repos.cases]]",
                f"commit = {_toml_string(case.commit)}",
                f"task = {_toml_string(case.task)}",
                f"task_type = {_toml_string(case.task_type)}",
                f"mode = {_toml_string(case.mode)}",
                f"budget = {int(case.budget)}",
                f"expected_files = {_toml_string_list(case.expected_files)}",
            ])
            if any((
                case.action_owner_files,
                case.required_support_files,
                case.incidental_changed_files,
                case.optional_context_files,
            )):
                lines.extend([
                    f"action_owner_files = {_toml_string_list(case.action_owner_files)}",
                    f"required_support_files = {_toml_string_list(case.required_support_files)}",
                    f"incidental_changed_files = {_toml_string_list(case.incidental_changed_files)}",
                    f"optional_context_files = {_toml_string_list(case.optional_context_files)}",
                ])
            if case.workspace:
                lines.append(f"workspace = {_toml_string(case.workspace)}")
            lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _default_public_repos_file(root: Path) -> Path:
    return root / "benchmarks" / "public-repos.toml"


def _default_release_repos_file(root: Path) -> Path:
    return root / "benchmarks" / "release-repos.lock.toml"


def _load_release_gate_config(path: Path) -> ReleaseGateConfig:
    """Read optional release-only quality floors without affecting public suite manifests."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_gate = data.get("gate")
    if not isinstance(raw_gate, dict):
        return ReleaseGateConfig()

    def _optional_float(key: str) -> float | None:
        value = raw_gate.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    min_cases = raw_gate.get("min_scored_cases")
    return ReleaseGateConfig(
        min_recall=_optional_float("min_recall"),
        min_token_precision=_optional_float("min_token_precision"),
        min_scored_cases=int(min_cases) if isinstance(min_cases, int) and min_cases >= 0 else None,
    )


def _load_history_cases(root: Path, n: int) -> list[BenchmarkCase]:
    """Sample last N unique tasks from metrics.jsonl."""
    metrics_path = root / ".agentpack" / "metrics.jsonl"
    if not metrics_path.exists():
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    for line in reversed(metrics_path.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            task = rec.get("task", "").strip()
            mode = normalize_mode(rec.get("mode", "balanced"))
            if task and task not in seen_set:
                seen_set.add(task)
                seen.append((task, mode))
                if len(seen) >= n:
                    break
        except json.JSONDecodeError:
            pass
    return [BenchmarkCase(task=t, mode=m, task_type="history") for t, m in seen]


def _append_benchmark_cases(root: Path, cases: list[BenchmarkCase]) -> Path:
    out = root / ".agentpack" / "benchmark.toml"
    out.parent.mkdir(parents=True, exist_ok=True)
    prefix = out.read_text(encoding="utf-8").rstrip() + "\n\n" if out.exists() and out.read_text(encoding="utf-8").strip() else ""
    blocks: list[str] = []
    for case in cases:
        lines = [
            "[[cases]]",
            f"task = {json.dumps(case.task)}",
            f"mode = {json.dumps(case.mode)}",
            f"task_type = {json.dumps(case.task_type)}",
        ]
        if case.workspace:
            lines.append(f"workspace = {json.dumps(case.workspace)}")
        if case.budget:
            lines.append(f"budget = {case.budget}")
        lines.append("expected_files = [" + ", ".join(json.dumps(path) for path in case.expected_files) + "]")
        if case.expected_skills:
            lines.append("expected_skills = [" + ", ".join(json.dumps(skill) for skill in case.expected_skills) + "]")
        if case.avoid_skills:
            lines.append("avoid_skills = [" + ", ".join(json.dumps(skill) for skill in case.avoid_skills) + "]")
        blocks.append("\n".join(lines))
    out.write_text(prefix + "\n\n".join(blocks) + "\n", encoding="utf-8")
    return out


def _write_anonymous_benchmark_report(root: Path) -> tuple[Path, Path]:
    data = _anonymous_benchmark_report_data(root)
    report_json = root / ".agentpack" / "benchmark-report.json"
    report_md = root / ".agentpack" / "benchmark-report.md"
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_md.write_text(_anonymous_benchmark_report_markdown(data), encoding="utf-8")
    return report_md, report_json


def _anonymous_benchmark_report_data(root: Path) -> dict[str, Any]:
    cases_path = root / ".agentpack" / "benchmark.toml"
    cases = _load_cases(cases_path) if cases_path.exists() else []
    records = _load_jsonl(root / ".agentpack" / "benchmark_results.jsonl")
    scored_records = [
        record
        for record in records
        if isinstance(record.get("recall"), (int, float))
        and isinstance(record.get("token_precision"), (int, float))
    ]
    language_mix = _language_mix(root)
    avg_recall = _avg_record_value(scored_records, "recall")
    avg_token_precision = _avg_record_value(scored_records, "token_precision")
    miss_count = sum(len(record.get("misses") or []) for record in scored_records)
    repo_type = "public" if (root / ".git").exists() and _git_remote_public(root) else "private-or-local"
    return {
        "schema_version": 1,
        "repo_type": repo_type,
        "language_mix": language_mix,
        "cases": len(cases),
        "scored_runs": len(scored_records),
        "recall": round(avg_recall, 3) if avg_recall is not None else None,
        "token_precision": round(avg_token_precision, 3) if avg_token_precision is not None else None,
        "misses": miss_count,
        "no_source_code_uploaded": True,
        "source_paths_included": False,
        "generated_files": [
            ".agentpack/benchmark-report.md",
            ".agentpack/benchmark-report.json",
        ],
    }


def _anonymous_benchmark_report_markdown(data: dict[str, Any]) -> str:
    language_rows = "\n".join(
        f"| {language} | {share:.1%} |"
        for language, share in data.get("language_mix", {}).items()
    ) or "| unknown | 0.0% |"
    recall = _fmt_report_pct(data.get("recall"))
    token_precision = _fmt_report_pct(data.get("token_precision"))
    return "\n".join([
        "# AgentPack Anonymous Benchmark Report",
        "",
        f"- Repo type: {data['repo_type']}",
        f"- Cases: {data['cases']}",
        f"- Scored runs: {data['scored_runs']}",
        f"- Recall: {recall}",
        f"- Token precision: {token_precision}",
        f"- Misses: {data['misses']}",
        f"- No source code uploaded: {str(data['no_source_code_uploaded']).lower()}",
        "",
        "## Language Mix",
        "",
        "| Language | Share |",
        "|---|---:|",
        language_rows,
        "",
        "## Notes",
        "",
        "- This report contains aggregate counts and percentages only.",
        "- It does not include source code or private file contents.",
        "- Share with: `agentpack benchmark capture --since main --anonymous-report`.",
        "",
    ]) + "\n"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _avg_record_value(records: list[dict[str, Any]], key: str) -> float | None:
    values = [float(record[key]) for record in records if isinstance(record.get(key), (int, float))]
    return sum(values) / len(values) if values else None


def _benchmark_record_token_stats(record: dict[str, Any]) -> tuple[int, int, int]:
    selected_tokens = record.get("selected_tokens") or {}
    expected_files = set(record.get("expected_files") or [])
    selected_total = 0
    expected_selected = 0
    if isinstance(selected_tokens, dict):
        for path, raw_tokens in selected_tokens.items():
            if not isinstance(raw_tokens, (int, float)):
                continue
            tokens = int(raw_tokens)
            selected_total += tokens
            if path in expected_files:
                expected_selected += tokens
    if selected_total <= 0 and isinstance(record.get("packed_tokens"), (int, float)):
        selected_total = int(record["packed_tokens"])
    return selected_total, expected_selected, max(0, selected_total - expected_selected)


def _noise_row_reasons(row: dict[str, Any]) -> list[str]:
    reasons = row.get("reasons") or row.get("selection_reasons") or []
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons if reason]


def _noise_row_rank(row: dict[str, Any]) -> int:
    rank = row.get("rank")
    if isinstance(rank, (int, float)):
        return int(rank)
    return 1_000_000


def _noise_row_has_reason(row: dict[str, Any], needle: str) -> bool:
    return any(needle in reason for reason in _noise_row_reasons(row))


def _noise_row_has_strong_support(row: dict[str, Any]) -> bool:
    support_markers = (
        "direct content evidence",
        "direct dependency",
        "has related tests",
        "test for ",
        "second-pass related test",
        "matched entrypoint",
        "cross-layer related implementation",
        "quoted literal match",
    )
    return any(marker in reason for reason in _noise_row_reasons(row) for marker in support_markers)


def _benchmark_noise_prune_decision(row: dict[str, Any]) -> tuple[bool, list[str]]:
    family = str(row.get("family") or "")
    mode = str(row.get("mode") or "")
    rank = _noise_row_rank(row)
    strong_support = _noise_row_has_strong_support(row)
    reasons: list[str] = []

    if family == "config":
        if mode == "summary":
            reasons.append("config_summary")
        if _noise_row_has_reason(row, "release/version metadata"):
            reasons.append("release_metadata")
        if rank > 8 and not strong_support:
            reasons.append("low_rank_config")

    if family == "test" and mode == "summary" and not strong_support:
        reasons.append("weak_test_summary")

    if _noise_row_has_reason(row, "release/version metadata") and not strong_support:
        reasons.append("unsupported_release_metadata")

    if _noise_row_has_reason(row, "literal definition match") and not strong_support:
        reasons.append("unsupported_literal_definition")

    return bool(reasons), reasons


def _plausibly_useful_noise_paths(record: dict[str, Any]) -> set[str]:
    diagnostics = record.get("selection_diagnostics") or {}
    rows = diagnostics.get("selected_not_expected_but_plausibly_useful") or []
    if not isinstance(rows, list):
        return set()
    return {str(row.get("path")) for row in rows if isinstance(row, dict) and row.get("path")}


def _miss_rank(miss: dict[str, Any]) -> int | None:
    rank = miss.get("rank")
    if isinstance(rank, (int, float)):
        return int(rank)
    return None


def _miss_status_bucket(status: str) -> str:
    status_lc = status.lower()
    if "compressed context cap" in status_lc:
        return "compressed_context_cap"
    if "summary score below floor" in status_lc:
        return "summary_score_floor"
    if "score too low" in status_lc:
        return "score_too_low"
    if "not ranked" in status_lc:
        return "not_ranked"
    if "missing" in status_lc:
        return "missing_file"
    return status[:48] if status else "unknown"


def _miss_has_strong_evidence(miss: dict[str, Any]) -> bool:
    cap = miss.get("cap_block_diagnostic") or {}
    if isinstance(cap, dict) and bool(cap.get("candidate_has_strong_evidence")):
        return True
    reasons = miss.get("reasons") or []
    if not isinstance(reasons, list):
        return False
    strong_markers = (
        "direct content evidence",
        "direct dependency",
        "matched define:",
        "matched call:",
        "quoted literal match:",
        "literal definition match:",
    )
    return any(
        marker in str(reason)
        for reason in reasons
        for marker in strong_markers
    )


def _benchmark_ranked_skip_audit(
    records: list[dict[str, Any]],
    *,
    high_rank_cutoff: int = 10,
) -> dict[str, Any]:
    missed_total = 0
    ranked_total = 0
    high_ranked_total = 0
    high_ranked_strong = 0
    one_more_slot = 0
    status_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    blocker_family_counts: Counter[str] = Counter()
    cap_block_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []

    for record in records:
        task = str(record.get("task") or "")
        for miss in record.get("misses") or []:
            if not isinstance(miss, dict):
                continue
            missed_total += 1
            rank = _miss_rank(miss)
            if rank is not None:
                ranked_total += 1
            status = str(miss.get("status") or "")
            status_bucket = _miss_status_bucket(status)
            failure_type = str(miss.get("failure_type") or "unknown")
            family = str(miss.get("family") or "unknown")
            strong = _miss_has_strong_evidence(miss)
            high_ranked = rank is not None and rank <= high_rank_cutoff
            if high_ranked:
                high_ranked_total += 1
                if strong:
                    high_ranked_strong += 1
            if bool(miss.get("would_select_with_one_more_slot")):
                one_more_slot += 1
            status_counts[status_bucket] += 1
            failure_counts[failure_type] += 1
            family_counts[family] += 1
            blocker = miss.get("selected_noise_file_that_beat_expected")
            if isinstance(blocker, dict):
                blocker_family_counts[str(blocker.get("family") or "unknown")] += 1
            cap = miss.get("cap_block_diagnostic") or {}
            if isinstance(cap, dict) and cap.get("block_reason"):
                cap_block_counts[str(cap["block_reason"])] += 1
            if rank is not None:
                rows.append({
                    "rank": rank,
                    "score": miss.get("score"),
                    "score_delta_vs_last_selected": miss.get("score_delta_vs_last_selected"),
                    "path": str(miss.get("path") or ""),
                    "task": task[:120],
                    "family": family,
                    "failure_type": failure_type,
                    "status": status,
                    "status_bucket": status_bucket,
                    "strong_evidence": strong,
                    "would_select_with_one_more_slot": bool(miss.get("would_select_with_one_more_slot")),
                    "block_reason": str(cap.get("block_reason") or "") if isinstance(cap, dict) else "",
                    "blocker_path": str(blocker.get("path") or "") if isinstance(blocker, dict) else "",
                    "blocker_family": str(blocker.get("family") or "") if isinstance(blocker, dict) else "",
                })

    rows.sort(key=lambda row: (
        int(row["rank"]),
        -float(row["score"]) if isinstance(row.get("score"), (int, float)) else 0.0,
        str(row["task"]),
        str(row["path"]),
    ))
    return {
        "missed_expected_files": missed_total,
        "ranked_missed_expected_files": ranked_total,
        "high_rank_cutoff": high_rank_cutoff,
        "high_ranked_missed_expected_files": high_ranked_total,
        "high_ranked_strong_evidence_files": high_ranked_strong,
        "would_select_with_one_more_slot": one_more_slot,
        "status_counts": dict(status_counts.most_common()),
        "failure_type_counts": dict(failure_counts.most_common()),
        "family_counts": dict(family_counts.most_common()),
        "blocker_family_counts": dict(blocker_family_counts.most_common()),
        "cap_block_reason_counts": dict(cap_block_counts.most_common()),
        "top_ranked_misses": rows[:12],
    }


_MAV_CODE_SUFFIXES = {".go", ".rs", ".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx"}
_MAV_GUARDED_PRUNE_THRESHOLD = 20.0
_MAV_AGGRESSIVE_PRUNE_THRESHOLD = 50.0


def _benchmark_mav_content_hits(reasons: list[str]) -> int:
    hits = 0
    for reason in reasons:
        match = re.search(r"content keyword match \((\d+)\)", reason)
        if match:
            hits = max(hits, int(match.group(1)))
    return hits


def _benchmark_mav_has_signal(reasons: list[str], *needles: str) -> bool:
    return any(
        reason.startswith(needle) or needle in reason
        for reason in reasons
        for needle in needles
    )


def _benchmark_mav_is_hub_path(path: str) -> bool:
    name = Path(path).name.lower()
    stem = Path(name).stem
    return name in {
        "__init__.py",
        "context.go",
        "core.py",
        "core.ts",
        "gin.go",
        "helpers.py",
        "index.js",
        "index.jsx",
        "index.ts",
        "index.tsx",
        "utils.go",
        "utils.py",
    } or stem in {"context", "core", "debug", "formatting", "helpers", "test_helpers", "testing", "utils"}


def _benchmark_mav_support_signals(reasons: list[str]) -> tuple[bool, bool, bool, bool]:
    direct = _benchmark_mav_has_signal(
        reasons,
        "direct content evidence",
        "keyword phrase match:",
        "literal definition match:",
        "matched call:",
        "matched define:",
        "multi-token",
        "quoted literal match:",
    )
    graph = _benchmark_mav_has_signal(
        reasons,
        "caller of",
        "direct dependency",
        "related test",
        "reverse dependency",
        "test for high-scoring",
        "workspace match",
    )
    structural = _benchmark_mav_has_signal(
        reasons,
        "cross-layer related",
        "recall neighbor",
        "second-pass recall neighbor",
        "build/dependency metadata",
        "matched external system:",
        "conventional scope path match",
    )
    symbolic = _benchmark_mav_has_signal(reasons, "symbol keyword match") and _benchmark_mav_has_signal(
        reasons,
        "matched role keyword:",
        "matched ranking keyword:",
        "conventional scope path match",
    )
    return direct, graph, structural, symbolic


def _benchmark_mav_score(row: dict[str, Any]) -> float:
    """Diagnostic Marginal Action Value from serialized benchmark evidence only."""
    path = str(row.get("path") or "")
    family = str(row.get("family") or _path_family(path))
    reasons = _noise_row_reasons(row)
    score = float(row.get("score") or 0.0)
    rank = max(1, _noise_row_rank(row))
    tokens = int(row.get("tokens") or row.get("candidate_tokens") or 0)
    content_hits = _benchmark_mav_content_hits(reasons)
    direct, graph, structural, symbolic = _benchmark_mav_support_signals(reasons)
    supported = direct or graph or structural or symbolic
    weak_content_only = content_hits > 0 and not supported
    recent_only = _benchmark_mav_has_signal(reasons, "recently modified") and not supported
    churn_only = _benchmark_mav_has_signal(reasons, "high churn") and not supported
    hub_path = _benchmark_mav_is_hub_path(path)

    action_value = min(max(score, 0.0), 800.0) * 0.055
    action_value += max(0.0, 26.0 - 4.0 * math.log2(rank))
    action_value += min(content_hits, 6) * 6.0
    if direct:
        action_value += 38.0
    if graph:
        action_value += 22.0
    if structural:
        action_value += 18.0
    if symbolic:
        action_value += 16.0
    if family == "source":
        action_value += 10.0
    if family == "test" and _benchmark_mav_has_signal(
        reasons,
        "explicit test task file",
        "matched call:",
        "matched define:",
        "test for high-scoring",
    ):
        action_value += 8.0
    if family in {"config", "examples"} and not structural:
        action_value -= 10.0
    if family == "test" and not _benchmark_mav_has_signal(
        reasons,
        "direct content evidence",
        "explicit test task file",
        "matched call:",
        "matched define:",
        "related test",
        "test for high-scoring",
    ) and not structural and not symbolic:
        action_value -= 22.0
    if weak_content_only:
        action_value -= 22.0
    if recent_only:
        action_value -= 12.0
    if churn_only:
        action_value -= 10.0
    if hub_path and not (direct or structural or symbolic):
        action_value -= 28.0

    true_noise_risk = 0.0
    if family in {"config", "examples"} and not structural:
        true_noise_risk += 0.25
    if family == "test" and not _benchmark_mav_has_signal(
        reasons,
        "explicit test task file",
        "related test",
        "test for high-scoring",
    ) and not structural and not symbolic:
        true_noise_risk += 0.20
    if hub_path and not (direct or structural or symbolic):
        true_noise_risk += 0.35
    if weak_content_only:
        true_noise_risk += 0.30
    if recent_only:
        true_noise_risk += 0.12
    if churn_only:
        true_noise_risk += 0.12
    if direct:
        true_noise_risk -= 0.25
    if graph:
        true_noise_risk -= 0.18
    if structural:
        true_noise_risk -= 0.16
    if symbolic:
        true_noise_risk -= 0.12
    true_noise_risk = min(1.0, max(0.0, true_noise_risk))

    return action_value - (70.0 * true_noise_risk) - (min(tokens, 1200) * 0.025)


def _benchmark_mav_low_value_reasons(row: dict[str, Any]) -> list[str]:
    path = str(row.get("path") or "")
    family = str(row.get("family") or _path_family(path))
    reasons = _noise_row_reasons(row)
    content_hits = _benchmark_mav_content_hits(reasons)
    direct, graph, structural, symbolic = _benchmark_mav_support_signals(reasons)
    supported = direct or graph or structural or symbolic
    out: list[str] = []
    if _benchmark_mav_is_hub_path(path) and not supported:
        out.append("hub_path")
    if family in {"config", "examples"} and not structural:
        out.append(f"{family}_family")
    if family == "test" and not graph and not direct and not structural and not symbolic:
        out.append("generic_test")
    if content_hits > 0 and not supported:
        out.append("content_only")
    if _benchmark_mav_has_signal(reasons, "recently modified") and not supported:
        out.append("recent_only")
    if _benchmark_mav_has_signal(reasons, "high churn") and not supported:
        out.append("churn_only")
    return out or ["low_mav"]


def _benchmark_mav_prune_profile(
    records: list[dict[str, Any]],
    *,
    threshold: float,
    min_token_precision: float,
) -> dict[str, Any]:
    selected_total = 0
    expected_selected_total = 0
    candidate_rows = 0
    pruned_rows = 0
    pruned_tokens = 0
    pruned_true_noise = 0
    pruned_plausible = 0
    projected_tps: list[float] = []
    passing_cases = 0
    reason_counts: Counter[str] = Counter()

    for record in records:
        selected_tokens, expected_selected, _strict_noise = _benchmark_record_token_stats(record)
        selected_total += selected_tokens
        expected_selected_total += expected_selected
        diagnostics = record.get("selection_diagnostics") or {}
        plausible_paths = _plausibly_useful_noise_paths(record)
        record_pruned = 0
        selected_noise = diagnostics.get("selected_noise") or []
        if isinstance(selected_noise, list):
            for row in selected_noise:
                if not isinstance(row, dict):
                    continue
                candidate_rows += 1
                mav = _benchmark_mav_score(row)
                if mav >= threshold:
                    continue
                tokens = int(row.get("tokens") or 0)
                pruned_rows += 1
                pruned_tokens += tokens
                record_pruned += tokens
                reason_counts.update(_benchmark_mav_low_value_reasons(row))
                if str(row.get("path") or "") in plausible_paths:
                    pruned_plausible += tokens
                else:
                    pruned_true_noise += tokens
        projected_tokens = max(0, selected_tokens - record_pruned)
        projected_tp = expected_selected / projected_tokens if projected_tokens > 0 else 1.0
        projected_tps.append(projected_tp)
        if projected_tp >= min_token_precision:
            passing_cases += 1

    projected_aggregate_tokens = max(0, selected_total - pruned_tokens)
    return {
        "threshold": threshold,
        "candidate_rows": candidate_rows,
        "pruned_rows": pruned_rows,
        "pruned_tokens": pruned_tokens,
        "pruned_true_noise_tokens": pruned_true_noise,
        "pruned_plausibly_useful_tokens": pruned_plausible,
        "true_noise_purity": pruned_true_noise / pruned_tokens if pruned_tokens > 0 else None,
        "projected_avg_token_precision": sum(projected_tps) / len(projected_tps) if projected_tps else None,
        "projected_aggregate_token_precision": (
            expected_selected_total / projected_aggregate_tokens
            if projected_aggregate_tokens > 0 else None
        ),
        "cases_passing_target": passing_cases,
        "reason_counts": dict(reason_counts.most_common()),
    }


def _benchmark_mav_candidate_tokens(miss: dict[str, Any]) -> int:
    cap = miss.get("cap_block_diagnostic") or {}
    if isinstance(cap, dict) and isinstance(cap.get("candidate_tokens"), (int, float)):
        return int(cap["candidate_tokens"])
    status = str(miss.get("status") or "").lower()
    if "summary score below floor" in status:
        return 80
    return 0


def _benchmark_mav_replacement_report(
    records: list[dict[str, Any]],
    *,
    min_gain: float = 55.0,
    token_delta_penalty: float = 0.04,
) -> dict[str, Any]:
    selected_total = 0
    expected_selected_total = 0
    candidate_pairs = 0
    accepted_replacements = 0
    added_expected_tokens = 0
    removed_tokens = 0
    removed_true_noise = 0
    removed_plausible = 0
    examples: list[dict[str, Any]] = []

    for record in records:
        selected_tokens, expected_selected, _strict_noise = _benchmark_record_token_stats(record)
        selected_total += selected_tokens
        expected_selected_total += expected_selected
        diagnostics = record.get("selection_diagnostics") or {}
        plausible_paths = _plausibly_useful_noise_paths(record)
        selected_noise = [
            row for row in diagnostics.get("selected_noise") or []
            if isinstance(row, dict)
        ]
        misses = [
            miss for miss in record.get("misses") or []
            if isinstance(miss, dict)
            and _miss_status_bucket(str(miss.get("status") or "")) in {"compressed_context_cap", "summary_score_floor"}
            and _benchmark_mav_candidate_tokens(miss) > 0
        ]
        pairs: list[tuple[float, int, int, dict[str, Any], dict[str, Any], int, int]] = []
        for candidate_index, miss in enumerate(misses):
            candidate_tokens = _benchmark_mav_candidate_tokens(miss)
            candidate_row = {**miss, "tokens": candidate_tokens}
            candidate_mav = _benchmark_mav_score(candidate_row)
            for incumbent_index, incumbent in enumerate(selected_noise):
                incumbent_tokens = int(incumbent.get("tokens") or 0)
                if incumbent_tokens <= 0:
                    continue
                token_delta = candidate_tokens - incumbent_tokens
                gain = candidate_mav - _benchmark_mav_score(incumbent) - (max(0, token_delta) * token_delta_penalty)
                if gain < min_gain:
                    continue
                candidate_pairs += 1
                pairs.append((gain, candidate_index, incumbent_index, miss, incumbent, candidate_tokens, incumbent_tokens))

        used_candidates: set[int] = set()
        used_incumbents: set[int] = set()
        for gain, candidate_index, incumbent_index, miss, incumbent, candidate_tokens, incumbent_tokens in sorted(
            pairs,
            key=lambda item: (-item[0], int(item[5] - item[6]), int(item[3].get("rank") or 999999)),
        ):
            if candidate_index in used_candidates or incumbent_index in used_incumbents:
                continue
            used_candidates.add(candidate_index)
            used_incumbents.add(incumbent_index)
            accepted_replacements += 1
            added_expected_tokens += candidate_tokens
            removed_tokens += incumbent_tokens
            if str(incumbent.get("path") or "") in plausible_paths:
                removed_plausible += incumbent_tokens
            else:
                removed_true_noise += incumbent_tokens
            if len(examples) < 8:
                examples.append({
                    "gain": round(gain, 1),
                    "task": str(record.get("task") or "")[:100],
                    "candidate": str(miss.get("path") or ""),
                    "candidate_rank": miss.get("rank"),
                    "candidate_tokens": candidate_tokens,
                    "incumbent": str(incumbent.get("path") or ""),
                    "incumbent_rank": incumbent.get("rank"),
                    "incumbent_tokens": incumbent_tokens,
                })

    projected_tokens = max(0, selected_total - removed_tokens + added_expected_tokens)
    projected_expected = expected_selected_total + added_expected_tokens
    return {
        "min_gain": min_gain,
        "candidate_pairs": candidate_pairs,
        "accepted_replacements": accepted_replacements,
        "added_expected_tokens": added_expected_tokens,
        "removed_tokens": removed_tokens,
        "removed_true_noise_tokens": removed_true_noise,
        "removed_plausibly_useful_tokens": removed_plausible,
        "projected_aggregate_token_precision": (
            projected_expected / projected_tokens
            if projected_tokens > 0 else None
        ),
        "examples": examples,
    }


def _benchmark_mav_ablation(records: list[dict[str, Any]], *, min_token_precision: float) -> dict[str, Any]:
    return {
        "policy": "marginal_action_value_v1_offline",
        "guarded_prune": _benchmark_mav_prune_profile(
            records,
            threshold=_MAV_GUARDED_PRUNE_THRESHOLD,
            min_token_precision=min_token_precision,
        ),
        "aggressive_prune": _benchmark_mav_prune_profile(
            records,
            threshold=_MAV_AGGRESSIVE_PRUNE_THRESHOLD,
            min_token_precision=min_token_precision,
        ),
        "replacement": _benchmark_mav_replacement_report(records),
    }


_ACTIVATION_DIRECT_CONFIRMATION_MARKERS = (
    "direct content evidence",
    "direct dependency",
    "explicit test task file",
    "literal definition match:",
    "matched external system:",
    "quoted literal match:",
)
_ACTIVATION_INDEPENDENT_CONFIRMATION_MARKERS = (
    "cross-layer related implementation",
    "has related tests",
    "matched entrypoint:",
    "recall neighbor",
    "related test",
    "second-pass recall neighbor",
    "second-pass related test",
    "test for high-scoring",
)


def _benchmark_activation_primary_intent(record: dict[str, Any], selected_noise: list[dict[str, Any]]) -> str:
    diagnostics = record.get("selection_diagnostics") or {}
    profile = diagnostics.get("intent_profile") or {}
    if isinstance(profile, dict) and profile.get("primary"):
        return str(profile["primary"])
    misses = [miss for miss in record.get("misses") or [] if isinstance(miss, dict)]
    computed = _benchmark_intent_profile(
        task=str(record.get("task") or ""),
        expected_files={str(path) for path in record.get("expected_files") or [] if path},
        missed_expected=misses,
        selected_noise=selected_noise,
    )
    return str(computed.get("primary") or "general")


def _benchmark_activation_has_costimulus(row: dict[str, Any]) -> bool:
    reasons = _noise_row_reasons(row)
    return _benchmark_mav_has_signal(reasons, *_ACTIVATION_DIRECT_CONFIRMATION_MARKERS) or _benchmark_mav_has_signal(
        reasons,
        *_ACTIVATION_INDEPENDENT_CONFIRMATION_MARKERS,
    )


def _benchmark_activation_gate_reasons(row: dict[str, Any], *, primary_intent: str) -> list[str]:
    if _benchmark_activation_has_costimulus(row):
        return []

    reasons = _noise_row_reasons(row)
    family = str(row.get("family") or _path_family(str(row.get("path") or "")))
    mode = str(row.get("mode") or "")
    rank = _noise_row_rank(row)
    score = float(row.get("score") or 0.0)
    has_symbol = _benchmark_mav_has_signal(reasons, "symbol keyword match")
    has_content = _benchmark_mav_has_signal(reasons, "content keyword match")
    has_define = _benchmark_mav_has_signal(reasons, "matched define:", "multi-token defines match")
    out: list[str] = []

    if family in {"config", "examples"}:
        out.append("non_action_family_without_costimulus")
    if (
        primary_intent == "test_focus"
        and family == "source"
        and mode == "skeleton"
        and rank > 8
        and has_symbol
        and has_content
    ):
        out.append("test_task_late_source_without_costimulus")
    if (
        primary_intent != "test_focus"
        and family == "test"
        and mode == "skeleton"
        and rank <= 8
        and score >= 300.0
        and has_symbol
        and has_define
    ):
        out.append("non_test_task_test_symbol_define_without_costimulus")
    return out


def _benchmark_activation_gate_profile(
    records: list[dict[str, Any]],
    *,
    min_token_precision: float,
    include_guarded_mav: bool,
) -> dict[str, Any]:
    selected_total = 0
    expected_selected_total = 0
    candidate_rows = 0
    pruned_rows = 0
    pruned_tokens = 0
    pruned_true_noise = 0
    pruned_plausible = 0
    pruned_unlabeled = 0
    visible_plausible = 0
    projected_tps: list[float] = []
    passing_cases = 0
    reason_counts: Counter[str] = Counter()
    family_token_counts: Counter[str] = Counter()
    intent_token_counts: Counter[str] = Counter()

    for record in records:
        selected_tokens, expected_selected, _strict_noise = _benchmark_record_token_stats(record)
        selected_total += selected_tokens
        expected_selected_total += expected_selected
        diagnostics = record.get("selection_diagnostics") or {}
        plausible_paths = _plausibly_useful_noise_paths(record)
        labels_available = isinstance(diagnostics.get("selected_not_expected_but_plausibly_useful"), list)
        selected_noise = [
            row for row in diagnostics.get("selected_noise") or []
            if isinstance(row, dict)
        ]
        primary_intent = _benchmark_activation_primary_intent(record, selected_noise)
        record_pruned = 0
        for row in selected_noise:
            candidate_rows += 1
            tokens = int(row.get("tokens") or 0)
            if str(row.get("path") or "") in plausible_paths:
                visible_plausible += tokens
            reasons = _benchmark_activation_gate_reasons(row, primary_intent=primary_intent)
            if include_guarded_mav and _benchmark_mav_score(row) < _MAV_GUARDED_PRUNE_THRESHOLD:
                reasons = [*reasons, "guarded_mav_low_value"]
            if not reasons:
                continue
            pruned_rows += 1
            pruned_tokens += tokens
            record_pruned += tokens
            reason_counts.update(reasons)
            family_token_counts[str(row.get("family") or _path_family(str(row.get("path") or "")))] += tokens
            intent_token_counts[primary_intent] += tokens
            if not labels_available:
                pruned_unlabeled += tokens
            elif str(row.get("path") or "") in plausible_paths:
                pruned_plausible += tokens
            else:
                pruned_true_noise += tokens

        projected_tokens = max(0, selected_tokens - record_pruned)
        projected_tp = expected_selected / projected_tokens if projected_tokens > 0 else 1.0
        projected_tps.append(projected_tp)
        if projected_tp >= min_token_precision:
            passing_cases += 1

    projected_aggregate_tokens = max(0, selected_total - pruned_tokens)
    return {
        "include_guarded_mav": include_guarded_mav,
        "candidate_rows": candidate_rows,
        "pruned_rows": pruned_rows,
        "pruned_tokens": pruned_tokens,
        "pruned_true_noise_tokens": pruned_true_noise,
        "pruned_plausibly_useful_tokens": pruned_plausible,
        "pruned_unlabeled_tokens": pruned_unlabeled,
        "true_noise_purity": (
            pruned_true_noise / pruned_tokens
            if pruned_tokens > 0 and pruned_unlabeled == 0 else None
        ),
        "plausibly_useful_prune_fraction": (
            pruned_plausible / visible_plausible
            if visible_plausible > 0 else None
        ),
        "projected_avg_token_precision": sum(projected_tps) / len(projected_tps) if projected_tps else None,
        "projected_aggregate_token_precision": (
            expected_selected_total / projected_aggregate_tokens
            if projected_aggregate_tokens > 0 else None
        ),
        "cases_passing_target": passing_cases,
        "reason_counts": dict(reason_counts.most_common()),
        "family_token_counts": dict(family_token_counts.most_common()),
        "intent_token_counts": dict(intent_token_counts.most_common()),
    }


def _benchmark_activation_atom_ceiling(
    records: list[dict[str, Any]],
    *,
    pruned_tokens: int,
    atom_sizes: tuple[int, ...] = (80, 120, 160, 240),
    max_atoms_per_case: int = 3,
) -> dict[str, Any]:
    selected_total = 0
    expected_selected_total = 0
    for record in records:
        selected_tokens, expected_selected, _strict_noise = _benchmark_record_token_stats(record)
        selected_total += selected_tokens
        expected_selected_total += expected_selected

    profiles: list[dict[str, Any]] = []
    for atom_size in atom_sizes:
        added_expected = 0
        atoms = 0
        cases = 0
        status_counts: Counter[str] = Counter()
        family_counts: Counter[str] = Counter()
        for record in records:
            candidates: list[tuple[int, str, int, str, str]] = []
            seen: set[str] = set()
            for miss in record.get("misses") or []:
                if not isinstance(miss, dict):
                    continue
                path = str(miss.get("path") or "")
                if not path or path in seen:
                    continue
                seen.add(path)
                status = _miss_status_bucket(str(miss.get("status") or ""))
                if status not in {"compressed_context_cap", "summary_score_floor"}:
                    continue
                candidate_tokens = _benchmark_mav_candidate_tokens(miss)
                if candidate_tokens <= 0:
                    continue
                candidates.append((
                    _miss_rank(miss) or 1_000_000,
                    path,
                    candidate_tokens,
                    status,
                    str(miss.get("family") or _path_family(path)),
                ))
            selected_candidates = sorted(candidates, key=lambda item: (item[0], item[1]))[:max_atoms_per_case]
            if selected_candidates:
                cases += 1
            for _rank, _path, candidate_tokens, status, family in selected_candidates:
                added = min(atom_size, candidate_tokens)
                added_expected += added
                atoms += 1
                status_counts[status] += 1
                family_counts[family] += 1

        projected_tokens = max(0, selected_total - pruned_tokens + added_expected)
        profiles.append({
            "atom_tokens": atom_size,
            "atoms": atoms,
            "cases": cases,
            "added_expected_tokens": added_expected,
            "projected_aggregate_token_precision": (
                (expected_selected_total + added_expected) / projected_tokens
                if projected_tokens > 0 else None
            ),
            "status_counts": dict(status_counts.most_common()),
            "family_counts": dict(family_counts.most_common()),
        })

    return {
        "candidate_policy": "missed_cap_or_summary_floor",
        "max_atoms_per_case": max_atoms_per_case,
        "profiles": profiles,
    }


def _benchmark_activation_gate_ablation(records: list[dict[str, Any]], *, min_token_precision: float) -> dict[str, Any]:
    activation_only = _benchmark_activation_gate_profile(
        records,
        min_token_precision=min_token_precision,
        include_guarded_mav=False,
    )
    combined = _benchmark_activation_gate_profile(
        records,
        min_token_precision=min_token_precision,
        include_guarded_mav=True,
    )
    return {
        "policy": "two_stage_activation_v1_offline",
        "activation_only": activation_only,
        "activation_plus_guarded_mav": combined,
        "atom_ceiling": _benchmark_activation_atom_ceiling(
            records,
            pruned_tokens=int(combined.get("pruned_tokens") or 0),
        ),
    }


def _benchmark_zero_expected_selected_audit(
    records: list[dict[str, Any]],
    *,
    high_rank_cutoff: int = 10,
) -> dict[str, Any]:
    cases = 0
    selected_total = 0
    label_selected_noise = 0
    audited_true_noise = 0
    plausible_noise = 0
    high_ranked_misses = 0
    high_ranked_strong_misses = 0
    miss_status_counts: Counter[str] = Counter()
    miss_failure_counts: Counter[str] = Counter()
    miss_family_counts: Counter[str] = Counter()
    cap_block_counts: Counter[str] = Counter()
    selected_family_tokens: Counter[str] = Counter()
    selected_mode_tokens: Counter[str] = Counter()
    guarded_mav_reason_counts: Counter[str] = Counter()
    case_rows: list[dict[str, Any]] = []

    for record in records:
        selected_tokens, expected_selected, _strict_noise = _benchmark_record_token_stats(record)
        if selected_tokens <= 0 or expected_selected > 0:
            continue
        cases += 1
        selected_total += selected_tokens
        task = str(record.get("task") or "")[:120]
        selected_token_map = record.get("selected_tokens") or {}
        if not isinstance(selected_token_map, dict):
            selected_token_map = {}

        diagnostics = record.get("selection_diagnostics") or {}
        selected_noise_rows = [
            row for row in diagnostics.get("selected_noise") or []
            if isinstance(row, dict)
        ]
        selected_noise_by_path = {
            str(row.get("path") or ""): row
            for row in selected_noise_rows
            if row.get("path")
        }
        for path, raw_tokens in selected_token_map.items():
            if not isinstance(raw_tokens, (int, float)):
                continue
            tokens = int(raw_tokens)
            row = selected_noise_by_path.get(str(path)) or {}
            family = str(row.get("family") or _path_family(str(path)))
            selected_family_tokens[family] += tokens
            mode = str(row.get("mode") or "unknown")
            selected_mode_tokens[mode] += tokens
            if row and _benchmark_mav_score(row) < _MAV_GUARDED_PRUNE_THRESHOLD:
                guarded_mav_reason_counts.update(_benchmark_mav_low_value_reasons(row))

        label_audit = diagnostics.get("label_audit") or {}
        true_noise = int(label_audit.get("audited_noise_tokens") or 0)
        plausible = int(label_audit.get("plausibly_useful_tokens") or 0)
        label_selected = int(label_audit.get("selected_noise_tokens") or 0)
        audited_true_noise += true_noise
        plausible_noise += plausible
        label_selected_noise += label_selected

        top_miss: dict[str, Any] | None = None
        for miss in record.get("misses") or []:
            if not isinstance(miss, dict):
                continue
            status_bucket = _miss_status_bucket(str(miss.get("status") or ""))
            miss_status_counts[status_bucket] += 1
            miss_failure_counts[str(miss.get("failure_type") or "unknown")] += 1
            miss_family_counts[str(miss.get("family") or "unknown")] += 1
            cap = miss.get("cap_block_diagnostic") or {}
            if isinstance(cap, dict) and cap.get("block_reason"):
                cap_block_counts[str(cap["block_reason"])] += 1
            rank = _miss_rank(miss)
            if rank is not None and rank <= high_rank_cutoff:
                high_ranked_misses += 1
                if _miss_has_strong_evidence(miss):
                    high_ranked_strong_misses += 1
            if top_miss is None:
                top_miss = miss
                continue
            top_rank = _miss_rank(top_miss) or 1_000_000
            miss_rank = rank or 1_000_000
            top_score = float(top_miss.get("score") or 0.0)
            miss_score = float(miss.get("score") or 0.0)
            if (miss_rank, -miss_score, str(miss.get("path") or "")) < (
                top_rank,
                -top_score,
                str(top_miss.get("path") or ""),
            ):
                top_miss = miss

        case_family_tokens: Counter[str] = Counter()
        for path, raw_tokens in selected_token_map.items():
            if isinstance(raw_tokens, (int, float)):
                row = selected_noise_by_path.get(str(path)) or {}
                case_family_tokens[str(row.get("family") or _path_family(str(path)))] += int(raw_tokens)
        family_mix = ", ".join(f"{family}={tokens}t" for family, tokens in case_family_tokens.most_common(3))
        top_selected = sorted(
            (
                {
                    "path": str(path),
                    "tokens": int(raw_tokens),
                    "family": str((selected_noise_by_path.get(str(path)) or {}).get("family") or _path_family(str(path))),
                    "rank": (selected_noise_by_path.get(str(path)) or {}).get("rank"),
                }
                for path, raw_tokens in selected_token_map.items()
                if isinstance(raw_tokens, (int, float))
            ),
            key=lambda item: (-int(item["tokens"]), str(item["path"])),
        )[:3]
        case_rows.append({
            "task": task,
            "selected_tokens": selected_tokens,
            "audited_true_noise_tokens": true_noise,
            "plausibly_useful_tokens": plausible,
            "label_selected_noise_tokens": label_selected,
            "miss_count": len([miss for miss in record.get("misses") or [] if isinstance(miss, dict)]),
            "top_miss_path": str(top_miss.get("path") or "") if top_miss else "",
            "top_miss_rank": _miss_rank(top_miss) if top_miss else None,
            "top_miss_score": top_miss.get("score") if top_miss else None,
            "top_miss_status": _miss_status_bucket(str(top_miss.get("status") or "")) if top_miss else "",
            "selected_family_mix": family_mix,
            "top_selected": top_selected,
        })

    case_rows.sort(key=lambda row: int(row["selected_tokens"]), reverse=True)
    return {
        "cases": cases,
        "selected_tokens": selected_total,
        "label_selected_noise_tokens": label_selected_noise,
        "audited_true_noise_tokens": audited_true_noise,
        "plausibly_useful_tokens": plausible_noise,
        "high_rank_cutoff": high_rank_cutoff,
        "high_ranked_missed_expected_files": high_ranked_misses,
        "high_ranked_strong_evidence_files": high_ranked_strong_misses,
        "miss_status_counts": dict(miss_status_counts.most_common()),
        "miss_failure_type_counts": dict(miss_failure_counts.most_common()),
        "miss_family_counts": dict(miss_family_counts.most_common()),
        "cap_block_reason_counts": dict(cap_block_counts.most_common()),
        "selected_family_tokens": dict(selected_family_tokens.most_common()),
        "selected_mode_tokens": dict(selected_mode_tokens.most_common()),
        "guarded_mav_reason_counts": dict(guarded_mav_reason_counts.most_common()),
        "top_cases": case_rows[:10],
    }


def _benchmark_ablation_report(records: list[dict[str, Any]], *, min_token_precision: float = 0.50) -> dict[str, Any]:
    scored_records = [
        record
        for record in records
        if isinstance(record.get("recall"), (int, float))
        and isinstance(record.get("token_precision"), (int, float))
    ]
    if not scored_records:
        return {
            "cases": len(records),
            "scored_cases": 0,
            "target_token_precision": min_token_precision,
        }

    selected_total = 0
    expected_selected_total = 0
    strict_noise_total = 0
    case_noise_removal_to_target = 0
    zero_expected_cases = 0
    zero_expected_selected_tokens = 0
    audited_true_noise = 0
    plausible_noise = 0
    label_selected_noise = 0
    projected_true_noise_tps: list[float] = []
    true_noise_target_passes = 0
    classifier_pruned_tokens = 0
    classifier_pruned_true_noise = 0
    classifier_pruned_plausible = 0
    classifier_pruned_unlabeled = 0
    classifier_visible_noise_tokens = 0
    classifier_plausible_visible_tokens = 0
    classifier_pruned_rows = 0
    classifier_candidate_rows = 0
    classifier_target_passes = 0
    classifier_projected_tps: list[float] = []
    classifier_reason_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    waste_family_tokens: Counter[str] = Counter()
    correction_cases: list[dict[str, Any]] = []

    for record in scored_records:
        selected_tokens, expected_selected, strict_noise = _benchmark_record_token_stats(record)
        selected_total += selected_tokens
        expected_selected_total += expected_selected
        strict_noise_total += strict_noise
        failure_counts.update(record.get("failure_type_counts") or {})
        waste_family_tokens.update(record.get("selected_family_waste_tokens") or {})

        required_case_removal = max(0, int(selected_tokens - (expected_selected / min_token_precision))) if min_token_precision > 0 else 0
        case_noise_removal_to_target += required_case_removal
        if required_case_removal:
            correction_cases.append({
                "task": str(record.get("task") or "")[:120],
                "required_noise_removal": required_case_removal,
                "recall": record.get("recall"),
                "token_precision": record.get("token_precision"),
                "packed_tokens": selected_tokens,
                "expected_selected_tokens": expected_selected,
            })

        if selected_tokens > 0 and expected_selected == 0:
            zero_expected_cases += 1
            zero_expected_selected_tokens += selected_tokens

        label_audit = (record.get("selection_diagnostics") or {}).get("label_audit") or {}
        true_noise = int(label_audit.get("audited_noise_tokens") or 0)
        audited_true_noise += true_noise
        plausible_noise += int(label_audit.get("plausibly_useful_tokens") or 0)
        label_selected_noise += int(label_audit.get("selected_noise_tokens") or 0)
        projected_tokens = max(0, selected_tokens - true_noise)
        projected_tp = expected_selected / projected_tokens if projected_tokens > 0 else 1.0
        projected_true_noise_tps.append(projected_tp)
        if projected_tp >= min_token_precision:
            true_noise_target_passes += 1

        diagnostics = record.get("selection_diagnostics") or {}
        selected_noise = diagnostics.get("selected_noise") or []
        plausible_paths = _plausibly_useful_noise_paths(record)
        labels_available = isinstance(diagnostics.get("selected_not_expected_but_plausibly_useful"), list)
        record_classifier_pruned = 0
        if isinstance(selected_noise, list):
            for row in selected_noise:
                if not isinstance(row, dict):
                    continue
                classifier_candidate_rows += 1
                tokens = int(row.get("tokens") or 0)
                classifier_visible_noise_tokens += tokens
                path = str(row.get("path") or "")
                if path in plausible_paths:
                    classifier_plausible_visible_tokens += tokens
                should_prune, prune_reasons = _benchmark_noise_prune_decision(row)
                if not should_prune:
                    continue
                classifier_pruned_rows += 1
                classifier_pruned_tokens += tokens
                record_classifier_pruned += tokens
                classifier_reason_counts.update(prune_reasons)
                if not labels_available:
                    classifier_pruned_unlabeled += tokens
                elif path in plausible_paths:
                    classifier_pruned_plausible += tokens
                else:
                    classifier_pruned_true_noise += tokens
        classifier_projected_tokens = max(0, selected_tokens - record_classifier_pruned)
        classifier_projected_tp = (
            expected_selected / classifier_projected_tokens
            if classifier_projected_tokens > 0 else 1.0
        )
        classifier_projected_tps.append(classifier_projected_tp)
        if classifier_projected_tp >= min_token_precision:
            classifier_target_passes += 1

    aggregate_noise_removal_to_target = (
        max(0, int(selected_total - (expected_selected_total / min_token_precision)))
        if min_token_precision > 0 else 0
    )
    projected_aggregate_tokens = max(0, selected_total - audited_true_noise)
    classifier_projected_aggregate_tokens = max(0, selected_total - classifier_pruned_tokens)
    oracle_excerpt_projection = _benchmark_fixed_selected_excerpt_projection(
        scored_records,
        diagnostic_key="oracle_non_expected_excerpt_ceiling",
        policy="oracle_non_expected_excerpt_ceiling_v1",
    )
    return {
        "cases": len(records),
        "scored_cases": len(scored_records),
        "target_token_precision": min_token_precision,
        "avg_recall": _avg_record_value(scored_records, "recall"),
        "avg_token_precision": _avg_record_value(scored_records, "token_precision"),
        "selected_tokens": selected_total,
        "expected_selected_tokens": expected_selected_total,
        "strict_noise_tokens": strict_noise_total,
        "aggregate_token_precision": expected_selected_total / selected_total if selected_total > 0 else None,
        "aggregate_noise_removal_to_target": aggregate_noise_removal_to_target,
        "strict_noise_removal_fraction": (
            aggregate_noise_removal_to_target / strict_noise_total
            if strict_noise_total > 0 else None
        ),
        "case_noise_removal_to_target": case_noise_removal_to_target,
        "zero_expected_selected_cases": zero_expected_cases,
        "zero_expected_selected_tokens": zero_expected_selected_tokens,
        "audited_true_noise_tokens": audited_true_noise,
        "plausibly_useful_noise_tokens": plausible_noise,
        "label_selected_noise_tokens": label_selected_noise,
        "projected_avg_tp_remove_true_noise": (
            sum(projected_true_noise_tps) / len(projected_true_noise_tps)
            if projected_true_noise_tps else None
        ),
        "projected_aggregate_tp_remove_true_noise": (
            expected_selected_total / projected_aggregate_tokens
            if projected_aggregate_tokens > 0 else None
        ),
        "cases_passing_target_after_true_noise_prune": true_noise_target_passes,
        "heuristic_prune": {
            "policy": "weak_release_config_test_summary_v1",
            "candidate_rows": classifier_candidate_rows,
            "visible_noise_tokens": classifier_visible_noise_tokens,
            "visible_plausibly_useful_tokens": classifier_plausible_visible_tokens,
            "pruned_rows": classifier_pruned_rows,
            "pruned_tokens": classifier_pruned_tokens,
            "pruned_true_noise_tokens": classifier_pruned_true_noise,
            "pruned_plausibly_useful_tokens": classifier_pruned_plausible,
            "pruned_unlabeled_tokens": classifier_pruned_unlabeled,
            "true_noise_purity": (
                classifier_pruned_true_noise / classifier_pruned_tokens
                if classifier_pruned_tokens > 0 and classifier_pruned_unlabeled == 0 else None
            ),
            "plausibly_useful_prune_fraction": (
                classifier_pruned_plausible / classifier_plausible_visible_tokens
                if classifier_plausible_visible_tokens > 0 else None
            ),
            "projected_avg_token_precision": (
                sum(classifier_projected_tps) / len(classifier_projected_tps)
                if classifier_projected_tps else None
            ),
            "projected_aggregate_token_precision": (
                expected_selected_total / classifier_projected_aggregate_tokens
                if classifier_projected_aggregate_tokens > 0 else None
            ),
            "cases_passing_target": classifier_target_passes,
            "prune_reason_counts": dict(classifier_reason_counts.most_common()),
        },
        "fixed_selected_excerpt_projection": _benchmark_fixed_selected_excerpt_projection(
            scored_records,
            diagnostic_key="fixed_selected_excerpt_projection",
            policy="fixed_selected_source_excerpt_v1",
        ),
        "guarded_fixed_selected_excerpt_projection": _benchmark_fixed_selected_excerpt_projection(
            scored_records,
            diagnostic_key="guarded_fixed_selected_excerpt_projection",
            policy="guarded_fixed_selected_source_excerpt_v1",
        ),
        "label_free_tiered_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="label_free_tiered_excerpt_projection",
            policy="label_free_tiered_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "weak_only_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="weak_only_excerpt_projection",
            policy="weak_only_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "weak_action_mismatch_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="weak_action_mismatch_excerpt_projection",
            policy="weak_action_mismatch_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "strong_carrier_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="strong_carrier_excerpt_projection",
            policy="strong_carrier_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "guarded_strong_carrier_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="guarded_strong_carrier_excerpt_projection",
            policy="guarded_strong_carrier_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "weak_plus_strong_carrier_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="weak_plus_strong_carrier_excerpt_projection",
            policy="weak_plus_strong_carrier_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "weak_plus_guarded_strong_carrier_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="weak_plus_guarded_strong_carrier_excerpt_projection",
            policy="weak_plus_guarded_strong_carrier_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "ast_checkpoint_memory_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="ast_checkpoint_memory_excerpt_projection",
            policy="ast_checkpoint_memory_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "ranked_test_skeleton_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="ranked_test_skeleton_excerpt_projection",
            policy="ranked_test_skeleton_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "ranked_test_symbol_carrier_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="ranked_test_symbol_carrier_excerpt_projection",
            policy="ranked_test_symbol_carrier_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "ranked_source_churn_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="ranked_source_churn_excerpt_projection",
            policy="ranked_source_churn_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "ranked_source_metadata_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="ranked_source_metadata_excerpt_projection",
            policy="ranked_source_metadata_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "ranked_metadata_summary_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="ranked_metadata_summary_excerpt_projection",
            policy="ranked_metadata_summary_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "mav_span_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="mav_span_excerpt_projection",
            policy="mav_span_per_token_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "neutral_mav_span_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="neutral_mav_span_excerpt_projection",
            policy="neutral_mav_span_optimizer_v1",
            oracle=oracle_excerpt_projection,
        ),
        "risk_aware_tiered_excerpt_projection": _benchmark_excerpt_with_oracle_capture(
            scored_records,
            diagnostic_key="risk_aware_tiered_excerpt_projection",
            policy="risk_aware_tiered_source_excerpt_v1",
            oracle=oracle_excerpt_projection,
        ),
        "oracle_miss_signature_audit": _benchmark_oracle_miss_signature_audit(
            scored_records,
            oracle_key="oracle_non_expected_excerpt_ceiling",
            comparator_key="risk_aware_tiered_excerpt_projection",
        ),
        "oracle_non_expected_excerpt_ceiling": oracle_excerpt_projection,
        "mav_ablation": _benchmark_mav_ablation(scored_records, min_token_precision=min_token_precision),
        "activation_gate": _benchmark_activation_gate_ablation(scored_records, min_token_precision=min_token_precision),
        "ranked_skip_audit": _benchmark_ranked_skip_audit(scored_records),
        "zero_expected_audit": _benchmark_zero_expected_selected_audit(scored_records),
        "failure_type_counts": dict(failure_counts.most_common()),
        "selected_waste_family_tokens": dict(waste_family_tokens.most_common()),
        "top_correction_cases": sorted(
            correction_cases,
            key=lambda item: int(item["required_noise_removal"]),
            reverse=True,
        )[:10],
    }


def _benchmark_fixed_selected_excerpt_projection(
    records: list[dict[str, Any]],
    *,
    diagnostic_key: str = "fixed_selected_excerpt_projection",
    policy: str = "fixed_selected_source_excerpt_v1",
) -> dict[str, Any]:
    cases = 0
    selected_file_set_violations = 0
    baseline_selected = 0
    projected_selected = 0
    baseline_expected = 0
    projected_expected = 0
    removed_tokens = 0
    expected_loss = 0
    strict_noise_removed = 0
    projected_files = 0
    memory_signal_selected_files = 0
    memory_signal_projected_files = 0
    improved_cases = 0
    regressed_cases = 0
    tier_counts: Counter[str] = Counter()
    projected_tier_counts: Counter[str] = Counter()
    removed_tokens_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []

    for record in records:
        projection = (record.get("selection_diagnostics") or {}).get(diagnostic_key) or {}
        if not isinstance(projection, dict) or not projection:
            continue
        cases += 1
        if not bool(projection.get("selected_file_set_unchanged")):
            selected_file_set_violations += 1
        baseline_selected += int(projection.get("baseline_selected_tokens") or 0)
        projected_selected += int(projection.get("projected_selected_tokens") or 0)
        baseline_expected += int(projection.get("baseline_expected_tokens") or 0)
        projected_expected += int(projection.get("projected_expected_tokens") or 0)
        removed_tokens += int(projection.get("removed_tokens") or 0)
        expected_loss += int(projection.get("expected_token_loss") or 0)
        strict_noise_removed += int(projection.get("strict_noise_removed") or 0)
        projected_files += int(projection.get("projected_file_count") or 0)
        memory_signal_selected_files += int(projection.get("memory_signal_selected_files") or 0)
        memory_signal_projected_files += int(projection.get("memory_signal_projected_files") or 0)
        tier_counts.update(projection.get("tier_counts") or {})
        projected_tier_counts.update(projection.get("projected_tier_counts") or {})
        removed_tokens_by_tier.update(projection.get("removed_tokens_by_tier") or {})
        strict_noise_removed_by_tier.update(projection.get("strict_noise_removed_by_tier") or {})
        expected_loss_by_tier.update(projection.get("expected_loss_by_tier") or {})
        delta = float(projection.get("token_precision_delta") or 0.0)
        if delta > 0:
            improved_cases += 1
        elif delta < 0:
            regressed_cases += 1
        if projection.get("projected_files"):
            rows.append({
                "task": str(record.get("task") or "")[:120],
                "removed_tokens": int(projection.get("removed_tokens") or 0),
                "expected_token_loss": int(projection.get("expected_token_loss") or 0),
                "strict_noise_removed": int(projection.get("strict_noise_removed") or 0),
                "token_precision_delta": delta,
                "projected_file_count": int(projection.get("projected_file_count") or 0),
                "memory_signal_selected_files": int(projection.get("memory_signal_selected_files") or 0),
                "memory_signal_projected_files": int(projection.get("memory_signal_projected_files") or 0),
                "removed_tokens_by_tier": projection.get("removed_tokens_by_tier") or {},
                "expected_loss_by_tier": projection.get("expected_loss_by_tier") or {},
            })

    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else None
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else None
    compression_profit = strict_noise_removed - expected_loss
    return {
        "policy": policy,
        "cases": cases,
        "selected_file_set_violations": selected_file_set_violations,
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": removed_tokens,
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": expected_loss,
        "strict_noise_removed": strict_noise_removed,
        "compression_profit": compression_profit,
        "expected_loss_per_1k_removed": (
            (expected_loss / removed_tokens) * 1000.0
            if removed_tokens > 0 else 0.0
        ),
        "projected_files": projected_files,
        "memory_signal_selected_files": memory_signal_selected_files,
        "memory_signal_projected_files": memory_signal_projected_files,
        "memory_signals_tested": memory_signal_selected_files > 0,
        "baseline_aggregate_token_precision": baseline_tp,
        "projected_aggregate_token_precision": projected_tp,
        "aggregate_token_precision_delta": (
            projected_tp - baseline_tp
            if projected_tp is not None and baseline_tp is not None else None
        ),
        "improved_cases": improved_cases,
        "regressed_cases": regressed_cases,
        "tier_counts": dict(tier_counts.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_tokens_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "top_cases": sorted(rows, key=lambda row: int(row["removed_tokens"]), reverse=True)[:10],
    }


def _benchmark_excerpt_with_oracle_capture(
    records: list[dict[str, Any]],
    *,
    diagnostic_key: str,
    policy: str,
    oracle: dict[str, Any],
) -> dict[str, Any]:
    report = _benchmark_fixed_selected_excerpt_projection(
        records,
        diagnostic_key=diagnostic_key,
        policy=policy,
    )
    extractor_gain = report.get("aggregate_token_precision_delta")
    oracle_gain = oracle.get("aggregate_token_precision_delta")
    report["oracle_policy"] = oracle.get("policy")
    report["oracle_aggregate_token_precision_delta"] = oracle_gain
    report["oracle_capture_rate"] = (
        extractor_gain / oracle_gain
        if isinstance(extractor_gain, (int, float))
        and isinstance(oracle_gain, (int, float))
        and oracle_gain > 0
        else None
    )
    return report


def _benchmark_oracle_miss_signature_audit(
    records: list[dict[str, Any]],
    *,
    oracle_key: str,
    comparator_key: str,
    min_oracle_removed_tokens: int = 40,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    signature_counts: Counter[str] = Counter()
    signature_tokens: Counter[str] = Counter()
    family_tokens: Counter[str] = Counter()
    protected_counts: Counter[str] = Counter()
    baseline_selected = 0
    baseline_expected = 0
    missed_tokens = 0
    missed_files = 0
    comparator_removed_tokens = 0

    for record in records:
        selected_tokens, expected_selected, _strict_noise = _benchmark_record_token_stats(record)
        baseline_selected += selected_tokens
        baseline_expected += expected_selected
        diagnostics = record.get("selection_diagnostics") or {}
        oracle = diagnostics.get(oracle_key) or {}
        comparator = diagnostics.get(comparator_key) or {}
        if not isinstance(oracle, dict) or not isinstance(comparator, dict):
            continue
        comparator_rows = comparator.get("projected_files") or []
        comparator_by_path = {
            str(row.get("path")): row
            for row in comparator_rows
            if isinstance(row, dict) and row.get("path")
        }
        selected_info = _benchmark_selected_file_info(record)
        for oracle_row in oracle.get("projected_files") or []:
            if not isinstance(oracle_row, dict):
                continue
            path = str(oracle_row.get("path") or "")
            oracle_removed = int(oracle_row.get("removed_tokens") or 0)
            if not path or oracle_removed < min_oracle_removed_tokens:
                continue
            comparator_row = comparator_by_path.get(path)
            comparator_removed = int(comparator_row.get("removed_tokens") or 0) if comparator_row else 0
            if comparator_removed >= int(oracle_removed * 0.5):
                continue

            info = selected_info.get(path, {})
            reasons = [str(reason) for reason in info.get("reasons") or []]
            mode = str(info.get("mode") or oracle_row.get("mode") or "")
            current_tokens = int(info.get("tokens") or oracle_row.get("current_tokens") or 0)
            family = str(info.get("family") or oracle_row.get("family") or _path_family(path))
            confidence = _source_excerpt_confidence_tier(
                path=path,
                mode=mode,
                reasons=reasons,
                current_tokens=current_tokens,
                changed_paths=set(),
                symbols=[],
            )
            should_compress, protection_reasons = _source_excerpt_should_compress(
                confidence=confidence,
                policy="risk_aware_tiered_source_excerpt_v1",
            )
            signatures = _oracle_miss_signatures(
                path=path,
                family=family,
                reasons=reasons,
                current_tokens=current_tokens,
                confidence=confidence,
            )
            if not signatures:
                signatures = ["unclassified_medium_or_strong"]
            for signature in signatures:
                signature_counts[signature] += 1
                signature_tokens[signature] += oracle_removed
            family_tokens[family] += oracle_removed
            if not should_compress:
                protected_counts.update(protection_reasons)
            else:
                protected_counts["projection_not_applied_or_too_small"] += 1
            missed_files += 1
            missed_tokens += oracle_removed - comparator_removed
            comparator_removed_tokens += comparator_removed
            rows.append({
                "task": str(record.get("task") or "")[:120],
                "path": path,
                "extension": Path(path).suffix.lower() or "(none)",
                "family": family,
                "mode": mode,
                "tokens": current_tokens,
                "oracle_removed_tokens": oracle_removed,
                "comparator_removed_tokens": comparator_removed,
                "missed_removed_tokens": oracle_removed - comparator_removed,
                "tier": confidence.get("tier"),
                "confidence_score": round(float(confidence.get("score") or 0.0), 1),
                "structural_risk": bool(confidence.get("structural_risk")),
                "action_mismatch": bool(confidence.get("action_mismatch")),
                "medium_compression_safe": bool(confidence.get("medium_compression_safe")),
                "why_protected": protection_reasons[:6],
                "signatures": signatures[:6],
                "selection_reasons": reasons[:6],
                "matched_terms": oracle_row.get("matched_terms", [])[:8],
                "rank": info.get("rank"),
                "score": info.get("score"),
            })

    rows.sort(key=lambda row: int(row["missed_removed_tokens"]), reverse=True)
    return {
        "policy": "oracle_miss_signature_audit_v1",
        "oracle_key": oracle_key,
        "comparator_key": comparator_key,
        "min_oracle_removed_tokens": min_oracle_removed_tokens,
        "missed_files": missed_files,
        "missed_oracle_tokens": missed_tokens,
        "comparator_removed_tokens": comparator_removed_tokens,
        "signature_counts": dict(signature_counts.most_common()),
        "signature_tokens": dict(signature_tokens.most_common()),
        "family_tokens": dict(family_tokens.most_common()),
        "protected_reason_counts": dict(protected_counts.most_common()),
        "baseline_selected_tokens": baseline_selected,
        "baseline_expected_tokens": baseline_expected,
        "signature_marginal_union": _oracle_miss_signature_marginal_union(
            rows=rows,
            baseline_selected=baseline_selected,
            baseline_expected=baseline_expected,
        ),
        "top_missed": rows[:20],
    }


_ORACLE_MISS_SIGNATURE_ORDER = (
    "strong_carrier_not_action_owner",
    "evidence_carrier_not_action_owner",
    "large_low_density_match",
    "dependency_neighbor_non_owner",
    "test_support_non_target",
    "structural_name_false_protection",
    "broad_api_surface",
    "import_or_dependency_only_confirmation",
    "metadata_or_docs_carrier",
    "action_path_mismatch",
)


def _oracle_miss_signature_marginal_union(
    *,
    rows: list[dict[str, Any]],
    baseline_selected: int,
    baseline_expected: int,
) -> list[dict[str, Any]]:
    """Deduplicate overlapping oracle signatures so bucket tokens are not double counted."""
    seen_keys: set[tuple[str, str]] = set()
    cumulative_removed = 0
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else None
    union_rows: list[dict[str, Any]] = []
    for signature in _ORACLE_MISS_SIGNATURE_ORDER:
        new_files = 0
        removed_tokens = 0
        for row in rows:
            row_signatures = {str(value) for value in row.get("signatures") or []}
            if signature not in row_signatures:
                continue
            key = (str(row.get("task") or ""), str(row.get("path") or ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            new_files += 1
            removed_tokens += int(row.get("missed_removed_tokens") or 0)
        if new_files <= 0:
            continue
        cumulative_removed += removed_tokens
        projected_selected = max(0, baseline_selected - cumulative_removed)
        projected_tp = (
            baseline_expected / projected_selected
            if projected_selected > 0 else None
        )
        union_rows.append({
            "signature": signature,
            "new_files": new_files,
            "removed_tokens": removed_tokens,
            "strict_noise_removed": removed_tokens,
            "expected_token_loss": 0,
            "compression_profit": removed_tokens,
            "cumulative_removed_tokens": cumulative_removed,
            "projected_aggregate_token_precision": projected_tp,
            "aggregate_token_precision_delta": (
                projected_tp - baseline_tp
                if projected_tp is not None and baseline_tp is not None else None
            ),
        })
    return union_rows


def _benchmark_selected_file_info(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    selected_tokens = record.get("selected_tokens") or {}
    selected_modes = record.get("selected_modes") or {}
    diagnostics = record.get("selection_diagnostics") or {}
    selected_noise = diagnostics.get("selected_noise") or []
    top_candidates = record.get("top_candidates") or []
    info: dict[str, dict[str, Any]] = {}

    if isinstance(selected_tokens, dict):
        for path, tokens in selected_tokens.items():
            path_str = str(path)
            info[path_str] = {
                "path": path_str,
                "tokens": int(tokens) if isinstance(tokens, (int, float)) else 0,
                "mode": selected_modes.get(path_str) if isinstance(selected_modes, dict) else None,
                "family": _path_family(path_str),
                "reasons": [],
            }
    for source in (selected_noise, top_candidates):
        if not isinstance(source, list):
            continue
        for row in source:
            if not isinstance(row, dict) or not row.get("path"):
                continue
            path = str(row["path"])
            target = info.setdefault(path, {
                "path": path,
                "tokens": 0,
                "mode": None,
                "family": _path_family(path),
                "reasons": [],
            })
            if row.get("reasons"):
                target["reasons"] = [str(reason) for reason in row.get("reasons") or []]
            if row.get("rank") is not None:
                target["rank"] = row.get("rank")
            if row.get("score") is not None:
                target["score"] = row.get("score")
            if row.get("family"):
                target["family"] = str(row["family"])
            if row.get("mode"):
                target["mode"] = str(row["mode"])
            if row.get("tokens") is not None:
                target["tokens"] = int(row.get("tokens") or 0)
    return info


def _oracle_miss_signatures(
    *,
    path: str,
    family: str,
    reasons: list[str],
    current_tokens: int,
    confidence: dict[str, Any],
) -> list[str]:
    content_hits = _content_keyword_hits_from_reasons(reasons)
    direct, graph, structural, symbolic = _benchmark_mav_support_signals(reasons)
    direct_symbol = _benchmark_mav_has_signal(
        reasons,
        "matched call:",
        "matched define:",
        "matched entrypoint:",
        "matched env read:",
        "matched side effect:",
        "quoted literal match:",
        "literal definition match:",
    )
    related_test = _benchmark_mav_has_signal(
        reasons,
        "has related tests",
        "related test",
        "test for high-scoring",
        "explicit test task file",
    )
    signatures: list[str] = []
    structural_risk = bool(confidence.get("structural_risk"))
    strong_carrier = bool(confidence.get("strong_carrier"))
    hub_path = _benchmark_mav_is_hub_path(path)
    if strong_carrier:
        signatures.append("strong_carrier_not_action_owner")
    if strong_carrier or ((graph or structural or symbolic) and not direct_symbol):
        signatures.append("evidence_carrier_not_action_owner")
    if graph and (strong_carrier or not direct_symbol):
        signatures.append("dependency_neighbor_non_owner")
    if structural_risk and (strong_carrier or not direct_symbol):
        signatures.append("structural_name_false_protection")
    if family in {"test", "fixtures", "examples"} and not related_test and (strong_carrier or not direct_symbol):
        signatures.append("test_support_non_target")
    if (hub_path or structural_risk) and content_hits >= 2 and current_tokens >= 240 and (strong_carrier or not direct_symbol):
        signatures.append("broad_api_surface")
    if graph and content_hits <= 2 and (strong_carrier or not direct_symbol):
        signatures.append("import_or_dependency_only_confirmation")
    if current_tokens >= 240 and content_hits <= 2 and (strong_carrier or not direct_symbol):
        signatures.append("large_low_density_match")
    if family in {"config", "docs"} and (strong_carrier or not direct_symbol):
        signatures.append("metadata_or_docs_carrier")
    if confidence.get("action_mismatch"):
        signatures.append("action_path_mismatch")

    seen: set[str] = set()
    deduped: list[str] = []
    for signature in signatures:
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(signature)
    return deduped


def _print_benchmark_ablation_report(report: dict[str, Any], *, label: str = "") -> None:
    title = "Benchmark Ablation Oracle"
    if label:
        title += f"  [dim]{label}[/]"
    console.print(f"\n[bold]{title}[/]\n")
    if not report.get("scored_cases"):
        console.print("[yellow]No scored benchmark records found.[/]")
        return

    target = float(report["target_token_precision"])
    rows = [
        ("cases", f"{report['scored_cases']}/{report['cases']}"),
        ("avg recall", f"{float(report['avg_recall']):.1%}"),
        ("avg token precision", f"{float(report['avg_token_precision']):.1%}"),
        ("aggregate token precision", f"{float(report['aggregate_token_precision']):.1%}"),
        ("selected tokens", f"{int(report['selected_tokens']):,}"),
        ("strict noise tokens", f"{int(report['strict_noise_tokens']):,}"),
        (f"aggregate noise removal for {target:.0%} TP", f"{int(report['aggregate_noise_removal_to_target']):,}"),
        ("strict noise fraction to remove", _fmt_report_pct(report.get("strict_noise_removal_fraction"))),
        (f"case-level correction for {target:.0%} TP", f"{int(report['case_noise_removal_to_target']):,}"),
        ("zero-expected selected cases", f"{int(report['zero_expected_selected_cases'])}"),
        ("zero-expected selected tokens", f"{int(report['zero_expected_selected_tokens']):,}"),
        ("audited true noise", f"{int(report['audited_true_noise_tokens']):,}"),
        ("plausibly useful noise", f"{int(report['plausibly_useful_noise_tokens']):,}"),
        ("projected avg TP after true-noise prune", _fmt_report_pct(report.get("projected_avg_tp_remove_true_noise"))),
        ("projected aggregate TP after true-noise prune", _fmt_report_pct(report.get("projected_aggregate_tp_remove_true_noise"))),
        ("cases passing after true-noise prune", f"{int(report['cases_passing_target_after_true_noise_prune'])}/{int(report['scored_cases'])}"),
    ]
    heuristic = report.get("heuristic_prune") or {}
    if heuristic:
        rows.extend([
            ("heuristic prune policy", str(heuristic.get("policy") or "")),
            ("heuristic candidate rows", f"{int(heuristic.get('candidate_rows') or 0):,}"),
            ("heuristic pruned tokens", f"{int(heuristic.get('pruned_tokens') or 0):,}"),
            ("heuristic pruned true noise", f"{int(heuristic.get('pruned_true_noise_tokens') or 0):,}"),
            ("heuristic pruned plausible useful", f"{int(heuristic.get('pruned_plausibly_useful_tokens') or 0):,}"),
            ("heuristic true-noise purity", _fmt_report_pct(heuristic.get("true_noise_purity"))),
            ("heuristic plausible prune fraction", _fmt_report_pct(heuristic.get("plausibly_useful_prune_fraction"))),
            ("projected avg TP after heuristic prune", _fmt_report_pct(heuristic.get("projected_avg_token_precision"))),
            ("projected aggregate TP after heuristic prune", _fmt_report_pct(heuristic.get("projected_aggregate_token_precision"))),
            ("cases passing after heuristic prune", f"{int(heuristic.get('cases_passing_target') or 0)}/{int(report['scored_cases'])}"),
        ])
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("metric", style="dim")
    table.add_column("value", justify="right")
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)

    zero_audit = report.get("zero_expected_audit") or {}
    if zero_audit and int(zero_audit.get("cases") or 0) > 0:
        zero_rows = [
            ("cases", f"{int(zero_audit.get('cases') or 0):,}"),
            ("selected tokens", f"{int(zero_audit.get('selected_tokens') or 0):,}"),
            ("audited true noise", f"{int(zero_audit.get('audited_true_noise_tokens') or 0):,}"),
            ("plausibly useful", f"{int(zero_audit.get('plausibly_useful_tokens') or 0):,}"),
            (
                f"rank <= {int(zero_audit.get('high_rank_cutoff') or 0)} missed",
                f"{int(zero_audit.get('high_ranked_missed_expected_files') or 0):,}",
            ),
            (
                "high-ranked strong evidence",
                f"{int(zero_audit.get('high_ranked_strong_evidence_files') or 0):,}",
            ),
        ]
        zero_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        zero_table.add_column("metric", style="dim")
        zero_table.add_column("value", justify="right")
        for name, value in zero_rows:
            zero_table.add_row(name, value)
        console.print("\n[bold]Zero Expected Selected Audit[/]")
        console.print(zero_table)

        zero_count_groups = [
            ("Zero Miss Status Buckets", zero_audit.get("miss_status_counts") or {}),
            ("Zero Selected Token Families", zero_audit.get("selected_family_tokens") or {}),
            ("Zero Cap Block Reasons", zero_audit.get("cap_block_reason_counts") or {}),
            ("Zero Guarded MAV Reasons", zero_audit.get("guarded_mav_reason_counts") or {}),
        ]
        for title, counts in zero_count_groups:
            if counts:
                console.print(f"\n[bold]{title}[/]")
                for name, count in list(counts.items())[:8]:
                    console.print(f"  {name}: {count}")

        zero_cases = zero_audit.get("top_cases") or []
        if zero_cases:
            top_zero = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
            top_zero.add_column("selected", justify="right")
            top_zero.add_column("true", justify="right")
            top_zero.add_column("plausible", justify="right")
            top_zero.add_column("top miss")
            top_zero.add_column("status")
            top_zero.add_column("selected mix")
            top_zero.add_column("task")
            for item in zero_cases[:8]:
                top_zero.add_row(
                    f"{int(item.get('selected_tokens') or 0):,}",
                    f"{int(item.get('audited_true_noise_tokens') or 0):,}",
                    f"{int(item.get('plausibly_useful_tokens') or 0):,}",
                    str(item.get("top_miss_path") or ""),
                    str(item.get("top_miss_status") or ""),
                    str(item.get("selected_family_mix") or ""),
                    str(item.get("task") or ""),
                )
            console.print("\n[bold]Top Zero-Expected Cases[/]")
            console.print(top_zero)

    failures = report.get("failure_type_counts") or {}
    if failures:
        console.print("\n[bold]Failure Types[/]")
        for name, count in failures.items():
            console.print(f"  {name}: {count}")

    heuristic_reasons = (report.get("heuristic_prune") or {}).get("prune_reason_counts") or {}
    if heuristic_reasons:
        console.print("\n[bold]Heuristic Prune Reasons[/]")
        for name, count in heuristic_reasons.items():
            console.print(f"  {name}: {count}")

    for excerpt_key, excerpt_title in (
        ("fixed_selected_excerpt_projection", "Fixed Selected Excerpt Projection"),
        ("guarded_fixed_selected_excerpt_projection", "Guarded Fixed Selected Excerpt Projection"),
        ("label_free_tiered_excerpt_projection", "Label-Free Tiered Excerpt Projection"),
        ("weak_only_excerpt_projection", "Weak-Only Excerpt Projection"),
        ("weak_action_mismatch_excerpt_projection", "Weak + Action-Mismatch Excerpt Projection"),
        ("strong_carrier_excerpt_projection", "Strong-Carrier Excerpt Projection"),
        ("guarded_strong_carrier_excerpt_projection", "Guarded Strong-Carrier Excerpt Projection"),
        ("weak_plus_strong_carrier_excerpt_projection", "Weak + Strong-Carrier Excerpt Projection"),
        ("weak_plus_guarded_strong_carrier_excerpt_projection", "Weak + Guarded Strong-Carrier Excerpt Projection"),
        ("ast_checkpoint_memory_excerpt_projection", "AST Checkpoint-Memory Excerpt Projection"),
        ("ranked_test_skeleton_excerpt_projection", "Ranked Test Skeleton Excerpt Projection"),
        ("ranked_test_symbol_carrier_excerpt_projection", "Ranked Test Symbol-Carrier Excerpt Projection"),
        ("ranked_source_churn_excerpt_projection", "Ranked Source Churn Excerpt Projection"),
        ("ranked_source_metadata_excerpt_projection", "Ranked Source Metadata Excerpt Projection"),
        ("ranked_metadata_summary_excerpt_projection", "Ranked Metadata Summary Excerpt Projection"),
        ("mav_span_excerpt_projection", "MAV Per-Token Span Excerpt Projection"),
        ("neutral_mav_span_excerpt_projection", "Neutral MAV Span Optimizer Projection"),
        ("risk_aware_tiered_excerpt_projection", "Risk-Aware Tiered Excerpt Projection"),
        ("oracle_non_expected_excerpt_ceiling", "Oracle Non-Expected Excerpt Ceiling"),
    ):
        excerpt = report.get(excerpt_key) or {}
        if not excerpt or int(excerpt.get("cases") or 0) <= 0:
            continue
        excerpt_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        excerpt_table.add_column("metric", style="dim")
        excerpt_table.add_column("value", justify="right")
        excerpt_rows = [
            ("policy", str(excerpt.get("policy") or "")),
            ("cases", f"{int(excerpt.get('cases') or 0):,}"),
            ("selected-file-set violations", f"{int(excerpt.get('selected_file_set_violations') or 0):,}"),
            ("projected files", f"{int(excerpt.get('projected_files') or 0):,}"),
            ("removed tokens", f"{int(excerpt.get('removed_tokens') or 0):,}"),
            ("strict noise removed", f"{int(excerpt.get('strict_noise_removed') or 0):,}"),
            ("expected token loss", f"{int(excerpt.get('expected_token_loss') or 0):,}"),
            ("compression profit", f"{int(excerpt.get('compression_profit') or 0):,}"),
            ("expected loss / 1k removed", f"{float(excerpt.get('expected_loss_per_1k_removed') or 0.0):.1f}t"),
            ("baseline aggregate TP", _fmt_report_pct(excerpt.get("baseline_aggregate_token_precision"))),
            ("projected aggregate TP", _fmt_report_pct(excerpt.get("projected_aggregate_token_precision"))),
            ("aggregate TP delta", _fmt_report_pct(excerpt.get("aggregate_token_precision_delta"))),
            ("improved/regressed cases", f"{int(excerpt.get('improved_cases') or 0):,}/{int(excerpt.get('regressed_cases') or 0):,}"),
        ]
        if excerpt.get("oracle_capture_rate") is not None:
            excerpt_rows.append(("oracle capture rate", _fmt_report_pct(excerpt.get("oracle_capture_rate"))))
        if excerpt.get("tier_counts"):
            excerpt_rows.append(("tier counts", _fmt_report_counts(excerpt.get("tier_counts"))))
        if excerpt.get("projected_tier_counts"):
            excerpt_rows.append(("projected tiers", _fmt_report_counts(excerpt.get("projected_tier_counts"))))
        if excerpt.get("removed_tokens_by_tier"):
            excerpt_rows.append(("removed by tier", _fmt_report_counts(excerpt.get("removed_tokens_by_tier"), suffix="t")))
        if excerpt.get("expected_loss_by_tier"):
            excerpt_rows.append(("expected loss by tier", _fmt_report_counts(excerpt.get("expected_loss_by_tier"), suffix="t")))
        for name, value in excerpt_rows:
            excerpt_table.add_row(name, value)
        console.print(f"\n[bold]{excerpt_title}[/]")
        console.print(excerpt_table)
        top_cases = excerpt.get("top_cases") or []
        if top_cases:
            top_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
            top_table.add_column("removed", justify="right")
            top_table.add_column("noise removed", justify="right")
            top_table.add_column("expected loss", justify="right")
            top_table.add_column("TP delta", justify="right")
            top_table.add_column("files", justify="right")
            top_table.add_column("task")
            for item in top_cases[:8]:
                top_table.add_row(
                    f"{int(item.get('removed_tokens') or 0):,}",
                    f"{int(item.get('strict_noise_removed') or 0):,}",
                    f"{int(item.get('expected_token_loss') or 0):,}",
                    _fmt_report_pct(item.get("token_precision_delta")),
                    f"{int(item.get('projected_file_count') or 0):,}",
                    str(item.get("task") or ""),
                )
            console.print(top_table)

    oracle_miss = report.get("oracle_miss_signature_audit") or {}
    if oracle_miss and int(oracle_miss.get("missed_files") or 0) > 0:
        audit_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        audit_table.add_column("metric", style="dim")
        audit_table.add_column("value", justify="right")
        audit_rows = [
            ("policy", str(oracle_miss.get("policy") or "")),
            ("comparator", str(oracle_miss.get("comparator_key") or "")),
            ("missed files", f"{int(oracle_miss.get('missed_files') or 0):,}"),
            ("missed oracle tokens", f"{int(oracle_miss.get('missed_oracle_tokens') or 0):,}"),
            ("signature tokens", _fmt_report_counts(oracle_miss.get("signature_tokens"), suffix="t")),
            ("family tokens", _fmt_report_counts(oracle_miss.get("family_tokens"), suffix="t")),
            ("protected reasons", _fmt_report_counts(oracle_miss.get("protected_reason_counts"))),
        ]
        for name, value in audit_rows:
            audit_table.add_row(name, value)
        console.print("\n[bold]Oracle Miss Signature Audit[/]")
        console.print(audit_table)

        marginal_union = oracle_miss.get("signature_marginal_union") or []
        if marginal_union:
            union_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
            union_table.add_column("signature")
            union_table.add_column("new files", justify="right")
            union_table.add_column("removed", justify="right")
            union_table.add_column("noise removed", justify="right")
            union_table.add_column("expected loss", justify="right")
            union_table.add_column("agg TP delta", justify="right")
            for item in marginal_union[:10]:
                union_table.add_row(
                    str(item.get("signature") or ""),
                    f"{int(item.get('new_files') or 0):,}",
                    f"{int(item.get('removed_tokens') or 0):,}",
                    f"{int(item.get('strict_noise_removed') or 0):,}",
                    f"{int(item.get('expected_token_loss') or 0):,}",
                    _fmt_report_pct(item.get("aggregate_token_precision_delta")),
                )
            console.print("\n[bold]Oracle Signature Marginal Union[/]")
            console.print(union_table)

        top_missed = oracle_miss.get("top_missed") or []
        if top_missed:
            missed_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
            missed_table.add_column("missed", justify="right")
            missed_table.add_column("tier")
            missed_table.add_column("family")
            missed_table.add_column("path")
            missed_table.add_column("signatures")
            missed_table.add_column("protected")
            missed_table.add_column("task")
            for item in top_missed[:10]:
                missed_table.add_row(
                    f"{int(item.get('missed_removed_tokens') or 0):,}",
                    str(item.get("tier") or ""),
                    str(item.get("family") or ""),
                    str(item.get("path") or ""),
                    ", ".join(str(value) for value in item.get("signatures") or []),
                    ", ".join(str(value) for value in item.get("why_protected") or []),
                    str(item.get("task") or ""),
                )
            console.print(missed_table)

    mav = report.get("mav_ablation") or {}
    if mav:
        mav_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        mav_table.add_column("profile")
        mav_table.add_column("threshold", justify="right")
        mav_table.add_column("pruned", justify="right")
        mav_table.add_column("true", justify="right")
        mav_table.add_column("plausible", justify="right")
        mav_table.add_column("purity", justify="right")
        mav_table.add_column("agg TP", justify="right")
        for name in ("guarded_prune", "aggressive_prune"):
            profile = mav.get(name) or {}
            mav_table.add_row(
                name.replace("_", " "),
                f"{float(profile.get('threshold') or 0.0):.1f}",
                f"{int(profile.get('pruned_tokens') or 0):,}",
                f"{int(profile.get('pruned_true_noise_tokens') or 0):,}",
                f"{int(profile.get('pruned_plausibly_useful_tokens') or 0):,}",
                _fmt_report_pct(profile.get("true_noise_purity")),
                _fmt_report_pct(profile.get("projected_aggregate_token_precision")),
            )
        replacement = mav.get("replacement") or {}
        console.print("\n[bold]MAV Offline Ablation[/]")
        console.print(mav_table)
        console.print(
            "  replacement "
            f"accepted={int(replacement.get('accepted_replacements') or 0):,} "
            f"pairs={int(replacement.get('candidate_pairs') or 0):,} "
            f"added_expected={int(replacement.get('added_expected_tokens') or 0):,}t "
            f"removed_true={int(replacement.get('removed_true_noise_tokens') or 0):,}t "
            f"removed_plausible={int(replacement.get('removed_plausibly_useful_tokens') or 0):,}t "
            f"aggTP={_fmt_report_pct(replacement.get('projected_aggregate_token_precision'))}"
        )
        reasons = (mav.get("aggressive_prune") or {}).get("reason_counts") or {}
        if reasons:
            console.print("  aggressive prune reasons: " + ", ".join(f"{name}={count}" for name, count in list(reasons.items())[:8]))

    activation = report.get("activation_gate") or {}
    if activation:
        activation_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        activation_table.add_column("profile")
        activation_table.add_column("pruned", justify="right")
        activation_table.add_column("true", justify="right")
        activation_table.add_column("plausible", justify="right")
        activation_table.add_column("purity", justify="right")
        activation_table.add_column("agg TP", justify="right")
        for name in ("activation_only", "activation_plus_guarded_mav"):
            profile = activation.get(name) or {}
            activation_table.add_row(
                name.replace("_", " "),
                f"{int(profile.get('pruned_tokens') or 0):,}",
                f"{int(profile.get('pruned_true_noise_tokens') or 0):,}",
                f"{int(profile.get('pruned_plausibly_useful_tokens') or 0):,}",
                _fmt_report_pct(profile.get("true_noise_purity")),
                _fmt_report_pct(profile.get("projected_aggregate_token_precision")),
            )
        console.print("\n[bold]Activation Gate Ablation[/]")
        console.print(activation_table)
        reasons = (activation.get("activation_plus_guarded_mav") or {}).get("reason_counts") or {}
        if reasons:
            console.print("  activation reasons: " + ", ".join(f"{name}={count}" for name, count in list(reasons.items())[:8]))
        atom_profiles = ((activation.get("atom_ceiling") or {}).get("profiles") or [])[:4]
        if atom_profiles:
            ceiling = ", ".join(
                f"{int(item.get('atom_tokens') or 0)}t->{_fmt_report_pct(item.get('projected_aggregate_token_precision'))}"
                for item in atom_profiles
            )
            console.print(f"  atom ceiling after activation+MAV prune: {ceiling}")

    ranked_audit = report.get("ranked_skip_audit") or {}
    if ranked_audit:
        audit_rows = [
            ("missed expected files", f"{int(ranked_audit.get('missed_expected_files') or 0):,}"),
            ("ranked missed expected files", f"{int(ranked_audit.get('ranked_missed_expected_files') or 0):,}"),
            (
                f"rank <= {int(ranked_audit.get('high_rank_cutoff') or 0)} missed",
                f"{int(ranked_audit.get('high_ranked_missed_expected_files') or 0):,}",
            ),
            ("high-ranked strong evidence", f"{int(ranked_audit.get('high_ranked_strong_evidence_files') or 0):,}"),
            ("would select with one more slot", f"{int(ranked_audit.get('would_select_with_one_more_slot') or 0):,}"),
        ]
        audit = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        audit.add_column("metric", style="dim")
        audit.add_column("value", justify="right")
        for name, value in audit_rows:
            audit.add_row(name, value)
        console.print("\n[bold]Ranked Expected Skip Audit[/]")
        console.print(audit)

        status_counts = ranked_audit.get("status_counts") or {}
        if status_counts:
            console.print("\n[bold]Ranked Skip Status Buckets[/]")
            for name, count in status_counts.items():
                console.print(f"  {name}: {count}")

        cap_block_counts = ranked_audit.get("cap_block_reason_counts") or {}
        if cap_block_counts:
            console.print("\n[bold]Cap Block Reasons[/]")
            for name, count in cap_block_counts.items():
                console.print(f"  {name}: {count}")

        top_misses = ranked_audit.get("top_ranked_misses") or []
        if top_misses:
            top = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
            top.add_column("rank", justify="right")
            top.add_column("score", justify="right")
            top.add_column("failure")
            top.add_column("status")
            top.add_column("path")
            top.add_column("task")
            for item in top_misses[:8]:
                score = item.get("score")
                top.add_row(
                    str(item.get("rank") or ""),
                    f"{float(score):.1f}" if isinstance(score, (int, float)) else "",
                    str(item.get("failure_type") or ""),
                    str(item.get("status_bucket") or ""),
                    str(item.get("path") or ""),
                    str(item.get("task") or ""),
                )
            console.print("\n[bold]Top Ranked Expected Misses[/]")
            console.print(top)

    correction_cases = report.get("top_correction_cases") or []
    if correction_cases:
        top = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        top.add_column("needed", justify="right")
        top.add_column("TP", justify="right")
        top.add_column("R", justify="right")
        top.add_column("task")
        for item in correction_cases[:8]:
            top.add_row(
                f"{int(item['required_noise_removal']):,}",
                _fmt_report_pct(item.get("token_precision")),
                _fmt_report_pct(item.get("recall")),
                str(item.get("task") or ""),
            )
        console.print("\n[bold]Largest Case Corrections[/]")
        console.print(top)


def _fmt_report_pct(value: Any) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "not measured"


def _fmt_report_counts(value: Any, *, suffix: str = "") -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    parts: list[str] = []
    for name, count in list(value.items())[:6]:
        if isinstance(count, float):
            formatted = f"{count:.1f}"
        elif isinstance(count, int):
            formatted = f"{count:,}"
        else:
            formatted = str(count)
        parts.append(f"{name}={formatted}{suffix}")
    return ", ".join(parts) if parts else "-"


def _language_mix(root: Path) -> dict[str, float]:
    counts: dict[str, int] = {}
    suffix_map = {
        ".py": "Python",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".go": "Go",
        ".java": "Java",
        ".rs": "Rust",
        ".rb": "Ruby",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".toml": "TOML",
    }
    for path in root.rglob("*"):
        if not path.is_file() or _anonymous_skip_path(path, root):
            continue
        language = suffix_map.get(path.suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {
        language: round(count / total, 4)
        for language, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    }


def _anonymous_skip_path(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    parts = set(rel.parts)
    return bool(parts & {".git", ".agentpack", "node_modules", ".venv", "__pycache__", "dist", "build"})


def _git_remote_public(root: Path) -> bool:
    try:
        remotes = _git_lines(root, ["remote", "-v"])
    except subprocess.CalledProcessError:
        return False
    return any("github.com" in line or "gitlab.com" in line for line in remotes)


def _random_baseline(
    packable_paths: list[str],
    packable_tokens: dict[str, int],
    expected_files: list[str],
    budget: int,
) -> tuple[list[str], float, float, float]:
    """Random file selection at same budget. Returns (selected, precision, recall, f1)."""
    shuffled = list(packable_paths)
    random.shuffle(shuffled)
    selected: list[str] = []
    used = 0
    for p in shuffled:
        tok = packable_tokens.get(p, 50)
        if used + tok <= budget:
            selected.append(p)
            used += tok

    expected = set(expected_files)
    sel_set = set(selected)
    if not expected or not sel_set:
        return selected, 0.0, 0.0, 0.0
    tp = len(sel_set & expected)
    p = tp / len(sel_set)
    r = tp / len(expected)
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return selected, p, r, f1


def _run_case(root: Path, case: BenchmarkCase) -> CaseResult:
    from agentpack.application.pack_service import PackPlanner, PackRequest, _sf_tokens
    from agentpack.core.config import load_config

    cfg = load_config(root)

    request = PackRequest(
        root=root,
        agent="generic",
        task=case.task,
        mode=case.mode,
        since=None,
        refresh=False,
        workspace=case.workspace,
        budget=case.budget,
    )

    t0 = time.perf_counter()
    plan = PackPlanner().plan(request)
    total_s = time.perf_counter() - t0

    packed_tokens = sum(_sf_tokens(sf) for sf in plan.selected)
    raw_tokens = sum(f.estimated_tokens for f in plan.scan_result.all_files)
    after_ignore_tokens = sum(f.estimated_tokens for f in plan.scan_result.packable)
    saving_pct = (1 - packed_tokens / raw_tokens) * 100 if raw_tokens > 0 else 0.0
    saving_pct_honest = (1 - packed_tokens / after_ignore_tokens) * 100 if after_ignore_tokens > 0 else 0.0

    selected_paths = [sf.path for sf in plan.selected]
    selected_set = set(selected_paths)
    selected_tokens = {sf.path: _sf_tokens(sf) for sf in plan.selected}
    selected_modes = {sf.path: _selected_mode(sf) for sf in plan.selected}

    changed_covered = len(plan.all_changed & selected_set)
    changed_total = len(plan.all_changed)

    # Rank@K: min rank in scored list to cover all expected files
    rank_at_k: int | None = None
    candidate_recall_at_20: float | None = None
    candidate_recall_at_50: float | None = None
    candidate_recall_at_100: float | None = None
    candidate_precision_at_3: float | None = None
    candidate_precision_at_5: float | None = None
    noise_pct: float | None = None
    low_budget_extra_file_waste: int | None = None
    precision_delta_if_drop_last_summary: float | None = None
    expected_token_coverage: float | None = None
    selected_family_tokens: dict[str, int] = {}
    selected_family_waste_tokens: dict[str, int] = {}
    reason_family_precision: dict[str, dict[str, float]] = {}
    failure_type_counts: dict[str, int] = {}
    top_candidates: list[dict[str, Any]] = []
    selection_diagnostics: dict[str, Any] = {}
    rand_p = rand_r = rand_f1 = None

    if case.expected_files:
        expected_set = set(case.expected_files)
        ranked_scored = sorted(plan.scored, key=lambda item: item[1], reverse=True)
        scored_paths = [fi.path for fi, _score, _reasons in ranked_scored]
        candidate_recall_at_20 = _candidate_recall_at(scored_paths, expected_set, 20)
        candidate_recall_at_50 = _candidate_recall_at(scored_paths, expected_set, 50)
        candidate_recall_at_100 = _candidate_recall_at(scored_paths, expected_set, 100)
        candidate_precision_at_3 = _candidate_precision_at(scored_paths, expected_set, 3)
        candidate_precision_at_5 = _candidate_precision_at(scored_paths, expected_set, 5)
        scored_map = {
            fi.path: {
                "rank": rank,
                "score": score,
                "reasons": reasons,
                "estimated_tokens": int(getattr(fi, "estimated_tokens", 0) or 0),
            }
            for rank, (fi, score, reasons) in enumerate(ranked_scored, 1)
        }
        top_candidates = _top_candidate_diagnostics(
            ranked_scored=ranked_scored,
            selected_set=selected_set,
            expected_set=expected_set,
        )
        selection_v2_evidence = _selection_v2_evidence_diagnostics(
            ranked_scored=ranked_scored,
            task=case.task,
            summaries=plan.summaries,
            keyword_plan=plan.keyword_plan,
            dependency_graph=plan.dep_graph,
            changed_paths=plan.all_changed,
            action_owner_files=set(case.action_owner_files),
            required_support_files=set(case.required_support_files),
            incidental_changed_files=set(case.incidental_changed_files),
            optional_context_files=set(case.optional_context_files),
        )
        all_file_map = {fi.path: fi for fi in plan.scan_result.all_files}
        receipt_map = {receipt.path: receipt.reason for receipt in plan.receipts}
        found: set[str] = set()
        for k, path in enumerate(scored_paths, 1):
            if path in expected_set:
                found.add(path)
            if found >= expected_set:
                rank_at_k = k
                break

        expected_tokens = sum(selected_tokens.get(p, 0) for p in selected_set & expected_set)
        noise_pct = (1 - expected_tokens / packed_tokens) * 100 if packed_tokens > 0 else 0.0
        expected_total_tokens = sum(
            all_file_map[p].estimated_tokens
            for p in expected_set
            if p in all_file_map and getattr(all_file_map[p], "estimated_tokens", 0) > 0
        )
        expected_token_coverage = expected_tokens / expected_total_tokens if expected_total_tokens > 0 else None
        selected_family_tokens = _selected_family_tokens(selected_paths, selected_tokens)
        selected_family_waste_tokens = _selected_family_tokens(
            [path for path in selected_paths if path not in expected_set],
            selected_tokens,
        )
        reason_family_precision = _reason_family_precision(plan.selected, expected_set)
        selected_by_path = {sf.path: sf for sf in plan.selected}
        selected_noise = _selected_noise_diagnostics(
            selected_paths=selected_paths,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            scored_map=scored_map,
            expected_set=expected_set,
        )
        low_budget_extra_file_waste, precision_delta_if_drop_last_summary = _low_budget_extra_file_waste(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            expected_files=expected_set,
            packed_tokens=packed_tokens,
            expected_tokens=expected_tokens,
            budget=case.budget or cfg.context.default_budget,
            changed_files_source=plan.changed_files_source,
        )

        packable_paths = [f.path for f in plan.scan_result.packable]
        packable_token_map = {f.path: f.estimated_tokens for f in plan.scan_result.packable}
        budget = case.budget or cfg.context.default_budget
        _, rand_p, rand_r, rand_f1 = _random_baseline(packable_paths, packable_token_map, case.expected_files, budget)

        missed_expected = []
        for expected_path in sorted(expected_set - selected_set):
            fi = all_file_map.get(expected_path)
            scored_info = scored_map.get(expected_path)
            status = _miss_status(
                fi=fi,
                expected_path=expected_path,
                receipt_map=receipt_map,
                scored_info=scored_info,
                changed_files_source=plan.changed_files_source,
            )
            failure_type = _miss_failure_type(
                fi=fi,
                scored_info=scored_info,
                status=status,
                selected_count=len(selected_paths),
            )
            failure_type_counts[failure_type] = failure_type_counts.get(failure_type, 0) + 1
            missed_expected.append({
                "path": expected_path,
                "status": status,
                "failure_type": failure_type,
                "family": _path_family(expected_path),
                "rank": scored_info["rank"] if scored_info else None,
                "score": round(scored_info["score"], 1) if scored_info else None,
                "reasons": scored_info["reasons"][:4] if scored_info else [],
                "basis": plan.changed_files_source,
                "would_select_with_one_more_slot": _would_select_with_one_more_slot(
                    scored_info=scored_info,
                    selected_count=len(selected_paths),
                    status=status,
                ),
                "score_delta_vs_last_selected": _score_delta_vs_last_selected(
                    scored_info=scored_info,
                    selected_paths=selected_paths,
                    scored_map=scored_map,
                ),
                "selected_noise_file_that_beat_expected": _selected_noise_that_beat_expected(
                    scored_info=scored_info,
                    selected_noise=selected_noise,
                ),
                "cap_block_diagnostic": _cap_block_diagnostic(
                    status=status,
                    fi=fi,
                    scored_info=scored_info,
                    summaries=plan.summaries,
                    selected_by_path=selected_by_path,
                    selected_tokens=selected_tokens,
                    expected_set=expected_set,
                    packed_tokens=packed_tokens,
                    budget=budget,
                ),
            })
        plausibly_useful_noise = _plausibly_useful_selected_noise(
            selected_noise=selected_noise,
            expected_set=expected_set,
            scored_map=scored_map,
        )
        excerpt_projection = _fixed_selected_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
        )
        guarded_excerpt_projection = _fixed_selected_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            guarded=True,
        )
        oracle_excerpt_ceiling = _oracle_non_expected_excerpt_ceiling(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
        )
        tiered_excerpt_projection = _label_free_tiered_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
        )
        weak_only_excerpt_projection = _label_free_tiered_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            policy="weak_only_source_excerpt_v1",
        )
        weak_action_mismatch_excerpt_projection = _label_free_tiered_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            policy="weak_action_mismatch_source_excerpt_v1",
        )
        strong_carrier_excerpt_projection = _label_free_tiered_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            policy="strong_carrier_source_excerpt_v1",
        )
        guarded_strong_carrier_excerpt_projection = _label_free_tiered_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            policy="guarded_strong_carrier_source_excerpt_v1",
        )
        weak_plus_strong_carrier_excerpt_projection = _label_free_tiered_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            policy="weak_plus_strong_carrier_source_excerpt_v1",
        )
        weak_plus_guarded_strong_carrier_excerpt_projection = _label_free_tiered_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            policy="weak_plus_guarded_strong_carrier_source_excerpt_v1",
        )
        ast_checkpoint_memory_excerpt_projection = _ast_checkpoint_memory_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            summaries=plan.summaries,
            task=case.task,
            changed_paths=plan.all_changed,
            scored_map=scored_map,
        )
        ranked_test_skeleton_excerpt_projection = _ranked_test_skeleton_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            scored_map=scored_map,
        )
        ranked_test_symbol_carrier_excerpt_projection = _ranked_test_symbol_carrier_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            scored_map=scored_map,
        )
        ranked_source_churn_excerpt_projection = _ranked_source_churn_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            scored_map=scored_map,
        )
        ranked_source_metadata_excerpt_projection = _ranked_source_metadata_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            scored_map=scored_map,
        )
        ranked_metadata_summary_excerpt_projection = _ranked_metadata_summary_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            scored_map=scored_map,
        )
        mav_span_excerpt_projection = _mav_span_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            summaries=plan.summaries,
            task=case.task,
            changed_paths=plan.all_changed,
        )
        neutral_mav_span_excerpt_projection = _neutral_mav_span_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            summaries=plan.summaries,
            task=case.task,
            changed_paths=plan.all_changed,
        )
        risk_aware_excerpt_projection = _label_free_tiered_excerpt_projection(
            selected=plan.selected,
            selected_tokens=selected_tokens,
            selected_modes=selected_modes,
            expected_set=expected_set,
            file_by_path=all_file_map,
            task=case.task,
            changed_paths=plan.all_changed,
            policy="risk_aware_tiered_source_excerpt_v1",
        )
        intent_profile = _benchmark_intent_profile(
            task=case.task,
            expected_files=expected_set,
            missed_expected=missed_expected,
            selected_noise=selected_noise,
        )
        selection_diagnostics = {
            "selection_v2": {"evidence": selection_v2_evidence},
            "intent_profile": intent_profile,
            "selected_noise": selected_noise[:10],
            "selected_noise_family_tokens": selected_family_waste_tokens,
            "expected_ranked_not_selected": sum(1 for miss in missed_expected if miss["rank"] is not None),
            "missed_expected_count": len(missed_expected),
            "replacement_pairs": _replacement_pair_diagnostics(plan.receipts, scored_map, selected_tokens),
            "same_scope_replacement_opportunities": _same_scope_replacement_opportunities(
                missed_expected=missed_expected,
                selected_noise=selected_noise,
                scored_map=scored_map,
            ),
            "selected_not_expected_but_plausibly_useful": plausibly_useful_noise,
            "label_audit": _label_audit_summary(
                selected_noise=selected_noise,
                plausibly_useful=plausibly_useful_noise,
                packed_tokens=packed_tokens,
            ),
            "fixed_selected_excerpt_projection": excerpt_projection,
            "guarded_fixed_selected_excerpt_projection": guarded_excerpt_projection,
            "oracle_non_expected_excerpt_ceiling": oracle_excerpt_ceiling,
            "label_free_tiered_excerpt_projection": tiered_excerpt_projection,
            "weak_only_excerpt_projection": weak_only_excerpt_projection,
            "weak_action_mismatch_excerpt_projection": weak_action_mismatch_excerpt_projection,
            "strong_carrier_excerpt_projection": strong_carrier_excerpt_projection,
            "guarded_strong_carrier_excerpt_projection": guarded_strong_carrier_excerpt_projection,
            "weak_plus_strong_carrier_excerpt_projection": weak_plus_strong_carrier_excerpt_projection,
            "weak_plus_guarded_strong_carrier_excerpt_projection": weak_plus_guarded_strong_carrier_excerpt_projection,
            "ast_checkpoint_memory_excerpt_projection": ast_checkpoint_memory_excerpt_projection,
            "ranked_test_skeleton_excerpt_projection": ranked_test_skeleton_excerpt_projection,
            "ranked_test_symbol_carrier_excerpt_projection": ranked_test_symbol_carrier_excerpt_projection,
            "ranked_source_churn_excerpt_projection": ranked_source_churn_excerpt_projection,
            "ranked_source_metadata_excerpt_projection": ranked_source_metadata_excerpt_projection,
            "ranked_metadata_summary_excerpt_projection": ranked_metadata_summary_excerpt_projection,
            "mav_span_excerpt_projection": mav_span_excerpt_projection,
            "neutral_mav_span_excerpt_projection": neutral_mav_span_excerpt_projection,
            "risk_aware_tiered_excerpt_projection": risk_aware_excerpt_projection,
            "owner_file_recall": _owner_file_recall(selected_set=selected_set, expected_set=expected_set),
            "expected_family_recall": _expected_family_recall(selected_set=selected_set, expected_set=expected_set),
            "expected_include_modes": _expected_include_mode_diagnostics(
                expected_set=expected_set,
                selected_modes=selected_modes,
            ),
            "expected_rank_distribution": _expected_rank_distribution(expected_set, scored_map),
            "package_boundary": _package_boundary_diagnostics(
                selected_paths=selected_paths,
                expected_set=expected_set,
            ),
        }
        ownership_metrics = _ownership_metrics(case, selected_set)
        if ownership_metrics is not None:
            selection_diagnostics["ownership_metrics"] = ownership_metrics
    else:
        missed_expected = []

    selected_skills, skill_token_cost = _route_skills_for_case(root, case)
    skill_recall, skill_precision, skill_mrr, skill_noise = _skill_metrics(
        selected_skills,
        expected_skills=case.expected_skills,
        avoid_skills=case.avoid_skills,
    )

    return CaseResult(
        case=case,
        packed_tokens=packed_tokens,
        raw_tokens=raw_tokens,
        after_ignore_tokens=after_ignore_tokens,
        saving_pct=saving_pct,
        saving_pct_honest=saving_pct_honest,
        selected_paths=selected_paths,
        selected_tokens=selected_tokens,
        selected_modes=selected_modes,
        changed_covered=changed_covered,
        changed_total=changed_total,
        total_s=total_s,
        phase_times=plan.phase_times,
        rank_at_k=rank_at_k,
        candidate_recall_at_20=candidate_recall_at_20,
        candidate_recall_at_50=candidate_recall_at_50,
        candidate_recall_at_100=candidate_recall_at_100,
        candidate_precision_at_3=candidate_precision_at_3,
        candidate_precision_at_5=candidate_precision_at_5,
        low_budget_extra_file_waste=low_budget_extra_file_waste,
        precision_delta_if_drop_last_summary=precision_delta_if_drop_last_summary,
        expected_token_coverage=expected_token_coverage,
        selected_family_tokens=selected_family_tokens,
        selected_family_waste_tokens=selected_family_waste_tokens,
        reason_family_precision=reason_family_precision,
        failure_type_counts=failure_type_counts,
        noise_pct=noise_pct,
        random_precision=rand_p,
        random_recall=rand_r,
        random_f1=rand_f1,
        selected_skills=selected_skills,
        skill_recall_at_3=skill_recall,
        skill_precision_at_3=skill_precision,
        skill_mrr=skill_mrr,
        skill_noise_rate=skill_noise,
        missed_expected=missed_expected,
        top_candidates=top_candidates,
        selection_diagnostics=selection_diagnostics,
    )


def _candidate_recall_at(scored_paths: list[str], expected_files: set[str], k: int) -> float:
    if not expected_files:
        return 0.0
    return len(set(scored_paths[:k]) & expected_files) / len(expected_files)


def _candidate_precision_at(scored_paths: list[str], expected_files: set[str], k: int) -> float:
    candidates = scored_paths[:k]
    if not candidates:
        return 0.0
    return len(set(candidates) & expected_files) / len(candidates)


def _selection_v2_evidence_diagnostics(
    *,
    ranked_scored: list[tuple[Any, float, list[str]]],
    task: str,
    summaries: dict[str, Any],
    keyword_plan: Any,
    dependency_graph: Any,
    changed_paths: set[str],
    action_owner_files: set[str],
    required_support_files: set[str],
    incidental_changed_files: set[str],
    optional_context_files: set[str],
) -> dict[str, Any]:
    """Build benchmark-only evidence traces; labels score but never construct evidence."""

    from agentpack.analysis.owner_features import build_owner_case_context, extract_owner_features
    from agentpack.analysis.ownership import build_candidate_evidence
    from agentpack.core.selection_models import adapt_ranked_candidate

    labeled_paths = (
        action_owner_files
        | required_support_files
        | incidental_changed_files
        | optional_context_files
    )
    candidates = [adapt_ranked_candidate(file_info, score, reasons) for file_info, score, reasons in ranked_scored]
    owner_context = build_owner_case_context(task, keyword_plan, candidates, summaries)
    rows: list[dict[str, Any]] = []
    evidence_by_path: dict[str, Any] = {}
    protection_misclassifications: list[dict[str, Any]] = []
    for rank, ((file_info, score, reasons), candidate) in enumerate(zip(ranked_scored, candidates), start=1):
        owner_features = extract_owner_features(candidate, summaries.get(candidate.path), owner_context)
        evidence = build_candidate_evidence(
            candidate,
            task=task,
            summary=summaries.get(candidate.path),
            owner_context=owner_context,
            owner_features=owner_features,
            dependency_graph=dependency_graph,
            changed_paths=changed_paths,
        )
        evidence_by_path[candidate.path] = evidence
        legacy_owner_strength = _legacy_independent_owner_strength(
            candidate=candidate,
            task=task,
            summary=summaries.get(candidate.path),
        )
        expected_protections = _benchmark_expected_protections(
            path=candidate.path,
            reasons=reasons,
            task=task,
            changed_paths=changed_paths,
        )
        missing_protections = sorted(expected_protections - set(evidence.protections))
        if missing_protections:
            protection_misclassifications.append({
                "path": candidate.path,
                "rank": rank,
                "missing_protections": missing_protections,
            })
        if rank > 200 and candidate.path not in labeled_paths:
            continue
        label = None
        if candidate.path in action_owner_files:
            label = "action_owner"
        elif candidate.path in required_support_files:
            label = "required_support"
        elif candidate.path in incidental_changed_files:
            label = "incidental_changed"
        elif candidate.path in optional_context_files:
            label = "optional_context"
        rows.append({
            "path": candidate.path,
            "rank": rank,
            "score": round(candidate.score, 1),
            "owner_strength": evidence.owner_strength,
            "legacy_owner_strength": legacy_owner_strength,
            "support_strength": evidence.support_strength,
            "carrier_strength": evidence.carrier_strength,
            "codes": list(evidence.codes),
            "protections": list(evidence.protections),
            "owner_features": {
                "anchor_codes": list(owner_features.anchor_codes),
                "corroboration_codes": list(owner_features.corroboration_codes),
                "penalty_codes": list(owner_features.penalty_codes),
                "matched_task_objects": list(owner_features.matched_task_objects),
                "competing_anchor_count": owner_features.competing_anchor_count,
            },
            "label": label,
        })

    def _label_recall(paths: set[str], field: str) -> float | None:
        if not paths:
            return None
        found = sum(
            1
            for path in paths
            if getattr(evidence_by_path.get(path), field, 0) >= 2
        )
        return found / len(paths)

    protected_rows = [row for row in rows if row["protections"]]
    return {
        "policy": "comparative_owner_evidence_v2",
        "rule_version": 2,
        "case_context": {
            "task_objects": list(owner_context.task_objects),
            "scope_terms": list(owner_context.scope_terms),
            "literal_phrases": list(owner_context.literal_phrases),
            "anchor_counts": [list(item) for item in owner_context.anchor_counts],
        },
        "candidate_count": len(ranked_scored),
        "emitted_candidate_count": len(rows),
        "owner_label_recall": _label_recall(action_owner_files, "owner_strength"),
        "support_label_recall": _label_recall(required_support_files, "support_strength"),
        "protected_candidate_count": len(protected_rows),
        "protected_file_misclassifications": len(protection_misclassifications),
        "protection_misclassification_examples": protection_misclassifications[:10],
        "candidates": rows,
    }


def _legacy_independent_owner_strength(*, candidate: Any, task: str, summary: Any) -> int:
    """Reproduce the pre-calibration owner rule for benchmark-only regression comparison."""

    reasons = tuple(reason.lower() for reason in candidate.legacy_reasons)
    dump = getattr(summary, "model_dump", None)
    summary_data = summary if isinstance(summary, dict) else dump() if callable(dump) else {}
    task_terms = set(re.findall(r"[a-z0-9]+", task.lower().replace("_", "-")))
    path_terms = set(re.findall(r"[a-z0-9]+", candidate.path.lower().replace("_", "-")))
    summary_values: list[str] = []
    for key in ("role", "domain", "defines", "entrypoints", "public_api", "ranking_keywords"):
        value = summary_data.get(key)
        if isinstance(value, str):
            summary_values.append(value)
        elif isinstance(value, list):
            summary_values.extend(str(item) for item in value)
    summary_terms = set(re.findall(r"[a-z0-9]+", " ".join(summary_values).lower().replace("_", "-")))
    explicit = any(
        marker in reason
        for reason in reasons
        for marker in ("filename keyword match", "conventional scope path match", "multi-term path match")
    )
    corroborated = explicit or bool(task_terms & path_terms) or bool(task_terms & path_terms & summary_terms)
    definition = any("matched define:" in reason or "multi-token defines match" in reason for reason in reasons)
    literal = any("literal definition match:" in reason for reason in reasons)
    entrypoint = any("matched entrypoint:" in reason for reason in reasons)
    role = any("implementation role match" in reason or "matched role keyword:" in reason for reason in reasons)
    if (definition or literal or entrypoint) and corroborated:
        return 3
    if role and corroborated:
        return 2
    if definition or literal or entrypoint:
        return 1
    return 0


def _benchmark_expected_protections(
    *,
    path: str,
    reasons: list[str],
    task: str,
    changed_paths: set[str],
) -> set[str]:
    """Independently audit safety signals consumed by typed ownership inference."""

    lowered_reasons = "\n".join(reasons).lower()
    lowered_path = path.lower()
    name = Path(lowered_path).name
    protections: set[str] = set()
    if path in changed_paths:
        protections.add("changed")
    if "episodic memory similar task" in lowered_reasons or "learning feedback miss" in lowered_reasons:
        protections.add("memory_confirmed")
    release_path = (
        name in {"changelog.md", "package.json", "pom.xml", "pyproject.toml", "version.go"}
        or "changelog" in name
        or "version" in name
    )
    release_signal = (
        "release/version metadata" in lowered_reasons
        or "build/dependency metadata" in lowered_reasons
    )
    if release_path and release_signal:
        protections.add("release_metadata")
    if set(Path(lowered_path).parts) & {"build", "coverage", "dist", "generated", "__generated__", "vendor"}:
        protections.add("generated")
    if "secret redaction candidate" in lowered_reasons:
        protections.add("redaction_sensitive")
    if (
        _is_test_path(path)
        and _benchmark_task_is_explicit_test(task)
        and "explicit test task file" in lowered_reasons
    ):
        protections.add("explicit_task_test")
    return protections


def _benchmark_task_is_explicit_test(task: str) -> bool:
    lowered = task.strip().lower()
    return (
        lowered.startswith(("test", "add test", "add missing validation test"))
        or "regression test" in lowered
        or "(test)" in lowered
        or "refactor(test)" in lowered
    )


def _top_candidate_diagnostics(
    *,
    ranked_scored: list[tuple[Any, float, list[str]]],
    selected_set: set[str],
    expected_set: set[str],
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, (fi, score, reasons) in enumerate(ranked_scored[:limit], 1):
        path = str(getattr(fi, "path", ""))
        rows.append({
            "path": path,
            "rank": rank,
            "score": round(score, 1),
            "family": _path_family(path),
            "selected": path in selected_set,
            "expected": path in expected_set,
            "reasons": reasons[:4],
        })
    return rows


def _selected_noise_diagnostics(
    *,
    selected_paths: list[str],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    scored_map: dict[str, dict[str, Any]],
    expected_set: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in selected_paths:
        if path in expected_set:
            continue
        scored_info = scored_map.get(path)
        rows.append({
            "path": path,
            "family": _path_family(path),
            "tokens": selected_tokens.get(path, 0),
            "mode": selected_modes.get(path),
            "rank": scored_info["rank"] if scored_info else None,
            "score": round(scored_info["score"], 1) if scored_info else None,
            "reasons": scored_info["reasons"][:4] if scored_info else [],
        })
    return rows


_EXCERPT_STOPWORDS = {
    "add",
    "and",
    "are",
    "but",
    "can",
    "fix",
    "for",
    "from",
    "has",
    "into",
    "not",
    "set",
    "the",
    "this",
    "use",
    "when",
    "with",
}


def _fixed_selected_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    task: str,
    changed_paths: set[str],
    max_excerpt_tokens: int = 160,
    guarded: bool = False,
) -> dict[str, Any]:
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0:
            continue
        reasons = [str(reason) for reason in (getattr(sf, "reasons", None) or [])]
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        if guarded and not _source_excerpt_guarded_candidate(path=path, mode=mode, reasons=reasons):
            continue
        projection = _source_excerpt_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=max_excerpt_tokens,
        )
        if projection is None:
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            continue
        projected_tokens[path] = projected
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": selected_modes.get(path),
            "expected": path in expected_set,
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": current_tokens - projected,
            "reason": projection["reason"],
            "matched_terms": projection["matched_terms"][:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": "guarded_fixed_selected_source_excerpt_v1" if guarded else "fixed_selected_source_excerpt_v1",
        "max_excerpt_tokens": max_excerpt_tokens,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "projected_file_count": len(rows),
        "projected_files": rows[:10],
        "expected_loss_files": expected_loss_rows[:10],
    }


def _oracle_non_expected_excerpt_ceiling(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    task: str,
    changed_paths: set[str],
    max_excerpt_tokens: int = 80,
    minimal_excerpt_tokens: int = 24,
) -> dict[str, Any]:
    """Diagnostic ceiling only: labels freeze expected files and shrink non-expected files."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0 or path in expected_set:
            continue
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        projection = _source_excerpt_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=max_excerpt_tokens,
        )
        if projection is None:
            projection = _minimal_source_excerpt_projection(
                file_info=file_by_path.get(path),
                current_tokens=current_tokens,
                path=path,
                max_excerpt_tokens=max_excerpt_tokens,
                minimal_excerpt_tokens=minimal_excerpt_tokens,
            )
        if projection is None:
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            continue
        projected_tokens[path] = projected
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": selected_modes.get(path),
            "expected": False,
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": current_tokens - projected,
            "reason": projection["reason"],
            "matched_terms": projection.get("matched_terms", [])[:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    return {
        "policy": "oracle_non_expected_excerpt_ceiling_v1",
        "oracle_uses_expected_labels": True,
        "max_excerpt_tokens": max_excerpt_tokens,
        "minimal_excerpt_tokens": minimal_excerpt_tokens,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "projected_file_count": len(rows),
        "projected_files": rows[:10],
        "expected_loss_files": [],
    }


def _label_free_tiered_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    task: str,
    changed_paths: set[str],
    policy: str = "label_free_tiered_source_excerpt_v1",
) -> dict[str, Any]:
    """Production-shaped diagnostic: tier selected files without expected labels."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    tier_counts: Counter[str] = Counter()
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0:
            continue
        reasons = [str(reason) for reason in (getattr(sf, "reasons", None) or [])]
        symbols = getattr(sf, "symbols", None) or []
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        confidence = _source_excerpt_confidence_tier(
            path=path,
            mode=mode,
            reasons=reasons,
            current_tokens=current_tokens,
            changed_paths=changed_paths,
            symbols=symbols,
        )
        tier = str(confidence["tier"])
        report_tier = str(confidence.get("role_tier") or tier)
        tier_counts[report_tier] += 1
        should_compress, compression_reasons = _source_excerpt_should_compress(
            confidence=confidence,
            policy=policy,
        )
        if not should_compress:
            continue

        if bool(confidence.get("strong_carrier")):
            projection = _source_excerpt_projection(
                selected_file=sf,
                file_info=file_by_path.get(path),
                current_tokens=current_tokens,
                mode=mode,
                task=task,
                changed_paths=changed_paths,
                max_excerpt_tokens=140,
            )
            if projection is None:
                projection = _minimal_source_excerpt_projection(
                    file_info=file_by_path.get(path),
                    current_tokens=current_tokens,
                    path=path,
                    max_excerpt_tokens=140,
                    minimal_excerpt_tokens=48,
                )
        elif tier == "medium":
            projection = _source_excerpt_projection(
                selected_file=sf,
                file_info=file_by_path.get(path),
                current_tokens=current_tokens,
                mode=mode,
                task=task,
                changed_paths=changed_paths,
                max_excerpt_tokens=120,
            )
        else:
            projection = _source_excerpt_projection(
                selected_file=sf,
                file_info=file_by_path.get(path),
                current_tokens=current_tokens,
                mode=mode,
                task=task,
                changed_paths=changed_paths,
                max_excerpt_tokens=80,
            )
            if projection is None:
                projection = _minimal_source_excerpt_projection(
                    file_info=file_by_path.get(path),
                    current_tokens=current_tokens,
                    path=path,
                    max_excerpt_tokens=80,
                    minimal_excerpt_tokens=24,
                )

        if projection is None:
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        projected_tier_counts[report_tier] += 1
        removed_by_tier[report_tier] += removed
        if is_expected:
            expected_loss_by_tier[report_tier] += removed
        else:
            strict_noise_removed_by_tier[report_tier] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": selected_modes.get(path),
            "expected": is_expected,
            "tier": report_tier,
            "base_tier": tier,
            "strong_action_owner": bool(confidence.get("strong_action_owner")),
            "strong_carrier": bool(confidence.get("strong_carrier")),
            "guarded_strong_carrier": bool(confidence.get("guarded_strong_carrier")),
            "confidence_score": round(float(confidence["score"]), 1),
            "tier_reasons": confidence["reasons"][:6],
            "carrier_reasons": confidence.get("carrier_reasons", [])[:6],
            "compression_reasons": compression_reasons[:6],
            "action_mismatch": bool(confidence.get("action_mismatch")),
            "structural_risk": bool(confidence.get("structural_risk")),
            "medium_compression_safe": bool(confidence.get("medium_compression_safe")),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "matched_terms": projection.get("matched_terms", [])[:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "projected_file_count": len(rows),
        "tier_counts": dict(tier_counts.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "projected_files": rows[:10],
        "expected_loss_files": expected_loss_rows[:10],
    }


def _ast_checkpoint_memory_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    summaries: dict[str, Any],
    task: str,
    changed_paths: set[str],
    scored_map: dict[str, dict[str, Any]] | None = None,
    policy: str = "ast_checkpoint_memory_source_excerpt_v1",
) -> dict[str, Any]:
    """Diagnostic: project selected files through AST spans and checkpoint-style summary evidence."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    tier_counts: Counter[str] = Counter()
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    memory_signal_selected_files = 0
    memory_signal_projected_files = 0

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0:
            continue
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        summary_data = _ast_checkpoint_summary_dict(summaries.get(path))
        symbols = list(getattr(sf, "symbols", None) or []) or _ast_checkpoint_summary_symbols(summary_data)
        scored_info = (scored_map or {}).get(path)
        reasons = _benchmark_combined_reasons(sf, scored_info)
        confidence = _source_excerpt_confidence_tier(
            path=path,
            mode=mode,
            reasons=reasons,
            current_tokens=current_tokens,
            changed_paths=changed_paths,
            symbols=symbols,
        )
        profile = _ast_checkpoint_memory_profile(
            path=path,
            mode=mode,
            reasons=reasons,
            current_tokens=current_tokens,
            changed_paths=changed_paths,
            confidence=confidence,
            summary_data=summary_data,
            task=task,
            symbols=symbols,
            scored_info=scored_info,
        )
        role = str(profile["role"])
        tier_counts[role] += 1
        has_memory_signal = bool(profile.get("memory_signal"))
        if has_memory_signal:
            memory_signal_selected_files += 1
        if not bool(profile.get("compress")):
            continue

        projection = _ast_checkpoint_symbol_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            summary_data=summary_data,
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=int(profile.get("max_excerpt_tokens") or 160),
            reasons_override=reasons,
        )
        if projection is None:
            projection = _source_excerpt_projection(
                selected_file=sf,
                file_info=file_by_path.get(path),
                current_tokens=current_tokens,
                mode=mode,
                task=task,
                changed_paths=changed_paths,
                max_excerpt_tokens=int(profile.get("max_excerpt_tokens") or 160),
                reasons_override=reasons,
            )
        if projection is None and bool(profile.get("allow_minimal_fallback")):
            projection = _minimal_source_excerpt_projection(
                file_info=file_by_path.get(path),
                current_tokens=current_tokens,
                path=path,
                max_excerpt_tokens=int(profile.get("max_excerpt_tokens") or 160),
                minimal_excerpt_tokens=48,
            )
        if projection is None:
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        projected_tier_counts[role] += 1
        if has_memory_signal:
            memory_signal_projected_files += 1
        removed_by_tier[role] += removed
        if is_expected:
            expected_loss_by_tier[role] += removed
        else:
            strict_noise_removed_by_tier[role] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": mode,
            "expected": is_expected,
            "tier": role,
            "base_tier": confidence.get("role_tier") or confidence.get("tier"),
            "checkpoint_reasons": profile.get("reasons", [])[:6],
            "owner_reasons": profile.get("owner_reasons", [])[:6],
            "memory_signal": has_memory_signal,
            "summary_match_counts": profile.get("summary_match_counts", {}),
            "matching_symbol_count": int(profile.get("matching_symbol_count") or 0),
            "rank": profile.get("rank"),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "matched_terms": projection.get("matched_terms", [])[:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "projected_file_count": len(rows),
        "memory_signal_selected_files": memory_signal_selected_files,
        "memory_signal_projected_files": memory_signal_projected_files,
        "memory_signals_tested": memory_signal_selected_files > 0,
        "tier_counts": dict(tier_counts.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "projected_files": rows[:10],
        "expected_loss_files": expected_loss_rows[:10],
    }


def _ranked_test_skeleton_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    task: str,
    changed_paths: set[str],
    scored_map: dict[str, dict[str, Any]] | None = None,
    policy: str = "ranked_test_skeleton_source_excerpt_v1",
) -> dict[str, Any]:
    """Diagnostic: shrink low-action ranked test skeletons using source windows only."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    candidate_files = 0
    eligible_files = 0
    projection_miss_reasons: Counter[str] = Counter()

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0 or path in changed_paths:
            continue
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        scored_info = (scored_map or {}).get(path)
        reasons = _benchmark_combined_reasons(sf, scored_info)
        decision = _ranked_test_skeleton_decision(
            path=path,
            mode=mode,
            current_tokens=current_tokens,
            reasons=reasons,
            scored_info=scored_info,
        )
        if bool(decision.get("candidate")):
            candidate_files += 1
        if not bool(decision.get("compress")):
            if bool(decision.get("candidate")):
                projection_miss_reasons[str(decision.get("tier") or "not_eligible")] += 1
            continue
        eligible_files += 1

        max_excerpt_tokens = int(decision["max_excerpt_tokens"])
        projection = _source_excerpt_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=max_excerpt_tokens,
            reasons_override=reasons,
        )
        if projection is None:
            projection_miss_reasons["source_projection_failed"] += 1
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            projection_miss_reasons["no_token_reduction"] += 1
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        tier = str(decision["tier"])
        projected_tier_counts[tier] += 1
        removed_by_tier[tier] += removed
        if is_expected:
            expected_loss_by_tier[tier] += removed
        else:
            strict_noise_removed_by_tier[tier] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": mode,
            "expected": is_expected,
            "tier": tier,
            "rank": decision.get("rank"),
            "action_risk": decision.get("action_risk"),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "compression_reasons": decision.get("reasons", [])[:6],
            "matched_terms": projection.get("matched_terms", [])[:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "candidate_file_count": candidate_files,
        "eligible_file_count": eligible_files,
        "projected_file_count": len(rows),
        "projection_miss_reasons": dict(projection_miss_reasons.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "expected_loss_files": expected_loss_rows[:10],
        "projected_files": rows[:25],
    }


def _ranked_test_symbol_carrier_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    task: str,
    changed_paths: set[str],
    scored_map: dict[str, dict[str, Any]] | None = None,
    policy: str = "ranked_test_symbol_carrier_source_excerpt_v1",
) -> dict[str, Any]:
    """Diagnostic: shrink ranked test skeletons whose evidence is only symbol carrier evidence."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    candidate_files = 0
    eligible_files = 0
    projection_miss_reasons: Counter[str] = Counter()

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0 or path in changed_paths:
            continue
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        scored_info = (scored_map or {}).get(path)
        reasons = _benchmark_combined_reasons(sf, scored_info)
        decision = _ranked_test_symbol_carrier_decision(
            path=path,
            mode=mode,
            current_tokens=current_tokens,
            task=task,
            reasons=reasons,
            scored_info=scored_info,
        )
        if bool(decision.get("candidate")):
            candidate_files += 1
        if not bool(decision.get("compress")):
            if bool(decision.get("candidate")):
                projection_miss_reasons[str(decision.get("tier") or "not_eligible")] += 1
            continue
        eligible_files += 1

        max_excerpt_tokens = int(decision["max_excerpt_tokens"])
        projection = _source_excerpt_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=max_excerpt_tokens,
            reasons_override=reasons,
        )
        if projection is None:
            projection_miss_reasons["source_projection_failed"] += 1
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            projection_miss_reasons["no_token_reduction"] += 1
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        tier = str(decision["tier"])
        projected_tier_counts[tier] += 1
        removed_by_tier[tier] += removed
        if is_expected:
            expected_loss_by_tier[tier] += removed
        else:
            strict_noise_removed_by_tier[tier] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": mode,
            "expected": is_expected,
            "tier": tier,
            "rank": decision.get("rank"),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "compression_reasons": decision.get("reasons", [])[:6],
            "matched_terms": projection.get("matched_terms", [])[:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "candidate_file_count": candidate_files,
        "eligible_file_count": eligible_files,
        "projected_file_count": len(rows),
        "projection_miss_reasons": dict(projection_miss_reasons.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "expected_loss_files": expected_loss_rows[:10],
        "projected_files": rows[:25],
    }


def _ranked_source_churn_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    task: str,
    changed_paths: set[str],
    scored_map: dict[str, dict[str, Any]] | None = None,
    policy: str = "ranked_source_churn_source_excerpt_v1",
) -> dict[str, Any]:
    """Diagnostic: shrink high-churn source skeletons that look like evidence carriers."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    candidate_files = 0
    eligible_files = 0
    projection_miss_reasons: Counter[str] = Counter()

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0 or path in changed_paths:
            continue
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        scored_info = (scored_map or {}).get(path)
        reasons = _benchmark_combined_reasons(sf, scored_info)
        decision = _ranked_source_churn_decision(
            path=path,
            mode=mode,
            current_tokens=current_tokens,
            reasons=reasons,
            scored_info=scored_info,
        )
        if bool(decision.get("candidate")):
            candidate_files += 1
        if not bool(decision.get("compress")):
            if bool(decision.get("candidate")):
                projection_miss_reasons[str(decision.get("tier") or "not_eligible")] += 1
            continue
        eligible_files += 1

        max_excerpt_tokens = int(decision["max_excerpt_tokens"])
        projection = _source_excerpt_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=max_excerpt_tokens,
            reasons_override=reasons,
        )
        if projection is None:
            projection_miss_reasons["source_projection_failed"] += 1
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            projection_miss_reasons["no_token_reduction"] += 1
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        tier = str(decision["tier"])
        projected_tier_counts[tier] += 1
        removed_by_tier[tier] += removed
        if is_expected:
            expected_loss_by_tier[tier] += removed
        else:
            strict_noise_removed_by_tier[tier] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": mode,
            "expected": is_expected,
            "tier": tier,
            "rank": decision.get("rank"),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "compression_reasons": decision.get("reasons", [])[:6],
            "matched_terms": projection.get("matched_terms", [])[:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "candidate_file_count": candidate_files,
        "eligible_file_count": eligible_files,
        "projected_file_count": len(rows),
        "projection_miss_reasons": dict(projection_miss_reasons.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "expected_loss_files": expected_loss_rows[:10],
        "projected_files": rows[:25],
    }


def _ranked_source_metadata_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    task: str,
    changed_paths: set[str],
    scored_map: dict[str, dict[str, Any]] | None = None,
    policy: str = "ranked_source_metadata_source_excerpt_v1",
) -> dict[str, Any]:
    """Diagnostic: shrink source skeletons selected for metadata or documentation tasks."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    candidate_files = 0
    eligible_files = 0
    projection_miss_reasons: Counter[str] = Counter()

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0 or path in changed_paths:
            continue
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        scored_info = (scored_map or {}).get(path)
        reasons = _benchmark_combined_reasons(sf, scored_info)
        decision = _ranked_source_metadata_decision(
            path=path,
            mode=mode,
            current_tokens=current_tokens,
            task=task,
            reasons=reasons,
            scored_info=scored_info,
        )
        if bool(decision.get("candidate")):
            candidate_files += 1
        if not bool(decision.get("compress")):
            if bool(decision.get("candidate")):
                projection_miss_reasons[str(decision.get("tier") or "not_eligible")] += 1
            continue
        eligible_files += 1

        max_excerpt_tokens = int(decision["max_excerpt_tokens"])
        projection = _source_excerpt_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=max_excerpt_tokens,
            reasons_override=reasons,
        )
        if projection is None:
            projection_miss_reasons["source_projection_failed"] += 1
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            projection_miss_reasons["no_token_reduction"] += 1
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        tier = str(decision["tier"])
        projected_tier_counts[tier] += 1
        removed_by_tier[tier] += removed
        if is_expected:
            expected_loss_by_tier[tier] += removed
        else:
            strict_noise_removed_by_tier[tier] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": mode,
            "expected": is_expected,
            "tier": tier,
            "rank": decision.get("rank"),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "compression_reasons": decision.get("reasons", [])[:6],
            "matched_terms": projection.get("matched_terms", [])[:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "candidate_file_count": candidate_files,
        "eligible_file_count": eligible_files,
        "projected_file_count": len(rows),
        "projection_miss_reasons": dict(projection_miss_reasons.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "expected_loss_files": expected_loss_rows[:10],
        "projected_files": rows[:25],
    }


def _ranked_metadata_summary_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    task: str,
    changed_paths: set[str],
    scored_map: dict[str, dict[str, Any]] | None = None,
    policy: str = "ranked_metadata_summary_source_excerpt_v1",
) -> dict[str, Any]:
    """Diagnostic: shrink ancillary summaries for metadata or documentation tasks."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    candidate_files = 0
    eligible_files = 0
    projection_miss_reasons: Counter[str] = Counter()

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0 or path in changed_paths:
            continue
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        scored_info = (scored_map or {}).get(path)
        reasons = _benchmark_combined_reasons(sf, scored_info)
        decision = _ranked_metadata_summary_decision(
            path=path,
            mode=mode,
            current_tokens=current_tokens,
            task=task,
            reasons=reasons,
            scored_info=scored_info,
        )
        if bool(decision.get("candidate")):
            candidate_files += 1
        if not bool(decision.get("compress")):
            if bool(decision.get("candidate")):
                projection_miss_reasons[str(decision.get("tier") or "not_eligible")] += 1
            continue
        eligible_files += 1

        projection = _source_excerpt_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=int(decision["max_excerpt_tokens"]),
            reasons_override=reasons,
        )
        if projection is None:
            projection_miss_reasons["source_projection_failed"] += 1
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            projection_miss_reasons["no_token_reduction"] += 1
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        tier = str(decision["tier"])
        projected_tier_counts[tier] += 1
        removed_by_tier[tier] += removed
        if is_expected:
            expected_loss_by_tier[tier] += removed
        else:
            strict_noise_removed_by_tier[tier] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": mode,
            "expected": is_expected,
            "tier": tier,
            "rank": decision.get("rank"),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "compression_reasons": decision.get("reasons", [])[:6],
            "matched_terms": projection.get("matched_terms", [])[:8],
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "candidate_file_count": candidate_files,
        "eligible_file_count": eligible_files,
        "projected_file_count": len(rows),
        "projection_miss_reasons": dict(projection_miss_reasons.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "expected_loss_files": expected_loss_rows[:10],
        "projected_files": rows[:25],
    }


def _ranked_test_skeleton_decision(
    *,
    path: str,
    mode: str,
    current_tokens: int,
    reasons: list[str],
    scored_info: dict[str, Any] | None,
) -> dict[str, Any]:
    rank = int((scored_info or {}).get("rank") or 0)
    candidate = mode in {"summary", "skeleton"} and current_tokens >= 120 and _is_test_path(path)
    if not candidate:
        return {"candidate": False, "compress": False, "tier": "not_ranked_test_skeleton", "rank": rank or None}
    if rank < 3:
        return {"candidate": True, "compress": False, "tier": "protected_owner_rank", "rank": rank or None}
    if not reasons:
        return {"candidate": True, "compress": False, "tier": "missing_reason_evidence", "rank": rank or None}

    action_risk = _ranked_test_skeleton_action_risk(path=path, reasons=reasons, rank=rank)
    if action_risk > 44:
        return {
            "candidate": True,
            "compress": False,
            "tier": "protected_high_action_risk",
            "rank": rank or None,
            "action_risk": action_risk,
        }

    max_excerpt_tokens = 72
    if _benchmark_mav_has_signal(reasons, "matched define:"):
        max_excerpt_tokens += 16
    if _benchmark_mav_has_signal(reasons, "matched call:"):
        max_excerpt_tokens += 8
    return {
        "candidate": True,
        "compress": True,
        "tier": "ranked_test_skeleton_carrier",
        "rank": rank,
        "action_risk": action_risk,
        "max_excerpt_tokens": max_excerpt_tokens,
        "reasons": ["ranked_low_action_test_skeleton", f"action_risk={action_risk}", f"rank={rank}"],
    }


def _ranked_test_skeleton_action_risk(*, path: str, reasons: list[str], rank: int) -> int:
    risk = 0
    if rank <= 1:
        risk += 24
    elif rank <= 2:
        risk += 16
    elif rank <= 4:
        risk += 8
    if _benchmark_mav_has_signal(reasons, "direct content evidence"):
        risk += 28
    if _benchmark_mav_has_signal(reasons, "explicit test task file"):
        risk += 30
    if _benchmark_mav_has_signal(reasons, "direct dependency of changed file", "has related tests", "test for high-scoring"):
        risk += 16
    if _benchmark_mav_has_signal(reasons, "matched entrypoint:"):
        risk += 24
    if _benchmark_mav_has_signal(reasons, "quoted literal match", "literal definition match"):
        risk += 28
    if _benchmark_mav_has_signal(reasons, "matched define:"):
        risk += 8
    if _benchmark_mav_has_signal(reasons, "matched call:"):
        risk += 5
    if _content_keyword_hits_from_reasons(reasons) >= 4:
        risk += 8
    if _source_excerpt_has_structural_risk(path):
        risk += 12
    return risk


def _ranked_test_symbol_carrier_decision(
    *,
    path: str,
    mode: str,
    current_tokens: int,
    task: str,
    reasons: list[str],
    scored_info: dict[str, Any] | None,
) -> dict[str, Any]:
    rank = int((scored_info or {}).get("rank") or 0)
    candidate = mode in {"summary", "skeleton"} and current_tokens >= 120 and _is_test_path(path)
    if not candidate:
        return {"candidate": False, "compress": False, "tier": "not_ranked_test_symbol_carrier", "rank": rank or None}
    if rank <= 0 or rank > 3:
        return {"candidate": True, "compress": False, "tier": "protected_outside_rank_window", "rank": rank or None}
    if not reasons:
        return {"candidate": True, "compress": False, "tier": "missing_reason_evidence", "rank": rank}
    if not _benchmark_mav_has_signal(reasons, "matched define:"):
        return {"candidate": True, "compress": False, "tier": "protected_no_definition_anchor", "rank": rank}
    if _benchmark_mav_has_signal(
        reasons,
        "direct content evidence",
        "explicit test task file",
        "matched entrypoint:",
    ):
        return {"candidate": True, "compress": False, "tier": "protected_action_owner_signal", "rank": rank}
    if _benchmark_path_task_aligned(path=path, task=task):
        return {"candidate": True, "compress": False, "tier": "protected_path_task_alignment", "rank": rank}
    if "refactor" in _benchmark_action_terms_from_task(task):
        return {"candidate": True, "compress": False, "tier": "protected_refactor_test_owner", "rank": rank}

    max_excerpt_tokens = 72
    if _benchmark_mav_has_signal(reasons, "matched define:"):
        max_excerpt_tokens += 16
    if _benchmark_mav_has_signal(reasons, "matched call:"):
        max_excerpt_tokens += 8
    return {
        "candidate": True,
        "compress": True,
        "tier": "ranked_test_symbol_carrier",
        "rank": rank,
        "max_excerpt_tokens": max_excerpt_tokens,
        "reasons": ["ranked_test_symbol_only_carrier", f"rank={rank}"],
    }


def _benchmark_path_task_aligned(*, path: str, task: str) -> bool:
    return bool(_benchmark_action_terms_from_path(path) & _benchmark_action_terms_from_task(task))


def _benchmark_action_terms_from_path(path: str) -> set[str]:
    terms: set[str] = set()
    for part in Path(path).parts:
        stem = Path(part).stem
        if stem.lower() in {"src", "test", "tests", "pkg", "packages", "node", "main", "spec", "e2e"}:
            continue
        terms.update(_benchmark_action_terms(stem))
    return terms


def _benchmark_action_terms_from_task(task: str) -> set[str]:
    terms = _benchmark_action_terms(task)
    if terms & {"completion", "completions", "fish", "shell", "zsh"}:
        terms.update({"completion", "completions", "shell"})
    if terms & {"logger", "logging"}:
        terms.update({"log", "logger"})
    if "response" in terms:
        terms.update({"response", "writer"})
    if "context" in terms:
        terms.add("context")
    return terms


def _benchmark_action_terms(value: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
    raw_terms = [
        token.lower()
        for token in re.split(r"[^A-Za-z0-9]+|_", spaced)
        if token
    ]
    terms: set[str] = set()
    stopwords = _EXCERPT_STOPWORDS | {"test", "tests", "src", "pkg", "packages", "spec", "e2e", "unit"}
    for raw in raw_terms:
        if len(raw) < 3 or raw in stopwords:
            continue
        term = raw[:-1] if len(raw) > 4 and raw.endswith("s") else raw
        terms.add(term)
    return terms


def _ranked_source_churn_decision(
    *,
    path: str,
    mode: str,
    current_tokens: int,
    reasons: list[str],
    scored_info: dict[str, Any] | None,
) -> dict[str, Any]:
    rank = int((scored_info or {}).get("rank") or 0)
    candidate = mode in {"summary", "skeleton"} and current_tokens >= 120 and _path_family(path) == "source"
    if not candidate:
        return {"candidate": False, "compress": False, "tier": "not_ranked_source_churn", "rank": rank or None}
    if rank < 4:
        return {"candidate": True, "compress": False, "tier": "protected_owner_rank", "rank": rank or None}
    if not reasons:
        return {"candidate": True, "compress": False, "tier": "missing_reason_evidence", "rank": rank or None}
    if _source_excerpt_has_structural_risk(path):
        return {"candidate": True, "compress": False, "tier": "protected_structural_risk", "rank": rank}
    if _benchmark_mav_has_signal(reasons, "quoted literal match", "literal definition match"):
        return {"candidate": True, "compress": False, "tier": "protected_literal_evidence", "rank": rank}
    if not _benchmark_mav_has_signal(reasons, "high churn"):
        return {"candidate": True, "compress": False, "tier": "protected_no_high_churn", "rank": rank}

    max_excerpt_tokens = 88
    if _benchmark_mav_has_signal(reasons, "matched define:"):
        max_excerpt_tokens += 20
    if _benchmark_mav_has_signal(reasons, "matched call:"):
        max_excerpt_tokens += 12
    return {
        "candidate": True,
        "compress": True,
        "tier": "ranked_source_churn_carrier",
        "rank": rank,
        "max_excerpt_tokens": max_excerpt_tokens,
        "reasons": ["ranked_high_churn_source_carrier", f"rank={rank}"],
    }


def _ranked_source_metadata_decision(
    *,
    path: str,
    mode: str,
    current_tokens: int,
    task: str,
    reasons: list[str],
    scored_info: dict[str, Any] | None,
) -> dict[str, Any]:
    rank = int((scored_info or {}).get("rank") or 0)
    candidate = mode in {"summary", "skeleton"} and current_tokens >= 80 and _path_family(path) == "source"
    if not candidate:
        return {"candidate": False, "compress": False, "tier": "not_ranked_source_metadata", "rank": rank or None}
    if not _benchmark_metadata_or_docs_task(task):
        return {"candidate": True, "compress": False, "tier": "protected_non_metadata_task", "rank": rank or None}
    if Path(path).name == "__init__.py":
        return {"candidate": True, "compress": False, "tier": "protected_package_init_metadata_owner", "rank": rank or None}
    if _source_excerpt_has_structural_risk(path):
        return {"candidate": True, "compress": False, "tier": "protected_structural_risk", "rank": rank or None}

    max_excerpt_tokens = 72
    if _benchmark_mav_has_signal(reasons, "matched define:"):
        max_excerpt_tokens += 16
    if _benchmark_mav_has_signal(reasons, "matched call:"):
        max_excerpt_tokens += 8
    return {
        "candidate": True,
        "compress": True,
        "tier": "ranked_source_metadata_carrier",
        "rank": rank or None,
        "max_excerpt_tokens": max_excerpt_tokens,
        "reasons": ["ranked_source_metadata_carrier", f"rank={rank}"],
    }


def _ranked_metadata_summary_decision(
    *,
    path: str,
    mode: str,
    current_tokens: int,
    task: str,
    reasons: list[str],
    scored_info: dict[str, Any] | None,
) -> dict[str, Any]:
    rank = int((scored_info or {}).get("rank") or 0)
    family = _path_family(path)
    candidate = mode == "summary" and current_tokens >= 80 and family in {"config", "examples"}
    if not candidate:
        return {"candidate": False, "compress": False, "tier": "not_ranked_metadata_summary", "rank": rank or None}
    if not _benchmark_metadata_or_docs_task(task):
        return {"candidate": True, "compress": False, "tier": "protected_non_metadata_task", "rank": rank or None}
    if rank <= 0 or rank < 3:
        return {"candidate": True, "compress": False, "tier": "protected_owner_rank", "rank": rank or None}
    if _benchmark_path_task_aligned(path=path, task=task):
        return {"candidate": True, "compress": False, "tier": "protected_path_task_alignment", "rank": rank}
    if _benchmark_mav_has_signal(
        reasons,
        "direct content evidence",
        "direct dependency of changed file",
        "has related tests",
        "matched define:",
        "matched call:",
        "matched entrypoint:",
        "explicit test task file",
        "keyword phrase match",
        "quoted literal match",
        "literal definition match",
        "conventional scope path match",
    ):
        return {"candidate": True, "compress": False, "tier": "protected_confirmed_action_signal", "rank": rank}

    return {
        "candidate": True,
        "compress": True,
        "tier": "ranked_metadata_summary_carrier",
        "rank": rank,
        "max_excerpt_tokens": 24,
        "reasons": ["ranked_metadata_summary_carrier", f"rank={rank}", f"family={family}"],
    }


def _benchmark_metadata_or_docs_task(task: str) -> bool:
    task_lc = str(task).lower()
    if task_lc.startswith(("docs:", "doc:", "ci:")):
        return True
    terms = _benchmark_action_terms_from_task(task)
    return bool(terms & {"doc", "docs", "document", "documentation", "license", "metadata", "release", "typo", "version"})


def _mav_span_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    summaries: dict[str, Any],
    task: str,
    changed_paths: set[str],
    policy: str = "mav_span_per_token_source_excerpt_v1",
) -> dict[str, Any]:
    """Diagnostic: keep selected files fixed, but project carriers by marginal action value per token."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    tier_counts: Counter[str] = Counter()
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    memory_signal_selected_files = 0
    memory_signal_projected_files = 0

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0:
            continue
        reasons = [str(reason) for reason in (getattr(sf, "reasons", None) or [])]
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        summary_data = _ast_checkpoint_summary_dict(summaries.get(path))
        symbols = list(getattr(sf, "symbols", None) or []) or _ast_checkpoint_summary_symbols(summary_data)
        confidence = _source_excerpt_confidence_tier(
            path=path,
            mode=mode,
            reasons=reasons,
            current_tokens=current_tokens,
            changed_paths=changed_paths,
            symbols=symbols,
        )
        profile = _mav_span_file_profile(
            path=path,
            mode=mode,
            reasons=reasons,
            current_tokens=current_tokens,
            changed_paths=changed_paths,
            confidence=confidence,
            summary_data=summary_data,
            task=task,
            symbols=symbols,
        )
        role = str(profile["role"])
        tier_counts[role] += 1
        has_memory_signal = bool(profile.get("memory_signal"))
        if has_memory_signal:
            memory_signal_selected_files += 1
        if not bool(profile.get("compress")):
            continue

        projection = _mav_span_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            summary_data=summary_data,
            profile=profile,
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=int(profile.get("max_excerpt_tokens") or 160),
        )
        if projection is None:
            continue
        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        projected_tier_counts[role] += 1
        if has_memory_signal:
            memory_signal_projected_files += 1
        removed_by_tier[role] += removed
        if is_expected:
            expected_loss_by_tier[role] += removed
        else:
            strict_noise_removed_by_tier[role] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": mode,
            "expected": is_expected,
            "tier": role,
            "base_tier": confidence.get("role_tier") or confidence.get("tier"),
            "mav_reasons": profile.get("reasons", [])[:6],
            "owner_reasons": profile.get("owner_reasons", [])[:6],
            "memory_signal": has_memory_signal,
            "summary_match_counts": profile.get("summary_match_counts", {}),
            "matching_symbol_count": int(profile.get("matching_symbol_count") or 0),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "matched_terms": projection.get("matched_terms", [])[:8],
            "span_count": int(projection.get("span_count") or 0),
            "mav_value": round(float(projection.get("mav_value") or 0.0), 2),
            "mav_density": round(float(projection.get("mav_density") or 0.0), 4),
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "projected_file_count": len(rows),
        "memory_signal_selected_files": memory_signal_selected_files,
        "memory_signal_projected_files": memory_signal_projected_files,
        "memory_signals_tested": memory_signal_selected_files > 0,
        "tier_counts": dict(tier_counts.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "projected_files": rows[:10],
        "expected_loss_files": expected_loss_rows[:10],
    }


def _neutral_mav_span_excerpt_projection(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    selected_modes: dict[str, str],
    expected_set: set[str],
    file_by_path: dict[str, Any],
    summaries: dict[str, Any],
    task: str,
    changed_paths: set[str],
    policy: str = "neutral_mav_span_optimizer_v1",
) -> dict[str, Any]:
    """Diagnostic: risk-adjusted marginal action value optimizer over fixed selected files."""
    projected_tokens = dict(selected_tokens)
    rows: list[dict[str, Any]] = []
    tier_counts: Counter[str] = Counter()
    projected_tier_counts: Counter[str] = Counter()
    removed_by_tier: Counter[str] = Counter()
    strict_noise_removed_by_tier: Counter[str] = Counter()
    expected_loss_by_tier: Counter[str] = Counter()
    memory_signal_selected_files = 0
    memory_signal_projected_files = 0
    term_counts = _neutral_mav_selected_term_counts(selected=selected, task=task)

    for sf in selected:
        path = str(getattr(sf, "path", ""))
        current_tokens = int(selected_tokens.get(path) or 0)
        if current_tokens <= 0:
            continue
        reasons = [str(reason) for reason in (getattr(sf, "reasons", None) or [])]
        mode = str(selected_modes.get(path) or getattr(sf, "include_mode", ""))
        summary_data = _ast_checkpoint_summary_dict(summaries.get(path))
        symbols = list(getattr(sf, "symbols", None) or []) or _ast_checkpoint_summary_symbols(summary_data)
        confidence = _source_excerpt_confidence_tier(
            path=path,
            mode=mode,
            reasons=reasons,
            current_tokens=current_tokens,
            changed_paths=changed_paths,
            symbols=symbols,
        )
        profile = _neutral_mav_span_file_profile(
            path=path,
            mode=mode,
            reasons=reasons,
            current_tokens=current_tokens,
            changed_paths=changed_paths,
            confidence=confidence,
            summary_data=summary_data,
            task=task,
            symbols=symbols,
            term_counts=term_counts,
        )
        role = str(profile["role"])
        tier_counts[role] += 1
        has_memory_signal = bool(profile.get("memory_signal"))
        if has_memory_signal:
            memory_signal_selected_files += 1
        if not bool(profile.get("eligible")):
            continue

        projection = _mav_span_projection(
            selected_file=sf,
            file_info=file_by_path.get(path),
            summary_data=summary_data,
            profile=profile,
            current_tokens=current_tokens,
            mode=mode,
            task=task,
            changed_paths=changed_paths,
            max_excerpt_tokens=int(profile.get("max_excerpt_tokens") or 160),
        )
        if projection is None:
            continue
        decision = _neutral_mav_projection_decision(
            profile=profile,
            projection=projection,
            current_tokens=current_tokens,
        )
        if not bool(decision["compress"]):
            continue

        projected = int(projection["projected_tokens"])
        if projected >= current_tokens:
            continue

        projected_tokens[path] = projected
        removed = current_tokens - projected
        is_expected = path in expected_set
        projected_tier_counts[role] += 1
        if has_memory_signal:
            memory_signal_projected_files += 1
        removed_by_tier[role] += removed
        if is_expected:
            expected_loss_by_tier[role] += removed
        else:
            strict_noise_removed_by_tier[role] += removed
        rows.append({
            "path": path,
            "family": _path_family(path),
            "mode": mode,
            "expected": is_expected,
            "tier": role,
            "base_tier": confidence.get("role_tier") or confidence.get("tier"),
            "neutral_mav_reasons": profile.get("reasons", [])[:6],
            "owner_reasons": profile.get("owner_reasons", [])[:6],
            "memory_signal": has_memory_signal,
            "summary_match_counts": profile.get("summary_match_counts", {}),
            "matching_symbol_count": int(profile.get("matching_symbol_count") or 0),
            "current_tokens": current_tokens,
            "projected_tokens": projected,
            "removed_tokens": removed,
            "reason": projection["reason"],
            "matched_terms": projection.get("matched_terms", [])[:8],
            "span_count": int(projection.get("span_count") or 0),
            "mav_value": round(float(projection.get("mav_value") or 0.0), 2),
            "mav_density": round(float(projection.get("mav_density") or 0.0), 4),
            "owner_risk": round(float(profile.get("owner_risk") or 0.0), 2),
            "carrier_score": round(float(profile.get("carrier_score") or 0.0), 2),
            "term_redundancy": round(float(profile.get("term_redundancy") or 0.0), 2),
            "compression_score": round(float(decision.get("compression_score") or 0.0), 4),
            "compression_threshold": round(float(decision.get("threshold") or 0.0), 4),
        })

    baseline_selected = sum(int(value) for value in selected_tokens.values())
    baseline_expected = sum(int(selected_tokens.get(path) or 0) for path in expected_set)
    projected_selected = sum(int(value) for value in projected_tokens.values())
    projected_expected = sum(int(projected_tokens.get(path) or 0) for path in expected_set)
    baseline_strict_noise = max(0, baseline_selected - baseline_expected)
    projected_strict_noise = max(0, projected_selected - projected_expected)
    baseline_tp = baseline_expected / baseline_selected if baseline_selected > 0 else 1.0
    projected_tp = projected_expected / projected_selected if projected_selected > 0 else 1.0

    rows.sort(key=lambda row: int(row["removed_tokens"]), reverse=True)
    expected_loss_rows = [
        row for row in rows
        if bool(row["expected"]) and int(row["removed_tokens"]) > 0
    ]
    return {
        "policy": policy,
        "selected_file_count_before": len(selected_tokens),
        "selected_file_count_after": len(projected_tokens),
        "selected_file_set_unchanged": set(selected_tokens) == set(projected_tokens),
        "baseline_selected_tokens": baseline_selected,
        "projected_selected_tokens": projected_selected,
        "removed_tokens": max(0, baseline_selected - projected_selected),
        "baseline_expected_tokens": baseline_expected,
        "projected_expected_tokens": projected_expected,
        "expected_token_loss": max(0, baseline_expected - projected_expected),
        "strict_noise_removed": max(0, baseline_strict_noise - projected_strict_noise),
        "baseline_token_precision": round(baseline_tp, 4),
        "projected_token_precision": round(projected_tp, 4),
        "token_precision_delta": round(projected_tp - baseline_tp, 4),
        "projected_file_count": len(rows),
        "memory_signal_selected_files": memory_signal_selected_files,
        "memory_signal_projected_files": memory_signal_projected_files,
        "memory_signals_tested": memory_signal_selected_files > 0,
        "tier_counts": dict(tier_counts.most_common()),
        "projected_tier_counts": dict(projected_tier_counts.most_common()),
        "removed_tokens_by_tier": dict(removed_by_tier.most_common()),
        "strict_noise_removed_by_tier": dict(strict_noise_removed_by_tier.most_common()),
        "expected_loss_by_tier": dict(expected_loss_by_tier.most_common()),
        "projected_files": rows[:10],
        "expected_loss_files": expected_loss_rows[:10],
    }


def _neutral_mav_selected_term_counts(*, selected: list[Any], task: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for sf in selected:
        path = str(getattr(sf, "path", ""))
        reasons = [str(reason) for reason in (getattr(sf, "reasons", None) or [])]
        symbols = list(getattr(sf, "symbols", None) or [])
        counts.update(set(_source_excerpt_terms(task=task, path=path, reasons=reasons, symbols=symbols)))
    return counts


def _neutral_mav_span_file_profile(
    *,
    path: str,
    mode: str,
    reasons: list[str],
    current_tokens: int,
    changed_paths: set[str],
    confidence: dict[str, Any],
    summary_data: dict[str, Any],
    task: str,
    symbols: list[Any],
    term_counts: Counter[str],
) -> dict[str, Any]:
    family = _path_family(path)
    content_hits = _content_keyword_hits_from_reasons(reasons)
    direct, graph, structural, symbolic = _benchmark_mav_support_signals(reasons)
    direct_symbol = _benchmark_mav_has_signal(
        reasons,
        "matched call:",
        "matched define:",
        "matched entrypoint:",
        "matched env read:",
        "matched side effect:",
        "quoted literal match:",
        "literal definition match:",
    )
    related_test = _benchmark_mav_has_signal(
        reasons,
        "has related tests",
        "related test",
        "test for high-scoring",
        "explicit test task file",
    )
    broad_local = _benchmark_mav_has_signal(
        reasons,
        "filename keyword match",
        "symbol keyword match",
        "matched ranking keyword:",
        "matched role keyword:",
    )
    memory_signal = _benchmark_mav_has_signal(
        reasons,
        "episodic memory similar task",
        "learning feedback miss",
        "procedure=",
    )
    summary_counts = _ast_checkpoint_summary_match_counts(summary_data, task=task, path=path, reasons=reasons)
    matching_symbols = _ast_checkpoint_matching_symbols(symbols, task=task, path=path, reasons=reasons)
    terms = _source_excerpt_terms(task=task, path=path, reasons=reasons, symbols=symbols)
    redundant_terms = [term for term in terms if term_counts.get(term, 0) > 1]
    term_redundancy = (
        sum(min(5, term_counts.get(term, 0)) for term in redundant_terms) / len(terms)
        if terms else 0.0
    )
    structural_risk = bool(confidence.get("structural_risk"))
    direct_action_signal = bool(confidence.get("direct_action_signal"))
    confirmation_count = int(confidence.get("confirmation_count") or 0)
    action_mismatch = bool(confidence.get("action_mismatch"))
    hub_path = _benchmark_mav_is_hub_path(path)
    summary_support = sum(int(value) for value in summary_counts.values())

    owner_reasons: list[str] = []
    owner_risk = 0.0
    if path in changed_paths:
        owner_reasons.append("changed_path_owner")
        owner_risk += 100.0
    if mode == "full":
        owner_reasons.append("full_mode_owner")
        owner_risk += 80.0
    if memory_signal:
        owner_reasons.append("memory_confirmed_owner")
        owner_risk += 70.0
    if bool(confidence.get("strong_action_owner")):
        owner_reasons.append("strong_action_owner")
        owner_risk += 60.0
    if related_test and _is_test_path(path):
        owner_reasons.append("direct_test_target_owner")
        owner_risk += 50.0
    if summary_counts.get("entrypoints", 0) > 0:
        owner_reasons.append("entrypoint_owner")
        owner_risk += 45.0
    if summary_counts.get("failure_hints", 0) > 0:
        owner_reasons.append("failure_hint_owner")
        owner_risk += 42.0
    if direct_action_signal and content_hits >= 3:
        owner_reasons.append("dense_direct_action_owner")
        owner_risk += 38.0
    if structural_risk and confirmation_count >= 3 and content_hits >= 3:
        owner_reasons.append("multi_confirmed_structural_owner")
        owner_risk += 30.0
    if direct_symbol and summary_counts.get("defines", 0) > 0 and content_hits >= 4:
        owner_reasons.append("dense_definition_owner")
        owner_risk += 25.0
    if current_tokens < 160 and (direct_symbol or summary_support > 0):
        owner_reasons.append("small_focused_file")
        owner_risk += 20.0

    carrier_reasons: list[str] = []
    carrier_score = 0.0
    if hub_path:
        carrier_reasons.append("hub_path")
        carrier_score += 25.0
    if action_mismatch:
        carrier_reasons.append("action_mismatch")
        carrier_score += 22.0
    if confidence.get("tier") == "weak":
        carrier_reasons.append("weak_tier")
        carrier_score += 20.0
    if confidence.get("tier") == "medium":
        carrier_reasons.append("medium_not_owner")
        carrier_score += 8.0
    if current_tokens >= 240:
        carrier_reasons.append("large_token_mass")
        carrier_score += min(28.0, (current_tokens - 160) / 24.0)
    if len(matching_symbols) <= 2 and content_hits <= 2:
        carrier_reasons.append("low_density_match")
        carrier_score += 18.0
    if summary_counts.get("calls", 0) > 0 and summary_counts.get("defines", 0) == 0:
        carrier_reasons.append("callsite_without_definition")
        carrier_score += 16.0
    if direct_symbol and hub_path:
        carrier_reasons.append("hub_symbol_reference")
        carrier_score += 14.0
    if family in {"config", "docs", "examples", "fixtures"} and not direct_action_signal:
        carrier_reasons.append(f"{family}_support_file")
        carrier_score += 12.0
    if broad_local and not direct_action_signal:
        carrier_reasons.append("broad_local_signal")
        carrier_score += 12.0
    if not direct_action_signal:
        carrier_reasons.append("no_direct_action_signal")
        carrier_score += 10.0
    if term_redundancy >= 1.0:
        carrier_reasons.append("redundant_terms_across_selection")
        carrier_score += min(24.0, term_redundancy * 8.0)
    if summary_support <= 2:
        carrier_reasons.append("low_summary_support")
        carrier_score += 6.0

    protected = owner_risk >= 70.0
    eligible = (
        not protected
        and path not in changed_paths
        and mode in {"summary", "skeleton", "symbols"}
        and current_tokens > 120
        and carrier_score >= 28.0
    )
    if protected:
        role = "neutral_mav_action_owner"
    elif carrier_score >= 45.0:
        role = "neutral_mav_carrier"
    else:
        role = "neutral_mav_uncertain"

    max_excerpt_tokens = 180
    if confidence.get("tier") == "weak":
        max_excerpt_tokens = 90
    elif owner_risk >= 45.0:
        max_excerpt_tokens = 220
    elif carrier_score >= 60.0:
        max_excerpt_tokens = 140

    density_threshold = 0.34
    if carrier_score >= 60.0:
        density_threshold = 0.24
    elif owner_risk >= 45.0:
        density_threshold = 0.45

    return {
        "role": role,
        "eligible": eligible,
        "reasons": _dedupe_strings(carrier_reasons),
        "owner_reasons": _dedupe_strings(owner_reasons),
        "memory_signal": memory_signal,
        "summary_match_counts": summary_counts,
        "matching_symbol_count": len(matching_symbols),
        "confirmation_multiplier": 1.0 + min(4, confirmation_count) * 0.18,
        "max_excerpt_tokens": max_excerpt_tokens,
        "density_threshold": density_threshold,
        "owner_risk": owner_risk,
        "carrier_score": carrier_score,
        "term_redundancy": term_redundancy,
    }


def _neutral_mav_projection_decision(
    *,
    profile: dict[str, Any],
    projection: dict[str, Any],
    current_tokens: int,
) -> dict[str, Any]:
    projected_tokens = int(projection.get("projected_tokens") or current_tokens)
    if projected_tokens <= 0 or projected_tokens >= current_tokens:
        return {"compress": False, "compression_score": 0.0, "threshold": 0.0}

    owner_risk = float(profile.get("owner_risk") or 0.0)
    carrier_score = float(profile.get("carrier_score") or 0.0)
    term_redundancy = float(profile.get("term_redundancy") or 0.0)
    density = float(projection.get("mav_density") or 0.0)
    value = float(projection.get("mav_value") or 0.0)
    compression_ratio = (current_tokens - projected_tokens) / current_tokens

    action_value = min(2.5, density) * (1.0 + min(2.0, value / 120.0))
    compressibility = compression_ratio * (1.0 + carrier_score / 70.0) * (1.0 + min(2.0, term_redundancy) / 3.0)
    risk_drag = 1.0 + owner_risk / 60.0
    compression_score = (action_value * compressibility) / risk_drag
    threshold = 0.42
    if carrier_score >= 60.0:
        threshold = 0.32
    if owner_risk >= 45.0:
        threshold += 0.18
    if owner_risk >= 70.0:
        return {
            "compress": False,
            "compression_score": compression_score,
            "threshold": threshold,
        }
    return {
        "compress": compression_score >= threshold and compression_ratio >= 0.15,
        "compression_score": compression_score,
        "threshold": threshold,
    }


def _mav_span_file_profile(
    *,
    path: str,
    mode: str,
    reasons: list[str],
    current_tokens: int,
    changed_paths: set[str],
    confidence: dict[str, Any],
    summary_data: dict[str, Any],
    task: str,
    symbols: list[Any],
    scored_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content_hits = _content_keyword_hits_from_reasons(reasons)
    summary_counts = _ast_checkpoint_summary_match_counts(summary_data, task=task, path=path, reasons=reasons)
    matching_symbols = _ast_checkpoint_matching_symbols(symbols, task=task, path=path, reasons=reasons)
    memory_signal = _benchmark_mav_has_signal(
        reasons,
        "episodic memory similar task",
        "learning feedback miss",
        "procedure=",
    )
    structural_risk = bool(confidence.get("structural_risk"))
    direct_action_signal = bool(confidence.get("direct_action_signal"))
    confirmation_count = int(confidence.get("confirmation_count") or 0)

    owner_reasons: list[str] = []
    if path in changed_paths:
        owner_reasons.append("changed_path_owner")
    if mode == "full":
        owner_reasons.append("full_mode_owner")
    if bool(confidence.get("strong_action_owner")):
        owner_reasons.append("strong_action_owner")
    if memory_signal:
        owner_reasons.append("memory_confirmed_owner")
    if summary_counts.get("entrypoints", 0) > 0:
        owner_reasons.append("entrypoint_owner")
    if summary_counts.get("failure_hints", 0) > 0:
        owner_reasons.append("failure_hint_owner")
    if summary_counts.get("test_hints", 0) > 0 and _is_test_path(path):
        owner_reasons.append("test_hint_owner")
    if structural_risk and (summary_counts.get("defines", 0) > 0 or content_hits >= 3):
        owner_reasons.append("structural_definition_owner")

    if owner_reasons:
        return {
            "role": "mav_action_owner",
            "compress": False,
            "reasons": [],
            "owner_reasons": owner_reasons,
            "memory_signal": memory_signal,
            "summary_match_counts": summary_counts,
            "matching_symbol_count": len(matching_symbols),
            "confirmation_multiplier": 1.0 + min(4, confirmation_count) * 0.25,
        }

    carrier_reasons: list[str] = []
    if bool(confidence.get("guarded_strong_carrier")):
        carrier_reasons.append("guarded_strong_carrier")
    if bool(confidence.get("action_mismatch")) and not structural_risk:
        carrier_reasons.append("action_mismatch_carrier")
    if confidence.get("tier") == "weak":
        carrier_reasons.append("weak_low_action_value")
    if _benchmark_mav_is_hub_path(path) and current_tokens >= 240 and not direct_action_signal:
        carrier_reasons.append("hub_low_owner_density")
    if summary_counts.get("calls", 0) > 0 and summary_counts.get("defines", 0) == 0 and not structural_risk:
        carrier_reasons.append("callsite_only_carrier")
    if current_tokens >= 240 and len(matching_symbols) <= 2 and content_hits <= 2 and not structural_risk:
        carrier_reasons.append("concentrated_span_evidence")
    if _is_test_path(path) and matching_symbols and not summary_counts.get("test_hints"):
        carrier_reasons.append("test_support_carrier")

    compress = mode in {"summary", "skeleton", "symbols"} and current_tokens > 120 and bool(carrier_reasons)
    role = "mav_evidence_carrier" if carrier_reasons else "mav_uncertain"
    max_excerpt_tokens = 180 if len(matching_symbols) >= 2 else 140
    if confidence.get("tier") == "weak":
        max_excerpt_tokens = 90
    return {
        "role": role,
        "compress": compress,
        "reasons": _dedupe_strings(carrier_reasons),
        "owner_reasons": [],
        "memory_signal": memory_signal,
        "summary_match_counts": summary_counts,
        "matching_symbol_count": len(matching_symbols),
        "confirmation_multiplier": 1.0 + min(4, confirmation_count) * 0.25,
        "max_excerpt_tokens": max_excerpt_tokens,
        "density_threshold": 0.35 if confidence.get("tier") == "weak" else 0.45,
    }


def _mav_span_projection(
    *,
    selected_file: Any,
    file_info: Any,
    summary_data: dict[str, Any],
    profile: dict[str, Any],
    current_tokens: int,
    mode: str,
    task: str,
    changed_paths: set[str],
    max_excerpt_tokens: int,
) -> dict[str, Any] | None:
    path = str(getattr(selected_file, "path", ""))
    if not path or path in changed_paths or mode not in {"summary", "skeleton", "symbols"}:
        return None
    text = _source_excerpt_text(file_info)
    if not text:
        return None
    lines = text.splitlines()
    if not lines:
        return None
    reasons = [str(reason) for reason in (getattr(selected_file, "reasons", None) or [])]
    symbols = list(getattr(selected_file, "symbols", None) or []) or _ast_checkpoint_summary_symbols(summary_data)
    terms = _source_excerpt_terms(task=task, path=path, reasons=reasons, symbols=symbols)
    if not terms:
        return None

    candidates = _mav_span_candidates(
        lines=lines,
        path=path,
        terms=terms,
        symbols=symbols,
        profile=profile,
    )
    if not candidates:
        return None
    threshold = float(profile.get("density_threshold") or 0.45)
    selected_ranges: list[tuple[int, int]] = [*_ast_checkpoint_header_ranges(lines, path)]
    matched_terms: list[str] = []
    total_value = 0.0
    best_density = 0.0
    selected_count = 0
    for candidate in sorted(candidates, key=lambda item: (float(item["density"]), float(item["value"])), reverse=True):
        if float(candidate["density"]) < threshold and selected_count > 0:
            continue
        if float(candidate["density"]) < threshold and float(candidate["value"]) < 48.0:
            continue
        trial_ranges = _merge_excerpt_ranges([*selected_ranges, candidate["range"]])
        excerpt = _source_excerpt_from_ranges(lines, trial_ranges, max_excerpt_tokens=max_excerpt_tokens)
        if not excerpt:
            continue
        projected_tokens = estimate_tokens(excerpt)
        if projected_tokens > max_excerpt_tokens and selected_count > 0:
            continue
        selected_ranges = trial_ranges
        matched_terms.extend(candidate.get("matched_terms") or [])
        total_value += float(candidate["value"])
        best_density = max(best_density, float(candidate["density"]))
        selected_count += 1
        if selected_count >= 4:
            break

    if selected_count <= 0:
        return None
    excerpt = _source_excerpt_from_ranges(lines, _merge_excerpt_ranges(selected_ranges), max_excerpt_tokens=max_excerpt_tokens)
    if not excerpt:
        return None
    projected_tokens = estimate_tokens(excerpt)
    if projected_tokens <= 0 or projected_tokens >= current_tokens:
        return None
    return {
        "projected_tokens": projected_tokens,
        "matched_terms": _dedupe_strings(matched_terms)[:8],
        "reason": "mav_per_token_spans",
        "span_count": selected_count,
        "mav_value": total_value,
        "mav_density": best_density,
    }


def _mav_span_candidates(
    *,
    lines: list[str],
    path: str,
    terms: list[str],
    symbols: list[Any],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_ranges: set[tuple[int, int]] = set()
    for sym in symbols[:80]:
        start_line = _ast_checkpoint_symbol_attr(sym, "start_line")
        end_line = _ast_checkpoint_symbol_attr(sym, "end_line")
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        start = _ast_checkpoint_include_preceding_comments(lines, max(0, start_line - 1))
        end = min(len(lines), max(start + 1, end_line))
        candidate = _mav_span_candidate(
            lines=lines,
            span=(start, end),
            label=str(_ast_checkpoint_symbol_attr(sym, "name") or ""),
            search_text=" ".join(
                str(part or "")
                for part in (
                    _ast_checkpoint_symbol_attr(sym, "name"),
                    _ast_checkpoint_symbol_attr(sym, "signature"),
                    _ast_checkpoint_symbol_attr(sym, "summary"),
                    _ast_checkpoint_symbol_attr(sym, "body"),
                )
            ),
            terms=terms,
            profile=profile,
            source="symbol",
        )
        if candidate and candidate["range"] not in seen_ranges:
            seen_ranges.add(candidate["range"])
            candidates.append(candidate)

    line_ranges, _matched = _source_excerpt_ranges(lines=lines, terms=terms, path=path, symbols=[])
    for span in line_ranges[:40]:
        snippet = "\n".join(lines[span[0]:span[1]])
        candidate = _mav_span_candidate(
            lines=lines,
            span=span,
            label="line_window",
            search_text=snippet,
            terms=terms,
            profile=profile,
            source="line_window",
        )
        if candidate and candidate["range"] not in seen_ranges:
            seen_ranges.add(candidate["range"])
            candidates.append(candidate)
    return candidates


def _mav_span_candidate(
    *,
    lines: list[str],
    span: tuple[int, int],
    label: str,
    search_text: str,
    terms: list[str],
    profile: dict[str, Any],
    source: str,
) -> dict[str, Any] | None:
    start, end = span
    if end <= start:
        return None
    snippet = "\n".join(lines[start:end]).strip()
    if not snippet:
        return None
    value, matched_terms = _mav_span_action_value(
        label=label,
        search_text=search_text,
        snippet=snippet,
        terms=terms,
        profile=profile,
        source=source,
    )
    if value <= 0:
        return None
    tokens = max(1, estimate_tokens(snippet))
    cost = max(16, tokens)
    density = value / cost
    return {
        "range": (start, end),
        "value": value,
        "density": density,
        "tokens": tokens,
        "matched_terms": matched_terms,
    }


def _mav_span_action_value(
    *,
    label: str,
    search_text: str,
    snippet: str,
    terms: list[str],
    profile: dict[str, Any],
    source: str,
) -> tuple[float, list[str]]:
    label_lc = label.lower()
    search_lc = search_text.lower()
    snippet_lc = snippet.lower()
    matched: list[str] = []
    value = 0.0
    for term in terms:
        term_lc = term.lower()
        if not term_lc:
            continue
        if term_lc in label_lc:
            value += 34.0
            matched.append(term_lc)
        elif term_lc in search_lc:
            value += 16.0
            matched.append(term_lc)
        elif term_lc in snippet_lc:
            value += 8.0
            matched.append(term_lc)
    summary_counts = profile.get("summary_match_counts") if isinstance(profile.get("summary_match_counts"), dict) else {}
    if source == "symbol":
        value += 10.0
    if summary_counts.get("calls", 0) and not summary_counts.get("defines", 0):
        value += 8.0
    if summary_counts.get("defines", 0):
        value += 12.0
    if summary_counts.get("public_api", 0):
        value += 6.0
    if summary_counts.get("test_hints", 0) or summary_counts.get("failure_hints", 0):
        value += 10.0
    value *= float(profile.get("confirmation_multiplier") or 1.0)
    return value, _dedupe_strings(matched)


def _ast_checkpoint_memory_profile(
    *,
    path: str,
    mode: str,
    reasons: list[str],
    current_tokens: int,
    changed_paths: set[str],
    confidence: dict[str, Any],
    summary_data: dict[str, Any],
    task: str,
    symbols: list[Any],
    scored_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content_hits = _content_keyword_hits_from_reasons(reasons)
    summary_counts = _ast_checkpoint_summary_match_counts(summary_data, task=task, path=path, reasons=reasons)
    matching_symbols = _ast_checkpoint_matching_symbols(symbols, task=task, path=path, reasons=reasons)
    memory_signal = _benchmark_mav_has_signal(
        reasons,
        "episodic memory similar task",
        "learning feedback miss",
        "procedure=",
    )
    structural_risk = bool(confidence.get("structural_risk"))
    direct_action_signal = bool(confidence.get("direct_action_signal"))
    release_metadata_signal = _benchmark_mav_has_signal(reasons, "release/version metadata")
    literal_definition_signal = _benchmark_mav_has_signal(
        reasons,
        "literal definition match:",
        "quoted literal match:",
    )
    rank = int((scored_info or {}).get("rank") or 0)
    ranked_test_support = _ast_checkpoint_ranked_test_support_carrier(
        path=path,
        reasons=reasons,
        rank=rank,
        direct_action_signal=direct_action_signal,
        literal_definition_signal=literal_definition_signal,
    )
    owner_reasons: list[str] = []
    if path in changed_paths:
        owner_reasons.append("changed_path_checkpoint")
    if bool(confidence.get("strong_action_owner")) and not ranked_test_support:
        owner_reasons.append("strong_action_owner")
    if memory_signal:
        owner_reasons.append("memory_confirmed_checkpoint")
    if summary_counts.get("entrypoints", 0) > 0:
        owner_reasons.append("matched_entrypoint_checkpoint")
    if summary_counts.get("failure_hints", 0) > 0:
        owner_reasons.append("failure_hint_checkpoint")
    if summary_counts.get("test_hints", 0) > 0 and _is_test_path(path) and not ranked_test_support:
        owner_reasons.append("test_hint_checkpoint")
    if structural_risk and (content_hits >= 3 or summary_counts.get("defines", 0) > 0):
        owner_reasons.append("structural_owner_checkpoint")
    if (
        literal_definition_signal
        and summary_counts.get("public_api", 0) > 0
        and summary_counts.get("defines", 0) > 0
    ):
        owner_reasons.append("literal_public_api_checkpoint")
    if (
        direct_action_signal
        and not release_metadata_signal
        and summary_counts.get("calls", 0) >= 3
        and (summary_counts.get("defines", 0) > 0 or len(matching_symbols) >= 3)
    ):
        owner_reasons.append("dense_call_literal_checkpoint")
    if mode == "full":
        owner_reasons.append("full_mode_checkpoint")

    if owner_reasons:
        return {
            "role": "ast_checkpoint_owner",
            "compress": False,
            "reasons": [],
            "owner_reasons": owner_reasons,
            "memory_signal": memory_signal,
            "summary_match_counts": summary_counts,
            "matching_symbol_count": len(matching_symbols),
            "rank": rank or None,
        }

    carrier_reasons: list[str] = []
    if bool(confidence.get("guarded_strong_carrier")):
        carrier_reasons.append("guarded_strong_carrier")
    if ranked_test_support:
        carrier_reasons.append("ranked_test_support_carrier")
    if confidence.get("tier") == "weak":
        carrier_reasons.append("weak_checkpoint_carrier")
    if _is_test_path(path) and matching_symbols and not summary_counts.get("test_hints"):
        carrier_reasons.append("support_symbol_carrier")
    summary_support = sum(summary_counts.values())
    if (
        current_tokens >= 240
        and not structural_risk
        and len(matching_symbols) <= 2
        and summary_support > 0
        and content_hits <= 2
    ):
        carrier_reasons.append("concentrated_ast_carrier")
    if summary_counts.get("calls", 0) > 0 and summary_counts.get("defines", 0) == 0 and not structural_risk:
        carrier_reasons.append("callsite_only_carrier")

    compress = mode in {"summary", "skeleton", "symbols"} and current_tokens > 120 and bool(carrier_reasons)
    return {
        "role": "ast_checkpoint_carrier" if carrier_reasons else "ast_checkpoint_uncertain",
        "compress": compress,
        "reasons": _dedupe_strings(carrier_reasons),
        "owner_reasons": [],
        "memory_signal": memory_signal,
        "summary_match_counts": summary_counts,
        "matching_symbol_count": len(matching_symbols),
        "rank": rank or None,
        "max_excerpt_tokens": 180 if matching_symbols else 120,
        "allow_minimal_fallback": confidence.get("tier") == "weak" or ranked_test_support,
    }


def _ast_checkpoint_ranked_test_support_carrier(
    *,
    path: str,
    reasons: list[str],
    rank: int,
    direct_action_signal: bool,
    literal_definition_signal: bool,
) -> bool:
    if not _is_test_path(path) or rank < 3:
        return False
    if direct_action_signal or literal_definition_signal:
        return False
    has_filename_signal = _benchmark_mav_has_signal(reasons, "filename keyword match")
    has_ranking_signal = _benchmark_mav_has_signal(reasons, "matched ranking keyword:")
    return has_filename_signal and has_ranking_signal


def _benchmark_combined_reasons(selected_file: Any, scored_info: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []
    reasons.extend(str(reason) for reason in (getattr(selected_file, "reasons", None) or []) if reason)
    if scored_info:
        reasons.extend(str(reason) for reason in (scored_info.get("reasons") or []) if reason)
    return _dedupe_strings(reasons)


def _ast_checkpoint_symbol_projection(
    *,
    selected_file: Any,
    file_info: Any,
    summary_data: dict[str, Any],
    current_tokens: int,
    mode: str,
    task: str,
    changed_paths: set[str],
    max_excerpt_tokens: int,
    reasons_override: list[str] | None = None,
) -> dict[str, Any] | None:
    path = str(getattr(selected_file, "path", ""))
    if not path or path in changed_paths or mode not in {"summary", "skeleton", "symbols"}:
        return None
    text = _source_excerpt_text(file_info)
    if not text:
        return None
    lines = text.splitlines()
    if not lines:
        return None
    reasons = reasons_override or [str(reason) for reason in (getattr(selected_file, "reasons", None) or [])]
    symbols = list(getattr(selected_file, "symbols", None) or []) or _ast_checkpoint_summary_symbols(summary_data)
    terms = _source_excerpt_terms(task=task, path=path, reasons=reasons, symbols=symbols)
    if not terms:
        return None

    scored_ranges: list[tuple[float, tuple[int, int], list[str]]] = []
    for sym in symbols:
        start_line = _ast_checkpoint_symbol_attr(sym, "start_line")
        end_line = _ast_checkpoint_symbol_attr(sym, "end_line")
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        score, matched = _ast_checkpoint_symbol_score(sym, terms)
        if score <= 0:
            continue
        start = max(0, start_line - 1)
        end = min(len(lines), max(start + 1, end_line))
        start = _ast_checkpoint_include_preceding_comments(lines, start)
        scored_ranges.append((score, (start, end), matched))

    if not scored_ranges:
        return None
    scored_ranges.sort(key=lambda item: (item[0], item[1][1] - item[1][0]), reverse=True)
    ranges = [*_ast_checkpoint_header_ranges(lines, path)]
    matched_terms: list[str] = []
    for _score, span, matched in scored_ranges[:4]:
        ranges.append(span)
        matched_terms.extend(matched)
    excerpt = _source_excerpt_from_ranges(lines, _merge_excerpt_ranges(ranges), max_excerpt_tokens=max_excerpt_tokens)
    if not excerpt:
        return None
    projected_tokens = estimate_tokens(excerpt)
    if projected_tokens <= 0 or projected_tokens >= current_tokens:
        return None
    return {
        "projected_tokens": projected_tokens,
        "matched_terms": _dedupe_strings(matched_terms)[:8],
        "reason": "ast_checkpoint_symbol_spans",
    }


def _ast_checkpoint_header_ranges(lines: list[str], path: str) -> list[tuple[int, int]]:
    suffix = Path(path).suffix.lower()
    ranges: list[tuple[int, int]] = []
    header_pattern = re.compile(
        r"^\s*(from\s+\S+\s+import\s+|import\s+|package\s+|use\s+|require\(|"
        r"#include\s+|using\s+|namespace\s+)"
    )
    for index, line in enumerate(lines[:100]):
        if suffix == ".go" and index == 0 and line.strip().startswith("package "):
            ranges.append((index, index + 1))
            continue
        if header_pattern.match(line):
            ranges.append((index, index + 1))
            continue
        if ranges and index > max(end for _start, end in ranges) + 12:
            break
    return _merge_excerpt_ranges(ranges)


def _ast_checkpoint_include_preceding_comments(lines: list[str], start: int) -> int:
    cursor = start - 1
    lower_bound = max(0, start - 6)
    while cursor >= lower_bound:
        stripped = lines[cursor].strip()
        if not stripped:
            cursor -= 1
            continue
        if stripped.startswith(("#", "//", "/*", "*", "@")):
            cursor -= 1
            continue
        break
    return cursor + 1


def _ast_checkpoint_summary_match_counts(
    summary_data: dict[str, Any],
    *,
    task: str,
    path: str,
    reasons: list[str],
) -> dict[str, int]:
    terms = set(_source_excerpt_terms(task=task, path=path, reasons=reasons, symbols=[]))
    counts: dict[str, int] = {}
    for summary_field in (
        "entrypoints",
        "defines",
        "calls",
        "public_api",
        "test_hints",
        "failure_hints",
        "side_effects",
        "external_systems",
        "reads_env",
        "naming_keywords",
        "related_hints",
    ):
        hits = sum(
            1
            for value in _ast_checkpoint_summary_values(summary_data, summary_field)
            if _ast_checkpoint_value_matches(value, terms)
        )
        if hits:
            counts[summary_field] = hits
    return counts


def _ast_checkpoint_matching_symbols(
    symbols: list[Any],
    *,
    task: str,
    path: str,
    reasons: list[str],
) -> list[Any]:
    terms = _source_excerpt_terms(task=task, path=path, reasons=reasons, symbols=symbols)
    return [
        sym for sym in symbols
        if _ast_checkpoint_symbol_score(sym, terms)[0] > 0
    ]


def _ast_checkpoint_symbol_score(sym: Any, terms: list[str]) -> tuple[float, list[str]]:
    name = str(_ast_checkpoint_symbol_attr(sym, "name") or "")
    signature = str(_ast_checkpoint_symbol_attr(sym, "signature") or "")
    summary = str(_ast_checkpoint_symbol_attr(sym, "summary") or "")
    body = str(_ast_checkpoint_symbol_attr(sym, "body") or "")
    buckets = (
        (name, 5.0),
        (signature, 4.0),
        (summary, 3.0),
        (body[:4000], 1.0),
    )
    score = 0.0
    matched: list[str] = []
    for term in terms:
        term_lc = term.lower()
        for value, weight in buckets:
            if term_lc and term_lc in value.lower():
                score += weight
                matched.append(term_lc)
                break
    return score, _dedupe_strings(matched)


def _ast_checkpoint_value_matches(value: str, terms: set[str]) -> bool:
    value_lc = value.lower()
    return any(term in value_lc for term in terms if len(term) >= 3)


def _ast_checkpoint_summary_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            return {}
    return {}


def _ast_checkpoint_summary_symbols(summary_data: dict[str, Any]) -> list[Any]:
    raw = summary_data.get("symbols") or []
    return list(raw) if isinstance(raw, list) else []


def _ast_checkpoint_summary_values(summary_data: dict[str, Any], field: str) -> list[str]:
    value = summary_data.get(field)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.extend(str(item.get(key) or "") for key in ("name", "signature", "summary") if item.get(key))
            else:
                name = getattr(item, "name", None)
                signature = getattr(item, "signature", None)
                summary = getattr(item, "summary", None)
                out.extend(str(part) for part in (name, signature, summary) if part)
        return out
    return []


def _ast_checkpoint_symbol_attr(sym: Any, field: str) -> Any:
    if isinstance(sym, dict):
        return sym.get(field)
    return getattr(sym, field, None)


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


_SOURCE_EXCERPT_STRUCTURAL_RISK_WORDS = {
    "adapter",
    "binding",
    "client",
    "command",
    "context",
    "controller",
    "core",
    "engine",
    "handler",
    "manager",
    "model",
    "models",
    "parser",
    "provider",
    "registry",
    "router",
    "schema",
    "serializer",
    "service",
    "store",
    "type",
    "types",
}


def _source_excerpt_should_compress(
    *,
    confidence: dict[str, Any],
    policy: str,
) -> tuple[bool, list[str]]:
    tier = str(confidence.get("tier") or "")
    structural_risk = bool(confidence.get("structural_risk"))
    action_mismatch = bool(confidence.get("action_mismatch"))
    medium_safe = bool(confidence.get("medium_compression_safe"))
    strong_carrier = bool(confidence.get("strong_carrier"))
    reasons: list[str] = []

    if policy == "label_free_tiered_source_excerpt_v1":
        if tier == "strong":
            return False, ["protected_strong"]
        return True, [f"compress_{tier}"]

    if policy == "weak_only_source_excerpt_v1":
        if tier == "weak":
            return True, ["compress_weak"]
        return False, [f"protect_{tier}"]

    if policy == "weak_action_mismatch_source_excerpt_v1":
        if tier == "strong":
            return False, ["protected_strong"]
        if tier == "weak":
            reasons.append("compress_weak")
        elif action_mismatch and not structural_risk:
            reasons.append("compress_action_mismatch")
        return bool(reasons), reasons or [f"protect_{tier}"]

    if policy == "strong_carrier_source_excerpt_v1":
        if strong_carrier:
            return True, ["compress_strong_carrier"]
        if tier == "strong":
            return False, ["protect_strong_action_owner"]
        return False, [f"protect_{tier}"]

    if policy == "guarded_strong_carrier_source_excerpt_v1":
        if bool(confidence.get("guarded_strong_carrier")):
            return True, ["compress_guarded_strong_carrier"]
        if tier == "strong":
            return False, ["protect_strong_or_structural_carrier"]
        return False, [f"protect_{tier}"]

    if policy == "weak_plus_strong_carrier_source_excerpt_v1":
        if strong_carrier:
            return True, ["compress_strong_carrier"]
        if tier == "weak":
            return True, ["compress_weak"]
        if tier == "strong":
            return False, ["protect_strong_action_owner"]
        return False, [f"protect_{tier}"]

    if policy == "weak_plus_guarded_strong_carrier_source_excerpt_v1":
        if bool(confidence.get("guarded_strong_carrier")):
            return True, ["compress_guarded_strong_carrier"]
        if tier == "weak":
            return True, ["compress_weak"]
        if tier == "strong":
            return False, ["protect_strong_or_structural_carrier"]
        return False, [f"protect_{tier}"]

    if policy == "risk_aware_tiered_source_excerpt_v1":
        if tier == "strong":
            return False, ["protected_strong"]
        if structural_risk:
            return False, ["protect_structural_risk"]
        if tier == "weak":
            return True, ["compress_weak"]
        if action_mismatch:
            return True, ["compress_action_mismatch"]
        if tier == "medium" and medium_safe:
            return True, ["compress_medium_evidence_retained"]
        return False, [f"protect_{tier}"]

    return False, ["unknown_policy"]


def _source_excerpt_confidence_tier(
    *,
    path: str,
    mode: str,
    reasons: list[str],
    current_tokens: int,
    changed_paths: set[str],
    symbols: list[Any],
) -> dict[str, Any]:
    family = _path_family(path)
    content_hits = _content_keyword_hits_from_reasons(reasons)
    direct, graph, structural, symbolic = _benchmark_mav_support_signals(reasons)
    direct_symbol = _benchmark_mav_has_signal(
        reasons,
        "matched call:",
        "matched define:",
        "matched entrypoint:",
        "matched env read:",
        "matched side effect:",
        "quoted literal match:",
        "literal definition match:",
    )
    related_test = _benchmark_mav_has_signal(
        reasons,
        "has related tests",
        "related test",
        "test for high-scoring",
        "explicit test task file",
    )
    broad_local = _benchmark_mav_has_signal(
        reasons,
        "filename keyword match",
        "symbol keyword match",
        "matched ranking keyword:",
        "matched role keyword:",
    )
    supported = direct or graph or structural or symbolic or direct_symbol or related_test
    content_only = content_hits > 0 and not supported
    recent_only = _benchmark_mav_has_signal(reasons, "recently modified") and not supported
    churn_only = _benchmark_mav_has_signal(reasons, "high churn") and not supported
    hub_path = _benchmark_mav_is_hub_path(path)
    structural_risk = _source_excerpt_has_structural_risk(path)
    changed = path in changed_paths
    confirmation_count = sum([
        bool(direct),
        bool(graph),
        bool(structural),
        bool(symbolic),
        bool(direct_symbol),
        bool(related_test),
        content_hits >= 3,
    ])
    action_mismatch = _source_excerpt_action_mismatch(
        path=path,
        reasons=reasons,
        family=family,
        content_hits=content_hits,
        supported=supported,
        broad_local=broad_local,
        hub_path=hub_path,
        structural_risk=structural_risk,
    )
    direct_action_signal = _source_excerpt_has_direct_action_signal(reasons)

    score = 0.0
    tier_reasons: list[str] = []
    if changed:
        score += 100.0
        tier_reasons.append("changed_path")
    if direct:
        score += 42.0
        tier_reasons.append("direct_evidence")
    if direct_symbol:
        score += 35.0
        tier_reasons.append("direct_symbol")
    if graph:
        score += 30.0
        tier_reasons.append("graph_confirmation")
    if structural:
        score += 24.0
        tier_reasons.append("structural_confirmation")
    if symbolic:
        score += 18.0
        tier_reasons.append("symbolic_confirmation")
    if related_test:
        score += 26.0
        tier_reasons.append("test_confirmation")
    if content_hits:
        score += min(content_hits, 6) * 5.0
        tier_reasons.append(f"content_hits_{content_hits}")
    if mode == "full":
        score += 10.0
        tier_reasons.append("full_mode")
    if len(symbols) >= 3 and not broad_local:
        score += 8.0
        tier_reasons.append("multiple_symbols")

    if family in {"config", "docs", "examples", "fixtures"} and not structural and not direct_symbol:
        score -= 18.0
        tier_reasons.append(f"{family}_family_penalty")
    if hub_path and confirmation_count < 2:
        score -= 30.0
        tier_reasons.append("hub_path_penalty")
    if structural_risk and confirmation_count >= 1:
        score += 14.0
        tier_reasons.append("structural_risk_protection")
    if content_only:
        score -= 32.0
        tier_reasons.append("content_only_penalty")
    if broad_local and not supported:
        score -= 18.0
        tier_reasons.append("broad_local_penalty")
    if recent_only:
        score -= 10.0
        tier_reasons.append("recent_only_penalty")
    if churn_only:
        score -= 10.0
        tier_reasons.append("churn_only_penalty")
    if family == "test" and not related_test and not direct_symbol and confirmation_count < 2:
        score -= 16.0
        tier_reasons.append("generic_test_penalty")
    if current_tokens > 320 and confirmation_count <= 1:
        score -= 10.0
        tier_reasons.append("large_low_confirmation_penalty")
    if action_mismatch:
        score -= 8.0
        tier_reasons.append("action_mismatch")

    if changed or related_test or (direct_symbol and confirmation_count >= 2) or (score >= 72.0 and confirmation_count >= 2):
        tier = "strong"
    elif score >= 30.0 or direct_symbol or graph or structural or symbolic or content_hits >= 3:
        tier = "medium"
    else:
        tier = "weak"
    medium_compression_safe = (
        tier == "medium"
        and action_mismatch
        and not structural_risk
        and not direct
        and not graph
        and not structural
        and not direct_symbol
        and not related_test
        and confirmation_count <= 1
    )
    strong_action_owner, carrier_reasons = _source_excerpt_strong_role(
        path=path,
        family=family,
        reasons=reasons,
        current_tokens=current_tokens,
        changed=changed,
        related_test=related_test,
        direct_symbol=direct_symbol,
        direct_action_signal=direct_action_signal,
        graph=graph,
        structural=structural,
        symbolic=symbolic,
        broad_local=broad_local,
        hub_path=hub_path,
        structural_risk=structural_risk,
        content_hits=content_hits,
        confirmation_count=confirmation_count,
    )
    strong_carrier = tier == "strong" and not strong_action_owner and bool(carrier_reasons)
    guarded_strong_carrier = strong_carrier and _source_excerpt_guarded_strong_carrier(
        carrier_reasons=carrier_reasons,
        structural_risk=structural_risk,
    )
    role_tier = "strong_carrier" if strong_carrier else ("strong_action_owner" if tier == "strong" else tier)

    return {
        "tier": tier,
        "role_tier": role_tier,
        "score": score,
        "reasons": tier_reasons or ["no_independent_confirmation"],
        "action_mismatch": action_mismatch,
        "medium_compression_safe": medium_compression_safe,
        "structural_risk": structural_risk,
        "confirmation_count": confirmation_count,
        "strong_action_owner": strong_action_owner if tier == "strong" else False,
        "strong_carrier": strong_carrier,
        "guarded_strong_carrier": guarded_strong_carrier,
        "carrier_reasons": carrier_reasons,
        "direct_action_signal": direct_action_signal,
    }


def _source_excerpt_guarded_strong_carrier(*, carrier_reasons: list[str], structural_risk: bool) -> bool:
    carrier_reason_set = set(carrier_reasons)
    return (
        ("support_file_symbol_carrier" in carrier_reason_set)
        or ("hub_symbol_carrier" in carrier_reason_set and not structural_risk)
    )


def _source_excerpt_has_direct_action_signal(reasons: list[str]) -> bool:
    return _benchmark_mav_has_signal(
        reasons,
        "direct content evidence",
        "keyword phrase match:",
        "literal definition match:",
        "matched entrypoint:",
        "matched env read:",
        "matched external system:",
        "matched side effect:",
        "quoted literal match:",
    )


def _source_excerpt_strong_role(
    *,
    path: str,
    family: str,
    reasons: list[str],
    current_tokens: int,
    changed: bool,
    related_test: bool,
    direct_symbol: bool,
    direct_action_signal: bool,
    graph: bool,
    structural: bool,
    symbolic: bool,
    broad_local: bool,
    hub_path: bool,
    structural_risk: bool,
    content_hits: int,
    confirmation_count: int,
) -> tuple[bool, list[str]]:
    action_owner_reasons: list[str] = []
    if changed:
        action_owner_reasons.append("changed_path_owner")
    if related_test:
        action_owner_reasons.append("direct_test_target_owner")
    if direct_action_signal and content_hits >= 3:
        action_owner_reasons.append("direct_action_dense_owner")
    if direct_symbol and confirmation_count >= 3 and content_hits >= 4:
        action_owner_reasons.append("multi_confirmed_symbol_owner")
    if (
        direct_symbol
        and confirmation_count >= 2
        and current_tokens < 240
        and not hub_path
        and not structural_risk
        and not broad_local
    ):
        action_owner_reasons.append("small_focused_symbol_owner")

    if action_owner_reasons:
        return True, []

    carrier_reasons: list[str] = []
    if direct_symbol:
        if hub_path:
            carrier_reasons.append("hub_symbol_carrier")
        if structural_risk:
            carrier_reasons.append("structural_symbol_carrier")
        if broad_local:
            carrier_reasons.append("broad_local_symbol_carrier")
        if current_tokens >= 240 and content_hits <= 2:
            carrier_reasons.append("large_low_density_symbol_carrier")
        if family in {"test", "fixtures", "examples"}:
            carrier_reasons.append("support_file_symbol_carrier")
    if graph and current_tokens >= 240 and not direct_action_signal:
        carrier_reasons.append("dependency_neighbor_carrier")
    if structural and current_tokens >= 240 and not direct_action_signal:
        carrier_reasons.append("structural_neighbor_carrier")
    if symbolic and current_tokens >= 240 and content_hits <= 2 and not direct_action_signal:
        carrier_reasons.append("symbolic_confirmation_carrier")
    if (hub_path or structural_risk) and current_tokens >= 320 and content_hits <= 3 and not direct_action_signal:
        carrier_reasons.append("broad_api_surface_carrier")

    seen: set[str] = set()
    deduped: list[str] = []
    for reason in carrier_reasons:
        if reason in seen:
            continue
        seen.add(reason)
        deduped.append(reason)
    return False, deduped


def _source_excerpt_has_structural_risk(path: str) -> bool:
    lowered = path.lower()
    path_obj = Path(lowered)
    parts = [path_obj.stem, *path_obj.parts]
    tokens: set[str] = set()
    for part in parts:
        tokens.update(_split_excerpt_terms(part))
    if tokens & _SOURCE_EXCERPT_STRUCTURAL_RISK_WORDS:
        return True
    return any(
        f"/{word}." in lowered or f"/{word}/" in lowered
        for word in _SOURCE_EXCERPT_STRUCTURAL_RISK_WORDS
    )


def _source_excerpt_action_mismatch(
    *,
    path: str,
    reasons: list[str],
    family: str,
    content_hits: int,
    supported: bool,
    broad_local: bool,
    hub_path: bool,
    structural_risk: bool,
) -> bool:
    if structural_risk:
        return False
    non_action_family = family in {"config", "docs", "examples", "fixtures"}
    weak_metadata = _benchmark_mav_has_signal(
        reasons,
        "release/version metadata",
        "config file",
        "large supported file",
    )
    explicit_dampening = _benchmark_mav_has_signal(
        reasons,
        "explicit test task non-test dampening",
        "backend-specific frontend dampening",
        "frontend-specific backend dampening",
    )
    broad_without_support = (broad_local or content_hits <= 1 or hub_path) and not supported
    return bool(
        explicit_dampening
        or (non_action_family and not supported)
        or (weak_metadata and not supported)
        or broad_without_support
    )


def _minimal_source_excerpt_projection(
    *,
    file_info: Any,
    current_tokens: int,
    path: str,
    max_excerpt_tokens: int,
    minimal_excerpt_tokens: int,
) -> dict[str, Any] | None:
    if current_tokens <= minimal_excerpt_tokens:
        return None
    text = _source_excerpt_text(file_info)
    if not text:
        projected_tokens = min(current_tokens, minimal_excerpt_tokens)
        if projected_tokens >= current_tokens:
            return None
        return {
            "projected_tokens": projected_tokens,
            "matched_terms": [],
            "reason": "oracle_minimal_token_floor_no_source",
        }

    lines = text.splitlines()
    if not lines:
        return None
    ranges = _minimal_source_excerpt_ranges(lines, path)
    excerpt = _source_excerpt_from_ranges(lines, ranges, max_excerpt_tokens=max_excerpt_tokens)
    projected_tokens = estimate_tokens(excerpt) if excerpt else 0
    if projected_tokens <= 0:
        projected_tokens = minimal_excerpt_tokens
    projected_tokens = max(minimal_excerpt_tokens, min(projected_tokens, max_excerpt_tokens))
    if projected_tokens >= current_tokens:
        return None
    return {
        "projected_tokens": projected_tokens,
        "matched_terms": [],
        "reason": "oracle_minimal_source_excerpt",
    }


def _minimal_source_excerpt_ranges(lines: list[str], path: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    suffix = Path(path).suffix.lower()
    if suffix in {".md", ".markdown", ".rst", ".txt"}:
        ranges.append((0, min(len(lines), 12)))
        for index, line in enumerate(lines[:120]):
            if line.lstrip().startswith(("#", "##", "###")):
                ranges.append((index, min(len(lines), index + 8)))
                break
        return _merge_excerpt_ranges(ranges)

    if suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        return [(0, min(len(lines), 16))]

    header_indexes: list[int] = []
    header_pattern = re.compile(
        r"^\s*(from\s+\S+\s+import\s+|import\s+|package\s+|use\s+|require\(|"
        r"#include\s+|using\s+|namespace\s+)"
    )
    for index, line in enumerate(lines[:120]):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*")):
            continue
        if header_pattern.match(line):
            header_indexes.append(index)
            continue
        if header_indexes and index > max(header_indexes) + 8:
            break
    for index in header_indexes[:24]:
        ranges.append((index, index + 1))

    structural_pattern = re.compile(
        r"^\s*(def |async def |class |func |function |(?:export\s+)?(?:async\s+)?function |"
        r"(?:export\s+)?class |(?:public|private|protected|internal|static|final)\s+)"
    )
    for index, line in enumerate(lines[:240]):
        if not structural_pattern.match(line):
            continue
        start, end = _source_excerpt_enclosing_block(lines, index)
        if start is None or end is None:
            start = index
            end = min(len(lines), index + 8)
        ranges.append((start, min(end, start + 24)))
        break

    if not ranges:
        ranges.append((0, min(len(lines), 12)))
    return _merge_excerpt_ranges(ranges)


def _source_excerpt_guarded_candidate(*, path: str, mode: str, reasons: list[str]) -> bool:
    if mode not in {"summary", "skeleton", "symbols"}:
        return False
    family = _path_family(path)
    content_hits = _content_keyword_hits_from_reasons(reasons)
    strong_markers = (
        "direct content evidence",
        "direct dependency",
        "has related tests",
        "keyword phrase match:",
        "literal definition match:",
        "matched call:",
        "matched define:",
        "matched entrypoint:",
        "matched external system:",
        "quoted literal match:",
        "related test",
        "test for high-scoring",
    )
    if any(reason.startswith(strong_markers) for reason in reasons):
        return False
    if family in {"config", "docs", "examples", "fixtures"}:
        return True
    has_broad_local = any(
        reason == "symbol keyword match"
        or reason.startswith(("filename keyword match", "matched ranking keyword:", "matched role keyword:"))
        for reason in reasons
    )
    if family == "test":
        return has_broad_local and content_hits <= 2
    if family == "source":
        return has_broad_local and content_hits <= 1
    return False


def _content_keyword_hits_from_reasons(reasons: list[str]) -> int:
    hits = 0
    for reason in reasons:
        match = re.match(r"content keyword match \((\d+)\)", reason)
        if match:
            hits = max(hits, int(match.group(1)))
    return hits


def _source_excerpt_projection(
    *,
    selected_file: Any,
    file_info: Any,
    current_tokens: int,
    mode: str,
    task: str,
    changed_paths: set[str],
    max_excerpt_tokens: int,
    reasons_override: list[str] | None = None,
) -> dict[str, Any] | None:
    path = str(getattr(selected_file, "path", ""))
    if not path or path in changed_paths:
        return None
    if mode not in {"summary", "skeleton", "symbols"}:
        return None
    if current_tokens <= max(80, max_excerpt_tokens // 2):
        return None

    text = _source_excerpt_text(file_info)
    if not text:
        return None
    lines = text.splitlines()
    if not lines:
        return None

    reasons = reasons_override or [str(reason) for reason in (getattr(selected_file, "reasons", None) or [])]
    terms = _source_excerpt_terms(
        task=task,
        path=path,
        reasons=reasons,
        symbols=getattr(selected_file, "symbols", None) or [],
    )
    ranges, matched_terms = _source_excerpt_ranges(
        lines=lines,
        terms=terms,
        path=path,
        symbols=getattr(selected_file, "symbols", None) or [],
    )
    if not ranges:
        return None

    excerpt = _source_excerpt_from_ranges(lines, ranges, max_excerpt_tokens=max_excerpt_tokens)
    if not excerpt:
        return None
    projected_tokens = estimate_tokens(excerpt)
    if projected_tokens <= 0 or projected_tokens >= current_tokens:
        return None
    return {
        "projected_tokens": projected_tokens,
        "matched_terms": matched_terms,
        "reason": "source_line_windows",
    }


def _source_excerpt_text(file_info: Any) -> str:
    content = getattr(file_info, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    abs_path = getattr(file_info, "abs_path", None)
    if isinstance(abs_path, Path) and abs_path.exists():
        try:
            return abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return ""


def _source_excerpt_terms(
    *,
    task: str,
    path: str,
    reasons: list[str],
    symbols: list[Any],
) -> list[str]:
    raw_parts: list[str] = []
    raw_parts.extend(_split_excerpt_terms(task))
    raw_parts.extend(_split_excerpt_terms(Path(path).stem))
    for reason in reasons:
        reason_lc = reason.lower()
        if reason_lc.startswith((
            "keyword phrase match:",
            "literal definition match:",
            "matched call:",
            "matched define:",
            "matched entrypoint:",
            "matched env read:",
            "matched external system:",
            "matched naming keyword:",
            "matched ranking keyword:",
            "matched role keyword:",
            "matched side effect:",
            "quoted literal match:",
        )):
            raw_parts.extend(_split_excerpt_terms(reason.split(":", 1)[1]))
    for sym in symbols[:20]:
        name = getattr(sym, "name", "")
        if isinstance(sym, dict):
            name = str(sym.get("name") or "")
        raw_parts.extend(_split_excerpt_terms(name))

    seen: set[str] = set()
    terms: list[str] = []
    for part in raw_parts:
        term = part.lower()
        if len(term) < 3 or term in _EXCERPT_STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms[:32]


def _split_excerpt_terms(value: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
    return [
        token.lower()
        for token in re.split(r"[^A-Za-z0-9_]+", spaced)
        if token
    ]


def _source_excerpt_ranges(
    *,
    lines: list[str],
    terms: list[str],
    path: str,
    symbols: list[Any],
) -> tuple[list[tuple[int, int]], list[str]]:
    if not terms:
        return [], []
    lowered = [line.lower() for line in lines]
    matched_terms: list[str] = []
    ranges: list[tuple[int, int]] = []
    window = 6 if _is_test_path(path) else 4

    for index, line in enumerate(lowered):
        line_matches = [term for term in terms if term in line]
        if not line_matches:
            continue
        for term in line_matches:
            if term not in matched_terms:
                matched_terms.append(term)
        start, end = _source_excerpt_enclosing_block(lines, index)
        if start is None or end is None:
            start = max(0, index - window)
            end = min(len(lines), index + window + 1)
        ranges.append((start, end))

    for sym in symbols[:20]:
        name = getattr(sym, "name", "")
        start_line = getattr(sym, "start_line", None)
        end_line = getattr(sym, "end_line", None)
        if isinstance(sym, dict):
            name = str(sym.get("name") or "")
            start_line = sym.get("start_line")
            end_line = sym.get("end_line")
        if not name or not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        name_terms = _split_excerpt_terms(name)
        if not any(term in terms for term in name_terms):
            continue
        start = max(0, start_line - 1)
        end = min(len(lines), max(start + 1, end_line))
        ranges.append((start, end))

    return _merge_excerpt_ranges(ranges), matched_terms


def _source_excerpt_enclosing_block(lines: list[str], index: int) -> tuple[int | None, int | None]:
    def_pattern = re.compile(
        r"^(\s*)(def |async def |class |function |(?:export\s+)?(?:async\s+)?function |(?:export\s+)?class |func\s+)"
    )
    start: int | None = None
    indent = 0
    for cursor in range(index, -1, -1):
        match = def_pattern.match(lines[cursor])
        if match:
            start = cursor
            indent = len(match.group(1))
            break
    if start is None:
        return None, None
    end = min(len(lines), start + 80)
    for cursor in range(start + 1, min(len(lines), start + 120)):
        if not lines[cursor].strip():
            continue
        current_indent = len(lines[cursor]) - len(lines[cursor].lstrip())
        if current_indent <= indent and def_pattern.match(lines[cursor]):
            end = cursor
            break
    return start, end


def _merge_excerpt_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _source_excerpt_from_ranges(
    lines: list[str],
    ranges: list[tuple[int, int]],
    *,
    max_excerpt_tokens: int,
) -> str:
    chunks: list[str] = []
    used = 0
    for start, end in ranges:
        chunk = "\n".join(lines[start:end]).strip()
        if not chunk:
            continue
        prefix = f"# lines {start + 1}-{end}"
        candidate = f"{prefix}\n{chunk}"
        tokens = estimate_tokens(candidate)
        if chunks and used + tokens > max_excerpt_tokens:
            continue
        if not chunks and tokens > max_excerpt_tokens:
            limited: list[str] = [prefix]
            for line in chunk.splitlines():
                next_text = "\n".join([*limited, line])
                if estimate_tokens(next_text) > max_excerpt_tokens:
                    break
                limited.append(line)
            candidate = "\n".join(limited).strip()
            tokens = estimate_tokens(candidate)
        if tokens <= 0 or used + tokens > max_excerpt_tokens:
            continue
        chunks.append(candidate)
        used += tokens
    return "\n\n".join(chunks).strip()


def _would_select_with_one_more_slot(
    *,
    scored_info: dict[str, Any] | None,
    selected_count: int,
    status: str,
) -> bool:
    if scored_info is None:
        return False
    if any(term in status.lower() for term in ("not found", "ignored", "binary", "scored too low")):
        return False
    return int(scored_info.get("rank") or 0) <= selected_count + 1


def _score_delta_vs_last_selected(
    *,
    scored_info: dict[str, Any] | None,
    selected_paths: list[str],
    scored_map: dict[str, dict[str, Any]],
) -> float | None:
    if scored_info is None:
        return None
    for path in reversed(selected_paths):
        selected_info = scored_map.get(path)
        if selected_info:
            delta = float(scored_info["score"]) - float(selected_info["score"])
            return round(delta, 1)
    return None


def _selected_noise_that_beat_expected(
    *,
    scored_info: dict[str, Any] | None,
    selected_noise: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if scored_info is None:
        return selected_noise[0] if selected_noise else None
    expected_rank = int(scored_info.get("rank") or 0)
    expected_score = float(scored_info.get("score") or 0.0)
    ranked_noise = [
        row for row in selected_noise
        if row.get("rank") is not None and int(row["rank"]) < expected_rank
    ]
    if not ranked_noise:
        ranked_noise = [
            row for row in selected_noise
            if row.get("score") is not None and float(row["score"]) >= expected_score
        ]
    return ranked_noise[0] if ranked_noise else None


_CAP_STRONG_REASON_PREFIXES = (
    "direct content evidence",
    "direct dependency",
    "has related tests",
    "historically co-changed",
    "keyword phrase match:",
    "literal definition match:",
    "matched call:",
    "matched define:",
    "matched entrypoint:",
    "matched env read:",
    "matched external system:",
    "matched side effect:",
    "multi-token",
    "quoted literal match:",
    "release/version metadata",
    "reverse dependency",
    "test for",
    "workspace match",
)


def _cap_block_diagnostic(
    *,
    status: str,
    fi: Any,
    scored_info: dict[str, Any] | None,
    summaries: dict[str, Any],
    selected_by_path: dict[str, Any],
    selected_tokens: dict[str, int],
    expected_set: set[str],
    packed_tokens: int,
    budget: int,
) -> dict[str, Any] | None:
    if "cap reached" not in status.lower():
        return None
    candidate_path = str(getattr(fi, "path", ""))
    candidate_tokens, candidate_mode = _candidate_compressed_estimate(
        candidate_path,
        fi=fi,
        score=float(scored_info["score"]) if scored_info else 0.0,
        summaries=summaries,
    )
    candidate_reasons = scored_info["reasons"] if scored_info else []
    candidate_has_strong_evidence = _cap_has_strong_evidence(candidate_reasons)
    replaceable = _replaceable_selected_noise(
        selected_by_path=selected_by_path,
        selected_tokens=selected_tokens,
        expected_set=expected_set,
    )
    replaceable_tokens = sum(item["tokens"] for item in replaceable)
    needed_tokens = max(0, packed_tokens + candidate_tokens - budget)
    if not candidate_has_strong_evidence:
        block_reason = "candidate evidence below replacement gate"
    elif not replaceable:
        block_reason = "no replaceable selected compressed noise"
    elif replaceable_tokens < needed_tokens:
        block_reason = "candidate too large for replaceable selected noise"
    else:
        block_reason = "replacement appears feasible"
    return {
        "candidate_tokens": candidate_tokens,
        "candidate_mode": candidate_mode,
        "candidate_has_strong_evidence": candidate_has_strong_evidence,
        "needed_tokens": needed_tokens,
        "replaceable_selected_tokens": replaceable_tokens,
        "replaceable_selected": replaceable[:5],
        "block_reason": block_reason,
    }


def _candidate_compressed_estimate(
    path: str,
    *,
    fi: Any,
    score: float,
    summaries: dict[str, Any],
) -> tuple[int, str]:
    summary_data = summaries.get(path) or {}
    symbols = _summary_symbols(summary_data)
    if symbols and score >= 160:
        parts: list[str] = []
        summary = str(summary_data.get("summary") or "").strip() if isinstance(summary_data, dict) else ""
        if summary:
            parts.append(summary)
        parts.extend(signature for signature in symbols if signature)
        text = "\n".join(parts)
        return (estimate_tokens(text) if text else 50), "skeleton"
    if isinstance(summary_data, dict):
        summary = str(summary_data.get("summary") or "").strip()
        if summary:
            return estimate_tokens(summary), "summary"
    return min(int(getattr(fi, "estimated_tokens", 0) or 0), 200) or 50, "summary"


def _summary_symbols(summary_data: Any) -> list[str]:
    if not isinstance(summary_data, dict):
        return []
    signatures: list[str] = []
    for item in summary_data.get("symbols") or []:
        if isinstance(item, dict):
            signature = item.get("signature")
            if signature:
                signatures.append(str(signature))
        elif hasattr(item, "signature") and item.signature:
            signatures.append(str(item.signature))
    return signatures


def _cap_has_strong_evidence(reasons: list[str]) -> bool:
    content_hits = 0
    for reason in reasons:
        match = re.match(r"content keyword match \((\d+)\)", reason)
        if match:
            content_hits = max(content_hits, int(match.group(1)))
    if content_hits >= 3 and any(reason.startswith(("matched define:", "matched call:", "keyword phrase match:")) for reason in reasons):
        return True
    if "config file" in reasons and content_hits >= 2:
        return True
    return any(reason.startswith(_CAP_STRONG_REASON_PREFIXES) for reason in reasons)


def _replacement_pair_diagnostics(
    receipts: list[Any],
    scored_map: dict[str, dict[str, Any]],
    selected_tokens: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    marker = "marginal slot replaced by "
    for receipt in receipts:
        reason = getattr(receipt, "reason", "")
        if not isinstance(reason, str) or marker not in reason:
            continue
        displaced_path = getattr(receipt, "path", "")
        challenger_path = reason.split(marker, 1)[1].strip()
        displaced = scored_map.get(displaced_path, {})
        challenger = scored_map.get(challenger_path, {})
        rows.append({
            "displaced": displaced_path,
            "challenger": challenger_path,
            "displaced_score": round(float(displaced.get("score", 0.0) or 0.0), 1),
            "challenger_score": round(float(challenger.get("score", 0.0) or 0.0), 1),
            "challenger_rank": challenger.get("rank"),
            "displaced_tokens": selected_tokens.get(displaced_path, 0),
            "challenger_reasons": list(challenger.get("reasons", []) or [])[:4],
            "displaced_reasons": list(displaced.get("reasons", []) or [])[:4],
        })
    return rows[:20]


def _same_scope_replacement_opportunities(
    *,
    missed_expected: list[dict[str, Any]],
    selected_noise: list[dict[str, Any]],
    scored_map: dict[str, dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for miss in missed_expected:
        missed_path = str(miss.get("path") or "")
        missed_info = scored_map.get(missed_path) or {}
        cap_diagnostic = miss.get("cap_block_diagnostic")
        if isinstance(cap_diagnostic, dict):
            missed_tokens = int(cap_diagnostic.get("candidate_tokens") or 0)
        else:
            missed_tokens = int(missed_info.get("estimated_tokens") or 0)
        if not missed_path or missed_tokens <= 0 or miss.get("rank") is None:
            continue
        if not _diagnostic_replacement_status(str(miss.get("status") or "")):
            continue
        missed_scope = _diagnostic_scope(missed_path)
        missed_reasons = list(miss.get("reasons") or [])
        missed_evidence = _diagnostic_evidence_score(
            path=missed_path,
            score=float(miss.get("score") or 0.0),
            reasons=missed_reasons,
        )
        for noise in selected_noise:
            noise_path = str(noise.get("path") or "")
            noise_tokens = int(noise.get("tokens") or 0)
            if not noise_path or noise_tokens <= 0 or missed_tokens > noise_tokens:
                continue
            noise_scope = _diagnostic_scope(noise_path)
            if not _diagnostic_related_scope(missed_scope, noise_scope):
                continue
            noise_reasons = list(noise.get("reasons") or [])
            noise_evidence = _diagnostic_evidence_score(
                path=noise_path,
                score=float(noise.get("score") or 0.0),
                reasons=noise_reasons,
            )
            evidence_gain = missed_evidence - noise_evidence
            if evidence_gain < 25:
                continue
            rows.append({
                "missed": missed_path,
                "selected_noise": noise_path,
                "scope": missed_scope,
                "missed_rank": miss.get("rank"),
                "noise_rank": noise.get("rank"),
                "missed_score": miss.get("score"),
                "noise_score": noise.get("score"),
                "missed_tokens": missed_tokens,
                "noise_tokens": noise_tokens,
                "token_delta": missed_tokens - noise_tokens,
                "missed_evidence": round(missed_evidence, 1),
                "noise_evidence": round(noise_evidence, 1),
                "evidence_gain": round(evidence_gain, 1),
                "missed_reasons": missed_reasons[:4],
                "noise_reasons": noise_reasons[:4],
            })

    return sorted(
        rows,
        key=lambda row: (
            -float(row["evidence_gain"]),
            int(row["token_delta"]),
            int(row["missed_rank"] or 999999),
            int(row["noise_rank"] or 999999),
        ),
    )[:limit]


def _plausibly_useful_selected_noise(
    *,
    selected_noise: list[dict[str, Any]],
    expected_set: set[str],
    scored_map: dict[str, dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    expected_scopes = {_diagnostic_scope(path) for path in expected_set}
    expected_packages = {_workspace_package(path) for path in expected_set}
    expected_families = {_path_family(path) for path in expected_set}
    rows: list[dict[str, Any]] = []
    for noise in selected_noise:
        path = str(noise.get("path") or "")
        if not path:
            continue
        scope = _diagnostic_scope(path)
        package = _workspace_package(path)
        family = _path_family(path)
        reasons: list[str] = []
        if any(_diagnostic_related_scope(scope, expected_scope) for expected_scope in expected_scopes):
            reasons.append("same_or_related_scope_as_expected")
        if package and package in expected_packages:
            reasons.append("same_workspace_package_as_expected")
        if family in expected_families and _cap_has_strong_evidence(list(noise.get("reasons") or [])):
            reasons.append("same_family_with_strong_evidence")
        if not reasons:
            continue
        scored_info = scored_map.get(path) or {}
        rows.append({
            "path": path,
            "family": family,
            "scope": scope,
            "workspace_package": package,
            "rank": noise.get("rank"),
            "score": noise.get("score"),
            "tokens": noise.get("tokens"),
            "plausibility_reasons": reasons,
            "selection_reasons": list(noise.get("reasons") or scored_info.get("reasons") or [])[:4],
        })
    return sorted(
        rows,
        key=lambda row: (
            int(row["rank"] or 999999),
            -float(row["score"] or 0.0),
            str(row["path"]),
        ),
    )[:limit]


def _label_audit_summary(
    *,
    selected_noise: list[dict[str, Any]],
    plausibly_useful: list[dict[str, Any]],
    packed_tokens: int,
) -> dict[str, Any]:
    noise_tokens = sum(int(row.get("tokens") or 0) for row in selected_noise)
    plausible_tokens = sum(int(row.get("tokens") or 0) for row in plausibly_useful)
    audited_noise_tokens = max(0, noise_tokens - plausible_tokens)
    adjusted_token_precision = None
    if packed_tokens > 0:
        adjusted_token_precision = 1 - (audited_noise_tokens / packed_tokens)
    return {
        "selected_noise_count": len(selected_noise),
        "selected_noise_tokens": noise_tokens,
        "plausibly_useful_count": len(plausibly_useful),
        "plausibly_useful_tokens": plausible_tokens,
        "audited_noise_tokens": audited_noise_tokens,
        "adjusted_token_precision": round(adjusted_token_precision, 3)
        if adjusted_token_precision is not None else None,
    }


_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "dependency_release",
        (
            "dependency",
            "dependencies",
            "deps",
            "upgrade",
            "update",
            "version",
            "release",
            "docker image",
            "license metadata",
            "spring boot",
            "java 17",
        ),
    ),
    (
        "config_build",
        (
            "config",
            "build",
            "cache",
            "env",
            "eslint",
            "checkstyle",
            "mypy",
            "ci",
            "node_modules",
            "pnp",
            "naming strategy",
        ),
    ),
    (
        "test_focus",
        (
            "test",
            "tests",
            "snapshot",
            "regression",
            "fixture",
        ),
    ),
    (
        "typing_api",
        (
            "typing",
            "type",
            "overload",
            "deprecated",
            "deprecation",
            "parameter",
            "exception",
            "api",
        ),
    ),
    (
        "cleanup_refactor",
        (
            "unused",
            "import",
            "refactor",
            "simplify",
            "cleanup",
            "polish",
            "format",
            "lint",
        ),
    ),
    (
        "docs_metadata",
        (
            "doc",
            "docs",
            "document",
            "readme",
            "changelog",
        ),
    ),
)


def _benchmark_intent_profile(
    *,
    task: str,
    expected_files: set[str],
    missed_expected: list[dict[str, Any]],
    selected_noise: list[dict[str, Any]],
) -> dict[str, Any]:
    task_lc = task.lower()
    expected_family_counts = _family_counts(expected_files)
    missed_family_counts = _family_counts(str(miss.get("path") or "") for miss in missed_expected)
    noise_family_counts: dict[str, int] = {}
    for row in selected_noise:
        family = str(row.get("family") or _path_family(str(row.get("path") or "")))
        if family:
            noise_family_counts[family] = noise_family_counts.get(family, 0) + 1

    scores: dict[str, int] = {}
    signals: list[str] = []
    for intent, terms in _INTENT_RULES:
        matches = [term for term in terms if term in task_lc]
        if matches:
            scores[intent] = scores.get(intent, 0) + len(matches) * 3
            signals.extend(f"task:{intent}:{term}" for term in matches[:3])

    if expected_family_counts.get("config", 0):
        scores["config_build"] = scores.get("config_build", 0) + expected_family_counts["config"] * 2
        signals.append("expected:config")
    if expected_family_counts.get("docs", 0):
        scores["docs_metadata"] = scores.get("docs_metadata", 0) + expected_family_counts["docs"] * 2
        signals.append("expected:docs")
    if expected_family_counts.get("test", 0) >= max(1, expected_family_counts.get("source", 0)):
        scores["test_focus"] = scores.get("test_focus", 0) + expected_family_counts["test"]
        signals.append("expected:test-heavy")
    if missed_family_counts.get("config", 0):
        scores["config_build"] = scores.get("config_build", 0) + missed_family_counts["config"]
        signals.append("missed:config")
    if missed_family_counts.get("test", 0) >= 2:
        scores["test_focus"] = scores.get("test_focus", 0) + missed_family_counts["test"]
        signals.append("missed:test-heavy")

    if not scores and any(term in task_lc for term in ("fix", "feat", "support", "add")):
        scores["source_behavior"] = 1
        signals.append("task:source_behavior")
    primary = max(sorted(scores), key=lambda key: scores[key]) if scores else "general"
    return {
        "primary": primary,
        "scores": dict(sorted(scores.items())),
        "signals": signals[:10],
        "expected_family_counts": expected_family_counts,
        "missed_family_counts": missed_family_counts,
        "selected_noise_family_counts": dict(sorted(noise_family_counts.items())),
    }


def _family_counts(paths: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        if not path:
            continue
        family = _path_family(path)
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def _owner_file_recall(*, selected_set: set[str], expected_set: set[str]) -> dict[str, Any]:
    if not expected_set:
        return {"owner_files": [], "selected": 0, "total": 0, "recall": None}
    owner_priority = min(_owner_priority(path) for path in expected_set)
    owner_files = sorted(path for path in expected_set if _owner_priority(path) == owner_priority)
    selected = sum(1 for path in owner_files if path in selected_set)
    return {
        "owner_files": owner_files,
        "selected": selected,
        "total": len(owner_files),
        "recall": round(selected / len(owner_files), 3) if owner_files else None,
        "owner_family": _path_family(owner_files[0]) if owner_files else None,
    }


def _owner_priority(path: str) -> int:
    family = _path_family(path)
    if family == "source":
        return 0
    if family == "config":
        return 1
    if family == "test":
        return 2
    if family == "docs":
        return 3
    return 4


def _expected_family_recall(*, selected_set: set[str], expected_set: set[str]) -> dict[str, dict[str, float]]:
    buckets: dict[str, dict[str, float]] = {}
    for path in expected_set:
        family = _path_family(path)
        bucket = buckets.setdefault(family, {"selected": 0.0, "expected": 0.0, "recall": 0.0})
        bucket["expected"] += 1
        if path in selected_set:
            bucket["selected"] += 1
    for bucket in buckets.values():
        expected = bucket["expected"]
        bucket["recall"] = round(bucket["selected"] / expected, 3) if expected else 0.0
    return dict(sorted(buckets.items()))


def _expected_include_mode_diagnostics(
    *,
    expected_set: set[str],
    selected_modes: dict[str, str],
) -> dict[str, Any]:
    selected_expected = sorted(path for path in expected_set if path in selected_modes)
    mode_counts: dict[str, int] = {}
    by_family: dict[str, dict[str, int]] = {}
    for path in selected_expected:
        mode = selected_modes.get(path, "missing")
        family = _path_family(path)
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        family_counts = by_family.setdefault(family, {})
        family_counts[mode] = family_counts.get(mode, 0) + 1
    summary_only = sum(1 for path in selected_expected if selected_modes.get(path) == "summary")
    return {
        "selected_expected_count": len(selected_expected),
        "expected_count": len(expected_set),
        "mode_counts": dict(sorted(mode_counts.items())),
        "by_family": {family: dict(sorted(counts.items())) for family, counts in sorted(by_family.items())},
        "source_code_block_rate": _family_actionable_mode_rate(
            expected_set=expected_set,
            selected_modes=selected_modes,
            family="source",
        ),
        "test_code_block_rate": _family_actionable_mode_rate(
            expected_set=expected_set,
            selected_modes=selected_modes,
            family="test",
        ),
        "summary_only_expected_rate": round(summary_only / len(selected_expected), 3) if selected_expected else None,
    }


def _family_actionable_mode_rate(
    *,
    expected_set: set[str],
    selected_modes: dict[str, str],
    family: str,
) -> float | None:
    paths = [path for path in expected_set if _path_family(path) == family]
    if not paths:
        return None
    actionable_modes = {"full", "diff", "symbols", "skeleton"}
    selected_actionable = sum(1 for path in paths if selected_modes.get(path) in actionable_modes)
    return round(selected_actionable / len(paths), 3)


def _expected_rank_distribution(
    expected_set: set[str],
    scored_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ranks = sorted(int(scored_map[path]["rank"]) for path in expected_set if path in scored_map and scored_map[path].get("rank"))
    if not ranks:
        return {
            "ranked_expected_count": 0,
            "unranked_expected_count": len(expected_set),
            "median": None,
            "p90": None,
            "min": None,
            "max": None,
            "buckets": {},
        }
    return {
        "ranked_expected_count": len(ranks),
        "unranked_expected_count": len(expected_set) - len(ranks),
        "median": _percentile_rank(ranks, 0.5),
        "p90": _percentile_rank(ranks, 0.9),
        "min": ranks[0],
        "max": ranks[-1],
        "buckets": {
            "1_3": sum(1 for rank in ranks if rank <= 3),
            "4_8": sum(1 for rank in ranks if 4 <= rank <= 8),
            "9_20": sum(1 for rank in ranks if 9 <= rank <= 20),
            "21_plus": sum(1 for rank in ranks if rank >= 21),
        },
    }


def _percentile_rank(sorted_ranks: list[int], percentile: float) -> int:
    if not sorted_ranks:
        return 0
    index = min(len(sorted_ranks) - 1, max(0, int(round((len(sorted_ranks) - 1) * percentile))))
    return sorted_ranks[index]


def _package_boundary_diagnostics(
    *,
    selected_paths: list[str],
    expected_set: set[str],
) -> dict[str, Any]:
    expected_packages = {_workspace_package(path) for path in expected_set if _workspace_package(path)}
    selected_packages = [_workspace_package(path) for path in selected_paths if _workspace_package(path)]
    selected_expected_package = sum(1 for package in selected_packages if package in expected_packages)
    selected_cross_package = len(selected_packages) - selected_expected_package
    return {
        "expected_packages": sorted(expected_packages),
        "selected_expected_package_files": selected_expected_package,
        "selected_cross_package_files": selected_cross_package,
        "selected_package_match_rate": round(selected_expected_package / len(selected_packages), 3)
        if selected_packages else None,
    }


def _workspace_package(path: str) -> str:
    normalized = path.lower().replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return ""
    if "packages" in parts:
        index = parts.index("packages")
        if len(parts) > index + 1:
            return "/".join(parts[:index + 2])
    if "apps" in parts:
        index = parts.index("apps")
        if len(parts) > index + 1:
            return "/".join(parts[:index + 2])
    if parts[0] in {"integration", "examples", "playground"} and len(parts) > 1:
        return "/".join(parts[:2])
    if parts[0] in {"src", "lib", "app", "tests", "test"}:
        return parts[0]
    return parts[0]


def _diagnostic_replacement_status(status: str) -> bool:
    lowered = status.lower()
    return any(term in lowered for term in ("budget", "cap reached", "compressed", "summarized", "stronger support"))


def _diagnostic_scope(path: str) -> str:
    normalized = path.lower().replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return ""
    for marker in ("src", "lib", "app", "packages", "tests", "test", "integration"):
        if marker not in parts:
            continue
        index = parts.index(marker)
        if marker == "packages" and len(parts) > index + 2:
            return "/".join(parts[:index + 3])
        tail = parts[index + 1:-1]
        depth = 2 if marker in {"src", "lib", "app"} else 1
        return "/".join(parts[:index + 1] + tail[:depth])
    if len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0]


def _diagnostic_related_scope(left: str, right: str) -> bool:
    if left == right:
        return True
    if not left or not right:
        return False
    return left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _diagnostic_evidence_score(*, path: str, score: float, reasons: list[str]) -> float:
    evidence = min(max(score, 0.0), 300.0) * 0.25
    for reason in reasons:
        lowered = reason.lower()
        content_match = re.match(r"content keyword match \((\d+)\)", lowered)
        if content_match:
            evidence += min(int(content_match.group(1)), 6) * 18
        if reason.startswith(_CAP_STRONG_REASON_PREFIXES):
            evidence += 55
        elif lowered.startswith(("filename keyword match", "symbol keyword match")):
            evidence += 12
        elif lowered.startswith(("recently modified", "high churn")):
            evidence += 5
    if _path_family(path) in {"examples", "fixtures", "generated", "docs"}:
        evidence -= 25
    return evidence


def _replaceable_selected_noise(
    *,
    selected_by_path: dict[str, Any],
    selected_tokens: dict[str, int],
    expected_set: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, sf in selected_by_path.items():
        if path in expected_set:
            continue
        mode = getattr(sf, "include_mode", "")
        reasons = list(getattr(sf, "reasons", []) or [])
        if mode not in {"summary", "skeleton"}:
            continue
        if _cap_has_strong_evidence(reasons):
            continue
        rows.append({
            "path": path,
            "tokens": selected_tokens.get(path, 0),
            "mode": mode,
            "score": round(float(getattr(sf, "score", 0.0) or 0.0), 1),
            "reasons": reasons[:4],
        })
    return sorted(rows, key=lambda row: (row["score"], -row["tokens"]))


def _path_family(path: str) -> str:
    normalized = path.lower().replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    name = parts[-1] if parts else normalized
    suffix = Path(name).suffix

    if any(part in {"docs", "doc"} for part in parts) or name.startswith("readme") or suffix in {".md", ".mdx", ".rst"}:
        return "docs"
    if any(part in {"fixtures", "fixture", "__fixtures__", "snapshots", "__snapshots__"} for part in parts):
        return "fixtures"
    if any(part in {"examples", "example", "playground", "playgrounds", "samples", "sample", "templates", "template"} for part in parts):
        return "examples"
    if any(part in {"test", "tests", "__tests__", "spec", "specs", "e2e", "integration"} for part in parts):
        return "test"
    if any(part in {"dist", "build", "generated", "__generated__", "coverage"} for part in parts):
        return "generated"
    if name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", "_test.go", "_test.py")):
        return "test"
    if name in {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "pyproject.toml",
        "go.mod",
        "go.sum",
        "pom.xml",
        "build.gradle",
        "gradle.properties",
    } or suffix in {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg"}:
        return "config"
    if suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt", ".rs", ".rb", ".php", ".cs"}:
        return "source"
    return "other"


def _selected_family_tokens(paths: list[str], selected_tokens: dict[str, int]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for path in paths:
        family = _path_family(path)
        totals[family] = totals.get(family, 0) + selected_tokens.get(path, 0)
    return dict(sorted(totals.items()))


def _reason_family(reason: str) -> str:
    reason = reason.lower()
    if reason.startswith("filename keyword match") or "matched naming keyword" in reason:
        return "filename"
    if reason.startswith("symbol keyword match") or reason.startswith("matched define"):
        return "symbol"
    if (
        reason.startswith("content keyword match")
        or reason.startswith("keyword phrase match")
        or reason.startswith("quoted literal match")
    ):
        return "content"
    if reason.startswith(("matched call", "matched entrypoint", "matched domain", "matched external system")):
        return "semantic"
    if reason.startswith(("direct dependency", "reverse dependency", "has related tests", "test for")):
        return "graph"
    if reason.startswith(("recently modified", "historically co-changed", "high churn")):
        return "history"
    if reason.startswith(("config file", "release/version metadata", "knowledge/architecture doc")):
        return "metadata"
    if reason.startswith(("matched role keyword", "matched ranking keyword")):
        return "summary"
    return "other"


def _reason_family_precision(selected: list[Any], expected_files: set[str]) -> dict[str, dict[str, float]]:
    counts: dict[str, dict[str, int]] = {}
    for sf in selected:
        path = str(getattr(sf, "path", ""))
        families = {_reason_family(reason) for reason in (getattr(sf, "reasons", None) or [])}
        for family in families:
            bucket = counts.setdefault(family, {"selected": 0, "expected": 0})
            bucket["selected"] += 1
            if path in expected_files:
                bucket["expected"] += 1

    result: dict[str, dict[str, float]] = {}
    for family, bucket in sorted(counts.items()):
        selected_count = bucket["selected"]
        expected_count = bucket["expected"]
        result[family] = {
            "selected": float(selected_count),
            "expected": float(expected_count),
            "precision": expected_count / selected_count if selected_count else 0.0,
        }
    return result


def _miss_failure_type(
    *,
    fi: Any,
    scored_info: dict[str, Any] | None,
    status: str,
    selected_count: int,
) -> str:
    if fi is None:
        return "EXPECTED_NOT_FOUND"
    if scored_info is None:
        return "EXPECTED_NOT_SCORED"
    rank = int(scored_info.get("rank") or 0)
    score = float(scored_info.get("score") or 0.0)
    if score <= 0 or rank > max(50, selected_count * 4):
        return "EXPECTED_RANKED_LOW"
    lowered = status.lower()
    if any(term in lowered for term in ("budget", "cap reached", "stronger support", "score below", "summarized", "compressed")):
        return "EXPECTED_SKIPPED"
    return "NOISE_SELECTED_ABOVE_EXPECTED"


def _low_budget_extra_file_waste(
    *,
    selected: list[Any],
    selected_tokens: dict[str, int],
    expected_files: set[str],
    packed_tokens: int,
    expected_tokens: int,
    budget: int,
    changed_files_source: str,
) -> tuple[int | None, float | None]:
    if budget > 2500 or not changed_files_source.startswith("no live changes"):
        return None, None
    last_summary = next((sf for sf in reversed(selected) if getattr(sf, "include_mode", "") == "summary"), None)
    if last_summary is None:
        return None, None
    last_path = str(getattr(last_summary, "path", ""))
    last_tokens = selected_tokens.get(last_path, 0)
    if last_tokens <= 0 or packed_tokens <= last_tokens:
        return None, None
    current_precision = expected_tokens / packed_tokens if packed_tokens else 0.0
    expected_without = expected_tokens - (last_tokens if last_path in expected_files else 0)
    precision_without = expected_without / (packed_tokens - last_tokens)
    waste = 0 if last_path in expected_files else last_tokens
    return waste, precision_without - current_precision


def _precision_recall(result: CaseResult) -> tuple[float, float, float]:
    expected = set(result.case.expected_files)
    if not expected:
        return 0.0, 0.0, 0.0
    selected = set(result.selected_paths)
    tp = len(selected & expected)
    p = tp / len(selected) if selected else 0.0
    r = tp / len(expected)
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1


def _ownership_metrics(case: BenchmarkCase, selected_paths: set[str]) -> dict[str, Any] | None:
    """Score reviewed ownership labels independently from legacy changed-file metrics."""

    if not any((
        case.action_owner_files,
        case.required_support_files,
        case.incidental_changed_files,
        case.optional_context_files,
    )):
        return None
    owners = set(case.action_owner_files)
    support = set(case.required_support_files)
    incidental = set(case.incidental_changed_files)
    optional = set(case.optional_context_files)
    useful = owners | support | optional
    return {
        "owner_recall": len(selected_paths & owners) / len(owners) if owners else None,
        "support_recall": len(selected_paths & support) / len(support) if support else None,
        "useful_context_precision": (
            len(selected_paths & useful) / len(selected_paths) if selected_paths else 0.0
        ),
        "selected_incidental_files": sorted(selected_paths & incidental),
        "incidental_selection_rate": (
            len(selected_paths & incidental) / len(incidental) if incidental else None
        ),
    }


def _route_skills_for_case(root: Path, case: BenchmarkCase) -> tuple[list[str], int]:
    if not case.expected_skills and not case.avoid_skills:
        return [], 0
    from agentpack.router.service import RouteService

    service = RouteService()
    route = service.route_task(root, case.task)
    selected = [item.skill.name for item in route.selected_skills[:3]]
    selected_keys = {_normalize_skill_name(name) for name in selected}
    token_cost = 0
    inventory = service.inventory(root)
    for skill in inventory.skills:
        keys = {
            _normalize_skill_name(skill.name),
            _normalize_skill_name(skill.path),
            _normalize_skill_name(str(Path(skill.path).parent)),
        }
        if selected_keys & keys:
            token_cost += estimate_tokens(skill.raw_text or skill.description or skill.name)
    return selected, token_cost


def _skill_metrics(
    selected_skills: list[str],
    *,
    expected_skills: list[str],
    avoid_skills: list[str],
) -> tuple[float | None, float | None, float | None, float | None]:
    selected = [_normalize_skill_name(skill) for skill in selected_skills[:3]]
    expected = {_normalize_skill_name(skill) for skill in expected_skills}
    avoided = {_normalize_skill_name(skill) for skill in avoid_skills}
    selected_set = set(selected)

    recall = len(selected_set & expected) / len(expected) if expected else None
    precision = len(selected_set & expected) / len(selected) if expected and selected else (0.0 if expected else None)
    mrr = None
    if expected:
        for idx, skill in enumerate(selected, start=1):
            if skill in expected:
                mrr = 1 / idx
                break
        if mrr is None:
            mrr = 0.0
    noise = len(selected_set & avoided) / len(selected) if avoided and selected else (0.0 if avoided else None)
    return recall, precision, mrr, noise


def _normalize_skill_name(value: str) -> str:
    normalized = value.strip().lower().replace("\\", "/").rstrip("/")
    if normalized.endswith("/skill.md"):
        normalized = normalized[: -len("/skill.md")]
    return normalized


def _miss_status(
    *,
    fi: Any,
    expected_path: str,
    receipt_map: dict[str, str],
    scored_info: dict[str, Any] | None,
    changed_files_source: str,
) -> str:
    suffix = ""
    if changed_files_source.startswith("no live changes"):
        suffix = "; no live changed-file signal"
    if fi is None:
        return "not found in scanned files"
    if fi.ignored or fi.binary:
        return "ignored or binary"
    if expected_path in receipt_map:
        return receipt_map[expected_path] + suffix
    if scored_info:
        if scored_info["score"] <= 0:
            return "scored too low" + suffix
        return "ranked but not selected" + suffix
    return "not scored" + suffix


def _result_record(result: CaseResult) -> dict[str, Any]:
    p, r, f1 = _precision_recall(result) if result.case.expected_files else (None, None, None)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task": result.case.task,
        "repository": result.case.repository,
        "task_type": result.case.task_type,
        "workspace": result.case.workspace,
        "mode": result.case.mode,
        "budget": result.case.budget,
        "expected_files": result.case.expected_files,
        "ownership_labels": {
            "action_owner_files": result.case.action_owner_files,
            "required_support_files": result.case.required_support_files,
            "incidental_changed_files": result.case.incidental_changed_files,
            "optional_context_files": result.case.optional_context_files,
        },
        "ownership_metrics": result.selection_diagnostics.get("ownership_metrics"),
        "selected_paths": result.selected_paths,
        "selected_tokens": result.selected_tokens,
        "selected_modes": result.selected_modes,
        "packed_tokens": result.packed_tokens,
        "raw_tokens": result.raw_tokens,
        "after_ignore_tokens": result.after_ignore_tokens,
        "saving_pct": round(result.saving_pct, 1),
        "saving_pct_honest": round(result.saving_pct_honest, 1),
        "files_selected": len(result.selected_paths),
        "mode_counts": _mode_counts(result.selected_modes),
        "changed_covered": result.changed_covered,
        "changed_total": result.changed_total,
        "total_s": round(result.total_s, 3),
        "phases": {k: round(v, 3) for k, v in result.phase_times.items()},
        "precision": round(p, 3) if p is not None else None,
        "recall": round(r, 3) if r is not None else None,
        "f1": round(f1, 3) if f1 is not None else None,
        "rank_at_k": result.rank_at_k,
        "candidate_recall_at_20": round(result.candidate_recall_at_20, 3)
        if result.candidate_recall_at_20 is not None else None,
        "candidate_recall_at_50": round(result.candidate_recall_at_50, 3)
        if result.candidate_recall_at_50 is not None else None,
        "candidate_recall_at_100": round(result.candidate_recall_at_100, 3)
        if result.candidate_recall_at_100 is not None else None,
        "candidate_precision_at_3": round(result.candidate_precision_at_3, 3)
        if result.candidate_precision_at_3 is not None else None,
        "candidate_precision_at_5": round(result.candidate_precision_at_5, 3)
        if result.candidate_precision_at_5 is not None else None,
        "low_budget_extra_file_waste": result.low_budget_extra_file_waste,
        "precision_delta_if_drop_last_summary": round(result.precision_delta_if_drop_last_summary, 3)
        if result.precision_delta_if_drop_last_summary is not None else None,
        "expected_token_coverage": round(result.expected_token_coverage, 3)
        if result.expected_token_coverage is not None else None,
        "selected_family_tokens": result.selected_family_tokens,
        "selected_family_waste_tokens": result.selected_family_waste_tokens,
        "reason_family_precision": result.reason_family_precision,
        "failure_type_counts": result.failure_type_counts,
        "noise_pct": round(result.noise_pct, 1) if result.noise_pct is not None else None,
        "token_precision": round(1 - (result.noise_pct / 100), 3) if result.noise_pct is not None else None,
        "random_f1": round(result.random_f1, 3) if result.random_f1 is not None else None,
        "selected_skills": result.selected_skills,
        "skill_recall_at_3": round(result.skill_recall_at_3, 3) if result.skill_recall_at_3 is not None else None,
        "skill_precision_at_3": round(result.skill_precision_at_3, 3) if result.skill_precision_at_3 is not None else None,
        "skill_mrr": round(result.skill_mrr, 3) if result.skill_mrr is not None else None,
        "skill_noise_rate": round(result.skill_noise_rate, 3) if result.skill_noise_rate is not None else None,
        "skill_token_cost": result.skill_token_cost,
        "misses": result.missed_expected,
        "top_candidates": result.top_candidates,
        "selection_diagnostics": result.selection_diagnostics,
    }


def _persist_result(root: Path, result: CaseResult) -> None:
    out = root / ".agentpack" / "benchmark_results.jsonl"
    record = _result_record(result)
    try:
        with out.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _write_results_jsonl(path: Path, results: list[CaseResult]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for result in results:
            fh.write(json.dumps(_result_record(result), sort_keys=True) + "\n")
    return path


def _owner_evidence_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate deterministic owner calibration metrics from benchmark JSONL."""

    candidate_rows: list[dict[str, Any]] = []
    protection_errors = 0
    signatures: dict[tuple[str, str], set[str]] = {}
    metric_signatures: dict[tuple[str, str], set[str]] = {}
    feature_signatures: dict[tuple[str, str, str], set[str]] = {}
    case_repetitions: Counter[tuple[str, str]] = Counter()
    for record in records:
        repository = str(record.get("repository") or "unknown")
        task = str(record.get("task") or "")
        evidence = (
            record.get("selection_diagnostics", {})
            .get("selection_v2", {})
            .get("evidence", {})
        )
        protection_errors += int(evidence.get("protected_file_misclassifications") or 0)
        for row in evidence.get("candidates", []):
            enriched = dict(row)
            enriched["repository"] = repository
            enriched["task"] = task
            candidate_rows.append(enriched)
            feature_signatures.setdefault((repository, task, str(row.get("path"))), set()).add(
                json.dumps(
                    {
                        "owner_strength": row.get("owner_strength"),
                        "owner_features": row.get("owner_features"),
                        "codes": row.get("codes"),
                        "protections": row.get("protections"),
                    },
                    sort_keys=True,
                )
            )
        key = (repository, task)
        case_repetitions[key] += 1
        signatures.setdefault(key, set()).add(json.dumps(record.get("selected_paths", []), sort_keys=True))
        metric_signatures.setdefault(key, set()).add(json.dumps({
            name: record.get(name)
            for name in ("precision", "recall", "f1", "token_precision", "packed_tokens")
        }, sort_keys=True))

    audited_rows = [row for row in candidate_rows if row.get("label") is not None]
    repositories = sorted({row["repository"] for row in audited_rows})
    thresholds = {
        str(strength): _owner_classification_metrics(audited_rows, strength=strength)
        for strength in (1, 2, 3)
    }
    per_repository = {
        repository: {
            "strong": _owner_classification_metrics(
                [row for row in audited_rows if row["repository"] == repository],
                strength=2,
            ),
            "legacy_strong_recall": _owner_legacy_recall(
                [row for row in audited_rows if row["repository"] == repository]
            ),
        }
        for repository in repositories
    }
    leave_one_out = {
        repository: _owner_classification_metrics(
            [row for row in audited_rows if row["repository"] == repository],
            strength=2,
        )
        for repository in repositories
    }
    false_by_code: dict[str, list[dict[str, Any]]] = {}
    missed_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in audited_rows:
        if int(row.get("owner_strength") or 0) >= 2 and row.get("label") != "action_owner":
            _append_owner_example(false_by_code, row)
        if row.get("label") == "action_owner" and int(row.get("owner_strength") or 0) < 2:
            _append_owner_example(missed_by_code, row)

    path_families: dict[str, dict[str, int]] = {}
    for row in audited_rows:
        family = _owner_path_family(str(row.get("path") or ""))
        cell = path_families.setdefault(family, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
        predicted = int(row.get("owner_strength") or 0) >= 2
        actual = row.get("label") == "action_owner"
        cell["tp" if predicted and actual else "fp" if predicted else "fn" if actual else "tn"] += 1

    owners = [row for row in audited_rows if row.get("label") == "action_owner"]
    availability = {
        f"r@{limit}": (
            sum(1 for row in owners if int(row.get("rank") or 10**9) <= limit) / len(owners)
            if owners else None
        )
        for limit in (20, 50, 100, 200)
    }
    strong = thresholds["2"]
    repository_precision = [data["strong"]["precision"] for data in per_repository.values()]
    repository_recall_ok = all(
        data["strong"]["recall"] >= data["legacy_strong_recall"]
        for data in per_repository.values()
    )
    three_run_coverage = bool(case_repetitions) and min(case_repetitions.values()) >= 3
    deterministic = three_run_coverage and all(len(values) == 1 for values in feature_signatures.values())
    report = {
        "rule_version": 2,
        "record_count": len(records),
        "candidate_count": len(candidate_rows),
        "audited_candidate_count": len(audited_rows),
        "strong_owner_min_strength": 2,
        "micro_by_min_strength": thresholds,
        "per_repository": per_repository,
        "leave_one_repository_out": leave_one_out,
        "path_family_confusion": path_families,
        "false_owner_examples_by_code": false_by_code,
        "missed_owner_examples_by_code": missed_by_code,
        "owner_availability": availability,
        "protection_misclassifications": protection_errors,
        "determinism": {
            "minimum_case_repetitions": min(case_repetitions.values()) if case_repetitions else 0,
            "three_run_coverage": three_run_coverage,
            "feature_drift_groups": sum(1 for values in feature_signatures.values() if len(values) > 1),
            "selected_path_drift_groups": sum(1 for values in signatures.values() if len(values) > 1),
            "legacy_metric_drift_groups": sum(1 for values in metric_signatures.values() if len(values) > 1),
        },
    }
    report["gates"] = {
        "strong_owner_micro_recall": strong["recall"] >= 0.65,
        "strong_owner_leave_one_repository_out_precision": (
            bool(leave_one_out)
            and min(metrics["precision"] for metrics in leave_one_out.values()) >= 0.95
        ),
        "every_repository_precision": bool(repository_precision) and min(repository_precision) >= 0.90,
        "no_repository_recall_regression": repository_recall_ok,
        "zero_protection_misclassifications": protection_errors == 0,
        "deterministic_classification": deterministic,
        "legacy_v1_selected_paths_identical": (
            three_run_coverage and report["determinism"]["selected_path_drift_groups"] == 0
        ),
        "legacy_v1_metrics_identical": (
            three_run_coverage and report["determinism"]["legacy_metric_drift_groups"] == 0
        ),
    }
    report["passed"] = all(report["gates"].values())
    return report


def _owner_classification_metrics(rows: list[dict[str, Any]], *, strength: int) -> dict[str, Any]:
    tp = sum(1 for row in rows if row.get("label") == "action_owner" and int(row.get("owner_strength") or 0) >= strength)
    fp = sum(1 for row in rows if row.get("label") != "action_owner" and int(row.get("owner_strength") or 0) >= strength)
    fn = sum(1 for row in rows if row.get("label") == "action_owner" and int(row.get("owner_strength") or 0) < strength)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / (tp + fn) if tp + fn else 1.0,
    }


def _owner_legacy_recall(rows: list[dict[str, Any]]) -> float:
    owners = [row for row in rows if row.get("label") == "action_owner"]
    return (
        sum(1 for row in owners if int(row.get("legacy_owner_strength") or 0) >= 2) / len(owners)
        if owners else 1.0
    )


def _append_owner_example(grouped: dict[str, list[dict[str, Any]]], row: dict[str, Any]) -> None:
    codes = row.get("codes") or row.get("owner_features", {}).get("penalty_codes") or ["no_owner_code"]
    example = {
        "repository": row["repository"],
        "task": row["task"],
        "path": row.get("path"),
        "rank": row.get("rank"),
        "strength": row.get("owner_strength"),
    }
    for code in codes:
        bucket = grouped.setdefault(str(code), [])
        if len(bucket) < 10:
            bucket.append(example)


def _owner_path_family(path: str) -> str:
    lowered = path.lower()
    parts = set(PurePosixPath(lowered).parts)
    name = PurePosixPath(lowered).name
    if parts & {"test", "tests", "__tests__"} or ".test." in name or ".spec." in name:
        return "test"
    if parts & {"docs", "examples"}:
        return "docs_example"
    if parts & {"build", "dist", "generated", "vendor"}:
        return "generated"
    if name in {"changelog.md", "package.json", "pom.xml", "pyproject.toml", "version.go"} or PurePosixPath(name).suffix in {".json", ".toml", ".yaml", ".yml"}:
        return "config_metadata"
    if PurePosixPath(name).suffix in {".py", ".go", ".java", ".js", ".jsx", ".ts", ".tsx", ".rb", ".rs"}:
        return "source"
    return "other"


def _print_owner_evidence_report(report: dict[str, Any], *, label: str) -> None:
    strong = report["micro_by_min_strength"][str(report["strong_owner_min_strength"])]
    console.print(f"[bold]Owner evidence report:[/] {label}")
    console.print(
        f"  strong precision [bold]{strong['precision']:.1%}[/]  "
        f"recall [bold]{strong['recall']:.1%}[/]  "
        f"tp/fp/fn {strong['tp']}/{strong['fp']}/{strong['fn']}"
    )
    for repository, data in report["per_repository"].items():
        current = data["strong"]
        console.print(
            f"  {repository}: precision {current['precision']:.1%}, "
            f"recall {current['recall']:.1%}, legacy recall {data['legacy_strong_recall']:.1%}"
        )
    status = "green" if report["passed"] else "red"
    console.print(f"[{status}]Calibration gates: {'PASS' if report['passed'] else 'FAIL'}[/]")


def _print_case_detail(result: CaseResult, show_misses: bool = False) -> None:
    has_gt = bool(result.case.expected_files)
    p, r, f1 = _precision_recall(result) if has_gt else (0.0, 0.0, 0.0)

    console.print(
        f"\n[bold cyan]{result.case.task}[/]  "
        f"[dim]mode={result.case.mode} type={result.case.task_type}"
        f"{' workspace=' + result.case.workspace if result.case.workspace else ''}[/]"
    )

    tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    tbl.add_column(style="dim")
    tbl.add_column(justify="right", style="bold")
    tbl.add_row("packed tokens", f"{result.packed_tokens:,}")
    tbl.add_row("raw tokens (all files)", f"{result.raw_tokens:,}")
    tbl.add_row("after ignore tokens", f"{result.after_ignore_tokens:,}")
    tbl.add_row("saving vs raw", f"[green]{result.saving_pct:.1f}%[/]")
    tbl.add_row("saving vs after-ignore", f"[cyan]{result.saving_pct_honest:.1f}%[/]")
    tbl.add_row("files selected", str(len(result.selected_paths)))
    tbl.add_row("mode mix", _format_mode_counts(_mode_counts(result.selected_modes)))
    if result.changed_total > 0:
        cov_pct = result.changed_covered / result.changed_total * 100
        tbl.add_row("changed files covered", f"{result.changed_covered}/{result.changed_total}  ({cov_pct:.0f}%)")
    tbl.add_row("total time", f"{result.total_s:.2f}s")
    console.print(tbl)

    if result.phase_times:
        phases = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        phases.add_column("phase", style="dim")
        phases.add_column("time", justify="right")
        for phase, t in result.phase_times.items():
            phases.add_row(phase, f"{t:.3f}s")
        console.print(phases)

    if has_gt:
        console.print(
            f"  precision [bold]{p:.1%}[/]  "
            f"recall [bold]{r:.1%}[/]  "
            f"F1 [bold]{f1:.1%}[/]"
        )
        if result.rank_at_k is not None:
            console.print(f"  rank@K (all expected covered at rank) [bold]{result.rank_at_k}[/]")
        else:
            console.print("  rank@K  [dim]expected files not all found in scored list[/]")
        if result.candidate_recall_at_20 is not None:
            console.print(
                "  candidate recall "
                f"@20 [bold]{result.candidate_recall_at_20:.1%}[/]  "
                f"@50 [bold]{result.candidate_recall_at_50:.1%}[/]  "
                f"@100 [bold]{result.candidate_recall_at_100:.1%}[/]"
            )
        if result.candidate_precision_at_3 is not None:
            console.print(
                "  candidate precision "
                f"@3 [bold]{result.candidate_precision_at_3:.1%}[/]  "
                f"@5 [bold]{result.candidate_precision_at_5:.1%}[/]"
            )
        if result.noise_pct is not None:
            console.print(f"  noise (tokens on non-expected files) [bold]{result.noise_pct:.1f}%[/]")
        if result.random_f1 is not None:
            lift = f1 - result.random_f1
            color = "green" if lift >= 0 else "red"
            console.print(
                f"  random baseline F1 [dim]{result.random_f1:.1%}[/]  "
                f"ranker lift [{color}]{lift:+.1%}[/{color}]"
            )
        expected_set = set(result.case.expected_files)
        selected_set = set(result.selected_paths)
        hits = expected_set & selected_set
        misses = expected_set - selected_set
        if hits:
            console.print("  [green]hit:[/]  " + ", ".join(sorted(hits)))
        if misses:
            console.print("  [red]miss:[/] " + ", ".join(sorted(misses)))
        if show_misses and result.missed_expected:
            console.print("  [yellow]miss details:[/]")
            for miss in result.missed_expected:
                rank = miss["rank"] if miss["rank"] is not None else "-"
                score = miss["score"] if miss["score"] is not None else "-"
                reasons = ", ".join(miss["reasons"]) if miss["reasons"] else "no scoring reasons"
                console.print(
                    f"    {miss['path']}  status={miss['status']}  "
                    f"rank={rank}  score={score}  why={reasons}"
                )

    if result.case.expected_skills or result.case.avoid_skills:
        mrr_text = f"{result.skill_mrr:.2f}" if result.skill_mrr is not None else "-"
        console.print(
            "  skill recall@3 "
            f"[bold]{_fmt_pct(result.skill_recall_at_3)}[/]  "
            "precision@3 "
            f"[bold]{_fmt_pct(result.skill_precision_at_3)}[/]  "
            f"MRR [bold]{mrr_text}[/]"
        )
        if result.skill_noise_rate is not None:
            console.print(f"  skill noise [bold]{result.skill_noise_rate:.1%}[/]")
        console.print(f"  skill token cost [bold]{result.skill_token_cost:,}[/]")
        if result.selected_skills:
            console.print("  [dim]top skills:[/] " + ", ".join(result.selected_skills[:3]))

    console.print("  [dim]top files:[/] " + ", ".join(result.selected_paths[:5]))


def _mode_counts(selected_modes: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for mode in selected_modes.values():
        counts[mode] = counts.get(mode, 0) + 1
    return counts


def _selected_mode(sf: Any) -> str:
    mode = getattr(sf, "include_mode", "summary")
    return mode if isinstance(mode, str) else "summary"


def _format_mode_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    order = ("full", "diff", "symbols", "skeleton", "summary")
    return ", ".join(f"{mode}:{counts[mode]}" for mode in order if counts.get(mode))


def _print_summary_table(results: list[CaseResult]) -> None:
    has_gt = any(r.case.expected_files for r in results)
    has_skill_gt = any(r.case.expected_skills or r.case.avoid_skills for r in results)

    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("task", max_width=40)
    tbl.add_column("mode", width=9)
    tbl.add_column("tokens", justify="right")
    tbl.add_column("vs raw", justify="right")
    tbl.add_column("vs ignore", justify="right")
    tbl.add_column("files", justify="right")
    tbl.add_column("time", justify="right")
    if has_gt:
        tbl.add_column("P", justify="right")
        tbl.add_column("R", justify="right")
        tbl.add_column("F1", justify="right")
        tbl.add_column("rand F1", justify="right")
        tbl.add_column("cand R@50", justify="right")
        tbl.add_column("cand P@3", justify="right")
        tbl.add_column("rank@K", justify="right")
        tbl.add_column("noise%", justify="right")
    if has_skill_gt:
        tbl.add_column("skill R@3", justify="right")
        tbl.add_column("skill P@3", justify="right")
        tbl.add_column("skill MRR", justify="right")
        tbl.add_column("skill noise", justify="right")

    for r in results:
        p, rec, f1 = _precision_recall(r) if r.case.expected_files else (0.0, 0.0, 0.0)
        row = [
            r.case.task[:38],
            r.case.mode,
            f"{r.packed_tokens:,}",
            f"{r.saving_pct:.1f}%",
            f"{r.saving_pct_honest:.1f}%",
            str(len(r.selected_paths)),
            f"{r.total_s:.2f}s",
        ]
        if has_gt:
            row += [
                f"{p:.1%}" if r.case.expected_files else "—",
                f"{rec:.1%}" if r.case.expected_files else "—",
                f"{f1:.1%}" if r.case.expected_files else "—",
                f"{r.random_f1:.1%}" if r.random_f1 is not None else "—",
                f"{r.candidate_recall_at_50:.1%}" if r.candidate_recall_at_50 is not None else "—",
                f"{r.candidate_precision_at_3:.1%}" if r.candidate_precision_at_3 is not None else "—",
                str(r.rank_at_k) if r.rank_at_k is not None else "—",
                f"{r.noise_pct:.0f}%" if r.noise_pct is not None else "—",
            ]
        if has_skill_gt:
            row += [
                _fmt_pct(r.skill_recall_at_3) if r.case.expected_skills else "—",
                _fmt_pct(r.skill_precision_at_3) if r.case.expected_skills else "—",
                f"{r.skill_mrr:.2f}" if r.skill_mrr is not None else "—",
                _fmt_pct(r.skill_noise_rate) if r.case.avoid_skills else "—",
            ]
        tbl.add_row(*row)

    console.print()
    console.print(tbl)


def _quality_status(
    results: list[CaseResult],
    *,
    min_recall: float = 0.60,
    min_token_precision: float = 0.50,
) -> tuple[bool, dict[str, float]]:
    scored = [result for result in results if result.case.expected_files]
    if not scored:
        return False, {"cases": 0.0}
    recalls = [_precision_recall(result)[1] for result in scored]
    token_precisions = [
        1 - (result.noise_pct / 100)
        for result in scored
        if result.noise_pct is not None
    ]
    avg_recall = sum(recalls) / len(recalls)
    avg_token_precision = sum(token_precisions) / len(token_precisions) if token_precisions else 0.0
    return (
        avg_recall >= min_recall and avg_token_precision >= min_token_precision,
        {
            "cases": float(len(scored)),
            "avg_recall": avg_recall,
            "avg_token_precision": avg_token_precision,
        },
    )


def _print_quality_status(
    results: list[CaseResult],
    *,
    min_recall: float = 0.60,
    min_token_precision: float = 0.50,
) -> bool:
    passed, metrics = _quality_status(
        results,
        min_recall=min_recall,
        min_token_precision=min_token_precision,
    )
    if not metrics.get("cases"):
        console.print("[yellow]Quality target not proven: no benchmark cases have expected_files.[/]")
        return False
    color = "green" if passed else "yellow"
    console.print(
        f"[{color}]Quality target {'passed' if passed else 'not met'}:[/{color}] "
        f"{int(metrics['cases'])} case(s), "
        f"avg recall {metrics['avg_recall']:.1%} / {min_recall:.0%}, "
        f"avg token precision {metrics['avg_token_precision']:.1%} / {min_token_precision:.0%}"
    )
    return passed


def _print_task_type_summary(results: list[CaseResult]) -> None:
    grouped: dict[str, list[CaseResult]] = {}
    for result in results:
        if result.case.expected_files:
            grouped.setdefault(result.case.task_type, []).append(result)
    if not grouped:
        return

    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("task type", max_width=28)
    tbl.add_column("cases", justify="right")
    tbl.add_column("avg P", justify="right")
    tbl.add_column("avg R", justify="right")
    tbl.add_column("avg F1", justify="right")
    tbl.add_column("avg cand R@50", justify="right")
    tbl.add_column("avg cand P@3", justify="right")
    tbl.add_column("avg noise", justify="right")
    tbl.add_column("last waste", justify="right")
    tbl.add_column("drop-last Δ", justify="right")

    for task_type, rows in sorted(grouped.items()):
        metrics = [_precision_recall(row) for row in rows]
        avg_p = sum(item[0] for item in metrics) / len(metrics)
        avg_r = sum(item[1] for item in metrics) / len(metrics)
        avg_f1 = sum(item[2] for item in metrics) / len(metrics)
        candidate_recall_values = [
            row.candidate_recall_at_50 for row in rows if row.candidate_recall_at_50 is not None
        ]
        avg_candidate_recall = (
            sum(candidate_recall_values) / len(candidate_recall_values)
            if candidate_recall_values else None
        )
        candidate_precision_values = [
            row.candidate_precision_at_3 for row in rows if row.candidate_precision_at_3 is not None
        ]
        avg_candidate_precision = (
            sum(candidate_precision_values) / len(candidate_precision_values)
            if candidate_precision_values else None
        )
        noise_values = [row.noise_pct for row in rows if row.noise_pct is not None]
        avg_noise = sum(noise_values) / len(noise_values) if noise_values else None
        avg_last_waste, avg_drop_last_delta, waste_cases = _low_budget_waste_summary(rows)
        tbl.add_row(
            task_type,
            str(len(rows)),
            f"{avg_p:.1%}",
            f"{avg_r:.1%}",
            f"{avg_f1:.1%}",
            f"{avg_candidate_recall:.1%}" if avg_candidate_recall is not None else "-",
            f"{avg_candidate_precision:.1%}" if avg_candidate_precision is not None else "-",
            f"{avg_noise:.0f}%" if avg_noise is not None else "-",
            f"{avg_last_waste:.0f}t/{waste_cases}" if waste_cases else "-",
            f"{avg_drop_last_delta:+.1%}" if waste_cases else "-",
        )

    console.print("\n[bold]By Task Type[/]")
    console.print(tbl)


def _print_intent_summary(results: list[CaseResult]) -> None:
    grouped: dict[str, list[CaseResult]] = {}
    for result in results:
        if not result.case.expected_files:
            continue
        intent_profile = result.selection_diagnostics.get("intent_profile")
        intent = "unknown"
        if isinstance(intent_profile, dict):
            intent = str(intent_profile.get("primary") or "unknown")
        grouped.setdefault(intent, []).append(result)
    if not grouped:
        return

    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("intent", max_width=24)
    tbl.add_column("cases", justify="right")
    tbl.add_column("avg R", justify="right")
    tbl.add_column("avg TP", justify="right")
    tbl.add_column("misses", justify="right")
    tbl.add_column("audited noise", justify="right")
    tbl.add_column("plausible", justify="right")

    for intent, rows in sorted(grouped.items()):
        recalls = [_precision_recall(row)[1] for row in rows]
        token_precisions = [
            1 - (row.noise_pct / 100)
            for row in rows
            if row.noise_pct is not None
        ]
        misses = sum(len(row.missed_expected) for row in rows)
        audited_noise = 0
        plausible_noise = 0
        for row in rows:
            audit = row.selection_diagnostics.get("label_audit")
            if isinstance(audit, dict):
                audited_noise += int(audit.get("audited_noise_tokens") or 0)
                plausible_noise += int(audit.get("plausibly_useful_tokens") or 0)
        avg_recall = sum(recalls) / len(recalls)
        avg_token_precision = sum(token_precisions) / len(token_precisions) if token_precisions else 0.0
        tbl.add_row(
            intent,
            str(len(rows)),
            f"{avg_recall:.1%}",
            f"{avg_token_precision:.1%}",
            str(misses),
            f"{audited_noise:,}t",
            f"{plausible_noise:,}t",
        )

    console.print("\n[bold]By Intent[/]")
    console.print(tbl)


def _low_budget_waste_summary(rows: list[CaseResult]) -> tuple[float, float, int]:
    values = [
        (row.low_budget_extra_file_waste, row.precision_delta_if_drop_last_summary)
        for row in rows
        if row.low_budget_extra_file_waste is not None
        and row.precision_delta_if_drop_last_summary is not None
    ]
    if not values:
        return 0.0, 0.0, 0
    avg_waste = sum(waste for waste, _delta in values) / len(values)
    avg_delta = sum(delta for _waste, delta in values) / len(values)
    return avg_waste, avg_delta, len(values)


def _print_precision_diagnostics(results: list[CaseResult]) -> None:
    scored = [result for result in results if result.case.expected_files]
    if not scored:
        return

    failure_counts: dict[str, int] = {}
    family_waste: dict[str, int] = {}
    reason_counts: dict[str, dict[str, float]] = {}
    coverage_values: list[float] = []
    label_audit_cases = 0
    label_audit_plausible_tokens = 0
    label_audit_noise_tokens = 0
    adjusted_precision_values: list[float] = []
    ast_memory_selected_files, ast_memory_projected_files = _ast_memory_signal_counts(results)

    for result in scored:
        for failure_type, count in result.failure_type_counts.items():
            failure_counts[failure_type] = failure_counts.get(failure_type, 0) + count
        for family, tokens in result.selected_family_waste_tokens.items():
            family_waste[family] = family_waste.get(family, 0) + tokens
        for family, stats in result.reason_family_precision.items():
            bucket = reason_counts.setdefault(family, {"selected": 0.0, "expected": 0.0})
            bucket["selected"] += stats.get("selected", 0.0)
            bucket["expected"] += stats.get("expected", 0.0)
        if result.expected_token_coverage is not None:
            coverage_values.append(result.expected_token_coverage)
        label_audit = result.selection_diagnostics.get("label_audit")
        if isinstance(label_audit, dict):
            plausible_tokens = int(label_audit.get("plausibly_useful_tokens") or 0)
            noise_tokens = int(label_audit.get("selected_noise_tokens") or 0)
            adjusted_precision = label_audit.get("adjusted_token_precision")
            if plausible_tokens > 0:
                label_audit_cases += 1
                label_audit_plausible_tokens += plausible_tokens
                label_audit_noise_tokens += noise_tokens
            if isinstance(adjusted_precision, (int, float)):
                adjusted_precision_values.append(float(adjusted_precision))

    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("diagnostic", max_width=30)
    tbl.add_column("value", justify="right")
    tbl.add_column("note", max_width=48)

    avg_coverage = sum(coverage_values) / len(coverage_values) if coverage_values else None
    tbl.add_row(
        "expected token coverage",
        f"{avg_coverage:.1%}" if avg_coverage is not None else "-",
        "selected expected tokens / estimated expected-file tokens",
    )

    for failure_type, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0])):
        tbl.add_row(f"miss {failure_type.lower()}", str(count), "primary funnel stage for missed expected files")

    for family, tokens in sorted(family_waste.items(), key=lambda item: (-item[1], item[0]))[:6]:
        if tokens > 0:
            tbl.add_row(f"{family} waste", f"{tokens:,}t", "selected tokens outside expected files")

    if label_audit_plausible_tokens > 0:
        avg_adjusted_precision = (
            sum(adjusted_precision_values) / len(adjusted_precision_values)
            if adjusted_precision_values else None
        )
        tbl.add_row(
            "label-audit plausible noise",
            f"{label_audit_plausible_tokens:,}t/{label_audit_cases}",
            "non-expected selected tokens with same-scope/package/family evidence",
        )
        tbl.add_row(
            "label-audit adjusted TP",
            f"{avg_adjusted_precision:.1%}" if avg_adjusted_precision is not None else "-",
            "diagnostic only; treats plausible unlabeled context as useful",
        )

    tbl.add_row(
        "AST memory signals",
        f"{ast_memory_selected_files} files",
        (
            f"{ast_memory_projected_files} projected; memory-confirmed compaction was exercised"
            if ast_memory_selected_files > 0
            else "not exercised by this benchmark; use memory A/B or seeded memory cases"
        ),
    )

    for family, stats in sorted(reason_counts.items()):
        selected = stats.get("selected", 0.0)
        expected = stats.get("expected", 0.0)
        if selected <= 0:
            continue
        precision = expected / selected
        if selected >= 2:
            tbl.add_row(
                f"reason {family}",
                f"{precision:.1%}",
                f"{int(expected)}/{int(selected)} selected files with this signal were expected",
            )

    console.print("\n[bold]Precision Diagnostics[/]")
    console.print(tbl)


def _ast_memory_signal_counts(results: list[CaseResult]) -> tuple[int, int]:
    selected_files = 0
    projected_files = 0
    for result in results:
        ast_memory = result.selection_diagnostics.get("ast_checkpoint_memory_excerpt_projection")
        if not isinstance(ast_memory, dict):
            continue
        selected_files += int(ast_memory.get("memory_signal_selected_files") or 0)
        projected_files += int(ast_memory.get("memory_signal_projected_files") or 0)
    return selected_files, projected_files


def _fmt_pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "-"


def _print_miss_details(results: list[CaseResult]) -> None:
    rows = [miss | {"task": result.case.task[:30]} for result in results for miss in result.missed_expected]
    if not rows:
        return

    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("task", max_width=30)
    tbl.add_column("missed file", max_width=42)
    tbl.add_column("failure", max_width=24)
    tbl.add_column("status", max_width=24)
    tbl.add_column("rank", justify="right")
    tbl.add_column("score", justify="right")
    tbl.add_column("why", max_width=40)

    for row in rows:
        tbl.add_row(
            row["task"],
            row["path"],
            row.get("failure_type", "-"),
            row["status"],
            str(row["rank"]) if row["rank"] is not None else "-",
            str(row["score"]) if row["score"] is not None else "-",
            ", ".join(row["reasons"]) if row["reasons"] else "-",
        )

    console.print("\n[bold]Miss Details[/]")
    console.print(tbl)


def _print_fixture_summary_table(results: list[CaseResult]) -> None:
    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("fixture task", max_width=42)
    tbl.add_column("mode", width=9)
    tbl.add_column("tokens", justify="right")
    tbl.add_column("R", justify="right")
    tbl.add_column("cand R@50", justify="right")
    tbl.add_column("cand P@3", justify="right")
    tbl.add_column("F1", justify="right")
    tbl.add_column("rank@K", justify="right")
    tbl.add_column("noise", justify="right")

    for result in results:
        _p, recall, f1 = _precision_recall(result)
        tbl.add_row(
            result.case.task[:40],
            result.case.mode,
            f"{result.packed_tokens:,}",
            f"{recall:.0%}",
            f"{result.candidate_recall_at_50:.0%}" if result.candidate_recall_at_50 is not None else "-",
            f"{result.candidate_precision_at_3:.0%}" if result.candidate_precision_at_3 is not None else "-",
            f"{f1:.0%}",
            str(result.rank_at_k) if result.rank_at_k is not None else "-",
            f"{result.noise_pct:.0f}%" if result.noise_pct is not None else "-",
        )

    console.print()
    console.print(tbl)


def _print_compare_table(task: str, results: list[CaseResult]) -> None:
    console.print(f"\n[bold]Mode comparison:[/] [cyan]{task}[/]\n")

    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
    tbl.add_column("mode", width=10)
    tbl.add_column("tokens", justify="right")
    tbl.add_column("vs raw", justify="right")
    tbl.add_column("vs ignore", justify="right")
    tbl.add_column("files", justify="right")
    tbl.add_column("time", justify="right")

    for r in results:
        tbl.add_row(
            r.case.mode,
            f"{r.packed_tokens:,}",
            f"{r.saving_pct:.1f}%",
            f"{r.saving_pct_honest:.1f}%",
            str(len(r.selected_paths)),
            f"{r.total_s:.2f}s",
        )
    console.print(tbl)


def _copy_fixture(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".agentpack", ".pytest_cache"),
    )


benchmark_app = typer.Typer(help="Benchmark file selection quality and token efficiency.")


def register(app: typer.Typer) -> None:
    app.add_typer(benchmark_app, name="benchmark")


@benchmark_app.command("capture")
def capture_benchmark_case(
    since: str = typer.Option(..., "--since", help="Git ref to diff against."),
    task: str = typer.Option(..., "--task", help="Task text for the captured benchmark case."),
    mode: str = typer.Option("balanced", "--mode", help=f"Benchmark mode ({MODE_HELP})."),
    workspace: str = typer.Option("", "--workspace", help="Optional workspace."),
    allow_empty: bool = typer.Option(False, "--allow-empty", help="Allow appending a case with no expected files."),
    anonymous_report: bool = typer.Option(False, "--anonymous-report", help="Write shareable benchmark-report files without source code."),
) -> None:
    """Append a benchmark case from git diff expected files."""
    if not is_requested_mode(mode):
        console.print(f"[red]{invalid_mode_message(mode)}[/]")
        raise typer.Exit(1)
    root = _root()
    expected = sorted(git.changed_files_since(root, since))
    if not expected and not allow_empty:
        console.print(f"[yellow]No files changed since {since}. Use --allow-empty to append anyway.[/]")
        raise typer.Exit(1)
    case = BenchmarkCase(task=task.strip(), mode=normalize_mode(mode), expected_files=expected, workspace=workspace or None)
    out = _append_benchmark_cases(root, [case])
    console.print(f"[green]✓[/] Appended benchmark case to [bold]{out}[/]")
    console.print(f"  expected_files: {len(expected)}")
    if anonymous_report:
        report_md, report_json = _write_anonymous_benchmark_report(root)
        console.print(f"[green]✓[/] Wrote anonymous report: [bold]{report_md}[/]")
        console.print(f"[green]✓[/] Wrote anonymous report data: [bold]{report_json}[/]")


@benchmark_app.command("scan-modes")
def benchmark_scan_modes(
    files: int = typer.Option(2000, "--files", help="Synthetic source file count."),
    target_every: int = typer.Option(200, "--target-every", help="Put the target symbol in every Nth file."),
    llm_command: str = typer.Option("", "--llm-command", help="Optional command that accepts a prompt file path as last arg."),
) -> None:
    """Compare grep baseline and AgentPack full/incremental scan on a synthetic repo."""
    with tempfile.TemporaryDirectory(prefix="agentpack-bench-") as tmp:
        root = Path(tmp)
        _build_synthetic_repo(root, files=files, target_every=target_every)
        _init_synthetic_git(root)

        task = "fix target_symbol caller behavior"
        grep_start = time.perf_counter()
        grep = subprocess.run(
            ["rg", "-n", "target_symbol", "src"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        grep_s = time.perf_counter() - grep_start

        full_start = time.perf_counter()
        full = PackService().run(PackRequest(
            root=root,
            agent="generic",
            task=task,
            mode="balanced",
            budget=40000,
            since=None,
            refresh=False,
            task_source="benchmark",
        ))
        full_s = time.perf_counter() - full_start

        changed = root / "src" / "file_0000.py"
        changed.write_text(changed.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        incremental_start = time.perf_counter()
        incremental = PackService().run(PackRequest(
            root=root,
            agent="generic",
            task=task,
            mode="balanced",
            budget=40000,
            since=None,
            refresh=False,
            task_source="benchmark",
        ))
        incremental_s = time.perf_counter() - incremental_start

        llm_s: float | None = None
        if llm_command:
            prompt_path = root / "prompt.txt"
            prompt_path.write_text(
                "Find files relevant to fixing target_symbol caller behavior. Return file paths only.\n",
                encoding="utf-8",
            )
            llm_start = time.perf_counter()
            subprocess.run(
                [*llm_command.split(), str(prompt_path)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            llm_s = time.perf_counter() - llm_start

        table = Table(box=box.SIMPLE, show_header=True)
        table.add_column("method")
        table.add_column("seconds", justify="right")
        table.add_column("details")
        table.add_row("grep rg", f"{grep_s:.3f}", f"{len(grep.stdout.splitlines())} matching lines")
        table.add_row(
            "agentpack full",
            f"{full_s:.3f}",
            (
                f"{full.scan_result.rehashed_count} hashed, {full.packed_tokens:,}/{full.raw_tokens:,} tokens "
                f"({full.saving_pct:.1f}% less)"
            ),
        )
        table.add_row(
            f"agentpack {incremental.scan_result.scan_mode}",
            f"{incremental_s:.3f}",
            (
                f"{incremental.scan_result.rehashed_count} rehashed, {incremental.scan_result.reused_count} reused"
                + (f"; {incremental.scan_result.full_scan_reason}" if incremental.scan_result.full_scan_reason else "")
            ),
        )
        if llm_s is not None:
            table.add_row("external llm/agent", f"{llm_s:.3f}", llm_command)
        console.print(table)


@benchmark_app.command("e2e-init")
def benchmark_e2e_init(
    output: str = typer.Option("", "--output", help="Output TOML path. Default: .agentpack/e2e_cases.toml."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Scaffold a guarded E2E benchmark suite for real coding agents."""
    root = _root()
    out_path = Path(output) if output else root / ".agentpack" / "e2e_cases.toml"
    if not out_path.is_absolute():
        out_path = root / out_path
    if out_path.exists() and not force:
        console.print(f"[yellow]E2E cases file already exists:[/] {out_path}")
        console.print("  Pass [bold]--force[/] to overwrite.")
        raise typer.Exit(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_e2e_cases_template(root), encoding="utf-8")
    console.print(f"[green]✓[/] Created [bold]{out_path}[/]")
    console.print("  Fill in setup/test commands, then run [bold]agentpack benchmark e2e[/].")


def _e2e_cases_template(root: Path) -> str:
    repo = json.dumps(str(root))
    categories = [
        ("caller_signature_change", "Function signature or behavior change requiring caller updates."),
        ("api_service_model_contract", "API route, service, serializer, and model contract change."),
        ("config_env_runtime", "Bug depends on config/env/default behavior."),
        ("deleted_or_renamed_file", "Snapshot or reference logic must handle deleted/renamed files."),
        ("omitted_related_test", "Related test is not obvious from the primary source file."),
        ("cross_package_monorepo", "Change spans workspace/package boundaries."),
        ("side_effect_eventing", "Fix needs awareness of emitted events, analytics, or side effects."),
        ("schema_migration_contract", "Schema/model/migration contract impacts runtime behavior."),
        ("generated_file_noise", "Generated or ignored files should not steer the fix."),
        ("broad_task_precision", "Broad task wording where noisy context hurts precision."),
    ]
    lines = [
        "# AgentPack guarded E2E benchmark cases",
        "#",
        "# Each case should protect validation tests from edits and name expected source files.",
        "# Run at least 3 trials across no-context, grep, agentpack-lite, hybrid, and agentpack.",
        "# Example:",
        "#   agentpack benchmark e2e --cases .agentpack/e2e_cases.toml \\",
        "#     --agent-command 'bash -lc \"codex exec --dangerously-bypass-approvals-and-sandbox --cd {repo} --skip-git-repo-check \\\"$(cat {prompt})\\\"\"' \\",
        "#     --strategies no-context,grep,agentpack-lite,hybrid,agentpack --trials 3",
        "",
    ]
    for name, description in categories:
        lines.extend([
            "# [[cases]]",
            f"# name = \"{name}\"",
            f"# repo = {repo}",
            f"# task = \"{description}\"",
            f"# setup_command = \"python /absolute/path/to/setup_{name}.py\"",
            "# test_command = \"PYTHONPATH=src pytest -q tests/path/to_targeted_test.py\"",
            "# protected_paths = [\"tests/path/to_targeted_test.py\"]",
            "# expected_edit_paths = [\"src/path/to_expected_source.py\"]",
            "",
        ])
    return "\n".join(lines)


@benchmark_app.command("e2e")
def benchmark_e2e(
    cases: str = typer.Option(..., "--cases", help="TOML file with [[cases]] entries."),
    agent_command: str = typer.Option(..., "--agent-command", help="Agent command. Use {prompt} and {repo} placeholders, or prompt path is appended."),
    strategies: str = typer.Option("no-context,grep,agentpack-lite,hybrid,agentpack", "--strategies", help="Comma-separated: no-context,grep,agentpack-lite,hybrid,agentpack."),
    trials: int = typer.Option(1, "--trials", help="Runs per case per strategy."),
    timeout: int = typer.Option(300, "--timeout", help="Agent command timeout seconds."),
    input_cost_per_mtok: float = typer.Option(0.0, "--input-cost-per-mtok", help="Optional input token price in USD per 1M tokens."),
    output_cost_per_mtok: float = typer.Option(0.0, "--output-cost-per-mtok", help="Optional output token price in USD per 1M tokens."),
    output: str = typer.Option("", "--output", help="JSONL output path. Default: .agentpack/e2e_results.jsonl"),
    keep_workdirs: bool = typer.Option(False, "--keep-workdirs", help="Keep temp workdirs for failed-result inspection."),
) -> None:
    """Run real coding-agent E2E evals and judge by test command pass/fail."""
    root = _root()
    parsed_cases = _load_e2e_cases(root, Path(cases))
    wanted_strategies = [item.strip() for item in strategies.split(",") if item.strip()]
    unknown = set(wanted_strategies) - {"no-context", "grep", "agentpack-lite", "hybrid", "agentpack"}
    if unknown:
        raise typer.BadParameter(f"Unknown strategy: {', '.join(sorted(unknown))}")
    if trials < 1:
        raise typer.BadParameter("--trials must be >= 1")

    out_path = Path(output) if output else root / ".agentpack" / "e2e_results.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[E2EResult] = []

    for case in parsed_cases:
        for strategy in wanted_strategies:
            for trial in range(1, trials + 1):
                result = _run_e2e_case(
                    case,
                    strategy=strategy,
                    trial=trial,
                    agent_command=agent_command,
                    timeout=timeout,
                    input_cost_per_mtok=input_cost_per_mtok,
                    output_cost_per_mtok=output_cost_per_mtok,
                    keep_workdir=keep_workdirs,
                )
                results.append(result)
                with out_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(result.__dict__) + "\n")

    _print_e2e_summary(results, out_path)


@benchmark_app.command("e2e-report")
def benchmark_e2e_report(
    results: str = typer.Option("", "--results", help="JSONL output from `agentpack benchmark e2e`. Default: .agentpack/e2e_results.jsonl."),
    baseline: str = typer.Option("no-context", "--baseline", help="Baseline strategy, usually no-context."),
    treatment: str = typer.Option("agentpack", "--treatment", help="Treatment strategy, usually agentpack."),
    markdown: bool = typer.Option(False, "--markdown", help="Print a Markdown report instead of a console table."),
) -> None:
    """Compare AgentPack vs no-AgentPack E2E benchmark runs."""
    root = _root()
    path = Path(results) if results else root / ".agentpack" / "e2e_results.jsonl"
    if not path.is_absolute():
        path = root / path
    records = _load_e2e_result_records(path)
    if not records:
        console.print(f"[yellow]No E2E results found at {path}[/]")
        raise typer.Exit(1)
    if markdown:
        console.print(_e2e_ab_markdown(records, baseline=baseline, treatment=treatment, source=path))
        return
    _print_e2e_ab_table(records, baseline=baseline, treatment=treatment, source=path)


def _load_e2e_result_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _e2e_strategy_metrics(records: list[dict[str, Any]], strategy: str) -> dict[str, float]:
    subset = [row for row in records if row.get("strategy") == strategy]
    if not subset:
        return {"runs": 0.0}

    def avg(key: str) -> float:
        return sum(float(row.get(key) or 0.0) for row in subset) / len(subset)

    first_file_values = [
        float(row["time_to_first_expected_file_s"])
        for row in subset
        if row.get("time_to_first_expected_file_s") is not None
    ]
    expected_rows = [
        row
        for row in subset
        if row.get("expected_files_touched") or row.get("missing_expected_edits")
    ]
    expected_touch_rate = (
        sum(1 for row in expected_rows if row.get("expected_files_touched")) / len(expected_rows)
        if expected_rows
        else 0.0
    )
    return {
        "runs": float(len(subset)),
        "success_rate": sum(1 for row in subset if row.get("passed")) / len(subset),
        "noise_rate": sum(1 for row in subset if row.get("agentpack_noise")) / len(subset),
        "expected_touch_rate": expected_touch_rate,
        "avg_input_tokens": avg("input_tokens"),
        "avg_output_tokens": avg("agent_output_tokens"),
        "avg_total_tokens": avg("input_tokens") + avg("agent_output_tokens"),
        "avg_total_cost_usd": avg("estimated_total_cost_usd"),
        "avg_duration_s": avg("duration_s"),
        "avg_tool_calls": avg("agent_tool_calls"),
        "avg_time_to_first_expected_file_s": (
            sum(first_file_values) / len(first_file_values)
            if first_file_values
            else 0.0
        ),
    }


def _e2e_ab_metrics(records: list[dict[str, Any]], *, baseline: str, treatment: str) -> dict[str, Any]:
    base = _e2e_strategy_metrics(records, baseline)
    treat = _e2e_strategy_metrics(records, treatment)
    if not base.get("runs") or not treat.get("runs"):
        return {"baseline": base, "treatment": treat, "deltas": {}}
    return {
        "baseline": base,
        "treatment": treat,
        "deltas": {
            "success_rate_pp": (treat["success_rate"] - base["success_rate"]) * 100,
            "task_success_saved": treat["success_rate"] - base["success_rate"],
            "tool_calls_saved": base["avg_tool_calls"] - treat["avg_tool_calls"],
            "token_cost_saved_usd": base["avg_total_cost_usd"] - treat["avg_total_cost_usd"],
            "tokens_saved": base["avg_total_tokens"] - treat["avg_total_tokens"],
            "time_to_first_correct_file_saved_s": (
                base["avg_time_to_first_expected_file_s"] - treat["avg_time_to_first_expected_file_s"]
            ),
            "duration_saved_s": base["avg_duration_s"] - treat["avg_duration_s"],
        },
    }


def _print_e2e_ab_table(
    records: list[dict[str, Any]],
    *,
    baseline: str,
    treatment: str,
    source: Path,
) -> None:
    metrics = _e2e_ab_metrics(records, baseline=baseline, treatment=treatment)
    table = Table(title=f"E2E A/B: {baseline} vs {treatment}", box=box.SIMPLE, show_header=True)
    table.add_column("metric")
    table.add_column(baseline, justify="right")
    table.add_column(treatment, justify="right")
    table.add_column("saved / lift", justify="right")
    base = metrics["baseline"]
    treat = metrics["treatment"]
    deltas = metrics["deltas"]
    if not deltas:
        console.print(f"[yellow]Need results for both {baseline} and {treatment} in {source}[/]")
        return
    table.add_row("runs", f"{base['runs']:.0f}", f"{treat['runs']:.0f}", "-")
    table.add_row("task success", f"{base['success_rate']:.0%}", f"{treat['success_rate']:.0%}", f"{deltas['success_rate_pp']:+.1f} pp")
    table.add_row("expected file touched", f"{base['expected_touch_rate']:.0%}", f"{treat['expected_touch_rate']:.0%}", "-")
    table.add_row("AgentPack noise cases", f"{base['noise_rate']:.0%}", f"{treat['noise_rate']:.0%}", "-")
    table.add_row("tool calls", f"{base['avg_tool_calls']:.1f}", f"{treat['avg_tool_calls']:.1f}", f"{deltas['tool_calls_saved']:+.1f}")
    table.add_row("tokens", f"{base['avg_total_tokens']:,.0f}", f"{treat['avg_total_tokens']:,.0f}", f"{deltas['tokens_saved']:+,.0f}")
    table.add_row("cost", f"${base['avg_total_cost_usd']:.4f}", f"${treat['avg_total_cost_usd']:.4f}", _fmt_signed_usd(deltas["token_cost_saved_usd"]))
    table.add_row("time to first correct file", f"{base['avg_time_to_first_expected_file_s']:.1f}s", f"{treat['avg_time_to_first_expected_file_s']:.1f}s", f"{deltas['time_to_first_correct_file_saved_s']:+.1f}s")
    table.add_row("duration", f"{base['avg_duration_s']:.1f}s", f"{treat['avg_duration_s']:.1f}s", f"{deltas['duration_saved_s']:+.1f}s")
    console.print(table)
    console.print(f"[dim]Source: {source}[/]")


def _e2e_ab_markdown(
    records: list[dict[str, Any]],
    *,
    baseline: str,
    treatment: str,
    source: Path,
) -> str:
    metrics = _e2e_ab_metrics(records, baseline=baseline, treatment=treatment)
    base = metrics["baseline"]
    treat = metrics["treatment"]
    deltas = metrics["deltas"]
    if not deltas:
        return f"Need results for both `{baseline}` and `{treatment}` in `{source}`.\n"
    return "\n".join([
        f"# AgentPack E2E A/B: {baseline} vs {treatment}",
        "",
        f"- source: `{source}`",
        "",
        "| Metric | Baseline | AgentPack | Saved / lift |",
        "|---|---:|---:|---:|",
        f"| runs | {base['runs']:.0f} | {treat['runs']:.0f} | - |",
        f"| task success | {base['success_rate']:.0%} | {treat['success_rate']:.0%} | {deltas['success_rate_pp']:+.1f} pp |",
        f"| expected file touched | {base['expected_touch_rate']:.0%} | {treat['expected_touch_rate']:.0%} | - |",
        f"| AgentPack noise cases | {base['noise_rate']:.0%} | {treat['noise_rate']:.0%} | - |",
        f"| tool calls | {base['avg_tool_calls']:.1f} | {treat['avg_tool_calls']:.1f} | {deltas['tool_calls_saved']:+.1f} |",
        f"| tokens | {base['avg_total_tokens']:,.0f} | {treat['avg_total_tokens']:,.0f} | {deltas['tokens_saved']:+,.0f} |",
        f"| token cost | ${base['avg_total_cost_usd']:.4f} | ${treat['avg_total_cost_usd']:.4f} | {_fmt_signed_usd(deltas['token_cost_saved_usd'])} |",
        f"| time to first correct file | {base['avg_time_to_first_expected_file_s']:.1f}s | {treat['avg_time_to_first_expected_file_s']:.1f}s | {deltas['time_to_first_correct_file_saved_s']:+.1f}s |",
        f"| duration | {base['avg_duration_s']:.1f}s | {treat['avg_duration_s']:.1f}s | {deltas['duration_saved_s']:+.1f}s |",
        "",
    ])


def _fmt_signed_usd(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):.4f}"


def _load_e2e_cases(root: Path, path: Path) -> list[E2ECase]:
    case_path = path if path.is_absolute() else root / path
    data = tomllib.loads(case_path.read_text(encoding="utf-8"))
    cases: list[E2ECase] = []
    for raw in data.get("cases") or []:
        repo_value = raw.get("repo")
        if not repo_value:
            raise ValueError(f"Case {raw.get('name') or '<unnamed>'} missing repo")
        repo = Path(str(repo_value))
        if not repo.is_absolute():
            repo = (case_path.parent / repo).resolve()
        cases.append(
            E2ECase(
                name=str(raw.get("name") or repo.name),
                repo=repo,
                task=str(raw.get("task") or ""),
                test_command=str(raw.get("test_command") or ""),
                setup_command=str(raw.get("setup_command") or ""),
                protected_paths=[str(path) for path in raw.get("protected_paths", [])],
                expected_edit_paths=[str(path) for path in raw.get("expected_edit_paths", [])],
            )
        )
    if not cases:
        raise ValueError(f"No [[cases]] found in {case_path}")
    return cases


def _run_e2e_case(
    case: E2ECase,
    *,
    strategy: str,
    trial: int,
    agent_command: str,
    timeout: int,
    keep_workdir: bool,
    input_cost_per_mtok: float = 0.0,
    output_cost_per_mtok: float = 0.0,
) -> E2EResult:
    start = time.perf_counter()
    work_root = Path(tempfile.mkdtemp(prefix=f"agentpack-e2e-{case.name}-{strategy}-"))
    repo = work_root / "repo"
    shutil.copytree(case.repo, repo, ignore=shutil.ignore_patterns(".git", ".agentpack", "__pycache__", ".pytest_cache"))
    _init_e2e_git(repo)
    if case.setup_command:
        subprocess.run(case.setup_command, cwd=repo, shell=True, capture_output=True, text=True, timeout=timeout)
    protected_hashes = _hash_protected_paths(repo, case.protected_paths)

    prompt = _e2e_prompt(case, strategy, repo)
    prompt_path = repo / ".agentpack_e2e_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    agent_args = _agent_args(agent_command, prompt_path, repo)
    timed_out = False
    agent_start_epoch = time.time()
    try:
        agent = subprocess.run(agent_args, cwd=repo, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        agent = _timeout_result(agent_args, exc)
    agent_log_path = work_root / "agent.log"
    test_log_path = work_root / "test.log"
    _write_e2e_process_log(agent_log_path, agent)
    try:
        test = subprocess.run(case.test_command, cwd=repo, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        test = _timeout_result(case.test_command, exc)
    _write_e2e_process_log(test_log_path, test)
    input_tokens = estimate_tokens(prompt)
    agent_output_tokens = _process_output_tokens(agent)
    input_cost = _estimate_token_cost(input_tokens, input_cost_per_mtok)
    output_cost = _estimate_token_cost(agent_output_tokens, output_cost_per_mtok)
    changed = sorted(git.dirty_files(repo)) if git.is_git_repo(repo) else []
    public_changed = _public_changed_files(changed)
    source_changed = [path for path in public_changed if not _is_test_path(path)]
    test_changed = [path for path in public_changed if _is_test_path(path)]
    protected_changed = _changed_protected_paths(repo, protected_hashes)
    expected_touched = _expected_files_touched(public_changed, case.expected_edit_paths)
    missing_expected = sorted(set(case.expected_edit_paths) - set(expected_touched))
    unexpected_touched = _unexpected_files_touched(public_changed, case.expected_edit_paths)
    agentpack_noise = _agentpack_noise(strategy, unexpected_touched, missing_expected, public_changed)
    time_to_first_expected_file = _time_to_first_expected_file(repo, expected_touched, agent_start_epoch)
    tool_calls = _estimate_agent_tool_calls(agent)
    duration = time.perf_counter() - start
    passed = not timed_out and agent.returncode == 0 and test.returncode == 0 and not protected_changed

    if not keep_workdir and passed:
        shutil.rmtree(work_root, ignore_errors=True)

    return E2EResult(
        schema_version=2,
        case=case.name,
        strategy=strategy,
        trial=trial,
        passed=passed,
        duration_s=round(duration, 3),
        input_tokens=input_tokens,
        agent_output_tokens=agent_output_tokens,
        estimated_input_cost_usd=round(input_cost, 8),
        estimated_output_cost_usd=round(output_cost, 8),
        estimated_total_cost_usd=round(input_cost + output_cost, 8),
        agent_returncode=agent.returncode,
        test_returncode=test.returncode,
        timed_out=timed_out,
        agent_tool_calls=tool_calls,
        time_to_first_expected_file_s=round(time_to_first_expected_file, 3) if time_to_first_expected_file is not None else None,
        changed_files=changed,
        source_files_changed=source_changed,
        test_files_changed=test_changed,
        protected_files_changed=protected_changed,
        expected_files_touched=expected_touched,
        missing_expected_edits=missing_expected,
        unexpected_files_touched=unexpected_touched,
        agentpack_noise=agentpack_noise,
        agent_log_path=str(agent_log_path),
        test_log_path=str(test_log_path),
        workdir=str(work_root),
    )


def _public_changed_files(changed: list[str]) -> list[str]:
    internal = {".agentpack_e2e_prompt.txt"}
    return sorted(path for path in changed if path not in internal and not _is_generated_e2e_path(path))


def _is_generated_e2e_path(path: str) -> bool:
    return (
        path == ".agentpack"
        or path.startswith(".agentpack/")
        or path == ".agentpack/"
        or "__pycache__/" in path
        or path.endswith("__pycache__/")
        or path.endswith(".pyc")
    )


def _is_test_path(path: str) -> bool:
    name = Path(path).name
    parts = {part.lower() for part in Path(path.lower()).parts}
    return (
        path.startswith("tests/")
        or "test" in parts
        or "/tests/" in path
        or "__test__/" in path
        or "__tests__/" in path
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
    )


def _expected_files_touched(changed: list[str], expected_edit_paths: list[str]) -> list[str]:
    expected = set(expected_edit_paths)
    return sorted(path for path in changed if path in expected)


def _unexpected_files_touched(changed: list[str], expected_edit_paths: list[str]) -> list[str]:
    if not expected_edit_paths:
        return []
    expected = set(expected_edit_paths)
    return sorted(path for path in changed if path not in expected)


def _agentpack_noise(strategy: str, unexpected: list[str], missing_expected: list[str], changed: list[str]) -> list[str]:
    if "agentpack" not in strategy:
        return []
    noise: list[str] = []
    if unexpected:
        noise.append(f"unexpected edits: {', '.join(unexpected[:5])}")
    if missing_expected:
        noise.append(f"missed expected files: {', '.join(missing_expected[:5])}")
    generated = [path for path in changed if _is_generated_e2e_path(path)]
    if generated:
        noise.append(f"generated AgentPack files changed: {', '.join(generated[:5])}")
    return noise


def _process_output_tokens(result: subprocess.CompletedProcess[str]) -> int:
    return estimate_tokens("\n".join(part for part in (result.stdout, result.stderr) if part))


def _estimate_agent_tool_calls(result: subprocess.CompletedProcess[str]) -> int:
    text = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if not text:
        return 0
    patterns = [
        r"\btool[_ -]?call\b",
        r"\bexec_command\b",
        r"\bapply_patch\b",
        r"\bread_file\b",
        r"\bwrite_file\b",
        r"\blist_files\b",
        r"\bsearch\b",
        r"\brg\b",
    ]
    return sum(len(re.findall(pattern, text, flags=re.IGNORECASE)) for pattern in patterns)


def _time_to_first_expected_file(repo: Path, expected_touched: list[str], agent_start_epoch: float) -> float | None:
    deltas: list[float] = []
    for path in expected_touched:
        target = repo / path
        if not target.exists():
            continue
        try:
            delta = target.stat().st_mtime - agent_start_epoch
        except OSError:
            continue
        if delta >= 0:
            deltas.append(delta)
    return min(deltas) if deltas else None


def _estimate_token_cost(tokens: int, cost_per_mtok: float) -> float:
    if tokens <= 0 or cost_per_mtok <= 0:
        return 0.0
    return tokens / 1_000_000 * cost_per_mtok


def _hash_protected_paths(repo: Path, paths: list[str]) -> dict[str, str | None]:
    return {path: _file_sha256(repo / path) for path in paths}


def _changed_protected_paths(repo: Path, before: dict[str, str | None]) -> list[str]:
    return [
        path
        for path, expected in before.items()
        if _file_sha256(repo / path) != expected
    ]


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_e2e_process_log(path: Path, result: subprocess.CompletedProcess[str]) -> None:
    path.write_text(
        "\n".join([
            f"returncode={result.returncode}",
            "",
            "STDOUT:",
            result.stdout,
            "",
            "STDERR:",
            result.stderr,
        ]),
        encoding="utf-8",
    )


def _timeout_result(
    args: str | list[str],
    exc: subprocess.TimeoutExpired,
) -> subprocess.CompletedProcess[str]:
    stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
    stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
    return subprocess.CompletedProcess(
        args=args,
        returncode=124,
        stdout=stdout,
        stderr=stderr + f"\nTimed out after {exc.timeout} seconds.",
    )


def _e2e_prompt(case: E2ECase, strategy: str, repo: Path) -> str:
    base = (
        f"Task: {case.task}\n\n"
        "Edit the repository to complete the task. Keep changes minimal. "
        f"After editing, the validation command should pass: `{case.test_command}`.\n"
    )
    if strategy == "no-context":
        return base
    if strategy == "grep":
        return base + "\nRelevant grep output:\n" + _grep_context(case.task, repo)
    if strategy == "agentpack-lite":
        return base + "\nAgentPack lite context:\n" + _agentpack_lite_context(case, repo)
    if strategy == "hybrid":
        return (
            base
            + "\nRelevant grep output:\n"
            + _grep_context(case.task, repo)
            + "\n\nAgentPack lite context:\n"
            + _agentpack_lite_context(case, repo)
        )
    if strategy == "agentpack":
        result = PackService().run(PackRequest(
            root=repo,
            agent="generic",
            task=case.task,
            mode="balanced",
            budget=40000,
            since=None,
            refresh=False,
            task_source="e2e",
        ))
        context = result.out_path.read_text(encoding="utf-8") if result.out_path.exists() else ""
        return base + "\nAgentPack context:\n" + context
    raise ValueError(f"unknown strategy: {strategy}")


def _agentpack_lite_context(case: E2ECase, repo: Path) -> str:
    lite = load_config(repo).context_lite
    result = PackService().run(PackRequest(
        root=repo,
        agent="generic",
        task=case.task,
        mode="lite",
        budget=lite.budget,
        since=None,
        refresh=False,
        task_source="e2e-lite",
    ))
    pack = result.pack
    lines = [
        "Purpose: cheap repo situational awareness. Inspect files before editing; omitted paths are warnings, not evidence.",
        "",
        "## Selected File Map",
        "| File | Mode | Score | Why |",
        "|---|---|---:|---|",
    ]
    for selected in pack.selected_files[:lite.max_selected_files]:
        why = ", ".join(selected.reasons[:3]) or "-"
        lines.append(f"| `{selected.path}` | {selected.include_mode} | {selected.score:.0f} | {why} |")

    if pack.omitted_relevant_files:
        lines.extend([
            "",
            "## High-Risk Omitted Files",
            "| File | Risk | Score | Why |",
            "|---|---|---:|---|",
        ])
        for omitted in pack.omitted_relevant_files[:lite.max_omitted_files]:
            why = ", ".join(omitted.reasons[:3]) or omitted.omission_reason
            lines.append(f"| `{omitted.path}` | {omitted.risk.upper()} | {omitted.score:.0f} | {why} |")

    if pack.changed_files:
        lines.extend(["", "## Changed Files"])
        lines.extend(f"- `{path}`" for path in pack.changed_files[:15])

    stubs = _lite_file_stubs(pack.selected_files[:lite.max_stubs], summary_chars=lite.summary_chars)
    if stubs:
        lines.extend(["", "## File Stubs", *stubs])

    return "\n".join(lines)


def _lite_file_stubs(selected_files: list[Any], *, summary_chars: int = 500) -> list[str]:
    lines: list[str] = []
    for selected in selected_files:
        parts = [f"### `{selected.path}`"]
        if selected.summary:
            parts.append(_truncate_line(selected.summary, summary_chars))
        signatures = [
            symbol.signature or f"{symbol.kind} {symbol.name}"
            for symbol in selected.symbols[:8]
        ]
        if signatures:
            parts.append("Symbols: " + "; ".join(signatures))
        if len(parts) > 1:
            lines.extend(parts)
    return lines


def _truncate_line(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _grep_context(task: str, repo: Path, *, max_lines: int = 120) -> str:
    terms = [term for term in task.replace("_", " ").replace("-", " ").split() if len(term) >= 4]
    if not terms:
        return "(no grep terms)"
    outputs: list[str] = []
    for term in terms[:8]:
        try:
            result = subprocess.run(
                ["rg", "-n", "--glob", "!.git", "--glob", "!.agentpack", term],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.stdout:
            outputs.extend(result.stdout.splitlines())
        if len(outputs) >= max_lines:
            break
    return "\n".join(outputs[:max_lines]) or "(no grep matches)"


def _agent_args(command: str, prompt_path: Path, repo: Path) -> list[str]:
    rendered = command.replace("{prompt}", str(prompt_path)).replace("{repo}", str(repo))
    args = shlex.split(rendered)
    if "{prompt}" not in command:
        args.append(str(prompt_path))
    return args


def _init_e2e_git(repo: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "agentpack@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "AgentPack E2E"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=repo, check=True)


def _print_e2e_summary(results: list[E2EResult], out_path: Path) -> None:
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("strategy")
    table.add_column("runs", justify="right")
    table.add_column("pass rate", justify="right")
    table.add_column("timeouts", justify="right")
    table.add_column("expected touch", justify="right")
    table.add_column("avg tokens", justify="right")
    table.add_column("avg cost", justify="right")
    table.add_column("avg tools", justify="right")
    table.add_column("first file", justify="right")
    table.add_column("avg seconds", justify="right")
    table.add_column("pass/min", justify="right")
    table.add_column("pass/$", justify="right")
    for strategy in sorted({result.strategy for result in results}):
        subset = [result for result in results if result.strategy == strategy]
        pass_rate = sum(1 for result in subset if result.passed) / len(subset)
        timeout_rate = sum(1 for result in subset if result.timed_out) / len(subset)
        expected_cases = [result for result in subset if result.expected_files_touched or result.missing_expected_edits]
        expected_touch_rate = (
            sum(1 for result in expected_cases if result.expected_files_touched) / len(expected_cases)
            if expected_cases
            else None
        )
        avg_tokens = sum(result.input_tokens for result in subset) / len(subset)
        avg_cost = sum(result.estimated_total_cost_usd for result in subset) / len(subset)
        avg_tools = sum(result.agent_tool_calls for result in subset) / len(subset)
        first_file_times = [
            result.time_to_first_expected_file_s
            for result in subset
            if result.time_to_first_expected_file_s is not None
        ]
        avg_first_file = sum(first_file_times) / len(first_file_times) if first_file_times else None
        avg_seconds = sum(result.duration_s for result in subset) / len(subset)
        total_seconds = sum(result.duration_s for result in subset)
        total_cost = sum(result.estimated_total_cost_usd for result in subset)
        pass_per_minute = (sum(1 for result in subset if result.passed) / total_seconds * 60) if total_seconds else 0.0
        pass_per_dollar = (sum(1 for result in subset if result.passed) / total_cost) if total_cost else 0.0
        table.add_row(
            strategy,
            str(len(subset)),
            f"{pass_rate:.0%}",
            f"{timeout_rate:.0%}",
            f"{expected_touch_rate:.0%}" if expected_touch_rate is not None else "-",
            f"{avg_tokens:,.0f}",
            f"${avg_cost:.4f}" if avg_cost else "-",
            f"{avg_tools:.1f}",
            f"{avg_first_file:.1f}s" if avg_first_file is not None else "-",
            f"{avg_seconds:.1f}",
            f"{pass_per_minute:.2f}",
            f"{pass_per_dollar:.2f}" if total_cost else "-",
        )
    console.print(table)
    console.print(f"[dim]JSONL: {out_path}[/]")


def _build_synthetic_repo(root: Path, *, files: int, target_every: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    for index in range(files):
        target = "\n    return target_symbol(value)\n" if index % max(1, target_every) == 0 else "\n    return value\n"
        (src / f"file_{index:04d}.py").write_text(
            f"def helper_{index}(value):{target}\n",
            encoding="utf-8",
        )
    (root / ".agentpack").mkdir()
    (root / ".gitignore").write_text(
        "\n".join([
            ".agentpack/cache/",
            ".agentpack/snapshots/",
            ".agentpack/context*.md",
            ".agentpack/metrics.jsonl",
            ".agentpack/pack_metadata.json",
            ".agentpack/term_stats.json",
            "",
        ]),
        encoding="utf-8",
    )
    (root / ".agentpack" / "config.toml").write_text(
        "[context]\ndefault_budget = 40000\nincremental_scan = true\ninclude_receipts = true\n",
        encoding="utf-8",
    )
    (root / ".agentpack" / "task.md").write_text("fix target_symbol caller behavior\n", encoding="utf-8")


def _init_synthetic_git(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "agentpack@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "AgentPack Benchmark"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=root, check=True)


@benchmark_app.callback(invoke_without_command=True)
def benchmark(
    ctx: typer.Context,
    task: str = typer.Option("", "--task", help="Single task to benchmark (skips cases file)."),
    mode: str = typer.Option("balanced", "--mode", help=f"Mode for single-task run ({MODE_HELP})."),
    workspace: str = typer.Option("", "--workspace", help="Restrict benchmark packs to a workspace, e.g. apps/web."),
    cases: str = typer.Option("", "--cases", help="Path to TOML cases file (default: .agentpack/benchmark.toml)."),
    compare: bool = typer.Option(False, "--compare", help="Compare lite/balanced/deep for each task."),
    init: bool = typer.Option(False, "--init", help="Scaffold a benchmark.toml and exit."),
    results_template: bool = typer.Option(False, "--results-template", help="Create benchmarks/results/YYYY-MM-DD.md for publishing benchmark evidence."),
    from_history: int = typer.Option(0, "--from-history", help="Sample last N unique tasks from metrics.jsonl history."),
    write_cases: bool = typer.Option(False, "--write-cases", help="Append --from-history cases to .agentpack/benchmark.toml."),
    sample_fixtures: bool = typer.Option(False, "--sample-fixtures", help="Run bundled FastAPI/Next.js/mixed-repo fixture evals from a source checkout."),
    release_gate: bool = typer.Option(False, "--release-gate", help="Run the public real-repo release gate."),
    public_suite: bool = typer.Option(False, "--public-suite", help="Alias for the reproducible public benchmark suite."),
    reproduce: str = typer.Option("", "--reproduce", help="Reproduce a published benchmark version, e.g. v0.3.20."),
    public_repos: bool = typer.Option(False, "--public-repos", help="Run real public-repo commit cases from benchmarks/public-repos.toml."),
    public_repos_file: str = typer.Option("", "--public-repos-file", help="Path to public repo benchmark manifest."),
    public_repos_cache: str = typer.Option("", "--public-repos-cache", help="Directory for cached public repo clones."),
    public_repo_filter: str = typer.Option("", "--public-repo-filter", help="Comma-separated public repo names to run, e.g. gin,vite."),
    public_task_type_filter: str = typer.Option("", "--public-task-type-filter", help="Comma-separated public task types to run, e.g. go-service,typescript."),
    refresh_public_repos: bool = typer.Option(False, "--refresh-public-repos", help="Delete and reclone public repo benchmark cache before running."),
    write_public_repos_lock: str = typer.Option("", "--write-public-repos-lock", help="Write resolved public repo sampled cases to a replayable TOML manifest."),
    benchmark_jsonl: str = typer.Option("", "--benchmark-jsonl", help="Write benchmark case metrics to this JSONL path."),
    owner_evidence_report: str = typer.Option("", "--owner-evidence-report", help="Analyze comparative owner evidence from benchmark JSONL and exit."),
    ablation_jsonl: str = typer.Option("", "--ablation-jsonl", help="Analyze an existing benchmark JSONL for pruning/replacement ceiling and exit."),
    public_table: bool = typer.Option(False, "--public-table", help="Write a publishable Markdown benchmark table under benchmarks/results/."),
    no_public_table: bool = typer.Option(False, "--no-public-table", help="Do not write a benchmark results markdown table."),
    misses: bool = typer.Option(False, "--misses", help="Show diagnostics for expected files that were not selected."),
    prove_targets: bool = typer.Option(False, "--prove-targets", help="Exit non-zero unless recall/token precision targets pass."),
    min_recall: float = typer.Option(0.60, "--min-recall", help="Recall target for --prove-targets."),
    min_token_precision: float = typer.Option(0.50, "--min-token-precision", help="Token precision target for --prove-targets."),
) -> None:
    """Benchmark file selection quality and token efficiency across tasks."""
    if ctx.invoked_subcommand is not None:
        return
    if not is_requested_mode(mode):
        console.print(f"[red]{invalid_mode_message(mode)}[/]")
        raise typer.Exit(1)
    mode = normalize_mode(mode)
    root = _root()
    if owner_evidence_report:
        records = _load_jsonl(Path(owner_evidence_report))
        if not records:
            console.print(f"[red]No benchmark records found in {owner_evidence_report}[/]")
            raise typer.Exit(1)
        report = _owner_evidence_report(records)
        _print_owner_evidence_report(report, label=owner_evidence_report)
        return
    if ablation_jsonl:
        records = _load_jsonl(Path(ablation_jsonl))
        if not records:
            console.print(f"[red]No benchmark records found in {ablation_jsonl}[/]")
            raise typer.Exit(1)
        report = _benchmark_ablation_report(records, min_token_precision=min_token_precision)
        _print_benchmark_ablation_report(report, label=ablation_jsonl)
        return
    if reproduce:
        normalized_reproduce = reproduce.strip().lstrip("v")
        if normalized_reproduce != "0.3.20":
            console.print(f"[yellow]No reproducible public suite registered for {reproduce}. Available: v0.3.20[/]")
            raise typer.Exit(1)
        public_suite = True
    if public_suite:
        public_repos = True
        prove_targets = True
        misses = True
        public_table = not no_public_table
        console.print(f"[bold]Public suite:[/] reproducible v{reproduce.strip().lstrip('v') or '0.3.20'} benchmark.")
    if release_gate:
        public_repos = True
        prove_targets = True
        misses = True
        public_table = not no_public_table
        console.print("[bold]Release gate:[/] public real-repo benchmark with target proof.")
    if write_public_repos_lock:
        public_repos = True

    if init:
        out = _scaffold_cases(root)
        console.print(f"[green]✓[/] Created [bold]{out}[/]")
        console.print("  Edit the file to add your tasks and expected files, then run [bold]agentpack benchmark[/].")
        return

    if results_template:
        out = _write_results_template(root)
        console.print(f"[green]✓[/] Created [bold]{out}[/]")
        console.print("  Fill it with `agentpack benchmark --compare --misses` results from real historical tasks.")
        return

    if sample_fixtures:
        fixtures_root = root / "tests" / "fixtures"
        fixture_cases = _sample_fixture_cases(fixtures_root)
        if not fixture_cases:
            console.print(f"[yellow]No bundled fixture repos found at {fixtures_root}[/]")
            console.print("  This demo is available from an AgentPack source checkout. For your own repo, run [bold]agentpack benchmark --init[/].")
            raise typer.Exit(1)

        if compare:
            expanded_fixtures: list[FixtureCase] = []
            for fixture_case in fixture_cases:
                for fixture_mode in ("lite", "balanced", "deep"):
                    expanded_fixtures.append(
                        FixtureCase(
                            fixture=fixture_case.fixture,
                            root=fixture_case.root,
                            case=BenchmarkCase(
                                task=fixture_case.case.task,
                                mode=fixture_mode,
                                expected_files=fixture_case.case.expected_files,
                                task_type=fixture_case.case.task_type,
                                workspace=fixture_case.case.workspace,
                                budget=fixture_case.case.budget,
                            ),
                        )
                    )
            fixture_cases = expanded_fixtures

        console.print(f"\n[bold]Running {len(fixture_cases)} sample fixture benchmark case(s)...[/]\n")

        results: list[CaseResult] = []
        with tempfile.TemporaryDirectory(prefix="agentpack-benchmark-") as temp_dir:
            temp_root = Path(temp_dir)
            for i, fixture_case in enumerate(fixture_cases, 1):
                case_root = temp_root / f"{i:02d}-{fixture_case.fixture}"
                _copy_fixture(fixture_case.root, case_root)
                label = f"[{i}/{len(fixture_cases)}] {fixture_case.fixture}: {fixture_case.case.task[:42]}  mode={fixture_case.case.mode}"
                with console.status(f"[dim]{label}[/]"):
                    try:
                        result = _run_case(case_root, fixture_case.case)
                        result.case.task = f"{fixture_case.fixture}: {result.case.task}"
                        results.append(result)
                    except Exception as e:
                        console.print(f"[red]Error on fixture case '{fixture_case.case.task}': {e}[/]")

        if not results:
            raise typer.Exit(1)

        console.print("[dim]Sample fixtures are regression smoke evals for this source checkout, not the public release gate.[/]")
        fixture_names = ", ".join(sorted({fixture_case.fixture for fixture_case in fixture_cases}))
        console.print(f"[dim]Fixtures:[/] {fixture_names}")
        if len(results) == 1:
            _print_case_detail(results[0], show_misses=misses)
            _print_quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)
        else:
            console.print("\n[bold]Summary[/]")
            _print_fixture_summary_table(results)
            _print_task_type_summary(results)
            _print_intent_summary(results)
            _print_precision_diagnostics(results)
            if misses:
                _print_miss_details(results)
            _print_quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)
        if public_table:
            from agentpack import __version__
            out = _write_public_benchmark_table(
                root,
                results,
                suite=f"source-checkout fixtures ({fixture_names})",
                version=__version__,
                command="agentpack benchmark --sample-fixtures --misses --public-table",
            )
            console.print(f"[green]✓[/] Wrote public benchmark table: [bold]{out}[/]")
        if prove_targets and not _quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)[0]:
            raise typer.Exit(2)
        return

    if public_repos:
        manifest = (
            Path(public_repos_file)
            if public_repos_file
            else _default_release_repos_file(root)
            if release_gate
            else _default_public_repos_file(root)
        )
        if not manifest.exists():
            console.print(f"[yellow]No public repo manifest found at {manifest}[/]")
            console.print("  Use [bold]benchmarks/public-repos.toml[/] or pass [bold]--public-repos-file[/].")
            raise typer.Exit(1)
        release_config = _load_release_gate_config(manifest) if release_gate else ReleaseGateConfig()
        if release_config.min_recall is not None:
            min_recall = max(min_recall, release_config.min_recall)
        if release_config.min_token_precision is not None:
            min_token_precision = max(min_token_precision, release_config.min_token_precision)

        specs = _load_public_repo_specs(manifest)
        specs = _filter_public_repo_specs(
            specs,
            repo_filter=public_repo_filter,
            task_type_filter=public_task_type_filter,
        )
        cache = Path(public_repos_cache) if public_repos_cache else None
        if write_public_repos_lock:
            with console.status("[dim]Resolving public repo sampled cases for lock file...[/]"):
                specs = _resolve_public_repo_lock_specs(
                    root,
                    specs,
                    cache_dir=cache,
                    refresh=refresh_public_repos,
                )
            out = _write_public_repo_lock(Path(write_public_repos_lock), specs)
            console.print(f"[green]✓[/] Wrote public repo lock manifest: [bold]{out}[/]")
            refresh_public_repos = False
        case_count = sum(len(spec.cases) + int(getattr(spec, "sample_history", 0) or 0) for spec in specs)
        if not specs or case_count == 0:
            console.print(f"[yellow]No public repo cases found in {manifest}[/]")
            raise typer.Exit(1)

        console.print(f"\n[bold]Running {case_count} public real-repo benchmark case(s)...[/]")
        console.print(f"[dim]Manifest:[/] {manifest}")
        if public_repo_filter:
            console.print(f"[dim]Repo filter:[/] {public_repo_filter}")
        if public_task_type_filter:
            console.print(f"[dim]Task-type filter:[/] {public_task_type_filter}")
        console.print("[dim]Each case checks out the parent of a real public commit and scores files changed by that commit.[/]\n")
        with console.status("[dim]Cloning/checking out public repo cases...[/]"):
            results = _run_public_repo_suite(root, specs, cache_dir=cache, refresh=refresh_public_repos)

        if not results:
            raise typer.Exit(1)

        scored_case_count = sum(1 for result in results if result.case.expected_files)
        if release_config.min_scored_cases is not None and scored_case_count < release_config.min_scored_cases:
            console.print(
                "[red]Release gate scored too few cases: "
                f"{scored_case_count} < {release_config.min_scored_cases}[/]"
            )
            raise typer.Exit(2)

        console.print("\n[bold]Summary[/]")
        _print_summary_table(results)
        _print_task_type_summary(results)
        _print_intent_summary(results)
        _print_precision_diagnostics(results)
        if misses:
            _print_miss_details(results)
        _print_quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)
        if benchmark_jsonl:
            out = _write_results_jsonl(Path(benchmark_jsonl), results)
            console.print(f"[green]✓[/] Wrote benchmark JSONL: [bold]{out}[/]")
        if public_table:
            from agentpack import __version__
            repo_names = ", ".join(spec.name for spec in specs)
            out = _write_public_benchmark_table(
                root,
                results,
                suite=f"public real-repo commits ({repo_names})",
                version=__version__,
                command=(
                    f"agentpack benchmark --public-suite --reproduce {reproduce}"
                    if public_suite and reproduce
                    else "agentpack benchmark --release-gate"
                ),
            )
            console.print(f"[green]✓[/] Wrote public benchmark table: [bold]{out}[/]")
        if prove_targets and not _quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)[0]:
            raise typer.Exit(2)
        return

    # Build case list
    if from_history > 0:
        bench_cases = _load_history_cases(root, from_history)
        if not bench_cases:
            console.print("[yellow]No task history found in metrics.jsonl. Run agentpack pack first.[/]")
            raise typer.Exit(1)
        if write_cases:
            out = _append_benchmark_cases(root, bench_cases)
            console.print(f"[green]✓[/] Appended {len(bench_cases)} history case(s) to [bold]{out}[/]")
            console.print("[yellow]History cases do not prove recall until expected_files are filled.[/]")
    elif task:
        resolved = _resolve_task(task) if task == "auto" else task
        bench_cases = [BenchmarkCase(task=resolved, mode=mode, workspace=workspace or None)]
    else:
        cases_path = Path(cases) if cases else root / ".agentpack" / "benchmark.toml"
        if not cases_path.exists():
            console.print(f"[yellow]No cases file found at {cases_path}[/]")
            console.print("  Run [bold]agentpack benchmark --init[/] to scaffold one, or use [bold]--task \"...\"[/]")
            raise typer.Exit(1)
        bench_cases = _load_cases(cases_path)
        if not bench_cases:
            console.print("[yellow]No cases defined in benchmark file.[/]")
            raise typer.Exit(1)
    if workspace and not compare:
        bench_cases = [
            BenchmarkCase(
                task=c.task,
                mode=c.mode,
                expected_files=c.expected_files,
                task_type=c.task_type,
                workspace=workspace,
                budget=c.budget,
            )
            for c in bench_cases
        ]

    # Expand for compare mode
    if compare:
        expanded: list[BenchmarkCase] = []
        for c in bench_cases:
            for m in ("lite", "balanced", "deep"):
                expanded.append(
                    BenchmarkCase(
                        task=c.task,
                        mode=m,
                        expected_files=c.expected_files,
                        task_type=c.task_type,
                        workspace=workspace or c.workspace,
                        budget=c.budget,
                    )
                )
        bench_cases = expanded

    console.print(f"\n[bold]Running {len(bench_cases)} benchmark case(s)...[/]\n")

    results: list[CaseResult] = []
    for i, c in enumerate(bench_cases, 1):
        label = f"[{i}/{len(bench_cases)}] {c.task[:50]}  mode={c.mode}"
        with console.status(f"[dim]{label}[/]"):
            try:
                r = _run_case(root, c)
                _persist_result(root, r)
                results.append(r)
            except Exception as e:
                console.print(f"[red]Error on case '{c.task}': {e}[/]")

    if not results:
        raise typer.Exit(1)

    # Output
    if compare and len(set(r.case.task for r in results)) == 1:
        _print_compare_table(results[0].case.task, results)
        if misses:
            _print_miss_details(results)
        _print_quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)
    elif len(results) == 1:
        _print_case_detail(results[0], show_misses=misses)
        _print_quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)
    else:
        if not compare:
            for r in results:
                _print_case_detail(r, show_misses=misses)
        console.print("\n[bold]Summary[/]")
        _print_summary_table(results)
        _print_task_type_summary(results)
        _print_intent_summary(results)
        _print_precision_diagnostics(results)
        if misses:
            _print_miss_details(results)
        _print_quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)
    if public_table:
        from agentpack import __version__
        out = _write_public_benchmark_table(
            root,
            results,
            suite="current repo benchmark.toml",
            version=__version__,
            command="agentpack benchmark --misses --public-table",
        )
        console.print(f"[green]✓[/] Wrote public benchmark table: [bold]{out}[/]")
    if prove_targets and not _quality_status(results, min_recall=min_recall, min_token_precision=min_token_precision)[0]:
        raise typer.Exit(2)
