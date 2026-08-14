from pathlib import Path
from agentpack.application.pack_service import _boost_semantic_graph_neighbors
from agentpack.analysis.ranking import (
    ambiguous_task_terms,
    build_keyword_plan,
    boost_api_endpoint_pairs,
    boost_cross_layer_related,
    boost_frontend_api_consumers,
    boost_paired_tests,
    concrete_task_terms,
    extract_keywords,
    extract_keyword_weights,
    generic_task_term_ratio,
    persist_keyword_plan_stats,
    score_files,
    suggest_task_rewrite,
    task_phrases,
    task_terms,
)
from agentpack.core.models import FileInfo


def _fi(path: str, tokens: int = 100, language: str = "python") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=Path("/nonexistent") / path,
        size_bytes=tokens * 4,
        estimated_tokens=tokens,
        language=language,
    )


def test_semantic_graph_neighbor_boost_reads_nested_locators_and_evidence() -> None:
    changed = _fi("src/changed.py")
    related = _fi("src/related.py")

    class Graph:
        def neighbors(self, _path: str, *, limit: int) -> list[dict]:
            assert limit == 200
            return [
                {
                    "node": {"locator": {"path": "src/related.py"}},
                    "relationship": "imports",
                    "edge_key": "edge-1",
                    "evidence": [{"path": "src/imports.py", "start_line": 12}],
                }
            ]

    scored = _boost_semantic_graph_neighbors(
        [(changed, 1.0, []), (related, 1.0, [])],
        Graph(),
        {"src/changed.py"},
    )

    scores = {file_info.path: (score, reasons) for file_info, score, reasons in scored}
    assert scores["src/related.py"][0] > scores["src/changed.py"][0]
    assert "src/imports.py:12" in scores["src/related.py"][1][0]


def test_extract_keywords_basic():
    kw = extract_keywords("fix Redis SSE cancellation issue")
    assert "redis" in kw
    assert "cancel" in kw  # variant of cancellation
    assert "fix" in kw


def test_extract_keywords_removes_stopwords():
    kw = extract_keywords("the and or but")
    assert len(kw) == 0


def test_extract_keywords_variants():
    kw = extract_keywords("authentication configuration")
    assert "auth" in kw
    assert "config" in kw


def test_task_terms_parse_conventional_commit_metadata_and_keep_type_token():
    plan = build_keyword_plan("test(core): add deeply nested transient providers")

    terms = set(task_terms("test(core): add deeply nested transient providers"))
    assert "test" in terms
    assert "core" in terms
    assert plan.task_kind == "test"
    assert plan.task_scope_terms == ("core",)


def test_conventional_commit_kind_is_dynamic_metadata_not_concrete_signal():
    plan = build_keyword_plan("chore(parser): correct parseAst hints")

    assert plan.task_kind == "chore"
    assert "chore" in plan.generic_terms
    assert "chore" not in plan.concrete_terms
    assert plan.weights["chore"] <= 0.25
    assert "parser" in plan.concrete_terms
    assert "parseast" in plan.concrete_terms


def test_task_phrases_include_dynamic_trigrams():
    phrases = task_phrases("test(core): add deeply nested transient providers in scoped chains", max_len=3)

    assert "deeply nested" in phrases
    assert "deeply nested transient" in phrases
    assert "nested transient providers" in phrases
    assert "transient providers scoped" in phrases


def test_keyword_plan_keeps_default_phrase_ranking_to_bigrams():
    plan = build_keyword_plan("test(core): add deeply nested transient providers in scoped chains")

    assert "deeply nested" in plan.phrase_weights
    assert "deeply nested transient" not in plan.phrase_weights


def test_conventional_commit_scope_boosts_matching_non_test_package_path():
    core_file = _fi("packages/core/test/injector/injector.spec.ts", language="typescript")
    integration_noise = _fi("integration/scopes/src/nested-transient/transient.service.ts", language="typescript")
    scored = score_files(
        [integration_noise, core_file],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("fix(core): skip transient providers for snapshots"),
        summaries={
            integration_noise.path: {
                "role": "transient scope service",
                "defines": ["TransientService"],
                "ranking_keywords": ["transient", "scoped"],
            },
            core_file.path: {
                "role": "injector spec",
                "calls": ["request"],
                "ranking_keywords": ["transient", "providers"],
            },
        },
    )

    scores = {fi.path: (score, reasons) for fi, score, reasons in scored}
    assert scores[core_file.path][0] > scores[integration_noise.path][0]
    assert "conventional scope path match" in scores[core_file.path][1]


