"""Sanitized artifacts for deterministic architecture CI checks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def write_ci_artifacts(*, diff_path: Path, check_path: Path, output_dir: Path) -> dict[str, str]:
    """Render source-free architecture artifacts from local command JSON."""
    diff = _sanitize(_read_json(diff_path))
    check = _sanitize(_read_json(check_path))
    violations = check.get("violations") if isinstance(check.get("violations"), list) else []
    blocking = sum(1 for item in violations if isinstance(item, dict) and item.get("blocking"))
    output_dir.mkdir(parents=True, exist_ok=True)
    diff_output = output_dir / "architecture-diff.json"
    check_output = output_dir / "architecture-check.json"
    markdown_output = output_dir / "architecture-diff.md"
    receipt_output = output_dir / "architecture-receipt.json"
    diff_output.write_text(_dump(diff), encoding="utf-8")
    check_output.write_text(_dump(check), encoding="utf-8")
    markdown_output.write_text(_markdown(diff, check), encoding="utf-8")
    receipt_output.write_text(
        _dump(
            {
                "schema_version": 1,
                "sanitized": True,
                "artifacts": [path.name for path in (diff_output, check_output, markdown_output)],
                "diff_sha256": _sha256(diff_output),
                "check_sha256": _sha256(check_output),
                "blocking_violations": blocking,
                "advisory_violations": len(violations) - blocking,
                "git_sha": str(check.get("git_sha") or diff.get("git_sha") or ""),
            }
        ),
        encoding="utf-8",
    )
    return {"diff": str(diff_output), "check": str(check_output), "markdown": str(markdown_output), "receipt": str(receipt_output)}


def load_verified_ci_artifact(output_dir: Path, *, head_sha: str) -> dict[str, Any] | None:
    """Read local CI artifacts only when receipt and head SHA agree."""
    receipt_path = output_dir / "architecture-receipt.json"
    check_path = output_dir / "architecture-check.json"
    diff_path = output_dir / "architecture-diff.json"
    try:
        receipt = _read_json(receipt_path)
        check = _read_json(check_path)
        diff = _read_json(diff_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if str(receipt.get("git_sha") or "") != head_sha:
        return None
    if str(receipt.get("check_sha256") or "") != _sha256(check_path):
        return None
    if str(receipt.get("diff_sha256") or "") != _sha256(diff_path):
        return None
    return {"receipt": receipt, "check": check, "diff": diff}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Architecture artifact must be a JSON object: {path}")
    return value


def _sanitize(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            str(name): _sanitize(item, key=str(name))
            for name, item in value.items()
            if name not in {"source_hash", "repo_fingerprint"}
        }
    if isinstance(value, list):
        return [_sanitize(item, key=key) for item in value]
    if key == "path" and isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            return "[redacted-path]"
    return value


def _markdown(diff: dict[str, Any], check: dict[str, Any]) -> str:
    violations = check.get("violations") if isinstance(check.get("violations"), list) else []
    blocking = sum(1 for item in violations if isinstance(item, dict) and item.get("blocking"))
    affected = diff.get("affected_domains") if isinstance(diff.get("affected_domains"), list) else []
    tests = diff.get("test_impact") if isinstance(diff.get("test_impact"), list) else []
    budget = check.get("budget") if isinstance(check.get("budget"), dict) else {}
    metrics = check.get("metrics") if isinstance(check.get("metrics"), dict) else {}
    lines = [
        "<!-- agentpack-architecture-summary -->",
        "# AgentPack Architecture Check",
        "",
        f"- Changed districts: {', '.join(str(item) for item in affected) or 'none'}",
        f"- Added roads: {len(diff.get('added_edges') or [])}",
        f"- Removed roads: {len(diff.get('removed_edges') or [])}",
        f"- Blocking invariant results: {blocking}",
        f"- Advisory invariant results: {len(violations) - blocking}",
        f"- Related tests: {', '.join(str(item) for item in tests) or 'none'}",
        f"- Map budget: {budget.get('status', 'unbaselined')}",
        f"- Snapshot: {metrics.get('entity_count', 0)} entities, {metrics.get('edge_count', 0)} roads, {metrics.get('artifact_bytes', 0)} bytes",
    ]
    for warning in budget.get("warnings") or []:
        lines.append(f"- Budget warning: {warning}")
    return "\n".join(lines) + "\n"


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
