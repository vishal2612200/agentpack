from __future__ import annotations

import re
from pathlib import Path

from agentpack.core.models import FileSummary, SUMMARY_SCHEMA_VERSION
from agentpack.analysis.role_inference import (
    extract_code_intelligence,
    extract_failure_hints,
    extract_side_effects,
    infer_role_domain,
)
from agentpack.analysis.naming_signals import collect_public_name_candidates, summarize_naming_signals
from agentpack.analysis.symbols import extract_python_symbols, extract_js_symbols, extract_go_symbols, extract_rust_symbols
from agentpack.analysis.python_imports import extract_imports as py_imports
from agentpack.analysis.js_ts_imports import extract_imports as js_imports
from agentpack.analysis.go_imports import extract_imports as go_imports
from agentpack.analysis.rust_imports import extract_imports as rust_imports


def summarize(path: str, abs_path: Path, language: str | None, file_hash: str) -> FileSummary:
    if language == "python":
        return _python_summary(path, abs_path, file_hash)
    if language in ("javascript", "typescript"):
        return _js_summary(path, abs_path, language, file_hash)
    if language == "go":
        return _go_summary(path, abs_path, file_hash)
    if language == "rust":
        return _rust_summary(path, abs_path, file_hash)
    return _generic_summary(path, abs_path, language, file_hash)


def _python_summary(path: str, abs_path: Path, file_hash: str) -> FileSummary:
    imports = py_imports(abs_path)
    symbols = extract_python_symbols(abs_path)
    text = _read_sample(abs_path)

    intel = extract_code_intelligence(path=path, language="python", text=text, symbols=symbols)
    effects = extract_side_effects(path=path, language="python", text=text, imports=imports)
    role_info = infer_role_domain(
        path=path,
        language="python",
        symbols=symbols,
        imports=imports,
        text=text,
        entrypoints=intel.entrypoints,
        external_systems=effects.external_systems,
    )
    role = role_info.role or _infer_responsibility(path, intel.defines)
    failure_hints = extract_failure_hints(text)
    public_api = _dedupe([*_infer_public_api(path, intel.defines, text), *intel.entrypoints])[:12]
    public_names = collect_public_name_candidates(abs_path, "python")
    naming_signals, naming_keywords = summarize_naming_signals(path, public_names, effects.reads_env)
    test_hints = _infer_test_hints(path, role, intel.defines)
    summary_text = _render_summary(
        language="Python",
        domain=role_info.domain,
        role=role,
        entrypoints=intel.entrypoints,
        defines=intel.defines,
        calls=intel.calls,
        imports=imports,
        external_systems=effects.external_systems,
        reads_env=effects.reads_env,
        side_effects=effects.side_effects,
        ranking_keywords=role_info.ranking_keywords,
        failure_hints=failure_hints,
    )

    return FileSummary(
        path=path,
        hash=file_hash,
        language="python",
        provider="offline",
        schema_version=SUMMARY_SCHEMA_VERSION,
        summary=summary_text,
        imports=imports[:20],
        symbols=symbols,
        domain=role_info.domain,
        role=role,
        entrypoints=intel.entrypoints,
        defines=intel.defines,
        calls=intel.calls,
        reads_env=effects.reads_env,
        reads_files=effects.reads_files,
        writes_files=effects.writes_files,
        external_systems=effects.external_systems,
        side_effects=effects.side_effects,
        failure_hints=failure_hints,
        ranking_keywords=role_info.ranking_keywords,
        related_hints=role_info.reasons,
        public_api=public_api,
        naming_signals=naming_signals,
        naming_keywords=naming_keywords,
        error_paths=failure_hints,
        test_hints=test_hints,
    )


