from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentpack.core.toon_parser import parse_toon
from agentpack.renderers.toon import render_toon


REVIEW_TOON_SCHEMAS = {"review-understanding", "review-findings"}
_SCHEMA_ROOTS = {
    "review-understanding": "review_understanding",
    "review-findings": "review_findings",
}
_CHANGE_UNIT_KINDS = {"core", "incidental"}
_CONFIDENCE_VALUES = {"high", "medium", "low"}
_FINDING_LENSES = {"unit", "integration"}
_FINDING_TYPES = {"logic", "edge_case", "naming", "complexity", "caller_break", "contract", "convention", "dependency"}
_FINDING_SEVERITIES = {"blocker", "should-fix", "nit"}
_FINDING_CATEGORIES = {"defect", "preference"}


@dataclass(frozen=True)
class ToonValidationResult:
    ok: bool
    source: str
    root: str | None = None
    parsed_type: str | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    schema: str = ""
    input_format: str = ""
    repair_hint: str = ""
    canonical_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "root": self.root,
            "parsed_type": self.parsed_type,
            "error": self.error,
            "warnings": self.warnings,
            "schema": self.schema,
            "input_format": self.input_format,
            "repair_hint": self.repair_hint,
            "canonical_available": self.canonical_available,
        }


@dataclass(frozen=True)
class ToonCanonicalizationResult:
    payload: Any
    text: str
    root: str | None
    input_format: str
    warnings: list[str] = field(default_factory=list)


def validate_toon_text(
    text: str,
    *,
    source: str = "<string>",
    require_format: bool = True,
    schema: str = "",
    allow_json: bool = False,
) -> ToonValidationResult:
    warnings: list[str] = []
    schema_error = _validate_schema_name(schema)
    if schema_error:
        return ToonValidationResult(ok=False, source=source, error=schema_error, schema=schema)

    clean_text, stripped_fence = strip_markdown_fence(text)
    if stripped_fence:
        warnings.append("removed surrounding markdown code fence")
    # Keep root metadata on failures so callers can show location context; `ok` remains the validity gate.
    root = _root_directive(clean_text)
    input_format = "toon"

    if allow_json and _looks_like_json(clean_text):
        try:
            payload = json.loads(clean_text)
        except json.JSONDecodeError as exc:
            return ToonValidationResult(
                ok=False,
                source=source,
                root=root,
                error=f"invalid JSON fallback: {exc.msg}",
                warnings=warnings,
                schema=schema,
                input_format="json",
                repair_hint="Emit valid JSON or canonical TOON.",
            )
        input_format = "json"
        root = _SCHEMA_ROOTS.get(schema)
    elif require_format and not _has_format_directive(clean_text):
        return ToonValidationResult(
            ok=False,
            source=source,
            root=root,
            error="missing required @format toon directive",
            warnings=warnings,
            schema=schema,
            input_format=input_format,
            repair_hint="Add @format toon as the first non-empty line, or pass --allow-missing-format for legacy files.",
        )
    else:
        if not require_format and not _has_format_directive(clean_text):
            warnings.append("missing @format toon directive")
        try:
            payload = parse_toon(clean_text)
        except Exception as exc:
            return ToonValidationResult(
                ok=False,
                source=source,
                root=root,
                error=str(exc),
                warnings=warnings,
                schema=schema,
                input_format=input_format,
                repair_hint=_repair_hint(str(exc)),
            )

    if input_format == "json":
        warnings.append("JSON fallback accepted; canonical TOON is available")

    schema_errors = validate_toon_payload_schema(payload, schema)
    if schema_errors:
        return ToonValidationResult(
            ok=False,
            source=source,
            root=root,
            parsed_type=type(payload).__name__,
            error="; ".join(schema_errors[:5]),
            warnings=warnings,
            schema=schema,
            input_format=input_format,
            repair_hint=f"Match the {schema} schema exactly." if schema else "",
            canonical_available=input_format == "json",
        )

    return ToonValidationResult(
        ok=True,
        source=source,
        root=root,
        parsed_type=type(payload).__name__,
        warnings=warnings,
        schema=schema,
        input_format=input_format,
        canonical_available=input_format == "json" or stripped_fence or not _has_format_directive(clean_text),
    )


