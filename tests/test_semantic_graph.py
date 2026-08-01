from __future__ import annotations

from pathlib import Path

import agentpack.architecture.semantic_graph as semantic_graph_module
from agentpack.architecture.index import SemanticGraphIndex
from agentpack.architecture.service import build_snapshot_for_ref


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_snapshot_preserves_semantic_edges_comments_and_unresolved_targets(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/auth.py",
        "# token validation rationale\nclass AuthService:\n    \"\"\"validates access tokens\"\"\"\n    def validate_token(self, token):\n        return TokenStore.lookup(token)\n\nclass TokenStore:\n    @staticmethod\n    def lookup(token):\n        return token\n",
    )
    _write(tmp_path, "tests/test_auth.py", "def test_validate_token():\n    return AuthService().validate_token('x')\n")
    _write(tmp_path, "README.md", "See [auth](src/auth.py).\n")

    snapshot = build_snapshot_for_ref(tmp_path)
    edge_types = {edge.edge_type for edge in snapshot.edges}
    entity_types = {entity.entity_type for entity in snapshot.entities}

    assert {"contains", "calls", "references", "tested_by", "documents"} <= edge_types
    assert {"comment", "document", "unresolved"} <= entity_types
    assert snapshot.schema_version == 8
    assert all(edge.evidence and edge.evidence[0].path for edge in snapshot.edges)
    assert all(entity.source_hash and entity.entity_key for entity in snapshot.entities)


def test_graph_index_resolves_neighbors_and_evidence(tmp_path: Path) -> None:
    _write(tmp_path, "src/store.py", "class TokenStore:\n    def lookup(self, value):\n        return value\n")
    _write(tmp_path, "src/auth.py", "from .store import TokenStore\n\ndef validate(value):\n    return TokenStore().lookup(value)\n")

    index = SemanticGraphIndex(build_snapshot_for_ref(tmp_path))
    matches = index.query("validate", limit=5)
    assert matches
    neighbors = index.neighbors("src.auth", limit=20)
    assert any(row["relationship"] == "imports" for row in neighbors)
    edge = next(row for row in neighbors if row["relationship"] == "imports")
    assert edge["evidence"][0]["path"] == "src/auth.py"


def test_ambiguous_symbol_target_is_preserved_without_false_resolution(tmp_path: Path) -> None:
    _write(tmp_path, "src/one.py", "def render(value):\n    return value\n")
    _write(tmp_path, "src/two.py", "def render(value):\n    return value\n")
    _write(tmp_path, "src/use.py", "def run(value):\n    return render(value)\n")

    snapshot = build_snapshot_for_ref(tmp_path)
    calls = [
        edge for edge in snapshot.edges
        if edge.edge_type == "calls"
        and any(evidence.path == "src/use.py" for evidence in edge.evidence)
    ]
    assert calls
    target = next(entity for entity in snapshot.entities if entity.entity_key == calls[0].target_entity_key)
    assert target.entity_type == "unresolved"
    assert target.metadata["resolution"] == "ambiguous"


def test_import_alias_resolves_symbol_calls(tmp_path: Path) -> None:
    _write(tmp_path, "src/store.py", "class TokenStore:\n    def lookup(self, value):\n        return value\n")
    _write(
        tmp_path,
        "src/auth.py",
        "from .store import TokenStore as TS\n\ndef validate(value):\n    return TS().lookup(value)\n",
    )

    snapshot = build_snapshot_for_ref(tmp_path)
    call_targets = [
        entity for edge in snapshot.edges
        if edge.edge_type == "calls"
        for entity in snapshot.entities
        if entity.entity_key == edge.target_entity_key
        and any(evidence.path == "src/auth.py" for evidence in edge.evidence)
    ]
    assert any(entity.display_name == "lookup" for entity in call_targets)


def test_snapshot_output_is_deterministic_for_same_worktree(tmp_path: Path) -> None:
    _write(tmp_path, "src/service.py", "def run():\n    return missing_dependency()\n")
    first = build_snapshot_for_ref(tmp_path)
    second = build_snapshot_for_ref(tmp_path)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_worktree_cache_invalidates_when_file_hash_changes(tmp_path: Path) -> None:
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("def old_name():\n    return 1\n", encoding="utf-8")
    first = build_snapshot_for_ref(tmp_path)
    target.write_text("def new_name():\n    return 2\n", encoding="utf-8")
    second = build_snapshot_for_ref(tmp_path)
    assert any(entity.display_name == "old_name" for entity in first.entities)
    assert not any(entity.display_name == "old_name" for entity in second.entities)
    assert any(entity.display_name == "new_name" for entity in second.entities)


