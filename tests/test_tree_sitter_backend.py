"""Tests for the optional tree-sitter backend.

Skipped when the `[tree-sitter]` extra is not installed. Under normal CI
(without the extra), this ensures no regression on the default install.
"""
import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from agentpack.analysis.symbols import extract_symbols
from agentpack.analysis.tree_sitter_backend import (
    extract_imports_ts,
    extract_symbols_ts,
    is_available,
)
from agentpack.analysis.dependency_graph import build
from agentpack.core.models import FileInfo


def test_backend_available():
    assert is_available() is True


# ---------------------------------------------------------------------------
# Java symbols
# ---------------------------------------------------------------------------

def test_java_class_and_method(tmp_path):
    f = tmp_path / "Greeter.java"
    f.write_text(
        "package com.example;\n"
        "import java.util.List;\n"
        "\n"
        "public class Greeter {\n"
        "    public Greeter(String name) { }\n"
        "    public String greet() { return \"hi\"; }\n"
        "}\n"
    )
    syms = extract_symbols_ts(f, "java")
    names = {(s.name, s.kind) for s in syms}
    assert ("Greeter", "class") in names
    assert ("Greeter.Greeter", "method") in names  # constructor qualified
    assert ("Greeter.greet", "method") in names


def test_java_interface_and_enum(tmp_path):
    f = tmp_path / "Types.java"
    f.write_text(
        "interface Formatter { String format(String s); }\n"
        "enum Color { RED, GREEN, BLUE }\n"
    )
    syms = extract_symbols_ts(f, "java")
    names = {(s.name, s.kind) for s in syms}
    assert ("Formatter", "class") in names
    assert ("Color", "class") in names
    assert ("Formatter.format", "method") in names


def test_java_via_public_dispatch(tmp_path):
    """The public `extract_symbols` should route Java through tree-sitter."""
    f = tmp_path / "A.java"
    f.write_text("class A { void run() {} }\n")
    syms = extract_symbols(f, "java")
    names = [s.name for s in syms]
    assert "A" in names
    assert "A.run" in names


# ---------------------------------------------------------------------------
# Ruby symbols
# ---------------------------------------------------------------------------

