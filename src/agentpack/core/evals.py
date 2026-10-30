from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]
import tomli_w

from agentpack.core.redactor import redact_secrets
from agentpack.core.citations import validate_citations
from agentpack.core.models import Citation
from agentpack.learning.episodes import record_episode


FAILURE_CLASSES = (
    "context",
    "tool",
    "planning",
    "reasoning",
    "implementation",
    "verification",
    "permission",
    "format",
    "hallucination",
    "over_action",
    "under_action",
    "harness",
    "flaky",
    "spec_ambiguous",
)
FAILURE_SOURCES = ("agent_failed", "harness_failed", "test_flaky", "spec_ambiguous", "unknown")


@dataclass
class EvalCheck:
    name: str
    command: str
    timeout_s: int = 120
    retries: int = 0


@dataclass
class GoldenFile:
    actual: str
    expected: str
    binary: bool = False


@dataclass
class EvalCase:
    id: str
    task: str
    failure_class: str
    failure_source: str = "agent_failed"
    base_ref: str = "HEAD"
    patch_file: str = ""
    patch_redaction_warnings: list[str] = field(default_factory=list)
    required_changed_files: list[str] = field(default_factory=list)
    forbidden_changed_files: list[str] = field(default_factory=list)
    max_changed_files: int | None = None
    max_changed_lines: int | None = None
    checks: list[EvalCheck] = field(default_factory=list)
    golden_files: list[GoldenFile] = field(default_factory=list)
    agent: str = ""
    prompt_file: str = ""
    context_file: str = ""
    context_hash: str = ""
    selected_files: list[str] = field(default_factory=list)
    agentpack_version: str = ""
    citation_manifest: str = ""
    min_citation_coverage: float | None = None
    max_invalid_citations: int | None = None
    require_review_citations: bool = False


@dataclass
class CheckResult:
    name: str
    passed: bool
    duration_s: float
    exit_code: int | None = None
    detail: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    attempts: int = 1
    flaky: bool = False


@dataclass
class EvalResult:
    case: EvalCase
    passed: bool
    duration_s: float
    changed_files: list[str]
    changed_lines: int
    checks: list[CheckResult]
    variant: str = "agentpack"
    citation_coverage: float = 0.0
    invalid_citation_count: int = 0
    uncited_claim_count: int = 0

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]


def default_eval_cases_path(root: Path) -> Path:
    return root / ".agentpack" / "evals.toml"


def eval_results_path(root: Path) -> Path:
    return root / ".agentpack" / "eval_results.jsonl"


def scaffold_eval_cases(root: Path) -> Path:
    out = default_eval_cases_path(root)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# AgentPack deterministic eval cases\n"
        "# Run agents outside this harness, then let AgentPack verify outcomes.\n\n"
        "[[cases]]\n"
        'id = "auth-timeout"\n'
        'task = "fix auth token timeout"\n'
        'failure_class = "context"\n'
        'failure_source = "agent_failed"\n'
        'base_ref = "HEAD"\n'
        'required_changed_files = ["src/auth/token.py"]\n'
        'forbidden_changed_files = ["src/db/**"]\n'
        "max_changed_files = 5\n"
        "max_changed_lines = 250\n\n"
        "[[cases.checks]]\n"
        'name = "tests"\n'
        'command = "pytest tests/test_auth.py -q"\n'
        "timeout_s = 120\n",
        encoding="utf-8",
    )
    return out