def test_fact_cache_reuses_unchanged_files_after_manifest_change(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "src/auth.py", "def validate():\n    return store()\n")
    _write(tmp_path, "src/store.py", "def store():\n    return True\n")
    original = semantic_graph_module.extract_semantic_facts
    calls: list[str] = []

    def counted(path, language, *args, **kwargs):
        calls.append(str(path))
        return original(path, language, *args, **kwargs)

    monkeypatch.setattr(semantic_graph_module, "extract_semantic_facts", counted)
    build_snapshot_for_ref(tmp_path)
    assert len(calls) == 2

    calls.clear()
    (tmp_path / "src" / "auth.py").write_text(
        "def validate():\n    return store_v2()\n", encoding="utf-8"
    )
    build_snapshot_for_ref(tmp_path)

    assert calls == [str(tmp_path / "src" / "auth.py")]


def test_graph_cache_reports_reuse_and_invalidation_stats(tmp_path: Path) -> None:
    _write(tmp_path, "src/store.py", "def store():\n    return True\n")
    _write(tmp_path, "src/auth.py", "from .store import store\n\ndef validate():\n    return store()\n")

    first = build_snapshot_for_ref(tmp_path)
    assert first.cache_stats["parsed_files"] == 2
    assert first.cache_stats["reused_files"] == 0

    (tmp_path / "src" / "store.py").write_text("def store():\n    return False\n", encoding="utf-8")
    second = build_snapshot_for_ref(tmp_path)
    assert second.cache_stats["parsed_files"] == 1
    assert second.cache_stats["reused_files"] == 1
    assert second.cache_stats["changed_files"] == 1
    assert second.cache_stats["affected_files"] == 2


def test_graph_cache_removes_deleted_file_record(tmp_path: Path) -> None:
    _write(tmp_path, "src/old.py", "def old():\n    return 1\n")
    first = build_snapshot_for_ref(tmp_path)
    assert any(entity.locator.path == "src/old.py" for entity in first.entities)

    (tmp_path / "src" / "old.py").unlink()
    second = build_snapshot_for_ref(tmp_path)
    assert not any(entity.locator.path == "src/old.py" for entity in second.entities)
    assert second.cache_stats["deleted_files"] == 1


def test_typescript_tsconfig_path_alias_resolves_import(tmp_path: Path) -> None:
    _write(tmp_path, "tsconfig.json", '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}\n')
    _write(tmp_path, "src/store.ts", "export function lookup(value: string) { return value; }\n")
    _write(tmp_path, "src/auth.ts", "import { lookup } from '@/store';\nexport function validate(value: string) { return lookup(value); }\n")

    snapshot = build_snapshot_for_ref(tmp_path)
    imports = [edge for edge in snapshot.edges if edge.edge_type == "imports" and edge.evidence[0].path == "src/auth.ts"]
    assert imports
    target = next(entity for entity in snapshot.entities if entity.entity_key == imports[0].target_entity_key)
    assert target.locator.path == "src/store.ts"


def test_unresolved_external_import_is_retained_with_evidence(tmp_path: Path) -> None:
    _write(tmp_path, "src/service.py", "import requests\n\ndef fetch():\n    return requests.get('https://example.test')\n")

    snapshot = build_snapshot_for_ref(tmp_path)
    imports = [edge for edge in snapshot.edges if edge.edge_type == "imports"]
    assert imports
    target = next(entity for entity in snapshot.entities if entity.entity_key == imports[0].target_entity_key)
    assert target.entity_type == "external"
    assert target.metadata["resolution"] == "unresolved"
    assert imports[0].evidence[0].path == "src/service.py"


