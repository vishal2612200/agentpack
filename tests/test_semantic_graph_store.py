from __future__ import annotations

import json
import subprocess
from pathlib import Path

import agentpack.architecture.semantic_graph as semantic_graph_module
import agentpack.architecture.store as semantic_store_module
from agentpack.architecture.service import build_snapshot_for_ref


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_incremental_materialization_reuses_unaffected_records(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "src/provider.py", "def lookup(value):\n    return value\n")
    _write(tmp_path, "src/consumer.py", "from .provider import lookup\ndef run(value):\n    return lookup(value)\n")
    _write(tmp_path, "src/unrelated.py", "def unrelated():\n    return 1\n")
    _write(tmp_path, "src/unrelated_two.py", "def unrelated_two():\n    return 2\n")
    _write(tmp_path, "src/unrelated_three.py", "def unrelated_three():\n    return 3\n")
    calls: list[str] = []
    original = semantic_graph_module.extract_semantic_facts

    def counted(path, language, *args, **kwargs):
        calls.append(str(path))
        return original(path, language, *args, **kwargs)

    monkeypatch.setattr(semantic_graph_module, "extract_semantic_facts", counted)
    first = build_snapshot_for_ref(tmp_path)
    assert first.cache_stats["build_mode"] == "cold"
    assert len(calls) == 5

    calls.clear()
    before = {
        entity.entity_key: entity.model_dump(mode="json")
        for entity in first.entities
        if entity.locator.path == "src/unrelated.py"
    }
    _write(tmp_path, "src/unrelated.py", "def unrelated():\n    return 2\n")
    second = build_snapshot_for_ref(tmp_path)

    assert calls == [str(tmp_path / "src/unrelated.py")]
    assert second.cache_stats["build_mode"] == "incremental"
    assert second.cache_stats["parsed_files"] == 1
    assert second.cache_stats["reused_records"] == 4
    after = {
        entity.entity_key: entity.model_dump(mode="json")
        for entity in second.entities
        if entity.locator.path == "src/provider.py"
    }
    assert before == {
        entity.entity_key: entity.model_dump(mode="json")
        for entity in first.entities
        if entity.locator.path == "src/unrelated.py"
    }
    assert after

    cold = build_snapshot_for_ref(tmp_path, cold=True, verify_incremental=False)
    assert second.model_dump(mode="json") == cold.model_dump(mode="json")


