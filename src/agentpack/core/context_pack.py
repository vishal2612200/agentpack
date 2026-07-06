from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agentpack.core.models import (
    Citation,
    FileInfo,
    OmittedRelevantFile,
    Receipt,
    SelectedFile,
    Symbol,
)
from agentpack.core.citations import file_citation, selected_file_citations
from agentpack.core.redactor import redact_secrets
from agentpack.core.task_freshness import task_hash
from agentpack.core.token_estimator import estimate_tokens
from agentpack.core.modes import PackMode


Mode = PackMode

_MODE_WEIGHTS: dict[str, dict[str, bool]] = {
    "lite": {
        "include_unchanged_deps": False,
        "include_rev_deps": False,
        "include_tests": False,
        "include_docs": False,
        "extra_full": False,
    },
    "balanced": {
        "include_unchanged_deps": True,
        "include_rev_deps": True,
        "include_tests": True,
        "include_docs": False,
        "extra_full": False,
    },
    "deep": {
        "include_unchanged_deps": True,
        "include_rev_deps": True,
        "include_tests": True,
        "include_docs": True,
        "extra_full": True,
    },
}


def _metadata_path(root: Path) -> Path:
    return root / ".agentpack" / "pack_metadata.json"


def save_pack_metadata(
    root: Path,
    context_path: str,
    snapshot_root_hash: str,
    task: str,
    agent: str,
    mode: str,
    budget: int,
    requested_mode: str | None = None,
    token_estimate: int = 0,
    freshness: dict[str, Any] | None = None,
    freshness_warnings: list[str] | None = None,
    selected_files: list[dict[str, Any]] | None = None,
    pack_handoff: dict[str, Any] | None = None,
    execution_state: dict[str, Any] | None = None,
    concurrent_context: dict[str, Any] | None = None,
    citation_manifest_path: str = "",
    citation_summary: dict[str, Any] | None = None,
    token_contract: dict[str, Any] | None = None,
    metadata_path: Path | None = None,
) -> None:
    generated_at = (
        freshness.get("generated_at")
        if freshness and freshness.get("generated_at")
        else datetime.now(timezone.utc).isoformat()
    )
    meta = {
        "context_path": context_path,
        "generated_at": generated_at,
        "snapshot_root_hash": snapshot_root_hash,
        "task": task,
        "task_hash": task_hash(task),
        "agent": agent,
        "mode": mode,
        "requested_mode": requested_mode or mode,
        "budget": budget,
        "token_estimate": token_estimate,
        "selected_files_meta": selected_files or [],
        "pack_handoff": pack_handoff or {},
        "freshness": freshness or {},
        "freshness_warnings": freshness_warnings or [],
        "execution_state": execution_state or {},
        "concurrent_context": concurrent_context or {},
        "citation_manifest_path": citation_manifest_path,
        "citation_summary": citation_summary or {},
        "token_contract": token_contract or {},
    }
    if freshness:
        for key in (
            "agentpack_version",
            "source_command",
            "cwd",
            "git_root",
            "worktree_path",
            "git_sha",
            "git_branch",
            "task_source",
            "changed_files_source",
            "task_class",
            "context_intent",
            "broad_context",
            "owner_thread_id",
            "thread_id",
            "task_status",
            "citation_manifest_path",
        ):
            if key in freshness:
                meta[key] = freshness[key]
    path = metadata_path or _metadata_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2))


