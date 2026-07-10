from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agentpack.architecture.service import capability_registry, build_snapshot_for_ref, run_check, serialize_model
from agentpack.cli import app
from agentpack.core.config import load_config


runner = CliRunner()


def test_snapshot_json_is_stable_for_same_commit(tmp_path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / "src" / "pkg" / "alpha.py", "def helper(value):\n    return value + 1\n")
    sha = _commit_all(tmp_path, "initial")

    first = build_snapshot_for_ref(tmp_path, sha)
    second = build_snapshot_for_ref(tmp_path, sha)

    assert serialize_model(first) == serialize_model(second)


def test_symbol_entity_key_survives_file_rename(tmp_path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / "src" / "pkg" / "alpha.py", "def helper(value):\n    return value + 1\n")
    base_sha = _commit_all(tmp_path, "base")

    source = tmp_path / "src" / "pkg" / "alpha.py"
    target = tmp_path / "src" / "pkg" / "renamed.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    head_sha = _commit_all(tmp_path, "rename")

    base_snapshot = build_snapshot_for_ref(tmp_path, base_sha)
    head_snapshot = build_snapshot_for_ref(tmp_path, head_sha)
    before = _find_symbol(base_snapshot, "helper")
    after = _find_symbol(head_snapshot, "helper")

    assert before.entity_key == after.entity_key
    assert before.locator.path == "src/pkg/alpha.py"
    assert after.locator.path == "src/pkg/renamed.py"


def test_architecture_check_blocks_new_forbidden_import(tmp_path) -> None:
    _init_repo(tmp_path)
    _write(
        tmp_path / ".agentpack" / "config.toml",
        "\n".join(
            [
                "[architecture]",
                'cache_dir = ".agentpack/architecture"',
                "",
                "[[architecture.invariant]]",
                'id = "no-public-internal-imports"',
                'kind = "forbid_edge"',
                'enforcement = "block"',
                'edge_types = ["imports"]',
                'min_confidence = "best_effort"',
                'source = { path_globs = ["src/public/**"] }',
                'target = { path_globs = ["src/internal/**"] }',
            ]
        )
        + "\n",
    )
    _write(tmp_path / "src" / "internal" / "__init__.py", "")
    _write(tmp_path / "src" / "internal" / "secret.py", "SECRET = 1\n")
    _write(tmp_path / "src" / "public" / "__init__.py", "")
    _write(tmp_path / "src" / "public" / "api.py", "def read_secret():\n    return 0\n")
    base_sha = _commit_all(tmp_path, "base")

    _write(
        tmp_path / "src" / "public" / "api.py",
        "from ..internal.secret import SECRET\n\n\ndef read_secret():\n    return SECRET\n",
    )
    head_sha = _commit_all(tmp_path, "head")

    result = run_check(tmp_path, base_sha, head_sha, load_config(tmp_path))

    assert any(violation.blocking for violation in result.violations)
    assert result.violations[0].invariant_id == "no-public-internal-imports"
    assert "imports" in result.violations[0].message


def test_best_effort_relationships_can_warn_but_never_block(tmp_path) -> None:
    _init_repo(tmp_path)
    _write(
        tmp_path / ".agentpack" / "config.toml",
        "\n".join(
            [
                "[[architecture.invariant]]",
                'id = "no-client-server-imports"',
                'kind = "forbid_edge"',
                'enforcement = "block"',
                'edge_types = ["imports"]',
                'min_confidence = "best_effort"',
                'source = { path_globs = ["src/client/**"] }',
                'target = { path_globs = ["src/server/**"] }',
            ]
        )
        + "\n",
    )
    _write(tmp_path / "src" / "server" / "secret.ts", "export const secret = 1;\n")
    _write(tmp_path / "src" / "client" / "api.ts", "export const api = 1;\n")
    base_sha = _commit_all(tmp_path, "base")

    _write(tmp_path / "src" / "client" / "api.ts", "import { secret } from '../server/secret';\nexport const api = secret;\n")
    head_sha = _commit_all(tmp_path, "head")

    result = run_check(tmp_path, base_sha, head_sha, load_config(tmp_path))

    assert result.violations
    assert not any(violation.blocking for violation in result.violations)
    assert result.violations[0].requested_enforcement == "block"
    assert result.violations[0].enforcement == "warn"


def test_capability_registry_reports_all_planned_language_tiers() -> None:
    capabilities = capability_registry()

    assert capabilities["python"] == "structured"
    assert {capabilities[language] for language in ("javascript", "typescript", "go", "rust")} == {"best_effort"}
    for language in ("java", "kotlin", "ruby", "php", "terraform", "dockerfile", "protobuf", "graphql"):
        assert capabilities[language] in {"structured", "unavailable"}


def test_all_language_fixture_emits_honest_file_entities(tmp_path) -> None:
    fixture_root = Path(__file__).parent / "fixtures" / "architecture_languages"
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
    for name in manifest:
        _write(tmp_path / name, (fixture_root / name).read_text(encoding="utf-8"))

    snapshot = build_snapshot_for_ref(tmp_path)
    file_entities = {
        entity.locator.path: entity
        for entity in snapshot.entities
        if entity.entity_type in {"module", "config"}
    }

    assert set(manifest).issubset(file_entities)
    for path, language in manifest.items():
        assert file_entities[path].language == language
        assert file_entities[path].confidence_tier == snapshot.capabilities[language]


def test_architecture_snapshot_command_emits_json(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / "src" / "pkg" / "alpha.py", "def helper(value):\n    return value + 1\n")
    sha = _commit_all(tmp_path, "initial")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["architecture", "snapshot", "--ref", sha, "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["commit_sha"] == sha
    assert payload["schema_version"] == 1
    assert payload["entities"]


def _find_symbol(snapshot, display_name: str):
    for entity in snapshot.entities:
        if entity.entity_type == "symbol" and entity.display_name == display_name:
            return entity
    raise AssertionError(f"Missing symbol {display_name}")


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Codex"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "codex@example.com"], cwd=root, check=True, capture_output=True, text=True)


def _commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True, text=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