def test_conventional_commit_workspace_scope_dampens_wrong_workspace():
    core_file = _fi("packages/core/injector/injector.ts", language="typescript")
    integration_noise = _fi("integration/scopes/src/nested-transient/transient.service.ts", language="typescript")
    scored = score_files(
        [integration_noise, core_file],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan(
            "fix(core): skip transient providers for snapshots",
            workspace_roots=["packages/core"],
        ),
        summaries={
            integration_noise.path: {
                "role": "transient scope service",
                "defines": ["TransientService"],
                "ranking_keywords": ["transient", "providers"],
            },
            core_file.path: {
                "role": "injector",
                "calls": ["providers.set"],
                "ranking_keywords": ["providers"],
            },
        },
    )

    scores = {fi.path: (score, reasons) for fi, score, reasons in scored}
    assert scores[core_file.path][0] > scores[integration_noise.path][0]
    assert "conventional scope mismatch dampening" in scores[integration_noise.path][1]
    assert "conventional scope mismatch dampening" not in scores[core_file.path][1]


def test_conventional_commit_scope_without_workspace_root_does_not_dampen():
    scoped_file = _fi("packages/vite/src/types/ws.d.ts", language="typescript")
    expected_source = _fi("packages/vite/src/node/index.ts", language="typescript")
    scored = score_files(
        [scoped_file, expected_source],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan(
            "feat(types): add more precise typing for known query types",
            workspace_roots=["packages/vite"],
        ),
        summaries={
            scoped_file.path: {"defines": ["WebSocket"]},
            expected_source.path: {"defines": ["KnownQueryTypes"], "ranking_keywords": ["query"]},
        },
    )

    reasons = {fi.path: reasons for fi, _score, reasons in scored}
    assert "conventional scope mismatch dampening" not in reasons[expected_source.path]


def test_conventional_commit_scope_does_not_force_test_tasks_to_package_path():
    core_file = _fi("packages/core/test/injector/injector.spec.ts", language="typescript")
    integration_test = _fi("integration/scopes/e2e/scoped-instances.spec.ts", language="typescript")
    scored = score_files(
        [integration_test, core_file],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("test(core): add several tests for request-scoped providers"),
        summaries={
            integration_test.path: {"role": "scoped instances.spec", "calls": ["request"], "ranking_keywords": ["providers"]},
            core_file.path: {"role": "injector spec", "ranking_keywords": ["providers"]},
        },
    )

    scores = {fi.path: (score, reasons) for fi, score, reasons in scored}
    assert "conventional scope path match" not in scores[core_file.path][1]
    assert "conventional scope mismatch dampening" not in scores[integration_test.path][1]


def test_chore_literal_dampens_partial_filename_match_without_exact_literal(tmp_path):
    exact = FileInfo(
        path="packages/vite/src/node/server/openBrowser.ts",
        abs_path=tmp_path / "packages/vite/src/node/server/openBrowser.ts",
        size_bytes=120,
        estimated_tokens=120,
        language="typescript",
        content="const message = 'Use create-react-app migration links here'\n",
    )
    partial = FileInfo(
        path="packages/create-vite/src/index.ts",
        abs_path=tmp_path / "packages/create-vite/src/index.ts",
        size_bytes=120,
        estimated_tokens=120,
        language="typescript",
        content="setupReactCompiler(); createColors(); updateLinks();\n",
    )

    scored = score_files(
        [partial, exact],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("chore: update `create-react-app` links"),
        summaries={
            exact.path: {"calls": ["openBrowser"]},
            partial.path: {
                "defines": ["setupReactCompiler"],
                "calls": ["createColors"],
                "ranking_keywords": ["update"],
            },
        },
    )

    scores = {fi.path: (score, reasons) for fi, score, reasons in scored}
    assert scores[exact.path][0] > scores[partial.path][0]
    assert "chore literal partial-match dampening" in scores[partial.path][1]


def test_extract_keyword_weights_downweight_expanded_concepts():
    weights = extract_keyword_weights("queue")
    assert weights["queue"] == 1.0
    assert weights["task"] < weights["queue"]


def test_generic_task_terms_are_downweighted():
    weights = extract_keyword_weights("fix auth implementation")
    assert weights["fix"] < weights["auth"]
    assert weights["impl"] < weights["auth"]
    assert generic_task_term_ratio("fix release implementation task") >= 0.75
    assert generic_task_term_ratio("improve context pack quality from stats") >= 0.8


def test_ambiguous_task_terms_are_softened_but_preserved():
    weights = extract_keyword_weights("public analysis seo preview")
    assert weights["analysis"] < weights["seo"]
    assert weights["public"] < weights["seo"]
    assert ambiguous_task_terms("public analysis seo preview") == ["analysis", "preview", "public"]
    assert concrete_task_terms("public analysis seo preview") == ["seo"]


