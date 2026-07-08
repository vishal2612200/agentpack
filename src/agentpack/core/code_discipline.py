from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
}
_TEST_PATH_RE = re.compile(r"(^|/)(tests?|specs?)/|(^|/)(test_|spec_)|(_test|_spec)\.")
_PYTHON_SYMBOL_KINDS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_JS_DEF_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:(?:async\s+)?function\s+"
    r"(?P<fn>[A-Za-z_$][\w$]*)|class\s+(?P<class>[A-Za-z_$][\w$]*)|"
    r"(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
    r"(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>|\{|\[))"
)


@dataclass(frozen=True)
class CodeDisciplineIssue:
    """One actionable warning about code minimality or missing intent anchors."""

    kind: str
    severity: str
    path: str
    line: int
    symbol: str
    message: str
    remediation: str


@dataclass(frozen=True)
class CodeDisciplineReport:
    """Structured code-discipline result that commands can print or persist."""

    diff_source: str
    additions: int = 0
    deletions: int = 0
    files_changed: int = 0
    source_files_changed: int = 0
    test_files_changed: int = 0
    new_files: int = 0
    issues: list[CodeDisciplineIssue] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "diff_source": self.diff_source,
            "stats": {
                "additions": self.additions,
                "deletions": self.deletions,
                "files_changed": self.files_changed,
                "source_files_changed": self.source_files_changed,
                "test_files_changed": self.test_files_changed,
                "new_files": self.new_files,
            },
            "issues": [issue.__dict__ for issue in self.issues],
        }


def assess_code_discipline(root: Path, *, diff_range: str | None = None) -> CodeDisciplineReport:
    """Assess changed code for minimality and definition-level intent anchors."""
    diff_source = diff_range or "worktree"
    diff_text = _git_diff(root, diff_range=diff_range)
    added_lines, new_files = _parse_added_lines(diff_text)
    if diff_range is None:
        _include_untracked_changed_files(root, added_lines, new_files)
    stats = _diff_stats(root, diff_range=diff_range, added_lines=added_lines, new_files=new_files)
    issues: list[CodeDisciplineIssue] = []
    issues.extend(_bloat_issues(stats))
    issues.extend(_symbol_anchor_issues(root, added_lines))
    return CodeDisciplineReport(
        diff_source=diff_source,
        additions=stats["additions"],
        deletions=stats["deletions"],
        files_changed=stats["files_changed"],
        source_files_changed=stats["source_files_changed"],
        test_files_changed=stats["test_files_changed"],
        new_files=stats["new_files"],
        issues=issues,
    )


def format_code_discipline_report(report: CodeDisciplineReport, *, max_issues: int = 8) -> list[str]:
    """Render concise, command-safe report lines."""
    if not report.has_issues:
        return [
            "✓ Code discipline clean: no minimality or definition-anchor warnings.",
        ]
    lines = [
        "Code discipline warnings:",
        (
            f"- Diff source: {report.diff_source}; +{report.additions}/-{report.deletions}; "
            f"{report.files_changed} files changed; {report.test_files_changed} test files"
        ),
    ]
    for issue in report.issues[:max_issues]:
        location = f"{issue.path}:{issue.line}" if issue.line else issue.path
        symbol = f" `{issue.symbol}`" if issue.symbol else ""
        lines.append(f"- [{issue.severity}] {issue.kind} at {location}{symbol}: {issue.message}")
        lines.append(f"  Fix: {issue.remediation}")
    remaining = len(report.issues) - max_issues
    if remaining > 0:
        lines.append(f"- ... {remaining} more warnings omitted")
    return lines


def _git_diff(root: Path, *, diff_range: str | None) -> str:
    if diff_range:
        return _run_git(root, ["diff", "--unified=0", diff_range, "--"])
    unstaged = _run_git(root, ["diff", "--unified=0", "--"])
    staged = _run_git(root, ["diff", "--cached", "--unified=0", "--"])
    return "\n".join(part for part in (staged, unstaged) if part)


def _parse_added_lines(diff_text: str) -> tuple[dict[str, dict[int, str]], set[str]]:
    added: dict[str, dict[int, str]] = {}
    new_files: set[str] = set()
    current_path = ""
    new_line: int | None = None
    current_is_new = False
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            current_path = _path_from_diff_header(raw_line)
            current_is_new = False
            new_line = None
            continue
        if raw_line.startswith("new file mode "):
            current_is_new = True
            if current_path:
                new_files.add(current_path)
            continue
        if raw_line.startswith("+++ b/"):
            current_path = raw_line[6:]
            if current_is_new:
                new_files.add(current_path)
            continue
        if raw_line.startswith("@@ "):
            new_line = _new_start_from_hunk(raw_line)
            continue
        if new_line is None or not current_path:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added.setdefault(current_path, {})[new_line] = raw_line[1:]
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        else:
            new_line += 1
    return added, new_files


