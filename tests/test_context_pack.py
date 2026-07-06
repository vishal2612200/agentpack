from pathlib import Path
import subprocess
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from agentpack.application.pack_service import _fit_rendered_budget, _settle_rendered_token_estimate, _sf_tokens, _summary_cap_for_mode, _summary_score_floor
from agentpack.core.config import DEFAULT_CONFIG
from agentpack.core.citations import (
    build_citation_manifest,
    extract_location_citations,
    file_citation,
    parse_location,
    validate_citations,
    validate_claim_support,
    write_citation_manifest,
)
from agentpack.core.context_pack import (
    compact_selected_file_payloads,
    _find_marginal_replacement,
    _selection_priority,
    _skeleton_content,
    enrich_call_site_scores,
    save_pack_metadata,
    select_files,
)
from agentpack.core.models import Citation, ContextPack, FileInfo, OmittedRelevantFile, Receipt, SelectedFile
from agentpack.core.pack_handoff import build_pack_handoff
from agentpack.core.scanner import file_hash
from agentpack.core.token_estimator import estimate_tokens
from agentpack.renderers.markdown import render_claude, render_generic


def _fi(path: str, tokens: int = 100, hash_val: str = "h1") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=Path("/nonexistent") / path,
        size_bytes=tokens * 4,
        estimated_tokens=tokens,
        hash=hash_val,
        language="python",
    )


def test_compact_selected_file_payloads_shrinks_symbol_carrier_without_changing_path(tmp_path):
    source = tmp_path / "tests" / "test_basic.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def test_default_true_sentinel():",
            "    assert True",
            "",
            *[f"def unrelated_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="tests/test_basic.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="tests/test_basic.py",
            language="python",
            score=300.0,
            include_mode="skeleton",
            reasons=["symbol keyword match", "matched define: test_default_true_sentinel"],
            content=source.read_text(encoding="utf-8"),
            summary="test helpers",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[(fi, 300.0, ["symbol keyword match", "matched define: test_default_true_sentinel"])],
        task='Use `default=True` as a sentinel for non-boolean flags',
        changed_paths=set(),
    )

    assert [sf.path for sf in compacted] == ["tests/test_basic.py"]
    assert _sf_tokens(compacted[0]) < _sf_tokens(selected[0])
    assert "test_default_true_sentinel" in (compacted[0].content or "")
    assert "source-aware compacted" in " ".join(compacted[0].reasons)


def test_compact_selected_file_payloads_protects_direct_owner_evidence(tmp_path):
    source = tmp_path / "tests" / "test_commands.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def test_no_such_command_suggestion():",
            "    assert True",
            "",
            *[f"def unrelated_command_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="tests/test_commands.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="tests/test_commands.py",
            language="python",
            score=350.0,
            include_mode="skeleton",
            reasons=[
                "direct content evidence +170",
                "matched entrypoint: CLI command: no-such-command",
                "matched define: test_no_such_command_suggestion",
            ],
            content=source.read_text(encoding="utf-8"),
            summary="command tests",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[
            (
                fi,
                350.0,
                [
                    "direct content evidence +170",
                    "matched entrypoint: CLI command: no-such-command",
                    "matched define: test_no_such_command_suggestion",
                ],
            )
        ],
        task="Add NoSuchCommand exception with suggestions for misspelled commands",
        changed_paths=set(),
    )

    assert compacted[0] == selected[0]


def test_compact_selected_file_payloads_protects_memory_confirmed_owner(tmp_path):
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def target_handler(request):",
            "    return request.user",
            "",
            *[f"def unrelated_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="src/service.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="src/service.py",
            language="python",
            score=210.0,
            include_mode="skeleton",
            reasons=[
                "matched define: target_handler",
                "episodic memory similar task; overlap=target; source hash still current; confidence=0.70 boost +12",
                "content keyword match (1)",
            ],
            content=source.read_text(encoding="utf-8"),
            summary="target handler",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={
            "src/service.py": {
                "defines": ["target_handler"],
                "symbols": [{
                    "name": "target_handler",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                    "signature": "def target_handler(request)",
                    "summary": "Handle target requests",
                    "body": "def target_handler(request): return request.user",
                }],
            }
        },
        scored=[(fi, 210.0, selected[0].reasons)],
        task="fix target handler",
        changed_paths=set(),
    )

    assert compacted[0] == selected[0]


def test_compact_selected_file_payloads_shrinks_low_rank_config_summary(tmp_path):
    source = tmp_path / "docker-compose.yml"
    source.write_text(
        "\n".join([
            "services:",
            "  db:",
            "    image: postgres:16",
            "  mysql:",
            "    image: mysql:8",
            *[f"  unused_{index}: value" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="docker-compose.yml",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=500,
        hash=file_hash(source),
        language="yaml",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="docker-compose.yml",
            language="yaml",
            score=140.0,
            include_mode="summary",
            reasons=["matched external system: PostgreSQL/Django ORM", "matched ranking keyword: postgre", "config file"],
            summary="\n".join(f"summary line {index}" for index in range(120)),
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[
            *[(_fi(f"src/owner_{index}.py"), 300.0 - index, ["direct content evidence +170"]) for index in range(7)],
            (fi, 140.0, selected[0].reasons),
        ],
        task="Register native resource hints for nested db/{h2,mysql,postgres}/ scripts",
        changed_paths=set(),
    )

    assert compacted[0].path == selected[0].path
    assert compacted[0].include_mode == "summary"
    assert _sf_tokens(compacted[0]) < _sf_tokens(selected[0])
    assert "postgres" in (compacted[0].summary or "")
    assert "source-aware compacted ranked_config_summary_carrier" in compacted[0].reasons


def test_compact_selected_file_payloads_protects_direct_config_summary(tmp_path):
    source = tmp_path / "docker-compose.yml"
    source.write_text("services:\n  db:\n    image: postgres:16\n", encoding="utf-8")
    fi = FileInfo(
        path="docker-compose.yml",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=120,
        hash=file_hash(source),
        language="yaml",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="docker-compose.yml",
            language="yaml",
            score=240.0,
            include_mode="summary",
            reasons=["direct content evidence +170", "config file"],
            summary="postgres service config",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[(fi, 240.0, selected[0].reasons)],
        task="Update PostgreSQL docker compose service",
        changed_paths=set(),
    )

    assert compacted[0] == selected[0]


def test_compact_selected_file_payloads_shrinks_ranked_test_support_skeleton(tmp_path):
    source = tmp_path / "tests" / "test_render.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def test_render_writer_support():",
            "    assert True",
            "",
            *[f"def unrelated_render_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="tests/test_render.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="tests/test_render.py",
            language="python",
            score=250.0,
            include_mode="skeleton",
            reasons=["filename keyword match", "symbol keyword match", "matched ranking keyword: writer"],
            content=source.read_text(encoding="utf-8"),
            summary="render test support",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[
            (_fi("tests/test_response_writer.py"), 400.0, ["keyword phrase match: response writer"]),
            (fi, 250.0, selected[0].reasons),
        ],
        task="test(response_writer): add tests for Flush with http Flusher",
        changed_paths=set(),
    )

    assert compacted[0].path == selected[0].path
    assert _sf_tokens(compacted[0]) < _sf_tokens(selected[0])
    assert "source-aware compacted ranked_test_action_mismatch_carrier" in compacted[0].reasons


def test_compact_selected_file_payloads_protects_path_aligned_ranked_test_owner(tmp_path):
    source = tmp_path / "tests" / "test_response_writer.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def test_response_writer_flush():",
            "    assert True",
            "",
            *[f"def unrelated_writer_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="tests/test_response_writer.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="tests/test_response_writer.py",
            language="python",
            score=400.0,
            include_mode="skeleton",
            reasons=["filename keyword match", "symbol keyword match", "matched ranking keyword: writer"],
            content=source.read_text(encoding="utf-8"),
            summary="response writer tests",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[(fi, 400.0, selected[0].reasons)],
        task="test(response_writer): add tests for Flush with http Flusher",
        changed_paths=set(),
    )

    assert compacted[0] == selected[0]


def test_compact_selected_file_payloads_protects_python_ranked_source_support_skeleton(tmp_path):
    source = tmp_path / "src" / "types.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def convert_value(value):",
            "    return value",
            "",
            *[f"def unrelated_type_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="src/types.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="src/types.py",
            language="python",
            score=220.0,
            include_mode="skeleton",
            reasons=["symbol keyword match", "matched ranking keyword: value", "matched define: convert_value"],
            content=source.read_text(encoding="utf-8"),
            summary="value conversion support",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[
            (_fi("src/core.py"), 400.0, ["quoted literal match: flag value", "direct content evidence +170"]),
            (_fi("src/parser.py"), 300.0, ["symbol keyword match"]),
            (fi, 220.0, selected[0].reasons),
        ],
        task="Only try to set flag_value if is_flag is true",
        changed_paths=set(),
    )

    assert compacted[0] == selected[0]


def test_compact_selected_file_payloads_shrinks_non_python_ranked_source_support_skeleton(tmp_path):
    source = tmp_path / "recovery" / "recovery.go"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "package binding",
            "",
            "func setIntField(value int) int {",
            "    return value",
            "}",
            "",
            *[f"func unrelatedField{index}() int {{ return {index} }}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="recovery/recovery.go",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="go",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="recovery/recovery.go",
            language="go",
            score=220.0,
            include_mode="skeleton",
            reasons=["symbol keyword match", "matched ranking keyword: field", "matched define: setIntField"],
            content=source.read_text(encoding="utf-8"),
            summary="field mapping support",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[
            (_fi("context.go"), 420.0, ["matched define: Context.Copy", "keyword phrase match: copy errors"]),
            (_fi("context_test.go"), 320.0, ["explicit test task file"]),
            (_fi("errors.go"), 260.0, ["symbol keyword match"]),
            (fi, 220.0, selected[0].reasons),
        ],
        task="fix(context): Copy copies Errors and Accepted fields",
        changed_paths=set(),
    )

    assert compacted[0].path == selected[0].path
    assert _sf_tokens(compacted[0]) < _sf_tokens(selected[0])
    assert "source-aware compacted ranked_source_support_skeleton_carrier" in compacted[0].reasons


def test_compact_selected_file_payloads_shrinks_low_anchor_source_carrier(tmp_path):
    source = tmp_path / "src" / "support.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def helper_line(value):",
            "    return str(value)",
            "",
            *[f"def unrelated_support_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="src/support.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="src/support.py",
            language="python",
            score=210.0,
            include_mode="skeleton",
            reasons=["symbol keyword match", "content keyword match (3)"],
            content=source.read_text(encoding="utf-8"),
            summary="broad support carrier",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[
            (_fi("src/owner.py"), 500.0, ["keyword phrase match: flag value", "matched define: set_flag_value"]),
            (_fi("tests/test_owner.py"), 300.0, ["explicit test task file"]),
            (_fi("src/other.py"), 260.0, ["matched define: other"]),
            (fi, 210.0, selected[0].reasons),
        ],
        task="Only try to set flag_value if is_flag is true",
        changed_paths=set(),
    )

    assert compacted[0].path == selected[0].path
    assert _sf_tokens(compacted[0]) < _sf_tokens(selected[0])
    assert "source-aware compacted ranked_source_low_anchor_carrier" in compacted[0].reasons


def test_compact_selected_file_payloads_protects_low_anchor_source_with_definition(tmp_path):
    source = tmp_path / "src" / "support.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def flag_value(value):",
            "    return value",
            "",
            *[f"def unrelated_support_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="src/support.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="src/support.py",
            language="python",
            score=210.0,
            include_mode="skeleton",
            reasons=["symbol keyword match", "matched define: flag_value", "content keyword match (3)"],
            content=source.read_text(encoding="utf-8"),
            summary="definition carrier",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[
            (_fi("src/owner.py"), 500.0, ["keyword phrase match: flag value", "matched define: set_flag_value"]),
            (_fi("tests/test_owner.py"), 300.0, ["explicit test task file"]),
            (_fi("src/other.py"), 260.0, ["matched define: other"]),
            (fi, 210.0, selected[0].reasons),
        ],
        task="Only try to set flag_value if is_flag is true",
        changed_paths=set(),
    )

    assert compacted[0] == selected[0]


def test_compact_selected_file_payloads_shrinks_late_ranked_source_carrier(tmp_path):
    source = tmp_path / "src" / "late_support.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "def related_helper(value):",
            "    return value",
            "",
            *[f"def unrelated_late_{index}(): return {index}" for index in range(120)],
        ]),
        encoding="utf-8",
    )
    fi = FileInfo(
        path="src/late_support.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=700,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected = [
        SelectedFile(
            path="src/late_support.py",
            language="python",
            score=180.0,
            include_mode="skeleton",
            reasons=["symbol keyword match", "matched define: related_helper", "content keyword match (3)"],
            content=source.read_text(encoding="utf-8"),
            summary="late support carrier",
            symbols=[],
            source_hash=fi.hash,
        )
    ]

    compacted = compact_selected_file_payloads(
        selected,
        files=[fi],
        summaries={},
        scored=[
            (_fi("src/owner.py"), 500.0, ["keyword phrase match: flag value", "matched define: set_flag_value"]),
            (_fi("tests/test_owner.py"), 300.0, ["explicit test task file"]),
            (_fi("src/first.py"), 260.0, ["matched define: first"]),
            (_fi("src/second.py"), 240.0, ["matched define: second"]),
            (_fi("src/third.py"), 220.0, ["matched define: third"]),
            (fi, 180.0, selected[0].reasons),
        ],
        task="Only try to set flag_value if is_flag is true",
        changed_paths=set(),
    )

    assert compacted[0].path == selected[0].path
    assert _sf_tokens(compacted[0]) < _sf_tokens(selected[0])
    assert "source-aware compacted ranked_source_late_carrier" in compacted[0].reasons