def test_task_rewrite_hint_prefers_scope_and_concrete_terms():
    hint = suggest_task_rewrite("Implement cost-safe public SEO tools with deterministic previews and signup-gated AI analysis")
    assert "frontend page/component work only" in hint
    assert "seo" in hint
    assert "no backend service or analysis changes" in hint


def test_keyword_plan_uses_repo_rarity_to_lift_concrete_terms():
    files = [
        _fi("src/common/preview_card.py"),
        _fi("src/common/preview_modal.py"),
        _fi("src/common/preview_shell.py"),
        _fi("src/seo/landing_tool.py"),
    ]
    plan = build_keyword_plan("preview seo", files=files)
    assert plan.weights["seo"] > plan.weights["preview"]
    assert plan.rarity["seo"] > plan.rarity["preview"]


def test_keyword_plan_learns_ambiguous_terms_from_noisy_history(tmp_path):
    metrics_dir = tmp_path / ".agentpack"
    metrics_dir.mkdir()
    (metrics_dir / "metrics.jsonl").write_text(
        "\n".join([
            '{"task":"public preview analysis flow","selection_token_precision":0.1,"selection_noise_paths":["src/noisy.py"]}',
            '{"task":"public preview analysis page","selection_token_precision":0.12,"selection_noise_paths":["src/noisy.py"]}',
            '{"task":"seo landing page","selection_token_precision":0.6,"selection_noise_paths":[]}',
        ]),
        encoding="utf-8",
    )
    plan = build_keyword_plan("public preview analysis seo", root=tmp_path)
    assert "preview" in plan.learned_ambiguous_terms
    assert plan.weights["preview"] <= 0.55


def test_keyword_plan_learns_positive_terms_and_phrases(tmp_path):
    metrics_dir = tmp_path / ".agentpack"
    metrics_dir.mkdir()
    (metrics_dir / "metrics.jsonl").write_text(
        "\n".join([
            '{"task":"signup gate preview","selection_token_precision":0.62,"selection_noise_paths":[]}',
            '{"task":"signup gate flow","selection_token_precision":0.58,"selection_noise_paths":[]}',
            '{"task":"preview analysis page","selection_token_precision":0.1,"selection_noise_paths":["src/noisy.py"]}',
        ]),
        encoding="utf-8",
    )
    plan = build_keyword_plan("signup gate preview", root=tmp_path)
    assert "signup" in plan.learned_positive_terms
    assert "signup gate" in plan.learned_positive_phrases
    assert plan.phrase_weights["signup gate"] > plan.phrase_weights["gate preview"]


def test_keyword_plan_workspace_weights_can_exceed_global_for_local_rarity():
    files = [
        _fi("apps/web/src/signup_gate.tsx"),
        _fi("apps/web/src/preview_card.tsx"),
        _fi("apps/api/src/signup_worker.ts"),
        _fi("apps/api/src/preview_job.ts"),
    ]
    plan = build_keyword_plan(
        "signup preview",
        files=files,
        workspace_roots=["apps/web", "apps/api"],
    )
    assert plan.workspace_weights["apps/web"]["signup"] >= plan.weights["signup"]


def test_keyword_plan_persists_term_stats(tmp_path):
    plan = build_keyword_plan("signup gate preview")
    out = persist_keyword_plan_stats(tmp_path, "signup gate preview", plan)
    text = out.read_text(encoding="utf-8")
    assert '"task": "signup gate preview"' in text
    assert '"terms"' in text
    assert '"phrases"' in text


def test_ambiguous_terms_restore_only_with_corroboration():
    weak = _fi("src/analysis.py")
    weak.content = "def helper():\n    return 1\n"
    strong = _fi("src/services/analysis_service.py")
    strong.content = "def analysis():\n    return analysis_result\nanalysis_result = analysis()\n"
    strong.language = "python"

    plan = build_keyword_plan("analysis", files=[weak, strong])
    scored = score_files(
        [weak, strong],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=plan,
    )
    reasons = {fi.path: rs for fi, _score, rs in scored}
    assert any(reason.startswith("ambiguous term cap") for reason in reasons["src/analysis.py"])
    assert any(reason.startswith("ambiguous term restored by corroboration") for reason in reasons["src/services/analysis_service.py"])


def test_generic_terms_do_not_dominate_concrete_file_matches():
    files = [_fi("src/release_notes.py"), _fi("src/auth/session.py")]
    scored = score_files(
        files,
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("fix auth release implementation"),
    )
    scores = {s[0].path: s[1] for s in scored}
    assert scores["src/auth/session.py"] > scores["src/release_notes.py"]