def load_eval_cases(path: Path) -> list[EvalCase]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for raw in data.get("cases", []):
        case = _parse_case(raw)
        if case.id in seen_ids:
            raise ValueError(f"duplicate eval case id: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)
    return cases


def run_eval_suite(
    root: Path,
    cases: list[EvalCase],
    *,
    variant: str = "agentpack",
    replay: bool = False,
) -> list[EvalResult]:
    return [run_eval_case(root, case, variant=variant, replay=replay) for case in cases]


def run_eval_case(root: Path, case: EvalCase, *, variant: str = "agentpack", replay: bool = False) -> EvalResult:
    if replay:
        return _run_eval_case_replay(root, case, variant=variant)
    return _run_eval_case_in_root(root, case, variant=variant)


def _run_eval_case_in_root(root: Path, case: EvalCase, *, variant: str = "agentpack") -> EvalResult:
    started = time.perf_counter()
    changed_files, changed_lines, diff_error = git_diff_summary(root, case.base_ref)
    check_results: list[CheckResult] = []

    needs_diff = bool(
        case.required_changed_files
        or case.forbidden_changed_files
        or case.max_changed_files is not None
        or case.max_changed_lines is not None
    )
    if diff_error and needs_diff:
        check_results.append(CheckResult(
            name="git_diff",
            passed=False,
            duration_s=0.0,
            detail=diff_error,
        ))
    if not diff_error:
        check_results.extend(_run_native_checks(root, case, changed_files, changed_lines))
    elif _case_needs_citation_checks(case):
        check_results.extend(_citation_check_results(root, case))

    for golden in case.golden_files:
        check_results.append(_compare_golden_file(root, golden))

    for check in case.checks:
        check_results.append(_run_command_check(root, check))

    return EvalResult(
        case=case,
        passed=all(check.passed for check in check_results),
        duration_s=time.perf_counter() - started,
        changed_files=changed_files,
        changed_lines=changed_lines,
        checks=check_results,
        variant=variant,
        **_eval_citation_metrics(root, case),
    )


def _run_eval_case_replay(root: Path, case: EvalCase, *, variant: str = "agentpack") -> EvalResult:
    started = time.perf_counter()
    if not case.patch_file:
        return EvalResult(
            case=case,
            passed=False,
            duration_s=time.perf_counter() - started,
            changed_files=[],
            changed_lines=0,
            checks=[CheckResult("replay", False, 0.0, detail="replay requires patch_file")],
            variant=variant,
        )

    patch_path = (root / case.patch_file).resolve()
    if not patch_path.exists():
        return EvalResult(
            case=case,
            passed=False,
            duration_s=time.perf_counter() - started,
            changed_files=[],
            changed_lines=0,
            checks=[CheckResult("replay", False, 0.0, detail=f"patch file missing: {case.patch_file}")],
            variant=variant,
        )

    with tempfile.TemporaryDirectory(prefix="agentpack-eval-replay-") as temp_dir:
        replay_root = Path(temp_dir) / case.id
        added = subprocess.run(
            ["git", "worktree", "add", "--quiet", "--detach", str(replay_root), case.base_ref],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if added.returncode != 0:
            return EvalResult(
                case=case,
                passed=False,
                duration_s=time.perf_counter() - started,
                changed_files=[],
                changed_lines=0,
                checks=[CheckResult("replay", False, 0.0, exit_code=added.returncode, detail=added.stderr.strip())],
                variant=variant,
            )
        try:
            applied = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", str(patch_path)],
                cwd=replay_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if applied.returncode != 0:
                return EvalResult(
                    case=case,
                    passed=False,
                    duration_s=time.perf_counter() - started,
                    changed_files=[],
                    changed_lines=0,
                    checks=[CheckResult("replay", False, 0.0, exit_code=applied.returncode, detail=applied.stderr.strip())],
                    variant=variant,
                )
            result = _run_eval_case_in_root(replay_root, case, variant=variant)
            result.duration_s = time.perf_counter() - started
            return result
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(replay_root)],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )


def git_diff_summary(root: Path, base_ref: str = "HEAD") -> tuple[list[str], int, str]:
    try:
        names = subprocess.run(
            ["git", "diff", "--name-only", base_ref, "--"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        numstat = subprocess.run(
            ["git", "diff", "--numstat", base_ref, "--"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        return [], 0, f"git diff failed for base_ref {base_ref}: {stderr}"

    changed = [line.strip() for line in names.stdout.splitlines() if line.strip()]
    tracked_set = set(changed)
    for line in untracked.stdout.splitlines():
        path = line.strip()
        if path and path not in tracked_set:
            changed.append(path)
    changed_lines = 0
    for line in numstat.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        for value in parts[:2]:
            if value.isdigit():
                changed_lines += int(value)
    for path in changed:
        if path in tracked_set:
            continue
        file_path = root / path
        try:
            changed_lines += len(file_path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            pass
    return changed, changed_lines, ""


def git_diff_patch(root: Path, base_ref: str = "HEAD") -> tuple[str, str]:
    try:
        diff = subprocess.run(
            ["git", "diff", "--binary", base_ref, "--"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        return "", f"git diff failed for base_ref {base_ref}: {stderr}"

    chunks = [diff.stdout]
    for line in untracked.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        new_file_diff = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--", "/dev/null", rel],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if new_file_diff.stdout:
            chunks.append(new_file_diff.stdout)
    return "\n".join(chunk.rstrip() for chunk in chunks if chunk.strip()) + "\n", ""


def eval_watch_fingerprint(root: Path, cases: list[EvalCase], *, extra_paths: list[Path] | None = None) -> str:
    payload: list[dict[str, Any]] = []
    for case in cases:
        changed_files, changed_lines, diff_error = git_diff_summary(root, case.base_ref)
        patch, patch_error = git_diff_patch(root, case.base_ref)
        payload.append({
            "case_id": case.id,
            "base_ref": case.base_ref,
            "changed_files": changed_files,
            "changed_lines": changed_lines,
            "diff_error": diff_error,
            "patch_hash": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            "patch_error": patch_error,
        })
    for path in extra_paths or []:
        if path.exists() and path.is_file():
            payload.append({
                "path": str(path),
                "hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    data = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def persist_eval_results(root: Path, results: list[EvalResult]) -> Path:
    out = eval_results_path(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        for result in results:
            fh.write(json.dumps(eval_result_record(result)) + "\n")
            record_episode(
                root,
                task=result.case.task,
                selected_files=result.case.selected_files,
                changed_files=result.changed_files,
                checks=[
                    {
                        "name": check.name,
                        "passed": check.passed,
                        "exit_code": check.exit_code,
                        "flaky": check.flaky,
                    }
                    for check in result.checks
                ],
                passed=result.passed,
                failure_class=result.case.failure_class,
                failure_source=result.case.failure_source,
                context_hash=result.case.context_hash,
            )
    return out


def eval_result_record(result: EvalResult) -> dict[str, Any]:
    failed = result.failed_checks
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "case_id": result.case.id,
        "variant": result.variant,
        "task": result.case.task,
        "agent": result.case.agent,
        "agentpack_version": result.case.agentpack_version,
        "context_hash": result.case.context_hash,
        "selected_files": result.case.selected_files,
        "patch_file": result.case.patch_file,
        "patch_redaction_warnings": result.case.patch_redaction_warnings,
        "passed": result.passed,
        "failure_class": result.case.failure_class,
        "failure_source": result.case.failure_source,
        "failed_checks": [check.name for check in failed],
        "changed_files": result.changed_files,
        "changed_lines": result.changed_lines,
        "citation_coverage": result.citation_coverage,
        "invalid_citation_count": result.invalid_citation_count,
        "uncited_claim_count": result.uncited_claim_count,
        "duration_s": round(result.duration_s, 3),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "duration_s": round(check.duration_s, 3),
                "exit_code": check.exit_code,
                "detail": check.detail,
                "attempts": check.attempts,
                "flaky": check.flaky,
            }
            for check in result.checks
        ],
    }


def append_captured_eval_case(
    path: Path,
    *,
    root: Path,
    case_id: str,
    failure_class: str,
    checks: list[str],
    task: str = "",
    failure_source: str = "agent_failed",
    base_ref: str = "HEAD",
    agent: str = "",
    prompt_file: str = "",
    context_file: str = ".agentpack/context.md",
) -> EvalCase:
    _validate_failure_class(failure_class)
    _validate_failure_source(failure_source)
    changed_files, _changed_lines, diff_error = git_diff_summary(root, base_ref)
    if diff_error:
        changed_files = []
    patch_rel = f".agentpack/evals/{case_id}.patch"
    patch_path = root / patch_rel
    patch_text, patch_error = git_diff_patch(root, base_ref)
    if patch_error:
        raise ValueError(patch_error)
    patch_text, redaction_warnings = redact_secrets(patch_text, patch_rel)
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(patch_text, encoding="utf-8")
    metadata = _capture_metadata(root, agent=agent, prompt_file=prompt_file, context_file=context_file)
    case = EvalCase(
        id=case_id,
        task=task or case_id.replace("-", " "),
        failure_class=failure_class,
        failure_source=failure_source,
        base_ref=base_ref,
        patch_file=patch_rel,
        patch_redaction_warnings=redaction_warnings,
        required_changed_files=changed_files,
        checks=[
            EvalCheck(name=f"check-{i}", command=command)
            for i, command in enumerate(checks, 1)
            if command.strip()
        ],
        agent=metadata["agent"],
        prompt_file=metadata["prompt_file"],
        context_file=metadata["context_file"],
        context_hash=metadata["context_hash"],
        selected_files=metadata["selected_files"],
        agentpack_version=metadata["agentpack_version"],
        citation_manifest=metadata["citation_manifest"],
        min_citation_coverage=0.75 if metadata["citation_manifest"] else None,
        max_invalid_citations=0 if metadata["citation_manifest"] else None,
    )
    if path.exists():
        existing = load_eval_cases(path)
        if any(item.id == case.id for item in existing):
            raise ValueError(f"eval case already exists: {case.id}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        tomli_w.dump({"cases": [_case_to_toml(case)]}, fh)
    return case


def append_eval_cases_from_episodes(
    path: Path,
    *,
    root: Path,
    episodes_path: str = ".agentpack/episodic-cases.jsonl",
    limit: int = 100,
) -> int:
    existing = load_eval_cases(path) if path.exists() else []
    existing_ids = {case.id for case in existing}
    new_cases: list[EvalCase] = []
    for record in _read_episode_records(root / episodes_path, limit=limit):
        if record.get("passed") is not False:
            continue
        changed_files = [str(item) for item in record.get("changed_files") or [] if isinstance(item, str)]
        task = str(record.get("task") or "").strip()
        if not task or not changed_files:
            continue
        case_id = _episode_case_id(task, changed_files)
        if case_id in existing_ids:
            continue
        failure_class = str(record.get("failure_class") or "context")
        failure_source = str(record.get("failure_source") or "agent_failed")
        _validate_failure_class(failure_class)
        _validate_failure_source(failure_source)
        new_cases.append(EvalCase(
            id=case_id,
            task=task,
            failure_class=failure_class,
            failure_source=failure_source,
            required_changed_files=changed_files,
            selected_files=[str(item) for item in record.get("selected_files") or [] if isinstance(item, str)],
            context_hash=str(record.get("context_hash") or ""),
            citation_manifest=str(record.get("citation_manifest_path") or record.get("citation_manifest") or ""),
        ))
        existing_ids.add(case_id)
    if not new_cases:
        return 0
    data = {"cases": [_case_to_toml(case) for case in [*existing, *new_cases]]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return len(new_cases)


def _read_episode_records(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _episode_case_id(task: str, changed_files: list[str]) -> str:
    digest = hashlib.sha256(json.dumps([task, changed_files], sort_keys=True).encode("utf-8")).hexdigest()[:8]
    words = re.findall(r"[a-z0-9]+", task.lower())[:4]
    slug = "-".join(words) or "episode"
    return f"episode-{slug}-{digest}"


def _capture_metadata(root: Path, *, agent: str, prompt_file: str, context_file: str) -> dict[str, Any]:
    try:
        from agentpack import __version__
    except Exception:
        __version__ = ""

    context_path = root / context_file if context_file else root / ".agentpack" / "context.md"
    context_hash = hashlib.sha256(context_path.read_bytes()).hexdigest()[:16] if context_path.exists() else ""
    selected_files: list[str] = []
    citation_manifest = ""
    meta_path = root / ".agentpack" / "pack_metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            selected_meta = meta.get("selected_files_meta") or []
            if isinstance(selected_meta, list):
                selected_files = [
                    str(item.get("path"))
                    for item in selected_meta
                    if isinstance(item, dict) and item.get("path")
                ]
            if not agent:
                agent = str(meta.get("agent") or "")
            citation_manifest = str(meta.get("citation_manifest_path") or "")
        except (OSError, json.JSONDecodeError):
            selected_files = []
    return {
        "agent": agent,
        "prompt_file": prompt_file,
        "context_file": str(context_file) if context_path.exists() else "",
        "context_hash": context_hash,
        "selected_files": selected_files,
        "agentpack_version": __version__,
        "citation_manifest": citation_manifest,
    }


def load_eval_result_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


def write_eval_ci_template(root: Path) -> Path:
    out = root / ".github" / "workflows" / "agentpack-eval.yml"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "name: AgentPack Eval\n\n"
        "on:\n"
        "  pull_request:\n"
        "  workflow_dispatch:\n\n"
        "jobs:\n"
        "  eval:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v7\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: '3.11'\n"
        "      - name: Install AgentPack\n"
        "        run: pip install agentpack-cli\n"
        "      - name: Run deterministic evals\n"
        "        run: agentpack eval --cases benchmarks/evals.toml --replay --prove-targets\n",
        encoding="utf-8",
    )
    return out


def write_eval_report(root: Path, records: list[dict[str, Any]], *, date: str | None = None) -> Path:
    stamp = date or datetime.now(timezone.utc).date().isoformat()
    out = root / "benchmarks" / "results" / f"{stamp}-eval.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    total = len(records)
    passed = sum(1 for row in records if row.get("passed") is True)
    failed = total - passed
    by_class: dict[str, int] = {}
    for row in records:
        if row.get("passed") is False:
            cls = str(row.get("failure_class") or "unknown")
            by_class[cls] = by_class.get(cls, 0) + 1

    lines = [
        "# AgentPack Eval Results",
        "",
        f"- date: {stamp}",
        f"- cases: {total}",
        f"- passed: {passed}",
        f"- failed: {failed}",
        "",
        "| Case | Status | Failure class | Failed checks |",
        "|---|---|---|---|",
    ]
    for row in records:
        status = "pass" if row.get("passed") else "fail"
        failed_checks = ", ".join(row.get("failed_checks") or []) or "-"
        variant = str(row.get("variant") or "agentpack")
        lines.append(
            f"| {_md_cell(str(row.get('case_id', '')))} ({_md_cell(variant)}) "
            f"| {status} "
            f"| {_md_cell(str(row.get('failure_class', '')))} "
            f"| {_md_cell(failed_checks)} |"
        )
    if by_class:
        lines.extend(["", "## Failure Taxonomy", ""])
        for cls, count in sorted(by_class.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {cls}: {count}")
    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return out


def compare_eval_variants(records: list[dict[str, Any]], baseline: str, variant: str) -> dict[str, Any]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in records:
        case_id = row.get("case_id")
        row_variant = row.get("variant") or "agentpack"
        if isinstance(case_id, str) and isinstance(row_variant, str):
            latest[(case_id, row_variant)] = row

    case_ids = sorted({case_id for case_id, row_variant in latest if row_variant in {baseline, variant}})
    rows = []
    improved = regressed = unchanged = incomplete = 0
    for case_id in case_ids:
        base_row = latest.get((case_id, baseline))
        variant_row = latest.get((case_id, variant))
        if not base_row or not variant_row:
            status = "incomplete"
            incomplete += 1
        else:
            base_pass = bool(base_row.get("passed"))
            variant_pass = bool(variant_row.get("passed"))
            if not base_pass and variant_pass:
                status = "improved"
                improved += 1
            elif base_pass and not variant_pass:
                status = "regressed"
                regressed += 1
            else:
                status = "unchanged"
                unchanged += 1
        rows.append({
            "case_id": case_id,
            "baseline_passed": base_row.get("passed") if base_row else None,
            "variant_passed": variant_row.get("passed") if variant_row else None,
            "status": status,
        })

    return {
        "baseline": baseline,
        "variant": variant,
        "cases": len(rows),
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "incomplete": incomplete,
        "rows": rows,
    }


def _parse_case(raw: dict[str, Any]) -> EvalCase:
    case_id = str(raw.get("id", "")).strip()
    if not case_id:
        raise ValueError("eval case missing id")
    task = str(raw.get("task", "")).strip()
    if not task:
        raise ValueError(f"eval case {case_id} missing task")
    failure_class = str(raw.get("failure_class", "")).strip()
    _validate_failure_class(failure_class)
    failure_source = str(raw.get("failure_source", "agent_failed")).strip()
    _validate_failure_source(failure_source)
    checks = [_parse_check(item, case_id) for item in raw.get("checks", []) or []]
    golden_files = [_parse_golden_file(item, case_id) for item in raw.get("golden_files", []) or []]
    return EvalCase(
        id=case_id,
        task=task,
        failure_class=failure_class,
        failure_source=failure_source,
        base_ref=str(raw.get("base_ref", "HEAD")).strip() or "HEAD",
        patch_file=str(raw.get("patch_file", "")).strip(),
        patch_redaction_warnings=_str_list(raw.get("patch_redaction_warnings", []), "patch_redaction_warnings", case_id),
        required_changed_files=_str_list(raw.get("required_changed_files", []), "required_changed_files", case_id),
        forbidden_changed_files=_str_list(raw.get("forbidden_changed_files", []), "forbidden_changed_files", case_id),
        max_changed_files=_optional_int(raw.get("max_changed_files"), "max_changed_files", case_id),
        max_changed_lines=_optional_int(raw.get("max_changed_lines"), "max_changed_lines", case_id),
        checks=checks,
        golden_files=golden_files,
        agent=str(raw.get("agent", "")).strip(),
        prompt_file=str(raw.get("prompt_file", "")).strip(),
        context_file=str(raw.get("context_file", "")).strip(),
        context_hash=str(raw.get("context_hash", "")).strip(),
        selected_files=_str_list(raw.get("selected_files", []), "selected_files", case_id),
        agentpack_version=str(raw.get("agentpack_version", "")).strip(),
        citation_manifest=str(raw.get("citation_manifest", "")).strip(),
        min_citation_coverage=_optional_float(raw.get("min_citation_coverage"), "min_citation_coverage", case_id),
        max_invalid_citations=_optional_int(raw.get("max_invalid_citations"), "max_invalid_citations", case_id),
        require_review_citations=bool(raw.get("require_review_citations", False)),
    )


def _parse_check(raw: dict[str, Any], case_id: str) -> EvalCheck:
    name = str(raw.get("name", "")).strip()
    command = str(raw.get("command", "")).strip()
    if not name:
        raise ValueError(f"eval case {case_id} has check missing name")
    if not command:
        raise ValueError(f"eval case {case_id} has check {name} missing command")
    if not shlex.split(command):
        raise ValueError(f"eval case {case_id} has empty command for check {name}")
    timeout_s = int(raw.get("timeout_s", 120))
    if timeout_s <= 0:
        raise ValueError(f"eval case {case_id} check {name} timeout_s must be positive")
    retries = int(raw.get("retries", 0))
    if retries < 0:
        raise ValueError(f"eval case {case_id} check {name} retries must be 0 or greater")
    return EvalCheck(name=name, command=command, timeout_s=timeout_s, retries=retries)


def _parse_golden_file(raw: Any, case_id: str) -> GoldenFile:
    if isinstance(raw, str):
        return GoldenFile(actual=raw, expected=f"{raw}.golden")
    if not isinstance(raw, dict):
        raise ValueError(f"eval case {case_id} golden_files entries must be strings or tables")
    actual = str(raw.get("actual") or raw.get("path") or "").strip()
    expected = str(raw.get("expected") or raw.get("golden") or "").strip()
    if not actual or not expected:
        raise ValueError(f"eval case {case_id} golden file requires actual and expected")
    return GoldenFile(actual=actual, expected=expected, binary=bool(raw.get("binary", False)))


def _eval_citation_metrics(root: Path, case: EvalCase) -> dict[str, Any]:
    manifest_path = _resolve_citation_manifest(root, case)
    if manifest_path is None or not manifest_path.exists():
        return {"citation_coverage": 0.0, "invalid_citation_count": 0, "uncited_claim_count": 0}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"citation_coverage": 0.0, "invalid_citation_count": 1, "uncited_claim_count": 0}
    raw_citations = manifest.get("citations") or []
    citations: list[Citation] = []
    invalid_count = 0
    if isinstance(raw_citations, list):
        for raw in raw_citations:
            try:
                citations.append(Citation.model_validate(raw))
            except Exception:
                invalid_count += 1
    validation = validate_citations(root, citations)
    invalid_count += len(validation.invalid)
    uncited_claim_count = int(manifest.get("uncited_claim_count") or 0) if isinstance(manifest, dict) else 0
    return {
        "citation_coverage": round(validation.coverage, 3),
        "invalid_citation_count": invalid_count,
        "uncited_claim_count": uncited_claim_count,
    }


def _resolve_citation_manifest(root: Path, case: EvalCase) -> Path | None:
    if case.citation_manifest:
        return root / case.citation_manifest
    if case.context_file:
        context_path = root / case.context_file
        sibling = context_path.with_name("citations.json")
        if sibling.exists():
            return sibling
    default = root / ".agentpack" / "citations.json"
    return default if default.exists() else None


def _run_native_checks(root: Path, case: EvalCase, changed_files: list[str], changed_lines: int) -> list[CheckResult]:
    results: list[CheckResult] = []
    changed_set = set(changed_files)
    for required in case.required_changed_files:
        matched = _any_match(changed_files, required)
        results.append(CheckResult(
            name=f"required_changed_file:{required}",
            passed=matched,
            duration_s=0.0,
            detail="" if matched else f"required changed file missing: {required}",
        ))
    for forbidden in case.forbidden_changed_files:
        matches = [path for path in changed_files if _path_matches(path, forbidden)]
        results.append(CheckResult(
            name=f"forbidden_changed_file:{forbidden}",
            passed=not matches,
            duration_s=0.0,
            detail="" if not matches else "forbidden changed files: " + ", ".join(matches),
        ))
    if case.max_changed_files is not None:
        passed = len(changed_set) <= case.max_changed_files
        results.append(CheckResult(
            name="max_changed_files",
            passed=passed,
            duration_s=0.0,
            detail="" if passed else f"{len(changed_set)} changed files > {case.max_changed_files}",
        ))
    if case.max_changed_lines is not None:
        passed = changed_lines <= case.max_changed_lines
        results.append(CheckResult(
            name="max_changed_lines",
            passed=passed,
            duration_s=0.0,
            detail="" if passed else f"{changed_lines} changed lines > {case.max_changed_lines}",
        ))
    results.extend(_citation_check_results(root, case))
    return results


def _case_needs_citation_checks(case: EvalCase) -> bool:
    return (
        case.min_citation_coverage is not None
        or case.max_invalid_citations is not None
        or case.require_review_citations
    )


def _citation_check_results(root: Path, case: EvalCase) -> list[CheckResult]:
    results: list[CheckResult] = []
    citation_metrics = _eval_citation_metrics(root, case)
    if case.min_citation_coverage is not None:
        coverage = citation_metrics["citation_coverage"]
        passed = coverage >= case.min_citation_coverage
        results.append(CheckResult(
            name="min_citation_coverage",
            passed=passed,
            duration_s=0.0,
            detail="" if passed else f"{coverage:.3f} citation coverage < {case.min_citation_coverage:.3f}",
        ))
    if case.max_invalid_citations is not None:
        invalid_count = citation_metrics["invalid_citation_count"]
        passed = invalid_count <= case.max_invalid_citations
        results.append(CheckResult(
            name="max_invalid_citations",
            passed=passed,
            duration_s=0.0,
            detail="" if passed else f"{invalid_count} invalid citation(s) > {case.max_invalid_citations}",
        ))
    if case.require_review_citations:
        coverage = citation_metrics["citation_coverage"]
        invalid_count = citation_metrics["invalid_citation_count"]
        passed = coverage > 0 and invalid_count == 0
        results.append(CheckResult(
            name="require_review_citations",
            passed=passed,
            duration_s=0.0,
            detail="" if passed else "citation manifest missing, empty, or invalid",
        ))
    return results


def _run_command_check(root: Path, check: EvalCheck) -> CheckResult:
    attempts: list[CheckResult] = []
    for _attempt in range(check.retries + 1):
        result = _run_command_attempt(root, check)
        attempts.append(result)
        if result.passed:
            result.attempts = len(attempts)
            result.flaky = any(not item.passed for item in attempts[:-1])
            return result
    final = attempts[-1]
    final.attempts = len(attempts)
    final.flaky = any(item.passed for item in attempts[:-1])
    return final


def _run_command_attempt(root: Path, check: EvalCheck) -> CheckResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            shlex.split(check.command),
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=check.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CheckResult(
            name=check.name,
            passed=False,
            duration_s=time.perf_counter() - started,
            detail=f"timed out after {check.timeout_s}s",
            stdout_tail=_tail(exc.stdout or ""),
            stderr_tail=_tail(exc.stderr or ""),
        )
    except OSError as exc:
        return CheckResult(
            name=check.name,
            passed=False,
            duration_s=time.perf_counter() - started,
            detail=str(exc),
        )
    return CheckResult(
        name=check.name,
        passed=completed.returncode == 0,
        duration_s=time.perf_counter() - started,
        exit_code=completed.returncode,
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(completed.stderr),
    )


def _compare_golden_file(root: Path, golden: GoldenFile) -> CheckResult:
    started = time.perf_counter()
    actual = root / golden.actual
    expected = root / golden.expected
    if not actual.exists():
        return CheckResult(golden.actual, False, time.perf_counter() - started, detail="actual file missing")
    if not expected.exists():
        return CheckResult(golden.actual, False, time.perf_counter() - started, detail="expected file missing")
    if golden.binary:
        passed = actual.read_bytes() == expected.read_bytes()
    else:
        passed = actual.read_text(encoding="utf-8") == expected.read_text(encoding="utf-8")
    return CheckResult(
        name=f"golden_file:{golden.actual}",
        passed=passed,
        duration_s=time.perf_counter() - started,
        detail="" if passed else f"{golden.actual} differs from {golden.expected}",
    )


def _case_to_toml(case: EvalCase) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": case.id,
        "task": case.task,
        "failure_class": case.failure_class,
        "failure_source": case.failure_source,
        "base_ref": case.base_ref,
    }
    if case.patch_file:
        data["patch_file"] = case.patch_file
    if case.patch_redaction_warnings:
        data["patch_redaction_warnings"] = case.patch_redaction_warnings
    if case.required_changed_files:
        data["required_changed_files"] = case.required_changed_files
    if case.forbidden_changed_files:
        data["forbidden_changed_files"] = case.forbidden_changed_files
    if case.max_changed_files is not None:
        data["max_changed_files"] = case.max_changed_files
    if case.max_changed_lines is not None:
        data["max_changed_lines"] = case.max_changed_lines
    if case.checks:
        checks = []
        for check in case.checks:
            item: dict[str, Any] = {"name": check.name, "command": check.command, "timeout_s": check.timeout_s}
            if check.retries:
                item["retries"] = check.retries
            checks.append(item)
        data["checks"] = checks
    if case.agent:
        data["agent"] = case.agent
    if case.prompt_file:
        data["prompt_file"] = case.prompt_file
    if case.context_file:
        data["context_file"] = case.context_file
    if case.context_hash:
        data["context_hash"] = case.context_hash
    if case.selected_files:
        data["selected_files"] = case.selected_files
    if case.agentpack_version:
        data["agentpack_version"] = case.agentpack_version
    if case.citation_manifest:
        data["citation_manifest"] = case.citation_manifest
    if case.min_citation_coverage is not None:
        data["min_citation_coverage"] = case.min_citation_coverage
    if case.max_invalid_citations is not None:
        data["max_invalid_citations"] = case.max_invalid_citations
    if case.require_review_citations:
        data["require_review_citations"] = case.require_review_citations
    return data


def _validate_failure_class(value: str) -> None:
    if value not in FAILURE_CLASSES:
        raise ValueError(f"unknown failure_class: {value}. Use one of: {', '.join(FAILURE_CLASSES)}")


def _validate_failure_source(value: str) -> None:
    if value not in FAILURE_SOURCES:
        raise ValueError(f"unknown failure_source: {value}. Use one of: {', '.join(FAILURE_SOURCES)}")


def _str_list(value: Any, name: str, case_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"eval case {case_id} field {name} must be a list of strings")
    return value


def _optional_int(value: Any, name: str, case_id: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"eval case {case_id} field {name} must be a non-negative integer")
    return value


def _optional_float(value: Any, name: str, case_id: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"eval case {case_id} field {name} must be a non-negative number")
    return float(value)


def _any_match(paths: list[str], pattern: str) -> bool:
    return any(_path_matches(path, pattern) for path in paths)


def _path_matches(path: str, pattern: str) -> bool:
    return path == pattern or fnmatch.fnmatchcase(path, pattern)


def _tail(text: str, *, limit: int = 2000) -> str:
    text = text or ""
    return text[-limit:]


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()