def test_materialized_state_references_canonical_snapshot(tmp_path: Path) -> None:
    _write(tmp_path, "src/service.py", "def run():\n    return 1\n")
    snapshot = build_snapshot_for_ref(tmp_path)

    cache = tmp_path / ".agentpack" / "architecture"
    state_path = next((cache / "state").glob("*.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["snapshot"] is None
    assert state["snapshot_path"]
    canonical = cache / state["snapshot_path"]
    assert json.loads(canonical.read_text(encoding="utf-8"))["commit_sha"] == snapshot.commit_sha


def test_manifest_route_validation_skips_record_deserialization_on_warm_cache(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "src/provider.py", "def lookup(value):\n    return value\n")
    first = build_snapshot_for_ref(tmp_path)
    calls = 0

    def fail_full_validation(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("warm manifest validation should not deserialize semantic records")

    monkeypatch.setattr(semantic_store_module.SemanticGraphStore, "validate_cached_snapshot", fail_full_validation)
    second = build_snapshot_for_ref(tmp_path, cache_validation="manifest")

    assert second.model_dump(mode="json") == first.model_dump(mode="json")
    assert calls == 0


def test_provider_change_invalidates_importers_but_not_unrelated_files(tmp_path: Path) -> None:
    _write(tmp_path, "src/provider.py", "def lookup(value):\n    return value\n")
    _write(tmp_path, "src/consumer.py", "from .provider import lookup\ndef run(value):\n    return lookup(value)\n")
    _write(tmp_path, "src/unrelated.py", "def unrelated():\n    return 1\n")
    _write(tmp_path, "src/unrelated_two.py", "def unrelated_two():\n    return 2\n")
    _write(tmp_path, "src/unrelated_three.py", "def unrelated_three():\n    return 3\n")
    build_snapshot_for_ref(tmp_path)

    _write(tmp_path, "src/provider.py", "def lookup(value):\n    return value + 1\n")
    snapshot = build_snapshot_for_ref(tmp_path)

    assert snapshot.cache_stats["build_mode"] == "incremental"
    assert snapshot.cache_stats["affected_files"] >= 2
    assert snapshot.cache_stats["re_resolved_relationships"] >= 1
    assert snapshot.cache_stats["parsed_files"] == 1


def test_deleted_file_removes_materialized_records_and_edges(tmp_path: Path) -> None:
    _write(tmp_path, "src/provider.py", "def lookup(value):\n    return value\n")
    _write(tmp_path, "src/consumer.py", "from .provider import lookup\ndef run(value):\n    return lookup(value)\n")
    build_snapshot_for_ref(tmp_path)
    records_dir = tmp_path / ".agentpack" / "architecture" / "records"
    assert list(records_dir.glob("*.json"))

    (tmp_path / "src" / "provider.py").unlink()
    snapshot = build_snapshot_for_ref(tmp_path)
    assert not any(entity.locator.path == "src/provider.py" for entity in snapshot.entities)
    assert snapshot.cache_stats["deleted_files"] == 1
    assert all("src/provider.py" not in (evidence.path for evidence in edge.evidence) for edge in snapshot.edges)

    state = next((tmp_path / ".agentpack" / "architecture" / "state").glob("*.json"))
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert "src/provider.py" not in payload["record_keys"]


def test_corrupt_materialized_record_forces_cold_fallback(tmp_path: Path) -> None:
    _write(tmp_path, "src/service.py", "def run():\n    return 1\n")
    build_snapshot_for_ref(tmp_path)
    record = next((tmp_path / ".agentpack" / "architecture" / "records").glob("*.json"))
    record.write_text("not-json", encoding="utf-8")
    _write(tmp_path, "src/service.py", "def run():\n    return 2\n")

    snapshot = build_snapshot_for_ref(tmp_path)
    assert snapshot.cache_stats["build_mode"] == "cold"
    assert snapshot.cache_stats["fallback_to_cold"] is False
    assert snapshot.cache_stats["parsed_files"] == 1


def test_materialized_record_contains_entities_edges_and_evidence(tmp_path: Path) -> None:
    _write(tmp_path, "src/provider.py", "# rationale\ndef lookup(value):\n    return value\n")
    _write(tmp_path, "src/consumer.py", "from .provider import lookup\ndef run(value):\n    return lookup(value)\n")
    build_snapshot_for_ref(tmp_path)

    records = list((tmp_path / ".agentpack" / "architecture" / "records").glob("*.json"))
    assert records
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["file_entity"]["entity_type"] == "module"
    assert payload["symbol_entities"]
    assert payload["local_edges"]
    assert payload["source_evidence"]
    assert all(edge["evidence"] for edge in payload["local_edges"])


def test_semantic_materialization_does_not_call_legacy_dependency_builder(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "src/provider.py", "def lookup(value):\n    return value\n")
    _write(tmp_path, "src/consumer.py", "from .provider import lookup\ndef run(value):\n    return lookup(value)\n")

    import agentpack.analysis.dependency_graph as dependency_graph

    monkeypatch.setattr(dependency_graph, "build", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy builder called")))
    snapshot = build_snapshot_for_ref(tmp_path)
    assert any(edge.edge_type == "imports" for edge in snapshot.edges)


def test_fast_path_validates_materialized_records(tmp_path: Path) -> None:
    _write(tmp_path, "src/service.py", "def run():\n    return 1\n")
    first = build_snapshot_for_ref(tmp_path)
    record = next((tmp_path / ".agentpack" / "architecture" / "records").glob("*.json"))
    record.write_text("not-json", encoding="utf-8")

    second = build_snapshot_for_ref(tmp_path)
    assert second.cache_stats["cache_invalid_reason"] == "record_corrupt"
    assert second.model_dump(mode="json") == first.model_dump(mode="json")


def test_incremental_equivalence_verification_is_opt_in(tmp_path: Path) -> None:
    _write(tmp_path, "src/service.py", "def run():\n    return 1\n")
    _write(tmp_path, "src/other.py", "def other():\n    return 1\n")
    _write(tmp_path, "src/third.py", "def third():\n    return 1\n")
    build_snapshot_for_ref(tmp_path)
    _write(tmp_path, "src/service.py", "def run():\n    return 2\n")

    normal = build_snapshot_for_ref(tmp_path)
    assert normal.cache_stats["build_mode"] == "incremental"
    assert normal.cache_stats["cold_build_seconds"] == 0.0

    _write(tmp_path, "src/service.py", "def run():\n    return 3\n")
    verified = build_snapshot_for_ref(tmp_path, verify_incremental=True)
    assert verified.cache_stats["cold_build_seconds"] > 0
    assert verified.cache_stats["fallback_to_cold"] is False


def test_unaffected_record_and_edges_are_byte_stable_after_unrelated_change(tmp_path: Path) -> None:
    _write(tmp_path, "src/provider.py", "def lookup(value):\n    return value\n")
    _write(tmp_path, "src/unrelated.py", "def unrelated():\n    return 1\n")
    first = build_snapshot_for_ref(tmp_path)
    state_path = next((tmp_path / ".agentpack" / "architecture" / "state").glob("*.json"))
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    provider_key = first_state["record_keys"]["src/provider.py"]
    record_path = tmp_path / ".agentpack" / "architecture" / "records" / f"{provider_key}.json"
    record_bytes = record_path.read_bytes()
    first_edges = {
        edge.edge_key: edge.model_dump(mode="json")
        for edge in first.edges
        if any(evidence.path == "src/provider.py" for evidence in edge.evidence)
    }

    _write(tmp_path, "src/unrelated.py", "def unrelated():\n    return 2\n")
    second = build_snapshot_for_ref(tmp_path)
    second_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert second_state["record_keys"]["src/provider.py"] == provider_key
    assert record_path.read_bytes() == record_bytes
    second_edges = {
        edge.edge_key: edge.model_dump(mode="json")
        for edge in second.edges
        if any(evidence.path == "src/provider.py" for evidence in edge.evidence)
    }
    assert second_edges == first_edges


def test_ref_namespaces_preserve_each_commit_snapshot(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "agentpack@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "AgentPack Tests"], cwd=tmp_path, check=True)
    _write(tmp_path, "src/service.py", "def old():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "old"], cwd=tmp_path, check=True)
    old_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    _write(tmp_path, "src/service.py", "def new():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "new"], cwd=tmp_path, check=True)
    new_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()

    old_snapshot = build_snapshot_for_ref(tmp_path, old_sha)
    build_snapshot_for_ref(tmp_path, new_sha)
    old_again = build_snapshot_for_ref(tmp_path, old_sha)

    assert old_again.model_dump(mode="json") == old_snapshot.model_dump(mode="json")
    state_files = list((tmp_path / ".agentpack" / "architecture" / "state").glob("*.json"))
    assert len(state_files) >= 2


def test_corrupt_record_ownership_and_edge_evidence_invalidates_cache(tmp_path: Path) -> None:
    _write(tmp_path, "src/service.py", "def run():\n    return 1\n")
    first = build_snapshot_for_ref(tmp_path)
    state_path = next((tmp_path / ".agentpack" / "architecture" / "state").glob("*.json"))
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    entity_key = next(iter(payload["entity_owners"]))
    payload["entity_owners"][entity_key] = "wrong/path.py"
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    second = build_snapshot_for_ref(tmp_path)
    assert second.cache_stats["cache_invalid_reason"] == "record_corrupt"
    assert second.model_dump(mode="json") == first.model_dump(mode="json")

    record_path = next((tmp_path / ".agentpack" / "architecture" / "records").glob("*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["local_edges"][0]["evidence"] = []
    record_path.write_text(json.dumps(record), encoding="utf-8")
    third = build_snapshot_for_ref(tmp_path)
    assert third.cache_stats["cache_invalid_reason"] == "record_corrupt"