def _js_summary(path: str, abs_path: Path, language: str, file_hash: str) -> FileSummary:
    imports = js_imports(abs_path)
    symbols = extract_js_symbols(abs_path)
    text = _read_sample(abs_path)

    intel = extract_code_intelligence(path=path, language=language, text=text, symbols=symbols)
    effects = extract_side_effects(path=path, language=language, text=text, imports=imports)
    role_info = infer_role_domain(
        path=path,
        language=language,
        symbols=symbols,
        imports=imports,
        text=text,
        entrypoints=intel.entrypoints,
        external_systems=effects.external_systems,
    )
    role = role_info.role or _infer_responsibility(path, intel.defines)
    failure_hints = extract_failure_hints(text)
    public_api = _dedupe([*_infer_public_api(path, intel.defines, text), *intel.entrypoints])[:12]
    public_names = collect_public_name_candidates(abs_path, language)
    naming_signals, naming_keywords = summarize_naming_signals(path, public_names, effects.reads_env)
    test_hints = _infer_test_hints(path, role, intel.defines)
    summary_text = _render_summary(
        language=language.capitalize(),
        domain=role_info.domain,
        role=role,
        entrypoints=intel.entrypoints,
        defines=intel.defines,
        calls=intel.calls,
        imports=imports,
        external_systems=effects.external_systems,
        reads_env=effects.reads_env,
        side_effects=effects.side_effects,
        ranking_keywords=role_info.ranking_keywords,
        failure_hints=failure_hints,
    )

    return FileSummary(
        path=path,
        hash=file_hash,
        language=language,
        provider="offline",
        schema_version=SUMMARY_SCHEMA_VERSION,
        summary=summary_text,
        imports=imports[:20],
        symbols=symbols,
        domain=role_info.domain,
        role=role,
        entrypoints=intel.entrypoints,
        defines=intel.defines,
        calls=intel.calls,
        reads_env=effects.reads_env,
        reads_files=effects.reads_files,
        writes_files=effects.writes_files,
        external_systems=effects.external_systems,
        side_effects=effects.side_effects,
        failure_hints=failure_hints,
        ranking_keywords=role_info.ranking_keywords,
        related_hints=role_info.reasons,
        public_api=public_api,
        naming_signals=naming_signals,
        naming_keywords=naming_keywords,
        error_paths=failure_hints,
        test_hints=test_hints,
    )


def _go_summary(path: str, abs_path: Path, file_hash: str) -> FileSummary:
    text = _read_sample(abs_path)
    imports = go_imports(abs_path, text=text)
    symbols = extract_go_symbols(abs_path)
    intel = extract_code_intelligence(path=path, language="go", text=text, symbols=symbols)
    effects = extract_side_effects(path=path, language="go", text=text, imports=imports)
    role_info = infer_role_domain(
        path=path,
        language="go",
        symbols=symbols,
        imports=imports,
        text=text,
        entrypoints=intel.entrypoints,
        external_systems=effects.external_systems,
    )
    role = role_info.role or _infer_responsibility(path, intel.defines)
    failure_hints = extract_failure_hints(text)
    public_api = _dedupe([*_infer_public_api(path, intel.defines, text), *intel.entrypoints])[:12]
    test_hints = _infer_test_hints(path, role, intel.defines)

    return FileSummary(
        path=path,
        hash=file_hash,
        language="go",
        provider="offline",
        schema_version=SUMMARY_SCHEMA_VERSION,
        summary=_render_summary(
            language="Go",
            domain=role_info.domain,
            role=role,
            entrypoints=intel.entrypoints,
            defines=intel.defines,
            calls=intel.calls,
            imports=imports,
            external_systems=effects.external_systems,
            reads_env=effects.reads_env,
            side_effects=effects.side_effects,
            ranking_keywords=role_info.ranking_keywords,
            failure_hints=failure_hints,
        ),
        imports=imports[:20],
        symbols=symbols,
        domain=role_info.domain,
        role=role,
        entrypoints=intel.entrypoints,
        defines=intel.defines,
        calls=intel.calls,
        reads_env=effects.reads_env,
        reads_files=effects.reads_files,
        writes_files=effects.writes_files,
        external_systems=effects.external_systems,
        side_effects=effects.side_effects,
        failure_hints=failure_hints,
        ranking_keywords=role_info.ranking_keywords,
        related_hints=role_info.reasons,
        public_api=public_api,
        naming_signals=[],
        naming_keywords=[],
        error_paths=failure_hints,
        test_hints=test_hints,
    )


def _rust_summary(path: str, abs_path: Path, file_hash: str) -> FileSummary:
    text = _read_sample(abs_path)
    imports = rust_imports(abs_path, text=text)
    symbols = extract_rust_symbols(abs_path)
    intel = extract_code_intelligence(path=path, language="rust", text=text, symbols=symbols)
    effects = extract_side_effects(path=path, language="rust", text=text, imports=imports)
    role_info = infer_role_domain(
        path=path,
        language="rust",
        symbols=symbols,
        imports=imports,
        text=text,
        entrypoints=intel.entrypoints,
        external_systems=effects.external_systems,
    )
    role = role_info.role or _infer_responsibility(path, intel.defines)
    failure_hints = extract_failure_hints(text)
    public_api = _dedupe([*_infer_public_api(path, intel.defines, text), *intel.entrypoints])[:12]
    test_hints = _infer_test_hints(path, role, intel.defines)

    return FileSummary(
        path=path,
        hash=file_hash,
        language="rust",
        provider="offline",
        schema_version=SUMMARY_SCHEMA_VERSION,
        summary=_render_summary(
            language="Rust",
            domain=role_info.domain,
            role=role,
            entrypoints=intel.entrypoints,
            defines=intel.defines,
            calls=intel.calls,
            imports=imports,
            external_systems=effects.external_systems,
            reads_env=effects.reads_env,
            side_effects=effects.side_effects,
            ranking_keywords=role_info.ranking_keywords,
            failure_hints=failure_hints,
        ),
        imports=imports[:20],
        symbols=symbols,
        domain=role_info.domain,
        role=role,
        entrypoints=intel.entrypoints,
        defines=intel.defines,
        calls=intel.calls,
        reads_env=effects.reads_env,
        reads_files=effects.reads_files,
        writes_files=effects.writes_files,
        external_systems=effects.external_systems,
        side_effects=effects.side_effects,
        failure_hints=failure_hints,
        ranking_keywords=role_info.ranking_keywords,
        related_hints=role_info.reasons,
        public_api=public_api,
        naming_signals=[],
        naming_keywords=[],
        error_paths=failure_hints,
        test_hints=test_hints,
    )


