from __future__ import annotations

from agentpack.learning.episodes import record_episode
from agentpack.learning.graph_memory import retrieve_memory_chain


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
