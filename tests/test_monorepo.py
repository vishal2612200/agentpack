from __future__ import annotations

import json
from pathlib import Path

from agentpack.analysis.monorepo import (
    detect_workspace_dependency_edges,
    detect_workspace_roots,
    normalize_workspace,
    workspace_for_path,
    workspace_tokens,
)


def test_detect_package_json_workspaces(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": ["apps/*", "packages/*"]}),
        encoding="utf-8",
    )
    for rel in ("apps/dashboard", "packages/core"):
        path = tmp_path / rel
        path.mkdir(parents=True)
        (path / "package.json").write_text("{}", encoding="utf-8")
    ignored = tmp_path / "packages" / "generated"
    ignored.mkdir(parents=True)

    assert detect_workspace_roots(tmp_path) == ["apps/dashboard", "packages/core"]


def test_detect_pnpm_and_cargo_workspaces(tmp_path: Path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n', encoding="utf-8")
    for rel, marker in (("apps/web", "package.json"), ("crates/engine", "Cargo.toml")):
        path = tmp_path / rel
        path.mkdir(parents=True)
        (path / marker).write_text("{}", encoding="utf-8")

    assert detect_workspace_roots(tmp_path) == ["apps/web", "crates/engine"]


def test_workspace_for_path_uses_deepest_workspace() -> None:
    roots = ["apps", "apps/dashboard", "packages/core"]

    assert workspace_for_path("apps/dashboard/src/page.tsx", roots) == "apps/dashboard"
    assert workspace_for_path("packages/core/src/index.ts", roots) == "packages/core"
    assert workspace_for_path("README.md", roots) is None


def test_workspace_tokens_split_names() -> None:
    assert {"dashboard", "web"} <= workspace_tokens("apps/dashboard-web")


def test_detect_workspace_dependency_edges_from_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["apps/*", "packages/*"]}), encoding="utf-8")
    app = tmp_path / "apps" / "web"
    shared = tmp_path / "packages" / "shared"
    app.mkdir(parents=True)
    shared.mkdir(parents=True)
    app.joinpath("package.json").write_text(
        json.dumps({"name": "@acme/web", "dependencies": {"@acme/shared": "workspace:*"}}),
        encoding="utf-8",
    )
    shared.joinpath("package.json").write_text(json.dumps({"name": "@acme/shared"}), encoding="utf-8")

    roots = detect_workspace_roots(tmp_path)
    edges = detect_workspace_dependency_edges(tmp_path, roots)

    assert edges["apps/web"] == {"packages/shared"}


def test_detect_workspace_dependency_edges_from_cargo_path_dependencies(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n', encoding="utf-8")
    api = tmp_path / "crates" / "api"
    core = tmp_path / "crates" / "core"
    api.mkdir(parents=True)
    core.mkdir(parents=True)
    api.joinpath("Cargo.toml").write_text(
        '[package]\nname = "api"\nversion = "0.1.0"\n\n[dependencies]\ncore = { path = "../core" }\n',
        encoding="utf-8",
    )
    core.joinpath("Cargo.toml").write_text('[package]\nname = "core"\nversion = "0.1.0"\n', encoding="utf-8")

    roots = detect_workspace_roots(tmp_path)
    edges = detect_workspace_dependency_edges(tmp_path, roots)

    assert edges["crates/api"] == {"crates/core"}


def test_detect_workspace_dependency_edges_from_go_modules(tmp_path: Path) -> None:
    (tmp_path / "go.work").write_text("go 1.22\n\nuse (\n  ./services/api\n  ./packages/shared\n)\n", encoding="utf-8")
    api = tmp_path / "services" / "api"
    shared = tmp_path / "packages" / "shared"
    api.mkdir(parents=True)
    shared.mkdir(parents=True)
    api.joinpath("go.mod").write_text(
        "module example.com/api\n\n"
        "go 1.22\n\n"
        "require example.com/shared v0.0.0\n\n"
        "replace example.com/shared => ../../packages/shared\n",
        encoding="utf-8",
    )
    shared.joinpath("go.mod").write_text("module example.com/shared\n\ngo 1.22\n", encoding="utf-8")

    roots = detect_workspace_roots(tmp_path)
    edges = detect_workspace_dependency_edges(tmp_path, roots)

    assert edges["services/api"] == {"packages/shared"}


def test_normalize_workspace() -> None:
    assert normalize_workspace("/apps/web/") == "apps/web"


def test_go_workspace_multi_require_replace_edge_detection(tmp_path: Path) -> None:
    # 1. Create a fake go.work file content as a string
    go_work_content = """go 1.22

use (
\t./services/api
\t./packages/shared
\t./packages/core
)
"""
    (tmp_path / "go.work").write_text(go_work_content, encoding="utf-8")

    # Create workspace directories
    api = tmp_path / "services" / "api"
    shared = tmp_path / "packages" / "shared"
    core = tmp_path / "packages" / "core"

    api.mkdir(parents=True)
    shared.mkdir(parents=True)
    core.mkdir(parents=True)

    # 2. Create the go.mod files with multiple require/replace blocks,
    # including at least two local modules and one external module replacement.
    api_go_mod = """module example.com/api

go 1.22

require (
\texample.com/shared v0.0.0
)

require (
\texample.com/core v0.0.0
\tgithub.com/external/module v1.0.0
)

replace (
\texample.com/shared => ../../packages/shared
)

replace (
\texample.com/core => ../../packages/core
\tgithub.com/external/module => github.com/external/module/v2 v2.0.0
)
"""
    api.joinpath("go.mod").write_text(api_go_mod, encoding="utf-8")
    shared.joinpath("go.mod").write_text("module example.com/shared\n\ngo 1.22\n", encoding="utf-8")
    core.joinpath("go.mod").write_text("module example.com/core\n\ngo 1.22\n", encoding="utf-8")

    # 3. Parse to extract dependency edges
    roots = detect_workspace_roots(tmp_path)
    edges = detect_workspace_dependency_edges(tmp_path, roots)

    # 4. Assert that only local workspace edges are returned
    assert edges["services/api"] == {"packages/shared", "packages/core"}

    # 5. Assert that external module replacements are NOT included in the edges
    for source, targets in edges.items():
        for target in targets:
            assert target in {"services/api", "packages/shared", "packages/core"}

    # Test the low-level string parsing functions directly
    from agentpack.analysis.monorepo import _go_replacements, _go_requires

    # Assert that all required modules are extracted from multiple require blocks
    requires = _go_requires(api_go_mod)
    assert requires == {"example.com/shared", "example.com/core", "github.com/external/module"}

    # Assert that replacements only map to local workspaces, and external ones are ignored
    replacements = _go_replacements(tmp_path, api, api_go_mod, roots)
    assert replacements == {
        "example.com/shared": "packages/shared",
        "example.com/core": "packages/core",
    }
