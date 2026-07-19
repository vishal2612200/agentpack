from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def is_git_repo(root: Path) -> bool:
    out = _run(["git", "rev-parse", "--is-inside-work-tree"], root)
    return out is not None and out.strip() == "true"


def git_path(root: Path, name: str) -> Path | None:
    """Resolve a repository metadata path for ordinary and linked worktrees."""
    out = _run(["git", "rev-parse", "--git-path", name], root)
    if not out or not out.strip():
        return None
    path = Path(out.strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def changed_files(root: Path) -> set[str]:
    """Unstaged + staged modified/added files."""
    result: set[str] = set()
    for args in [
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
    ]:
        out = _run(args, root)
        if out:
            for line in out.splitlines():
                line = line.strip()
                if line:
                    result.add(line)
    return result


def untracked_files(root: Path) -> set[str]:
    out = _run(["git", "status", "--short"], root)
    result: set[str] = set()
    if not out:
        return result
    for line in out.splitlines():
        if line.startswith("??"):
            result.add(line[3:].strip())
    return result


def recently_modified_files(root: Path, n: int = 20) -> list[str]:
    out = _run(
        ["git", "log", "--diff-filter=M", "--name-only", "--format=", f"-{n}"],
        root,
    )
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def changed_files_since(root: Path, ref: str) -> set[str]:
    """Files changed between ref and HEAD (e.g. ref='HEAD~1', ref='main')."""
    result: set[str] = set()
    out = _run(["git", "diff", "--name-only", ref, "HEAD"], root)
    if out:
        for line in out.splitlines():
            line = line.strip()
            if line:
                result.add(line)
    return result


def diff_name_status(root: Path, since: str | None = None) -> dict[str, str]:
    """Return changed path -> change kind for learning/reporting commands."""
    result: dict[str, str] = {}
    args = ["git", "diff", "--name-status"]
    if since:
        args.extend([since, "HEAD"])
    out = _run(args, root)
    if out:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status, path = parts[0], parts[-1]
                result[path] = _status_label(status)

    untracked_out = _run(["git", "ls-files", "--others", "--exclude-standard"], root)
    if untracked_out:
        for line in untracked_out.splitlines():
            path = line.strip()
            if path:
                result[path] = "untracked"
    return result


def diff_name_status_since_date(root: Path, since_date: str) -> dict[str, str]:
    """Return changed path -> change kind for commits since an ISO date plus dirty files."""
    result: dict[str, str] = {}
    out = _run(["git", "log", f"--since={since_date}", "--name-status", "--format="], root)
    if out:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status, path = parts[0], parts[-1]
                result[path] = _status_label(status)
    result.update(diff_name_status(root))
    return result


def file_diff(root: Path, path: str, *, since: str | None = None, max_chars: int = 1200) -> tuple[str, list[str]]:
    """Return a redacted, bounded diff for one file."""
    from agentpack.core.redactor import redact_secrets

    args = ["git", "diff", "--", path]
    if since:
        args = ["git", "diff", since, "HEAD", "--", path]
    out = _run(args, root) or ""
    if not out and (root / path).exists():
        out = (root / path).read_text(encoding="utf-8", errors="replace")
    redacted, warnings = redact_secrets(out[:max_chars], path)
    if len(out) > max_chars:
        redacted += "\n[diff truncated]\n"
    return redacted, warnings


def file_diff_since_date(root: Path, path: str, since_date: str, *, max_chars: int = 1200) -> tuple[str, list[str]]:
    """Return redacted diff for one path since an ISO date, falling back to file text."""
    from agentpack.core.redactor import redact_secrets

    base = _run(["git", "rev-list", "-n", "1", f"--before={since_date}", "HEAD"], root)
    base_ref = base.strip() if base else ""
    out = _run(["git", "diff", base_ref, "HEAD", "--", path], root) if base_ref else ""
    dirty_out = _run(["git", "diff", "--", path], root) or ""
    if dirty_out:
        out = (out or "") + ("\n" if out else "") + dirty_out
    if not out and (root / path).is_file():
        out = (root / path).read_text(encoding="utf-8", errors="replace")
    redacted, warnings = redact_secrets((out or "")[:max_chars], path)
    if out and len(out) > max_chars:
        redacted += "\n[diff truncated]\n"
    return redacted, warnings


def _status_label(status: str) -> str:
    code = status[:1]
    if code == "A":
        return "added"
    if code == "D":
        return "deleted"
    if code == "R":
        return "renamed"
    if code == "C":
        return "copied"
    return "modified"


def current_sha(root: Path) -> str | None:
    out = _run(["git", "rev-parse", "HEAD"], root)
    return out.strip() if out else None


def current_branch(root: Path) -> str | None:
    out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    if not out:
        return None
    branch = out.strip()
    return branch if branch and branch != "HEAD" else None


def dirty_files(root: Path) -> set[str]:
    """Tracked and untracked files in git status --short output."""
    out = _run(["git", "status", "--short"], root)
    if not out:
        return set()
    paths: set[str] = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        # Handles ordinary status lines and simple renames.
        raw_path = line[3:].strip() if len(line) > 3 else line
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        if raw_path:
            paths.add(raw_path)
    return paths


def working_tree_summary(root: Path) -> dict[str, object]:
    summary: dict[str, object] = {
        "branch": None,
        "sha": current_sha(root) if is_git_repo(root) else None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "staged_count": 0,
        "unstaged_count": 0,
        "untracked_count": 0,
        "dirty_sample": [],
    }
    out = _run(["git", "status", "--porcelain=v1", "--branch"], root)
    if not out:
        return summary
    dirty: list[str] = []
    for line in out.splitlines():
        if line.startswith("## "):
            branch, upstream, ahead, behind = _parse_branch_status(line[3:].strip())
            summary["branch"] = branch
            summary["upstream"] = upstream
            summary["ahead"] = ahead
            summary["behind"] = behind
            continue
        if not line:
            continue
        status = line[:2]
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if status == "??":
            summary["untracked_count"] = int(summary["untracked_count"]) + 1
        else:
            if status[0] != " ":
                summary["staged_count"] = int(summary["staged_count"]) + 1
            if len(status) > 1 and status[1] != " ":
                summary["unstaged_count"] = int(summary["unstaged_count"]) + 1
        if path:
            dirty.append(path)
    summary["dirty_sample"] = dirty[:8]
    if summary["branch"] is None:
        summary["branch"] = current_branch(root)
    return summary


def _parse_branch_status(value: str) -> tuple[str | None, str | None, int, int]:
    if "..." not in value:
        return (None if value == "HEAD (no branch)" else value), None, 0, 0
    branch_part, rest = value.split("...", 1)
    branch = branch_part.strip() or None
    upstream = rest.split(" ", 1)[0].strip() or None
    ahead = behind = 0
    if "[" in rest and "]" in rest:
        meta = rest.split("[", 1)[1].split("]", 1)[0]
        for part in meta.split(","):
            item = part.strip()
            if item.startswith("ahead "):
                ahead = _safe_int(item.split(" ", 1)[1])
            elif item.startswith("behind "):
                behind = _safe_int(item.split(" ", 1)[1])
    return branch, upstream, ahead, behind


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def file_churn_counts(root: Path, max_commits: int = 200) -> dict[str, int]:
    """Return commit count per file from the last max_commits commits.

    Uses a single git log call — O(1) subprocess, not O(n files).
    Returns empty dict if not a git repo or git unavailable.
    """
    out = _run(
        ["git", "log", "--name-only", "--format=", f"-{max_commits}"],
        root,
    )
    if not out:
        return {}
    counts: dict[str, int] = {}
    for line in out.splitlines():
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
    return counts


def co_changed_files(root: Path, seed_paths: set[str], max_commits: int = 200) -> dict[str, int]:
    """Return files that changed in the same recent commits as seed_paths.

    This is a cheap history signal for recall expansion: if a live changed file
    often lands with a service, schema, test, or config file, give that neighbor
    a small ranking boost without forcing full-content inclusion.
    """
    if not seed_paths:
        return {}
    out = _run(
        ["git", "log", "--name-only", "--pretty=format:--AGENTPACK-COMMIT--", f"-{max_commits}"],
        root,
    )
    if not out:
        return {}

    counts: dict[str, int] = {}
    commit_files: list[str] = []

    def flush() -> None:
        if not commit_files or not (set(commit_files) & seed_paths):
            return
        for path in commit_files:
            if path not in seed_paths:
                counts[path] = counts.get(path, 0) + 1

    for raw_line in out.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "--AGENTPACK-COMMIT--":
            flush()
            commit_files = []
            continue
        commit_files.append(line)
    flush()
    return counts


def staged_files(root: Path) -> set[str]:
    """Files staged for commit (git index only)."""
    out = _run(["git", "diff", "--cached", "--name-only"], root)
    if not out:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def infer_task_with_source(root: Path) -> tuple[str, str]:
    """Infer task description with the heuristic that fired.

    Priority (strongest → weakest):
      branch+staged    staged files present + branch name
      staged           staged files, no branch
      branch+unstaged  unstaged changes + branch name
      branch+commit    branch + latest commit message
      branch           branch name alone
      unstaged         unstaged changes, no branch
      commits          recent commit messages
      recently_modified git log history (noisy — last resort)
      fallback         "general development"
    """
    if not is_git_repo(root):
        return "general development", "fallback"

    staged = staged_files(root)

    unstaged_out = _run(["git", "diff", "--name-only"], root)
    unstaged: set[str] = set()
    if unstaged_out:
        for line in unstaged_out.splitlines():
            line = line.strip()
            if line:
                unstaged.add(line)

    staged_topic = _topic_from_paths(staged) if staged else None
    unstaged_topic = _topic_from_paths(unstaged) if unstaged else None

    branch: str | None = None
    branch_out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    if branch_out:
        b = branch_out.strip()
        if b and b not in ("HEAD", "main", "master", "develop"):
            slug = b.split("/", 1)[-1]
            branch = slug.replace("-", " ").replace("_", " ")

    commit_msgs: list[str] = []
    log_out = _run(["git", "log", "--oneline", "-10"], root)
    if log_out:
        for line in log_out.splitlines():
            line = line.strip()
            if not line:
                continue
            msg = line.split(" ", 1)[1] if " " in line else line
            if not msg.lower().startswith("merge "):
                commit_msgs.append(msg)
            if len(commit_msgs) == 3:
                break

    if branch and staged_topic:
        return f"{branch}: {staged_topic}", "branch+staged"
    if staged_topic:
        return staged_topic, "staged"
    if branch and unstaged_topic:
        return f"{branch}: {unstaged_topic}", "branch+unstaged"
    if branch and commit_msgs:
        return f"{branch}: {commit_msgs[0]}", "branch+commit"
    if branch:
        return branch, "branch"
    if unstaged_topic and commit_msgs:
        return f"{unstaged_topic}: {commit_msgs[0]}", "unstaged+commit"
    if unstaged_topic:
        return unstaged_topic, "unstaged"
    if commit_msgs:
        return "; ".join(commit_msgs[:2]), "commits"

    # Last resort: historical git log — only fires when no live signal found
    recent = recently_modified_files(root, n=10)
    recent_topic = _topic_from_paths(set(recent)) if recent else None
    if recent_topic:
        return recent_topic, "recently_modified"

    return "general development", "fallback"


def infer_task_from_git(root: Path) -> str:
    """Infer a task description from git state. See infer_task_with_source for priority chain."""
    task, _ = infer_task_with_source(root)
    return task


def _topic_from_paths(paths: set[str]) -> str | None:
    """Extract a short topic string from a set of file paths."""
    _SKIP = {"__init__", "index", "main", "mod", "lib", "utils", "helpers", "types", "constants"}
    words: list[str] = []
    for path in sorted(paths):
        parts = Path(path).parts
        # Skip test dirs and generated dirs
        stem = Path(path).stem
        if stem in _SKIP:
            continue
        # Take the most specific meaningful directory + stem
        for part in reversed(parts[:-1]):
            if part not in ("src", "lib", "pkg", "app", "tests", "test", "__pycache__"):
                words.append(part.replace("_", " ").replace("-", " "))
                break
        words.append(stem.replace("_", " ").replace("-", " "))
    if not words:
        return None
    # Deduplicate preserving order, keep up to 5 words
    seen: set[str] = set()
    unique: list[str] = []
    for w in words:
        if w not in seen:
            seen.add(w)
            unique.append(w)
        if len(unique) == 5:
            break
    return ", ".join(unique)