def _generic_summary(path: str, abs_path: Path, language: str | None, file_hash: str) -> FileSummary:
    try:
        lines = abs_path.read_text(errors="replace").splitlines()[:30]
        snippet = "\n".join(lines)
    except OSError:
        snippet = ""

    effects = extract_side_effects(path=path, language=language, text=snippet, imports=[])
    role_info = infer_role_domain(
        path=path,
        language=language,
        symbols=[],
        imports=[],
        text=snippet,
        entrypoints=[],
        external_systems=effects.external_systems,
    )
    role = role_info.role or _infer_responsibility(path, [])
    failure_hints = extract_failure_hints(snippet)

    return FileSummary(
        path=path,
        hash=file_hash,
        language=language,
        provider="offline",
        schema_version=SUMMARY_SCHEMA_VERSION,
        summary=_render_summary(
            language=language or "unknown",
            domain=role_info.domain,
            role=role,
            entrypoints=[],
            defines=[],
            calls=[],
            imports=[],
            external_systems=effects.external_systems,
            reads_env=effects.reads_env,
            side_effects=effects.side_effects,
            ranking_keywords=role_info.ranking_keywords,
            failure_hints=failure_hints,
            fallback=f"First lines:\n{snippet[:300]}",
        ),
        imports=[],
        symbols=[],
        domain=role_info.domain,
        role=role,
        entrypoints=[],
        defines=[],
        calls=[],
        reads_env=effects.reads_env,
        reads_files=effects.reads_files,
        writes_files=effects.writes_files,
        external_systems=effects.external_systems,
        side_effects=effects.side_effects,
        failure_hints=failure_hints,
        ranking_keywords=role_info.ranking_keywords,
        related_hints=role_info.reasons,
        public_api=[],
        naming_signals=[],
        naming_keywords=[],
        error_paths=failure_hints,
        test_hints=_infer_test_hints(path, role, []),
    )


def _read_sample(abs_path: Path, max_chars: int = 16000) -> str:
    try:
        return abs_path.read_text(errors="replace")[:max_chars]
    except OSError:
        return ""


def _render_summary(
    *,
    language: str,
    domain: str | None,
    role: str | None,
    entrypoints: list[str],
    defines: list[str],
    calls: list[str],
    imports: list[str],
    external_systems: list[str],
    reads_env: list[str],
    side_effects: list[str],
    ranking_keywords: list[str],
    failure_hints: list[str],
    fallback: str | None = None,
) -> str:
    lines = [f"Language: {language}"]
    if domain:
        lines.append(f"Domain: {domain}")
    if role:
        lines.append(f"Role: {role}")
    _add_list(lines, "Entrypoints", entrypoints, limit=6)
    _add_list(lines, "Defines", defines, limit=8)
    internal_deps = [imp for imp in imports if imp.startswith(".")]
    external_deps = [imp for imp in imports if not imp.startswith(".")]
    _add_list(lines, "Internal deps", internal_deps, limit=8)
    _add_list(lines, "Imports", external_deps, limit=8)
    _add_list(lines, "External systems", external_systems, limit=6)
    _add_list(lines, "Reads env", reads_env, limit=8)
    _add_list(lines, "Side effects", side_effects, limit=6)
    _add_list(lines, "Failure hints", failure_hints, limit=4)
    if fallback:
        lines.append(fallback)
    return "\n".join(lines)


