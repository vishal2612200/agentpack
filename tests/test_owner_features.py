from pathlib import Path

from agentpack.analysis.owner_features import build_owner_case_context, extract_owner_features
from agentpack.analysis.ranking import KeywordPlan
from agentpack.core.models import FileInfo
from agentpack.core.selection_models import adapt_ranked_candidate


def _candidate(path: str, reasons: list[str], score: float = 100.0):
    info = FileInfo(path=path, abs_path=Path(path), size_bytes=100, estimated_tokens=25)
    return adapt_ranked_candidate(info, score, reasons)


def _plan(*objects: str, scope: tuple[str, ...] = (), literals: tuple[str, ...] = ()) -> KeywordPlan:
    return KeywordPlan(
        weights={value: 1.0 for value in objects},
        generic_terms=(),
        ambiguous_terms=(),
        learned_ambiguous_terms=(),
        concrete_terms=objects,
        rarity={value: 1.0 for value in objects},
        literal_phrases=literals,
        task_scope_terms=scope,
    )


def _features(task: str, plan: KeywordPlan, candidates, summaries):
    context = build_owner_case_context(task, plan, candidates, summaries)
    return [extract_owner_features(candidate, summaries.get(candidate.path), context) for candidate in candidates]


def test_optimizer_owner_is_stronger_than_tests_and_middleware() -> None:
    candidates = [
        _candidate("packages/vite/src/node/optimizer/optimizer.ts", ["matched define: optimizeDeps", "multi-term path match +70"]),
        _candidate("packages/vite/src/node/optimizer/__tests__/optimizer.spec.ts", ["matched call: optimizeDeps", "content keyword match (3)"]),
        _candidate("packages/vite/src/node/server/middlewares/transform.ts", ["matched call: optimizeDeps"]),
    ]
    summaries = {
        candidates[0].path: {"defines": ["optimizeDeps"], "role": "dependency optimizer"},
        candidates[1].path: {"calls": ["optimizeDeps"], "role": "optimizer tests"},
        candidates[2].path: {"calls": ["optimizeDeps"], "role": "transform middleware"},
    }

    owner, test, middleware = _features("fix optimize deps", _plan("optimize", "deps"), candidates, summaries)

    assert "definition" in owner.anchor_codes
    assert "summary_definition" in owner.corroboration_codes
    assert test.anchor_codes == ()
    assert "broad_test_match" in test.penalty_codes
    assert "call_site_only" in middleware.penalty_codes


def test_css_and_import_analysis_owners_beat_broad_plugin_tests() -> None:
    candidates = [
        _candidate("packages/vite/src/node/plugins/css.ts", ["matched define: cssPlugin"]),
        _candidate("packages/vite/src/node/plugins/importAnalysis.ts", ["matched define: importAnalysisPlugin"]),
        _candidate("packages/vite/src/node/__tests__/plugins.spec.ts", ["matched call: cssPlugin", "matched call: importAnalysisPlugin"]),
    ]
    summaries = {
        candidates[0].path: {"defines": ["cssPlugin"], "role": "css plugin"},
        candidates[1].path: {"defines": ["importAnalysisPlugin"], "role": "import analysis plugin"},
        candidates[2].path: {"calls": ["cssPlugin", "importAnalysisPlugin"], "role": "plugin tests"},
    }
    css, imports, tests = _features("fix css import analysis plugins", _plan("css", "import", "analysis"), candidates, summaries)

    assert css.anchor_codes == ("definition",)
    assert imports.anchor_codes == ("definition",)
    assert tests.anchor_codes == ()
    assert "broad_test_match" in tests.penalty_codes


def test_nested_fixture_package_is_not_a_release_owner() -> None:
    candidate = _candidate("packages/vite/fixtures/legacy/package.json", ["build/dependency metadata"])
    features = _features("release vite", _plan("vite", scope=("vite",)), [candidate], {candidate.path: {}})[0]

    assert features.anchor_codes == ()
    assert "unrelated_metadata" in features.penalty_codes
    assert "generated_docs_example" in features.penalty_codes


def test_repeated_generic_definitions_are_not_unique() -> None:
    candidates = [
        _candidate("src/a/plugin.ts", ["matched define: setup"]),
        _candidate("src/b/plugin.ts", ["matched define: setup"]),
    ]
    summaries = {candidate.path: {"defines": ["setup"]} for candidate in candidates}
    first, second = _features("fix setup plugin", _plan("setup", "plugin"), candidates, summaries)

    assert first.competing_anchor_count == 2
    assert second.competing_anchor_count == 2
    assert "non_unique_definition" in first.penalty_codes
    assert "non_unique_definition" in second.penalty_codes