def test_selects_changed_file_as_full(tmp_path):
    f = tmp_path / "session.py"
    f.write_text("def login(): pass\n")
    fi = FileInfo(
        path="session.py",
        abs_path=f,
        size_bytes=20,
        estimated_tokens=5,
        hash="h1",
        language="python",
    )
    scored = [(fi, 100.0, ["modified"])]
    selected, _receipts = select_files(
        files=[fi],
        scored=scored,
        changed_paths={"session.py"},
        summaries={},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
    )
    assert len(selected) == 1
    assert selected[0].include_mode == "full"
    assert selected[0].content is not None
    assert selected[0].source_hash == "h1"
    assert selected[0].citations[0].path == "session.py"
    assert selected[0].citations[0].start_line == 1


def test_large_dirty_file_uses_diff_mode(tmp_path):
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    f = tmp_path / "large.py"
    f.write_text("def old():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "large.py"], cwd=tmp_path, check=True)
    f.write_text("def old():\n    return 2\n" + "\n".join(f"# filler {i}" for i in range(2000)), encoding="utf-8")
    fi = FileInfo(
        path="large.py",
        abs_path=f,
        size_bytes=f.stat().st_size,
        estimated_tokens=5000,
        hash="h2",
        language="python",
    )

    selected, _ = select_files(
        files=[fi],
        scored=[(fi, 100.0, ["modified"])],
        changed_paths={"large.py"},
        summaries={},
        mode="balanced",
        budget=3000,
        max_file_tokens=4000,
    )

    assert selected[0].include_mode == "diff"
    assert "diff --git" in selected[0].content
    assert "# filler 1999" not in selected[0].content


def test_diff_mode_citations_point_to_changed_new_lines(tmp_path):
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    f = tmp_path / "large.py"
    f.write_text("def old():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "large.py"], cwd=tmp_path, check=True)
    f.write_text("def old():\n    return 2\n", encoding="utf-8")
    fi = FileInfo(
        path="large.py",
        abs_path=f,
        size_bytes=f.stat().st_size,
        estimated_tokens=5000,
        hash=file_hash(f),
        language="python",
    )

    selected, _ = select_files(
        files=[fi],
        scored=[(fi, 100.0, ["modified"])],
        changed_paths={"large.py"},
        summaries={},
        mode="balanced",
        budget=3000,
        max_file_tokens=4000,
    )

    assert selected[0].include_mode == "diff"
    assert any(citation.start_line == 2 and citation.end_line == 2 for citation in selected[0].citations)


def test_validate_citations_rejects_source_hash_mismatch(tmp_path):
    source = tmp_path / "src.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    citation = Citation(
        path="src.py",
        start_line=1,
        end_line=1,
        source_hash=file_hash(source),
        kind="code",
    )

    assert validate_citations(tmp_path, [citation]).invalid == []

    source.write_text("def run():\n    return 2\n", encoding="utf-8")
    validation = validate_citations(tmp_path, [citation])

    assert validation.invalid == ["src.py:1: source hash mismatch"]


def test_validate_citations_rejects_ranges_outside_file(tmp_path):
    source = tmp_path / "src.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    citation = Citation(path="src.py", start_line=1, end_line=99, kind="code")

    validation = validate_citations(tmp_path, [citation])

    assert validation.invalid == ["src.py:1-99: range outside file"]


def test_validate_citations_requires_support_text_inside_cited_span(tmp_path):
    source = tmp_path / "src.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    valid = Citation(path="src.py", start_line=1, end_line=2, kind="code", support_text="return 1")
    invalid = Citation(path="src.py", start_line=1, end_line=1, kind="code", support_text="return 1")

    validation = validate_citations(tmp_path, [valid, invalid])

    assert validation.valid == [valid]
    assert validation.invalid == ["src.py:1: support text not found"]


def test_location_citation_parser_does_not_absorb_prose_before_path():
    citations = extract_location_citations(
        "no guard against existing entry. Compare: _set_temp_ip_abuse_block at ajeei/otp/utils.py:152"
    )

    assert len(citations) == 1
    assert citations[0].path == "ajeei/otp/utils.py"
    assert citations[0].start_line == 152


def test_location_citation_parser_supports_angle_wrapped_spaced_paths():
    citation = parse_location("see <src/path with spaces.py>:12-14")

    assert citation is not None
    assert citation.path == "src/path with spaces.py"
    assert citation.start_line == 12
    assert citation.end_line == 14


def test_validate_citations_requires_external_provenance() -> None:
    missing_url = Citation(path="", kind="external", retrieved_at="2026-06-28T00:00:00Z")
    missing_provenance = Citation(path="", kind="external", url="https://example.com/source")
    with_provenance = Citation(
        path="",
        kind="external",
        url="https://example.com/source",
        retrieved_at="2026-06-28T00:00:00Z",
    )

    validation = validate_citations(Path("."), [missing_url, missing_provenance, with_provenance])

    assert validation.valid == [with_provenance]
    assert validation.invalid == [
        "external: external citation missing http(s) url",
        "https://example.com/source: external citation missing retrieval provenance",
    ]


def test_validate_citations_verifies_external_support_hash() -> None:
    support_text = "External docs state the limit is 10 requests per minute."
    good = Citation(
        path="",
        kind="external",
        url="https://example.com/source",
        retrieved_at="2026-06-28T00:00:00Z",
        support_text=support_text,
        content_hash="sha256:a6934dde65f2d9c4568acca8d1d33c0c58953869a0065c5c08e3007dd64af8c5",
    )
    bad = good.model_copy(update={"content_hash": "sha256:bad"})

    validation = validate_citations(Path("."), [good, bad])

    assert validation.valid == [good]
    assert validation.invalid == ["https://example.com/source: external support text hash mismatch"]


def test_validate_citations_can_verify_external_support_text_live() -> None:
    body = "External docs state the limit is 10 requests per minute."

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/source"
        good = Citation(
            path="",
            kind="external",
            url=url,
            retrieved_at="2026-06-28T00:00:00Z",
            support_text="limit is 10 requests",
            content_hash="sha256:255630b268657fa210578524bb08c8a1f602dd1a2eef551f031a301dc61a210c",
        )
        bad = good.model_copy(update={
            "support_text": "limit is 20 requests",
            "content_hash": "sha256:4fd7745c16735d4a50e83495685f5ec9e3f5b2979aa577b98a4412db44290059",
        })

        validation = validate_citations(
            Path("."),
            [good, bad],
            verify_external_content=True,
            external_timeout_s=2,
        )

        assert validation.valid == [good]
        assert validation.invalid == [f"{url}: external support text not found at url"]
    finally:
        server.shutdown()
        server.server_close()


def test_file_citation_redacts_support_text(tmp_path):
    secret = "sk-" + "a" * 48
    source = tmp_path / "settings.py"
    source.write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    fi = FileInfo(
        path="settings.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=10,
        hash=file_hash(source),
        language="python",
        content=source.read_text(encoding="utf-8"),
    )

    citation = file_citation(fi)

    assert secret not in citation.support_text
    assert "OPENAI_API_KEY=" in citation.support_text


def test_validate_claim_support_accepts_semantic_judge_callback(tmp_path):
    source = tmp_path / "src.py"
    source.write_text("def limit():\n    return 10\n", encoding="utf-8")
    citation = Citation(path="src.py", start_line=2, end_line=2, kind="code")

    accepted = validate_claim_support(
        tmp_path,
        "limit returns 10",
        [citation],
        semantic_judge=lambda _payload: None,
    )
    rejected = validate_claim_support(
        tmp_path,
        "limit returns 10",
        [citation],
        semantic_judge=lambda _payload: "meaning does not follow",
    )

    assert accepted == []
    assert len(rejected) == 1
    assert rejected[0].startswith("claim: src.py:2 semantic support rejected (meaning does not follow)")
    assert "claim=limit returns 10" in rejected[0]
    assert "cited=return 10" in rejected[0]


def test_high_score_unchanged_file_uses_skeleton_mode():
    fi = _fi("src/service.py", tokens=2000)
    summaries = {
        "src/service.py": {
            "summary": "Service orchestration.",
            "imports": ["src.db", "src.models"],
            "symbols": [
                {
                    "name": "BillingService",
                    "kind": "class",
                    "start_line": 1,
                    "end_line": 20,
                    "signature": "class BillingService:",
                },
                {
                    "name": "charge",
                    "kind": "function",
                    "start_line": 22,
                    "end_line": 35,
                    "signature": "def charge(invoice_id: str) -> None:",
                },
            ],
        }
    }

    selected, _ = select_files(
        files=[fi],
        scored=[(fi, 180.0, ["filename keyword match"])],
        changed_paths=set(),
        summaries=summaries,
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
    )

    assert selected[0].include_mode == "skeleton"
    assert "class BillingService" in selected[0].content
    assert "src.db" in selected[0].content


def test_skeleton_content_is_token_capped_for_large_symbol_maps():
    fi = _fi("gin.go", tokens=4000)
    summary = {
        "imports": [f"pkg/{index}" for index in range(30)],
        "symbols": [
            {
                "name": f"Handler{index}",
                "kind": "function",
                "start_line": index,
                "end_line": index + 4,
                "signature": f"func Handler{index}(context *Context, request Request, response Response) error",
            }
            for index in range(80)
        ],
    }

    content, tokens = _skeleton_content(fi, summary)

    assert content is not None
    assert tokens <= 220
    assert "skeleton truncated by AgentPack budget" in content


def test_budget_respected():
    files = [_fi(f"file{i}.py", tokens=1000) for i in range(20)]
    scored = [(fi, 80.0 - i, [f"reason {i}"]) for i, fi in enumerate(files)]
    # budget=500, summary fallback uses min(1000, 200)=200 per file -> max 2 files
    selected, _ = select_files(
        files=files,
        scored=scored,
        changed_paths=set(),
        summaries={},
        mode="balanced",
        budget=500,
        max_file_tokens=4000,
    )
    assert len(selected) <= 3


def test_budget_exhausted_files_can_be_collected_as_omitted_relevant():
    files = [
        _fi("src/refund_service.py", tokens=100),
        _fi("api/routes/refund.py", tokens=1000),
    ]
    omitted: list[OmittedRelevantFile] = []

    selected, receipts = select_files(
        files=files,
        scored=[
            (files[0], 250.0, ["filename keyword match"]),
            (files[1], 210.0, ["reverse dependency of src/refund_service.py"]),
        ],
        changed_paths=set(),
        summaries={},
        mode="balanced",
        budget=250,
        max_file_tokens=4000,
        omitted_relevant_files=omitted,
    )

    assert [sf.path for sf in selected] == ["src/refund_service.py"]
    assert any(r.path == "api/routes/refund.py" and r.reason == "budget exhausted" for r in receipts)
    assert all(receipt.citations for receipt in receipts)
    assert omitted[0].path == "api/routes/refund.py"
    assert omitted[0].score == 210.0
    assert omitted[0].risk == "high"
    assert omitted[0].suggested_mode == "summary"


def test_strong_owner_replacement_does_not_overflow_compressed_context_cap():
    files = [
        _fi("api/views/user_list.py", tokens=55),
        _fi("api/serializers/user.py", tokens=55),
        _fi("api/models/user.py", tokens=55),
        _fi("api/pagination.py", tokens=55),
    ]

    selected, receipts = select_files(
        files=files,
        scored=[
            (files[0], 430.0, ["filename keyword match", "matched role keyword: user list"]),
            (files[1], 420.0, ["filename keyword match", "matched role keyword: serializer"]),
            (files[2], 410.0, ["filename keyword match", "matched role keyword: model"]),
            (
                files[3],
                404.0,
                [
                    "filename keyword match",
                    "multi-term path match +70",
                    "symbol keyword match",
                    "matched role keyword: pagination classes",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={path.path: {"summary": path.path} for path in files},
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        min_summary_score=120,
        max_summary_files=3,
    )

    assert len(selected) == 3
    assert "api/pagination.py" in [item.path for item in selected]
    assert all("strong-owner cap overflow" not in item.reasons for item in selected)


def test_strong_test_can_overflow_compressed_context_cap_at_fixture_score():
    files = [
        _fi("api/views/user_list.py", tokens=55),
        _fi("api/serializers/user.py", tokens=55),
        _fi("api/models/user.py", tokens=55),
        _fi("tests/test_pagination.py", tokens=55),
    ]

    selected, _receipts = select_files(
        files=files,
        scored=[
            (files[0], 430.0, ["filename keyword match", "matched role keyword: user list"]),
            (files[1], 420.0, ["filename keyword match", "matched role keyword: serializer"]),
            (files[2], 410.0, ["filename keyword match", "matched role keyword: model"]),
            (
                files[3],
                389.0,
                [
                    "filename keyword match",
                    "symbol keyword match",
                    "matched role keyword: test pagination functions",
                    "matched ranking keyword: pagination",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={path.path: {"summary": path.path} for path in files},
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        min_summary_score=120,
        max_summary_files=3,
    )

    assert "tests/test_pagination.py" in [item.path for item in selected]
    assert any("strong-test cap overflow" in item.reasons for item in selected if item.path == "tests/test_pagination.py")


def test_paired_test_and_deploy_config_do_not_bypass_no_live_summary_floor():
    test_file = _fi("tests/test_users.py", tokens=60)
    dockerfile = _fi("Dockerfile", tokens=40)
    files = [test_file, dockerfile]

    selected, receipts = select_files(
        files=files,
        scored=[
            (test_file, 59.0, ["recall neighbor of src/app/users.py", "test for high-scoring src/app/users.py"]),
            (dockerfile, 37.5, ["content keyword match (1)", "config file"]),
        ],
        changed_paths=set(),
        summaries={path.path: {"summary": path.path} for path in files},
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        min_summary_score=120,
        max_summary_files=3,
    )

    assert selected == []
    assert any(receipt.reason == "summary score below floor" for receipt in receipts)


def test_citation_manifest_records_selected_files(tmp_path):
    source = tmp_path / "src.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    fi = FileInfo(
        path="src.py",
        abs_path=source,
        size_bytes=source.stat().st_size,
        estimated_tokens=10,
        hash="hash1",
        language="python",
        content=source.read_text(encoding="utf-8"),
    )
    selected, receipts = select_files(
        files=[fi],
        scored=[(fi, 100.0, ["modified"])],
        changed_paths={"src.py"},
        summaries={},
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
    )
    pack = ContextPack(
        task="cite pack",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=10,
        raw_repo_tokens=10,
        after_ignore_tokens=10,
        estimated_savings_percent=0,
        changed_files=["src.py"],
        selected_files=selected,
        receipts=receipts,
    )

    manifest = build_citation_manifest(pack)
    out = write_citation_manifest(pack, tmp_path, tmp_path / ".agentpack" / "context.md")

    assert manifest["citation_count"] >= 1
    assert manifest["selected_files"][0]["citations"][0]["path"] == "src.py"
    assert out == tmp_path / ".agentpack" / "citations.json"
    assert json.loads(out.read_text(encoding="utf-8"))["selected_files"][0]["source_hash"] == "hash1"


def test_call_site_expansion_boosts_files_calling_selected_symbols():
    service = _fi("payments/refund_service.py", tokens=100)
    route = _fi("api/routes/refund.py", tokens=100)
    summaries = {
        service.path: {
            "summary": "Refund service.",
            "symbols": [
                {
                    "name": "refund_order",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": "def refund_order(order_id):",
                }
            ],
            "defines": ["refund_order"],
            "calls": [],
        },
        route.path: {
            "summary": "Refund API route.",
            "symbols": [],
            "defines": [],
            "calls": ["refund_order"],
        },
    }
    selected = [
        SelectedFile(
            path=service.path,
            language="python",
            score=250,
            include_mode="full",
            reasons=["modified"],
        )
    ]

    expanded, count = enrich_call_site_scores(
        scored=[
            (service, 250.0, ["modified"]),
            (route, 20.0, ["score floor candidate"]),
        ],
        selected=selected,
        summaries=summaries,
        changed_paths={service.path},
    )

    route_item = next(item for item in expanded if item[0].path == route.path)
    assert count == 1
    assert route_item[1] > 20.0
    assert "caller of selected symbol `refund_order`" in route_item[2]


def test_call_site_omissions_are_high_risk_budget_exclusions():
    service = _fi("payments/refund_service.py", tokens=100)
    route = _fi("api/routes/refund.py", tokens=1000)
    summaries = {
        service.path: {
            "summary": "Refund service.",
            "symbols": [
                {
                    "name": "refund_order",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": "def refund_order(order_id):",
                }
            ],
            "defines": ["refund_order"],
            "calls": [],
        },
        route.path: {
            "summary": "Refund API route.",
            "symbols": [
                {
                    "name": f"route_helper_{i}",
                    "kind": "function",
                    "start_line": i + 1,
                    "end_line": i + 1,
                    "signature": f"def route_helper_{i}(request, response, refund_context):",
                }
                for i in range(30)
            ],
            "defines": [],
            "calls": ["controller.refund_order"],
        },
    }

    selected, _receipts = select_files(
        files=[service, route],
        scored=[
            (service, 250.0, ["modified"]),
            (route, 80.0, ["score floor candidate"]),
        ],
        changed_paths={service.path},
        summaries=summaries,
        mode="balanced",
        budget=100,
        max_file_tokens=4000,
        min_summary_score=100,
    )
    expanded, count = enrich_call_site_scores(
        scored=[
            (service, 250.0, ["modified"]),
            (route, 80.0, ["score floor candidate"]),
        ],
        selected=selected,
        summaries=summaries,
        changed_paths={service.path},
    )
    omitted: list[OmittedRelevantFile] = []

    selected, receipts = select_files(
        files=[service, route],
        scored=expanded,
        changed_paths={service.path},
        summaries=summaries,
        mode="balanced",
        budget=100,
        max_file_tokens=4000,
        min_summary_score=100,
        omitted_relevant_files=omitted,
    )

    assert count == 1
    assert [sf.path for sf in selected] == [service.path]
    assert any(r.path == route.path and r.reason == "budget exhausted" for r in receipts)
    assert omitted[0].path == route.path
    assert omitted[0].risk == "high"
    assert "caller of selected symbol `refund_order`" in omitted[0].reasons


def test_reserve_bucket_order_seeds_tests_docs_and_deps():
    changed = _fi("src/noise.py", tokens=100)
    test = _fi("tests/test_auth.py", tokens=100)
    doc = _fi("docs/auth.md", tokens=100)
    dep = _fi("src/auth_service.py", tokens=100)
    other = _fi("src/other.py", tokens=100)

    selected, _ = select_files(
        files=[changed, test, doc, dep, other],
        scored=[
            (changed, 500.0, ["modified"]),
            (other, 490.0, ["filename keyword match"]),
            (test, 120.0, ["test for high-scoring src/auth.py"]),
            (doc, 110.0, ["knowledge/architecture doc"]),
            (dep, 100.0, ["direct dependency of changed file"]),
        ],
        changed_paths={"src/noise.py"},
        summaries={},
        mode="deep",
        budget=10000,
        max_file_tokens=4000,
    )

    assert [sf.path for sf in selected[:4]] == [
        "src/noise.py",
        "tests/test_auth.py",
        "docs/auth.md",
        "src/auth_service.py",
    ]


def test_runtime_infra_config_gets_priority_over_unrelated_changed_file():
    manifest = _fi("copilot/api/manifest.yml", tokens=100)
    waf = _fi("deploy/waf/cloudformation.yml", tokens=100)
    payment_noise = _fi("payments/provider.py", tokens=100)

    selected, _ = select_files(
        files=[payment_noise, manifest, waf],
        scored=[
            (payment_noise, 150.0, ["modified"]),
            (manifest, 90.0, ["config file", "content keyword match (2)", "keyword phrase match: copilot waf"]),
            (waf, 85.0, ["config file", "content keyword match (2)", "keyword phrase match: cloudformation waf"]),
        ],
        changed_paths={"payments/provider.py"},
        summaries={},
        mode="deep",
        budget=10000,
        max_file_tokens=4000,
        keywords={"otp", "waf", "copilot", "cloudformation"},
    )

    assert [sf.path for sf in selected[:2]] == [
        "copilot/api/manifest.yml",
        "deploy/waf/cloudformation.yml",
    ]


def test_packages_directory_config_is_not_runtime_infra_priority():
    template_config = _fi("packages/create-vite/template-react/vite.config.js", tokens=40)
    deploy_manifest = _fi("copilot/api/manifest.yml", tokens=100)

    template_priority = _selection_priority(
        (
            template_config,
            275.0,
            ["config file", "filename keyword match", "workspace match packages/create-vite"],
        ),
        set(),
        4000,
    )
    deploy_priority = _selection_priority(
        (
            deploy_manifest,
            90.0,
            ["config file", "content keyword match (2)", "keyword phrase match: copilot waf"],
        ),
        set(),
        4000,
    )

    assert template_priority[0] == 0
    assert deploy_priority[0] == 1


def test_deploy_runbook_signals_get_priority_over_review_noise():
    review_noise = _fi("src/agentpack/commands/review_cmd.py", tokens=100)
    workflow = _fi(".github/workflows/prod-deploy.yml", tokens=100)
    manifest = _fi("copilot/api/manifest.yml", tokens=100)
    patch = _fi("copilot/api/overrides/cfn.patches.yml", tokens=100)
    deploy_script = _fi("scripts/deploy-prod.sh", tokens=100)

    selected, _ = select_files(
        files=[review_noise, workflow, manifest, patch, deploy_script],
        scored=[
            (review_noise, 180.0, ["filename keyword match", "content keyword match (2)"]),
            (workflow, 85.0, ["config file", "content keyword match (2)", "keyword phrase match: deploy prod"]),
            (manifest, 80.0, ["config file", "content keyword match (2)", "keyword phrase match: copilot manifest"]),
            (patch, 75.0, ["config file", "content keyword match (2)", "keyword phrase match: cfn iam"]),
            (deploy_script, 70.0, ["content keyword match (2)", "keyword phrase match: deploy service"]),
        ],
        changed_paths=set(),
        summaries={},
        mode="deep",
        budget=10000,
        max_file_tokens=4000,
        keywords={"deploy", "prod", "copilot", "cloudformation", "ecs", "cloudwatch"},
    )

    assert [sf.path for sf in selected[:4]] == [
        ".github/workflows/prod-deploy.yml",
        "copilot/api/manifest.yml",
        "copilot/api/overrides/cfn.patches.yml",
        "scripts/deploy-prod.sh",
    ]


def test_reserve_bucket_order_does_not_seed_without_changed_files():
    test = _fi("tests/test_auth.py", tokens=100)
    dep = _fi("src/auth_service.py", tokens=100)
    other = _fi("src/other.py", tokens=100)

    selected, _ = select_files(
        files=[test, dep, other],
        scored=[
            (test, 120.0, ["test for high-scoring src/auth.py"]),
            (dep, 100.0, ["direct dependency of changed file"]),
            (other, 500.0, ["filename keyword match"]),
        ],
        changed_paths=set(),
        summaries={},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
    )

    assert selected[0].path == "src/other.py"


def test_summary_score_floor_excludes_weak_unchanged_files():
    fi = _fi("weak.py")
    selected, receipts = select_files(
        files=[fi],
        scored=[(fi, 10.0, ["content keyword match (1)"])],
        changed_paths=set(),
        summaries={"weak.py": {"summary": "Weak match.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        min_summary_score=60,
    )
    assert selected == []
    assert receipts[0].reason == "summary score below floor"


def test_summary_score_floor_keeps_changed_files():
    f = _fi("changed.py", tokens=5)
    selected, _ = select_files(
        files=[f],
        scored=[(f, 10.0, ["modified"])],
        changed_paths={"changed.py"},
        summaries={},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        min_summary_score=60,
    )
    assert len(selected) == 1


def test_summary_cap_limits_unchanged_summaries():
    files = [_fi(f"file{i}.py") for i in range(3)]
    scored = [(fi, 100.0 - i, ["filename keyword match"]) for i, fi in enumerate(files)]
    selected, receipts = select_files(
        files=files,
        scored=scored,
        changed_paths=set(),
        summaries={fi.path: {"summary": f"Summary {i}", "symbols": []} for i, fi in enumerate(files)},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=2,
    )
    assert [sf.path for sf in selected] == ["file0.py", "file1.py"]
    assert any(r.reason == "compressed context cap reached" for r in receipts)


def test_summary_mode_does_not_carry_symbol_signatures():
    fi = _fi("summary.py")
    selected, _ = select_files(
        files=[fi],
        scored=[(fi, 100.0, ["content keyword match (1)"])],
        changed_paths=set(),
        summaries={
            fi.path: {
                "summary": "Short file summary.",
                "symbols": [
                    {
                        "name": "noisy_helper",
                        "kind": "function",
                        "start_line": 1,
                        "end_line": 1,
                        "signature": "def noisy_helper(argument_one, argument_two): ...",
                    }
                ],
            }
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
    )

    assert selected[0].include_mode == "summary"
    assert selected[0].symbols == []
    assert _sf_tokens(selected[0]) == estimate_tokens("Short file summary.")


def test_summary_cap_counts_skeletons_too():
    files = [
        FileInfo(
            path=f"file{i}.py",
            abs_path=Path(f"/tmp/file{i}.py"),
            language="python",
            size_bytes=100,
            estimated_tokens=100,
            hash=f"h{i}",
            content="\n".join(f"def f{j}(): pass" for j in range(60)),
        )
        for i in range(4)
    ]
    summaries = {
        fi.path: {
            "role": "python module",
            "symbols": [
                {
                    "name": f"f{j}",
                    "kind": "function",
                    "start_line": j + 1,
                    "end_line": j + 1,
                    "signature": f"def f{j}(): pass",
                }
                for j in range(30)
            ],
        }
        for fi in files
    }
    scored = [(fi, 200.0 - i, ["filename keyword match", "symbol keyword match"]) for i, fi in enumerate(files)]

    selected, receipts = select_files(
        files=files,
        scored=scored,
        changed_paths=set(),
        summaries=summaries,
        mode="balanced",
        budget=1000,
        max_file_tokens=20,
        max_summary_files=2,
    )

    assert len(selected) == 2
    assert all(sf.include_mode == "skeleton" for sf in selected)
    assert any(r.reason == "compressed context cap reached" for r in receipts)


def test_balanced_excludes_unchanged_docs_when_docs_disabled():
    docs = _fi("CHANGELOG.md")
    source = _fi("src/app.py")

    selected, receipts = select_files(
        files=[docs, source],
        scored=[
            (docs, 300.0, ["filename keyword match"]),
            (source, 100.0, ["filename keyword match"]),
        ],
        changed_paths=set(),
        summaries={},
        mode="balanced",
        budget=1000,
        max_file_tokens=200,
    )

    assert [sf.path for sf in selected] == ["src/app.py"]
    assert any(r.path == "CHANGELOG.md" and r.reason == "docs disabled by mode" for r in receipts)


def test_balanced_treats_markdown_test_docs_as_docs_when_docs_disabled():
    docs = _fi("docs/testing.md")
    source = _fi("src/testing.py")

    selected, receipts = select_files(
        files=[docs, source],
        scored=[
            (docs, 300.0, ["test for high-scoring src/testing.py", "content keyword match (2)"]),
            (source, 100.0, ["filename keyword match"]),
        ],
        changed_paths=set(),
        summaries={},
        mode="balanced",
        budget=1000,
        max_file_tokens=200,
    )

    assert [sf.path for sf in selected] == ["src/testing.py"]
    assert any(r.path == "docs/testing.md" and r.reason == "docs disabled by mode" for r in receipts)


def test_weak_signal_candidates_are_capped_and_compressed_to_summary():
    files = [_fi(f"file{i}.py", tokens=1500) for i in range(3)]
    summaries = {
        fi.path: {
            "summary": f"Summary {i}",
            "imports": [f"pkg{i}"],
            "symbols": [{
                "name": f"Service{i}",
                "kind": "class",
                "start_line": 1,
                "end_line": 10,
                "signature": f"class Service{i}:",
            }],
        }
        for i, fi in enumerate(files)
    }
    scored = [
        (fi, 180.0 - i, ["filename keyword match", "broad-task weak-signal dampening"])
        for i, fi in enumerate(files)
    ]

    selected, receipts = select_files(
        files=files,
        scored=scored,
        changed_paths=set(),
        summaries=summaries,
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=5,
        max_weak_signal_files=1,
    )

    assert [sf.path for sf in selected] == ["file0.py"]
    assert selected[0].include_mode == "summary"
    assert "weak-signal file compressed to summary" in selected[0].reasons
    assert any(r.reason == "weak-signal cap reached" for r in receipts)


def test_selection_priority_lifts_paired_tests() -> None:
    src = _fi("src/types.py", tokens=1000)
    test = _fi("tests/test_types.py", tokens=1000)

    src_priority = _selection_priority((src, 250.0, ["filename keyword match"]), set(), 4000)
    test_priority = _selection_priority((test, 230.0, ["test for high-scoring src/types.py"]), set(), 4000)

    assert test_priority > src_priority


def test_selection_priority_lifts_explicit_test_task_files() -> None:
    src = _fi("src/types.py", tokens=1000)
    test = _fi("tests/test_types.py", tokens=1000)

    src_priority = _selection_priority((src, 250.0, ["filename keyword match"]), set(), 4000)
    test_priority = _selection_priority(
        (test, 230.0, ["test for high-scoring src/types.py", "explicit test task file"]),
        set(),
        4000,
    )

    assert test_priority > src_priority


def test_negative_summary_cap_disables_summaries():
    fi = _fi("file.py")
    selected, receipts = select_files(
        files=[fi],
        scored=[(fi, 100.0, ["content keyword match (1)"])],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Noisy summary.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=-1,
    )
    assert selected == []
    assert any(r.reason == "summaries disabled by precision guard" for r in receipts)


def test_strict_summary_selection_requires_support_signal():
    fi = _fi("file.py")
    selected, receipts = select_files(
        files=[fi],
        scored=[(fi, 100.0, ["filename keyword match"])],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Noisy summary.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )
    assert selected == []
    assert any(r.reason == "compressed context needs stronger support signal" for r in receipts)


def test_strict_summary_selection_keeps_supported_summary():
    fi = _fi("file.py")
    selected, _ = select_files(
        files=[fi],
        scored=[(fi, 100.0, ["filename keyword match", "direct dependency of changed file"])],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Supported summary.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )
    assert len(selected) == 1
    assert selected[0].include_mode == "summary"


def test_strict_summary_selection_keeps_root_go_scope_source_only():
    go_file = _fi("logger.go")
    java_file = _fi("src/main/java/example/OwnerRepository.java")

    reasons = [
        "filename keyword match",
        "conventional scope path match",
        "matched role keyword: logger",
        "matched ranking keyword: logger",
    ]
    selected, receipts = select_files(
        files=[go_file, java_file],
        scored=[
            (go_file, 100.0, reasons),
            (java_file, 99.0, reasons),
        ],
        changed_paths=set(),
        summaries={
            go_file.path: {"summary": "Logger source.", "symbols": []},
            java_file.path: {"summary": "Repository source.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["logger.go"]
    assert any(
        receipt.path == java_file.path and receipt.reason == "compressed context needs stronger support signal"
        for receipt in receipts
    )


def test_same_package_test_can_overflow_summary_cap_once():
    source = _fi("packages/core/injector/injector.ts")
    paired_test = _fi("packages/core/test/injector/injector.spec.ts")
    extra_test = _fi("packages/core/test/scanner.spec.ts")

    selected, receipts = select_files(
        files=[source, paired_test, extra_test],
        scored=[
            (source, 500.0, ["matched call: this.loadProvider", "direct content evidence +270"]),
            (
                paired_test,
                450.0,
                [
                    "matched call: providers.set",
                    "content keyword match (4)",
                    "direct content evidence +220",
                    f"test for high-scoring {source.path}",
                ],
            ),
            (
                extra_test,
                440.0,
                [
                    "matched call: scanner.insertProvider",
                    "content keyword match (4)",
                    "direct content evidence +220",
                    "test for high-scoring packages/core/scanner.ts",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            source.path: {"summary": "Injector source.", "symbols": []},
            paired_test.path: {"summary": "Injector tests.", "symbols": []},
            extra_test.path: {"summary": "Scanner tests.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [source.path, paired_test.path]
    assert "same-package test overflow" in selected[1].reasons
    assert any(receipt.path == extra_test.path and receipt.reason == "compressed context cap reached" for receipt in receipts)


def test_same_package_test_overflow_requires_strong_task_evidence():
    source = _fi("packages/vite/src/node/plugins/worker.ts")
    weak_test = _fi("packages/vite/src/node/__tests__/plugins/worker.spec.ts")

    selected, receipts = select_files(
        files=[source, weak_test],
        scored=[
            (source, 500.0, ["matched call: normalizePath", "content keyword match (4)", "direct content evidence +220"]),
            (
                weak_test,
                450.0,
                [
                    "matched call: code.match",
                    "content keyword match (3)",
                    "direct content evidence +170",
                    f"test for high-scoring {source.path}",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            source.path: {"summary": "Worker plugin.", "symbols": []},
            weak_test.path: {"summary": "Worker tests.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [source.path]
    assert any(receipt.path == weak_test.path and receipt.reason == "compressed context cap reached" for receipt in receipts)


def test_same_playground_test_can_overflow_summary_cap_once():
    selected_context = _fi("playground/css/vite.config.ts")
    paired_test = _fi("playground/css/__tests__/tests.ts")
    other_playground_test = _fi("playground/html/__tests__/html.spec.ts")

    selected, receipts = select_files(
        files=[selected_context, paired_test, other_playground_test],
        scored=[
            (selected_context, 500.0, ["filename keyword match", "conventional scope path match"]),
            (
                paired_test,
                450.0,
                [
                    "filename keyword match",
                    "conventional scope path match",
                    "matched call: rawImportCss.textContent",
                    "content keyword match (4)",
                ],
            ),
            (
                other_playground_test,
                440.0,
                [
                    "filename keyword match",
                    "conventional scope path match",
                    "matched call: rawImportHtml.textContent",
                    "content keyword match (4)",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            selected_context.path: {"summary": "CSS playground config.", "symbols": []},
            paired_test.path: {"summary": "CSS playground tests.", "symbols": []},
            other_playground_test.path: {"summary": "HTML playground tests.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [selected_context.path, paired_test.path]
    assert "same-playground test overflow" in selected[1].reasons
    assert any(
        receipt.path == other_playground_test.path and receipt.reason == "compressed context cap reached"
        for receipt in receipts
    )


def test_same_playground_overflow_test_is_not_replaced_by_later_source_noise():
    selected_context = _fi("playground/css/vite.config.ts")
    paired_test = _fi("playground/css/__tests__/tests.ts")
    later_source = _fi("packages/vite/rollupLicensePlugin.ts")

    selected, receipts = select_files(
        files=[selected_context, paired_test, later_source],
        scored=[
            (
                selected_context,
                800.0,
                ["filename keyword match", "conventional scope path match", "config file", "content keyword match (4)"],
            ),
            (
                paired_test,
                120.0,
                [
                    "filename keyword match",
                    "conventional scope path match",
                    "matched call: rawImportCss.textContent",
                    "content keyword match (4)",
                ],
            ),
            (
                later_source,
                110.0,
                [
                    "symbol keyword match",
                    "matched define: renderLicense",
                    "matched call: rawImportCss.textContent",
                    "content keyword match (5)",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            selected_context.path: {"summary": "CSS playground config.", "symbols": []},
            paired_test.path: {"summary": "CSS playground tests.", "symbols": []},
            later_source.path: {"summary": "Rollup license plugin.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [selected_context.path, paired_test.path]
    assert "same-playground test overflow" in selected[1].reasons
    assert any(r.path == later_source.path and r.reason == "compressed context cap reached" for r in receipts)


def test_strict_summary_selection_keeps_direct_summary_evidence():
    fi = _fi("src/lib/auth.ts")
    selected, _ = select_files(
        files=[fi],
        scored=[(
            fi,
            240.0,
            [
                "filename keyword match",
                "symbol keyword match",
                "matched domain: auth",
                "matched define: verifySession",
            ],
        )],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Auth session helpers.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["src/lib/auth.ts"]


def test_strict_summary_selection_allows_direct_evidence_below_guarded_floor():
    fi = _fi("src/config.py")
    selected, _ = select_files(
        files=[fi],
        scored=[(fi, 105.0, ["filename keyword match", "matched naming keyword: config", "config file"])],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Runtime config.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        min_summary_score=120,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["src/config.py"]


def test_secret_candidate_bypasses_guarded_summary_floor_for_redaction():
    fi = FileInfo(
        path="src/leak.py",
        abs_path=Path("/nonexistent/src/leak.py"),
        size_bytes=24,
        estimated_tokens=10,
        hash="h1",
        language="python",
        content="".join(
            [
                "sk-ant-api03-",
                "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
                "\n",
            ]
        ),
    )
    selected, _ = select_files(
        files=[fi],
        scored=[(fi, 100.0, ["secret redaction candidate"])],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Potential secret.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        min_summary_score=120,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["src/leak.py"]
    assert selected[0].include_mode == "full"
    assert selected[0].redaction_warnings


def test_strict_summary_selection_keeps_release_metadata():
    fi = _fi("src/pkg/__init__.py")
    selected, _ = select_files(
        files=[fi],
        scored=[(fi, 150.0, ["content keyword match (1)", "release/version metadata"])],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Package version metadata.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )
    assert [sf.path for sf in selected] == ["src/pkg/__init__.py"]
    assert selected[0].include_mode == "summary"


def test_primary_release_metadata_selection_beats_noisy_source():
    pyproject = _fi("pyproject.toml", tokens=80)
    noisy_source = _fi("src/pkg/testing.py", tokens=200)

    selected, _ = select_files(
        files=[noisy_source, pyproject],
        scored=[
            (
                noisy_source,
                320.0,
                ["symbol keyword match", "matched ranking keyword: start", "has related tests"],
            ),
            (
                pyproject,
                250.0,
                ["content keyword match (2)", "config file", "release/version metadata"],
            ),
        ],
        changed_paths=set(),
        summaries={
            noisy_source.path: {"summary": "Test helper.", "symbols": []},
            pyproject.path: {"summary": "Project version metadata.", "symbols": []},
        },
        mode="balanced",
        budget=500,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert selected[0].path == "pyproject.toml"


def test_secondary_release_metadata_skipped_after_primary_metadata():
    primary = _fi("src/pkg/__init__.py")
    secondary = _fi("setup.cfg")

    selected, receipts = select_files(
        files=[primary, secondary],
        scored=[
            (primary, 220.0, ["release/version metadata"]),
            (secondary, 180.0, ["release/version metadata", "config file"]),
        ],
        changed_paths=set(),
        summaries={
            primary.path: {"summary": "Package version.", "symbols": []},
            secondary.path: {"summary": "Setup metadata.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["src/pkg/__init__.py"]
    assert any(
        r.path == "setup.cfg" and r.reason == "secondary release metadata skipped after primary"
        for r in receipts
    )


def test_secondary_release_metadata_kept_without_primary_metadata():
    secondary = _fi("setup.cfg")

    selected, _ = select_files(
        files=[secondary],
        scored=[(secondary, 180.0, ["release/version metadata", "config file"])],
        changed_paths=set(),
        summaries={secondary.path: {"summary": "Setup metadata.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["setup.cfg"]


def test_strict_balanced_caps_duplicate_config_summaries():
    primary = _fi("vite.config.ts")
    secondary = _fi("tailwind.config.ts")

    selected, receipts = select_files(
        files=[primary, secondary],
        scored=[
            (primary, 220.0, ["config file", "content keyword match (2)", "keyword phrase match: tailwind config"]),
            (secondary, 210.0, ["config file", "content keyword match (2)", "keyword phrase match: tailwind config"]),
        ],
        changed_paths=set(),
        summaries={
            primary.path: {"summary": "Vite config.", "symbols": []},
            secondary.path: {"summary": "Tailwind config.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        strict_summary_selection=True,
    )

    assert len(selected) == 1
    assert any(r.path == "tailwind.config.ts" and r.reason == "config compressed context cap reached" for r in receipts)


def test_strict_balanced_replaces_weak_config_slot_with_stronger_direct_evidence():
    weak = _fi("playground/basic/vite.config.ts", tokens=120)
    strong = _fi("vite.config.ts", tokens=120)

    selected, receipts = select_files(
        files=[weak, strong],
        scored=[
            (weak, 260.0, ["filename keyword match", "config file", "content keyword match (2)"]),
            (
                strong,
                220.0,
                [
                    "filename keyword match",
                    "config file",
                    "content keyword match (6)",
                    "keyword phrase match: vite config",
                    "matched env read: VITE_API_URL",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            weak.path: {"summary": "Weak playground config.", "symbols": []},
            strong.path: {"summary": "Root Vite config.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [strong.path]
    assert any(
        r.path == weak.path and r.reason == f"marginal slot replaced by {strong.path}"
        for r in receipts
    )


def test_marginal_replacement_allows_symbol_owner_over_broad_source_carrier():
    broad_carrier = SelectedFile(
        path="packages/app/src/node/optimizer/index.ts",
        language="typescript",
        score=490.0,
        include_mode="skeleton",
        reasons=[
            "matched call: scanImports",
            "content keyword match (5)",
            "keyword phrase match: dynamic imports",
            "direct content evidence +270",
        ],
        content="export function scanImports() {}",
        symbols=[],
    )

    replacement_index = _find_marginal_replacement(
        [broad_carrier],
        challenger_path="packages/app/src/node/plugins/importAnalysisBuild.ts",
        challenger_score=461.0,
        challenger_reasons=[
            "filename keyword match",
            "symbol keyword match",
            "matched ranking keyword: preload",
            "matched define: preloadMethod",
        ],
        challenger_tokens=290,
        selected_token_costs={broad_carrier.path: 194},
        max_extra_tokens=120,
        max_owner_carrier_extra_tokens=220,
    )

    assert replacement_index == 0


def test_marginal_replacement_protects_broad_source_carrier_without_symbol_owner():
    broad_carrier = SelectedFile(
        path="packages/app/src/node/optimizer/index.ts",
        language="typescript",
        score=490.0,
        include_mode="skeleton",
        reasons=[
            "matched call: scanImports",
            "content keyword match (5)",
            "keyword phrase match: dynamic imports",
            "direct content evidence +270",
        ],
        content="export function scanImports() {}",
        symbols=[],
    )

    replacement_index = _find_marginal_replacement(
        [broad_carrier],
        challenger_path="packages/app/src/node/plugins/importAnalysisBuild.ts",
        challenger_score=461.0,
        challenger_reasons=[
            "filename keyword match",
            "content keyword match (2)",
            "matched ranking keyword: preload",
        ],
        challenger_tokens=290,
        selected_token_costs={broad_carrier.path: 194},
        max_extra_tokens=120,
        max_owner_carrier_extra_tokens=220,
    )

    assert replacement_index is None


def test_marginal_replacement_protects_high_score_content_owner_from_lower_symbol_carrier():
    content_owner = SelectedFile(
        path="packages/core/injector/injector.ts",
        language="typescript",
        score=515.0,
        include_mode="skeleton",
        reasons=[
            "matched call: this.shouldSkipProviderLoading",
            "content keyword match (5)",
            "keyword phrase match: transient providers",
            "direct content evidence +270",
        ],
        content="export class Injector {}",
        symbols=[],
    )

    replacement_index = _find_marginal_replacement(
        [content_owner],
        challenger_path="integration/scopes/src/nested-transient/transient-logger.service.ts",
        challenger_score=367.4,
        challenger_reasons=[
            "filename keyword match",
            "symbol keyword match",
            "matched role keyword: transient logger.service classes",
            "matched ranking keyword: transient",
            "matched define: TransientLoggerService",
        ],
        challenger_tokens=36,
        selected_token_costs={content_owner.path: 160},
        max_extra_tokens=120,
        max_owner_carrier_extra_tokens=220,
    )

    assert replacement_index is None


def test_config_source_balance_seeds_source_before_duplicate_configs():
    first_config = _fi("packages/vite/vite.config.ts", tokens=120)
    second_config = _fi("packages/vite/vitest.config.ts", tokens=120)
    source = _fi("packages/vite/src/node/server/index.ts", tokens=120)

    selected, receipts = select_files(
        files=[first_config, second_config, source],
        scored=[
            (first_config, 420.0, ["config file", "content keyword match (3)", "keyword phrase match: vite config"]),
            (second_config, 410.0, ["config file", "content keyword match (3)", "keyword phrase match: vite config"]),
            (
                source,
                300.0,
                ["symbol keyword match", "matched define: createServer", "content keyword match (3)"],
            ),
        ],
        changed_paths=set(),
        summaries={
            first_config.path: {"summary": "Vite config.", "symbols": []},
            second_config.path: {"summary": "Vitest config.", "symbols": []},
            source.path: {"summary": "Server source.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=2,
    )

    assert [sf.path for sf in selected] == [source.path, first_config.path]
    assert any(r.path == second_config.path and r.reason == "compressed context cap reached" for r in receipts)


def test_context_shape_order_does_not_seed_tests_before_duplicate_sources():
    source_one = _fi("packages/app/src/auth/session.ts", tokens=120)
    source_two = _fi("packages/app/src/auth/cache.ts", tokens=120)
    paired_test = _fi("packages/app/src/auth/session.spec.ts", tokens=120)

    selected, receipts = select_files(
        files=[source_one, source_two, paired_test],
        scored=[
            (
                source_one,
                520.0,
                ["matched call: verifySession", "content keyword match (4)", "direct content evidence +220"],
            ),
            (
                source_two,
                500.0,
                ["matched call: loadSession", "content keyword match (3)", "direct content evidence +170"],
            ),
            (
                paired_test,
                360.0,
                [
                    "matched define: test_verify_session",
                    "matched call: verifySession",
                    "content keyword match (5)",
                    f"test for high-scoring {source_one.path}",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            source_one.path: {"summary": "Session owner.", "symbols": []},
            source_two.path: {"summary": "Session cache.", "symbols": []},
            paired_test.path: {"summary": "Session tests.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=2,
    )

    assert [sf.path for sf in selected] == [source_one.path, source_two.path]
    assert any(r.path == paired_test.path and r.reason == "compressed context cap reached" for r in receipts)


def test_context_shape_order_respects_package_scope():
    source_one = _fi("packages/app/src/auth/session.ts", tokens=120)
    source_two = _fi("packages/app/src/auth/cache.ts", tokens=120)
    other_package_test = _fi("packages/admin/src/auth/session.spec.ts", tokens=120)

    selected, receipts = select_files(
        files=[source_one, source_two, other_package_test],
        scored=[
            (
                source_one,
                520.0,
                ["matched call: verifySession", "content keyword match (4)", "direct content evidence +220"],
            ),
            (
                source_two,
                500.0,
                ["matched call: loadSession", "content keyword match (3)", "direct content evidence +170"],
            ),
            (
                other_package_test,
                480.0,
                [
                    "matched define: test_verify_session",
                    "matched call: verifySession",
                    "content keyword match (5)",
                    "direct content evidence +170",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            source_one.path: {"summary": "Session owner.", "symbols": []},
            source_two.path: {"summary": "Session cache.", "symbols": []},
            other_package_test.path: {"summary": "Admin session tests.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=2,
    )

    assert [sf.path for sf in selected] == [source_one.path, source_two.path]
    assert any(r.path == other_package_test.path and r.reason == "compressed context cap reached" for r in receipts)


def test_context_shape_order_requires_concrete_scope():
    focused_source = _fi("render/reader.go", tokens=120)
    root_source = _fi("gin.go", tokens=120)
    sibling_source = _fi("binding/default_validator.go", tokens=120)

    selected, receipts = select_files(
        files=[focused_source, root_source, sibling_source],
        scored=[
            (
                focused_source,
                520.0,
                ["matched call: Render", "content keyword match (4)", "direct content evidence +220"],
            ),
            (
                root_source,
                500.0,
                ["matched define: Engine", "content keyword match (3)", "direct content evidence +170"],
            ),
            (
                sibling_source,
                480.0,
                ["matched define: Validate", "content keyword match (3)", "direct content evidence +170"],
            ),
        ],
        changed_paths=set(),
        summaries={
            focused_source.path: {"summary": "Focused render code.", "symbols": []},
            root_source.path: {"summary": "Root engine code.", "symbols": []},
            sibling_source.path: {"summary": "Binding validator.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=2,
    )

    assert {sf.path for sf in selected} == {focused_source.path, root_source.path}
    assert any(r.path == sibling_source.path and r.reason == "compressed context cap reached" for r in receipts)


def test_scoped_replacement_can_spend_small_token_delta_for_stronger_evidence():
    weak = _fi("packages/vite/src/node/plugins/html.ts", tokens=120)
    strong = _fi("packages/vite/src/node/plugins/index.ts", tokens=120)

    selected, receipts = select_files(
        files=[weak, strong],
        scored=[
            (weak, 500.0, ["filename keyword match", "content keyword match (1)"]),
            (
                strong,
                300.0,
                [
                    "symbol keyword match",
                    "matched define: createServer",
                    "matched call: resolveServerOptions",
                    "content keyword match (4)",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            weak.path: {"summary": "Short plugin.", "symbols": []},
            strong.path: {
                "summary": "Server source has direct evidence and a slightly longer useful summary.",
                "symbols": [
                    {
                        "name": "createServer",
                        "kind": "function",
                        "start_line": 1,
                        "end_line": 3,
                        "signature": "export async function createServer()",
                    }
                ],
            },
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [strong.path]
    assert any(r.path == weak.path and r.reason == f"marginal slot replaced by {strong.path}" for r in receipts)


def test_specific_source_scope_can_replace_generic_parent_source():
    weak_parent = _fi("packages/vite/src/node/config.ts", tokens=120)
    strong_child = _fi("packages/vite/src/node/server/index.ts", tokens=120)

    selected, receipts = select_files(
        files=[weak_parent, strong_child],
        scored=[
            (weak_parent, 360.0, ["symbol keyword match", "matched define: applyConfig", "content keyword match (1)"]),
            (
                strong_child,
                280.0,
                ["matched call: ssrFixStacktrace", "content keyword match (3)", "direct content evidence +120"],
            ),
        ],
        changed_paths=set(),
        summaries={
            weak_parent.path: {"summary": "Generic node config.", "symbols": []},
            strong_child.path: {"summary": "Server source with direct evidence.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [strong_child.path]
    assert any(r.path == weak_parent.path and r.reason == f"marginal slot replaced by {strong_child.path}" for r in receipts)


def test_high_signal_rescue_can_replace_weak_cross_family_context():
    weak_config = SelectedFile(
        path=".github/workflows/tests.yaml",
        language="yaml",
        score=160.0,
        include_mode="summary",
        reasons=["content keyword match (1)", "config file", "recently modified"],
    )

    replacement_index = _find_marginal_replacement(
        [weak_config],
        challenger_path="binding/bson.go",
        challenger_score=520.0,
        challenger_reasons=[
            "filename keyword match",
            "conventional scope path match",
            "multi-term path match +70",
            "symbol keyword match",
        ],
        challenger_tokens=100,
        selected_token_costs={weak_config.path: 80},
        required_family="source",
        max_extra_tokens=120,
    )

    assert replacement_index == 0


def test_budgeted_strong_source_can_replace_weak_cross_package_template_slot():
    weak_template = _fi("packages/create-vite/template-react/vite.config.js", tokens=80)
    strong_source = _fi("packages/vite/src/node/server/openBrowser.ts", tokens=640)

    selected, receipts = select_files(
        files=[weak_template, strong_source],
        scored=[
            (
                weak_template,
                500.0,
                ["filename keyword match", "multi-term path match +175", "matched call: react", "content keyword match (1)"],
            ),
            (
                strong_source,
                360.0,
                [
                    "content keyword match (3)",
                    "quoted literal match: create react app",
                    "keyword phrase match: create react app",
                    "direct content evidence +170",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            weak_template.path: {"summary": "React template config.", "symbols": []},
            strong_source.path: {"summary": "Open browser logic for create-react-app migration links.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [strong_source.path]
    assert any(r.path == weak_template.path and r.reason == f"marginal slot replaced by {strong_source.path}" for r in receipts)


def test_budgeted_strong_source_can_replace_weak_test_artifact_slot():
    weak_test_artifact = _fi("packages/vite/src/node/__tests__/package.json", tokens=120)
    strong_source = _fi("packages/vite/src/node/server/openBrowser.ts", tokens=640)

    selected, receipts = select_files(
        files=[weak_test_artifact, strong_source],
        scored=[
            (
                weak_test_artifact,
                500.0,
                [
                    "content keyword match (1)",
                    "config file",
                    "high churn (1 commits)",
                    "test for high-scoring packages/vite/src/node/utils.ts",
                ],
            ),
            (
                strong_source,
                360.0,
                [
                    "content keyword match (3)",
                    "quoted literal match: create react app",
                    "keyword phrase match: create react app",
                    "direct content evidence +170",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            weak_test_artifact.path: {"summary": "Test fixture package metadata.", "symbols": []},
            strong_source.path: {"summary": "Open browser logic for create-react-app migration links.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [strong_source.path]
    assert any(r.path == weak_test_artifact.path and r.reason == f"marginal slot replaced by {strong_source.path}" for r in receipts)


def test_token_neutral_strong_test_can_replace_weaker_same_scope_test():
    weak_test = _fi("packages/vite/src/node/__tests__/config.spec.ts", tokens=220)
    strong_test = _fi("packages/vite/src/node/__tests__/utils.spec.ts", tokens=160)

    selected, receipts = select_files(
        files=[weak_test, strong_test],
        scored=[
            (
                weak_test,
                500.0,
                ["filename keyword match", "matched define: test_config_defaults", "content keyword match (1)"],
            ),
            (
                strong_test,
                360.0,
                [
                    "matched define: test_clean_up_eslint_config",
                    "matched call: resolveConfig",
                    "content keyword match (5)",
                    "direct content evidence +170",
                ],
            ),
        ],
        changed_paths=set(),
        summaries={
            weak_test.path: {"summary": "Broad config tests covering several defaults and setup branches.", "symbols": []},
            strong_test.path: {"summary": "Focused utils tests.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [strong_test.path]
    assert any(r.path == weak_test.path and r.reason == f"marginal slot replaced by {strong_test.path}" for r in receipts)


def test_token_neutral_source_does_not_replace_selected_test_context():
    selected_test = _fi("packages/vite/src/node/__tests__/config.spec.ts", tokens=220)
    source = _fi("packages/vite/src/node/config.ts", tokens=160)

    selected, receipts = select_files(
        files=[selected_test, source],
        scored=[
            (
                selected_test,
                500.0,
                ["matched define: test_config_defaults", "matched call: resolveConfig", "content keyword match (4)"],
            ),
            (
                source,
                360.0,
                ["matched define: resolveConfig", "matched call: loadEnv", "content keyword match (5)", "direct content evidence +170"],
            ),
        ],
        changed_paths=set(),
        summaries={
            selected_test.path: {"summary": "Focused config tests covering setup branches.", "symbols": []},
            source.path: {"summary": "Focused config source.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [selected_test.path]
    assert any(r.path == source.path and r.reason == "compressed context cap reached" for r in receipts)


def test_generic_parent_source_cannot_replace_specific_child_source():
    specific_child = _fi("packages/vite/src/node/optimizer/index.ts", tokens=120)
    generic_parent = _fi("packages/vite/src/node/build.ts", tokens=120)

    selected, receipts = select_files(
        files=[specific_child, generic_parent],
        scored=[
            (
                specific_child,
                500.0,
                ["matched call: closeBundle", "content keyword match (3)", "direct content evidence +120"],
            ),
            (generic_parent, 360.0, ["symbol keyword match", "matched define: build", "content keyword match (1)"]),
        ],
        changed_paths=set(),
        summaries={
            specific_child.path: {"summary": "Optimizer source with direct evidence.", "symbols": []},
            generic_parent.path: {"summary": "Generic build source.", "symbols": []},
        },
        mode="balanced",
        budget=1000,
        max_file_tokens=4000,
        max_summary_files=1,
    )

    assert [sf.path for sf in selected] == [specific_child.path]
    assert any(r.path == generic_parent.path and r.reason == "compressed context cap reached" for r in receipts)


def test_strong_test_cap_overflow_adds_two_cheap_high_evidence_tests():
    paths = [
        "integration/injector/e2e/request-0.spec.ts",
        "integration/injector/e2e/request-1.spec.ts",
        "packages/core/test/injector/request-2.spec.ts",
        "integration/graphql/e2e/request-3.spec.ts",
        "integration/scopes/e2e/request-4.spec.ts",
        "integration/scopes/e2e/request-5.spec.ts",
        "integration/scopes/e2e/request-6.spec.ts",
    ]
    files = [_fi(path, tokens=80) for path in paths]
    scored = [
        (
            fi,
            900.0 - index,
            [
                "symbol keyword match",
                "matched call: request",
                "content keyword match (3)",
                "explicit test task file",
            ],
        )
        for index, fi in enumerate(files)
    ]

    selected, receipts = select_files(
        files=files,
        scored=scored,
        changed_paths=set(),
        summaries={fi.path: {"summary": f"Request scoped test {index}.", "symbols": []} for index, fi in enumerate(files)},
        mode="balanced",
        budget=5000,
        max_file_tokens=4000,
        max_summary_files=4,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [fi.path for fi in files[:6]]
    assert selected[4].reasons.count("strong-test cap overflow") == 1
    assert selected[5].reasons.count("strong-test cap overflow") == 1
    assert any(r.path == files[6].path and r.reason == "tests compressed context cap reached" for r in receipts)


def test_cleanup_refactor_candidates_can_bypass_guarded_summary_floor():
    files = [
        _fi("src/main/java/example/OwnerRepository.java", tokens=200),
        _fi("src/main/java/example/PetTypeRepository.java", tokens=180),
        _fi("src/test/java/example/PetControllerTests.java", tokens=160),
        _fi("pom.xml", tokens=80),
    ]
    maintenance_reasons = [
        "content keyword match (1)",
        "implementation role match",
        "cross-layer related implementation",
    ]

    selected, receipts = select_files(
        files=files,
        scored=[
            (files[0], 90.0, maintenance_reasons),
            (files[1], 75.0, maintenance_reasons),
            (files[2], 85.0, maintenance_reasons),
            (files[3], 65.0, ["recently modified", "high churn (29 commits)"]),
        ],
        changed_paths=set(),
        summaries={fi.path: {"summary": f"Summary for {fi.path}.", "symbols": []} for fi in files},
        mode="balanced",
        budget=5000,
        max_file_tokens=4000,
        keywords={"unused", "imports"},
        min_summary_score=130.0,
        max_summary_files=4,
        strict_summary_selection=True,
    )

    assert {sf.path for sf in selected} == {files[0].path, files[1].path, files[2].path}
    assert all(sf.include_mode == "summary" for sf in selected)
    assert any(r.path == files[3].path and r.reason == "summary score below floor" for r in receipts)


def test_cleanup_refactor_config_file_can_bypass_guarded_summary_floor():
    config = _fi("pyproject.toml", tokens=120)
    weak_config = _fi("pom.xml", tokens=120)

    selected, receipts = select_files(
        files=[config, weak_config],
        scored=[
            (config, 88.0, ["filename keyword match", "content keyword match (2)", "config file"]),
            (weak_config, 65.0, ["content keyword match (1)", "config file"]),
        ],
        changed_paths=set(),
        summaries={
            config.path: {"summary": "Python project configuration.", "symbols": []},
            weak_config.path: {"summary": "Maven project configuration.", "symbols": []},
        },
        mode="balanced",
        budget=5000,
        max_file_tokens=4000,
        keywords={"unused", "config"},
        min_summary_score=130.0,
        max_summary_files=4,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [config.path]
    assert any(r.path == weak_config.path and r.reason == "summary score below floor" for r in receipts)


def test_deprecation_maintenance_code_can_bypass_guarded_summary_floor():
    source = _fi("src/markupsafe/__init__.py", tokens=160)
    weak_source = _fi("src/markupsafe/_native.py", tokens=160)

    selected, receipts = select_files(
        files=[source, weak_source],
        scored=[
            (source, 60.0, ["content keyword match (2)", "recently modified", "high churn (25 commits)"]),
            (weak_source, 55.0, ["recently modified", "high churn (25 commits)"]),
        ],
        changed_paths=set(),
        summaries={
            source.path: {"summary": "Package init deprecation cleanup.", "symbols": []},
            weak_source.path: {"summary": "Native helpers.", "symbols": []},
        },
        mode="balanced",
        budget=5000,
        max_file_tokens=4000,
        keywords={"deprecated", "code"},
        min_summary_score=130.0,
        max_summary_files=4,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [source.path]
    assert any(r.path == weak_source.path and r.reason == "summary score below floor" for r in receipts)


def test_cleanup_refactor_cap_overflow_adds_one_same_scope_candidate():
    first = _fi("src/main/java/example/OwnerRepository.java", tokens=200)
    overflow = _fi("src/main/java/example/PetTypeRepository.java", tokens=180)
    unrelated = _fi("src/main/java/other/OtherRepository.java", tokens=180)
    maintenance_reasons = [
        "content keyword match (1)",
        "implementation role match",
        "cross-layer related implementation",
    ]

    selected, receipts = select_files(
        files=[first, overflow, unrelated],
        scored=[
            (first, 95.0, maintenance_reasons),
            (overflow, 90.0, maintenance_reasons),
            (unrelated, 85.0, maintenance_reasons),
        ],
        changed_paths=set(),
        summaries={
            first.path: {"summary": "Owner repository cleanup.", "symbols": []},
            overflow.path: {"summary": "Pet type repository cleanup.", "symbols": []},
            unrelated.path: {"summary": "Other repository cleanup.", "symbols": []},
        },
        mode="balanced",
        budget=5000,
        max_file_tokens=4000,
        keywords={"cleanup", "imports"},
        min_summary_score=130.0,
        max_summary_files=1,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [first.path, overflow.path]
    assert "cleanup-refactor cap overflow" in selected[1].reasons
    assert any(r.path == unrelated.path and r.reason == "compressed context cap reached" for r in receipts)


def test_direct_source_candidate_beats_smaller_playground_match():
    source = _fi("packages/vite/src/node/plugins/css.ts", tokens=900)
    playground = _fi("playground/css/lightningcss-plugins.js", tokens=50)

    selected, _ = select_files(
        files=[source, playground],
        scored=[
            (
                source,
                240.0,
                ["filename keyword match", "symbol keyword match", "matched define: cssConfigDefaults"],
            ),
            (
                playground,
                240.0,
                ["filename keyword match", "symbol keyword match", "matched define: lightningcssPlugin"],
            ),
        ],
        changed_paths=set(),
        summaries={
            source.path: {"summary": "CSS plugin source.", "symbols": []},
            playground.path: {"summary": "Playground plugin.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=1,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["packages/vite/src/node/plugins/css.ts"]


def test_root_go_source_candidate_beats_related_test_noise():
    source = _fi("context.go", tokens=800)
    test = _fi("context_test.go", tokens=600)

    source_priority = _selection_priority(
        (
            source,
            390.0,
            [
                "filename keyword match",
                "matched domain: context packing",
                "content keyword match (3)",
            ],
        ),
        set(),
        4000,
    )
    test_priority = _selection_priority(
        (
            test,
            475.0,
            [
                "filename keyword match",
                "matched domain: context packing",
                "content keyword match (3)",
                "test for high-scoring context.go",
            ],
        ),
        set(),
        4000,
    )

    assert source_priority > test_priority


def test_package_root_source_candidate_beats_tiny_integration_source_noise():
    expected = _fi("packages/core/middleware/middleware-module.ts", tokens=3000)
    noise = _fi("integration/injector/src/inject/core.service.ts", tokens=40)

    selected, _receipts = select_files(
        files=[noise, expected],
        scored=[
            (
                noise,
                470.0,
                ["filename keyword match", "symbol keyword match", "matched define: CoreService"],
            ),
            (
                expected,
                590.0,
                ["filename keyword match", "symbol keyword match", "matched define: MiddlewareModule"],
            ),
        ],
        changed_paths=set(),
        summaries={
            noise.path: {"summary": "Core service.", "symbols": []},
            expected.path: {"summary": "Middleware module.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=1,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [expected.path]


def test_template_path_does_not_get_direct_source_priority():
    source = _fi("packages/vite/src/node/server/openBrowser.ts", tokens=900)
    template = _fi("packages/create-vite/template-react/src/App.jsx", tokens=80)

    selected, receipts = select_files(
        files=[source, template],
        scored=[
            (
                template,
                240.0,
                ["filename keyword match", "symbol keyword match", "matched define: App"],
            ),
            (
                source,
                220.0,
                ["symbol keyword match", "matched define: openBrowser", "matched call: openBrowser"],
            ),
        ],
        changed_paths=set(),
        summaries={
            source.path: {"summary": "Open browser links.", "symbols": []},
            template.path: {"summary": "React app template.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        max_summary_files=1,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["packages/vite/src/node/server/openBrowser.ts"]


def test_java_package_named_samples_is_not_example_path():
    source = _fi("src/main/java/org/springframework/samples/petclinic/model/Person.java")
    test = _fi("src/test/java/org/springframework/samples/petclinic/model/ValidatorTests.java")

    selected, receipts = select_files(
        files=[source, test],
        scored=[
            (
                source,
                270.0,
                ["filename keyword match", "matched role keyword: Person", "matched define: Person", "content keyword match (2)"],
            ),
            (
                test,
                140.0,
                ["filename keyword match", "content keyword match (5)", "direct dependency of changed file"],
            ),
        ],
        changed_paths=set(),
        summaries={
            source.path: {"summary": "Person entity.", "symbols": []},
            test.path: {"summary": "Validator tests.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [source.path, test.path]
    assert not any(r.path == test.path and r.reason == "examples compressed context cap reached" for r in receipts)


def test_direct_source_candidate_does_not_consume_expansion_family_cap():
    expansion = _fi("packages/vite/src/node/plugins/resolve.ts")
    source = _fi("packages/vite/src/node/plugins/css.ts")

    selected, receipts = select_files(
        files=[expansion, source],
        scored=[
            (
                expansion,
                260.0,
                ["content keyword match (3)", "large supported file"],
            ),
            (
                source,
                250.0,
                ["symbol keyword match", "matched define: cssConfigDefaults", "large supported file"],
            ),
        ],
        changed_paths=set(),
        summaries={
            expansion.path: {"summary": "Resolve plugin.", "symbols": []},
            source.path: {"summary": "CSS plugin.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert "packages/vite/src/node/plugins/css.ts" in {sf.path for sf in selected}
    assert not any(r.path == source.path and r.reason == "expansion compressed context cap reached" for r in receipts)


def test_specific_config_path_match_can_select_two_configs_under_strict_cap():
    first = _fi("playground/tailwind-v3/tailwind.config.ts", tokens=120)
    second = _fi("playground/tailwind/tailwind.config.ts", tokens=120)
    generic = _fi("playground/hmr-full-bundle-mode/vite.config.ts", tokens=120)

    selected, receipts = select_files(
        files=[first, second, generic],
        scored=[
            (first, 350.0, ["filename keyword match", "multi-term path match +175", "matched naming keyword: tailwind", "config file"]),
            (second, 340.0, ["filename keyword match", "multi-term path match +175", "matched naming keyword: tailwind", "config file"]),
            (generic, 330.0, ["filename keyword match", "matched naming keyword: playground", "config file"]),
        ],
        changed_paths=set(),
        summaries={
            first.path: {"summary": "Tailwind v3 config.", "symbols": []},
            second.path: {"summary": "Tailwind config.", "symbols": []},
            generic.path: {"summary": "Generic playground config.", "symbols": []},
        },
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [first.path, second.path]
    assert any(r.path == generic.path and r.reason == "config compressed context cap reached" for r in receipts)


def test_strict_summary_selection_allows_build_metadata_reason():
    pom = _fi("pom.xml", tokens=1000)

    selected, _receipts = select_files(
        files=[pom],
        scored=[(pom, 260.0, ["content keyword match (1)", "config file", "build/dependency metadata"])],
        changed_paths=set(),
        summaries={pom.path: {"summary": "Maven build metadata.", "symbols": []}},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == [pom.path]


def test_strict_balanced_excludes_lockfiles_without_release_evidence():
    lockfile = _fi("package-lock.json")

    selected, receipts = select_files(
        files=[lockfile],
        scored=[(lockfile, 220.0, ["config file", "content keyword match (3)", "keyword phrase match: react app"])],
        changed_paths=set(),
        summaries={lockfile.path: {"summary": "Dependency lockfile.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        strict_summary_selection=True,
    )

    assert selected == []
    assert any(r.path == "package-lock.json" and r.reason == "lock-generated compressed context cap reached" for r in receipts)


def test_strict_summary_selection_keeps_corroborated_phrase_match():
    fi = _fi("src/owner_controller.py")
    selected, _ = select_files(
        files=[fi],
        scored=[(
            fi,
            250.0,
            [
                "filename keyword match",
                "content keyword match (1)",
                "keyword phrase match: allowed fields",
                "implementation role match",
            ],
        )],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Owner form allowed fields.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        min_summary_score=100,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["src/owner_controller.py"]


def test_strict_summary_selection_keeps_high_content_phrase_match():
    fi = _fi("src/open_browser.py")
    selected, _ = select_files(
        files=[fi],
        scored=[(
            fi,
            250.0,
            [
                "filename keyword match",
                "content keyword match (3)",
                "keyword phrase match: react app",
            ],
        )],
        changed_paths=set(),
        summaries={fi.path: {"summary": "React app link handling.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        min_summary_score=100,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["src/open_browser.py"]


def test_strict_summary_selection_keeps_high_content_source_match_without_phrase():
    fi = _fi("context.go")
    selected, _ = select_files(
        files=[fi],
        scored=[(
            fi,
            215.0,
            [
                "content keyword match (5)",
                "recently modified",
                "high churn (14 commits)",
                "large supported file",
            ],
        )],
        changed_paths=set(),
        summaries={fi.path: {"summary": "HTTP context helpers.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        min_summary_score=100,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["context.go"]


def test_strict_summary_selection_keeps_source_with_correlated_path_evidence():
    fi = _fi("cmd/server/main.go")
    selected, _ = select_files(
        files=[fi],
        scored=[(
            fi,
            220.0,
            [
                "filename keyword match",
                "keyword phrase match: server main",
                "multi-term path match +70",
                "matched role keyword: main",
            ],
        )],
        changed_paths=set(),
        summaries={fi.path: {"summary": "Go server entrypoint.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["cmd/server/main.go"]


def test_strict_summary_selection_keeps_deploy_config_with_content_hit():
    dockerfile = _fi("Dockerfile")
    selected, _ = select_files(
        files=[dockerfile],
        scored=[(
            dockerfile,
            120.0,
            [
                "content keyword match (1)",
                "config file",
            ],
        )],
        changed_paths=set(),
        summaries={dockerfile.path: {"summary": "Docker build metadata.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        strict_summary_selection=True,
    )

    assert [sf.path for sf in selected] == ["Dockerfile"]


def test_balanced_excludes_gitignore_without_ignore_task_evidence():
    gitignore = _fi(".gitignore")

    selected, receipts = select_files(
        files=[gitignore],
        scored=[(gitignore, 220.0, ["filename keyword match", "content keyword match (2)"])],
        changed_paths=set(),
        summaries={gitignore.path: {"summary": "Ignore build output.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        keywords={"current", "bar", "factor"},
    )

    assert selected == []
    assert any(
        r.path == ".gitignore" and r.reason == "ignore-control file lacks ignore-task evidence"
        for r in receipts
    )


def test_balanced_keeps_gitignore_for_ignore_task():
    gitignore = _fi(".gitignore")

    selected, _receipts = select_files(
        files=[gitignore],
        scored=[(
            gitignore,
            220.0,
            ["filename keyword match", "content keyword match (1)", "keyword phrase match: gitignore"],
        )],
        changed_paths=set(),
        summaries={gitignore.path: {"summary": "Ignore build output.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        keywords={"gitignore", "ignore"},
    )

    assert [sf.path for sf in selected] == [".gitignore"]


def test_balanced_excludes_unrelated_test_without_direct_evidence():
    test_file = _fi("tests/intraday_current_bar_test.py")

    selected, receipts = select_files(
        files=[test_file],
        scored=[(
            test_file,
            260.0,
            [
                "filename keyword match",
                "matched domain: intraday factor",
                "content keyword match (2)",
            ],
        )],
        changed_paths=set(),
        summaries={test_file.path: {"summary": "Current bar intraday tests.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        keywords={"current", "bar", "factor"},
    )

    assert selected == []
    assert any(
        r.path == test_file.path and r.reason == "test file lacks direct task evidence"
        for r in receipts
    )


def test_balanced_keeps_paired_test_with_direct_evidence():
    test_file = _fi("tests/current_bar_test.py")

    selected, _receipts = select_files(
        files=[test_file],
        scored=[(
            test_file,
            260.0,
            [
                "filename keyword match",
                "content keyword match (3)",
                "direct content evidence +170",
                "test for src/current_bar.py",
            ],
        )],
        changed_paths=set(),
        summaries={test_file.path: {"summary": "Current bar regression test.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        keywords={"current", "bar"},
    )

    assert [sf.path for sf in selected] == [test_file.path]


def test_balanced_excludes_broad_factor_file_without_direct_evidence():
    broad = _fi("src/factors/current_bar_factor.py")

    selected, receipts = select_files(
        files=[broad],
        scored=[(
            broad,
            260.0,
            [
                "filename keyword match",
                "matched domain: factors",
                "matched role keyword: factor model",
                "content keyword match (1)",
                "implementation role match",
            ],
        )],
        changed_paths=set(),
        summaries={broad.path: {"summary": "General factor calculations.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        keywords={"current", "bar", "factor"},
    )

    assert selected == []
    assert any(
        r.path == broad.path and r.reason == "broad family match lacks direct task evidence"
        for r in receipts
    )


def test_balanced_keeps_factor_file_with_direct_definition_evidence():
    direct = _fi("src/factors/current_bar_factor.py")

    selected, _receipts = select_files(
        files=[direct],
        scored=[(
            direct,
            320.0,
            [
                "filename keyword match",
                "matched define: CurrentBarFactor",
                "content keyword match (2)",
                "keyword phrase match: current bar",
                "implementation role match",
            ],
        )],
        changed_paths=set(),
        summaries={direct.path: {"summary": "Defines CurrentBarFactor.", "symbols": []}},
        mode="balanced",
        budget=1000,
        max_file_tokens=100,
        keywords={"current", "bar", "factor"},
    )

    assert [sf.path for sf in selected] == [direct.path]


def test_excluded_ignored_files():
    fi = FileInfo(
        path="node_modules/x.js",
        abs_path=Path("/nonexistent/node_modules/x.js"),
        size_bytes=100,
        estimated_tokens=25,
        ignored=True,
    )
    scored = [(fi, 50.0, ["test"])]
    selected, receipts = select_files(
        files=[fi],
        scored=scored,
        changed_paths=set(),
        summaries={},
        mode="balanced",
        budget=10000,
        max_file_tokens=4000,
    )
    assert len(selected) == 0
    assert any(r.action == "excluded" for r in receipts)


def test_render_includes_freshness_metadata():
    pack = ContextPack(
        task="fix auth",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
        freshness={
            "generated_at": "2026-05-13T00:00:00+00:00",
            "git_branch": "codex/example",
            "git_sha": "abc123",
            "agentpack_version": "0.test",
            "source_command": "agentpack pack --agent claude --task auto",
            "cwd": "/repo",
            "git_root": "/repo",
            "worktree_path": "/repo",
            "task_source": "task.md",
            "changed_files_source": "git working tree",
            "workspace_roots": ["apps/dashboard", "packages/core"],
            "snapshot_root_hash": "root123",
            "dirty_files_count": 2,
        },
        freshness_warnings=["Task terms are broad/generic; pack tightened weak-summary selection."],
        execution_state={
            "task": {
                "status": "in_progress",
                "summary": "Budget done.",
                "state_file": ".agentpack/task_state.md",
                "checklist": {"done": 1, "open": 2, "blocked": 0},
            },
            "git": {
                "branch": "main",
                "sha": "abc123456789",
                "staged_count": 1,
                "unstaged_count": 2,
                "untracked_count": 3,
                "ahead": 1,
                "behind": 0,
            },
            "runtime": {"docker": "running", "compose_file": "docker-compose.yml"},
        },
        concurrent_context={
            "thread_id": "thread-a",
            "active_threads": 2,
            "conflicts": [
                {
                    "thread_id": "thread-b",
                    "task": "edit auth",
                    "status": "in_progress",
                    "overlap": ["src/auth.py"],
                    "overlap_count": 1,
                }
            ],
        },
    )

    rendered = render_claude(pack)

    assert "## Freshness" in rendered
    assert "<!-- agentpack:freshness" in rendered
    freshness_block = rendered.split("<!-- agentpack:freshness", 1)[1].split("-->", 1)[0]
    assert "@format toon" in freshness_block
    assert "active_context: mcp" in freshness_block
    assert "fallback_context: markdown" in freshness_block
    assert "snapshot_root_hash: root123" in freshness_block
    assert "refresh_required: false" in freshness_block
    assert "cli_refresh_command: agentpack guard --agent claude --repair-stale --refresh-context" in freshness_block
    assert "agentpack_version: 0.test" in freshness_block
    assert "git_root: /repo" in freshness_block
    assert "**Generated:** 2026-05-13T00:00:00+00:00" in rendered
    assert "**Source command:** agentpack pack --agent claude --task auto" in rendered
    assert "**Task source:** task.md" in rendered
    assert "**Workspaces:** apps/dashboard, packages/core" in rendered
    assert "Refresh recommended" in rendered
    assert "## Execution State" in rendered
    assert "**Task status:** in_progress" in rendered
    assert "## Concurrent Context" in rendered
    assert "`thread-b`" in rendered
    assert "If this pack's task does not match the user's current task" in rendered
    assert "agentpack pack --task auto" in rendered


def test_render_stable_prefix_survives_volatile_pack_changes():
    base = ContextPack(
        task="fix auth",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
        freshness={
            "generated_at": "2026-05-13T00:00:00+00:00",
            "git_branch": "main",
            "git_sha": "abc123",
            "snapshot_root_hash": "root123",
        },
    )
    refreshed = ContextPack(
        task="add billing retry",
        agent="claude",
        mode="deep",
        budget=2000,
        token_estimate=150,
        raw_repo_tokens=2000,
        after_ignore_tokens=1200,
        estimated_savings_percent=94.0,
        changed_files=["src/billing.py"],
        selected_files=[],
        receipts=[],
        freshness={
            "generated_at": "2026-05-14T00:00:00+00:00",
            "git_branch": "feature/billing",
            "git_sha": "def456",
            "snapshot_root_hash": "root456",
        },
    )

    base_rendered = render_claude(base)
    refreshed_rendered = render_claude(refreshed)
    marker = "<!-- agentpack:stable-prefix:end -->"

    assert base_rendered.split(marker, 1)[0] == refreshed_rendered.split(marker, 1)[0]
    assert base_rendered.index(marker) < base_rendered.index("<!-- agentpack:freshness")
    assert refreshed_rendered.index(marker) < refreshed_rendered.index("## Task")
    assert "fix auth" not in base_rendered.split(marker, 1)[0]
    assert "2026-05-13T00:00:00+00:00" not in base_rendered.split(marker, 1)[0]


def test_render_generic_uses_agent_neutral_stable_prefix():
    pack = ContextPack(
        task="fix auth",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
    )

    rendered = render_generic(pack)

    assert "# AgentPack Context" in rendered
    assert "## Instructions for Agent" in rendered
    assert "## Instructions for Claude" not in rendered


def test_context_pack_renders_agent_lessons():
    pack = ContextPack(
        task="Add CLI learning summaries",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        selected_files=[],
        omitted_relevant_files=[],
        receipts=[],
        changed_files=[],
        agent_lessons="- When editing CLI commands, update command docs and CLI tests.",
    )

    rendered = render_claude(pack)

    assert "## Agent Lessons From Prior Work" in rendered
    assert "update command docs" in rendered


def test_render_compresses_excluded_receipts_and_lists_token_consumers():
    selected = SelectedFile(
        path="src/big.py",
        language="python",
        score=100,
        include_mode="full",
        reasons=["modified"],
        content="print('x')\n" * 100,
    )
    pack = ContextPack(
        task="fix auth",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=["src/big.py"],
        selected_files=[selected],
        receipts=[
            Receipt(path="src/big.py", action="included", reason="modified"),
            *[
                Receipt(path=f"src/omitted_{i}.py", action="excluded", reason="budget exhausted")
                for i in range(12)
            ],
        ],
    )

    rendered = render_claude(pack)

    assert "## Largest Token Consumers" in rendered
    assert "| `src/big.py` | full |" in rendered
    assert "12 file(s) excluded because budget exhausted" in rendered
    assert "`src/omitted_9.py`" in rendered
    assert "`src/omitted_10.py`" not in rendered
    assert "+2 more" in rendered


def test_render_lists_omitted_but_relevant_files_before_file_context():
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
        omitted_relevant_files=[
            OmittedRelevantFile(
                path="api/routes/refund.py",
                score=210,
                reasons=["reverse dependency of payments/refund_service.py"],
                estimated_tokens=900,
                suggested_mode="summary",
                risk="high",
            )
        ],
    )

    rendered = render_claude(pack)

    assert "## Omitted But Relevant Files" in rendered
    assert rendered.index("## Omitted But Relevant Files") < rendered.index("## File Context")
    assert "`api/routes/refund.py`" in rendered
    assert "inspect caller" in rendered
    assert "Do not assume omitted relevant files are safe" in rendered


def test_pack_handoff_ready_when_pack_has_selected_files():
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=400,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[
            SelectedFile(
                path="src/refund.py",
                language="python",
                score=100,
                include_mode="summary",
                reasons=["task keyword match"],
            )
        ],
        receipts=[],
        freshness={
            "generated_at": "2026-05-13T00:00:00+00:00",
            "git_branch": "main",
            "git_sha": "abc123def456",
            "snapshot_root_hash": "root123",
        },
    )

    handoff = build_pack_handoff(pack)
    rendered = render_claude(pack)

    assert handoff["recommended_action"] == "ready_to_inspect_selected"
    assert handoff["schema_version"] == 2
    assert handoff["before_editing"]["verifier_hint"] == "inspect selected files before editing"
    assert "## Pack Sufficiency Receipt" in rendered
    assert "`ready_to_inspect_selected`" in rendered
    assert "Repo ref" in rendered
    assert "main @ abc123def456" in rendered
    assert "Pack snapshot" in rendered
    assert "2026-05-13T00:00:00+00:00 (root123)" in rendered


def test_pack_handoff_refreshes_stale_metadata():
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=400,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
        freshness_warnings=["packed task differs from .agentpack/task.md"],
    )

    handoff = build_pack_handoff(pack)
    rendered = render_claude(pack)

    assert handoff["recommended_action"] == "refresh_context"
    assert handoff["freshness"]["refresh_required"] is True
    assert "`refresh_context`" in rendered


def test_pack_handoff_deepens_under_budget_pressure():
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=980,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[
            SelectedFile(
                path="src/refund.py",
                language="python",
                score=100,
                include_mode="full",
                reasons=["task keyword match"],
            )
        ],
        receipts=[],
    )

    handoff = build_pack_handoff(pack)

    assert handoff["recommended_action"] == "deepen_pack"
    assert handoff["budget"]["pressure"] is True
    assert handoff["before_editing"]["verifier_hint"].startswith("increase the budget")


def test_pack_handoff_inspects_high_risk_omitted_files_first():
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=400,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[
            SelectedFile(
                path="src/refund.py",
                language="python",
                score=100,
                include_mode="summary",
                reasons=["task keyword match"],
            )
        ],
        receipts=[],
        omitted_relevant_files=[
            OmittedRelevantFile(
                path="tests/test_refund.py",
                score=200,
                reasons=["related test for src/refund.py"],
                estimated_tokens=900,
                suggested_mode="summary",
                risk="high",
            )
        ],
    )

    handoff = build_pack_handoff(pack)
    rendered = render_claude(pack)

    assert handoff["recommended_action"] == "inspect_omitted_first"
    assert handoff["omitted_relevant"]["high_risk"] == 1
    assert handoff["omitted_relevant"]["reason_counts"] == {"related_test": 1}
    assert "`inspect_omitted_first`" in rendered
    assert "`tests/test_refund.py`" in rendered
    assert "Omitted reason counts" in rendered


def test_pack_handoff_buckets_and_sorts_omitted_reason_counts():
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=400,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
        omitted_relevant_files=[
            OmittedRelevantFile(
                path="tests/test_refund.py",
                score=200,
                reasons=["related test for src/refund.py"],
                estimated_tokens=900,
                suggested_mode="summary",
                risk="high",
            ),
            OmittedRelevantFile(
                path="tests/test_payments.py",
                score=190,
                reasons=["test for src/payments.py"],
                estimated_tokens=800,
                suggested_mode="summary",
                risk="medium",
            ),
            OmittedRelevantFile(
                path="api/routes/refund.py",
                score=180,
                reasons=["caller of selected symbol `refund_user`"],
                estimated_tokens=700,
                suggested_mode="summary",
                risk="medium",
            ),
        ],
    )

    handoff = build_pack_handoff(pack)

    assert list(handoff["omitted_relevant"]["reason_counts"].items()) == [
        ("related_test", 2),
        ("caller_or_reverse_dependency", 1),
    ]


def test_pack_handoff_counts_excluded_receipts_by_bucket():
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=400,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[
            Receipt(path="src/noise.py", action="excluded", reason="marginal slot replaced by src/refund.py"),
            Receipt(path="src/noise2.py", action="excluded", reason="marginal slot replaced by src/payments.py"),
            Receipt(path="dist/app.js", action="excluded", reason="generated artifact"),
        ],
    )

    handoff = build_pack_handoff(pack)
    rendered = render_claude(pack)

    assert handoff["skipped_uncertain"]["excluded_files"] == 3
    assert list(handoff["skipped_uncertain"]["excluded_reason_counts"].items()) == [
        ("marginal_slot_replaced", 2),
        ("generated artifact", 1),
    ]
    assert "Excluded receipt counts" in rendered


def test_pack_handoff_preserves_high_risk_omitted_gate_after_budget_fit():
    omitted = [
        OmittedRelevantFile(
            path="api/routes/refund.py",
            score=240,
            reasons=["reverse dependency of src/refund.py"],
            estimated_tokens=900,
            suggested_mode="summary",
            risk="high",
        )
    ]
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=120,
        token_estimate=1000,
        raw_repo_tokens=2000,
        after_ignore_tokens=1800,
        estimated_savings_percent=50.0,
        changed_files=[],
        selected_files=[
            SelectedFile(
                path="src/refund.py",
                language="python",
                score=100,
                include_mode="summary",
                reasons=["task keyword match"],
                summary="refund helper" * 20,
            )
        ],
        receipts=[],
        omitted_relevant_files=list(omitted),
        pack_handoff_omitted_relevant_files=list(omitted),
    )

    class Adapter:
        def render(self, current_pack: ContextPack) -> str:
            return render_claude(current_pack)

    _fit_rendered_budget(pack, Adapter())
    handoff = build_pack_handoff(pack)
    rendered = render_claude(pack)

    assert pack.omitted_relevant_files == []
    assert handoff["recommended_action"] == "inspect_omitted_first"
    assert handoff["omitted_relevant"]["high_risk"] == 1
    assert handoff["omitted_relevant"]["top"] == ["api/routes/refund.py"]
    assert "`inspect_omitted_first`" in rendered
    assert "`api/routes/refund.py`" in rendered


def test_minimal_budget_render_preserves_compact_handoff_action():
    pack = ContextPack(
        task="fix refunds",
        agent="claude",
        mode="balanced",
        budget=500,
        token_estimate=490,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
    )

    rendered = render_claude(pack)

    assert "**Budget note:**" in rendered
    assert "`deepen_pack`" in rendered
    assert "## Pack Sufficiency Receipt" not in rendered


def test_rendered_token_estimate_includes_markdown_overhead():
    pack = ContextPack(
        task="fix auth",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=1,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
    )

    class Adapter:
        def render(self, current_pack: ContextPack) -> str:
            return f"# Context\nToken estimate: {current_pack.token_estimate}\n" + ("overhead " * 80)

    rendered_tokens = _settle_rendered_token_estimate(pack, Adapter())

    assert rendered_tokens > 1
    assert pack.token_estimate == rendered_tokens


def test_render_loud_stale_task_warning():
    pack = ContextPack(
        task="old task",
        agent="claude",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[],
        receipts=[],
        freshness={
            "generated_at": "2026-01-01T00:00:00+00:00",
            "agentpack_version": "0.1.0",
            "cwd": "/tmp/old",
            "git_root": "/tmp/repo",
            "worktree_path": "/tmp/repo",
            "git_branch": "main",
            "git_sha": "abc123",
        },
        freshness_warnings=[
            ".agentpack/task.md differs from the packed task; AgentPack-controlled context reads should auto-refresh."
        ],
    )

    rendered = render_claude(pack)

    assert "STALE TASK CONTEXT" in rendered
    freshness_block = rendered.split("<!-- agentpack:freshness", 1)[1].split("-->", 1)[0]
    assert "stale_task_context: true" in freshness_block
    assert "refresh_required: true" in freshness_block
    assert "Do not trust selected files until refreshed" in rendered
    assert "Stale Context Provenance" in rendered
    assert "Packed task" in rendered
    assert "/tmp/old" in rendered
    assert "direct `rg`, target-file reads" in rendered
    assert "agentpack_get_context()" in rendered
    assert "agentpack_pack_context()" in rendered
    assert "only if those MCP tools are visible" in rendered


def test_select_files_caps_unrelated_changed_files_when_many_dirty():
    files = [_fi(f"src/noise_{i}.py", tokens=20) for i in range(6)]
    files.append(_fi("src/auth.py", tokens=20))
    changed = {fi.path for fi in files}
    scored = [(fi, 100, ["modified"]) for fi in files[:6]]
    scored.append((files[-1], 140, ["modified", "symbol keyword match"]))

    selected, receipts = select_files(
        files=files,
        scored=scored,
        changed_paths=changed,
        summaries={},
        mode="balanced",
        budget=10000,
        max_file_tokens=1000,
    )

    selected_paths = {sf.path for sf in selected}
    capped = [r for r in receipts if r.reason == "unrelated changed-file safety cap"]
    assert "src/auth.py" in selected_paths
    assert len([path for path in selected_paths if "noise" in path]) == 3
    assert len(capped) == 3


def test_save_pack_metadata_persists_freshness(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    save_pack_metadata(
        tmp_path,
        context_path=".agentpack/context.md",
        snapshot_root_hash="root123",
        task="fix auth",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        freshness={
            "generated_at": "2026-05-13T00:00:00+00:00",
            "git_sha": "abc123",
            "task_source": "task.md",
            "changed_files_source": "git working tree",
        },
        freshness_warnings=["refresh"],
        selected_files=[
            {
                "path": "src/auth.py",
                "mode": "full",
                "score": 100,
                "why": "modified",
                "tokens": 10,
            }
        ],
        pack_handoff={"recommended_action": "ready_to_inspect_selected"},
    )
    meta = (tmp_path / ".agentpack" / "pack_metadata.json").read_text()
    assert '"git_sha": "abc123"' in meta
    assert '"task_hash":' in meta
    assert '"task_source": "task.md"' in meta
    assert '"freshness_warnings": [' in meta
    assert '"selected_files_meta": [' in meta
    assert '"path": "src/auth.py"' in meta
    assert '"pack_handoff": {' in meta
    assert '"recommended_action": "ready_to_inspect_selected"' in meta


def test_save_pack_metadata_persists_real_pack_handoff_v2_fields(tmp_path):
    (tmp_path / ".agentpack").mkdir()
    pack = ContextPack(
        task="fix auth",
        agent="generic",
        mode="balanced",
        budget=1000,
        token_estimate=100,
        raw_repo_tokens=1000,
        after_ignore_tokens=800,
        estimated_savings_percent=90.0,
        changed_files=[],
        selected_files=[
            SelectedFile(
                path="src/auth.py",
                language="python",
                score=100,
                include_mode="full",
                reasons=["modified"],
            )
        ],
        receipts=[],
        freshness={
            "generated_at": "2026-05-13T00:00:00+00:00",
            "git_branch": "main",
            "git_sha": "abc123def456",
            "snapshot_root_hash": "root123",
        },
    )

    save_pack_metadata(
        tmp_path,
        context_path=".agentpack/context.md",
        snapshot_root_hash="root123",
        task=pack.task,
        agent=pack.agent,
        mode=pack.mode,
        budget=pack.budget,
        token_estimate=pack.token_estimate,
        freshness=pack.freshness,
        selected_files=[],
        pack_handoff=build_pack_handoff(pack),
    )
    meta = json.loads((tmp_path / ".agentpack" / "pack_metadata.json").read_text())

    assert meta["pack_handoff"]["schema_version"] == 2
    assert meta["pack_handoff"]["repo_ref"] == {"branch": "main", "sha": "abc123def456"}
    assert meta["pack_handoff"]["pack_snapshot"] == {
        "generated_at": "2026-05-13T00:00:00+00:00",
        "snapshot_hash": "root123",
    }


def test_generic_task_tightens_summary_floor_and_cap():
    cfg = DEFAULT_CONFIG
    assert _summary_score_floor(cfg, 0.6) > cfg.context.min_summary_score
    assert _summary_cap_for_mode(cfg, "balanced", 0.6) < cfg.context.max_summary_files_balanced