def test_api_routes_and_event_effects_are_graph_entities(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/api.py",
        "@app.get('/tokens')\ndef tokens():\n    publish('token.created')\n    return True\n",
    )

    snapshot = build_snapshot_for_ref(tmp_path)
    api_entities = [entity for entity in snapshot.entities if entity.entity_type == "api"]
    assert any(entity.metadata.get("route") == "/tokens" for entity in api_entities)
    assert any(edge.edge_type == "publishes" for edge in snapshot.edges)
    route_edge = next(edge for edge in snapshot.edges if edge.edge_type == "contains" and edge.evidence[0].note == "API route declaration")
    assert route_edge.evidence[0].path == "src/api.py"


def test_typescript_workspace_exports_and_index_resolution(tmp_path: Path) -> None:
    _write(tmp_path, "packages/core/package.json", '{"name":"@acme/core","exports":{"./store":"./src/store.ts",".":"./src/index.ts"}}\n')
    _write(tmp_path, "packages/core/src/store.ts", "export class TokenStore {}\n")
    _write(tmp_path, "packages/core/src/index.ts", "export { TokenStore } from './store';\n")
    _write(tmp_path, "apps/api/package.json", '{"name":"@acme/api"}\n')
    _write(tmp_path, "apps/api/src/auth.ts", "import { TokenStore } from '@acme/core/store';\nexport function validate(store: TokenStore) { return store; }\n")

    snapshot = build_snapshot_for_ref(tmp_path)
    imports = [edge for edge in snapshot.edges if edge.edge_type == "imports" and edge.evidence[0].path == "apps/api/src/auth.ts"]
    assert imports
    target = next(entity for entity in snapshot.entities if entity.entity_key == imports[0].target_entity_key)
    assert target.locator.path == "packages/core/src/store.ts"


def test_java_maven_and_kotlin_gradle_package_resolution(tmp_path: Path) -> None:
    _write(tmp_path, "pom.xml", "<project><modelVersion>4.0.0</modelVersion></project>\n")
    _write(tmp_path, "src/main/java/auth/TokenStore.java", "package auth;\npublic class TokenStore {}\n")
    _write(tmp_path, "src/main/java/auth/AuthService.java", "package auth;\nimport auth.TokenStore;\nclass AuthService { TokenStore store; }\n")
    _write(tmp_path, "build.gradle.kts", "plugins { kotlin(\"jvm\") version \"1.9.0\" }\n")
    _write(tmp_path, "src/main/kotlin/auth/TokenContract.kt", "package auth\ninterface TokenContract\n")
    _write(tmp_path, "src/main/kotlin/auth/KotlinService.kt", "package auth\nclass KotlinService : TokenContract\n")

    snapshot = build_snapshot_for_ref(tmp_path)
    assert any(
        edge.edge_type == "imports"
        and edge.evidence[0].path == "src/main/java/auth/AuthService.java"
        and next(entity for entity in snapshot.entities if entity.entity_key == edge.target_entity_key).locator.path == "src/main/java/auth/TokenStore.java"
        for edge in snapshot.edges
    )
    assert any(edge.edge_type == "implements" and edge.evidence[0].path == "src/main/kotlin/auth/KotlinService.kt" for edge in snapshot.edges)


def test_ruby_load_path_and_php_composer_psr4_resolution(tmp_path: Path) -> None:
    _write(tmp_path, "Gemfile", "source 'https://rubygems.org'\n")
    _write(tmp_path, "lib/auth/token_store.rb", "module Auth\n  class TokenStore; end\nend\n")
    _write(tmp_path, "app.rb", "require 'auth/token_store'\n")
    _write(tmp_path, "composer.json", '{"autoload":{"psr-4":{"App\\\\":"src/"}}}\n')
    _write(tmp_path, "src/Service/TokenStore.php", "<?php\nnamespace App\\Service;\nclass TokenStore {}\n")
    _write(tmp_path, "src/Service/AuthService.php", "<?php\nnamespace App\\Service;\nuse App\\Service\\TokenStore;\nclass AuthService { private TokenStore $store; }\n")

    snapshot = build_snapshot_for_ref(tmp_path)
    assert any(edge.edge_type == "imports" and edge.evidence[0].path == "app.rb" for edge in snapshot.edges)
    assert any(edge.edge_type == "imports" and edge.evidence[0].path == "src/Service/AuthService.php" for edge in snapshot.edges)
