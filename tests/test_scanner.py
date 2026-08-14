import pathspec
from typer.testing import CliRunner

from agentpack.application.pack_service import AdapterRegistry
from agentpack.cli import app
from agentpack.core.config import load_config
from agentpack.core.scanner import scan, scan_incremental, file_hash
from agentpack.core.snapshot import build_snapshot
from agentpack.core.ignore import DEFAULT_AGENTIGNORE


def _spec() -> pathspec.PathSpec:
    return pathspec.PathSpec.from_lines("gitignore", DEFAULT_AGENTIGNORE.splitlines())


def test_scan_excludes_ignored(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("module.exports = {}")

    spec = _spec()
    result = scan(tmp_path, spec)
    packable_paths = {f.path for f in result.packable}

    assert "src/main.py" in packable_paths
    assert not any("node_modules" in p for p in packable_paths)


def test_scan_excludes_android_build_artifacts(tmp_path):
    source = tmp_path / "android" / "app" / "src" / "main.cpp"
    artifact = tmp_path / "android" / "app" / ".cxx" / "Debug" / "noise.cpp"
    source.parent.mkdir(parents=True)
    artifact.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }", encoding="utf-8")
    artifact.write_text("generated build output", encoding="utf-8")

    result = scan(tmp_path, _spec())
    packable_paths = {file_info.path for file_info in result.packable}

    assert "android/app/src/main.cpp" in packable_paths
    assert "android/app/.cxx/Debug/noise.cpp" not in packable_paths


def test_scan_excludes_agentpack_antigravity_skill(tmp_path):
    (tmp_path / ".agent" / "skills" / "agentpack").mkdir(parents=True)
    (tmp_path / ".agent" / "skills" / "agentpack" / "SKILL.md").write_text("# generated context")
    (tmp_path / ".agent" / "skills" / "custom").mkdir(parents=True)
    (tmp_path / ".agent" / "skills" / "custom" / "SKILL.md").write_text("# custom skill")

    spec = _spec()
    result = scan(
        tmp_path,
        spec,
        always_skip_paths={".agent/skills/agentpack/SKILL.md"},
    )
    packable_paths = {f.path for f in result.packable}

    assert ".agent/skills/agentpack/SKILL.md" not in packable_paths
    assert ".agent/skills/custom/SKILL.md" in packable_paths


def test_generated_paths_include_antigravity_citation_manifest(tmp_path):
    (tmp_path / ".agentpack").mkdir()

    cfg = load_config(tmp_path)
    paths = AdapterRegistry.generated_output_paths(tmp_path, cfg)

    assert ".agent/skills/agentpack/SKILL.md" in paths
    assert ".agent/skills/agentpack/citations.json" in paths


def test_incremental_scan_drops_previous_generated_paths(tmp_path):
    generated = tmp_path / ".agent" / "skills" / "agentpack" / "citations.json"
    generated.parent.mkdir(parents=True)
    generated.write_text("{}\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")

    previous = build_snapshot(scan(tmp_path, _spec()).packable)
    result = scan_incremental(
        tmp_path,
        _spec(),
        changed_paths=set(),
        previous_snapshot=previous,
        always_skip_paths={".agent/skills/agentpack/citations.json"},
    )
    packable_paths = {f.path for f in result.packable}

    assert ".agent/skills/agentpack/citations.json" not in packable_paths
    assert "app.py" in packable_paths


def test_scan_marks_ignored(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("x")

    spec = _spec()
    result = scan(tmp_path, spec)
    assert any("node_modules" in f.path for f in result.ignored)


def test_scan_result_all_files(tmp_path):
    (tmp_path / "main.py").write_text("x = 1")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("x")

    spec = _spec()
    result = scan(tmp_path, spec)
    all_paths = {f.path for f in result.all_files}
    assert "main.py" in all_paths


def test_hash_changes_with_content(tmp_path):
    f = tmp_path / "file.py"
    f.write_text("version 1")
    h1 = file_hash(f)
    f.write_text("version 2")
    h2 = file_hash(f)
    assert h1 != h2


def test_hash_stable(tmp_path):
    f = tmp_path / "file.py"
    f.write_text("stable content")
    assert file_hash(f) == file_hash(f)


def test_token_estimation(tmp_path):
    f = tmp_path / "big.py"
    content = "x" * 400
    f.write_text(content)
    spec = _spec()
    result = scan(tmp_path, spec)
    fi = next(x for x in result.packable if x.path == "big.py")
    assert fi.estimated_tokens > 0
    assert fi.estimated_tokens <= 400


def test_scan_cli_largest_and_ignored_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "small.py").write_text("x = 1")
    (tmp_path / "src" / "large.py").write_text("x = 1\n" * 100)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("module.exports = {}")

    result = CliRunner().invoke(app, ["scan", "--largest", "1", "--ignored-summary"])

    assert result.exit_code == 0, result.output
    assert "Largest Files" in result.output
    assert "src/large.py" in result.output
    assert "src/small.py" not in result.output
    assert "Ignored / Binary Summary" in result.output
    assert "node_modules" in result.output


