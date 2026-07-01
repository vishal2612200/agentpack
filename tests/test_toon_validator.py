from __future__ import annotations

import json

from typer.testing import CliRunner

from agentpack.cli import app
from agentpack.core.toon_validator import validate_toon_text


def test_validate_toon_text_accepts_rendered_toon() -> None:
    result = validate_toon_text("@format toon\n@root sample\nname: demo\nitems[]:\n  - one\n")

    assert result.ok is True
    assert result.root == "sample"
    assert result.parsed_type == "dict"


def test_validate_toon_text_rejects_invalid_scalar_json() -> None:
    result = validate_toon_text("@format toon\nvalue: \"unterminated\n")

    assert result.ok is False
    assert "invalid JSON scalar" in result.error


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