def _add_list(lines: list[str], label: str, values: list[str], *, limit: int) -> None:
    shown = _dedupe(values)[:limit]
    if shown:
        lines.append(f"{label}: {', '.join(shown)}")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _infer_side_effects(path: str, text: str, imports: list[str]) -> list[str]:
    haystack = f"{path}\n{text}\n{' '.join(imports)}".lower()
    checks = [
        ("network I/O", ("requests.", "fetch(", "httpx", "aiohttp", "urllib", "axios", "client.")),
        ("filesystem I/O", ("open(", "read_text(", "write_text(", "unlink(", "mkdir(", "shutil", "pathlib")),
        ("subprocess", ("subprocess", "popen(", "exec(", "spawn(")),
        ("database", ("select ", "insert ", "update ", "delete ", "session.", "cursor.", "sqlalchemy")),
        ("environment", ("os.environ", "process.env", "dotenv", "getenv(")),
        ("logging", ("logger.", "logging.", "console.", "print(")),
        ("external process", ("git ", "npm ", "pip ", "docker ")),
    ]
    return [label for label, needles in checks if any(needle in haystack for needle in needles)]


def _infer_public_api(path: str, symbols: list[str], text: str) -> list[str]:
    api = [name for name in symbols if not name.startswith("_")][:8]
    if re.search(r"@\w*app\.(get|post|put|delete|patch)|router\.(get|post|put|delete|patch)", text):
        api.append("HTTP route")
    if "typer.Option" in text or "@app.command" in text:
        api.append("CLI command")
    if "export " in text:
        api.append("module exports")
    return list(dict.fromkeys(api))[:8]


def _infer_error_paths(text: str) -> list[str]:
    checks = [
        ("raises exceptions", (r"\braise\s+\w+", r"\bthrow\s+new\b")),
        ("catches exceptions", (r"\bexcept\b", r"\bcatch\s*\(")),
        ("exits process", (r"typer\.Exit", r"sys\.exit", r"process\.exit")),
        ("returns error responses", (r"HTTPException", r"status_code\s*=\s*[45]\d\d", r"Response\(")),
        ("logs errors", (r"\.exception\(", r"\.error\(")),
    ]
    found: list[str] = []
    for label, patterns in checks:
        if any(re.search(pattern, text) for pattern in patterns):
            found.append(label)
    return found


def _infer_test_hints(path: str, role: str, symbols: list[str]) -> list[str]:
    hints: list[str] = []
    stem = Path(path).stem
    if "/tests/" not in path and not stem.startswith("test_"):
        hints.append(f"look for tests around `{stem}`")
    if role != stem:
        hints.append(f"exercise {role}")
    for sym in symbols[:3]:
        hints.append(f"cover `{sym}`")
    return hints[:6]