def test_ruby_class_method_and_module(tmp_path):
    f = tmp_path / "user.rb"
    f.write_text(
        "module MyApp\n"
        "  class User\n"
        "    def initialize(name)\n"
        "      @name = name\n"
        "    end\n"
        "    def greet\n"
        "      \"hi #{@name}\"\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    syms = extract_symbols_ts(f, "ruby")
    names = {(s.name, s.kind) for s in syms}
    assert ("MyApp", "class") in names  # modules classified as class
    # Nested classes get their full scope path (MyApp::User), not just the
    # bare class name — keeps same-named classes in different modules
    # distinct in the ranker's keyword index.
    assert ("MyApp::User", "class") in names
    assert ("MyApp::User.initialize", "method") in names
    assert ("MyApp::User.greet", "method") in names


def test_ruby_singleton_method(tmp_path):
    f = tmp_path / "counter.rb"
    f.write_text(
        "class Counter\n"
        "  def self.count\n"
        "    42\n"
        "  end\n"
        "end\n"
    )
    syms = extract_symbols_ts(f, "ruby")
    names = {(s.name, s.kind) for s in syms}
    assert ("Counter.count", "method") in names


def test_ruby_top_level_function(tmp_path):
    f = tmp_path / "helpers.rb"
    f.write_text("def helper\n  :ok\nend\n")
    syms = extract_symbols_ts(f, "ruby")
    names = {(s.name, s.kind) for s in syms}
    assert ("helper", "function") in names


def test_ruby_via_public_dispatch(tmp_path):
    f = tmp_path / "a.rb"
    f.write_text("class A\n  def run; end\nend\n")
    syms = extract_symbols(f, "ruby")
    names = [s.name for s in syms]
    assert "A" in names
    assert "A.run" in names


# ---------------------------------------------------------------------------
# PHP symbols
# ---------------------------------------------------------------------------

def test_php_class_and_method(tmp_path):
    f = tmp_path / "Controller.php"
    f.write_text(
        "<?php\n"
        "namespace App\\Http;\n"
        "class Controller {\n"
        "    public function index(): string { return \"hi\"; }\n"
        "    private function build() { return 1; }\n"
        "}\n"
    )
    syms = extract_symbols_ts(f, "php")
    names = {(s.name, s.kind) for s in syms}
    assert ("Controller", "class") in names
    assert ("Controller.index", "method") in names
    assert ("Controller.build", "method") in names


def test_php_interface_trait_and_function(tmp_path):
    f = tmp_path / "Types.php"
    f.write_text(
        "<?php\n"
        "interface Renderable { public function render(): string; }\n"
        "trait Loggable { public function log() { } }\n"
        "function helper(): int { return 1; }\n"
    )
    syms = extract_symbols_ts(f, "php")
    names = {(s.name, s.kind) for s in syms}
    assert ("Renderable", "class") in names
    assert ("Loggable", "class") in names
    assert ("helper", "function") in names


def test_php_via_public_dispatch(tmp_path):
    f = tmp_path / "a.php"
    f.write_text("<?php\nclass A { public function run() {} }\n")
    syms = extract_symbols(f, "php")
    names = [s.name for s in syms]
    assert "A" in names
    assert "A.run" in names


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------

def test_ruby_imports(tmp_path):
    f = tmp_path / "app.rb"
    f.write_text(
        "require \"json\"\n"
        "require_relative \"./user\"\n"
    )
    imports = extract_imports_ts(f, None, "ruby")
    assert "json" in imports
    assert "./user" in imports


def test_php_use_imports(tmp_path):
    f = tmp_path / "A.php"
    f.write_text(
        "<?php\n"
        "namespace App;\n"
        "use App\\Models\\User;\n"
        "use Illuminate\\Support\\Str;\n"
        "class A {}\n"
    )
    imports = extract_imports_ts(f, None, "php")
    assert "App\\Models\\User" in imports
    assert "Illuminate\\Support\\Str" in imports


def test_php_require_family_double_quoted(tmp_path):
    """PHP's grammar emits `encapsed_string` for double-quoted literals
    (even with no interpolation) and `string` for single-quoted — both must
    be captured or the common double-quoted style is silently dropped."""
    f = tmp_path / "bootstrap.php"
    f.write_text(
        "<?php\n"
        'require_once "vendor/autoload.php";\n'
        'require "config.php";\n'
        "include_once 'legacy.php';\n"
    )
    imports = extract_imports_ts(f, None, "php")
    assert "vendor/autoload.php" in imports
    assert "config.php" in imports
    assert "legacy.php" in imports


def test_ruby_nested_module_scope_qualification(tmp_path):
    """Nested class/module scope is fully qualified with `::`, not just
    the nearest enclosing name — MyApp::User.greet, not User.greet."""
    f = tmp_path / "nested.rb"
    f.write_text(
        "module MyApp\n"
        "  class User\n"
        "    def greet\n"
        "      :ok\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    syms = extract_symbols_ts(f, "ruby")
    names = {(s.name, s.kind) for s in syms}
    assert ("MyApp::User", "class") in names
    assert ("MyApp::User.greet", "method") in names
    # bare names should not appear as separate/duplicate entries
    assert ("User", "class") not in names
    assert ("User.greet", "method") not in names


# ---------------------------------------------------------------------------
# Dependency graph — the actual downstream consumer
# ---------------------------------------------------------------------------

def _fi(root, rel, lang):
    p = root / rel
    return FileInfo(
        path=rel, abs_path=str(p), language=lang, hash="h",
        size_bytes=p.stat().st_size, estimated_tokens=50,
    )


def test_ruby_relative_import_resolves_to_repo_file(tmp_path):
    """require_relative should build a real edge in the dep graph."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "user.rb").write_text("class User; end\n")
    (tmp_path / "lib" / "app.rb").write_text(
        "require_relative \"./user\"\nclass App; end\n"
    )
    files = [
        _fi(tmp_path, "lib/user.rb", "ruby"),
        _fi(tmp_path, "lib/app.rb", "ruby"),
    ]
    graph = build(files, tmp_path)
    assert "lib/user.rb" in graph.nodes["lib/app.rb"].imports
    assert "lib/app.rb" in graph.nodes["lib/user.rb"].imported_by


def test_php_use_import_kept_as_raw_edge(tmp_path):
    """PHP `use` statements are namespaced; kept as raw strings like Java."""
    (tmp_path / "A.php").write_text(
        "<?php\nnamespace App;\nuse App\\Models\\User;\nclass A {}\n"
    )
    files = [_fi(tmp_path, "A.php", "php")]
    graph = build(files, tmp_path)
    imports = graph.nodes["A.php"].imports
    assert "App\\Models\\User" in imports


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_returns_empty_on_unreadable_file(tmp_path):
    f = tmp_path / "missing.rb"  # not created
    assert extract_symbols_ts(f, "ruby") == []
    assert extract_imports_ts(f, None, "ruby") == []


def test_returns_empty_on_unsupported_language(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello")
    assert extract_symbols_ts(f, "unsupported") == []
    assert extract_imports_ts(f, None, "unsupported") == []


def test_partially_invalid_source_does_not_crash(tmp_path):
    """Tree-sitter is error-tolerant; extraction should return whatever it can."""
    f = tmp_path / "broken.rb"
    f.write_text("class Ok\n  def fine; end\nend\n\nclass Bad !!! garbage\n  def x\n")
    syms = extract_symbols_ts(f, "ruby")
    assert any(s.name == "Ok" for s in syms)
