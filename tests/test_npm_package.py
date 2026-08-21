from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_npm_package_version_matches_python_package() -> None:
    package = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    init_text = (ROOT / "src" / "agentpack" / "__init__.py").read_text(encoding="utf-8")
    init_version = re.search(r'__version__ = "([^"]+)"', init_text)

    assert init_version is not None
    assert package["version"] == _pyproject_version()
    assert package["version"] == init_version.group(1)


def test_npm_launcher_pins_matching_pypi_package() -> None:
    package = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    launcher = (ROOT / "npm" / "bin" / "agentpack.js").read_text(encoding="utf-8")

    assert f'const PACKAGE_VERSION = "{package["version"]}"' in launcher
    assert "agentpack-cli==" in launcher
    assert '"agentpack": "bin/agentpack.js"' in (ROOT / "npm" / "package.json").read_text(encoding="utf-8")
    assert "Windows is not supported yet" not in launcher


def test_npm_publish_workflow_preflights_scope_access() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-npm.yml").read_text(encoding="utf-8")

    assert "Verify npm scope access" in workflow
    assert "npm whoami" in workflow
    assert "npm access list packages" in workflow
    assert "NPM_TOKEN is authenticated as" in workflow


def test_publish_workflows_preflight_existing_versions() -> None:
    pypi_workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    npm_workflow = (ROOT / ".github" / "workflows" / "publish-npm.yml").read_text(encoding="utf-8")

    assert "check-pypi-version-available" in pypi_workflow
    assert "already exists on PyPI" in pypi_workflow
    assert 'TAG="${RELEASE_TAG}"' in pypi_workflow
    assert 'VERSION="${TAG#v}"' in pypi_workflow
    assert "Verify npm version is not already published" in npm_workflow
    assert "already exists on npm" in npm_workflow
