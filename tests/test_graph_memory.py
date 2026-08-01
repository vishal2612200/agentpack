from __future__ import annotations

from agentpack.core.scanner import file_hash
from agentpack.learning.episodes import record_episode
from agentpack.learning.graph_memory import retrieve_memory_chain
from agentpack.learning.procedures import record_procedure


def test_graph_memory_stays_within_live_paths_and_keeps_failed_guidance(tmp_path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    record_episode(
        tmp_path,
        task="fix auth retry behavior",
        selected_files=["src/auth.py"],
        changed_files=["src/auth.py"],
        passed=True,
    )
    record_episode(
        tmp_path,
        task="fix auth retry behavior",
        selected_files=["src/auth.py"],
        changed_files=["src/auth.py"],
        passed=False,
    )

    chain = retrieve_memory_chain(
        tmp_path,
        task="fix auth retry behavior",
        live_paths=["src/auth.py"],
        live_entity_keys=["entity:auth"],
        architecture_edges=[
            {
                "edge_key": "edge:auth",
                "edge_type": "imports",
                "source_entity_key": "entity:auth",
                "target_entity_key": "entity:shared",
            }
        ],
    )

    assert chain["order"] == ["live_pr_entities", "architecture_one_hop", "compatible_episodes", "validated_procedures"]
    assert chain["candidate_boosts"] == {"src/auth.py": 12.0}
    assert all(item["path"] == "src/auth.py" for item in chain["compatible_episodes"])
    assert any(item["negative_guidance"] for item in chain["compatible_episodes"])
    assert chain["constraints"]["blocking_effect"] == "none"


def test_graph_memory_uses_current_node_identity_before_task_terms(tmp_path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    current_hash = file_hash(source)
    record_procedure(
        tmp_path,
        procedure_id="auth-incident-playbook",
        title="Inspect auth failure path",
        triggers=["auth", "retry"],
        steps=["inspect source"],
    )
    record_episode(
        tmp_path,
        task="old incident words only",
        selected_files=["src/auth.py"],
        changed_files=["src/auth.py"],
        passed=True,
        touched_nodes=[
            {
                "node_key": "node:auth-send",
                "path": "src/auth.py",
                "source_hash": current_hash,
            }
        ],
        procedure_ids=["auth-incident-playbook"],
    )
    record_episode(
        tmp_path,
        task="new incident words only",
        selected_files=["src/auth.py"],
        changed_files=["src/auth.py"],
        passed=True,
        touched_nodes=[
            {
                "node_key": "node:unrelated",
                "path": "src/auth.py",
                "source_hash": current_hash,
            }
        ],
    )

    chain = retrieve_memory_chain(
        tmp_path,
        task="new incident words only",
        live_paths=["src/auth.py"],
        live_entity_keys=["entity:auth"],
        entity_node_keys={"entity:auth": "node:auth-send"},
    )

    assert len(chain["compatible_episodes"]) == 1
    assert chain["compatible_episodes"][0]["retrieval_source"] == "node_identity"
    assert chain["compatible_episodes"][0]["matched_node_keys"] == ["node:auth-send"]
    assert chain["validated_procedures"][0]["procedure_id"] == "auth-incident-playbook"
    assert chain["constraints"]["episode_gate"] == "current node identity"


def test_graph_memory_traverses_one_hop_without_boosting_unrelated_path(tmp_path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    current_hash = file_hash(source)
    record_procedure(
        tmp_path,
        procedure_id="auth-incident-playbook",
        title="Inspect auth failure path",
        triggers=["auth"],
        steps=["inspect source"],
    )
    record_episode(
        tmp_path,
        task="unrelated historical task",
        selected_files=["src/auth.py"],
        changed_files=["src/auth.py"],
        passed=True,
        touched_nodes=[
            {
                "node_key": "node:auth-send",
                "path": "src/auth.py",
                "source_hash": current_hash,
            }
        ],
        procedure_ids=["auth-incident-playbook"],
    )

    chain = retrieve_memory_chain(
        tmp_path,
        task="review route update",
        live_paths=["src/routes.py"],
        live_entity_keys=["entity:route"],
        architecture_edges=[
            {
                "edge_key": "edge:route-auth",
                "edge_type": "calls",
                "source_entity_key": "entity:route",
                "target_entity_key": "entity:auth",
            }
        ],
        entity_node_keys={"entity:auth": "node:auth-send"},
    )

    assert chain["architecture_one_hop"][0]["target_entity_key"] == "entity:auth"
    assert chain["compatible_episodes"][0]["matched_node_keys"] == ["node:auth-send"]
    assert chain["validated_procedures"][0]["procedure_id"] == "auth-incident-playbook"
    assert chain["candidate_boosts"] == {}


def test_graph_memory_rejects_stale_matched_node(tmp_path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    record_episode(
        tmp_path,
        task="auth incident",
        selected_files=["src/auth.py"],
        changed_files=["src/auth.py"],
        passed=True,
        touched_nodes=[
            {
                "node_key": "node:auth-send",
                "path": "src/auth.py",
                "source_hash": file_hash(source),
            }
        ],
    )
    source.write_text("VALUE = 2\n", encoding="utf-8")

    chain = retrieve_memory_chain(
        tmp_path,
        task="auth incident",
        live_paths=["src/auth.py"],
        live_entity_keys=["entity:auth"],
        entity_node_keys={"entity:auth": "node:auth-send"},
    )

    assert chain["compatible_episodes"] == []
    assert chain["candidate_boosts"] == {}


def test_graph_memory_validates_against_reviewed_source_hashes(tmp_path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    reviewed_hash = file_hash(source)
    record_episode(
        tmp_path,
        task="auth incident",
        selected_files=["src/auth.py"],
        changed_files=["src/auth.py"],
        passed=True,
        touched_nodes=[{"node_key": "node:auth-send", "path": "src/auth.py", "source_hash": reviewed_hash}],
    )
    source.write_text("VALUE = 2\n", encoding="utf-8")

    chain = retrieve_memory_chain(
        tmp_path,
        task="auth incident",
        live_paths=["src/auth.py"],
        live_entity_keys=["entity:auth"],
        entity_node_keys={"entity:auth": "node:auth-send"},
        current_source_hashes={"src/auth.py": reviewed_hash},
    )

    assert len(chain["compatible_episodes"]) == 1