def _diff_stats(
    root: Path,
    *,
    diff_range: str | None,
    added_lines: dict[str, dict[int, str]],
    new_files: set[str],
) -> dict[str, int]:
    numstat = _run_git(root, ["diff", "--numstat", diff_range, "--"] if diff_range else ["diff", "--numstat", "--"])
    if not diff_range:
        cached = _run_git(root, ["diff", "--cached", "--numstat", "--"])
        numstat = "\n".join(part for part in (cached, numstat) if part)

    additions = deletions = 0
    files: set[str] = set(added_lines)
    for raw_line in numstat.splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 3:
            continue
        add, delete, path = parts[0], parts[1], parts[2]
        files.add(path)
        additions += _safe_int(add)
        deletions += _safe_int(delete)

    source_files = {path for path in files if _is_source_path(path)}
    test_files = {path for path in files if _is_test_path(path)}
    return {
        "additions": additions or sum(len(lines) for lines in added_lines.values()),
        "deletions": deletions,
        "files_changed": len(files),
        "source_files_changed": len(source_files),
        "test_files_changed": len(test_files),
        "new_files": len(new_files),
    }


def _bloat_issues(stats: dict[str, int]) -> list[CodeDisciplineIssue]:
    issues: list[CodeDisciplineIssue] = []
    if stats["additions"] >= 150:
        issues.append(
            CodeDisciplineIssue(
                kind="large-diff",
                severity="warning",
                path="<diff>",
                line=0,
                symbol="",
                message=f"diff adds {stats['additions']} lines; review for unnecessary code or split points",
                remediation="trim generated/bloated code, split unrelated changes, or document why the larger diff is necessary",
            )
        )
    if stats["new_files"] >= 6:
        issues.append(
            CodeDisciplineIssue(
                kind="many-new-files",
                severity="warning",
                path="<diff>",
                line=0,
                symbol="",
                message=f"diff adds {stats['new_files']} files; broad file growth raises review and rollback cost",
                remediation="collapse unnecessary files or explain the ownership boundary that requires each new file",
            )
        )
    if stats["source_files_changed"] and not stats["test_files_changed"] and stats["additions"] >= 20:
        issues.append(
            CodeDisciplineIssue(
                kind="missing-tests",
                severity="warning",
                path="<diff>",
                line=0,
                symbol="",
                message="source changed without nearby test/spec changes in this diff",
                remediation="add focused tests or record the exact manual validation path before review",
            )
        )
    return issues


def _symbol_anchor_issues(root: Path, added_lines: dict[str, dict[int, str]]) -> list[CodeDisciplineIssue]:
    issues: list[CodeDisciplineIssue] = []
    for path, lines in added_lines.items():
        full_path = root / path
        if not full_path.is_file() or not _is_source_path(path):
            continue
        if full_path.suffix == ".py":
            issues.extend(_python_anchor_issues(full_path, path, set(lines)))
        elif full_path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
            issues.extend(_javascript_anchor_issues(full_path, path, lines))
    return issues


def _include_untracked_changed_files(root: Path, added_lines: dict[str, dict[int, str]], new_files: set[str]) -> None:
    out = _run_git(root, ["ls-files", "--others", "--exclude-standard"])
    for raw_path in out.splitlines():
        path = raw_path.strip()
        full_path = root / path
        if not path or not full_path.is_file() or not (_is_source_path(path) or _is_test_path(path)):
            continue
        text_lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        added_lines[path] = {index: line for index, line in enumerate(text_lines, start=1)}
        new_files.add(path)


def _python_anchor_issues(full_path: Path, path: str, added_line_numbers: set[int]) -> list[CodeDisciplineIssue]:
    try:
        source = full_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except SyntaxError:
        return []
    file_lines = source.splitlines()
    issues: list[CodeDisciplineIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, _PYTHON_SYMBOL_KINDS):
            if not _node_intersects_added_lines(node, added_line_numbers):
                continue
            if not _is_non_trivial_python_node(node):
                continue
            symbol = node.name
            anchor = ast.get_docstring(node) or _nearby_comment(file_lines, node.lineno)
            issues.extend(_anchor_issue_if_needed(path, node.lineno, symbol, anchor))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and _is_module_level_node(tree, node):
            if node.lineno not in added_line_numbers:
                continue
            symbol = _python_assignment_symbol(node)
            if not symbol or not _is_non_trivial_variable(symbol, node):
                continue
            anchor = _nearby_comment(file_lines, node.lineno)
            issues.extend(_anchor_issue_if_needed(path, node.lineno, symbol, anchor))
    return issues