_SEMANTIC_PATTERNS = [
    ({"test", "spec", "fixture"}, {"test_", "spec_", "fixture_"}, "tests"),
    ({"auth", "login", "token", "session", "jwt"}, {"authenticate", "authorize", "login", "logout", "token"}, "authentication / authorization"),
    ({"cache", "redis", "memcache"}, {"cache", "invalidate", "ttl", "expire"}, "caching layer"),
    ({"model", "schema", "entity"}, {"BaseModel", "Model", "Schema", "dataclass"}, "data models / schema"),
    ({"migrate", "migration", "alembic", "flyway"}, set(), "database migrations"),
    ({"route", "router", "endpoint", "view", "handler", "controller"}, {"get", "post", "put", "delete", "patch", "handle", "dispatch"}, "request routing / handlers"),
    ({"config", "setting", "env"}, {"load_config", "Config", "Settings"}, "configuration"),
    ({"cli", "command", "cmd"}, {"main", "cli", "command", "run"}, "CLI entry point"),
    ({"scan", "parse", "extract", "analyze"}, {"scan", "parse", "extract", "analyze", "build"}, "analysis / parsing"),
    ({"render", "format", "template", "serializ"}, {"render", "format", "serialize", "template"}, "rendering / formatting"),
    ({"deploy", "ci", "docker", "k8s", "workflow"}, set(), "deployment / CI"),
    ({"util", "helper", "tool", "common", "shared"}, set(), "utilities / helpers"),
    ({"payment", "billing", "invoice", "stripe", "checkout"}, {"charge", "pay", "invoice", "refund", "subscription"}, "payments / billing"),
    ({"email", "mail", "smtp", "sendgrid", "mailgun", "notification"}, {"send_email", "send_mail", "notify", "alert"}, "email / notifications"),
    ({"queue", "worker", "job", "task", "celery", "rq", "sidekiq", "kafka", "rabbit"}, {"enqueue", "dequeue", "consume", "publish", "process_job"}, "background jobs / queues"),
    ({"websocket", "ws", "socket", "realtime", "sse", "stream"}, {"broadcast", "emit", "subscribe", "on_message"}, "realtime / websockets"),
    ({"search", "index", "elastic", "solr", "lucene", "vector", "embed"}, {"search", "index_doc", "query", "reindex"}, "search / indexing"),
    ({"storage", "s3", "gcs", "blob", "bucket", "upload", "download", "file"}, {"upload", "download", "put_object", "get_object", "store"}, "file storage"),
    ({"metric", "monitor", "trace", "telemetry", "otel", "prometheus", "sentry", "datadog"}, {"record_metric", "trace", "span", "counter", "gauge"}, "observability / metrics"),
    ({"permission", "role", "rbac", "acl", "policy", "access"}, {"check_permission", "has_role", "is_allowed", "enforce"}, "authorization / RBAC"),
    ({"rate", "throttle", "limit", "quota", "ratelimit"}, {"throttle", "rate_limit", "check_quota"}, "rate limiting"),
    ({"seed", "factory", "fake", "mock", "stub"}, {"create_", "build_", "make_", "factory", "seed"}, "test data / factories"),
    ({"admin", "dashboard", "panel", "backoffice"}, {"admin_", "register_", "site"}, "admin / dashboard"),
    ({"report", "export", "csv", "pdf", "excel", "xlsx"}, {"generate_report", "export_csv", "to_pdf"}, "reporting / exports"),
    ({"hook", "webhook", "event", "signal", "dispatch", "listener"}, {"on_", "handle_", "dispatch", "emit", "signal"}, "events / webhooks"),
    ({"validate", "validator", "form", "serializer"}, {"validate", "is_valid", "clean", "deserialize"}, "validation / serialization"),
    ({"crypto", "encrypt", "decrypt", "hash", "sign", "verify", "hmac", "aes", "rsa"}, {"encrypt", "decrypt", "hash_", "sign", "verify"}, "cryptography"),
    ({"log", "logging", "logger", "audit", "trail"}, {"log_", "audit_", "get_logger"}, "logging / audit"),
    ({"batch", "bulk", "import", "etl", "ingest", "pipeline"}, {"batch_", "bulk_", "ingest", "process_batch"}, "data pipeline / ETL"),
    ({"health", "ping", "status", "ready", "live"}, {"healthcheck", "ping", "is_ready", "is_alive"}, "health checks"),
    ({"retry", "backoff", "circuit", "breaker", "fault"}, {"retry", "with_retry", "backoff", "circuit_breaker"}, "resilience / retry"),
    ({"i18n", "locale", "translation", "l10n", "gettext", "language"}, {"translate", "gettext", "_", "ngettext"}, "i18n / localization"),
    ({"proxy", "gateway", "load", "balancer", "upstream", "downstream"}, {"forward", "proxy_pass", "route_request"}, "proxy / gateway"),
    ({"sourcing", "cqrs", "aggregate", "projection"}, {"apply_event", "handle_command", "project", "aggregate"}, "event sourcing / CQRS"),
    ({"graph", "node", "edge", "tree", "traverse", "walk"}, {"traverse", "dfs", "bfs", "walk_tree"}, "graph / tree traversal"),
    ({"client", "sdk", "fetch"}, {"get", "post", "fetch", "request", "call_api"}, "API client"),
    ({"lock", "mutex", "semaphore", "concurrent", "thread"}, {"acquire", "release", "lock", "asyncio", "await"}, "concurrency / locking"),
    ({"snapshot", "backup", "restore", "archive"}, {"snapshot", "backup", "restore", "archive"}, "snapshots / backup"),
    ({"diff", "patch", "merge", "conflict", "delta"}, {"diff", "patch", "merge", "apply_patch"}, "diff / merge"),
    ({"plugin", "extension", "addon", "middleware"}, {"register_plugin", "use_middleware", "extend"}, "plugins / middleware"),
]


def _infer_responsibility(path: str, symbols: list[str]) -> str:
    segments = re.split(r"[/_\-.]", path.lower())
    segments_set = set(segments)
    bigrams = {f"{segments[i]}_{segments[i + 1]}" for i in range(len(segments) - 1)}
    bigrams |= {f"{segments[i]}{segments[i + 1]}" for i in range(len(segments) - 1)}
    all_path_tokens = segments_set | bigrams
    symbols_lower = [s.lower() for s in symbols]
    for path_kw, sym_kw, label in _SEMANTIC_PATTERNS:
        if any(tok for tok in all_path_tokens if any(kw in tok for kw in path_kw)):
            return label
        if sym_kw and any(kw.lower() in sym for sym in symbols_lower for kw in sym_kw):
            return label
    return Path(path).stem.replace("_", " ")
