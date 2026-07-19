from __future__ import annotations

import json
import re
import hashlib
import shlex
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentpack.core.models import Citation, ContextPack, FileInfo, SelectedFile
from agentpack.core.redactor import redact_secrets
from agentpack.core.scanner import file_hash

LOCATION_RE = re.compile(r"(?P<path><[^>\n]+>|[A-Za-z0-9_./@+~:-]+):(?P<line>\d+)(?:-(?P<end>\d+))?")
HUNK_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")
SemanticSupportJudge = Callable[[dict[str, Any]], str | None]
CitationContentResolver = Callable[[Citation], str | None]


@dataclass
class CitationValidation:
    valid: list[Citation]
    invalid: list[str]
    missing: list[str]
    repairs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        total = len(self.valid) + len(self.invalid) + len(self.missing)
        if total == 0:
            return 0.0
        return len(self.valid) / total


def file_citation(fi: FileInfo, *, kind: str = "code", claim_id: str | None = None, note: str = "") -> Citation:
    end_line = None
    content = fi.content
    if content is None and fi.abs_path.exists():
        try:
            content = fi.abs_path.read_text(errors="replace")
        except OSError:
            content = None
    if content:
        end_line = max(1, len(content.splitlines()))
    return Citation(
        path=fi.path,
        start_line=1 if end_line else None,
        end_line=end_line,
        source_hash=fi.hash,
        kind=kind,  # type: ignore[arg-type]
        claim_id=claim_id,
        note=note,
        support_text=_support_text(content),
    )


def selected_file_citations(fi: FileInfo, selected: SelectedFile) -> list[Citation]:
    citations: list[Citation] = []
    if selected.include_mode == "diff" and selected.content:
        citations.extend(diff_hunk_citations(selected.path, selected.content, source_hash=fi.hash))
    if selected.symbols:
        for symbol in selected.symbols:
            citations.append(
                Citation(
                    path=selected.path,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    source_hash=fi.hash,
                    kind="symbol",
                    claim_id=f"selected:{selected.path}:{symbol.name}",
                    note=f"{symbol.kind} {symbol.name}",
                    support_text=symbol.signature or symbol.name,
                )
            )
    if not citations:
        citations.append(
            file_citation(
                fi,
                kind="summary" if selected.include_mode == "summary" else "code",
                claim_id=f"selected:{selected.path}",
                note=f"{selected.include_mode} context",
            )
        )
    return citations