def test_changed_file_gets_high_score():
    files = [_fi("src/auth/session.py")]
    scored = score_files(
        files,
        changed_paths={"src/auth/session.py"},
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords={"auth", "session"},
    )
    assert scored[0][1] >= 100


def test_filename_keyword_match():
    files = [_fi("src/auth/session.py"), _fi("src/billing/invoice.py")]
    scored = score_files(
        files,
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords={"auth", "session"},
    )
    scores = {s[0].path: s[1] for s in scored}
    assert scores["src/auth/session.py"] > scores["src/billing/invoice.py"]


def test_filename_keyword_match_uses_whole_tokens():
    files = [_fi("src/tasks.py"), _fi("src/task_runner.py")]
    scored = score_files(
        files,
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords={"task"},
    )
    scores = {s[0].path: s[1] for s in scored}
    assert scores["src/tasks.py"] == 0
    assert scores["src/task_runner.py"] > 0


def test_content_keyword_match_uses_whole_tokens():
    fi = _fi("src/status.py")
    fi.content = "def status(): pass\n"
    scored = score_files(
        [fi],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords={"stat"},
    )
    assert scored[0][1] == 0


def test_quoted_hyphen_literal_match_beats_generic_symbol_match():
    expected = _fi("packages/vite/src/node/server/openBrowser.ts", language="typescript")
    expected.content = "https://github.com/facebook/create-react-app/blob/main/LICENSE\n"
    generic = _fi("packages/create-vite/template-react/src/App.jsx", language="typescript")
    generic.content = "export function App() { return <main>React app</main> }\n"
    summaries = {
        generic.path: {
            "symbols": [{"name": "App"}],
            "defines": ["App"],
            "entrypoints": ["React component: App"],
            "role": "React UI component",
        },
        expected.path: {
            "symbols": [{"name": "openBrowser"}],
            "defines": ["openBrowser"],
        },
    }

    scored = score_files(
        [generic, expected],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("chore: update `create-react-app` links"),
        summaries=summaries,
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores[expected.path] > scores[generic.path]
    assert "quoted literal match: create react app" in reasons[expected.path]


def test_plural_task_term_matches_singular_content_signal():
    expected = _fi("packages/core/injector/injector.ts", language="typescript")
    expected.content = "const isSnapshotGraphCompilation = options.snapshot\n"

    scored = score_files(
        [expected],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("fix: should skip transient providers for snapshots"),
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores[expected.path] > 0
    assert "content keyword match (1)" in reasons[expected.path]


def test_direct_content_evidence_beats_symbol_only_filename_noise():
    expected = _fi("packages/core/injector/injector.ts", language="typescript")
    expected.content = "providers transient providers snapshot transient providers snapshot"
    noise = _fi("integration/scopes/src/nested-transient/transient.service.ts", language="typescript")

    scored = score_files(
        [noise, expected],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("fix: should skip transient providers for snapshots"),
        summaries={
            expected.path: {"calls": ["providers.set"]},
            noise.path: {
                "defines": ["TransientService"],
                "ranking_keywords": ["transient"],
            },
        },
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores[expected.path] > scores[noise.path]
    assert any(reason.startswith("direct content evidence") for reason in reasons[expected.path])
    assert not any(reason.startswith("direct content evidence") for reason in reasons[noise.path])


def test_concept_keyword_scores_less_than_literal_keyword():
    files = [_fi("src/task.py")]
    literal = score_files(
        files,
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords={"task": 1.0},
    )
    expanded = score_files(
        files,
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("queue"),
    )
    assert 0 < expanded[0][1] < literal[0][1]


def test_score_includes_reasons():
    files = [_fi("src/redis_client.py")]
    scored = score_files(
        files,
        changed_paths={"src/redis_client.py"},
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords={"redis"},
    )
    reasons = scored[0][2]
    assert any("modified" in r or "keyword" in r for r in reasons)


def test_kundali_expands_to_astrology_domain_terms():
    kw = extract_keywords("fix Kundali chart compatibility flow")
    assert "kundali" in kw
    assert "astrology" in kw
    assert "horoscope" in kw or "chart" in kw


def test_implementation_role_boost_keeps_astrology_service_above_summary_floor():
    files = [
        _fi("frontend/app/charts/page.tsx", language="typescript"),
        _fi("backend/src/services/astrology.service.ts", language="typescript"),
        _fi("backend/src/handlers/astrology_handler.py"),
        _fi("backend/src/utils/date_format.py"),
    ]
    scored = score_files(
        files,
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("fix Kundali chart compatibility flow"),
    )
    scores = {s[0].path: s[1] for s in scored}
    reasons = {s[0].path: s[2] for s in scored}

    assert scores["backend/src/services/astrology.service.ts"] >= 60
    assert scores["backend/src/handlers/astrology_handler.py"] >= 60
    assert scores["backend/src/services/astrology.service.ts"] > scores["backend/src/utils/date_format.py"]
    assert "implementation role match" in reasons["backend/src/services/astrology.service.ts"]


def test_cross_layer_boost_connects_page_to_matching_service():
    files = [
        _fi("frontend/app/charts/page.tsx", language="typescript"),
        _fi("backend/src/services/chart.service.ts", language="typescript"),
        _fi("backend/src/services/payment.service.ts", language="typescript"),
    ]
    scored = score_files(
        files,
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("fix chart rendering"),
    )
    boosted = boost_cross_layer_related(scored, extract_keyword_weights("fix chart rendering"))
    scores = {s[0].path: s[1] for s in boosted}
    reasons = {s[0].path: s[2] for s in boosted}

    assert scores["backend/src/services/chart.service.ts"] > scores["backend/src/services/payment.service.ts"]
    assert "cross-layer related implementation" in reasons["backend/src/services/chart.service.ts"]


def test_strong_public_name_gets_bonus():
    fi = _fi("src/auth/otp.py")
    summaries = {
        fi.path: {
            "symbols": [],
            "naming_signals": ["strong public name: verify_otp"],
            "naming_keywords": ["verify", "otp"],
        }
    }
    scored = score_files(
        [fi],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("fix otp verify issue"),
        summaries=summaries,
    )
    assert any("matched naming keyword:" in reason for reason in scored[0][2])


def test_generic_public_name_gets_small_penalty_when_otherwise_weak():
    good = _fi("src/auth/otp.py")
    weak = _fi("src/auth/handler.py")
    summaries = {
        good.path: {
            "symbols": [],
            "naming_signals": ["strong public name: verify_otp"],
            "naming_keywords": ["verify", "otp"],
        },
        weak.path: {
            "symbols": [],
            "naming_signals": ["generic public name: handle"],
            "naming_keywords": [],
        },
    }
    scored = score_files(
        [good, weak],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("fix otp verify issue"),
        summaries=summaries,
    )
    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores["src/auth/otp.py"] > scores["src/auth/handler.py"]
    assert any(reason == "generic public API penalty: handle" for reason in reasons["src/auth/handler.py"])


def test_multi_token_define_beats_single_token_define():
    exact = _fi("src/node/index.ts", language="typescript")
    partial = _fi("src/node/plugins/worker.ts", language="typescript")
    summaries = {
        exact.path: {
            "symbols": [{"name": "parseAst"}],
            "defines": ["parseAst"],
        },
        partial.path: {
            "symbols": [{"name": "extractWorkerTypeFromAst"}],
            "defines": ["extractWorkerTypeFromAst"],
        },
    }

    scored = score_files(
        [partial, exact],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("correct parse ast deprecation hint"),
        summaries=summaries,
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores["src/node/index.ts"] > scores["src/node/plugins/worker.ts"]
    assert any(reason.startswith("multi-token defines match") for reason in reasons["src/node/index.ts"])


def test_release_task_boosts_version_metadata_files():
    pyproject = _fi("pyproject.toml", language=None)
    init_file = _fi("src/pkg/__init__.py")
    random_file = _fi("src/pkg/runtime.py")

    scored = score_files(
        [random_file, pyproject, init_file],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("start version 2.2.0"),
        summaries={},
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores["pyproject.toml"] > scores["src/pkg/runtime.py"]
    assert scores["src/pkg/__init__.py"] > scores["src/pkg/runtime.py"]
    assert "release/version metadata" in reasons["pyproject.toml"]
    assert "release/version metadata" in reasons["src/pkg/__init__.py"]


def test_metadata_task_boosts_project_metadata_not_license_doc():
    pyproject = _fi("pyproject.toml", language=None)
    license_doc = _fi("docs/license.rst", language=None)

    scored = score_files(
        [license_doc, pyproject],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("fix license metadata"),
        summaries={},
    )

    reasons = {item[0].path: item[2] for item in scored}
    assert "release/version metadata" in reasons["pyproject.toml"]
    assert "release/version metadata" not in reasons["docs/license.rst"]


def test_release_task_does_not_boost_test_or_example_metadata_files():
    root_pyproject = _fi("pyproject.toml", language=None)
    example_pyproject = _fi("examples/demo/pyproject.toml", language=None)
    test_init = _fi("tests/test_pkg/__init__.py")

    scored = score_files(
        [root_pyproject, example_pyproject, test_init],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("start version 2.2.0"),
        summaries={},
    )

    reasons = {item[0].path: item[2] for item in scored}
    assert "release/version metadata" in reasons["pyproject.toml"]
    assert "release/version metadata" not in reasons["examples/demo/pyproject.toml"]
    assert "release/version metadata" not in reasons["tests/test_pkg/__init__.py"]


def test_build_task_boosts_root_build_metadata_not_source_noise():
    pom = _fi("pom.xml", language="xml")
    source = _fi("src/main/java/org/example/OwnerRepository.java", language="java")

    scored = score_files(
        [source, pom],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("Support building with Java 17"),
        summaries={},
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores["pom.xml"] > scores[source.path]
    assert "build/dependency metadata" in reasons["pom.xml"]
    assert "build/dependency metadata" not in reasons[source.path]


def test_build_metadata_boost_ignores_nested_metadata_files():
    root_pom = _fi("pom.xml", language="xml")
    nested_pom = _fi("examples/demo/pom.xml", language="xml")
    pyproject = _fi("pyproject.toml", language=None)

    scored = score_files(
        [nested_pom, root_pom, pyproject],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("Use more specific test dependencies"),
        summaries={},
    )

    reasons = {item[0].path: item[2] for item in scored}
    assert "build/dependency metadata" in reasons[root_pom.path]
    assert "build/dependency metadata" not in reasons[nested_pom.path]
    assert "build/dependency metadata" not in reasons[pyproject.path]


def test_release_task_prefers_primary_metadata_over_secondary_metadata():
    init_file = _fi("src/pkg/__init__.py")
    setup_cfg = _fi("setup.cfg", language=None)

    scored = score_files(
        [setup_cfg, init_file],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("prerelease version 2.0.0rc1"),
        summaries={},
    )

    scores = {item[0].path: item[1] for item in scored}
    assert scores["src/pkg/__init__.py"] > scores["setup.cfg"]


def test_release_task_dampens_non_metadata_version_only_matches():
    version_noise = _fi("src/pkg/testing.py")
    version_noise.content = "def helper():\n    return 'version check'\n"
    pyproject = _fi("pyproject.toml", language=None)

    scored = score_files(
        [version_noise, pyproject],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("start version 8.5.0"),
        summaries={},
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores["pyproject.toml"] > scores["src/pkg/testing.py"]
    assert "release-term-only non-metadata dampening" in reasons["src/pkg/testing.py"]


def test_typescript_config_file_gets_config_signal():
    config = _fi("playground/tailwind/tailwind.config.ts", language="typescript")
    spec = _fi("playground/tailwind/__test__/tailwind.spec.ts", language="typescript")

    scored = score_files(
        [spec, config],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("chore: fix tailwind playground comments"),
        summaries={},
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores["playground/tailwind/tailwind.config.ts"] > scores["playground/tailwind/__test__/tailwind.spec.ts"]
    assert "config file" in reasons["playground/tailwind/tailwind.config.ts"]


def test_literal_definition_beats_literal_call_site_noise():
    exported = _fi("packages/vite/src/node/index.ts", language="typescript")
    call_site = _fi("packages/vite/src/node/plugins/importMetaGlob.ts", language="typescript")

    scored = score_files(
        [call_site, exported],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("chore: correct `parseAst`/`parseAstAsync` deprecation hints"),
        summaries={
            exported.path: {"defines": ["parseAst", "parseAstAsync"]},
            call_site.path: {"calls": ["parseAstAsync"]},
        },
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores[exported.path] > scores[call_site.path]
    assert any(reason.startswith("literal definition match:") for reason in reasons[exported.path])


def test_multi_term_path_match_boosts_specific_playground_config():
    expected_config = _fi("playground/tailwind/tailwind.config.ts", language="typescript")
    generic_config = _fi("playground/hmr-full-bundle-mode/vite.config.ts", language="typescript")
    related_test = _fi("playground/tailwind/__test__/tailwind.spec.ts", language="typescript")

    scored = score_files(
        [generic_config, expected_config, related_test],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("chore: fix tailwind playground comments"),
        summaries={
            generic_config.path: {
                "defines": ["delayTransformComment"],
                "ranking_keywords": ["comment"],
            },
            expected_config.path: {
                "naming_keywords": ["tailwind"],
            },
        },
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores[expected_config.path] > scores[generic_config.path]
    assert scores[expected_config.path] > scores[related_test.path]
    assert any(reason.startswith("multi-term path match") for reason in reasons[expected_config.path])
    assert not any(reason.startswith("multi-term path match") for reason in reasons[related_test.path])


def test_paired_test_boost_does_not_use_config_files_as_source_seed():
    config = _fi("packages/vite/src/node/__tests__/package.json", language="json")
    test = _fi("packages/vite/src/node/__tests__/packages/package.spec.ts", language="typescript")
    source = _fi("packages/vite/src/node/plugins/css.ts", language="typescript")

    boosted = boost_paired_tests([
        (config, 300.0, ["config file"]),
        (test, 100.0, ["filename keyword match"]),
        (source, 280.0, ["filename keyword match"]),
    ])

    reasons = {fi.path: item_reasons for fi, _score, item_reasons in boosted}
    assert not any("test for high-scoring" in reason for reason in reasons[test.path])


def test_explicit_test_task_prefers_spec_over_source_file():
    spec = _fi("integration/scopes/e2e/transient-scope.spec.ts", language="typescript")
    source = _fi("integration/scopes/src/nested-transient/nested-transient.service.ts", language="typescript")
    summaries = {
        spec.path: {
            "role": "transient scope.spec classes",
            "defines": ["DeepNestedTransient"],
            "symbols": [{"name": "DeepNestedTransient"}],
        },
        source.path: {
            "role": "nested transient.service classes",
            "defines": ["NestedTransientService"],
            "symbols": [{"name": "NestedTransientService"}],
        },
    }

    scored = score_files(
        [source, spec],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("test(core): add deeply nested transient providers in scoped chains"),
        summaries=summaries,
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores[spec.path] > scores[source.path]
    assert "explicit test task file" in reasons[spec.path]
    assert "explicit test task non-test dampening" in reasons[source.path]


def test_large_file_with_task_support_is_not_marked_large_unrelated():
    large = _fi("packages/vite/src/node/server/index.ts", tokens=5000, language="typescript")
    large.too_large = True
    large.content = "const fs = true\nconst restrict = true\n"

    scored = score_files(
        [large],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("fix correct fs restrictions"),
        summaries={large.path: {"calls": ["ssrFixStacktrace"]}},
    )

    score, reasons = scored[0][1], scored[0][2]
    assert score > 0
    assert "large supported file" in reasons
    assert "large unrelated file" not in reasons


def test_api_route_owner_beats_broad_domain_engine_file():
    route = _fi("dashboard/src/app/api/signals/stats/route.ts", language="typescript")
    broad = _fi("dashboard/src/lib/signals/signal-generator.ts", language="typescript")

    scored = score_files(
        [broad, route],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("fix dashboard signals stats win rate metric"),
        summaries={
            route.path: {
                "entrypoints": ["Next API route: dashboard/src/app/api/signals/stats/route.ts"],
                "ranking_keywords": ["signals", "stats", "win_rate"],
            },
            broad.path: {
                "role": "signal generator engine",
                "domain": "signals",
                "ranking_keywords": ["signals"],
            },
        },
    )

    scores = {item[0].path: item[1] for item in scored}
    reasons = {item[0].path: item[2] for item in scored}
    assert scores[route.path] > scores[broad.path]
    assert any(reason.startswith("API route owner match:") for reason in reasons[route.path])
    assert "no direct tests found for endpoint" in reasons[route.path]


def test_api_endpoint_pair_boosts_same_family_history_route():
    stats = _fi("dashboard/src/app/api/signals/stats/route.ts", language="typescript")
    history = _fi("dashboard/src/app/api/signals/history/route.ts", language="typescript")
    unrelated = _fi("dashboard/src/app/api/runs/history/route.ts", language="typescript")
    keywords = build_keyword_plan("fix signals stats win rate metric")

    scored = score_files(
        [history, unrelated, stats],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=keywords,
        summaries={
            stats.path: {"entrypoints": ["Next API route: app/api/signals/stats/route.ts"]},
            history.path: {"entrypoints": ["Next API route: app/api/signals/history/route.ts"]},
            unrelated.path: {"entrypoints": ["Next API route: app/api/runs/history/route.ts"]},
        },
    )
    boosted = boost_api_endpoint_pairs(scored, keywords)

    scores = {item[0].path: item[1] for item in boosted}
    reasons = {item[0].path: item[2] for item in boosted}
    assert scores[history.path] > scores[unrelated.path]
    assert any(reason.startswith("API endpoint pair with") for reason in reasons[history.path])
    assert not any(reason.startswith("API endpoint pair with") for reason in reasons[unrelated.path])


def test_frontend_api_consumer_boosts_owning_endpoint_over_broad_domain_file():
    client = _fi("dashboard/src/app/signals/signals-client.tsx", language="typescript")
    stats = _fi("dashboard/src/app/api/signals/stats/route.ts", language="typescript")
    broad = _fi("dashboard/src/lib/signals/signal-generator.ts", language="typescript")
    keywords = build_keyword_plan("fix dashboard win rate metric")

    scored = score_files(
        [broad, stats, client],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=keywords,
        summaries={
            client.path: {
                "entrypoints": ["React component: SignalsClient"],
                "calls": ["API call: /api/signals/stats"],
                "ranking_keywords": ["win_rate"],
            },
            stats.path: {
                "entrypoints": ["GET /api/signals/stats"],
            },
            broad.path: {
                "role": "signal generator engine",
                "domain": "signals",
                "ranking_keywords": ["signals"],
            },
        },
    )
    boosted = boost_frontend_api_consumers(scored, {
        client.path: {
            "entrypoints": ["React component: SignalsClient"],
            "calls": ["API call: /api/signals/stats"],
            "ranking_keywords": ["win_rate"],
        },
        stats.path: {
            "entrypoints": ["GET /api/signals/stats"],
        },
        broad.path: {
            "role": "signal generator engine",
            "domain": "signals",
            "ranking_keywords": ["signals"],
        },
    }, keywords)

    scores = {item[0].path: item[1] for item in boosted}
    reasons = {item[0].path: item[2] for item in boosted}
    assert scores[stats.path] > scores[broad.path]
    assert any(reason.startswith("API producer for frontend call /api/signals/stats") for reason in reasons[stats.path])


def test_keyword_only_broad_domain_match_is_labeled_likely_false_positive():
    broad = _fi("dashboard/src/lib/signals/factor-history.ts", language="typescript")

    scored = score_files(
        [broad],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("fix 12 factor signals history stats"),
        summaries={
            broad.path: {
                "role": "signal factor engine",
                "domain": "signals factor history",
                "ranking_keywords": ["factor", "history"],
            },
        },
    )

    reasons = scored[0][2]
    assert "likely false positive: keyword-only match" in reasons


def test_changed_noise_file_is_labeled_workspace_context_only():
    gitignore = _fi(".gitignore", language=None)

    scored = score_files(
        [gitignore],
        changed_paths={gitignore.path},
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("fix signals stats win rate metric"),
        summaries={},
    )

    assert "modified workspace context only" in scored[0][2]


def test_broad_root_go_symbol_carrier_is_dampened():
    hub = _fi("gin.go", language="go")
    hub.content = "func updateRouteTree() {}\nfunc unrelated() {}\n"

    scored = score_files(
        [hub],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("ci update go version support"),
        summaries={
            hub.path: {
                "symbols": [{"name": "updateRouteTree"}],
                "defines": ["updateRouteTree"],
                "ranking_keywords": ["version"],
            },
        },
    )

    assert "broad Go symbol carrier dampening" in scored[0][2]
    assert scored[0][1] < 120


def test_go_action_owner_path_signal_is_not_dampened():
    owner = _fi("binding/bson.go", language="go")
    owner.content = "func EncodeBSON() {}\n"

    scored = score_files(
        [owner],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("upgrade bson mongo driver"),
        summaries={
            owner.path: {
                "symbols": [{"name": "EncodeBSON"}],
                "defines": ["EncodeBSON"],
                "external_systems": ["MongoDB/Mongoose"],
            },
        },
    )

    assert "broad Go symbol carrier dampening" not in scored[0][2]
    assert scored[0][1] >= 120


def test_generated_agent_artifacts_are_quiet_without_direct_need():
    artifact = _fi(".agentpack/context.md", language="markdown")
    source = _fi("src/auth/session.py")
    source.content = "def auth_session():\n    return True\n"

    scored = score_files(
        [artifact, source],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("fix auth session context"),
        summaries={
            artifact.path: {"ranking_keywords": ["context", "auth", "session"]},
            source.path: {"defines": ["auth_session"], "ranking_keywords": ["auth", "session"]},
        },
    )

    scores = {fi.path: score for fi, score, _reasons in scored}
    reasons = {fi.path: reasons for fi, _score, reasons in scored}
    assert scores[source.path] > scores[artifact.path]
    assert "generated agent artifact dampening" in reasons[artifact.path]


def test_android_build_artifacts_are_quiet_without_direct_need():
    artifact = _fi("android/app/.cxx/Debug/generated.cpp", language="cpp")
    source = _fi("android/app/src/main.cpp", language="cpp")
    source.content = "int main() { return 0; }\n"

    scored = score_files(
        [artifact, source],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=build_keyword_plan("fix Android build pipeline"),
        summaries={
            artifact.path: {"ranking_keywords": ["android", "build", "pipeline"]},
            source.path: {"defines": ["main"], "ranking_keywords": ["android", "build", "pipeline"]},
        },
    )

    scores = {file_info.path: score for file_info, score, _reasons in scored}
    reasons = {file_info.path: reasons for file_info, _score, reasons in scored}
    assert scores[source.path] > scores[artifact.path]
    assert "generated Android build artifact dampening" in reasons[artifact.path]