def load_pack_metadata(root: Path, metadata_path: Path | None = None) -> dict[str, Any] | None:
    path = metadata_path or _metadata_path(root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _extract_relevant_symbol_bodies(
    fi: FileInfo,
    syms: list[Symbol],
    keywords: set[str],
    budget_remaining: int,
) -> tuple[str | None, int]:
    """Assemble symbol bodies from Symbol.body (captured at extraction time — no file re-read)."""
    from agentpack.analysis.symbols import filter_symbols_by_keywords

    relevant = filter_symbols_by_keywords(syms, keywords) if keywords else syms[:5]
    if not relevant:
        return None, 0

    parts: list[str] = []
    tokens_used = 0
    for sym in relevant:
        body = sym.body
        if body:
            tok = estimate_tokens(body)
            if tokens_used + tok <= budget_remaining:
                parts.append(body)
                tokens_used += tok
            elif sym.signature:
                sig_tok = estimate_tokens(sym.signature)
                if tokens_used + sig_tok <= budget_remaining:
                    parts.append(sym.signature)
                    tokens_used += sig_tok
        elif sym.signature:
            sig_tok = estimate_tokens(sym.signature)
            if tokens_used + sig_tok <= budget_remaining:
                parts.append(sym.signature)
                tokens_used += sig_tok

    return "\n\n".join(parts) if parts else None, tokens_used


def _has_task_signal(reasons: list[str]) -> bool:
    """Return True when a file matched the task beyond generic dirtiness."""
    weak_prefixes = (
        "modified",
        "staged",
        "recently modified",
        "high churn",
        "likely false positive",
    )
    return any(not reason.startswith(weak_prefixes) for reason in reasons)


def _git_diff_for_file(fi: FileInfo, max_tokens: int, keywords: set[str] | None = None) -> tuple[str | None, int]:
    """Return a compact git diff for one file when available."""
    root = _find_git_root(fi.abs_path)
    if root is None:
        return None, 0
    rel = fi.path
    pieces: list[str] = []
    for args in (["git", "diff", "--", rel], ["git", "diff", "--cached", "--", rel]):
        try:
            result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip():
            pieces.append(result.stdout.strip())
    diff_text = "\n".join(pieces).strip()
    if not diff_text:
        return None, 0
    content = _select_diff_hunks(diff_text, max_tokens=max_tokens, keywords=keywords or set())
    return content, estimate_tokens(content)


def _select_diff_hunks(diff_text: str, max_tokens: int, keywords: set[str]) -> str:
    """Keep diff headers and the most task-relevant hunks under budget."""
    lines = diff_text.splitlines()
    if not lines:
        return ""
    header: list[str] = []
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("@@"):
            current = [line]
            hunks.append(current)
        elif current is None:
            header.append(line)
        else:
            current.append(line)

    if not hunks:
        return _truncate_lines(lines, max_tokens)

    keyword_lc = {kw.lower() for kw in keywords if len(kw) >= 3}

    def hunk_score(item: tuple[int, list[str]]) -> tuple[int, int]:
        index, hunk = item
        text = "\n".join(hunk).lower()
        hits = sum(1 for kw in keyword_lc if kw in text)
        changed_lines = sum(1 for line in hunk if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
        return hits * 100 + changed_lines, -index

    ordered = sorted(enumerate(hunks), key=hunk_score, reverse=True)
    kept_hunks: list[tuple[int, list[str]]] = []
    kept_text = "\n".join(header)
    for index, hunk in ordered:
        candidate_parts = [kept_text] if kept_text else []
        candidate_parts.extend("\n".join(existing) for _i, existing in kept_hunks)
        candidate_parts.append("\n".join(hunk))
        candidate = "\n".join(part for part in candidate_parts if part)
        if estimate_tokens(candidate) <= max_tokens:
            kept_hunks.append((index, hunk))
            kept_text = "\n".join(header)
        elif not kept_hunks:
            truncated = _truncate_lines([*header, *hunk], max_tokens)
            return truncated

    omitted = len(hunks) - len(kept_hunks)
    kept_hunks.sort(key=lambda item: item[0])
    output: list[str] = [*header]
    if omitted > 0:
        output.append(f"... {omitted} less relevant diff hunk(s) omitted by AgentPack ...")
    for _index, hunk in kept_hunks:
        output.extend(hunk)
    return "\n".join(output)


def _truncate_lines(lines: list[str], max_tokens: int) -> str:
    kept: list[str] = []
    tokens = 0
    for line in lines:
        line_tokens = estimate_tokens(line)
        if kept and tokens + line_tokens > max_tokens:
            kept.append("... diff truncated by AgentPack budget ...")
            break
        kept.append(line)
        tokens += line_tokens
    return "\n".join(kept)


_SKELETON_TOKEN_CAP = 180


def _truncate_skeleton_lines(lines: list[str], max_tokens: int = _SKELETON_TOKEN_CAP) -> str:
    kept: list[str] = []
    tokens = 0
    for line in lines:
        line_tokens = estimate_tokens(line)
        if kept and tokens + line_tokens > max_tokens:
            kept.append("... skeleton truncated by AgentPack budget ...")
            break
        kept.append(line)
        tokens += line_tokens
    return "\n".join(kept)


def _find_git_root(path: Path) -> Path | None:
    cur = path if path.is_dir() else path.parent
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _skeleton_content(fi: FileInfo, summary_data: dict[str, Any] | None) -> tuple[str | None, int]:
    """Build a compact interface view from imports and signatures."""
    if not summary_data:
        return None, 0
    lines: list[str] = []
    for label, field, limit in [
        ("# entrypoints", "entrypoints", 12),
        ("# external systems", "external_systems", 8),
    ]:
        values = summary_data.get(field) or []
        if values:
            if lines:
                lines.append("")
            lines.append(label)
            lines.extend(f"- {item}" for item in values[:limit])
    imports = summary_data.get("imports") or []
    if imports:
        if lines:
            lines.append("")
        lines.append("# imports")
        lines.extend(f"- {item}" for item in imports[:20])
    raw_symbols = summary_data.get("symbols") or []
    symbol_lines: list[str] = []
    for raw in raw_symbols[:40]:
        try:
            sym = Symbol(**raw) if isinstance(raw, dict) else raw
        except Exception:
            continue
        if sym.signature:
            symbol_lines.append(sym.signature)
        else:
            symbol_lines.append(f"{sym.kind} {sym.name}")
    if symbol_lines:
        if lines:
            lines.append("")
        lines.append("# interface")
        lines.extend(symbol_lines)
    if not lines and summary_data.get("summary"):
        lines.append(str(summary_data["summary"]))
    content = _truncate_skeleton_lines(lines).strip()
    return (content, estimate_tokens(content)) if content else (None, 0)


def _summary_tokens(summary_data: dict[str, Any] | None, fallback: int) -> int:
    if not summary_data:
        return fallback
    summary = str(summary_data.get("summary") or "").strip()
    return estimate_tokens(summary) if summary else fallback


def _is_test_path(path: str) -> bool:
    path_lc = path.lower()
    name = Path(path_lc).name
    parts = {part.lower() for part in Path(path_lc).parts}
    return (
        path_lc.startswith(("tests/", "test/"))
        or "test" in parts
        or "/tests/" in path_lc
        or "__tests__/" in path_lc
        or "__test__/" in path_lc
        or name.startswith("test_")
        or name.endswith(("_test.go", "_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )


def _is_docs_path(path: str) -> bool:
    path_lc = path.lower()
    return path_lc.startswith(("docs/", "doc/")) or path_lc.endswith((".md", ".mdx", ".rst"))


def _is_example_or_playground_path(path: str) -> bool:
    path_parts = [part.lower() for part in Path(path).parts]
    parts = set(path_parts)
    sample_dir = bool(path_parts and path_parts[0] in {"sample", "samples"})
    return bool(
        parts
        & {
            "example",
            "examples",
            "fixture",
            "fixtures",
            "playground",
            "playgrounds",
            "template",
            "templates",
        }
    ) or sample_dir or any(part.startswith(("template-", "sample-")) for part in path_parts)


def _has_direct_source_evidence(reasons: list[str]) -> bool:
    return any(
        reason == "symbol keyword match"
        or reason.startswith((
            "matched domain:",
            "matched define:",
            "multi-token defines match",
            "matched call:",
            "matched entrypoint:",
            "keyword phrase match:",
        ))
        for reason in reasons
    )


def _content_keyword_hits(reasons: list[str]) -> int:
    hits = 0
    for reason in reasons:
        match = re.match(r"content keyword match \((\d+)\)", reason)
        if match:
            hits = max(hits, int(match.group(1)))
    return hits


_IGNORE_CONTROL_NAMES = {
    ".agentignore",
    ".cursorignore",
    ".dockerignore",
    ".eslintignore",
    ".gitignore",
    ".npmignore",
    ".prettierignore",
}

_IGNORE_TASK_TERMS = {
    "agentignore",
    "dockerignore",
    "eslintignore",
    "exclude",
    "excluded",
    "exclusion",
    "gitignore",
    "ignore",
    "ignored",
    "ignores",
    "ignoring",
    "npmignore",
    "prettierignore",
}

_RUNTIME_INFRA_TERMS = {
    "alb",
    "aws",
    "cfn",
    "cloud",
    "cloudformation",
    "cloudwatch",
    "copilot",
    "deploy",
    "deployment",
    "ecs",
    "iam",
    "infra",
    "lambda",
    "package",
    "rendered",
    "runbook",
    "otp",
    "secret",
    "security",
    "serverless",
    "ssm",
    "waf",
    "workflow",
}

_DEPLOY_CONFIG_FILENAMES = {
    "buildspec.yml",
    "buildspec.yaml",
    "cfn.patches.yml",
    "cfn.patches.yaml",
    "cloudformation.yml",
    "cloudformation.yaml",
    "containerfile",
    "dockerfile",
    "serverless.yml",
    "serverless.yaml",
}

_DEPLOY_CONFIG_PARTS = {
    ".github/workflows",
    "addons",
    "cfn",
    "cloudformation",
    "copilot",
    "deploy",
    "deployment",
    "github/workflows",
    "infra",
    "k8s",
    "kubernetes",
    "overrides",
    "pipeline",
    "pipelines",
    "rendered",
    "serverless",
}


def _is_ignore_control_path(path: str) -> bool:
    return Path(path).name.lower() in _IGNORE_CONTROL_NAMES


def _has_ignore_task_evidence(reasons: list[str], keywords: set[str]) -> bool:
    if keywords & _IGNORE_TASK_TERMS:
        return True
    return any("ignore" in reason.lower() for reason in reasons)


def _has_actionable_compressed_evidence(reasons: list[str]) -> bool:
    content_hits = _content_keyword_hits(reasons)
    if any(
        reason.startswith((
            "caller of selected symbol",
            "direct content evidence",
            "direct dependency",
            "historically co-changed",
            "keyword phrase match:",
            "literal definition match:",
            "matched call:",
            "matched define:",
            "matched entrypoint:",
            "matched env read:",
            "matched external system:",
            "matched side effect:",
            "multi-token defines match",
            "quoted literal match:",
            "release/version metadata",
            "reverse dependency",
            "test for",
            "workspace match",
        ))
        or reason == "build/dependency metadata"
        or reason == "has related tests"
        for reason in reasons
    ):
        return True
    if "symbol keyword match" in reasons and content_hits >= 1:
        return True
    if any(reason.startswith("multi-term path match") for reason in reasons) and content_hits >= 1:
        return True
    return content_hits >= 4 and any(
        reason.startswith(("matched naming keyword:", "matched ranking keyword:", "matched role keyword:"))
        for reason in reasons
    )


def _weak_compressed_noise_reason(path: str, reasons: list[str], keywords: set[str]) -> str | None:
    if _is_ignore_control_path(path) and not _has_ignore_task_evidence(reasons, keywords):
        return "ignore-control file lacks ignore-task evidence"

    cleanup_refactor_evidence = _is_cleanup_refactor_task(keywords) and _has_cleanup_refactor_candidate_evidence(path, reasons)
    actionable = _has_actionable_compressed_evidence(reasons)
    if _is_test_path(path) and "explicit test task file" not in reasons and not actionable and not cleanup_refactor_evidence:
        if (
            "symbol keyword match" in reasons
            and "filename keyword match" in reasons
            and any(reason.startswith(("matched role keyword:", "matched ranking keyword:")) for reason in reasons)
        ):
            return None
        return "test file lacks direct task evidence"

    path_terms = {part.lower() for part in Path(path).parts}
    if (
        not actionable
        and ("api" in path_terms or Path(path).name.lower() in {"api.ts", "api.js", "api.py"})
        and "filename keyword match" in reasons
        and _content_keyword_hits(reasons) >= 1
        and any(reason.startswith("matched domain: api") for reason in reasons)
    ):
        return None

    broad_only = _is_source_path(path) and "implementation role match" in reasons and any(
        reason.startswith((
            "matched domain:",
            "matched naming keyword:",
            "matched ranking keyword:",
            "matched role keyword:",
        ))
        for reason in reasons
    )
    if broad_only and not actionable and not cleanup_refactor_evidence:
        return "broad family match lacks direct task evidence"

    return None


def _is_source_path(path: str) -> bool:
    if _is_test_path(path) or _is_docs_path(path) or _is_example_or_playground_path(path):
        return False
    if _is_lock_or_generated_path(path):
        return False
    suffix = Path(path).suffix.lower()
    if suffix not in {".go", ".rs", ".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx"}:
        return False
    parts = {part.lower() for part in Path(path).parts}
    return (
        len(Path(path).parts) == 1
        or "src" in parts
        or "source" in parts
        or path.lower().startswith(("src/", "lib/", "pkg/", "cmd/", "packages/"))
    )


def _is_direct_source_candidate(path: str, reasons: list[str]) -> bool:
    if not _is_source_path(path):
        return False
    if "config file" in reasons:
        return False
    if not _has_direct_source_evidence(reasons):
        return False
    return True


def _is_root_go_source_candidate(path: str, reasons: list[str]) -> bool:
    return (
        len(Path(path).parts) == 1
        and Path(path).suffix.lower() == ".go"
        and _is_direct_source_candidate(path, reasons)
    )


def _is_balance_config_candidate(path: str, reasons: list[str]) -> bool:
    if "config file" not in reasons or _is_lock_or_generated_path(path):
        return False
    return _content_keyword_hits(reasons) >= 2 or any(
        reason.startswith((
            "keyword phrase match:",
            "matched define:",
            "matched env read:",
            "multi-term path match",
        ))
        for reason in reasons
    )


def _is_runtime_infra_candidate(path: str, reasons: list[str]) -> bool:
    haystack = f"{path} {' '.join(reasons)}".lower()
    has_runtime_term = any(term in haystack for term in _RUNTIME_INFRA_TERMS if term != "package")
    has_package_file_signal = Path(path).name.lower() == "package.json" or any(
        re.search(r"\bpackage\b", reason.lower())
        for reason in reasons
    )
    if not has_runtime_term and not has_package_file_signal:
        return False
    if "config file" in reasons or _is_deploy_config_path(path):
        return True
    return any(
        term in haystack
        for term in (
            ".github/workflows",
            "copilot",
            "cloudformation",
            "cfn",
            "iam",
            "waf",
            "manifest",
            "override",
            "security",
        )
    )


def _is_balance_source_candidate(path: str, reasons: list[str]) -> bool:
    if not _is_source_path(path) or "config file" in reasons:
        return False
    return _has_direct_source_evidence(reasons) or _has_actionable_compressed_evidence(reasons)


def _balance_scope(path: str) -> str:
    scoped = _package_root(path) or _playground_root(path)
    if scoped:
        return scoped
    parts = [part for part in Path(path).parts if part]
    if len(parts) >= 2 and parts[0] in {"src", "lib", "app", "apps"}:
        return "/".join(parts[:2])
    return ""


def _selection_priority(
    item: tuple[FileInfo, float, list[str]],
    changed_paths: set[str],
    max_file_tokens: int,
    summaries: dict[str, Any] | None = None,
) -> tuple[int, int, int, float, float]:
    """Hybrid rank: changed/task-relevant first, then score with a token-value nudge."""
    fi, score, reasons = item
    changed_priority = 1 if fi.path in changed_paths else 0
    signal_priority = 1 if _has_task_signal(reasons) else 0
    infra_priority = 1 if _is_runtime_infra_candidate(fi.path, reasons) else 0
    role_bonus = 0.0
    explicit_test_task = any(reason == "explicit test task file" for reason in reasons)
    if _is_primary_release_metadata(fi.path, reasons):
        role_bonus += 180.0
    elif explicit_test_task:
        role_bonus += 45.0
    elif _is_runtime_infra_candidate(fi.path, reasons):
        role_bonus += 170.0
    elif _is_root_go_source_candidate(fi.path, reasons):
        role_bonus += 230.0
    elif _is_direct_source_candidate(fi.path, reasons):
        role_bonus += 125.0
    elif any(
        ("test for high-scoring" in reason and "docs/" not in reason and "examples/" not in reason)
        or "related test" in reason
        for reason in reasons
    ):
        role_bonus += 30.0
    if any("config file" in reason for reason in reasons):
        role_bonus += 25.0
    rough_tokens = max(1, min(fi.estimated_tokens, max_file_tokens))
    value_bonus = min(60.0, (score / rough_tokens) * 120.0)
    return infra_priority, changed_priority, signal_priority, score + role_bonus + value_bonus, score


_RESERVED_BUCKET_ORDER = ("changed", "tests", "docs", "deps")

_CALL_TARGET_STOPWORDS = {
    "build",
    "check",
    "close",
    "create",
    "delete",
    "find",
    "get",
    "init",
    "list",
    "load",
    "main",
    "make",
    "open",
    "parse",
    "read",
    "render",
    "run",
    "save",
    "set",
    "test",
    "update",
    "write",
}


def classify_omission_risk(path: str, reasons: list[str], score: float) -> Literal["high", "medium", "low"]:
    path_lc = path.lower()
    reason_text = " ".join(reasons).lower()
    if (
        "reverse dependency" in reason_text
        or "caller" in reason_text
        or "related test" in reason_text
        or "test for" in reason_text
        or path_lc.startswith(("tests/", "test/"))
        or "/tests/" in path_lc
        or any(part in path_lc for part in ("route", "routes", "controller", "api/"))
        or any(part in path_lc for part in ("schema", "migration", "model", "models"))
    ):
        return "high"
    if (
        "direct dependency" in reason_text
        or "config" in reason_text
        or any(part in path_lc for part in ("config", ".env", "deploy", "settings"))
        or score > 150
    ):
        return "medium"
    return "low"


def enrich_call_site_scores(
    scored: list[tuple[FileInfo, float, list[str]]],
    selected: list[SelectedFile],
    summaries: dict[str, Any],
    changed_paths: set[str],
    *,
    boost: float = 90.0,
) -> tuple[list[tuple[FileInfo, float, list[str]]], int]:
    """Boost files whose cached calls reference symbols from selected source files."""
    targets = _selected_call_targets(selected, summaries, changed_paths)
    if not targets:
        return scored, 0

    selected_paths = {sf.path for sf in selected}
    expanded: list[tuple[FileInfo, float, list[str]]] = []
    changed_count = 0
    for fi, score, reasons in scored:
        if fi.path in selected_paths:
            expanded.append((fi, score, reasons))
            continue
        matched = _matched_called_symbols(summaries.get(fi.path), targets)
        if not matched:
            expanded.append((fi, score, reasons))
            continue
        caller_reasons = [f"caller of selected symbol `{name}`" for name in matched[:3]]
        new_reasons = [*reasons, *[reason for reason in caller_reasons if reason not in reasons]]
        expanded.append((fi, score + min(boost * len(matched), boost * 2), new_reasons))
        changed_count += 1

    return expanded, changed_count


def _selected_call_targets(
    selected: list[SelectedFile],
    summaries: dict[str, Any],
    changed_paths: set[str],
    *,
    limit: int = 40,
) -> dict[str, str]:
    targets: dict[str, str] = {}
    for sf in selected:
        if sf.include_mode not in ("full", "diff", "symbols") and sf.path not in changed_paths:
            continue
        summary_data = summaries.get(sf.path) or {}
        for raw_name in _summary_symbol_names(summary_data):
            base = _call_symbol_base(raw_name)
            if not base or base in targets:
                continue
            targets[base] = raw_name
            if len(targets) >= limit:
                return targets
    return targets


def _summary_symbol_names(summary_data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for raw in summary_data.get("symbols") or []:
        name = raw.get("name") if isinstance(raw, dict) else getattr(raw, "name", None)
        if isinstance(name, str):
            names.append(name)
    for name in summary_data.get("defines") or []:
        if isinstance(name, str):
            names.append(name)
    return names


def _call_symbol_base(name: str) -> str | None:
    base = name.rsplit(".", 1)[-1].strip()
    if not base:
        return None
    base_lc = base.lower()
    if len(base_lc) < 4 or base_lc in _CALL_TARGET_STOPWORDS:
        return None
    if not re.search(r"[a-zA-Z]", base_lc):
        return None
    return base_lc


def _matched_called_symbols(summary_data: dict[str, Any] | None, targets: dict[str, str]) -> list[str]:
    if not summary_data:
        return []
    matched: list[str] = []
    for raw_call in summary_data.get("calls") or []:
        if not isinstance(raw_call, str):
            continue
        call_lc = raw_call.lower()
        call_base = _call_symbol_base(raw_call)
        for target_base, display_name in targets.items():
            if call_base == target_base or call_lc.endswith(f".{target_base}"):
                if display_name not in matched:
                    matched.append(display_name)
    return matched


def _selection_bucket(fi: FileInfo, reasons: list[str], changed_paths: set[str]) -> str:
    path = fi.path
    if path in changed_paths:
        return "changed"
    if _is_docs_path(path) or any(
        "knowledge/architecture doc" in reason for reason in reasons
    ):
        return "docs"
    if _is_test_path(path) or any(
        "test for" in reason or "related test" in reason for reason in reasons
    ):
        return "tests"
    if any(
        reason.startswith(("direct dependency", "reverse dependency", "cross-layer related"))
        or reason == "has related tests"
        for reason in reasons
    ):
        return "deps"
    return "other"


def _reserve_bucket_order(
    ordered: list[tuple[FileInfo, float, list[str]]],
    changed_paths: set[str],
    budget: int,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Seed top changed/test/doc/dependency files before normal rank order."""
    if not changed_paths or budget < 1000 or len(ordered) < 4:
        return ordered

    selected_indexes: set[int] = set()
    seeded: list[tuple[FileInfo, float, list[str]]] = []
    for bucket in _RESERVED_BUCKET_ORDER:
        for index, item in enumerate(ordered):
            if index in selected_indexes:
                continue
            fi, _score, reasons = item
            if _selection_bucket(fi, reasons, changed_paths) == bucket:
                seeded.append(item)
                selected_indexes.add(index)
                break

    if not seeded:
        return ordered
    seeded.extend(item for index, item in enumerate(ordered) if index not in selected_indexes)
    return seeded


def _config_source_balanced_order(
    ordered: list[tuple[FileInfo, float, list[str]]],
    max_summary_files: int,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Seed one strong config and one strong source before duplicate same-family picks."""
    if max_summary_files < 2 or len(ordered) < 3:
        return ordered
    first_window = ordered[:max_summary_files]
    has_config = any(_is_balance_config_candidate(fi.path, reasons) for fi, _score, reasons in first_window)
    has_source = any(_is_balance_source_candidate(fi.path, reasons) for fi, _score, reasons in first_window)
    if has_config and has_source:
        return ordered

    config_index: int | None = None
    source_index: int | None = None
    config_scope = ""
    source_scope = ""
    for index, (fi, _score, reasons) in enumerate(ordered[:30]):
        if config_index is None and _is_balance_config_candidate(fi.path, reasons):
            config_index = index
            config_scope = _balance_scope(fi.path)
        if source_index is None and _is_balance_source_candidate(fi.path, reasons):
            source_index = index
            source_scope = _balance_scope(fi.path)
        if config_index is not None and source_index is not None:
            break
    if config_index is None or source_index is None:
        return ordered
    if config_scope and source_scope and config_scope != source_scope:
        return ordered

    seed_indexes: list[int] = []
    if not has_config:
        seed_indexes.append(config_index)
    if not has_source:
        seed_indexes.append(source_index)
    if not seed_indexes:
        return ordered
    seed_indexes = sorted(set(seed_indexes))
    seeded = [ordered[index] for index in seed_indexes]
    seeded.extend(item for index, item in enumerate(ordered) if index not in seed_indexes)
    return seeded


_ContextRole = Literal["manifest", "config", "source_owner"]


def _context_shape_order(
    ordered: list[tuple[FileInfo, float, list[str]]],
    max_summary_files: int,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Seed generic context roles before duplicate same-role candidates."""
    if max_summary_files < 2 or len(ordered) < 3:
        return ordered
    window_size = min(max_summary_files, 4)
    first_window = ordered[:window_size]
    present_roles = {
        role
        for fi, score, reasons in first_window
        if (role := _context_role(fi.path, score, reasons)) is not None
    }
    scope = _context_shape_scope(first_window)
    if not scope:
        return ordered

    seed_indexes: list[int] = []
    for role in ("manifest", "config", "source_owner"):
        if role in present_roles:
            continue
        candidate_index = _best_context_role_index(
            ordered,
            role=role,
            scope=scope,
            used=set(seed_indexes),
        )
        if candidate_index is not None:
            seed_indexes.append(candidate_index)

    if not seed_indexes:
        return ordered
    protected = set(range(window_size))
    seed_indexes = [index for index in sorted(set(seed_indexes)) if index not in protected]
    if not seed_indexes:
        return ordered
    seeded = [ordered[index] for index in seed_indexes]
    seeded.extend(item for index, item in enumerate(ordered) if index not in seed_indexes)
    return seeded


def _best_context_role_index(
    ordered: list[tuple[FileInfo, float, list[str]]],
    *,
    role: _ContextRole,
    scope: str,
    used: set[int],
) -> int | None:
    best_index: int | None = None
    best_value = 0.0
    for index, (fi, score, reasons) in enumerate(ordered[:40]):
        if index in used:
            continue
        if _context_role(fi.path, score, reasons) != role:
            continue
        if scope and _balance_scope(fi.path) and _balance_scope(fi.path) != scope:
            continue
        value = _context_role_value(fi.path, score, reasons)
        if value > best_value:
            best_index = index
            best_value = value
    return best_index


def _context_shape_scope(items: list[tuple[FileInfo, float, list[str]]]) -> str:
    counts: dict[str, int] = {}
    for fi, _score, _reasons in items:
        scope = _balance_scope(fi.path)
        if scope:
            counts[scope] = counts.get(scope, 0) + 1
    if not counts:
        return ""
    return max(sorted(counts), key=lambda item: counts[item])


def _context_role(path: str, score: float, reasons: list[str]) -> _ContextRole | None:
    if score <= 0 or _is_weak_signal_candidate(reasons):
        return None
    if _has_release_metadata_reason(reasons) or any(reason == "build/dependency metadata" for reason in reasons):
        return "manifest"
    if _is_balance_config_candidate(path, reasons):
        return "config"
    if _is_source_path(path) and _context_role_value(path, score, reasons) >= 130:
        return "source_owner"
    return None


def _context_role_value(path: str, score: float, reasons: list[str]) -> float:
    value = min(score, 300.0) * 0.25
    content_hits = _content_keyword_hits(reasons)
    value += min(content_hits, 6) * 15.0
    if _has_direct_source_evidence(reasons):
        value += 90.0
    if _has_actionable_compressed_evidence(reasons):
        value += 65.0
    if _has_strict_summary_support(reasons, path):
        value += 45.0
    if _is_docs_path(path) or _is_example_or_playground_path(path):
        value -= 35.0
    return value


_WEAK_SIGNAL_REASONS = {
    "broad-task weak-signal dampening",
    "no-live filename-only dampening",
    "broad-task meta-summary dampening",
}

_STRICT_SUMMARY_SUPPORT_PREFIXES = (
    "direct dependency of changed file",
    "reverse dependency",
    "historically co-changed",
    "has related tests",
    "test for",
    "release/version metadata",
    "build/dependency metadata",
    "secret redaction candidate",
    "matched domain:",
    "matched external system:",
    "matched env read:",
    "matched side effect:",
    "matched call:",
)

_EXPANSION_ONLY_SUPPORT_PREFIXES = (
    "recall neighbor",
    "workspace match",
    "large supported file",
    "second-pass recall neighbor",
)


def _is_weak_signal_candidate(reasons: list[str]) -> bool:
    return any(reason in _WEAK_SIGNAL_REASONS for reason in reasons)


def _has_strict_summary_support(reasons: list[str], path: str = "") -> bool:
    if any(reason.startswith(_STRICT_SUMMARY_SUPPORT_PREFIXES) for reason in reasons):
        return True
    has_phrase = any(reason.startswith("keyword phrase match:") for reason in reasons)
    has_config = "config file" in reasons
    has_impl_or_define = any(
        reason == "implementation role match"
        or reason.startswith(("matched define:", "multi-token defines match", "matched entrypoint:"))
        for reason in reasons
    )
    content_hits = _content_keyword_hits(reasons)
    path_obj = Path(path) if path else None
    has_root_go_source_path = (
        path_obj is not None
        and len(path_obj.parts) == 1
        and path_obj.suffix.lower() == ".go"
        and _is_source_path(path)
        and "config file" not in reasons
    )
    if has_root_go_source_path:
        has_scope_source_evidence = (
            "conventional scope path match" in reasons
            and any(
                reason == "filename keyword match" or reason.startswith("matched role keyword:")
                for reason in reasons
            )
        )
        has_content_source_evidence = content_hits >= 2 and (
            "implementation role match" in reasons
            or any(
                reason.startswith((
                    "direct content evidence",
                    "matched call:",
                    "keyword phrase match:",
                    "quoted literal match:",
                ))
                for reason in reasons
            )
        )
        if content_hits >= 5:
            return True
        if has_scope_source_evidence or has_content_source_evidence:
            return True
    if has_config and content_hits >= 2:
        return True
    if has_config and has_phrase and content_hits >= 1:
        return True
    if _is_deploy_config_path(path) and has_config and content_hits >= 1:
        return True
    if _is_source_like_code_path(path) and "config file" not in reasons:
        has_path_evidence = any(
            reason.startswith(("multi-term path match", "keyword phrase match:", "quoted literal match:"))
            for reason in reasons
        )
        has_path_owner = any(
            reason == "filename keyword match" or reason.startswith(("matched role keyword:", "matched ranking keyword:"))
            for reason in reasons
        )
        if has_path_evidence and has_path_owner:
            return True
        if content_hits >= 1 and any(reason == "implementation role match" for reason in reasons) and any(
            reason.startswith("cross-layer related") for reason in reasons
        ):
            return True
    has_direct_summary_field = any(
        reason.startswith((
            "matched define:",
            "multi-token defines match",
            "matched naming keyword:",
            "matched entrypoint:",
        ))
        for reason in reasons
    )
    has_symbol = "symbol keyword match" in reasons
    if has_direct_summary_field and (has_symbol or has_config or content_hits >= 1):
        return True
    has_expansion = any(reason.startswith(_EXPANSION_ONLY_SUPPORT_PREFIXES) for reason in reasons)
    if has_expansion:
        if has_phrase and has_impl_or_define and content_hits >= 1:
            return True
        return content_hits >= 2 and has_impl_or_define
    if has_phrase and has_impl_or_define and content_hits >= 1:
        return True
    return has_phrase and content_hits >= 3


def _can_bypass_guarded_summary_floor(path: str, reasons: list[str], score: float, min_summary_score: float) -> bool:
    if score < max(60.0, min_summary_score - 60.0):
        return False
    if any(reason.startswith(("secret redaction candidate", "matched domain:")) for reason in reasons):
        return True
    has_symbol = "symbol keyword match" in reasons
    has_config = "config file" in reasons
    has_direct_summary_field = any(
        reason.startswith((
            "matched define:",
            "multi-token defines match",
            "matched call:",
            "matched naming keyword:",
            "matched entrypoint:",
            "matched env read:",
            "matched side effect:",
            "matched external system:",
        ))
        for reason in reasons
    )
    content_hits = 0
    for reason in reasons:
        match = re.match(r"content keyword match \((\d+)\)", reason)
        if match:
            content_hits = max(content_hits, int(match.group(1)))
    return has_direct_summary_field and (has_symbol or has_config or content_hits >= 1)


def _has_release_metadata_reason(reasons: list[str]) -> bool:
    return any(reason.startswith("release/version metadata") for reason in reasons)


def _is_primary_release_metadata(path: str, reasons: list[str]) -> bool:
    if not _has_release_metadata_reason(reasons):
        return False
    name = Path(path).name.lower()
    return name in {"pyproject.toml", "package.json", "cargo.toml", "pom.xml", "__init__.py", "__about__.py", "_version.py", "version.py"}


def _is_secondary_release_metadata(path: str, reasons: list[str]) -> bool:
    return _has_release_metadata_reason(reasons) and not _is_primary_release_metadata(path, reasons)


def _is_lock_or_generated_path(path: str) -> bool:
    name = Path(path).name.lower()
    parts = {part.lower() for part in Path(path).parts}
    if parts & {"dist", "build", "coverage", "vendor", "generated", "__generated__", "snapshots", "__snapshots__"}:
        return True
    return name in {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "cargo.lock",
        "poetry.lock",
        "gemfile.lock",
    } or name.endswith((".snap", ".snapshot"))


def _is_source_like_code_path(path: str) -> bool:
    if _is_source_path(path) or any(part.lower() in {"api", "handler", "handlers", "cmd"} for part in Path(path).parts):
        suffix = Path(path).suffix.lower()
        return suffix in {".go", ".rs", ".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx"}
    return False


def _is_deploy_config_path(path: str) -> bool:
    path_lc = path.lower()
    name = Path(path_lc).name
    if name in _DEPLOY_CONFIG_FILENAMES:
        return True
    parts = {part.lower() for part in Path(path_lc).parts}
    if name in {"manifest.yml", "manifest.yaml"}:
        return bool(parts & {"copilot", "deploy", "deployment", "k8s", "kubernetes"})
    if name.startswith(("deploy", "release", "rollback")) and Path(name).suffix.lower() in {".sh", ".py", ".js", ".ts"}:
        return True
    if parts & (_DEPLOY_CONFIG_PARTS - {".github/workflows", "github/workflows"}):
        return True
    return path_lc.startswith(".github/workflows/")


def _compressed_context_family(path: str, reasons: list[str]) -> tuple[str, int] | None:
    reason_text = " ".join(reasons).lower()
    explicit_test_task = "explicit test task file" in reasons
    if _is_lock_or_generated_path(path):
        if _has_release_metadata_reason(reasons):
            return "lock-release", 1
        return "lock-generated", 0
    if _has_release_metadata_reason(reasons):
        return "release-metadata", 2
    if _is_test_path(path):
        return "tests", 4 if explicit_test_task else 2
    if "config file" in reasons:
        has_specific_path_match = any(reason.startswith("multi-term path match") for reason in reasons)
        return "config", 2 if has_specific_path_match else 1
    if _is_docs_path(path):
        return "docs", 1
    if _is_example_or_playground_path(path):
        return "examples", 1
    if _is_direct_source_candidate(path, reasons):
        return None
    if (
        "recall neighbor" in reason_text
        or "workspace match" in reason_text
        or "large supported file" in reason_text
        or "second-pass recall neighbor" in reason_text
    ):
        return "expansion", 1
    return None


def _package_root(path: str) -> str | None:
    parts = [part for part in Path(path).parts if part]
    if len(parts) >= 2 and parts[0] == "packages":
        return "/".join(parts[:2])
    return None


def _playground_root(path: str) -> str | None:
    parts = [part for part in Path(path).parts if part]
    if len(parts) >= 2 and parts[0] == "playground":
        return "/".join(parts[:2])
    return None


def _can_overflow_same_package_test(path: str, reasons: list[str], selected: list[SelectedFile]) -> bool:
    package = _package_root(path)
    if package is None or not _is_test_path(path):
        return False
    if "explicit test task file" in reasons:
        return False
    has_direct_test_evidence = any(reason.startswith("direct content evidence") for reason in reasons)
    has_paired_source_reason = any(reason.startswith("test for high-scoring ") for reason in reasons)
    has_strong_task_evidence = _content_keyword_hits(reasons) >= 4 or any(
        reason.startswith("keyword phrase match:")
        for reason in reasons
    )
    if not (has_direct_test_evidence and has_paired_source_reason and has_strong_task_evidence):
        return False
    selected_package_sources = {
        sf.path
        for sf in selected
        if _package_root(sf.path) == package and not _is_test_path(sf.path)
    }
    if not selected_package_sources:
        return False
    for reason in reasons:
        if not reason.startswith("test for high-scoring "):
            continue
        source_path = reason.removeprefix("test for high-scoring ").strip()
        if source_path in selected_package_sources:
            return True
    return False


def _can_overflow_same_playground_test(path: str, reasons: list[str], selected: list[SelectedFile]) -> bool:
    playground = _playground_root(path)
    if playground is None or not _is_test_path(path):
        return False
    has_scope_evidence = "conventional scope path match" in reasons
    has_phrase_evidence = any(reason.startswith("keyword phrase match:") for reason in reasons)
    has_content_evidence = _content_keyword_hits(reasons) >= 3 or any(
        reason.startswith(("matched call:", "direct content evidence"))
        for reason in reasons
    )
    if not (has_scope_evidence and (has_phrase_evidence or has_content_evidence)):
        return False
    return any(_playground_root(sf.path) == playground for sf in selected)


def _selected_test_scope_count(path: str, selected: list[SelectedFile]) -> int:
    scope = _replacement_scope(path, [])
    if scope is None:
        return 0
    return sum(1 for sf in selected if _is_test_path(sf.path) and _replacement_scope(sf.path, sf.reasons) == scope)


def _can_overflow_strong_test_cap(
    path: str,
    reasons: list[str],
    score: float,
    token_cost: int,
    selected: list[SelectedFile],
) -> bool:
    if not _is_test_path(path) or token_cost > 160 or score < 300:
        return False
    if _is_example_or_playground_path(path) or _package_root(path) == "packages/vite":
        return False
    if _selected_test_scope_count(path, selected) >= 2:
        return False
    if _content_keyword_hits(reasons) < 2 and not (
        "symbol keyword match" in reasons
        and (
            "filename keyword match" in reasons
            or any(reason.startswith(("matched role keyword:", "matched ranking keyword:")) for reason in reasons)
        )
    ) and not any(
        reason.startswith((
            "keyword phrase match:",
            "matched call:",
            "multi-token defines match",
            "literal definition match:",
        ))
        for reason in reasons
    ):
        return False
    return any(
        reason.startswith((
            "direct content evidence",
            "keyword phrase match:",
            "matched call:",
            "matched define:",
            "matched role keyword:",
            "multi-token defines match",
        ))
        or reason == "explicit test task file"
        for reason in reasons
    )


def _can_overflow_strong_owner_cap(
    path: str,
    reasons: list[str],
    score: float,
    token_cost: int,
) -> bool:
    if token_cost > 220 or score < 180:
        return False
    if _is_test_path(path) or _is_docs_path(path) or _is_example_or_playground_path(path):
        return False
    if not (_is_source_like_code_path(path) or "config file" in reasons):
        return False
    if _is_lock_or_generated_path(path):
        return False
    return (
        _has_direct_source_evidence(reasons)
        or _has_actionable_compressed_evidence(reasons)
        or _has_strict_summary_support(reasons, path)
    )


_CLEANUP_REFACTOR_TASK_TERMS = {
    "cleanup",
    "deprecated",
    "deprecation",
    "format",
    "lint",
    "polish",
    "simplify",
    "unused",
}


def _is_cleanup_refactor_task(keywords: set[str]) -> bool:
    return bool(keywords & _CLEANUP_REFACTOR_TASK_TERMS)


def _is_cleanup_refactor_code_path(path: str) -> bool:
    if _is_docs_path(path) or _is_lock_or_generated_path(path):
        return False
    if _is_test_path(path):
        return True
    return Path(path).suffix.lower() in {".go", ".rs", ".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx"}


def _has_cleanup_refactor_candidate_evidence(path: str, reasons: list[str]) -> bool:
    if "config file" in reasons:
        return _content_keyword_hits(reasons) >= 2
    if not _is_cleanup_refactor_code_path(path):
        return False
    if _content_keyword_hits(reasons) >= 2 and any(
        reason.startswith(("matched call:", "matched define:", "keyword phrase match:", "quoted literal match:"))
        for reason in reasons
    ):
        return True
    return _content_keyword_hits(reasons) >= 1 and "implementation role match" in reasons


def _can_bypass_cleanup_summary_floor(
    path: str,
    reasons: list[str],
    score: float,
    min_summary_score: float,
    keywords: set[str],
) -> bool:
    if not _is_cleanup_refactor_task(keywords):
        return False
    minimum_score = 60.0 if keywords & {"deprecated", "deprecation"} else 70.0
    if score < max(minimum_score, min_summary_score - 70.0):
        return False
    if keywords & {"deprecated", "deprecation"} and _is_cleanup_refactor_code_path(path):
        return _content_keyword_hits(reasons) >= 2
    return _has_cleanup_refactor_candidate_evidence(path, reasons)


def _can_overflow_cleanup_refactor_cap(
    path: str,
    reasons: list[str],
    score: float,
    token_cost: int,
    keywords: set[str],
    selected: list[SelectedFile],
) -> bool:
    if token_cost > 140 or score < 70.0:
        return False
    if not _can_bypass_cleanup_summary_floor(path, reasons, score, score, keywords):
        return False
    candidate_scope = _cleanup_refactor_scope(path, reasons)
    if candidate_scope is None:
        return False
    return any(_cleanup_refactor_scope(sf.path, sf.reasons) == candidate_scope for sf in selected)


def _cleanup_refactor_scope(path: str, reasons: list[str]) -> str | None:
    parts = [part for part in Path(path).parts if part]
    for marker in (("src", "main", "java"), ("src", "test", "java")):
        marker_len = len(marker)
        for index in range(0, len(parts) - marker_len + 1):
            if tuple(part.lower() for part in parts[index:index + marker_len]) == marker:
                package_parts = parts[index + marker_len:-1]
                if package_parts:
                    return "/".join(package_parts)
    return _replacement_scope(path, reasons)


def _find_cleanup_refactor_replacement(
    selected: list[SelectedFile],
    *,
    challenger_path: str,
    challenger_score: float,
    challenger_reasons: list[str],
    challenger_tokens: int,
    selected_token_costs: dict[str, int],
    keywords: set[str],
) -> int | None:
    if not _is_cleanup_refactor_task(keywords):
        return None
    if not _has_cleanup_refactor_candidate_evidence(challenger_path, challenger_reasons):
        return None

    best_index: int | None = None
    best_gain = 0.0
    for index, incumbent in enumerate(selected):
        if incumbent.include_mode not in ("summary", "skeleton"):
            continue
        if not _has_cleanup_refactor_candidate_evidence(incumbent.path, incumbent.reasons):
            continue
        incumbent_tokens = selected_token_costs.get(incumbent.path, 0)
        if challenger_tokens - incumbent_tokens > 20:
            continue
        score_gain = challenger_score - incumbent.score
        if score_gain < 10.0:
            continue
        evidence_gain = _marginal_evidence_score(
            challenger_path,
            challenger_score,
            challenger_reasons,
            challenger_tokens,
        ) - _marginal_evidence_score(
            incumbent.path,
            incumbent.score,
            incumbent.reasons,
            incumbent_tokens,
        )
        gain = score_gain + max(0.0, evidence_gain)
        if gain > best_gain:
            best_gain = gain
            best_index = index
    return best_index


def _marginal_evidence_score(path: str, score: float, reasons: list[str], token_cost: int) -> float:
    """Score final-slot utility using evidence density, not raw rank alone."""
    evidence = min(score, 220.0) * 0.25
    content_hits = _content_keyword_hits(reasons)
    evidence += min(content_hits, 6) * 18.0
    if _has_direct_source_evidence(reasons):
        evidence += 130.0
    if _has_actionable_compressed_evidence(reasons):
        evidence += 95.0
    if _has_strict_summary_support(reasons, path):
        evidence += 65.0
    if "config file" in reasons and content_hits >= 2:
        evidence += 45.0
    if _is_runtime_infra_candidate(path, reasons):
        evidence += 80.0
    if any(reason.startswith(("direct dependency", "reverse dependency", "caller of selected symbol")) for reason in reasons):
        evidence += 60.0
    if any(reason.startswith("workspace match") for reason in reasons):
        evidence += 25.0
    if _is_test_path(path) and "explicit test task file" not in reasons:
        evidence -= 55.0
    if _is_example_or_playground_path(path):
        evidence -= 45.0
    if _is_docs_path(path):
        evidence -= 40.0
    if _is_weak_signal_candidate(reasons):
        evidence -= 60.0
    if (
        _is_source_path(path)
        and content_hits <= 1
        and not any(
            reason.startswith(("direct content evidence", "keyword phrase match:", "matched call:"))
            for reason in reasons
        )
    ):
        evidence -= 70.0
    if token_cost > 0:
        evidence += min(35.0, 1200.0 / max(token_cost, 40))
    return evidence


def _find_marginal_replacement(
    selected: list[SelectedFile],
    *,
    challenger_path: str,
    challenger_score: float,
    challenger_reasons: list[str],
    challenger_tokens: int,
    selected_token_costs: dict[str, int],
    required_family: str | None = None,
    max_extra_tokens: int = 0,
    max_strong_source_extra_tokens: int = 0,
    max_owner_carrier_extra_tokens: int = 0,
) -> int | None:
    challenger_evidence = _marginal_evidence_score(
        challenger_path,
        challenger_score,
        challenger_reasons,
        challenger_tokens,
    )
    best_index: int | None = None
    best_gain = 0.0
    for index, incumbent in enumerate(selected):
        if incumbent.include_mode not in ("summary", "skeleton"):
            continue
        if any(
            reason in {"same-package test overflow", "same-playground test overflow", "cleanup-refactor cap overflow"}
            for reason in incumbent.reasons
        ):
            continue
        if (
            len(Path(incumbent.path).parts) == 1
            and _is_primary_release_metadata(incumbent.path, incumbent.reasons)
            and len(Path(challenger_path).parts) > 1
        ):
            continue
        incumbent_tokens = selected_token_costs.get(incumbent.path, 0)
        token_delta = challenger_tokens - incumbent_tokens
        can_budgeted_source_replace = _can_budgeted_strong_source_replace_incumbent(
            challenger_path=challenger_path,
            challenger_reasons=challenger_reasons,
            challenger_score=challenger_score,
            challenger_tokens=challenger_tokens,
            incumbent=incumbent,
            token_delta=token_delta,
            max_extra_tokens=max_strong_source_extra_tokens,
        )
        can_rescue_high_signal_candidate = _can_rescue_high_signal_candidate_replace_incumbent(
            challenger_path=challenger_path,
            challenger_reasons=challenger_reasons,
            challenger_score=challenger_score,
            challenger_tokens=challenger_tokens,
            challenger_evidence=challenger_evidence,
            incumbent=incumbent,
            incumbent_tokens=incumbent_tokens,
            max_extra_tokens=max_extra_tokens,
            max_owner_carrier_extra_tokens=max_owner_carrier_extra_tokens,
        )
        if token_delta > max_extra_tokens and not can_budgeted_source_replace and not can_rescue_high_signal_candidate:
            continue
        incumbent_scope = _replacement_scope(incumbent.path, incumbent.reasons)
        challenger_scope = _replacement_scope(challenger_path, challenger_reasons)
        challenger_is_root_config = len(Path(challenger_path).parts) == 1 and "config file" in challenger_reasons
        if (
            incumbent_scope is not None
            and challenger_scope != incumbent_scope
            and not challenger_is_root_config
            and not _can_cross_scope_replace(incumbent_scope, challenger_scope, incumbent.reasons, challenger_reasons)
            and not can_budgeted_source_replace
            and not can_rescue_high_signal_candidate
        ):
            continue
        if (
            _is_test_path(challenger_path)
            and "explicit test task file" not in challenger_reasons
            and not _can_token_neutral_test_replace_incumbent(
                challenger_reasons=challenger_reasons,
                incumbent=incumbent,
                token_delta=token_delta,
            )
            and not can_rescue_high_signal_candidate
        ):
            continue
        if required_family is not None:
            incumbent_family = _compressed_context_family(incumbent.path, incumbent.reasons)
            if (incumbent_family is None or incumbent_family[0] != required_family) and not can_rescue_high_signal_candidate:
                continue
        incumbent_evidence = _marginal_evidence_score(
            incumbent.path,
            incumbent.score,
            incumbent.reasons,
            incumbent_tokens,
        )
        gain = challenger_evidence - incumbent_evidence
        if can_rescue_high_signal_candidate:
            gain += min(140.0, max(0.0, challenger_score - incumbent.score) * 0.25)
        can_owner_over_broad_carrier = can_rescue_high_signal_candidate and _can_symbol_owner_replace_broad_source_carrier(
            challenger_path=challenger_path,
            challenger_reasons=challenger_reasons,
            incumbent=incumbent,
        )
        if can_owner_over_broad_carrier:
            gain += 220.0
        required_gain = 70.0 + max(0, token_delta) * 0.15
        if _can_token_neutral_owner_replace_incumbent(
            challenger_path=challenger_path,
            challenger_reasons=challenger_reasons,
            incumbent=incumbent,
            token_delta=token_delta,
        ):
            required_gain = min(required_gain, 45.0)
        if can_budgeted_source_replace:
            required_gain = min(required_gain, 45.0 + max(0, token_delta) * 0.04)
        if can_rescue_high_signal_candidate:
            required_gain = min(required_gain, 50.0 + max(0, token_delta) * 0.05)
        if can_owner_over_broad_carrier:
            required_gain = min(required_gain, 45.0 + max(0, token_delta) * 0.03)
        if (
            _is_test_path(challenger_path)
            and _can_token_neutral_test_replace_incumbent(
                challenger_reasons=challenger_reasons,
                incumbent=incumbent,
                token_delta=token_delta,
            )
        ):
            required_gain = min(required_gain, 55.0)
        if gain >= required_gain and gain > best_gain:
            best_gain = gain
            best_index = index
    return best_index


def _can_rescue_high_signal_candidate_replace_incumbent(
    *,
    challenger_path: str,
    challenger_reasons: list[str],
    challenger_score: float,
    challenger_tokens: int,
    challenger_evidence: float,
    incumbent: SelectedFile,
    incumbent_tokens: int,
    max_extra_tokens: int,
    max_owner_carrier_extra_tokens: int = 0,
) -> bool:
    token_delta = challenger_tokens - incumbent_tokens
    owner_over_broad_carrier = _can_symbol_owner_replace_broad_source_carrier(
        challenger_path=challenger_path,
        challenger_reasons=challenger_reasons,
        incumbent=incumbent,
    )
    token_limit = 360 if owner_over_broad_carrier else 220
    extra_limit = max(max_extra_tokens, max_owner_carrier_extra_tokens) if owner_over_broad_carrier else max_extra_tokens
    if challenger_tokens > token_limit or token_delta > extra_limit:
        return False
    if challenger_score < 120.0:
        return False
    if _is_docs_path(challenger_path) or _is_lock_or_generated_path(challenger_path):
        return False
    if _is_example_or_playground_path(challenger_path):
        return False
    if any(
        reason in {"same-package test overflow", "same-playground test overflow", "cleanup-refactor cap overflow"}
        for reason in incumbent.reasons
    ):
        return False
    if _is_primary_release_metadata(incumbent.path, incumbent.reasons):
        return False
    if _is_direct_test_context(incumbent.path, incumbent.reasons):
        return False
    incumbent_is_supported_code_owner = (
        not _is_test_path(incumbent.path)
        and not _is_docs_path(incumbent.path)
        and not _is_example_or_playground_path(incumbent.path)
        and not _is_lock_or_generated_path(incumbent.path)
        and Path(incumbent.path).suffix.lower() in {".go", ".rs", ".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx"}
        and (
            _has_direct_source_evidence(incumbent.reasons)
            or _has_actionable_compressed_evidence(incumbent.reasons)
            or _has_strict_summary_support(incumbent.reasons, incumbent.path)
        )
    )
    if incumbent_is_supported_code_owner and not owner_over_broad_carrier:
        return False

    content_hits = _content_keyword_hits(challenger_reasons)
    has_structural_path_signal = "conventional scope path match" in challenger_reasons or any(
        reason.startswith("multi-term path match")
        for reason in challenger_reasons
    )
    has_direct_signal = any(
        reason.startswith((
            "direct content evidence",
            "keyword phrase match:",
            "literal definition match:",
            "matched call:",
            "matched define:",
            "matched entrypoint:",
            "matched external system:",
            "quoted literal match:",
        ))
        for reason in challenger_reasons
    )
    has_symbolic_owner_signal = "symbol keyword match" in challenger_reasons and (
        has_structural_path_signal
        or any(reason.startswith(("matched role keyword:", "matched ranking keyword:")) for reason in challenger_reasons)
        or content_hits >= 2
    )
    has_config_signal = "config file" in challenger_reasons and (
        content_hits >= 2
        or has_structural_path_signal
        or any(reason in {"build/dependency metadata"} or reason.startswith("matched external system:") for reason in challenger_reasons)
    )
    has_test_signal = _is_test_path(challenger_path) and (
        "explicit test task file" in challenger_reasons
        or content_hits >= 4
        or has_direct_signal
        or has_symbolic_owner_signal
    )
    if not (has_direct_signal or has_symbolic_owner_signal or has_config_signal or has_test_signal):
        return False

    incumbent_evidence = _marginal_evidence_score(
        incumbent.path,
        incumbent.score,
        incumbent.reasons,
        incumbent_tokens,
    )
    score_gain = challenger_score - incumbent.score
    if owner_over_broad_carrier:
        return challenger_score >= incumbent.score - 140.0
    if challenger_evidence + max(0.0, score_gain) * 0.25 < incumbent_evidence + 45.0:
        return False
    return True


def _can_symbol_owner_replace_broad_source_carrier(
    *,
    challenger_path: str,
    challenger_reasons: list[str],
    incumbent: SelectedFile,
) -> bool:
    if not _is_source_like_code_path(challenger_path) or _is_test_path(challenger_path):
        return False
    if not _is_source_like_code_path(incumbent.path) or _is_test_path(incumbent.path):
        return False
    if _is_docs_path(challenger_path) or _is_example_or_playground_path(challenger_path):
        return False
    if _is_docs_path(incumbent.path) or _is_example_or_playground_path(incumbent.path):
        return False

    challenger_has_symbol_owner = (
        _has_reason_signal(challenger_reasons, "symbol keyword match")
        and _has_reason_signal(challenger_reasons, "matched define:", "multi-token defines match")
        and (
            _has_reason_signal(challenger_reasons, "filename keyword match", "conventional scope path match", "multi-term path match")
            or _has_reason_signal(challenger_reasons, "matched ranking keyword:", "matched role keyword:")
        )
    )
    if not challenger_has_symbol_owner:
        return False

    incumbent_has_owner_anchor = _has_reason_signal(
        incumbent.reasons,
        "filename keyword match",
        "symbol keyword match",
        "matched define:",
        "multi-token defines match",
        "conventional scope path match",
        "multi-term path match",
        "literal definition match:",
        "quoted literal match:",
        "matched entrypoint:",
    )
    if incumbent_has_owner_anchor:
        return False
    return _has_reason_signal(incumbent.reasons, "direct content evidence", "keyword phrase match:", "matched call:") and _content_keyword_hits(incumbent.reasons) >= 3


def _can_budgeted_strong_source_replace_incumbent(
    *,
    challenger_path: str,
    challenger_reasons: list[str],
    challenger_score: float,
    challenger_tokens: int,
    incumbent: SelectedFile,
    token_delta: int,
    max_extra_tokens: int,
) -> bool:
    if token_delta > max_extra_tokens or challenger_tokens > 220 or challenger_score < 180:
        return False
    if _is_test_path(challenger_path) or _is_docs_path(challenger_path) or _is_example_or_playground_path(challenger_path):
        return False
    if not (_is_source_like_code_path(challenger_path) or "config file" in challenger_reasons):
        return False
    if _is_lock_or_generated_path(challenger_path):
        return False
    has_strong_challenger_evidence = (
        _has_direct_source_evidence(challenger_reasons)
        or _has_actionable_compressed_evidence(challenger_reasons)
        or _has_strict_summary_support(challenger_reasons, challenger_path)
    )
    has_content_anchor = _content_keyword_hits(challenger_reasons) >= 3 or any(
        reason.startswith((
            "direct content evidence",
            "keyword phrase match:",
            "matched call:",
            "matched define:",
            "quoted literal match:",
        ))
        for reason in challenger_reasons
    )
    if not (has_strong_challenger_evidence and has_content_anchor):
        return False
    incumbent_is_direct_test_context = _is_direct_test_context(incumbent.path, incumbent.reasons)
    if incumbent_is_direct_test_context or _is_primary_release_metadata(incumbent.path, incumbent.reasons):
        return False
    incumbent_is_weak_compressed = incumbent.include_mode in {"summary", "skeleton"} and (
        (_is_test_path(incumbent.path) and not incumbent_is_direct_test_context)
        or _is_docs_path(incumbent.path)
        or _is_example_or_playground_path(incumbent.path)
        or "config file" in incumbent.reasons
        or not _has_strict_summary_support(incumbent.reasons, incumbent.path)
    )
    if not incumbent_is_weak_compressed:
        return False
    incumbent_has_strong_owner_signal = (
        _is_source_like_code_path(incumbent.path)
        and (
            _has_direct_source_evidence(incumbent.reasons)
            or _has_actionable_compressed_evidence(incumbent.reasons)
            or _has_strict_summary_support(incumbent.reasons, incumbent.path)
        )
    )
    return not incumbent_has_strong_owner_signal


def _is_direct_test_context(path: str, reasons: list[str]) -> bool:
    if not _is_test_path(path):
        return False
    if "explicit test task file" in reasons:
        return True
    is_code_test = Path(path).suffix.lower() in {
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
    }
    if is_code_test and any(reason.startswith("test for high-scoring") or "related test" in reason for reason in reasons):
        return True
    has_direct_test_signal = any(
        reason.startswith((
            "direct content evidence",
            "matched call:",
            "matched define:",
            "keyword phrase match:",
            "quoted literal match:",
        ))
        for reason in reasons
    )
    return is_code_test and has_direct_test_signal and _content_keyword_hits(reasons) >= 2


def _can_token_neutral_owner_replace_incumbent(
    *,
    challenger_path: str,
    challenger_reasons: list[str],
    incumbent: SelectedFile,
    token_delta: int,
) -> bool:
    if token_delta > 0 or _is_test_path(challenger_path):
        return False
    if not _is_source_path(challenger_path) and "config file" not in challenger_reasons:
        return False
    if not (
        _has_direct_source_evidence(challenger_reasons)
        or _has_actionable_compressed_evidence(challenger_reasons)
        or _has_strict_summary_support(challenger_reasons, challenger_path)
    ):
        return False
    if _is_test_path(incumbent.path):
        return False
    incumbent_has_strong_owner_signal = (
        _is_source_path(incumbent.path)
        and (
            _has_direct_source_evidence(incumbent.reasons)
            or _has_actionable_compressed_evidence(incumbent.reasons)
        )
    )
    if incumbent_has_strong_owner_signal:
        return False
    if _is_primary_release_metadata(incumbent.path, incumbent.reasons):
        return False
    return True


def _can_token_neutral_test_replace_incumbent(
    *,
    challenger_reasons: list[str],
    incumbent: SelectedFile,
    token_delta: int,
) -> bool:
    if token_delta > 0:
        return False
    has_test_pairing = any(
        reason.startswith("test for high-scoring") or "related test" in reason
        for reason in challenger_reasons
    )
    has_direct_test_evidence = _content_keyword_hits(challenger_reasons) >= 3 and any(
        reason.startswith((
            "matched call:",
            "matched define:",
            "keyword phrase match:",
            "quoted literal match:",
            "direct content evidence",
        ))
        for reason in challenger_reasons
    )
    if not (has_test_pairing or has_direct_test_evidence):
        return False
    if _is_source_path(incumbent.path) and (
        _has_direct_source_evidence(incumbent.reasons)
        or _has_actionable_compressed_evidence(incumbent.reasons)
    ):
        return False
    if any(reason in {"same-package test overflow", "same-playground test overflow"} for reason in incumbent.reasons):
        return False
    return True


def _replacement_scope(path: str, reasons: list[str]) -> str | None:
    parts = [part for part in Path(path).parts if part]
    if not parts:
        return None
    if _playground_root(path):
        return _playground_root(path)
    if len(parts) >= 2 and parts[0] == "integration":
        return "/".join(parts[:2])
    if _is_source_path(path) and len(parts) >= 5 and parts[0] == "packages" and parts[2] == "src":
        return "/".join(parts[:-1])
    if _is_test_path(path) and len(parts) >= 3:
        return "/".join(parts[:-1])
    if _package_root(path):
        return _package_root(path)
    return _balance_scope(path) or None


def _can_cross_scope_replace(
    incumbent_scope: str | None,
    challenger_scope: str | None,
    incumbent_reasons: list[str],
    challenger_reasons: list[str],
) -> bool:
    if not incumbent_scope or not challenger_scope:
        return False
    incumbent_parts = incumbent_scope.split("/")
    challenger_parts = challenger_scope.split("/")
    if len(challenger_parts) <= len(incumbent_parts):
        return False
    if challenger_parts[: len(incumbent_parts)] != incumbent_parts:
        return False
    incumbent_direct_content = any(reason.startswith("direct content evidence") for reason in incumbent_reasons)
    if incumbent_direct_content or _content_keyword_hits(incumbent_reasons) >= 3:
        return False
    return any(reason.startswith("direct content evidence") for reason in challenger_reasons)


def select_files(
    files: list[FileInfo],
    scored: list[tuple[FileInfo, float, list[str]]],
    changed_paths: set[str],
    summaries: dict[str, Any],
    mode: Mode,
    budget: int,
    max_file_tokens: int,
    keywords: set[str] | None = None,
    min_summary_score: float = 0,
    max_summary_files: int = 0,
    max_weak_signal_files: int = 0,
    strict_summary_selection: bool = False,
    omitted_relevant_files: list[OmittedRelevantFile] | None = None,
) -> tuple[list[SelectedFile], list[Receipt]]:
    opts = _MODE_WEIGHTS[mode]
    selected: list[SelectedFile] = []
    receipts: list[Receipt] = []
    selected_token_costs: dict[str, int] = {}
    file_by_path = {fi.path: fi for fi in files}
    tokens_used = 0
    summaries_used = 0
    kw = keywords or set()
    budget_pressure = budget < 12000 or len(changed_paths) > 5
    unrelated_changed_cap = 3 if len(changed_paths) > 5 else 0
    unrelated_changed_used = 0
    weak_signal_used = 0
    paired_test_overflow_used = 0
    playground_test_overflow_used = 0
    strong_test_overflow_used = 0
    strong_owner_overflow_used = 0
    cleanup_refactor_overflow_used = 0
    primary_release_metadata_selected = False
    compressed_family_counts: dict[str, int] = {}

    def displace_selected(index: int, challenger_path: str) -> None:
        nonlocal tokens_used, summaries_used
        displaced = selected.pop(index)
        displaced_tokens = selected_token_costs.pop(displaced.path, 0)
        tokens_used -= displaced_tokens
        if displaced.include_mode in ("summary", "skeleton"):
            summaries_used = max(0, summaries_used - 1)
        displaced_family = _compressed_context_family(displaced.path, displaced.reasons)
        if displaced_family is not None:
            family, _cap = displaced_family
            compressed_family_counts[family] = max(0, compressed_family_counts.get(family, 0) - 1)
        receipts.append(
            Receipt(
                path=displaced.path,
                action="excluded",
                reason=f"marginal slot replaced by {challenger_path}",
                citations=_receipt_citations(displaced.path, file_by_path),
            )
        )

    ranked = sorted(
        scored,
        key=lambda item: _selection_priority(item, changed_paths, max_file_tokens, summaries=summaries),
        reverse=True,
    )
    ordered = _reserve_bucket_order(ranked, changed_paths, budget)
    ordered = _config_source_balanced_order(ordered, max_summary_files)
    ordered = _context_shape_order(ordered, max_summary_files)
    for fi, score, reasons in ordered:
        if fi.ignored or fi.binary:
            receipts.append(_receipt(fi, "excluded", "ignored or binary"))
            continue

        if score <= 0:
            receipts.append(_receipt(fi, "excluded", "score too low"))
            continue

        is_changed = fi.path in changed_paths
        summary_data = summaries.get(fi.path)
        has_task_signal = _has_task_signal(reasons)
        weak_signal_only = not is_changed and _is_weak_signal_candidate(reasons)
        if not is_changed and not opts["include_docs"] and _selection_bucket(fi, reasons, changed_paths) == "docs":
            receipts.append(_receipt(fi, "excluded", "docs disabled by mode"))
            continue
        if is_changed and not has_task_signal and unrelated_changed_cap:
            if unrelated_changed_used >= unrelated_changed_cap:
                receipts.append(_receipt(fi, "excluded", "unrelated changed-file safety cap"))
                continue
            unrelated_changed_used += 1
        if weak_signal_only and max_weak_signal_files >= 0 and weak_signal_used >= max_weak_signal_files:
            receipts.append(_receipt(fi, "excluded", "weak-signal cap reached"))
            continue
        if primary_release_metadata_selected and not is_changed and _is_secondary_release_metadata(fi.path, reasons):
            receipts.append(_receipt(fi, "excluded", "secondary release metadata skipped after primary"))
            continue
        will_be_summary = not is_changed and not (
            opts["extra_full"] and fi.estimated_tokens <= max_file_tokens
        )
        has_redaction_reason = any(reason.startswith("secret redaction candidate") for reason in reasons)
        floor_bypass = (
            has_redaction_reason
            or _can_bypass_guarded_summary_floor(fi.path, reasons, score, min_summary_score)
            or _can_bypass_cleanup_summary_floor(fi.path, reasons, score, min_summary_score, kw)
        )
        if (
            will_be_summary
            and score < min_summary_score
            and not floor_bypass
        ):
            receipts.append(_receipt(fi, "excluded", "summary score below floor"))
            continue

        # Determine inclusion mode. Changed files are not all equal: tiny or
        # task-matching files still get full source, while large unrelated dirty
        # files are compressed before they can consume the whole budget.
        mode_str: Literal["full", "diff", "symbols", "skeleton", "summary"]
        content: str | None = None
        diff_content: str | None = None
        skeleton: str | None = None
        skeleton_tokens = 0

        if summary_data:
            skeleton, skeleton_tokens = _skeleton_content(fi, summary_data)

        if is_changed:
            small_changed = fi.estimated_tokens <= min(600, max_file_tokens)
            should_full = fi.estimated_tokens <= max_file_tokens and (
                small_changed or (has_task_signal and not budget_pressure)
            )
            if should_full:
                mode_str = "full"
                content = fi.content if fi.content is not None else (
                    fi.abs_path.read_text(errors="replace") if fi.abs_path.exists() else None
                )
                tok = fi.estimated_tokens
            else:
                diff_content, diff_tokens = _git_diff_for_file(
                    fi,
                    max(200, min(max_file_tokens // 2, budget // 4)),
                    keywords=kw,
                )
                if diff_content:
                    mode_str = "diff"
                    content = diff_content
                    tok = diff_tokens
                    reasons = reasons + ["compressed changed file as diff"]
                elif has_task_signal:
                    mode_str = "symbols"
                    tok = min(fi.estimated_tokens, max_file_tokens // 2)
                elif skeleton:
                    mode_str = "skeleton"
                    content = skeleton
                    tok = skeleton_tokens
                    reasons = reasons + ["dirty file compressed to skeleton"]
                elif summary_data:
                    mode_str = "summary"
                    tok = _summary_tokens(summary_data, min(fi.estimated_tokens, 200))
                    reasons = reasons + ["dirty file compressed to summary"]
                else:
                    mode_str = "summary"
                    tok = min(fi.estimated_tokens, 200)
                    reasons = reasons + ["dirty file compressed to summary"]
        elif fi.estimated_tokens <= max_file_tokens and score > 0 and _has_redactable_secret(fi):
            mode_str = "full"
            content = fi.content if fi.content is not None else (
                fi.abs_path.read_text(errors="replace") if fi.abs_path.exists() else None
            )
            tok = fi.estimated_tokens
            reasons = reasons + ["full file included for secret redaction"]
        elif opts["extra_full"] and fi.estimated_tokens <= max_file_tokens and score >= 120:
            mode_str = "full"
            content = fi.content if fi.content is not None else (
                fi.abs_path.read_text(errors="replace") if fi.abs_path.exists() else None
            )
            tok = fi.estimated_tokens
        elif weak_signal_only and summary_data:
            mode_str = "summary"
            tok = _summary_tokens(summary_data, min(fi.estimated_tokens, 200))
            reasons = reasons + ["weak-signal file compressed to summary"]
        elif weak_signal_only:
            mode_str = "summary"
            tok = min(fi.estimated_tokens, 200)
            reasons = reasons + ["weak-signal file compressed to summary"]
        elif summary_data and skeleton and score >= 160:
            mode_str = "skeleton"
            content = skeleton
            tok = skeleton_tokens
        elif summary_data:
            mode_str = "summary"
            tok = _summary_tokens(summary_data, min(fi.estimated_tokens, 200))
        else:
            mode_str = "summary"
            tok = min(fi.estimated_tokens, 200)

        if tokens_used + tok > budget:
            fallback = _budget_fallback(
                mode_str=mode_str,
                fi=fi,
                summary_data=summary_data,
                skeleton=skeleton,
                skeleton_tokens=skeleton_tokens,
                current_tokens=tokens_used,
                budget=budget,
                max_file_tokens=max_file_tokens,
            )
            if fallback is not None:
                mode_str, content, tok, why = fallback
                reasons = reasons + [why]

        if mode_str == "summary" and max_summary_files < 0:
            receipts.append(_receipt(fi, "excluded", "summaries disabled by precision guard"))
            continue

        compressed_context = mode_str in ("summary", "skeleton")
        if compressed_context and not is_changed and mode == "balanced":
            weak_noise_reason = _weak_compressed_noise_reason(fi.path, reasons, kw)
            if weak_noise_reason:
                receipts.append(_receipt(fi, "excluded", weak_noise_reason))
                continue

        if (
            strict_summary_selection
            and compressed_context
            and not is_changed
            and not _has_strict_summary_support(reasons, fi.path)
            and not _can_bypass_cleanup_summary_floor(fi.path, reasons, score, min_summary_score, kw)
        ):
            receipts.append(_receipt(fi, "excluded", "compressed context needs stronger support signal"))
            continue

        paired_test_overflow = False
        playground_test_overflow = False
        strong_test_overflow = False
        strong_owner_overflow = False
        cleanup_refactor_overflow = False

        if strict_summary_selection and compressed_context and not is_changed and mode == "balanced":
            family_cap = _compressed_context_family(fi.path, reasons)
            if family_cap is not None:
                family, cap = family_cap
                if compressed_family_counts.get(family, 0) >= cap:
                    replacement_index: int | None = None
                    strong_test_overflow = (
                        family == "tests"
                        and strong_test_overflow_used < 2
                        and mode == "balanced"
                        and not changed_paths
                        and _can_overflow_strong_test_cap(fi.path, reasons, score, tok, selected)
                    )
                    cleanup_refactor_overflow = (
                        cleanup_refactor_overflow_used < 2
                        and mode == "balanced"
                        and not changed_paths
                        and _can_overflow_cleanup_refactor_cap(fi.path, reasons, score, tok, kw, selected)
                    )
                    strong_owner_overflow = (
                        False
                    )
                    if strong_test_overflow:
                        if "strong-test cap overflow" not in reasons:
                            reasons = reasons + ["strong-test cap overflow"]
                    elif cleanup_refactor_overflow:
                        reasons = reasons + ["cleanup-refactor cap overflow"]
                    elif strong_owner_overflow:
                        reasons = reasons + ["strong-owner cap overflow"]
                    else:
                        replacement_index = _find_cleanup_refactor_replacement(
                            selected,
                            challenger_path=fi.path,
                            challenger_score=score,
                            challenger_reasons=reasons,
                            challenger_tokens=tok,
                            selected_token_costs=selected_token_costs,
                            keywords=kw,
                        )
                        if replacement_index is not None:
                            displace_selected(replacement_index, fi.path)
                        else:
                            replacement_index = None
                    if not strong_test_overflow and not cleanup_refactor_overflow and not strong_owner_overflow and replacement_index is None:
                        max_extra_tokens = min(120, max(0, budget - tokens_used))
                        replacement_index = _find_marginal_replacement(
                            selected,
                            challenger_path=fi.path,
                            challenger_score=score,
                            challenger_reasons=reasons,
                            challenger_tokens=tok,
                            selected_token_costs=selected_token_costs,
                            required_family=family,
                            max_extra_tokens=max_extra_tokens,
                            max_owner_carrier_extra_tokens=min(220, max(0, budget - tokens_used)),
                        )
                        if replacement_index is not None:
                            displace_selected(replacement_index, fi.path)
                        else:
                            receipts.append(_receipt(fi, "excluded", f"{family} compressed context cap reached"))
                            continue
                if (
                    compressed_family_counts.get(family, 0) >= cap
                    and not strong_test_overflow
                    and not cleanup_refactor_overflow
                    and not strong_owner_overflow
                ):
                    receipts.append(_receipt(fi, "excluded", f"{family} compressed context cap reached"))
                    continue

        if compressed_context and max_summary_files > 0 and summaries_used >= max_summary_files:
            paired_test_overflow = (
                paired_test_overflow_used < 1
                and mode == "balanced"
                and not changed_paths
                and _can_overflow_same_package_test(fi.path, reasons, selected)
            )
            playground_test_overflow = (
                playground_test_overflow_used < 1
                and mode == "balanced"
                and not changed_paths
                and _can_overflow_same_playground_test(fi.path, reasons, selected)
            )
            strong_test_overflow = (
                strong_test_overflow
                or strong_test_overflow_used < 2
                and mode == "balanced"
                and not changed_paths
                and max_summary_files >= 3
                and _can_overflow_strong_test_cap(fi.path, reasons, score, tok, selected)
            )
            cleanup_refactor_overflow = (
                cleanup_refactor_overflow_used < 2
                and mode == "balanced"
                and not changed_paths
                and _can_overflow_cleanup_refactor_cap(fi.path, reasons, score, tok, kw, selected)
            )
            strong_owner_overflow = (
                False
            )
            if (
                not paired_test_overflow
                and not playground_test_overflow
                and not strong_test_overflow
                and not cleanup_refactor_overflow
                and not strong_owner_overflow
            ):
                max_extra_tokens = min(120, max(0, budget - tokens_used))
                replacement_index = _find_marginal_replacement(
                    selected,
                    challenger_path=fi.path,
                    challenger_score=score,
                    challenger_reasons=reasons,
                    challenger_tokens=tok,
                    selected_token_costs=selected_token_costs,
                    max_extra_tokens=max_extra_tokens,
                    max_strong_source_extra_tokens=min(220, max(0, budget - tokens_used)),
                    max_owner_carrier_extra_tokens=min(220, max(0, budget - tokens_used)),
                )
                if replacement_index is not None:
                    displace_selected(replacement_index, fi.path)
                else:
                    cleanup_replacement_index = _find_cleanup_refactor_replacement(
                        selected,
                        challenger_path=fi.path,
                        challenger_score=score,
                        challenger_reasons=reasons,
                        challenger_tokens=tok,
                        selected_token_costs=selected_token_costs,
                        keywords=kw,
                    )
                    if cleanup_replacement_index is not None:
                        displace_selected(cleanup_replacement_index, fi.path)
                    else:
                        receipts.append(_receipt(fi, "excluded", "compressed context cap reached"))
                        continue
            else:
                if paired_test_overflow:
                    reasons = reasons + ["same-package test overflow"]
                elif playground_test_overflow:
                    reasons = reasons + ["same-playground test overflow"]
                elif strong_test_overflow and "strong-test cap overflow" not in reasons:
                    reasons = reasons + ["strong-test cap overflow"]
                elif cleanup_refactor_overflow:
                    reasons = reasons + ["cleanup-refactor cap overflow"]
                elif strong_owner_overflow:
                    reasons = reasons + ["strong-owner cap overflow"]

        if tokens_used + tok > budget:
            replacement_index = _find_marginal_replacement(
                selected,
                challenger_path=fi.path,
                challenger_score=score,
                challenger_reasons=reasons,
                challenger_tokens=tok,
                selected_token_costs=selected_token_costs,
            )
            if replacement_index is not None:
                displace_selected(replacement_index, fi.path)
            elif omitted_relevant_files is not None:
                omitted_relevant_files.append(
                    OmittedRelevantFile(
                        path=fi.path,
                        score=score,
                        reasons=reasons,
                        estimated_tokens=fi.estimated_tokens,
                        suggested_mode=mode_str,
                        omission_reason="budget exhausted",
                        risk=classify_omission_risk(fi.path, reasons, score),
                    )
                )
                receipts.append(_receipt(fi, "excluded", "budget exhausted"))
                continue
            else:
                receipts.append(_receipt(fi, "excluded", "budget exhausted"))
                continue

        if tokens_used + tok > budget:
            if omitted_relevant_files is not None:
                omitted_relevant_files.append(
                    OmittedRelevantFile(
                        path=fi.path,
                        score=score,
                        reasons=reasons,
                        estimated_tokens=fi.estimated_tokens,
                        suggested_mode=mode_str,
                        omission_reason="budget exhausted",
                        risk=classify_omission_risk(fi.path, reasons, score),
                    )
                )
            receipts.append(_receipt(fi, "excluded", "budget exhausted"))
            continue

        tokens_used += tok
        if compressed_context:
            summaries_used += 1
        if paired_test_overflow:
            paired_test_overflow_used += 1
        if playground_test_overflow:
            playground_test_overflow_used += 1
        if strong_test_overflow:
            strong_test_overflow_used += 1
        if strong_owner_overflow:
            strong_owner_overflow_used += 1
        if cleanup_refactor_overflow:
            cleanup_refactor_overflow_used += 1
        if weak_signal_only:
            weak_signal_used += 1
        if _is_primary_release_metadata(fi.path, reasons):
            primary_release_metadata_selected = True
        if strict_summary_selection and compressed_context and not is_changed and mode == "balanced":
            family_cap = _compressed_context_family(fi.path, reasons)
            if family_cap is not None:
                family, _cap = family_cap
                compressed_family_counts[family] = compressed_family_counts.get(family, 0) + 1

        # Build symbol list
        syms: list[Symbol] = []
        if summary_data and mode_str in ("symbols", "skeleton"):
            raw_syms = summary_data.get("symbols", [])
            for s in raw_syms:
                try:
                    syms.append(Symbol(**s) if isinstance(s, dict) else s)
                except Exception as exc:
                    import warnings
                    warnings.warn(f"skipping malformed symbol in {fi.path}: {exc}", stacklevel=2)

        # Symbol body extraction for "symbols" mode
        sym_body_content: str | None = None
        if mode_str == "symbols" and syms and fi.abs_path.exists():
            budget_remaining = budget - tokens_used
            sym_body_content, extra_tok = _extract_relevant_symbol_bodies(
                fi, syms, kw, min(budget_remaining, max_file_tokens // 2)
            )
            if extra_tok > 0 and tokens_used + extra_tok <= budget:
                tokens_used += extra_tok

        # Redact secrets at materialization — before content reaches any renderer or adapter
        materialized = content if mode_str in ("full", "diff", "skeleton") else sym_body_content
        redaction_warnings: list[str] = []
        if materialized:
            materialized, redaction_warnings = redact_secrets(materialized, fi.path)

        selected_file = SelectedFile(
            path=fi.path,
            language=fi.language,
            score=score,
            include_mode=mode_str,
            reasons=reasons,
            content=materialized,
            summary=summary_data.get("summary") if summary_data else None,
            symbols=syms,
            redaction_warnings=redaction_warnings,
            source_hash=fi.hash,
        )
        selected_file.citations = selected_file_citations(fi, selected_file)
        selected.append(selected_file)
        selected_token_costs[fi.path] = tok

        action: Literal["included", "excluded", "summarized"] = (
            "included" if mode_str == "full" else "summarized"
        )
        receipts.append(_receipt(fi, action, ", ".join(reasons[:2])))

    return selected, receipts


def compact_selected_file_payloads(
    selected: list[SelectedFile],
    *,
    files: list[FileInfo],
    summaries: dict[str, Any],
    scored: list[tuple[FileInfo, float, list[str]]],
    task: str,
    changed_paths: set[str],
) -> list[SelectedFile]:
    """Shrink selected compressed files without changing the selected path set."""
    if not selected:
        return selected

    file_by_path = {fi.path: fi for fi in files}
    ranked_scored = sorted(scored, key=lambda item: item[1], reverse=True)
    scored_map = {
        fi.path: {"rank": rank, "score": score, "reasons": reasons}
        for rank, (fi, score, reasons) in enumerate(ranked_scored, 1)
    }

    compacted: list[SelectedFile] = []
    for sf in selected:
        replacement = _compact_selected_file_payload(
            sf,
            file_info=file_by_path.get(sf.path),
            summary_data=_summary_dict(summaries.get(sf.path)),
            scored_info=scored_map.get(sf.path),
            task=task,
            changed_paths=changed_paths,
        )
        compacted.append(replacement or sf)
    return compacted


def _compact_selected_file_payload(
    sf: SelectedFile,
    *,
    file_info: FileInfo | None,
    summary_data: dict[str, Any],
    scored_info: dict[str, Any] | None,
    task: str,
    changed_paths: set[str],
) -> SelectedFile | None:
    path = sf.path
    if not path or path in changed_paths or sf.include_mode not in {"summary", "skeleton", "symbols"}:
        return None
    current_tokens = _selected_payload_tokens(sf)
    if current_tokens <= 80:
        return None

    reasons = _combined_reasons(sf, scored_info)
    projections: list[tuple[int, str, str]] = []

    ast_projection = _ast_checkpoint_compaction_projection(
        sf,
        file_info=file_info,
        summary_data=summary_data,
        scored_info=scored_info,
        task=task,
        changed_paths=changed_paths,
        current_tokens=current_tokens,
        reasons=reasons,
    )
    if ast_projection is not None:
        projections.append(ast_projection)

    ranked_decision = _ranked_compaction_decision(
        path=path,
        mode=sf.include_mode,
        current_tokens=current_tokens,
        task=task,
        reasons=reasons,
        scored_info=scored_info,
    )
    if ranked_decision is not None:
        projection = _source_window_projection(
            file_info=file_info,
            path=path,
            task=task,
            reasons=reasons,
            symbols=list(sf.symbols) or _summary_symbols(summary_data),
            current_tokens=current_tokens,
            max_excerpt_tokens=int(ranked_decision["max_excerpt_tokens"]),
        )
        if projection is not None:
            projected_tokens, excerpt = projection
            projections.append((projected_tokens, excerpt, str(ranked_decision["tier"])))

    if not projections:
        return None
    projected_tokens, excerpt, tier = min(projections, key=lambda item: item[0])
    if projected_tokens <= 0 or projected_tokens >= current_tokens:
        return None

    compact_reason = f"source-aware compacted {tier}"
    reasons_out = sf.reasons if compact_reason in sf.reasons else [*sf.reasons, compact_reason]
    if sf.include_mode == "summary":
        updated = sf.model_copy(update={"summary": excerpt, "content": None, "reasons": reasons_out})
    else:
        updated = sf.model_copy(update={"content": excerpt, "reasons": reasons_out})
    if file_info is not None:
        updated.citations = selected_file_citations(file_info, updated)
    return updated


def _selected_payload_tokens(sf: SelectedFile) -> int:
    if sf.content:
        return estimate_tokens(sf.content)
    if sf.include_mode == "summary":
        return estimate_tokens(sf.summary) if sf.summary else 50
    parts: list[str] = []
    if sf.summary:
        parts.append(sf.summary)
    for sym in sf.symbols:
        if sym.signature:
            parts.append(sym.signature)
    return estimate_tokens("\n".join(parts)) if parts else 50


def _combined_reasons(sf: SelectedFile, scored_info: dict[str, Any] | None) -> list[str]:
    reasons = [str(reason) for reason in sf.reasons if reason]
    if scored_info:
        reasons.extend(str(reason) for reason in (scored_info.get("reasons") or []) if reason)
    return _dedupe_strings(reasons)


def _summary_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            return {}
    return {}


def _summary_symbols(summary_data: dict[str, Any]) -> list[Any]:
    raw = summary_data.get("symbols") or []
    return list(raw) if isinstance(raw, list) else []


def _ranked_compaction_decision(
    *,
    path: str,
    mode: str,
    current_tokens: int,
    task: str,
    reasons: list[str],
    scored_info: dict[str, Any] | None,
) -> dict[str, Any] | None:
    rank = int((scored_info or {}).get("rank") or 0)
    if mode not in {"summary", "skeleton"}:
        return None

    if current_tokens >= 120 and _is_test_path(path):
        test_skeleton = _ranked_test_skeleton_decision(path=path, reasons=reasons, rank=rank)
        test_symbol = _ranked_test_symbol_carrier_decision(path=path, task=task, reasons=reasons, rank=rank)
        decisions = [decision for decision in (test_skeleton, test_symbol) if decision is not None]
        if decisions:
            return min(decisions, key=lambda decision: int(decision["max_excerpt_tokens"]))

    if current_tokens >= 120 and _path_family(path) == "source":
        source_churn = _ranked_source_churn_decision(path=path, reasons=reasons, rank=rank)
        if source_churn is not None:
            return source_churn

    if current_tokens >= 80 and _benchmark_metadata_or_docs_task(task):
        source_metadata = _ranked_source_metadata_decision(path=path, reasons=reasons, rank=rank)
        metadata_summary = _ranked_metadata_summary_decision(path=path, mode=mode, task=task, reasons=reasons, rank=rank)
        decisions = [decision for decision in (source_metadata, metadata_summary) if decision is not None]
        if decisions:
            return min(decisions, key=lambda decision: int(decision["max_excerpt_tokens"]))

    config_summary = _ranked_config_summary_decision(path=path, mode=mode, reasons=reasons, rank=rank)
    if config_summary is not None:
        return config_summary

    return None


def _ranked_test_skeleton_decision(*, path: str, reasons: list[str], rank: int) -> dict[str, Any] | None:
    if rank < 3:
        return None
    action_risk = _ranked_test_skeleton_action_risk(path=path, reasons=reasons, rank=rank)
    if action_risk > 44:
        return None
    max_excerpt_tokens = 72
    if _has_reason_signal(reasons, "matched define:"):
        max_excerpt_tokens += 16
    if _has_reason_signal(reasons, "matched call:"):
        max_excerpt_tokens += 8
    return {"tier": "ranked_test_skeleton_carrier", "max_excerpt_tokens": max_excerpt_tokens}


def _ranked_test_skeleton_action_risk(*, path: str, reasons: list[str], rank: int) -> int:
    risk = 0
    if rank <= 1:
        risk += 24
    elif rank <= 2:
        risk += 16
    elif rank <= 4:
        risk += 8
    if _has_reason_signal(reasons, "direct content evidence"):
        risk += 28
    if _has_reason_signal(reasons, "explicit test task file"):
        risk += 30
    if _has_reason_signal(reasons, "direct dependency of changed file", "has related tests", "test for high-scoring"):
        risk += 16
    if _has_reason_signal(reasons, "matched entrypoint:"):
        risk += 24
    if _has_reason_signal(reasons, "quoted literal match", "literal definition match"):
        risk += 28
    if _has_reason_signal(reasons, "matched define:"):
        risk += 8
    if _has_reason_signal(reasons, "matched call:"):
        risk += 5
    if _content_keyword_hits(reasons) >= 4:
        risk += 8
    if _source_compaction_has_structural_risk(path):
        risk += 12
    return risk


def _ranked_test_symbol_carrier_decision(*, path: str, task: str, reasons: list[str], rank: int) -> dict[str, Any] | None:
    if rank <= 0 or rank > 3:
        return None
    if not _has_reason_signal(reasons, "matched define:"):
        return None
    if _has_reason_signal(reasons, "direct content evidence", "explicit test task file", "matched entrypoint:"):
        return None
    if _path_task_aligned(path=path, task=task):
        return None
    if "refactor" in _action_terms_from_task(task):
        return None
    max_excerpt_tokens = 72
    if _has_reason_signal(reasons, "matched define:"):
        max_excerpt_tokens += 16
    if _has_reason_signal(reasons, "matched call:"):
        max_excerpt_tokens += 8
    return {"tier": "ranked_test_symbol_carrier", "max_excerpt_tokens": max_excerpt_tokens}


def _ranked_source_churn_decision(*, path: str, reasons: list[str], rank: int) -> dict[str, Any] | None:
    if rank < 4:
        return None
    if _source_compaction_has_structural_risk(path):
        return None
    if _has_reason_signal(reasons, "quoted literal match", "literal definition match"):
        return None
    if not _has_reason_signal(reasons, "high churn"):
        return None
    max_excerpt_tokens = 88
    if _has_reason_signal(reasons, "matched define:"):
        max_excerpt_tokens += 20
    if _has_reason_signal(reasons, "matched call:"):
        max_excerpt_tokens += 12
    return {"tier": "ranked_source_churn_carrier", "max_excerpt_tokens": max_excerpt_tokens}


def _ranked_source_metadata_decision(*, path: str, reasons: list[str], rank: int) -> dict[str, Any] | None:
    if _path_family(path) != "source":
        return None
    if Path(path).name == "__init__.py":
        return None
    if _source_compaction_has_structural_risk(path):
        return None
    max_excerpt_tokens = 72
    if _has_reason_signal(reasons, "matched define:"):
        max_excerpt_tokens += 16
    if _has_reason_signal(reasons, "matched call:"):
        max_excerpt_tokens += 8
    return {"tier": "ranked_source_metadata_carrier", "max_excerpt_tokens": max_excerpt_tokens, "rank": rank}


def _ranked_metadata_summary_decision(
    *,
    path: str,
    mode: str,
    task: str,
    reasons: list[str],
    rank: int,
) -> dict[str, Any] | None:
    family = _path_family(path)
    if mode != "summary" or family not in {"config", "examples"}:
        return None
    if rank <= 0 or rank < 3:
        return None
    if _path_task_aligned(path=path, task=task):
        return None
    if _has_reason_signal(
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
        return None
    return {"tier": "ranked_metadata_summary_carrier", "max_excerpt_tokens": 24}


def _ranked_config_summary_decision(*, path: str, mode: str, reasons: list[str], rank: int) -> dict[str, Any] | None:
    if mode != "summary" or _path_family(path) != "config" or rank < 8:
        return None
    if _has_reason_signal(
        reasons,
        "direct content evidence",
        "direct dependency of changed file",
        "keyword phrase match",
        "quoted literal match",
        "literal definition match",
    ):
        return None
    return {"tier": "ranked_config_summary_carrier", "max_excerpt_tokens": 48}


def _ast_checkpoint_compaction_projection(
    sf: SelectedFile,
    *,
    file_info: FileInfo | None,
    summary_data: dict[str, Any],
    scored_info: dict[str, Any] | None,
    task: str,
    changed_paths: set[str],
    current_tokens: int,
    reasons: list[str],
) -> tuple[int, str, str] | None:
    symbols = list(sf.symbols) or _summary_symbols(summary_data)
    confidence = _source_compaction_confidence(
        path=sf.path,
        mode=sf.include_mode,
        reasons=reasons,
        current_tokens=current_tokens,
        changed_paths=changed_paths,
        symbols=symbols,
    )
    profile = _ast_checkpoint_profile(
        path=sf.path,
        mode=sf.include_mode,
        reasons=reasons,
        current_tokens=current_tokens,
        changed_paths=changed_paths,
        confidence=confidence,
        summary_data=summary_data,
        task=task,
        symbols=symbols,
        scored_info=scored_info,
    )
    if not bool(profile.get("compress")):
        return None
    max_tokens = int(profile.get("max_excerpt_tokens") or 160)
    projection = _symbol_span_projection(
        file_info=file_info,
        path=sf.path,
        task=task,
        reasons=reasons,
        symbols=symbols,
        current_tokens=current_tokens,
        max_excerpt_tokens=max_tokens,
    )
    if projection is None:
        projection = _source_window_projection(
            file_info=file_info,
            path=sf.path,
            task=task,
            reasons=reasons,
            symbols=symbols,
            current_tokens=current_tokens,
            max_excerpt_tokens=max_tokens,
        )
    if projection is None and bool(profile.get("allow_minimal_fallback")):
        projection = _minimal_compaction_projection(
            file_info=file_info,
            path=sf.path,
            current_tokens=current_tokens,
            max_excerpt_tokens=max_tokens,
            minimal_excerpt_tokens=48,
        )
    if projection is None:
        return None
    projected_tokens, excerpt = projection
    return projected_tokens, excerpt, str(profile.get("role") or "ast_checkpoint_carrier")


def _ast_checkpoint_profile(
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
    scored_info: dict[str, Any] | None,
) -> dict[str, Any]:
    content_hits = _content_keyword_hits(reasons)
    summary_counts = _summary_match_counts(summary_data, task=task, path=path, reasons=reasons)
    matching_symbols = _matching_symbols(symbols, task=task, path=path, reasons=reasons)
    memory_signal = _has_reason_signal(reasons, "episodic memory similar task", "learning feedback miss", "procedure=")
    structural_risk = bool(confidence.get("structural_risk"))
    direct_action_signal = bool(confidence.get("direct_action_signal"))
    release_metadata_signal = _has_reason_signal(reasons, "release/version metadata")
    literal_definition_signal = _has_reason_signal(reasons, "literal definition match:", "quoted literal match:")
    rank = int((scored_info or {}).get("rank") or 0)
    ranked_test_support = _ast_ranked_test_support_carrier(
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
    if (
        _path_family(path) == "source"
        and rank <= 1
        and current_tokens < 240
        and not release_metadata_signal
        and _has_reason_signal(reasons, "matched define:")
    ):
        owner_reasons.append("small_ranked_source_symbol_owner")
    if direct_action_signal and not release_metadata_signal and _has_reason_signal(
        reasons,
        "direct content evidence",
        "explicit test task file",
        "matched entrypoint:",
        "keyword phrase match:",
        "quoted literal match:",
        "literal definition match:",
    ):
        owner_reasons.append("direct_action_checkpoint")
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
    if literal_definition_signal and summary_counts.get("public_api", 0) > 0 and summary_counts.get("defines", 0) > 0:
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
        return {"role": "ast_checkpoint_owner", "compress": False, "owner_reasons": owner_reasons}

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
    if current_tokens >= 240 and not structural_risk and len(matching_symbols) <= 2 and summary_support > 0 and content_hits <= 2:
        carrier_reasons.append("concentrated_ast_carrier")
    if summary_counts.get("calls", 0) > 0 and summary_counts.get("defines", 0) == 0 and not structural_risk:
        carrier_reasons.append("callsite_only_carrier")

    return {
        "role": "ast_checkpoint_carrier" if carrier_reasons else "ast_checkpoint_uncertain",
        "compress": mode in {"summary", "skeleton", "symbols"} and current_tokens > 120 and bool(carrier_reasons),
        "reasons": _dedupe_strings(carrier_reasons),
        "memory_signal": memory_signal,
        "matching_symbol_count": len(matching_symbols),
        "max_excerpt_tokens": 180 if matching_symbols else 120,
        "allow_minimal_fallback": confidence.get("tier") == "weak" or ranked_test_support,
    }


def _source_window_projection(
    *,
    file_info: FileInfo | None,
    path: str,
    task: str,
    reasons: list[str],
    symbols: list[Any],
    current_tokens: int,
    max_excerpt_tokens: int,
) -> tuple[int, str] | None:
    text = _compaction_source_text(file_info)
    if not text:
        return None
    lines = text.splitlines()
    terms = _source_compaction_terms(task=task, path=path, reasons=reasons, symbols=symbols)
    ranges, _matched = _source_compaction_ranges(lines=lines, terms=terms, path=path, symbols=symbols)
    if not ranges:
        return None
    excerpt = _source_excerpt_from_ranges(lines, ranges, max_excerpt_tokens=max_excerpt_tokens)
    if not excerpt:
        return None
    projected_tokens = estimate_tokens(excerpt)
    if projected_tokens <= 0 or projected_tokens >= current_tokens:
        return None
    return projected_tokens, excerpt


def _symbol_span_projection(
    *,
    file_info: FileInfo | None,
    path: str,
    task: str,
    reasons: list[str],
    symbols: list[Any],
    current_tokens: int,
    max_excerpt_tokens: int,
) -> tuple[int, str] | None:
    text = _compaction_source_text(file_info)
    if not text:
        return None
    lines = text.splitlines()
    terms = _source_compaction_terms(task=task, path=path, reasons=reasons, symbols=symbols)
    if not terms:
        return None
    scored_ranges: list[tuple[float, tuple[int, int]]] = []
    for sym in symbols:
        start_line = _symbol_attr(sym, "start_line")
        end_line = _symbol_attr(sym, "end_line")
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        score, _matched = _symbol_score(sym, terms)
        if score <= 0:
            continue
        start = max(0, start_line - 1)
        end = min(len(lines), max(start + 1, end_line))
        scored_ranges.append((score, (_include_preceding_comments(lines, start), end)))
    if not scored_ranges:
        return None
    scored_ranges.sort(key=lambda item: (item[0], item[1][1] - item[1][0]), reverse=True)
    ranges = [*_header_ranges(lines, path), *[span for _score, span in scored_ranges[:4]]]
    excerpt = _source_excerpt_from_ranges(lines, _merge_excerpt_ranges(ranges), max_excerpt_tokens=max_excerpt_tokens)
    if not excerpt:
        return None
    projected_tokens = estimate_tokens(excerpt)
    if projected_tokens <= 0 or projected_tokens >= current_tokens:
        return None
    return projected_tokens, excerpt


def _minimal_compaction_projection(
    *,
    file_info: FileInfo | None,
    path: str,
    current_tokens: int,
    max_excerpt_tokens: int,
    minimal_excerpt_tokens: int,
) -> tuple[int, str] | None:
    if current_tokens <= minimal_excerpt_tokens:
        return None
    text = _compaction_source_text(file_info)
    if not text:
        return minimal_excerpt_tokens, f"# {path}\n# source excerpt omitted; inspect file if needed"
    lines = text.splitlines()
    ranges = _minimal_source_ranges(lines, path)
    excerpt = _source_excerpt_from_ranges(lines, ranges, max_excerpt_tokens=max_excerpt_tokens)
    if not excerpt:
        return minimal_excerpt_tokens, f"# {path}\n# source excerpt omitted; inspect file if needed"
    projected_tokens = estimate_tokens(excerpt)
    projected_tokens = max(minimal_excerpt_tokens, min(projected_tokens, max_excerpt_tokens))
    if projected_tokens >= current_tokens:
        return None
    return projected_tokens, excerpt


def _compaction_source_text(file_info: FileInfo | None) -> str:
    if file_info is None:
        return ""
    if file_info.content and file_info.content.strip():
        return file_info.content
    try:
        if file_info.abs_path.exists():
            return file_info.abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _source_compaction_terms(*, task: str, path: str, reasons: list[str], symbols: list[Any]) -> list[str]:
    raw_parts: list[str] = []
    raw_parts.extend(_split_compaction_terms(task))
    raw_parts.extend(_split_compaction_terms(Path(path).stem))
    for reason in reasons:
        if ":" in reason:
            raw_parts.extend(_split_compaction_terms(reason.split(":", 1)[1]))
        elif reason.startswith("matched ranking keyword"):
            raw_parts.extend(_split_compaction_terms(reason))
    for sym in symbols:
        for field in ("name", "signature", "summary"):
            value = _symbol_attr(sym, field)
            if value:
                raw_parts.extend(_split_compaction_terms(str(value)))
    return _dedupe_strings(raw_parts)[:40]


def _split_compaction_terms(value: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
    stopwords = {
        "add", "and", "are", "but", "can", "class", "code", "def", "file", "fix", "for", "from",
        "get", "has", "into", "new", "not", "set", "src", "test", "tests", "the", "this", "use",
        "uses", "using", "with", "without",
    } | _CALL_TARGET_STOPWORDS
    terms: list[str] = []
    for raw in re.split(r"[^A-Za-z0-9]+|_", spaced):
        term = raw.lower().strip()
        if len(term) < 3 or term in stopwords:
            continue
        if len(term) > 4 and term.endswith("s"):
            term = term[:-1]
        terms.append(term)
    return terms


def _source_compaction_ranges(
    *,
    lines: list[str],
    terms: list[str],
    path: str,
    symbols: list[Any],
) -> tuple[list[tuple[int, int]], list[str]]:
    ranges = [*_header_ranges(lines, path)]
    matched_terms: list[str] = []
    lowered_terms = [term.lower() for term in terms if len(term) >= 3]
    for index, line in enumerate(lines):
        line_lc = line.lower()
        matched = [term for term in lowered_terms if term in line_lc]
        if not matched:
            continue
        start, end = _source_enclosing_block(lines, index)
        ranges.append((start, end))
        matched_terms.extend(matched)
        if len(ranges) >= 8:
            break
    for sym in symbols:
        name = str(_symbol_attr(sym, "name") or "")
        start_line = _symbol_attr(sym, "start_line")
        end_line = _symbol_attr(sym, "end_line")
        if not name or not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        name_terms = _split_compaction_terms(name)
        if any(term in lowered_terms for term in name_terms):
            ranges.append((max(0, start_line - 1), min(len(lines), max(start_line, end_line))))
            matched_terms.extend(name_terms)
    return _merge_excerpt_ranges(ranges), _dedupe_strings(matched_terms)


def _header_ranges(lines: list[str], path: str) -> list[tuple[int, int]]:
    suffix = Path(path).suffix.lower()
    ranges: list[tuple[int, int]] = []
    pattern = re.compile(r"^\s*(from\s+\S+\s+import\s+|import\s+|package\s+|use\s+|require\(|#include\s+|using\s+|namespace\s+)")
    for index, line in enumerate(lines[:100]):
        if suffix == ".go" and index == 0 and line.strip().startswith("package "):
            ranges.append((index, index + 1))
            continue
        if pattern.match(line):
            ranges.append((index, index + 1))
            continue
        if ranges and index > max(end for _start, end in ranges) + 12:
            break
    return _merge_excerpt_ranges(ranges)


def _minimal_source_ranges(lines: list[str], path: str) -> list[tuple[int, int]]:
    ranges = _header_ranges(lines, path)
    if ranges:
        return ranges
    for index, line in enumerate(lines[:80]):
        if line.strip() and not line.strip().startswith(("#", "//", "/*", "*")):
            return [(max(0, index - 1), min(len(lines), index + 3))]
    return [(0, min(len(lines), 4))]


def _source_enclosing_block(lines: list[str], index: int) -> tuple[int, int]:
    start = max(0, index - 2)
    end = min(len(lines), index + 6)
    line = lines[index]
    stripped = line.lstrip()
    if re.match(r"(async\s+def|def|class)\s+", stripped):
        indent = len(line) - len(stripped)
        end = index + 1
        while end < len(lines):
            next_line = lines[end]
            next_stripped = next_line.lstrip()
            if next_stripped and len(next_line) - len(next_stripped) <= indent and re.match(r"(async\s+def|def|class)\s+", next_stripped):
                break
            end += 1
        return _include_preceding_comments(lines, index), min(len(lines), end)
    return start, end


def _include_preceding_comments(lines: list[str], start: int) -> int:
    cursor = start - 1
    lower_bound = max(0, start - 6)
    while cursor >= lower_bound:
        stripped = lines[cursor].strip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*", "@")):
            cursor -= 1
            continue
        break
    return cursor + 1


def _source_excerpt_from_ranges(lines: list[str], ranges: list[tuple[int, int]], *, max_excerpt_tokens: int) -> str:
    chunks: list[str] = []
    used = 0
    for start, end in _merge_excerpt_ranges(ranges):
        chunk = "\n".join(lines[start:end]).strip()
        if not chunk:
            continue
        tokens = estimate_tokens(chunk)
        if chunks and used + tokens > max_excerpt_tokens:
            break
        if not chunks and tokens > max_excerpt_tokens:
            kept: list[str] = []
            for line in chunk.splitlines():
                trial = "\n".join([*kept, line])
                if estimate_tokens(trial) > max_excerpt_tokens:
                    break
                kept.append(line)
            chunk = "\n".join(kept).strip()
            tokens = estimate_tokens(chunk)
        if tokens <= 0 or used + tokens > max_excerpt_tokens:
            continue
        chunks.append(chunk)
        used += tokens
    return "\n\n".join(chunks).strip()


def _merge_excerpt_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    clean = sorted((max(0, start), max(0, end)) for start, end in ranges if end > start)
    merged: list[tuple[int, int]] = []
    for start, end in clean:
        if not merged or start > merged[-1][1] + 2:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _source_compaction_confidence(
    *,
    path: str,
    mode: str,
    reasons: list[str],
    current_tokens: int,
    changed_paths: set[str],
    symbols: list[Any],
) -> dict[str, Any]:
    family = _path_family(path)
    content_hits = _content_keyword_hits(reasons)
    direct, graph, structural, symbolic = _compaction_support_signals(reasons)
    direct_symbol = _has_reason_signal(
        reasons,
        "matched call:",
        "matched define:",
        "matched entrypoint:",
        "matched env read:",
        "matched side effect:",
        "quoted literal match:",
        "literal definition match:",
    )
    related_test = _has_reason_signal(
        reasons,
        "has related tests",
        "related test",
        "test for high-scoring",
        "explicit test task file",
    )
    broad_local = _has_reason_signal(
        reasons,
        "filename keyword match",
        "symbol keyword match",
        "matched ranking keyword:",
        "matched role keyword:",
    )
    supported = direct or graph or structural or symbolic or direct_symbol or related_test
    structural_risk = _source_compaction_has_structural_risk(path)
    hub_path = _compaction_is_hub_path(path)
    direct_action_signal = _source_compaction_has_direct_action_signal(reasons)
    content_only = content_hits > 0 and not supported
    recent_only = _has_reason_signal(reasons, "recently modified") and not supported
    churn_only = _has_reason_signal(reasons, "high churn") and not supported
    confirmation_count = sum([
        bool(direct),
        bool(graph),
        bool(structural),
        bool(symbolic),
        bool(direct_symbol),
        bool(related_test),
        content_hits >= 3,
    ])

    score = 0.0
    changed = path in changed_paths
    if changed:
        score += 100.0
    if direct:
        score += 42.0
    if direct_symbol:
        score += 35.0
    if graph:
        score += 30.0
    if structural:
        score += 24.0
    if symbolic:
        score += 18.0
    if related_test:
        score += 26.0
    if content_hits:
        score += min(content_hits, 6) * 5.0
    if mode == "full":
        score += 10.0
    if len(symbols) >= 3 and not broad_local:
        score += 8.0
    if family in {"config", "docs", "examples", "fixtures"} and not structural and not direct_symbol:
        score -= 18.0
    if hub_path and confirmation_count < 2:
        score -= 30.0
    if structural_risk and confirmation_count >= 1:
        score += 14.0
    if content_only:
        score -= 32.0
    if broad_local and not supported:
        score -= 18.0
    if recent_only:
        score -= 10.0
    if churn_only:
        score -= 10.0
    if family == "test" and not related_test and not direct_symbol and confirmation_count < 2:
        score -= 16.0
    if current_tokens > 320 and confirmation_count <= 1:
        score -= 10.0

    if changed or related_test or (direct_symbol and confirmation_count >= 2) or (score >= 72.0 and confirmation_count >= 2):
        tier = "strong"
    elif score >= 30.0 or direct_symbol or graph or structural or symbolic or content_hits >= 3:
        tier = "medium"
    else:
        tier = "weak"
    strong_owner, carrier_reasons = _source_compaction_strong_role(
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
    strong_carrier = tier == "strong" and not strong_owner and bool(carrier_reasons)
    guarded_strong_carrier = strong_carrier and _source_compaction_guarded_strong_carrier(
        carrier_reasons=carrier_reasons,
        structural_risk=structural_risk,
    )
    return {
        "tier": tier,
        "structural_risk": structural_risk,
        "confirmation_count": confirmation_count,
        "direct_action_signal": direct_action_signal,
        "strong_action_owner": strong_owner if tier == "strong" else False,
        "strong_carrier": strong_carrier,
        "guarded_strong_carrier": guarded_strong_carrier,
    }


def _compaction_support_signals(reasons: list[str]) -> tuple[bool, bool, bool, bool]:
    direct = _has_reason_signal(
        reasons,
        "direct content evidence",
        "keyword phrase match:",
        "literal definition match:",
        "matched call:",
        "matched define:",
        "multi-token",
        "quoted literal match:",
    )
    graph = _has_reason_signal(
        reasons,
        "caller of",
        "direct dependency",
        "related test",
        "reverse dependency",
        "test for high-scoring",
        "workspace match",
    )
    structural = _has_reason_signal(
        reasons,
        "cross-layer related",
        "recall neighbor",
        "second-pass recall neighbor",
        "build/dependency metadata",
        "matched external system:",
        "conventional scope path match",
    )
    symbolic = _has_reason_signal(reasons, "symbol keyword match") and _has_reason_signal(
        reasons,
        "matched role keyword:",
        "matched ranking keyword:",
        "conventional scope path match",
    )
    return direct, graph, structural, symbolic


def _compaction_is_hub_path(path: str) -> bool:
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


def _source_compaction_strong_role(
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
    if direct_symbol and confirmation_count >= 2 and current_tokens < 240 and not hub_path and not structural_risk and not broad_local:
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
    if family == "test" and direct_symbol and not related_test:
        carrier_reasons.append("support_file_symbol_carrier")
    if graph and not related_test and not direct_action_signal:
        carrier_reasons.append("dependency_neighbor_carrier")
    if symbolic and not direct_action_signal and not graph:
        carrier_reasons.append("symbolic_surface_carrier")
    if structural and not direct_action_signal and not graph:
        carrier_reasons.append("structural_neighbor_carrier")
    return False, _dedupe_strings(carrier_reasons)


def _source_compaction_guarded_strong_carrier(*, carrier_reasons: list[str], structural_risk: bool) -> bool:
    carrier_reason_set = set(carrier_reasons)
    return (
        "support_file_symbol_carrier" in carrier_reason_set
        or ("hub_symbol_carrier" in carrier_reason_set and not structural_risk)
    )


def _ast_ranked_test_support_carrier(
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
    return _has_reason_signal(reasons, "filename keyword match") and _has_reason_signal(reasons, "matched ranking keyword:")


def _summary_match_counts(summary_data: dict[str, Any], *, task: str, path: str, reasons: list[str]) -> dict[str, int]:
    terms = set(_source_compaction_terms(task=task, path=path, reasons=reasons, symbols=[]))
    counts: dict[str, int] = {}
    for field in (
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
        hits = sum(1 for value in _summary_values(summary_data, field) if _value_matches_terms(value, terms))
        if hits:
            counts[field] = hits
    return counts


def _summary_values(summary_data: dict[str, Any], field: str) -> list[str]:
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
                out.extend(str(part) for part in (getattr(item, "name", None), getattr(item, "signature", None), getattr(item, "summary", None)) if part)
        return out
    return []


def _matching_symbols(symbols: list[Any], *, task: str, path: str, reasons: list[str]) -> list[Any]:
    terms = _source_compaction_terms(task=task, path=path, reasons=reasons, symbols=symbols)
    return [sym for sym in symbols if _symbol_score(sym, terms)[0] > 0]


def _symbol_score(sym: Any, terms: list[str]) -> tuple[float, list[str]]:
    buckets = (
        (str(_symbol_attr(sym, "name") or ""), 5.0),
        (str(_symbol_attr(sym, "signature") or ""), 4.0),
        (str(_symbol_attr(sym, "summary") or ""), 3.0),
        (str(_symbol_attr(sym, "body") or "")[:4000], 1.0),
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


def _symbol_attr(sym: Any, field: str) -> Any:
    if isinstance(sym, dict):
        return sym.get(field)
    return getattr(sym, field, None)


def _value_matches_terms(value: str, terms: set[str]) -> bool:
    value_lc = value.lower()
    return any(term in value_lc for term in terms if len(term) >= 3)


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
        "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml",
        "go.mod", "go.sum", "pom.xml", "build.gradle", "gradle.properties",
    } or suffix in {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg"}:
        return "config"
    if suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt", ".rs", ".rb", ".php", ".cs"}:
        return "source"
    return "other"


_SOURCE_COMPACTION_STRUCTURAL_RISK_WORDS = {
    "adapter", "binding", "client", "command", "context", "controller", "core", "engine",
    "handler", "manager", "model", "models", "parser", "provider", "registry", "router",
    "schema", "serializer", "service", "store", "type", "types",
}


def _source_compaction_has_structural_risk(path: str) -> bool:
    tokens: set[str] = set()
    for part in Path(path).parts:
        tokens.update(_split_compaction_terms(part))
    return bool(tokens & _SOURCE_COMPACTION_STRUCTURAL_RISK_WORDS)


def _source_compaction_has_direct_action_signal(reasons: list[str]) -> bool:
    return _has_reason_signal(
        reasons,
        "direct content evidence",
        "explicit test task file",
        "matched entrypoint:",
        "quoted literal match",
        "literal definition match",
    )


def _benchmark_metadata_or_docs_task(task: str) -> bool:
    task_lc = str(task).lower()
    if task_lc.startswith(("docs:", "doc:", "ci:")):
        return True
    terms = _action_terms_from_task(task)
    return bool(terms & {"doc", "docs", "document", "documentation", "license", "metadata", "release", "typo", "version"})


def _path_task_aligned(*, path: str, task: str) -> bool:
    return bool(_action_terms_from_path(path) & _action_terms_from_task(task))


def _action_terms_from_path(path: str) -> set[str]:
    terms: set[str] = set()
    for part in Path(path).parts:
        stem = Path(part).stem
        if stem.lower() in {"src", "test", "tests", "pkg", "packages", "node", "main", "spec", "e2e"}:
            continue
        terms.update(_action_terms(stem))
    return terms


def _action_terms_from_task(task: str) -> set[str]:
    terms = _action_terms(task)
    if terms & {"completion", "completions", "fish", "shell", "zsh"}:
        terms.update({"completion", "completions", "shell"})
    if terms & {"logger", "logging"}:
        terms.update({"log", "logger"})
    if "response" in terms:
        terms.update({"response", "writer"})
    if "context" in terms:
        terms.add("context")
    return terms


def _action_terms(value: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
    stopwords = {"test", "tests", "src", "pkg", "packages", "spec", "e2e", "unit"} | set(_CALL_TARGET_STOPWORDS)
    terms: set[str] = set()
    for raw in re.split(r"[^A-Za-z0-9]+|_", spaced):
        term = raw.lower().strip()
        if len(term) < 3 or term in stopwords:
            continue
        if len(term) > 4 and term.endswith("s"):
            term = term[:-1]
        terms.add(term)
    return terms


def _has_reason_signal(reasons: list[str], *needles: str) -> bool:
    return any(reason.startswith(needle) or needle in reason for reason in reasons for needle in needles)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _receipt(
    fi: FileInfo,
    action: Literal["included", "excluded", "summarized"],
    reason: str,
) -> Receipt:
    return Receipt(
        path=fi.path,
        action=action,
        reason=reason,
        citations=[file_citation(fi, kind="receipt", claim_id=f"receipt:{fi.path}", note=reason)],
    )


def _receipt_citations(path: str, files: dict[str, FileInfo]) -> list[Citation]:
    fi = files.get(path)
    if fi is None:
        return []
    return [file_citation(fi, kind="receipt", claim_id=f"receipt:{path}", note="displaced selected file")]



def _has_redactable_secret(fi: FileInfo) -> bool:
    text = fi.content
    if text is None and fi.abs_path.exists():
        try:
            text = fi.abs_path.read_text(errors="replace")
        except OSError:
            return False
    if not text:
        return False
    _redacted, warnings = redact_secrets(text, fi.path)
    return bool(warnings)


def _budget_fallback(
    *,
    mode_str: Literal["full", "diff", "symbols", "skeleton", "summary"],
    fi: FileInfo,
    summary_data: dict[str, Any] | None,
    skeleton: str | None,
    skeleton_tokens: int,
    current_tokens: int,
    budget: int,
    max_file_tokens: int,
) -> tuple[Literal["skeleton", "summary"], str | None, int, str] | None:
    """Downgrade high-value files instead of dropping them when budget is tight."""
    if mode_str == "summary":
        return None
    candidates: list[tuple[Literal["skeleton", "summary"], str | None, int, str]] = []
    if skeleton:
        candidates.append(("skeleton", skeleton, skeleton_tokens, "value optimizer downgraded to skeleton"))
    if summary_data:
        summary_tok = _summary_tokens(summary_data, min(fi.estimated_tokens, 200))
        candidates.append(("summary", None, summary_tok, "value optimizer downgraded to summary"))
    else:
        candidates.append(("summary", None, min(fi.estimated_tokens, min(200, max_file_tokens)), "value optimizer downgraded to summary"))

    for candidate in sorted(candidates, key=lambda item: item[2]):
        if current_tokens + candidate[2] <= budget:
            return candidate
    return None