def diff_hunk_citations(path: str, diff_text: str, *, source_hash: str | None = None) -> list[Citation]:
    citations: list[Citation] = []
    new_line: int | None = None
    current_start: int | None = None
    current_end: int | None = None
    support_lines: list[str] = []

    def flush() -> None:
        nonlocal current_start, current_end, support_lines
        if current_start is not None:
            citations.append(
                Citation(
                    path=path,
                    start_line=current_start,
                    end_line=current_end,
                    source_hash=source_hash,
                    kind="code",
                    claim_id=f"diff:{path}:{current_start}",
                    note="selected diff hunk",
                    support_text=_support_text("\n".join(support_lines)),
                )
            )
        current_start = None
        current_end = None
        support_lines = []

    for line in diff_text.splitlines():
        match = HUNK_RE.match(line)
        if match:
            flush()
            new_line = int(match.group("new_start"))
            continue
        if new_line is None:
            continue
        if line.startswith("\\"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if current_start is None:
                current_start = new_line
            current_end = new_line
            support_lines.append(line[1:])
            new_line += 1
            continue
        flush()
        if line.startswith("-") and not line.startswith("---"):
            continue
        new_line += 1
    flush()
    return citations


def parse_location(value: str) -> Citation | None:
    match = LOCATION_RE.search(value.strip())
    if not match:
        return None
    try:
        start = int(match.group("line"))
        end = int(match.group("end") or start)
    except ValueError:
        return None
    if start < 1 or end < start:
        return None
    path = match.group("path").strip()
    if path.startswith("<") and path.endswith(">"):
        path = path[1:-1].strip()
    return Citation(path=path, start_line=start, end_line=end, kind="code")


def extract_location_citations(value: object) -> list[Citation]:
    if value is None:
        return []
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    citations: list[Citation] = []
    for match in LOCATION_RE.finditer(text):
        raw = f"{match.group('path')}:{match.group('line')}"
        if match.group("end"):
            raw += f"-{match.group('end')}"
        citation = parse_location(raw)
        if citation is not None:
            citations.append(citation)
    return citations


def validate_citations(
    root: Path,
    citations: list[Citation],
    *,
    required_claims: list[str] | None = None,
    verify_external_content: bool = False,
    external_timeout_s: float = 5.0,
    content_resolver: CitationContentResolver | None = None,
) -> CitationValidation:
    valid: list[Citation] = []
    invalid: list[str] = []
    required = set(required_claims or [])
    seen_claims: set[str] = set()
    for citation in citations:
        if citation.claim_id:
            seen_claims.add(citation.claim_id)
        if citation.kind == "external":
            external_error = _external_validation_error(
                citation,
                verify_content=verify_external_content,
                timeout_s=external_timeout_s,
            )
            if external_error:
                invalid.append(external_error)
            else:
                valid.append(citation)
            continue
        if citation.start_line is None:
            invalid.append(f"{citation.path}: missing line")
            continue
        resolved_content = content_resolver(citation) if content_resolver else None
        path = root / citation.path
        if resolved_content is None and (not path.exists() or not path.is_file()):
            invalid.append(f"{citation.path}:{citation.start_line}: file missing")
            continue
        if citation.source_hash and resolved_content is None:
            try:
                current_hash = file_hash(path)
            except OSError as exc:
                invalid.append(f"{citation.path}:{citation.start_line}: unreadable ({exc})")
                continue
            if current_hash != citation.source_hash:
                invalid.append(f"{citation.path}:{citation.start_line}: source hash mismatch")
                continue
        if resolved_content is not None:
            lines = resolved_content.splitlines()
            line_count = len(lines)
        else:
            try:
                lines = path.read_text(errors="replace").splitlines()
                line_count = len(lines)
            except OSError as exc:
                invalid.append(f"{citation.path}:{citation.start_line}: unreadable ({exc})")
                continue
        if citation.start_line < 1 or citation.start_line > max(line_count, 1):
            invalid.append(f"{citation.path}:{citation.start_line}: line outside file")
            continue
        if citation.end_line is not None and citation.end_line < citation.start_line:
            invalid.append(f"{citation.path}:{citation.start_line}-{citation.end_line}: invalid range")
            continue
        if citation.end_line is not None and citation.end_line > max(line_count, 1):
            invalid.append(f"{citation.path}:{citation.start_line}-{citation.end_line}: range outside file")
            continue
        if citation.support_text:
            end_line = citation.end_line or citation.start_line
            span = "\n".join(lines[citation.start_line - 1:end_line])
            if _normalize_support(citation.support_text) not in _normalize_support(span):
                invalid.append(f"{citation.path}:{citation.start_line}: support text not found")
                continue
        valid.append(citation)
    missing = sorted(required - seen_claims)
    return CitationValidation(valid=valid, invalid=invalid, missing=missing)


def validate_claim_support(
    root: Path,
    claim_text: object,
    citations: list[Citation],
    *,
    label: str = "claim",
    min_overlap: int = 1,
    ignored_claim_terms: object = None,
    semantic_judge: SemanticSupportJudge | None = None,
    content_resolver: CitationContentResolver | None = None,
) -> list[str]:
    """Verify cited spans contain at least one meaningful term from the claim text.

    This is a mechanical support check, not semantic entailment. It catches
    arbitrary path:line citations that point at unrelated code while keeping the
    existing path/range/hash validator responsible for citation validity.
    """
    claim_terms = _claim_terms(claim_text)
    claim_terms -= _claim_terms(ignored_claim_terms)
    if not claim_terms:
        return []
    invalid: list[str] = []
    for citation in citations:
        if citation.kind == "external":
            continue
        if citation.start_line is None:
            continue
        span = _citation_span(root, citation, content_resolver=content_resolver)
        if span is None:
            continue
        overlap = claim_terms & _terms(span)
        if len(overlap) < min_overlap:
            suggestions = _suggest_citation_lines(root, citation, claim_terms, content_resolver=content_resolver)
            suggestion = suggestions[0]["location"] if suggestions else ""
            suggested_text = f"; suggested={suggestion}" if suggestion else ""
            suggestions_text = f"; suggestions={_format_citation_suggestions(suggestions)}" if suggestions else ""
            invalid.append(
                f"{label}: {citation.path}:{citation.start_line} does not support claim text; "
                f"claim={_compact_error_text(claim_text)}; cited={_compact_error_text(span)}; "
                f"fix=cite a line that contains the claimed term, narrow the claim, or mark it unresolved"
                f"{suggested_text}{suggestions_text}"
            )
            continue
        if semantic_judge is not None:
            reason = semantic_judge({
                "claim_text": claim_text,
                "citation": citation.model_dump(mode="json"),
                "cited_text": span,
                "overlap_terms": sorted(overlap),
            })
            if reason:
                invalid.append(
                    f"{label}: {citation.path}:{citation.start_line} semantic support rejected ({reason}); "
                    f"claim={_compact_error_text(claim_text)}; cited={_compact_error_text(span)}; "
                    "fix=cite stronger evidence, narrow the claim, or mark it unresolved"
                )
    return invalid


def _compact_error_text(value: object, limit: int = 140) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def semantic_support_command_judge(command: str, *, timeout_s: float = 30.0) -> SemanticSupportJudge:
    parts = shlex.split(command)
    if not parts:
        raise ValueError("semantic support command is empty")

    def judge(payload: dict[str, Any]) -> str | None:
        try:
            result = subprocess.run(
                parts,
                input=json.dumps(payload, sort_keys=True),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"semantic support judge failed: {exc}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return detail or f"semantic support judge exited {result.returncode}"
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            return f"semantic support judge returned invalid JSON: {exc}"
        if isinstance(response, bool):
            return None if response else "semantic support judge rejected citation"
        if not isinstance(response, dict):
            return "semantic support judge returned invalid JSON object"
        supported = response.get("supported")
        reason = str(response.get("reason") or "").strip()
        if supported is True:
            return None
        if supported is False:
            return reason or "semantic support judge rejected citation"
        return "semantic support judge response missing supported boolean"

    return judge


def build_citation_manifest(pack: ContextPack) -> dict[str, Any]:
    citations = collect_pack_citations(pack)
    return {
        "schema_version": 1,
        "task": pack.task,
        "agent": pack.agent,
        "context_hash": pack.freshness.get("snapshot_root_hash", ""),
        "citation_count": len(citations),
        "selected_files": [
            {
                "path": sf.path,
                "include_mode": sf.include_mode,
                "source_hash": sf.source_hash,
                "citations": [citation.model_dump(mode="json") for citation in sf.citations],
            }
            for sf in pack.selected_files
        ],
        "broad_context": (
            {
                "intent": pack.broad_context.intent,
                "citations": [citation.model_dump(mode="json") for citation in pack.broad_context.citations],
                "module_summaries": [
                    {
                        "path": module.path,
                        "key_files": module.key_files,
                        "citations": [citation.model_dump(mode="json") for citation in module.citations],
                    }
                    for module in pack.broad_context.module_summaries
                ],
            }
            if pack.broad_context
            else {}
        ),
        "receipts": [
            {
                "path": receipt.path,
                "action": receipt.action,
                "reason": receipt.reason,
                "citations": [citation.model_dump(mode="json") for citation in receipt.citations],
            }
            for receipt in pack.receipts
            if receipt.citations
        ],
        "citations": [citation.model_dump(mode="json") for citation in citations],
    }


def collect_pack_citations(pack: ContextPack) -> list[Citation]:
    citations: list[Citation] = []
    citations.extend(pack.citations)
    for sf in pack.selected_files:
        citations.extend(sf.citations)
    for receipt in pack.receipts:
        citations.extend(receipt.citations)
    if pack.broad_context:
        citations.extend(pack.broad_context.citations)
        for module in pack.broad_context.module_summaries:
            citations.extend(module.citations)
    return _dedupe_citations(citations)


def write_citation_manifest(pack: ContextPack, root: Path, out_path: Path) -> Path:
    manifest_path = _citation_manifest_path(root, out_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(build_citation_manifest(pack), indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


def citation_manifest_relpath(root: Path, out_path: Path) -> str:
    path = _citation_manifest_path(root, out_path)
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _citation_manifest_path(root: Path, out_path: Path) -> Path:
    try:
        rel = out_path.relative_to(root)
    except ValueError:
        return out_path.with_name("citations.json")
    if rel.parent == Path("."):
        return root / ".agentpack" / "citations.json"
    return out_path.with_name("citations.json")


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    seen: set[tuple[object, ...]] = set()
    result: list[Citation] = []
    for citation in citations:
        key = (
            citation.path,
            citation.start_line,
            citation.end_line,
            citation.source_hash,
            citation.kind,
            citation.claim_id,
            citation.note,
            citation.support_text,
            citation.url,
            citation.retrieved_at,
            citation.content_hash,
        )
        if key not in seen:
            seen.add(key)
            result.append(citation)
    return result


def _support_text(content: str | None, *, max_chars: int = 160) -> str:
    if not content:
        return ""
    for line in content.splitlines():
        value = line.strip()
        if value:
            redacted, _warnings = redact_secrets(value[:max_chars], "citation")
            return redacted
    return ""


def _normalize_support(value: str) -> str:
    return " ".join(value.split()).lower()


def _citation_span(
    root: Path,
    citation: Citation,
    *,
    content_resolver: CitationContentResolver | None = None,
) -> str | None:
    content = content_resolver(citation) if content_resolver else None
    if content is not None:
        lines = content.splitlines()
    else:
        path = root / citation.path
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            return None
    if citation.start_line is None or citation.start_line < 1:
        return None
    end_line = citation.end_line or citation.start_line
    return "\n".join(lines[citation.start_line - 1:end_line])


def _suggest_citation_lines(
    root: Path,
    citation: Citation,
    claim_terms: set[str],
    *,
    content_resolver: CitationContentResolver | None = None,
) -> list[dict[str, Any]]:
    if not claim_terms:
        return []
    content = content_resolver(citation) if content_resolver else None
    if content is None:
        try:
            content = (root / citation.path).read_text(errors="replace")
        except OSError:
            return []
    candidates: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        overlap = len(claim_terms & _terms(line))
        if overlap <= 0:
            continue
        snippet = " ".join(line.strip().split())
        if not snippet:
            continue
        candidates.append(
            {
                "location": f"{citation.path}:{line_number}",
                "snippet": _compact_error_text(snippet, limit=100),
                "overlap": overlap,
                "distance": abs(line_number - (citation.start_line or line_number)),
            }
        )
    candidates.sort(key=lambda item: (-int(item["overlap"]), int(item["distance"]), str(item["location"])))
    return [{key: value for key, value in item.items() if key != "distance"} for item in candidates[:3]]


def _format_citation_suggestions(suggestions: list[dict[str, Any]]) -> str:
    formatted: list[str] = []
    for item in suggestions[:3]:
        location = str(item.get("location") or "")
        snippet = str(item.get("snippet") or "")
        if not location:
            continue
        formatted.append(f'{location} "{snippet}"')
    return " | ".join(formatted)


def _claim_terms(value: object) -> set[str]:
    if value is None:
        return set()
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = LOCATION_RE.sub(" ", text)
    return _terms(text)


def _terms(value: str) -> set[str]:
    stop = {
        "about",
        "after",
        "also",
        "because",
        "before",
        "claim",
        "code",
        "does",
        "evidence",
        "file",
        "from",
        "line",
        "show",
        "shows",
        "that",
        "this",
        "value",
        "with",
    }
    return {
        _stem(term)
        for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value.lower())
        if term not in stop
    }


def _stem(value: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(value) > len(suffix) + 3 and value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _external_validation_error(
    citation: Citation,
    *,
    verify_content: bool = False,
    timeout_s: float = 5.0,
) -> str:
    label = citation.url or citation.path or "external"
    if not citation.url.startswith(("https://", "http://")):
        return f"{label}: external citation missing http(s) url"
    if not (citation.retrieved_at or citation.content_hash or citation.source_hash):
        return f"{label}: external citation missing retrieval provenance"
    if citation.support_text and not (citation.content_hash or citation.source_hash):
        return f"{label}: external support text requires content hash"
    if citation.support_text and citation.content_hash:
        expected = _external_support_hash(citation.support_text)
        if citation.content_hash not in {expected, expected.removeprefix("sha256:")}:
            return f"{label}: external support text hash mismatch"
    if verify_content and citation.support_text:
        content, error = _fetch_external_text(citation.url, timeout_s=timeout_s)
        if error:
            return f"{label}: external citation fetch failed ({error})"
        if _normalize_support(citation.support_text) not in _normalize_support(content):
            return f"{label}: external support text not found at url"
    return ""


def _external_support_hash(value: str) -> str:
    normalized = _normalize_support(value)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _fetch_external_text(url: str, *, timeout_s: float, max_bytes: int = 1_000_000) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "agentpack-citation-validator/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read(max_bytes + 1)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return "", str(exc)
    if len(raw) > max_bytes:
        return "", "response too large"
    return raw.decode("utf-8", errors="replace"), ""