def validate_toon_file(
    path: Path,
    *,
    require_format: bool = True,
    schema: str = "",
    allow_json: bool = False,
) -> ToonValidationResult:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ToonValidationResult(ok=False, source=str(path), error=f"unable to read file: {exc}", schema=schema)
    return validate_toon_text(text, source=str(path), require_format=require_format, schema=schema, allow_json=allow_json)


def canonicalize_to_toon_text(
    text: str,
    *,
    schema: str = "",
    source: str = "<string>",
    allow_json: bool = True,
) -> ToonCanonicalizationResult:
    schema_error = _validate_schema_name(schema)
    if schema_error:
        raise ValueError(schema_error)
    clean_text, stripped_fence = strip_markdown_fence(text)
    warnings: list[str] = []
    if stripped_fence:
        warnings.append("removed surrounding markdown code fence")
    root = _root_directive(clean_text) or _SCHEMA_ROOTS.get(schema)

    if allow_json and _looks_like_json(clean_text):
        try:
            payload = json.loads(clean_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source} is not valid JSON fallback: {exc.msg}") from exc
        input_format = "json"
    else:
        if not _has_format_directive(clean_text):
            warnings.append("missing @format toon directive")
        try:
            payload = parse_toon(clean_text)
        except Exception as exc:
            raise ValueError(f"{source} is not valid TOON: {exc}") from exc
        input_format = "toon"

    schema_errors = validate_toon_payload_schema(payload, schema)
    if schema_errors:
        raise ValueError(f"{source} does not match {schema or 'TOON'} schema: {'; '.join(schema_errors[:5])}")

    return ToonCanonicalizationResult(
        payload=payload,
        text=render_toon(payload, root_name=root),
        root=root,
        input_format=input_format,
        warnings=warnings,
    )


def validate_toon_payload_schema(payload: Any, schema: str = "") -> list[str]:
    if not schema:
        return []
    schema_error = _validate_schema_name(schema)
    if schema_error:
        return [schema_error]
    if schema == "review-understanding":
        return _validate_review_understanding_payload(payload)
    if schema == "review-findings":
        return _validate_review_findings_payload(payload)
    return []


