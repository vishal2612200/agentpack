from __future__ import annotations

import json

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.toon_validator import canonicalize_to_toon_text, validate_toon_text


def test_validate_toon_text_accepts_rendered_toon() -> None:
    result = validate_toon_text("@format toon\n@root sample\nname: demo\nitems[]:\n  - one\n")

    assert result.ok is True
    assert result.root == "sample"
    assert result.parsed_type == "dict"


def test_validate_toon_text_rejects_invalid_scalar_json() -> None:
    result = validate_toon_text("@format toon\nvalue: \"unterminated\n")

    assert result.ok is False
    assert "invalid JSON scalar" in result.error


def test_validate_toon_text_accepts_review_json_fallback() -> None:
    result = validate_toon_text(
        json.dumps({"findings": [], "coverage": "complete"}),
        schema="review-findings",
        allow_json=True,
    )

    assert result.ok is True
    assert result.input_format == "json"
    assert result.canonical_available is True


def test_canonicalize_to_toon_text_renders_review_json() -> None:
    result = canonicalize_to_toon_text(
        "```json\n" + json.dumps({"findings": [], "coverage": "complete"}) + "\n```\n",
        schema="review-findings",
    )

    assert result.input_format == "json"
    assert result.text.startswith("@format toon\n@root review_findings\n")
    assert "findings[]:" in result.text


def test_validate_toon_text_rejects_wrong_review_schema() -> None:
    result = validate_toon_text("@format toon\n@root review_findings\nfindings[]:\n  []\n", schema="review-findings")

    assert result.ok is False
    assert "review-findings missing required key: coverage" in result.error


def test_review_findings_schema_rejects_invalid_enum_values() -> None:
    result = validate_toon_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "f1",
                        "unit": "cu1",
                        "location": "src/foo.py:2",
                        "claim": "foo changed",
                        "evidence": "src/foo.py:2 shows the change",
                        "severity": "critical",
                        "confidence": "certain",
                    }
                ],
                "coverage": "complete",
            }
        ),
        schema="review-findings",
        allow_json=True,
    )

    assert result.ok is False
    assert "findings[1].severity must be one of" in result.error
    assert "findings[1].confidence must be one of" in result.error


def test_review_findings_schema_requires_path_line_evidence() -> None:
    result = validate_toon_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "f1",
                        "unit": "cu1",
                        "location": "src/foo.py",
                        "claim": "foo changed",
                        "evidence": "code shows the change",
                        "severity": "should-fix",
                    }
                ],
                "coverage": {"status": "done"},
            }
        ),
        schema="review-findings",
        allow_json=True,
    )

    assert result.ok is False
    assert "findings[1].location must include path:line evidence" in result.error
    assert "findings[1].evidence must include path:line evidence" in result.error
    assert "coverage.status must be one of" in result.error


def test_review_understanding_schema_rejects_bad_nested_shapes() -> None:
    result = validate_toon_text(
        json.dumps(
            {
                "intent": {"requirement": "placeholder"},
                "change_units": [
                    {
                        "id": "cu1",
                        "location": "src/foo.py:1-2",
                        "kind": "core",
                        "what_changed": "foo changed",
                        "code": "src/foo.py:2 return 2",
                        "referenced_symbols": [
                            {
                                "name": "foo",
                                "defined_at": "src/foo.py:1",
                                "code": "def foo(): ...",
                                "confidence": "certain",
                            }
                        ],
                        "callers": [],
                        "contracts_touched": [123],
                        "local_convention_refs": [{"pattern": "same style"}],
                    }
                ],
                "open_questions": [{"unit": "cu1", "question": "unknown", "matters_because": "review", "status": "open"}],
            }
        ),
        schema="review-understanding",
        allow_json=True,
    )

    assert result.ok is False
    assert "referenced_symbols[1].confidence must be one of" in result.error
    assert "contracts_touched[1] must be a string or object" in result.error
    assert "local_convention_refs[1] missing required key: example_at" in result.error
    assert "open_questions[1].status must be one of: unresolved" in result.error


def test_toon_validate_cli_emits_json(tmp_path) -> None:
    path = tmp_path / "sample.toon"
    path.write_text("@format toon\nname: demo\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["toon-validate", str(path), "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["source"] == str(path)


def test_toon_validate_cli_fails_invalid_file(tmp_path) -> None:
    path = tmp_path / "bad.toon"
    path.write_text("name: demo\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["toon-validate", str(path), "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "missing required @format toon" in payload["error"]


def test_toon_validate_cli_writes_canonical_review_json(tmp_path) -> None:
    path = tmp_path / "findings.toon"
    path.write_text(json.dumps({"findings": [], "coverage": "complete"}), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "toon-validate",
            str(path),
            "--schema",
            "review-findings",
            "--allow-json",
            "--write-canonical",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["input_format"] == "json"
    assert path.read_text(encoding="utf-8").startswith("@format toon\n@root review_findings\n")
