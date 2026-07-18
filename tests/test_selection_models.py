from pathlib import Path

import pytest

from agentpack.core.models import SelectedFile
from agentpack.core.selection_models import (
    CandidateEvidence,
    RepresentationOption,
    SelectionEngine,
    SelectionPlan,
    adapt_ranked_candidate,
)


def _selected(path: str = "src/service.py") -> SelectedFile:
    return SelectedFile(
        path=path,
        score=120.0,
        include_mode="summary",
        reasons=["matched define: Service"],
        summary="service summary",
    )


def test_candidate_evidence_rejects_strength_outside_contract() -> None:
    with pytest.raises(ValueError, match="owner_strength"):
        CandidateEvidence(owner_strength=4, support_strength=0, carrier_strength=0)


def test_representation_option_requires_matching_selected_path() -> None:
    with pytest.raises(ValueError, match="selected_file.path"):
        RepresentationOption(
            path="src/other.py",
            mode="summary",
            token_cost=20,
            coverage_level=1,
            selected_file=_selected(),
        )


def test_selection_plan_requires_exact_total_tokens() -> None:
    option = RepresentationOption(
        path="src/service.py",
        mode="summary",
        token_cost=20,
        coverage_level=1,
        selected_file=_selected(),
    )
    with pytest.raises(ValueError, match="total_tokens"):
        SelectionPlan(selected=(option,), rejected=(), total_tokens=19, objective=(1,))


def test_ranked_candidate_adapter_preserves_legacy_reasons() -> None:
    file_info = type(
        "Candidate",
        (),
        {"path": "src/service.py", "estimated_tokens": 40, "abs_path": Path("src/service.py")},
    )()

    candidate = adapt_ranked_candidate(
        file_info,
        120.0,
        ["matched define: Service", "content keyword: service"],
    )

    assert candidate.path == "src/service.py"
    assert candidate.score == 120.0
    assert candidate.legacy_reasons == (
        "matched define: Service",
        "content keyword: service",
    )
    assert candidate.evidence.owner_strength == 2
    assert candidate.evidence.carrier_strength == 1
    assert candidate.evidence.codes == ("definition", "content")


def test_selection_engine_values_are_stable() -> None:
    assert [engine.value for engine in SelectionEngine] == ["v1", "v2", "shadow"]