# ---------------------------------------------------------------------------
# include_globs / exclude_globs
# ---------------------------------------------------------------------------

def test_include_globs_restricts_packable(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x = 1")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "util.py").write_text("y = 2")

    spec = _spec()
    result = scan(tmp_path, spec, include_globs=["app/**"])
    packable_paths = {f.path for f in result.packable}
    assert "app/main.py" in packable_paths
    assert "other/util.py" not in packable_paths
    # excluded by include_globs lands in ignored
    ignored_paths = {f.path for f in result.ignored}
    assert "other/util.py" in ignored_paths


def test_exclude_globs_removes_files(tmp_path):
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "001.sql").write_text("CREATE TABLE x (id int);")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "views.py").write_text("pass")

    spec = _spec()
    result = scan(tmp_path, spec, exclude_globs=["migrations/**"])
    packable_paths = {f.path for f in result.packable}
    assert "app/views.py" in packable_paths
    assert "migrations/001.sql" not in packable_paths


def test_include_and_exclude_globs_combined(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x = 1")
    (tmp_path / "app" / "generated.py").write_text("# generated")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "util.py").write_text("y = 2")

    spec = _spec()
    result = scan(tmp_path, spec, include_globs=["app/**"], exclude_globs=["app/generated*"])
    packable_paths = {f.path for f in result.packable}
    assert "app/main.py" in packable_paths
    assert "app/generated.py" not in packable_paths
    assert "other/util.py" not in packable_paths


def test_empty_globs_include_all(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 1")
    (tmp_path / "src" / "b.py").write_text("b = 2")

    spec = _spec()
    result = scan(tmp_path, spec, include_globs=[], exclude_globs=[])
    packable_paths = {f.path for f in result.packable}
    assert "src/a.py" in packable_paths
    assert "src/b.py" in packable_paths


def test_incremental_scan_reuses_snapshot_and_rehashes_changed_paths(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 1")
    (tmp_path / "src" / "b.py").write_text("b = 1")
    (tmp_path / "src" / "delete_me.py").write_text("gone = False")

    spec = _spec()
    full = scan(tmp_path, spec)
    previous = build_snapshot(full.packable)
    (tmp_path / "src" / "a.py").write_text("a = 2")
    (tmp_path / "src" / "c.py").write_text("c = 1")
    (tmp_path / "src" / "delete_me.py").unlink()

    result = scan_incremental(
        tmp_path,
        spec,
        changed_paths={"src/a.py", "src/c.py", "src/delete_me.py"},
        previous_snapshot=previous,
    )

    by_path = {fi.path: fi for fi in result.packable}
    assert result.scan_mode == "incremental"
    assert result.reused_count == 1
    assert result.rehashed_count == 2
    assert set(by_path) == {"src/a.py", "src/b.py", "src/c.py"}
    assert by_path["src/b.py"].content is None
    assert by_path["src/a.py"].content == "a = 2"
    assert by_path["src/a.py"].hash != previous["files"]["src/a.py"]["hash"]


def test_incremental_scan_classifies_new_ignored_and_binary_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 1")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("ignored")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00")

    spec = _spec()
    previous = build_snapshot(scan(tmp_path, spec).packable)

    result = scan_incremental(
        tmp_path,
        spec,
        changed_paths={"node_modules/pkg.js", "image.png"},
        previous_snapshot=previous,
    )

    assert "src/a.py" in {fi.path for fi in result.packable}
    assert "node_modules/pkg.js" in {fi.path for fi in result.ignored}
    assert "image.png" in {fi.path for fi in result.binary}