def strip_markdown_fence(text: str) -> tuple[str, bool]:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:[A-Za-z0-9_-]+)?\s*\n(?P<body>.*)\n```", stripped, flags=re.DOTALL)
    if not match:
        return text, False
    return match.group("body").strip() + "\n", True


def _validate_schema_name(schema: str) -> str:
    if schema and schema not in REVIEW_TOON_SCHEMAS:
        return f"unknown TOON schema: {schema}"
    return ""


def _validate_review_understanding_payload(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["review-understanding must decode to an object"]
    errors.extend(_require_keys(payload, ("intent", "change_units", "open_questions"), "review-understanding"))
    if "intent" in payload and not isinstance(payload["intent"], dict):
        errors.append("intent must be an object")
    elif isinstance(payload.get("intent"), dict):
        intent = payload["intent"]
        errors.extend(_require_string(intent, "requirement", "intent"))
        if "issue_ref" in intent:
            errors.extend(_require_string(intent, "issue_ref", "intent", allow_null=True))
        if "author_decisions" in intent and not isinstance(intent["author_decisions"], list):
            errors.append("intent.author_decisions must be a list")
    if "change_units" in payload and not isinstance(payload["change_units"], list):
        errors.append("change_units must be a list")
    if "open_questions" in payload and not isinstance(payload["open_questions"], list):
        errors.append("open_questions must be a list")
    if isinstance(payload.get("change_units"), list):
        for index, unit in enumerate(payload["change_units"], start=1):
            if not isinstance(unit, dict):
                errors.append(f"change_units[{index}] must be an object")
                continue
            errors.extend(_require_keys(unit, ("id", "location", "kind", "what_changed", "code"), f"change_units[{index}]"))
            errors.extend(_require_string_fields(unit, ("id", "location", "kind", "what_changed", "code"), f"change_units[{index}]"))
            errors.extend(_validate_enum(unit, "kind", _CHANGE_UNIT_KINDS, f"change_units[{index}]"))
            for field in ("referenced_symbols", "callers", "contracts_touched", "local_convention_refs"):
                if field in unit and not isinstance(unit[field], list):
                    errors.append(f"change_units[{index}].{field} must be a list")
            if isinstance(unit.get("referenced_symbols"), list):
                errors.extend(_validate_referenced_symbols(unit["referenced_symbols"], f"change_units[{index}].referenced_symbols"))
            if isinstance(unit.get("callers"), list):
                errors.extend(_validate_callers(unit["callers"], f"change_units[{index}].callers"))
            if isinstance(unit.get("contracts_touched"), list):
                errors.extend(_validate_contracts_touched(unit["contracts_touched"], f"change_units[{index}].contracts_touched"))
            if isinstance(unit.get("local_convention_refs"), list):
                errors.extend(_validate_local_convention_refs(unit["local_convention_refs"], f"change_units[{index}].local_convention_refs"))
    if isinstance(payload.get("open_questions"), list):
        errors.extend(_validate_open_questions(payload["open_questions"], "open_questions"))
    return errors


def _validate_review_findings_payload(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["review-findings must decode to an object"]
    errors.extend(_require_keys(payload, ("findings", "coverage"), "review-findings"))
    findings = payload.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        return errors
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            errors.append(f"findings[{index}] must be an object")
            continue
        errors.extend(
            _require_keys(
                finding,
                ("id", "unit", "location", "claim", "evidence", "severity"),
                f"findings[{index}]",
            )
        )
        errors.extend(_require_string_fields(finding, ("id", "unit", "location", "claim", "evidence", "severity"), f"findings[{index}]"))
        errors.extend(_validate_enum(finding, "lens", _FINDING_LENSES, f"findings[{index}]"))
        errors.extend(_validate_enum(finding, "type", _FINDING_TYPES, f"findings[{index}]"))
        errors.extend(_validate_enum(finding, "severity", _FINDING_SEVERITIES, f"findings[{index}]"))
        errors.extend(_validate_enum(finding, "category", _FINDING_CATEGORIES, f"findings[{index}]"))
        errors.extend(_validate_enum(finding, "confidence", _CONFIDENCE_VALUES, f"findings[{index}]"))
        errors.extend(_require_string(finding, "depends_on", f"findings[{index}]", allow_null=True, required=False))
        errors.extend(_require_string(finding, "direction", f"findings[{index}]", allow_null=True, required=False))
        errors.extend(_require_path_line(finding, "location", f"findings[{index}]"))
        errors.extend(_require_path_line(finding, "evidence", f"findings[{index}]"))
    coverage = payload.get("coverage")
    if "coverage" in payload and not isinstance(coverage, (str, dict)):
        errors.append("coverage must be a string or object")
    elif isinstance(coverage, str) and not coverage.strip():
        errors.append("coverage must not be empty")
    elif isinstance(coverage, dict):
        errors.extend(_require_string(coverage, "status", "coverage"))
        errors.extend(_validate_enum(coverage, "status", {"complete", "incomplete"}, "coverage"))
    return errors


def _require_keys(payload: dict[str, Any], required: tuple[str, ...], label: str) -> list[str]:
    return [f"{label} missing required key: {key}" for key in required if key not in payload]


def _require_string_fields(payload: dict[str, Any], fields: tuple[str, ...], label: str) -> list[str]:
    errors: list[str] = []
    for field_name in fields:
        errors.extend(_require_string(payload, field_name, label, required=field_name in payload))
    return errors


def _require_string(
    payload: dict[str, Any],
    field: str,
    label: str,
    *,
    allow_null: bool = False,
    required: bool = True,
) -> list[str]:
    if field not in payload:
        return [f"{label} missing required key: {field}"] if required else []
    value = payload[field]
    if value is None and allow_null:
        return []
    if not isinstance(value, str):
        return [f"{label}.{field} must be a string" + (" or null" if allow_null else "")]
    if not value.strip() and not allow_null:
        return [f"{label}.{field} must not be empty"]
    return []


def _validate_enum(payload: dict[str, Any], field: str, allowed: set[str], label: str) -> list[str]:
    if field not in payload:
        return []
    value = payload[field]
    if not isinstance(value, str):
        return [f"{label}.{field} must be a string"]
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        return [f"{label}.{field} must be one of: {choices}"]
    return []


def _require_path_line(payload: dict[str, Any], field: str, label: str) -> list[str]:
    value = str(payload.get(field) or "")
    if re.search(r"(?:^|\s)[\w./-]+:\d+(?:-\d+)?(?:\s|$|[,.])", value):
        return []
    return [f"{label}.{field} must include path:line evidence"]


def _validate_referenced_symbols(items: list[Any], label: str) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be an object")
            continue
        errors.extend(_require_keys(item, ("name", "defined_at", "code", "confidence"), item_label))
        errors.extend(_require_string_fields(item, ("name", "defined_at", "signature", "code", "confidence"), item_label))
        errors.extend(_validate_enum(item, "confidence", _CONFIDENCE_VALUES, item_label))
    return errors


def _validate_callers(items: list[Any], label: str) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be an object")
            continue
        errors.extend(_require_keys(item, ("at", "call_site_behavior"), item_label))
        errors.extend(_require_string_fields(item, ("at", "code", "call_site_behavior"), item_label))
    return errors


def _validate_contracts_touched(items: list[Any], label: str) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        item_label = f"{label}[{index}]"
        if isinstance(item, str):
            continue
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be a string or object")
            continue
        errors.extend(_require_keys(item, ("contract", "before", "after", "evidence"), item_label))
        errors.extend(_require_string_fields(item, ("contract", "before", "after", "evidence"), item_label))
        if "evidence" in item:
            errors.extend(_require_path_line(item, "evidence", item_label))
    return errors


def _validate_local_convention_refs(items: list[Any], label: str) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be an object")
            continue
        errors.extend(_require_keys(item, ("pattern", "example_at"), item_label))
        errors.extend(_require_string_fields(item, ("pattern", "example_at"), item_label))
    return errors


def _validate_open_questions(items: list[Any], label: str) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be an object")
            continue
        errors.extend(_require_keys(item, ("unit", "question", "matters_because", "status"), item_label))
        errors.extend(_require_string(item, "unit", item_label, allow_null=True, required="unit" in item))
        errors.extend(_require_string_fields(item, ("question", "matters_because", "status"), item_label))
        errors.extend(_validate_enum(item, "status", {"unresolved"}, item_label))
    return errors


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _repair_hint(error: str) -> str:
    if "unexpected indent" in error:
        return "Use two-space indentation and list items as '-' followed by nested fields indented two more spaces."
    if "unrecognized object entry" in error:
        return "Use 'key: value', 'key:', or 'key[]:' entries. Do not emit YAML block scalars or markdown bullets."
    if "invalid JSON scalar" in error:
        return "Quote multiline or special scalar values as JSON strings, or keep them as plain single-line values."
    if "empty TOON input" in error:
        return "Write a single TOON object with @format toon and the required review schema keys."
    return "Rewrite the file as canonical TOON or valid JSON matching the requested schema."


def _has_format_directive(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip().lower()
        if not line:
            continue
        return line == "@format toon"
    return False


def _root_directive(text: str) -> str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("@root "):
            root = line.removeprefix("@root ").strip()
            return root or None
    return None