def _javascript_anchor_issues(full_path: Path, path: str, added_lines: dict[int, str]) -> list[CodeDisciplineIssue]:
    file_lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
    issues: list[CodeDisciplineIssue] = []
    for line_number, text in added_lines.items():
        match = _JS_DEF_RE.match(text)
        if not match:
            continue
        symbol = match.group("fn") or match.group("class") or match.group("var") or ""
        anchor = _nearby_comment(file_lines, line_number)
        issues.extend(_anchor_issue_if_needed(path, line_number, symbol, anchor))
    return issues


def _anchor_issue_if_needed(path: str, line: int, symbol: str, anchor: str | None) -> list[CodeDisciplineIssue]:
    if not anchor:
        return [
            CodeDisciplineIssue(
                kind="missing-intent-anchor",
                severity="warning",
                path=path,
                line=line,
                symbol=symbol,
                message="non-trivial changed definition has no nearby intent comment or docstring",
                remediation="add a short comment/docstring explaining why the symbol exists, its contract, or its invariant",
            )
        ]
    if _low_quality_anchor(anchor, symbol):
        return [
            CodeDisciplineIssue(
                kind="weak-intent-anchor",
                severity="warning",
                path=path,
                line=line,
                symbol=symbol,
                message="definition anchor appears to restate the symbol instead of preserving intent",
                remediation="replace it with the why, contract, invariant, or rollback-sensitive behavior",
            )
        ]
    return []


def _node_intersects_added_lines(node: ast.AST, added_line_numbers: set[int]) -> bool:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start)
    return any(start <= line <= end for line in added_line_numbers)


def _is_non_trivial_python_node(node: ast.AST) -> bool:
    if isinstance(node, ast.ClassDef):
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        length = getattr(node, "end_lineno", node.lineno) - node.lineno + 1
        return length >= 3 or not node.name.startswith("_")
    return False


def _is_module_level_node(tree: ast.Module, target_node: ast.AST) -> bool:
    return any(node is target_node for node in tree.body)


def _python_assignment_symbol(node: ast.Assign | ast.AnnAssign) -> str:
    target: ast.expr | None = None
    if isinstance(node, ast.Assign) and node.targets:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    return target.id if isinstance(target, ast.Name) else ""


def _is_non_trivial_variable(symbol: str, node: ast.Assign | ast.AnnAssign) -> bool:
    if symbol.startswith("_"):
        return False
    important_name = symbol.isupper() or any(term in symbol.lower() for term in ("config", "schema", "contract", "registry", "map"))
    complex_value = isinstance(getattr(node, "value", None), (ast.Dict, ast.List, ast.Tuple, ast.Set, ast.Call))
    return important_name and complex_value


def _nearby_comment(lines: list[str], line_number: int) -> str | None:
    index = line_number - 1
    if 0 <= index < len(lines) and "#" in lines[index]:
        return lines[index].split("#", 1)[1].strip()
    comments: list[str] = []
    for cursor in range(index - 1, max(-1, index - 4), -1):
        if cursor < 0:
            break
        stripped = lines[cursor].strip()
        if not stripped:
            continue
        if stripped.startswith("@"):
            continue
        if stripped.startswith(("#", "//", "/*", "*")):
            comments.append(_strip_comment(stripped))
            continue
        break
    if not comments:
        return None
    return " ".join(reversed([comment for comment in comments if comment]))


def _strip_comment(text: str) -> str:
    return text.strip().lstrip("#/ *").rstrip("*/").strip()


def _low_quality_anchor(anchor: str, symbol: str) -> bool:
    normalized = re.sub(r"[_\W]+", " ", anchor.lower()).strip()
    if len(normalized.split()) < 4:
        return True
    symbol_words = set(re.sub(r"[_\W]+", " ", symbol.lower()).split())
    anchor_words = set(normalized.split())
    return bool(symbol_words) and anchor_words <= (symbol_words | {"the", "a", "an", "this", "function", "class", "value"})


def _path_from_diff_header(line: str) -> str:
    parts = line.split(" ")
    if len(parts) >= 4 and parts[3].startswith("b/"):
        return parts[3][2:]
    return ""


def _new_start_from_hunk(line: str) -> int | None:
    match = re.search(r"\+(\d+)(?:,\d+)?", line)
    return int(match.group(1)) if match else None


def _is_source_path(path: str) -> bool:
    return Path(path).suffix in _SOURCE_EXTENSIONS and not _is_test_path(path)


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _run_git(root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""
