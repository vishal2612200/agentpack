import warnings

from agentpack.analysis.naming_signals import (
    classify_public_name,
    collect_public_name_candidates,
)
from agentpack.analysis.symbols import extract_python_symbols, extract_js_symbols, extract_go_symbols, extract_rust_symbols, extract_symbols
from agentpack.core.node_identity import symbol_node_id, symbol_node_ref


def test_python_function(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def hello(x, y):\n    '''Greet.'''\n    return x + y\n")
    syms = extract_python_symbols(f)
    names = [s.name for s in syms]
    assert "hello" in names
    fn = next(s for s in syms if s.name == "hello")
    assert fn.kind == "function"
    assert "hello" in fn.signature
    node_ref = symbol_node_ref("src/mod.py", fn, source_hash="abc123")
    assert node_ref["node_id"].startswith("node:")
    assert node_ref["path"] == "src/mod.py"
    assert node_ref["symbol"] == "hello"
    assert node_ref["source_hash"] == "abc123"
    assert symbol_node_id("src/mod.py", fn, source_hash="abc123") == node_ref["node_id"]


def test_python_class_and_method(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "class Foo:\n"
        "    def bar(self):\n"
        "        pass\n"
    )
    syms = extract_python_symbols(f)
    kinds = {s.kind for s in syms}
    assert "class" in kinds
    assert "method" in kinds


def test_python_invalid_syntax(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def (broken:")
    assert extract_python_symbols(f) == []


def test_python_invalid_escape_does_not_warn(tmp_path):
    f = tmp_path / "regex.py"
    f.write_text('PATTERN = "' + "\\(" + '"\n\ndef hello():\n    pass\n')

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", SyntaxWarning)
        syms = extract_python_symbols(f)

    assert "hello" in [s.name for s in syms]
    assert not [warning for warning in captured if issubclass(warning.category, SyntaxWarning)]


def test_js_function(tmp_path):
    f = tmp_path / "mod.js"
    f.write_text("export function doThing(x) { return x; }\n")
    syms = extract_js_symbols(f)
    names = [s.name for s in syms]
    assert "doThing" in names


def test_js_class(tmp_path):
    f = tmp_path / "mod.ts"
    f.write_text("export class AuthService {\n  login() {}\n}\n")
    syms = extract_js_symbols(f)
    names = [s.name for s in syms]
    assert "AuthService" in names


def test_js_arrow_function_detected(tmp_path):
    f = tmp_path / "mod.js"
    f.write_text("const handleClick = (e) => { console.log(e); }\n")
    syms = extract_js_symbols(f)
    names = [s.name for s in syms]
    assert "handleClick" in names


def test_js_arrow_function_no_params(tmp_path):
    f = tmp_path / "mod.js"
    f.write_text("const init = () => { return 42; }\n")
    syms = extract_js_symbols(f)
    names = [s.name for s in syms]
    assert "init" in names


def test_js_non_arrow_assignment_not_extracted(tmp_path):
    # This is NOT an arrow function — should not be extracted as a function symbol
    f = tmp_path / "mod.js"
    f.write_text("const result = (a + b) * c;\n")
    syms = extract_js_symbols(f)
    names = [s.name for s in syms]
    assert "result" not in names


def test_js_exported_typed_const_detected_as_variable(tmp_path):
    f = tmp_path / "mod.ts"
    f.write_text(
        "import { parseAst as _parseAst } from 'rolldown/parseAst'\n"
        "export const parseAst: typeof _parseAst = _parseAst\n"
    )

    syms = extract_js_symbols(f)
    match = next(s for s in syms if s.name == "parseAst")

    assert match.kind == "variable"
    assert "parseAst" in (match.signature or "")


def test_js_async_arrow_function(tmp_path):
    f = tmp_path / "mod.ts"
    f.write_text("export const fetchUser = async (id: string) => {\n  return db.find(id);\n};\n")
    syms = extract_js_symbols(f)
    names = [s.name for s in syms]
    assert "fetchUser" in names


def test_go_function_method_and_type_symbols(tmp_path):
    f = tmp_path / "server.go"
    f.write_text(
        "package server\n\n"
        "type Handler struct {}\n\n"
        "func NewHandler() *Handler { return &Handler{} }\n\n"
        "func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {\n"
        "    w.WriteHeader(200)\n"
        "}\n",
    )

    syms = extract_go_symbols(f)
    names = [s.name for s in syms]

    assert "Handler" in names
    assert "NewHandler" in names
    assert "Handler.ServeHTTP" in names
    assert next(s for s in syms if s.name == "Handler").kind == "class"
    assert next(s for s in syms if s.name == "Handler.ServeHTTP").kind == "method"
    assert [s.name for s in extract_symbols(f, "go")] == names


def test_rust_struct_function_and_impl_method_symbols(tmp_path):
    f = tmp_path / "config.rs"
    f.write_text(
        "use std::collections::HashMap;\n\n"
        "pub struct Config {\n"
        "    name: String,\n"
        "}\n\n"
        "struct Meters(f64);\n\n"
        "struct Unit;\n\n"
        "impl Config {\n"
        "    pub fn new(name: &str) -> Self {\n"
        "        Config { name: name.to_string() }\n"
        "    }\n\n"
        "    fn is_named(&self) -> bool {\n"
        "        !self.name.is_empty()\n"
        "    }\n"
        "}\n\n"
        "pub async fn build(name: &str) -> Config {\n"
        "    Config::new(name)\n"
        "}\n\n"
        "const fn answer() -> u32 { 42 }\n",
    )

    syms = extract_rust_symbols(f)
    by_name = {s.name: s for s in syms}

    # class-like constructs: named, tuple, and unit structs
    assert by_name["Config"].kind == "class"
    assert by_name["Config"].summary == "Rust struct"
    assert by_name["Meters"].kind == "class"
    assert by_name["Unit"].kind == "class"
    # tuple/unit structs are single-line declarations
    assert by_name["Meters"].start_line == by_name["Meters"].end_line
    assert by_name["Unit"].start_line == by_name["Unit"].end_line

    # free functions (incl. async / const fn) are functions, not methods
    assert by_name["build"].kind == "function"
    assert by_name["answer"].kind == "function"

    # inherent impl methods are qualified with the owning type
    assert by_name["Config.new"].kind == "method"
    assert by_name["Config.is_named"].kind == "method"
    assert "new" not in by_name  # not surfaced as a bare free function

    # the braced struct body spans multiple lines
    assert by_name["Config"].end_line > by_name["Config"].start_line
    assert [s.name for s in extract_symbols(f, "rust")] == [s.name for s in syms]


def test_rust_enum_trait_and_impl_trait_for_type(tmp_path):
    f = tmp_path / "greet.rs"
    f.write_text(
        "pub enum Mode {\n"
        "    Fast,\n"
        "    Slow(u32),\n"
        "}\n\n"
        "pub trait Greet {\n"
        "    fn hello(&self) -> String;\n"
        "    fn shout(&self) -> String {\n"
        "        self.hello().to_uppercase()\n"
        "    }\n"
        "}\n\n"
        "struct Server;\n\n"
        "impl<T> Greet for Server {\n"
        "    fn hello(&self) -> String {\n"
        "        String::from(\"hi\")\n"
        "    }\n"
        "}\n",
    )

    syms = extract_rust_symbols(f)
    by_name = {s.name: s for s in syms}

    assert by_name["Mode"].kind == "class"
    assert by_name["Mode"].summary == "Rust enum"
    assert by_name["Greet"].kind == "class"
    assert by_name["Greet"].summary == "Rust trait"

    # both a declared method and a defaulted method are owned by the trait
    assert by_name["Greet.hello"].kind == "method"
    assert by_name["Greet.shout"].kind == "method"
    # the bodyless trait method declaration ends on its own line
    assert by_name["Greet.hello"].start_line == by_name["Greet.hello"].end_line

    # `impl Trait for Type` attributes the method to the concrete type
    assert by_name["Server.hello"].kind == "method"
    assert "Greet.hello" != "Server.hello"


def test_rust_invalid_or_empty_source(tmp_path):
    f = tmp_path / "empty.rs"
    f.write_text("// just a comment\nlet x = 5;\n")
    assert extract_rust_symbols(f) == []


def test_classify_public_name_domain_revealing():
    result = classify_public_name("verify_otp")
    assert result.label == "domain_revealing"
    assert "verify" in result.keywords
    assert "otp" in result.keywords


def test_classify_public_name_generic_unqualified():
    result = classify_public_name("handle")
    assert result.label == "generic"


def test_classify_public_name_qualified_generic_stem_not_generic():
    result = classify_public_name("WebhookHandler")
    assert result.label != "generic"


def test_collect_public_name_candidates_python_public_only(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "class SessionManager:\n"
        "    def issue_token(self):\n"
        "        pass\n"
        "    def _helper(self):\n"
        "        pass\n"
        "def verify_otp(code):\n"
        "    return code\n"
    )
    candidates = collect_public_name_candidates(f, "python")
    assert "SessionManager" in candidates
    assert "SessionManager.issue_token" in candidates
    assert "verify_otp" in candidates
    assert "SessionManager._helper" not in candidates


def test_collect_public_name_candidates_js_exports_only(tmp_path):
    f = tmp_path / "mod.ts"
    f.write_text(
        "export function verifyOtp() { return true; }\n"
        "const hiddenHelper = () => false;\n"
        "export class StripeWebhookHandler {}\n"
    )
    candidates = collect_public_name_candidates(f, "typescript")
    assert "verifyOtp" in candidates
    assert "StripeWebhookHandler" in candidates
    assert "hiddenHelper" not in candidates
