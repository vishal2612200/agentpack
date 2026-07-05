from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpack.core.models import DependencyGraph, FileInfo
from agentpack.core.config import ScoringWeights
from agentpack.analysis.monorepo import workspace_for_path, workspace_tokens

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "is", "are", "was", "were", "be", "been", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "can", "that", "this", "these", "those", "it", "its",
    "from", "not", "we", "our", "you", "your", "they", "them", "their",
    "file", "files", "code", "function", "method", "class", "module",
    "use", "using", "used", "how", "what", "when", "where", "why",
}

_GENERIC_TASK_TERMS = {
    "add", "added", "change", "changed", "changes", "clean", "cleanup",
    "code", "commit", "context", "debug", "dev", "development", "doc",
    "docs", "eval", "evals", "feature", "fix", "freshness", "general",
    "gap", "gaps", "generic", "impl", "implement", "implementation", "improve",
    "issue", "metric", "metrics", "noise", "noisy", "package", "pack", "packs",
    "quality", "release", "remaining", "repo", "root", "rule", "rules",
    "source", "stat", "stats", "sync", "task", "tasks", "test", "tests",
    "text", "update", "use", "useful", "usefulness", "version", "visibility",
    "workflow", "workflows", "wording",
}

_AMBIGUOUS_TASK_TERMS = {
    "analysis", "analyze", "analytics", "preview", "previews", "public",
    "tool", "tools", "page", "pages", "component", "components", "screen",
    "screens", "ui", "flow", "flows",
}

_CONCEPT_MAP: dict[str, frozenset[str]] = {
    # rate limiting
    "rate": frozenset({"throttle", "ratelimit", "leaky", "bucket", "debounce", "backoff", "quota"}),
    "limiting": frozenset({"throttle", "ratelimit", "leaky", "bucket", "debounce", "quota"}),
    "throttle": frozenset({"rate", "limit", "ratelimit", "leaky", "bucket", "quota"}),

    # authentication
    "auth": frozenset({"jwt", "bearer", "token", "oauth", "credential", "login", "signin", "identity", "principal"}),
    "authentication": frozenset({"jwt", "bearer", "token", "oauth", "credential", "login"}),
    "login": frozenset({"auth", "signin", "credential", "token", "session"}),

    # caching
    "cache": frozenset({"lru", "memoize", "memo", "ttl", "evict", "invalidate", "redis", "memcache"}),
    "caching": frozenset({"lru", "memoize", "memo", "ttl", "evict", "redis"}),

    # queue / messaging
    "queue": frozenset({"broker", "pubsub", "kafka", "rabbitmq", "worker", "job", "task", "celery", "enqueue", "dequeue"}),
    "message": frozenset({"queue", "broker", "pubsub", "event", "dispatch", "emit", "publish", "subscribe"}),

    # database
    "database": frozenset({"db", "orm", "migration", "schema", "query", "repository", "dao", "entity"}),
    "db": frozenset({"database", "orm", "migration", "schema", "query", "repository"}),
    "migration": frozenset({"schema", "database", "db", "alembic", "flyway", "liquibase"}),

    # concurrency
    "concurrency": frozenset({"mutex", "lock", "semaphore", "atomic", "thread", "async", "goroutine", "coroutine"}),
    "concurrent": frozenset({"mutex", "lock", "semaphore", "atomic", "thread", "race", "goroutine"}),
    "race": frozenset({"mutex", "lock", "semaphore", "atomic", "concurrent", "thread"}),
    "async": frozenset({"await", "coroutine", "future", "promise", "concurrent", "thread"}),

    # error handling
    "error": frozenset({"exception", "fault", "failure", "retry", "fallback", "circuit", "breaker"}),
    "retry": frozenset({"backoff", "error", "fault", "resilience", "circuit", "breaker"}),

    # http / api
    "api": frozenset({"endpoint", "route", "handler", "controller", "rest", "graphql", "grpc", "rpc"}),
    "endpoint": frozenset({"route", "handler", "controller", "api", "path", "url"}),
    "middleware": frozenset({"interceptor", "filter", "hook", "plugin", "decorator", "wrapper"}),

    # storage / files
    "storage": frozenset({"disk", "filesystem", "bucket", "blob", "upload", "download", "s3", "gcs"}),
    "upload": frozenset({"storage", "blob", "bucket", "multipart", "stream", "file"}),

    # security
    "security": frozenset({"auth", "permission", "role", "acl", "policy", "rbac", "encrypt", "hash", "sign"}),
    "permission": frozenset({"role", "acl", "policy", "rbac", "auth", "access", "grant", "deny"}),
    "encrypt": frozenset({"decrypt", "cipher", "hash", "sign", "verify", "secret", "key"}),

    # logging / observability
    "log": frozenset({"trace", "metric", "monitor", "observe", "telemetry", "audit", "event"}),
    "metric": frozenset({"log", "trace", "monitor", "observe", "telemetry", "prometheus", "gauge", "counter"}),

    # search
    "search": frozenset({"index", "query", "fulltext", "elasticsearch", "solr", "lucene", "rank", "score"}),

    # streaming / SSE / websocket
    "stream": frozenset({"sse", "websocket", "ws", "chunk", "realtime", "push", "subscribe", "channel"}),
    "sse": frozenset({"stream", "eventstream", "push", "realtime", "subscribe"}),
    "websocket": frozenset({"stream", "ws", "socket", "realtime", "channel", "push"}),
    "realtime": frozenset({"stream", "sse", "websocket", "push", "subscribe", "channel"}),

    # webhooks / events
    "webhook": frozenset({"event", "callback", "notify", "dispatch", "trigger", "listener", "handler"}),
    "event": frozenset({"webhook", "listener", "handler", "dispatch", "emit", "publish", "subscribe", "bus"}),

    # pagination
    "pagination": frozenset({"page", "cursor", "offset", "limit", "paginate", "scroll", "infinite"}),
    "paginate": frozenset({"page", "cursor", "offset", "limit", "pagination"}),

    # validation / schema
    "validation": frozenset({"validate", "schema", "sanitize", "constraint", "rule", "pydantic", "zod", "yup"}),
    "validate": frozenset({"validation", "schema", "sanitize", "constraint"}),
    "schema": frozenset({"validate", "model", "serializer", "deserializer", "marshal", "unmarshal"}),

    # deployment / infra
    "deploy": frozenset({"release", "rollout", "container", "docker", "k8s", "kubernetes", "terraform", "ci", "cd"}),
    "docker": frozenset({"container", "image", "compose", "deploy", "k8s", "registry"}),
    "kubernetes": frozenset({"k8s", "pod", "deployment", "service", "ingress", "helm", "container"}),

    # email / notifications
    "email": frozenset({"smtp", "sendgrid", "mailgun", "ses", "template", "notification", "mailer"}),
    "notification": frozenset({"email", "push", "sms", "alert", "webhook", "event"}),

    # payment / billing
    "payment": frozenset({"stripe", "paypal", "billing", "invoice", "charge", "subscription", "checkout"}),
    "billing": frozenset({"payment", "subscription", "invoice", "charge", "plan", "tier"}),

    # file / upload
    "file": frozenset({"upload", "download", "storage", "s3", "blob", "multipart", "attachment", "disk"}),

    # test / testing
    "test": frozenset({"spec", "fixture", "mock", "stub", "assert", "expect", "describe", "jest", "pytest"}),
    "mock": frozenset({"stub", "spy", "patch", "fixture", "test", "fake"}),

    # config / env
    "config": frozenset({"env", "settings", "environment", "dotenv", "toml", "yaml", "ini", "conf"}),
    "env": frozenset({"config", "settings", "environment", "dotenv", "variable"}),

    # serialization
    "serialize": frozenset({"json", "marshal", "encode", "decode", "pickle", "protobuf", "msgpack"}),
    "deserialize": frozenset({"json", "unmarshal", "decode", "parse", "protobuf"}),

    # health check / liveness
    "health": frozenset({"ping", "liveness", "readiness", "probe", "heartbeat", "status", "check"}),

    # astrology / charting product domains
    "kundali": frozenset({"astrology", "horoscope", "chart", "birth", "natal", "compatibility", "matching"}),
    "astrology": frozenset({"kundali", "horoscope", "chart", "birth", "natal", "compatibility"}),
    "horoscope": frozenset({"astrology", "kundali", "chart", "natal"}),
    "compatibility": frozenset({"matching", "match", "compare", "score", "relationship"}),
}

_VARIANTS: dict[str, str] = {
    "cancellation": "cancel",
    "cancelled": "cancel",
    "canceling": "cancel",
    "authentication": "auth",
    "authenticated": "auth",
    "authorize": "auth",
    "authorization": "auth",
    "configuration": "config",
    "configured": "config",
    "database": "db",
    "databases": "db",
    "connection": "conn",
    "connections": "conn",
    "management": "manage",
    "manager": "manage",
    "implementation": "impl",
    "implements": "impl",
    "middleware": "middleware",
    "request": "req",
    "response": "res",
    "verification": "verify",
    "verified": "verify",
    "verifying": "verify",
    "payments": "payment",
    "webhooks": "webhook",
    "variables": "variable",
    "session": "session",
    "sessions": "session",
    "error": "error",
    "errors": "error",
    "exception": "exception",
    "exceptions": "exception",
    "handler": "handler",
    "handlers": "handler",
    "service": "service",
    "services": "service",
    "endpoint": "endpoint",
    "endpoints": "endpoint",
    "router": "router",
    "routing": "router",
    "redis": "redis",
    "stream": "stream",
    "streaming": "stream",
    "goroutine": "goroutine",
    "channel": "chan",
    "interface": "interface",
    "struct": "struct",
    "trait": "trait",
    "impl": "impl",
}

CONFIG_EXTENSIONS = {
    ".toml", ".yaml", ".yml", ".json", ".env", ".ini", ".cfg", ".conf",
    ".dockerfile", ".makefile",
}
CONFIG_NAMES = {
    "config", "settings", "configuration", "env", ".env",
    "pyproject", "package", "dockerfile", "makefile", "cargo", "go",
    "build", "cmake",
}

_DEFAULT_WEIGHTS = ScoringWeights()


@dataclass
class KeywordPlan:
    weights: dict[str, float]
    generic_terms: tuple[str, ...]
    ambiguous_terms: tuple[str, ...]
    learned_ambiguous_terms: tuple[str, ...]
    concrete_terms: tuple[str, ...]
    rarity: dict[str, float]
    phrase_weights: dict[str, float] = field(default_factory=dict)
    workspace_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    workspace_phrase_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    learned_positive_terms: tuple[str, ...] = ()
    learned_ambiguous_phrases: tuple[str, ...] = ()
    learned_positive_phrases: tuple[str, ...] = ()
    literal_phrases: tuple[str, ...] = ()
    phrase_rarity: dict[str, float] = field(default_factory=dict)
    term_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    phrase_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    workspace_roots: tuple[str, ...] = ()
    task_kind: str = ""
    task_scope_terms: tuple[str, ...] = ()

_IMPLEMENTATION_ROLE_TOKENS = {
    "api", "apis", "route", "routes", "router", "endpoint", "endpoints",
    "controller", "controllers", "service", "services", "handler", "handlers",
    "resolver", "resolvers", "schema", "schemas", "model", "models",
    "repository", "repositories", "repo", "repos", "client", "clients",
    "adapter", "adapters", "provider", "providers", "serializer",
    "serializers", "validator", "validators", "worker", "workers", "job",
    "jobs", "mailer", "mailers", "migration", "migrations", "paginator",
    "pagination",
}

_ENTRYPOINT_ROLE_TOKENS = {
    "page", "pages", "screen", "screens", "view", "views", "component",
    "components", "api", "route", "routes", "router", "controller",
    "controllers", "endpoint", "endpoints", "handler", "handlers",
    "webhook", "webhooks",
}

_PATH_NOISE_TOKENS = {
    "src", "app", "apps", "lib", "libs", "pkg", "packages", "backend",
    "frontend", "server", "client", "web", "mobile", "index", "main",
    "test", "tests", "spec", "specs",
} | _IMPLEMENTATION_ROLE_TOKENS | _ENTRYPOINT_ROLE_TOKENS
_MAX_PHRASE_NGRAM = 2

_FILENAME_CORROBORATION_PREFIXES = (
    "modified",
    "staged",
    "symbol keyword match",
    "content keyword match",
    "matched ",
    "direct dependency",
    "reverse dependency",
    "has related tests",
    "test for",
    "config file",
    "knowledge/architecture doc",
    "implementation role match",
    "recently modified",
    "historically co-changed",
    "recall neighbor",
    "workspace match",
)

_GENERATED_AGENT_ARTIFACT_PREFIXES = (
    ".agentpack/",
    ".agent/",
)


def _add_keyword_weight(weights: dict[str, float], keyword: str, weight: float) -> None:
    weights[keyword] = max(weights.get(keyword, 0.0), weight)


def _singular_variant(word: str) -> str | None:
    if len(word) < 5 or word in _GENERIC_TASK_TERMS:
        return None
    if word.endswith("ies") and len(word) > 5:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")) and len(word) > 5:
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return None


def _conventional_commit_parts(task: str) -> tuple[str, str, str] | None:
    match = re.match(r"^\s*([a-z][a-z0-9-]*)(?:\(([^)]{1,80})\))?!?:\s*(.+)$", task, flags=re.IGNORECASE)
    if not match:
        return None
    kind, scope, subject = match.groups()
    return kind.lower(), scope or "", subject


def _task_match_text(task: str) -> str:
    conventional = _conventional_commit_parts(task)
    if not conventional:
        return task
    kind, scope, subject = conventional
    scope_text = " ".join(_ordered_tokens(scope))
    return f"{kind} {scope_text} {subject}".strip()


def task_terms(task: str) -> list[str]:
    return [
        word for word in re.split(r"[^a-zA-Z0-9]+", _task_match_text(task).lower())
        if len(word) >= 3 and word not in _STOPWORDS
    ]


def task_phrases(task: str, max_len: int = _MAX_PHRASE_NGRAM) -> list[str]:
    terms = task_terms(task)
    return _ngram_phrases(terms, max_len=max_len)


def _task_literal_phrases(task: str) -> tuple[str, ...]:
    candidates: list[str] = []
    candidates.extend(match.group(1) for match in re.finditer(r"`([^`]{3,120})`", task))
    candidates.extend(match.group(1) for match in re.finditer(r'"([^"]{3,120})"', task))
    candidates.extend(match.group(1) for match in re.finditer(r"'([^']{3,120})'", task))
    candidates.extend(
        match.group(0)
        for match in re.finditer(r"\b[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+){1,}\b", task)
    )

    phrases: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        tokens = [tok for tok in _ordered_tokens(raw) if len(tok) >= 2 and tok not in _STOPWORDS]
        if not (2 <= len(tokens) <= 8):
            continue
        if all(tok in _GENERIC_TASK_TERMS for tok in tokens):
            continue
        phrase = " ".join(tokens)
        if phrase in seen:
            continue
        seen.add(phrase)
        phrases.append(phrase)
    return tuple(phrases)


def _ngram_phrases(terms: list[str], *, max_len: int = _MAX_PHRASE_NGRAM) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    for size in range(2, max_len + 1):
        for index in range(len(terms) - size + 1):
            phrase_terms = terms[index:index + size]
            if all(term in _GENERIC_TASK_TERMS for term in phrase_terms):
                continue
            phrase = " ".join(phrase_terms)
            if phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)
    return phrases


def ambiguous_task_terms(task: str) -> list[str]:
    return sorted({word for word in task_terms(task) if word in _AMBIGUOUS_TASK_TERMS})


def concrete_task_terms(task: str) -> list[str]:
    return [
        word for word in task_terms(task)
        if word not in _GENERIC_TASK_TERMS and word not in _AMBIGUOUS_TASK_TERMS
    ]


def suggest_task_rewrite(task: str) -> str:
    tokens = task_terms(task)
    generic = [word for word in tokens if word in _GENERIC_TASK_TERMS]
    ambiguous = [word for word in tokens if word in _AMBIGUOUS_TASK_TERMS]
    concrete = concrete_task_terms(task)[:4]
    concrete_text = " + ".join(concrete) if concrete else "exact file, route, or failing symptom"
    if set(tokens) & {"frontend", "page", "pages", "component", "components", "seo", "signup", "public"}:
        scope = "frontend page/component work only"
        boundary = "; no backend service or analysis changes"
    elif set(tokens) & {"backend", "api", "service", "services", "job", "jobs"}:
        scope = "backend service/route work only"
        boundary = "; no frontend page or component changes"
    else:
        scope = "focus exact subsystem only"
        boundary = ""
    caution = ""
    if generic or ambiguous:
        weak_terms = [*generic[:2], *[word for word in ambiguous if word not in generic][:2]]
        if weak_terms:
            caution = f"; avoid vague terms like {', '.join(weak_terms)}"
    return f"{scope}: {concrete_text}{boundary}{caution}"


def _base_keyword_weights(task: str) -> dict[str, float]:
    words = task_terms(task)
    keyword_weights: dict[str, float] = {}
    for word in words:
        if word in _GENERIC_TASK_TERMS:
            literal_weight = 0.25
        elif word in _AMBIGUOUS_TASK_TERMS:
            literal_weight = 0.45
        else:
            literal_weight = 1.0
        _add_keyword_weight(keyword_weights, word, literal_weight)
        if word in _VARIANTS:
            variant = _VARIANTS[word]
            if variant in _GENERIC_TASK_TERMS:
                variant_weight = 0.25
            elif variant in _AMBIGUOUS_TASK_TERMS:
                variant_weight = min(0.45, literal_weight)
            else:
                variant_weight = min(0.75, literal_weight)
            _add_keyword_weight(keyword_weights, variant, variant_weight)
        singular = _singular_variant(word)
        if singular and singular not in _STOPWORDS:
            _add_keyword_weight(keyword_weights, singular, min(0.85, literal_weight))

    # Expand via concept map one level only. Expanded concepts are weaker than
    # literal task words so broad terms like "task" do not dominate ranking.
    expanded: dict[str, float] = {}
    for kw in keyword_weights:
        if kw in _CONCEPT_MAP and kw not in _GENERIC_TASK_TERMS:
            for synonym in _CONCEPT_MAP[kw]:
                _add_keyword_weight(expanded, synonym, 0.35)
                if synonym in _VARIANTS:
                    _add_keyword_weight(expanded, _VARIANTS[synonym], 0.35)
    for kw, weight in expanded.items():
        _add_keyword_weight(keyword_weights, kw, weight)
    return keyword_weights


def _load_metric_rows(root: Path | None, window: int = 40) -> list[dict[str, Any]]:
    if root is None:
        return []
    metrics_path = root / ".agentpack" / "metrics.jsonl"
    if not metrics_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in reversed(metrics_path.read_text(encoding="utf-8").splitlines()):
            if len(rows) >= window:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        return []
    return rows


def _task_signal_stats(root: Path | None, *, window: int = 40) -> dict[str, dict[str, dict[str, int]]]:
    rows = _load_metric_rows(root, window=window)
    stats: dict[str, dict[str, dict[str, int]]] = {
        "terms": {"good": {}, "bad": {}},
        "phrases": {"good": {}, "bad": {}},
    }
    if not rows:
        return stats
    bad_counts: dict[str, int] = {}
    good_counts: dict[str, int] = {}
    bad_phrases: dict[str, int] = {}
    good_phrases: dict[str, int] = {}
    for row in rows:
        task = row.get("task")
        if not isinstance(task, str):
            continue
        precision = row.get("selection_token_precision")
        bad_run = (
            isinstance(precision, int | float) and float(precision) <= 0.2
        ) or bool(row.get("selection_noise_paths"))
        good_run = isinstance(precision, int | float) and float(precision) >= 0.45
        for term in task_terms(task):
            if term in _GENERIC_TASK_TERMS or len(term) < 4:
                continue
            if bad_run:
                bad_counts[term] = bad_counts.get(term, 0) + 1
            if good_run:
                good_counts[term] = good_counts.get(term, 0) + 1
        for phrase in task_phrases(task):
            if bad_run:
                bad_phrases[phrase] = bad_phrases.get(phrase, 0) + 1
            if good_run:
                good_phrases[phrase] = good_phrases.get(phrase, 0) + 1
    stats["terms"]["bad"] = bad_counts
    stats["terms"]["good"] = good_counts
    stats["phrases"]["bad"] = bad_phrases
    stats["phrases"]["good"] = good_phrases
    return stats


def _summary_term_corpus(summary_data: dict[str, Any] | None) -> set[str]:
    if not summary_data:
        return set()
    terms: set[str] = set()
    for summary_field in (
        "role", "domain", "ranking_keywords", "defines", "calls", "entrypoints",
        "external_systems", "naming_keywords", "reads_env",
    ):
        terms.update(_summary_values(summary_data, summary_field))
    tokens: set[str] = set()
    for term in terms:
        tokens |= _tokens_for_match(term)
    return {token for token in tokens if len(token) >= 3}


def _document_phrases(path: str, summary_data: dict[str, Any] | None) -> set[str]:
    parts: list[str] = [path.replace("/", " ").replace("\\", " ")]
    if summary_data:
        for field in (
            "role", "domain", "ranking_keywords", "defines", "calls", "entrypoints",
            "external_systems", "naming_keywords", "reads_env",
        ):
            parts.extend(_summary_values(summary_data, field))
    phrases: set[str] = set()
    for value in parts:
        ordered = _ordered_tokens(value)
        phrases.update(_ngram_phrases(ordered, max_len=_MAX_PHRASE_NGRAM))
    return {phrase for phrase in phrases if phrase}


def _term_document_frequency(
    files: list[FileInfo],
    summaries: dict[str, Any] | None,
    workspace_roots: list[str] | None = None,
) -> tuple[int, dict[str, int], dict[str, dict[str, int]], dict[str, int], dict[str, int], dict[str, dict[str, int]]]:
    doc_counts: dict[str, int] = {}
    phrase_counts: dict[str, int] = {}
    workspace_counts: dict[str, dict[str, int]] = {}
    workspace_phrase_counts: dict[str, dict[str, int]] = {}
    workspace_totals: dict[str, int] = {}
    total_docs = 0
    for fi in files:
        if fi.ignored or fi.binary:
            continue
        doc_terms = _path_tokens(fi.path)
        doc_phrases = _document_phrases(fi.path, summaries.get(fi.path) if summaries else None)
        if summaries:
            doc_terms |= _summary_term_corpus(summaries.get(fi.path))
        if not doc_terms and not doc_phrases:
            continue
        total_docs += 1
        workspace = workspace_for_path(fi.path, workspace_roots or []) or "(root)"
        workspace_totals[workspace] = workspace_totals.get(workspace, 0) + 1
        for term in doc_terms:
            doc_counts[term] = doc_counts.get(term, 0) + 1
            workspace_counts.setdefault(workspace, {})
            workspace_counts[workspace][term] = workspace_counts[workspace].get(term, 0) + 1
        for phrase in doc_phrases:
            phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
            workspace_phrase_counts.setdefault(workspace, {})
            workspace_phrase_counts[workspace][phrase] = workspace_phrase_counts[workspace].get(phrase, 0) + 1
    return total_docs, doc_counts, workspace_counts, workspace_totals, phrase_counts, workspace_phrase_counts


def _rarity_score(term: str, total_docs: int, doc_counts: dict[str, int]) -> float:
    if total_docs <= 1:
        return 1.0
    df = doc_counts.get(term, 0)
    numerator = math.log((total_docs + 1) / (df + 1))
    denominator = math.log(total_docs + 1)
    if denominator <= 0:
        return 1.0
    return max(0.0, min(1.0, numerator / denominator))


def _learned_signal_terms(
    candidates: set[str],
    good_counts: dict[str, int],
    bad_counts: dict[str, int],
    *,
    min_good: int = 2,
    min_bad: int = 2,
) -> tuple[set[str], set[str]]:
    learned_positive = {
        term
        for term in candidates
        if good_counts.get(term, 0) >= min_good
        and good_counts.get(term, 0) >= max(2, bad_counts.get(term, 0) * 2)
    }
    learned_ambiguous = {
        term
        for term in candidates
        if bad_counts.get(term, 0) >= min_bad
        and bad_counts.get(term, 0) > max(1, good_counts.get(term, 0) * 2)
    }
    return learned_positive, learned_ambiguous


def build_keyword_plan(
    task: str,
    *,
    files: list[FileInfo] | None = None,
    summaries: dict[str, Any] | None = None,
    root: Path | None = None,
    workspace_roots: list[str] | None = None,
) -> KeywordPlan:
    conventional = _conventional_commit_parts(task)
    task_kind = conventional[0] if conventional else ""
    task_scope_terms = tuple(_ordered_tokens(conventional[1])) if conventional and conventional[1] else ()
    weights = _base_keyword_weights(task)
    literal_phrases = _task_literal_phrases(task)
    phrases = list(dict.fromkeys([*task_phrases(task), *literal_phrases]))
    dynamic_generic = {task_kind} if task_kind else set()
    generic_terms = {word for word in task_terms(task) if word in _GENERIC_TASK_TERMS} | dynamic_generic
    generic = tuple(sorted(generic_terms))
    static_ambiguous = set(ambiguous_task_terms(task))
    signal_stats = _task_signal_stats(root)
    term_good = signal_stats["terms"]["good"]
    term_bad = signal_stats["terms"]["bad"]
    phrase_good = signal_stats["phrases"]["good"]
    phrase_bad = signal_stats["phrases"]["bad"]
    learned_positive_terms, learned_ambiguous = _learned_signal_terms(set(weights), term_good, term_bad)
    learned_positive_phrases, learned_ambiguous_phrases = _learned_signal_terms(set(phrases), phrase_good, phrase_bad, min_good=1, min_bad=1)
    total_docs, doc_counts, workspace_counts, workspace_totals, phrase_counts, workspace_phrase_counts = _term_document_frequency(
        files or [],
        summaries,
        workspace_roots=workspace_roots,
    )
    rarity: dict[str, float] = {}
    adjusted: dict[str, float] = {}
    for term, weight in weights.items():
        rarity_value = _rarity_score(term, total_docs, doc_counts) if total_docs else 1.0
        rarity[term] = round(rarity_value, 3)
        rarity_factor = 0.55 + (0.7 * rarity_value)
        candidate = weight * rarity_factor
        if term in dynamic_generic:
            candidate = min(candidate, 0.25)
        if term in static_ambiguous:
            candidate = min(candidate, 0.45)
        if term in learned_ambiguous and term not in static_ambiguous:
            candidate = min(candidate, 0.55)
        if term in learned_positive_terms and term not in static_ambiguous:
            candidate = min(1.35, candidate + 0.2)
        adjusted[term] = round(candidate, 3)
    phrase_rarity: dict[str, float] = {}
    phrase_weights: dict[str, float] = {}
    for phrase in phrases:
        phrase_terms = phrase.split()
        part_weights = [adjusted.get(term, weights.get(term, 1.0)) for term in phrase_terms]
        base = sum(part_weights) / len(part_weights)
        rarity_value = _rarity_score(phrase, total_docs, phrase_counts) if total_docs else 1.0
        phrase_rarity[phrase] = round(rarity_value, 3)
        candidate = base * (0.75 + (0.45 * rarity_value))
        if phrase in learned_ambiguous_phrases:
            candidate = min(candidate, 0.45)
        elif phrase in learned_positive_phrases:
            candidate = min(1.4, candidate + 0.25)
        elif all(part not in static_ambiguous and part not in learned_ambiguous for part in phrase_terms):
            candidate = min(1.25, candidate + 0.12)
        phrase_weights[phrase] = round(candidate, 3)
    for phrase in literal_phrases:
        phrase_weights[phrase] = max(phrase_weights.get(phrase, 0.0), 1.6)
        phrase_rarity.setdefault(phrase, 1.0)
    workspace_weights: dict[str, dict[str, float]] = {}
    workspace_phrase_weights: dict[str, dict[str, float]] = {}
    for workspace, total in workspace_totals.items():
        workspace_weights[workspace] = {}
        for term in weights:
            ws_rarity = _rarity_score(term, total, workspace_counts.get(workspace, {}))
            workspace_weights[workspace][term] = round(
                min(1.5, adjusted.get(term, weights[term]) * (0.85 + (0.45 * ws_rarity))),
                3,
            )
        workspace_phrase_weights[workspace] = {}
        for phrase in phrase_weights:
            ws_rarity = _rarity_score(phrase, total, workspace_phrase_counts.get(workspace, {}))
            workspace_phrase_weights[workspace][phrase] = round(
                min(1.5, phrase_weights[phrase] * (0.85 + (0.45 * ws_rarity))),
                3,
            )
    concrete = tuple(sorted(
        term for term in weights
        if term not in set(generic) and term not in static_ambiguous and term not in learned_ambiguous
    ))
    term_stats = {
        term: {
            "weight": adjusted[term],
            "rarity": rarity[term],
            "good_runs": term_good.get(term, 0),
            "bad_runs": term_bad.get(term, 0),
            "kind": (
                "generic" if term in generic else
                "ambiguous" if term in static_ambiguous or term in learned_ambiguous else
                "positive" if term in learned_positive_terms else
                "concrete"
            ),
        }
        for term in adjusted
    }
    phrase_stats = {
        phrase: {
            "weight": phrase_weights[phrase],
            "rarity": phrase_rarity[phrase],
            "good_runs": phrase_good.get(phrase, 0),
            "bad_runs": phrase_bad.get(phrase, 0),
            "kind": (
                "ambiguous" if phrase in learned_ambiguous_phrases else
                "positive" if phrase in learned_positive_phrases else
                "phrase"
            ),
        }
        for phrase in phrase_weights
    }
    return KeywordPlan(
        weights=adjusted,
        phrase_weights=phrase_weights,
        workspace_weights=workspace_weights,
        workspace_phrase_weights=workspace_phrase_weights,
        generic_terms=generic,
        ambiguous_terms=tuple(sorted(static_ambiguous | learned_ambiguous)),
        learned_ambiguous_terms=tuple(sorted(learned_ambiguous)),
        learned_positive_terms=tuple(sorted(learned_positive_terms)),
        learned_ambiguous_phrases=tuple(sorted(learned_ambiguous_phrases)),
        learned_positive_phrases=tuple(sorted(learned_positive_phrases)),
        literal_phrases=literal_phrases,
        concrete_terms=concrete,
        rarity=rarity,
        phrase_rarity=phrase_rarity,
        term_stats=term_stats,
        phrase_stats=phrase_stats,
        workspace_roots=tuple(workspace_roots or ()),
        task_kind=task_kind,
        task_scope_terms=task_scope_terms,
    )


def persist_keyword_plan_stats(root: Path, task: str, plan: KeywordPlan) -> Path:
    out = root / ".agentpack" / "term_stats.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "generic_terms": list(plan.generic_terms),
        "ambiguous_terms": list(plan.ambiguous_terms),
        "learned_ambiguous_terms": list(plan.learned_ambiguous_terms),
        "learned_positive_terms": list(plan.learned_positive_terms),
        "learned_ambiguous_phrases": list(plan.learned_ambiguous_phrases),
        "learned_positive_phrases": list(plan.learned_positive_phrases),
        "literal_phrases": list(plan.literal_phrases),
        "concrete_terms": list(plan.concrete_terms),
        "workspace_roots": list(plan.workspace_roots),
        "terms": plan.term_stats,
        "phrases": plan.phrase_stats,
        "workspace_weights": plan.workspace_weights,
        "workspace_phrase_weights": plan.workspace_phrase_weights,
    }
    out.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return out


def extract_keyword_weights(task: str) -> dict[str, float]:
    return _base_keyword_weights(task)


def generic_task_term_ratio(task: str) -> float:
    words = task_terms(task)
    if not words:
        return 0.0
    generic = sum(1 for word in words if word in _GENERIC_TASK_TERMS)
    return generic / len(words)


def extract_keywords(task: str) -> set[str]:
    return set(extract_keyword_weights(task))


def enrich_keywords_from_files(
    keywords: set[str],
    changed_paths: set[str],
    files: list[FileInfo],
    max_new_keywords: int = 20,
) -> set[str]:
    """Expand keywords with high-frequency terms from changed file content.

    Reads only the changed files, extracts identifier-like tokens, and adds
    those that appear repeatedly — giving the ranker semantic signal beyond
    the task string alone.
    """
    path_map = {fi.path: fi for fi in files if not fi.ignored and not fi.binary}
    token_freq: dict[str, int] = {}

    for path in changed_paths:
        fi = path_map.get(path)
        if fi is None:
            continue
        if fi.content is not None:
            text = fi.content
        elif fi.abs_path.exists():
            try:
                text = fi.abs_path.read_text(errors="replace")
            except OSError:
                continue
        else:
            continue
        # Extract camelCase/snake_case identifiers and plain words
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text)
        for raw in tokens:
            # Split camelCase into parts
            parts = re.sub(r"([A-Z])", r" \1", raw).lower().split()
            for part in parts:
                if len(part) < 3 or part in _STOPWORDS:
                    continue
                token_freq[part] = token_freq.get(part, 0) + 1

    # Keep tokens that appear ≥3 times and aren't already in keywords
    new_keywords = {
        tok for tok, freq in token_freq.items()
        if freq >= 3 and tok not in keywords
    }

    # Limit to top max_new_keywords by frequency
    top = sorted(new_keywords, key=lambda t: -token_freq[t])[:max_new_keywords]
    return keywords | set(top)


def enrich_keyword_weights_from_files(
    keyword_weights: dict[str, float],
    changed_paths: set[str],
    files: list[FileInfo],
    max_new_keywords: int = 20,
) -> dict[str, float]:
    enriched = dict(keyword_weights)
    enriched_keywords = enrich_keywords_from_files(set(keyword_weights), changed_paths, files, max_new_keywords)
    for keyword in enriched_keywords - set(keyword_weights):
        enriched[keyword] = 0.5
    return enriched


def _ordered_tokens(text: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [tok for tok in re.split(r"[^a-zA-Z0-9]+", spaced.lower()) if tok]


def _tokens_for_match(text: str) -> set[str]:
    """Return identifier-ish tokens for exact keyword matching."""
    return set(_ordered_tokens(text))


def _path_tokens(path: str) -> set[str]:
    p = Path(path)
    pieces = list(p.parts[:-1]) + [p.stem]
    tokens: set[str] = set()
    for piece in pieces:
        tokens |= _tokens_for_match(piece)
    return tokens


def _keyword_weight_items(keywords: set[str] | dict[str, float] | KeywordPlan):
    if isinstance(keywords, KeywordPlan):
        return keywords.weights.items()
    if isinstance(keywords, dict):
        return keywords.items()
    return ((keyword, 1.0) for keyword in keywords)


def _term_weight_for_context(
    term: str,
    keywords: set[str] | dict[str, float] | KeywordPlan,
    *,
    path: str | None = None,
) -> float:
    plan = _keyword_plan(keywords)
    if plan is None:
        token_weights = dict(_keyword_weight_items(keywords))
        return float(token_weights.get(term, 0.0))
    weight = float(plan.weights.get(term, 0.0))
    if path and plan.workspace_weights and plan.workspace_roots:
        workspace = workspace_for_path(path, list(plan.workspace_roots)) or "(root)"
        weight = max(weight, float(plan.workspace_weights.get(workspace, {}).get(term, weight)))
    return weight


def _phrase_weight_for_context(
    phrase: str,
    plan: KeywordPlan,
    *,
    path: str | None = None,
) -> float:
    weight = float(plan.phrase_weights.get(phrase, 0.0))
    if path and plan.workspace_phrase_weights and plan.workspace_roots:
        workspace = workspace_for_path(path, list(plan.workspace_roots)) or "(root)"
        weight = max(weight, float(plan.workspace_phrase_weights.get(workspace, {}).get(phrase, weight)))
    return weight


def _keyword_token_weights(
    keywords: set[str] | dict[str, float] | KeywordPlan,
    *,
    path: str | None = None,
) -> dict[str, float]:
    items = _keyword_weight_items(keywords)
    token_weights: dict[str, float] = {}
    for keyword, weight in items:
        for token in _tokens_for_match(keyword):
            if len(token) >= 3:
                contextual = _term_weight_for_context(token, keywords, path=path) or weight
                token_weights[token] = max(token_weights.get(token, 0.0), contextual)
    return token_weights


def _keyword_plan(keywords: set[str] | dict[str, float] | KeywordPlan) -> KeywordPlan | None:
    return keywords if isinstance(keywords, KeywordPlan) else None


def _matched_keyword_tokens(text: str, keywords: set[str] | dict[str, float] | KeywordPlan) -> set[str]:
    token_weights = _keyword_token_weights(keywords)
    return _tokens_for_match(text) & set(token_weights)


def _matched_symbol_tokens(symbols: list[str], keywords: set[str] | dict[str, float] | KeywordPlan) -> set[str]:
    matches: set[str] = set()
    for sym in symbols:
        matches |= _matched_keyword_tokens(sym, keywords)
    return matches


def _matched_keyword_phrases(
    text: str,
    keywords: set[str] | dict[str, float] | KeywordPlan,
) -> set[str]:
    plan = _keyword_plan(keywords)
    if plan is None or not plan.phrase_weights:
        return set()
    normalized = " ".join(_ordered_tokens(text))
    return {phrase for phrase in plan.phrase_weights if phrase in normalized}


def _matched_literal_phrases(text: str, plan: KeywordPlan | None) -> set[str]:
    if plan is None or not plan.literal_phrases:
        return set()
    normalized = " ".join(_ordered_tokens(text))
    return {phrase for phrase in plan.literal_phrases if phrase in normalized}


def _literal_define_matches(value: str, plan: KeywordPlan | None) -> set[str]:
    if plan is None or not plan.literal_phrases:
        return set()
    normalized = " ".join(_ordered_tokens(value))
    return {
        phrase
        for phrase in plan.literal_phrases
        if phrase in normalized or normalized in phrase
    }


def _match_weight(
    text: str,
    keywords: set[str] | dict[str, float] | KeywordPlan,
    *,
    path: str | None = None,
) -> float:
    token_weights = _keyword_token_weights(keywords, path=path)
    matches = _tokens_for_match(text) & set(token_weights)
    best = max((token_weights[token] for token in matches), default=0.0)
    plan = _keyword_plan(keywords)
    if plan is not None and plan.phrase_weights:
        normalized = " ".join(_ordered_tokens(text))
        for phrase in plan.phrase_weights:
            if phrase in normalized:
                best = max(best, _phrase_weight_for_context(phrase, plan, path=path))
    return best


def _path_matches_keywords(path: str, keywords: set[str] | dict[str, float] | KeywordPlan) -> float:
    return _match_weight(path, keywords, path=path)


def _content_matches_keywords(text: str, keywords: set[str] | dict[str, float] | KeywordPlan) -> tuple[int, float]:
    token_weights = _keyword_token_weights(keywords)
    text_tokens = _tokens_for_match(text)
    matches = text_tokens & set(token_weights)
    return len(matches), sum(token_weights[token] for token in matches)


def _symbol_matches_keywords(symbols: list[str], keywords: set[str] | dict[str, float] | KeywordPlan) -> float:
    best_weight = 0.0
    for sym in symbols:
        best_weight = max(best_weight, _match_weight(sym, keywords))
    return best_weight


def _summary_values(summary: object, field: str) -> list[str]:
    if summary is None:
        return []
    if isinstance(summary, dict):
        value = summary.get(field)
    else:
        value = getattr(summary, field, None)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict) and "name" in item:
                result.append(str(item["name"]))
            elif hasattr(item, "name"):
                result.append(str(item.name))
            else:
                result.append(str(item))
        return result
    return [str(value)]


def _best_summary_match(
    values: list[str],
    keywords: set[str] | dict[str, float] | KeywordPlan,
    *,
    presence_terms: set[str] | None = None,
    path: str | None = None,
) -> tuple[str, float] | None:
    if not values:
        return None
    best_value = ""
    best_weight = 0.0
    for value in values:
        weight = _match_weight(value, keywords, path=path)
        if weight > best_weight:
            best_value = value
            best_weight = weight
    if best_weight > 0:
        return best_value, best_weight
    if presence_terms and (set(_keyword_token_weights(keywords)) & presence_terms):
        return values[0], 0.6
    return None


def _short_reason_value(value: str, max_len: int = 80) -> str:
    clean = " ".join(value.split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "..."


_GENERIC_SUMMARY_VALUES = {
    "HTTP API route handler",
    "React UI component",
    "React page component",
    "React page: page",
    "entrypoint",
    "configuration",
    "api",
    "frontend",
    "data",
}


def _summary_boost_weight(field: str, value: str, amount: float) -> float:
    if field in {"role", "domain"} and value in _GENERIC_SUMMARY_VALUES:
        return min(amount, 16.0)
    if field == "ranking_keywords" and value in {"handler", "http", "route", "api", "component", "page"}:
        return min(amount, 10.0)
    return amount


def _multi_token_summary_bonus(field: str, value: str, keywords: set[str] | dict[str, float] | KeywordPlan) -> float:
    if field not in {"defines", "public_api"}:
        return 0.0
    matched = _matched_keyword_tokens(value, keywords)
    if not matched:
        return 0.0
    plan = _keyword_plan(keywords)
    concrete = set(plan.concrete_terms) if plan is not None else set(_keyword_token_weights(keywords)) - _GENERIC_TASK_TERMS
    specific_matches = {term for term in matched if term in concrete and term not in _GENERIC_TASK_TERMS}
    if len(specific_matches) < 2:
        return 0.0
    return min(120.0, 55.0 * (len(specific_matches) - 1))


def _direct_content_evidence_bonus(reasons: list[str], content_hits: int) -> float:
    if content_hits < 2:
        return 0.0
    if "filename keyword match" in reasons or "symbol keyword match" in reasons:
        return 0.0
    has_direct_evidence = any(
        reason.startswith((
            "matched call:",
            "matched define:",
            "literal definition match:",
            "multi-token defines match",
            "matched entrypoint:",
            "keyword phrase match:",
        ))
        for reason in reasons
    )
    if not has_direct_evidence:
        return 0.0
    bonus = 120.0 + (50.0 * min(3, content_hits - 2))
    return min(270.0, bonus)


def _path_concrete_term_bonus(path: str, plan: KeywordPlan | None) -> float:
    if plan is None:
        return 0.0
    if _is_test_file(path) and plan.task_kind != "test":
        return 0.0
    path_terms = _path_tokens(path)
    concrete_matches = {
        term
        for term in plan.concrete_terms
        if term not in _GENERIC_TASK_TERMS and term in path_terms
    }
    if len(concrete_matches) < 2:
        return 0.0
    bonus = 70.0 + (35.0 * min(2, len(concrete_matches) - 2))
    if _is_config_file(path):
        bonus += 105.0
    return min(210.0 if _is_config_file(path) else 150.0, bonus)


def _scope_is_workspace_root(plan: KeywordPlan | None) -> bool:
    if plan is None or not plan.task_scope_terms or not plan.workspace_roots:
        return False
    scope_terms = set(plan.task_scope_terms)
    for root in plan.workspace_roots:
        root_parts = [part for part in Path(root).parts if part not in {".", ""}]
        if not root_parts:
            continue
        root_name_terms = set(_ordered_tokens(root_parts[-1]))
        if scope_terms <= root_name_terms:
            return True
    return False


def _should_dampen_scope_mismatch(path: str, plan: KeywordPlan | None) -> bool:
    if plan is None or plan.task_kind == "test" or not _scope_is_workspace_root(plan):
        return False
    path_terms = _path_tokens(path)
    return not set(plan.task_scope_terms) <= path_terms


def _has_role(path: str, roles: set[str]) -> bool:
    return bool(_path_tokens(path) & roles)


def _domain_tokens(path: str) -> set[str]:
    return {tok for tok in _path_tokens(path) if len(tok) >= 3 and tok not in _PATH_NOISE_TOKENS}


def _api_route_terms(path: str) -> tuple[str, ...]:
    parts = [part.lower() for part in Path(path).parts]
    if "api" not in parts:
        return ()
    api_index = parts.index("api")
    suffix = parts[api_index + 1:]
    if not suffix:
        return ()
    route_file_names = {
        "route.ts", "route.tsx", "route.js", "route.jsx",
        "routes.py", "urls.py", "views.py", "controller.ts", "controller.js",
    }
    if suffix[-1] in route_file_names or Path(suffix[-1]).stem.lower() in {"route", "routes", "urls", "views", "controller"}:
        suffix = suffix[:-1]
    terms: list[str] = []
    for part in suffix:
        clean = part.strip("[](){}")
        if not clean or clean.startswith("_"):
            continue
        terms.extend(token for token in _ordered_tokens(clean) if token and token not in _PATH_NOISE_TOKENS)
    return tuple(dict.fromkeys(terms))


def _is_api_route_path(path: str) -> bool:
    return bool(_api_route_terms(path))


def _api_route_label(path: str) -> str:
    terms = _api_route_terms(path)
    return "/api/" + "/".join(terms) if terms else path


def _normalize_api_path(value: str) -> str | None:
    match = re.search(r"/api/[A-Za-z0-9_./${}\[\]-]+", value)
    if not match:
        return None
    path = match.group(0).split("?", 1)[0].split("#", 1)[0].split("${", 1)[0]
    path = re.sub(r"/\[[^\]]+\]", "", path)
    path = path.rstrip("/,;)")
    return path.rstrip("/") or "/api"


def _api_paths_from_summary(summary_data: object | None) -> set[str]:
    paths: set[str] = set()
    if summary_data is None:
        return paths
    for summary_field in ("calls", "entrypoints", "public_api"):
        for value in _summary_values(summary_data, summary_field):
            normalized = _normalize_api_path(value)
            if normalized:
                paths.add(normalized)
    return paths


def _api_path_terms(api_path: str) -> set[str]:
    return {
        token
        for token in _ordered_tokens(api_path.removeprefix("/api/"))
        if token and token not in _PATH_NOISE_TOKENS
    }


def _api_route_path(path: str, summary_data: object | None = None) -> str | None:
    for value in _summary_values(summary_data, "entrypoints"):
        normalized = _normalize_api_path(value)
        if normalized:
            return normalized
    terms = _api_route_terms(path)
    if terms:
        return "/api/" + "/".join(terms)
    return None


def _looks_like_frontend_consumer(path: str, summary_data: object | None) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in {".tsx", ".jsx"}:
        return True
    path_terms = _path_tokens(path)
    if path_terms & {"component", "components", "page", "pages", "client", "dashboard"}:
        return True
    return any(
        value.startswith(("React page:", "React layout:", "React component:"))
        for value in _summary_values(summary_data, "entrypoints")
    )


def _has_strong_structural_reason(reasons: list[str]) -> bool:
    return any(
        reason.startswith((
            "API endpoint pair",
            "API route owner match",
            "direct content evidence",
            "direct dependency",
            "historically co-changed",
            "keyword phrase match:",
            "literal definition match:",
            "matched call:",
            "matched define:",
            "matched entrypoint:",
            "multi-token",
            "quoted literal match:",
            "recall neighbor",
            "reverse dependency",
            "test for",
            "workspace match",
        ))
        or reason in {
            "build/dependency metadata",
            "config file",
            "has related tests",
            "knowledge/architecture doc",
            "release/version metadata",
        }
        for reason in reasons
    )


def _is_generated_agent_artifact(path: str) -> bool:
    return path.startswith(_GENERATED_AGENT_ARTIFACT_PREFIXES)


def _generated_agent_artifact_score(score: float, *, changed: bool) -> float:
    if changed:
        return min(score, 160.0)
    return min(score * 0.25, 80.0)


def _keyword_only_false_positive(path: str, reasons: list[str], content_hits: int) -> bool:
    if _is_test_file(path):
        return False
    if _has_strong_structural_reason(reasons):
        return False
    keyword_reasons = [
        reason for reason in reasons
        if reason == "filename keyword match"
        or reason == "symbol keyword match"
        or reason.startswith((
            "content keyword match",
            "matched domain:",
            "matched naming keyword:",
            "matched ranking keyword:",
            "matched role keyword:",
        ))
    ]
    if len(keyword_reasons) < 2:
        return False
    if _is_api_route_path(path):
        return False
    if content_hits >= 4 and "symbol keyword match" in reasons:
        return False
    return True


def _is_go_file(path: str) -> bool:
    return Path(path).suffix.lower() == ".go"


def _is_root_go_source_file(path: str) -> bool:
    path_obj = Path(path)
    return _is_go_file(path) and len(path_obj.parts) == 1 and not _is_test_file(path)


def _has_go_symbol_carrier_signal(reasons: list[str]) -> bool:
    return "symbol keyword match" in reasons or any(
        reason.startswith(("matched define:", "matched ranking keyword:", "matched role keyword:"))
        for reason in reasons
    )


def _has_go_action_owner_signal(path: str, reasons: list[str], content_hits: int) -> bool:
    if content_hits >= 5:
        return True
    if any(
        reason.startswith((
            "conventional scope path match",
            "direct content evidence",
            "keyword phrase match:",
            "literal definition match:",
            "multi-term path match",
            "quoted literal match:",
            "test for ",
        ))
        or reason == "explicit test task file"
        for reason in reasons
    ):
        return True
    if _is_root_go_source_file(path) and "filename keyword match" in reasons and any(
        reason.startswith("matched role keyword:")
        for reason in reasons
    ):
        return True
    return False


def _should_dampen_go_symbol_carrier(path: str, reasons: list[str], content_hits: int) -> bool:
    if not _is_go_file(path) or not _has_go_symbol_carrier_signal(reasons):
        return False
    if _has_go_action_owner_signal(path, reasons, content_hits):
        return False
    if _is_root_go_source_file(path):
        return True
    if _is_test_file(path) and content_hits <= 2:
        return True
    return False


def score_files(
    files: list[FileInfo],
    changed_paths: set[str],
    staged_paths: set[str],
    recently_modified: list[str],
    dep_graph: "DependencyGraph | dict",
    keywords: set[str] | dict[str, float] | KeywordPlan,
    include_tests: bool = True,
    include_configs: bool = True,
    weights: ScoringWeights | None = None,
    summaries: dict | None = None,
    churn_counts: dict[str, int] | None = None,
    co_changed_paths: dict[str, int] | None = None,
) -> list[tuple[FileInfo, float, list[str]]]:
    from agentpack.core.models import DependencyGraph as _DG
    if not isinstance(dep_graph, _DG):
        dep_graph = _DG()
    w = weights or _DEFAULT_WEIGHTS
    all_paths = {f.path for f in files}
    results: list[tuple[FileInfo, float, list[str]]] = []
    recently_set = set(recently_modified[:20])
    keyword_plan = _keyword_plan(keywords)
    ambiguous_terms = set(keyword_plan.ambiguous_terms) if keyword_plan else set()
    concrete_terms = set(keyword_plan.concrete_terms) if keyword_plan else set()
    release_task = _is_release_task_keywords(keywords)
    explicit_test_task = _is_explicit_test_task_keywords(keywords)
    build_metadata_task = _is_build_metadata_task_keywords(keywords)

    churn_threshold: int | None = None
    if churn_counts:
        vals = sorted(churn_counts.values(), reverse=True)
        cutoff_idx = max(0, len(vals) // 10 - 1)  # top 10%
        churn_threshold = vals[cutoff_idx] if vals else None

    for fi in files:
        if fi.ignored or fi.binary:
            results.append((fi, w.ignored_penalty, ["ignored/binary"]))
            continue

        score = 0.0
        reasons: list[str] = []

        if fi.path in changed_paths:
            score += w.modified
            reasons.append("modified")

        if fi.path in staged_paths:
            score += w.staged
            reasons.append("staged")

        filename_weight = _path_matches_keywords(fi.path, keywords)
        matched_terms = _matched_keyword_tokens(fi.path, keywords)
        matched_phrases = _matched_keyword_phrases(fi.path, keywords)
        if filename_weight > 0:
            score += w.filename_keyword * filename_weight
            reasons.append("filename keyword match")
        if keyword_plan is not None and matched_phrases:
            best_phrase = max(
                matched_phrases,
                key=lambda phrase: _phrase_weight_for_context(phrase, keyword_plan, path=fi.path),
            )
            score += min(28.0, 18.0 * _phrase_weight_for_context(best_phrase, keyword_plan, path=fi.path))
            reasons.append(f"keyword phrase match: {best_phrase}")
        if keyword_plan is not None and keyword_plan.task_scope_terms and keyword_plan.task_kind != "test":
            scope_terms = set(keyword_plan.task_scope_terms)
            path_terms = _path_tokens(fi.path)
            if scope_terms <= path_terms:
                score += 160.0
                reasons.append("conventional scope path match")
        path_term_bonus = _path_concrete_term_bonus(fi.path, keyword_plan)
        if path_term_bonus:
            score += path_term_bonus
            reasons.append(f"multi-term path match +{path_term_bonus:.0f}")

        node = dep_graph.get(fi.path)
        sym_names: list[str] = []
        summary_data = summaries.get(fi.path) if summaries and fi.path in summaries else None
        if summary_data:
            sym_names = _summary_values(summary_data, "symbols")
        symbol_weight = _symbol_matches_keywords(sym_names, keywords)
        matched_terms |= _matched_symbol_tokens(sym_names, keywords)
        if symbol_weight > 0:
            score += w.symbol_keyword * symbol_weight
            reasons.append("symbol keyword match")

        if summary_data:
            summary_boosts = [
                ("entrypoints", "matched entrypoint", 72.0, None),
                ("external_systems", "matched external system", 64.0, None),
                ("role", "matched role keyword", 56.0, None),
                ("domain", "matched domain", 50.0, None),
                ("ranking_keywords", "matched ranking keyword", 44.0, None),
                ("defines", "matched define", 42.0, None),
                ("reads_env", "matched env read", 52.0, {"env", "environment", "variable", "config", "settings"}),
                ("side_effects", "matched side effect", 46.0, {"side", "effect", "effects", "io", "debug"}),
                ("calls", "matched call", 26.0, None),
            ]
            for field, label, amount, presence_terms in summary_boosts:
                match = _best_summary_match(
                    _summary_values(summary_data, field),
                    keywords,
                    presence_terms=presence_terms,
                    path=fi.path,
                )
                if not match:
                    continue
                value, match_weight = match
                score += _summary_boost_weight(field, value, amount) * match_weight
                multi_token_bonus = _multi_token_summary_bonus(field, value, keywords)
                if multi_token_bonus:
                    score += multi_token_bonus
                    reasons.append(f"multi-token {field} match +{multi_token_bonus:.0f}: {_short_reason_value(value)}")
                literal_define_matches = _literal_define_matches(value, keyword_plan) if field in {"defines", "public_api"} else set()
                if literal_define_matches:
                    best_literal_define = max(literal_define_matches, key=len)
                    score += 150.0
                    reasons.append(f"literal definition match: {best_literal_define}")
                reasons.append(f"{label}: {_short_reason_value(value)}")

            naming_keywords = _summary_values(summary_data, "naming_keywords")
            naming_signals = _summary_values(summary_data, "naming_signals")
            naming_match = _best_summary_match(naming_keywords, keywords, path=fi.path)
            if naming_match:
                value, match_weight = naming_match
                score += min(20.0, match_weight * 18.0)
                reasons.append(f"matched naming keyword: {_short_reason_value(value)}")

            generic_public_names = [
                value.split(": ", 1)[1]
                for value in naming_signals
                if value.startswith("generic public name: ")
            ]
            if generic_public_names and filename_weight == 0 and symbol_weight == 0:
                score += min(-6.0, w.weak_filename_match_penalty / 2)
                reasons.append(f"generic public API penalty: {generic_public_names[0]}")

        api_route_terms = set(_api_route_terms(fi.path))
        api_route_matches = api_route_terms & (set(_keyword_token_weights(keywords, path=fi.path)) - _PATH_NOISE_TOKENS)
        if api_route_matches:
            score += 170.0 + (35.0 * min(2, len(api_route_matches) - 1))
            reasons.append(f"API route owner match: {_api_route_label(fi.path)}")

        content_hits = 0
        searchable_text = ""
        if fi.content is not None:
            searchable_text = fi.content
            hits, hit_weight = _content_matches_keywords(searchable_text, keywords)
            content_hits = hits
            matched_terms |= _matched_keyword_tokens(searchable_text, keywords)
            matched_phrases |= _matched_keyword_phrases(searchable_text, keywords)
            if hits > 0:
                score += min(w.content_keyword_max, hit_weight * w.content_keyword_per_hit)
                reasons.append(f"content keyword match ({hits})")
        elif fi.abs_path.exists():
            try:
                searchable_text = fi.abs_path.read_text(errors="replace")
                hits, hit_weight = _content_matches_keywords(searchable_text, keywords)
                content_hits = hits
                matched_terms |= _matched_keyword_tokens(searchable_text, keywords)
                matched_phrases |= _matched_keyword_phrases(searchable_text, keywords)
                if hits > 0:
                    score += min(w.content_keyword_max, hit_weight * w.content_keyword_per_hit)
                    reasons.append(f"content keyword match ({hits})")
            except OSError:
                pass

        literal_matches = _matched_literal_phrases(f"{fi.path}\n{searchable_text}", keyword_plan)
        if literal_matches:
            best_literal = max(literal_matches, key=len)
            score += 360.0
            reasons.append(f"quoted literal match: {best_literal}")
        elif (
            keyword_plan is not None
            and keyword_plan.task_kind == "chore"
            and keyword_plan.literal_phrases
            and (filename_weight > 0 or symbol_weight > 0)
        ):
            score *= 0.55
            reasons.append("chore literal partial-match dampening")

        if keyword_plan is not None and matched_phrases:
            best_content_phrase = max(
                matched_phrases,
                key=lambda phrase: _phrase_weight_for_context(phrase, keyword_plan, path=fi.path),
            )
            score += min(20.0, 14.0 * _phrase_weight_for_context(best_content_phrase, keyword_plan, path=fi.path))
            if not any(reason.startswith("keyword phrase match:") for reason in reasons):
                reasons.append(f"keyword phrase match: {best_content_phrase}")

        direct_content_bonus = _direct_content_evidence_bonus(reasons, content_hits)
        if direct_content_bonus:
            score += direct_content_bonus
            reasons.append(f"direct content evidence +{direct_content_bonus:.0f}")

        if ambiguous_terms:
            ambiguous_hits = matched_terms & ambiguous_terms
            concrete_hits = matched_terms & concrete_terms
            if ambiguous_hits and not concrete_hits:
                labels = ", ".join(sorted(ambiguous_hits)[:3])
                if content_hits >= 1 and (
                    symbol_weight > 0
                    or _has_role(fi.path, _IMPLEMENTATION_ROLE_TOKENS)
                ):
                    bonus = min(24.0, 10.0 + (6.0 * min(content_hits, 3)))
                    score += bonus
                    reasons.append(f"ambiguous term restored by corroboration: {labels}")
                elif filename_weight > 0 and content_hits == 0 and symbol_weight == 0:
                    score = max(0.0, score - 8.0)
                    reasons.append(f"ambiguous term cap: {labels}")

        matched_task_signal = filename_weight > 0 or symbol_weight > 0 or content_hits > 0
        if matched_task_signal and _has_role(fi.path, _IMPLEMENTATION_ROLE_TOKENS):
            score += w.implementation_role
            reasons.append("implementation role match")
        elif fi.path in changed_paths:
            reasons.append("modified workspace context only")

        if explicit_test_task:
            if _is_test_file(fi.path):
                test_bonus = 95.0
                if "e2e" in {part.lower() for part in Path(fi.path).parts}:
                    test_bonus += 35.0
                score += test_bonus
                reasons.append("explicit test task file")
            elif not _is_config_file(fi.path):
                score = max(0.0, score * 0.72)
                reasons.append("explicit test task non-test dampening")

        for dep_path in node.imports:
            if dep_path in changed_paths or _path_matches_keywords(dep_path, keywords) > 0:
                score += w.direct_dep
                reasons.append("direct dependency of changed file")
                break

        for other_path, other_node in dep_graph.items():
            if fi.path in other_node.imports and other_path in changed_paths:
                score += w.reverse_dep
                reasons.append("reverse dependency")
                break

        if include_tests:
            tests = node.tests
            if tests and any(t in all_paths for t in tests):
                score += w.related_test
                reasons.append("has related tests")
            elif _is_api_route_path(fi.path):
                reasons.append("no direct tests found for endpoint")

            if _is_test_file(fi.path):
                for src_path in changed_paths:
                    if _test_matches_source(fi.path, src_path):
                        score += w.related_test
                        reasons.append(f"test for {src_path}")
                        break

        if include_configs and _is_config_file(fi.path):
            score += w.config_file
            reasons.append("config file")

        if build_metadata_task and _is_build_metadata_file(fi.path):
            score += 190.0 + _build_metadata_priority_bonus(fi.path)
            reasons.append("build/dependency metadata")

        is_release_metadata = release_task and _is_release_metadata_file(fi.path)
        if is_release_metadata:
            score += 110.0 + _release_metadata_priority_bonus(fi.path)
            reasons.append("release/version metadata")

        if _has_secret_content(fi):
            score += w.modified
            reasons.append("secret redaction candidate")

        if _is_knowledge_file(fi.path):
            score += w.knowledge_file
            reasons.append("knowledge/architecture doc")

        if fi.path in recently_set:
            score += w.recently_modified
            reasons.append("recently modified")

        if churn_counts and churn_threshold is not None:
            count = churn_counts.get(fi.path, 0)
            if count >= churn_threshold:
                score += w.churn_high
                reasons.append(f"high churn ({count} commits)")

        if co_changed_paths and fi.path in co_changed_paths:
            count = co_changed_paths[fi.path]
            score += w.co_changed * min(1.0, 0.5 + (count / 4))
            reasons.append(f"historically co-changed ({count} commits)")

        if release_task and not is_release_metadata and _has_only_release_term_signal(matched_terms, reasons):
            score = max(0.0, score * 0.35)
            reasons.append("release-term-only non-metadata dampening")

        if _should_dampen_scope_mismatch(fi.path, keyword_plan):
            score = max(0.0, score * 0.62)
            reasons.append("conventional scope mismatch dampening")

        if filename_weight > 0 and not _has_filename_corroboration(reasons):
            score = max(1.0, score + w.weak_filename_match_penalty)
            reasons.append(f"weak filename-only match {w.weak_filename_match_penalty:.0f}")

        if fi.too_large and (content_hits >= 2 or _has_large_file_support(reasons)):
            score += 120.0
            reasons.append("large supported file")
        elif fi.too_large and score < 50:
            score += w.large_unrelated_penalty
            reasons.append("large unrelated file")

        if _keyword_only_false_positive(fi.path, reasons, content_hits):
            score = max(0.0, score * 0.72)
            reasons.append("likely false positive: keyword-only match")

        if _should_dampen_go_symbol_carrier(fi.path, reasons, content_hits):
            score = max(0.0, score * 0.45)
            reasons.append("broad Go symbol carrier dampening")

        if _is_generated_agent_artifact(fi.path):
            score = _generated_agent_artifact_score(score, changed=fi.path in changed_paths)
            reasons.append("generated agent artifact dampening")

        results.append((fi, score, reasons))

    return results


def _has_filename_corroboration(reasons: list[str]) -> bool:
    return any(reason.startswith(_FILENAME_CORROBORATION_PREFIXES) for reason in reasons)


def boost_cross_layer_related(
    scored: list[tuple[FileInfo, float, list[str]]],
    keywords: set[str] | dict[str, float] | KeywordPlan,
    weights: ScoringWeights | None = None,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Boost service/controller/schema/handler files near high-scoring entrypoints.

    Full-stack tasks often start from a UI page or route, but the actual fix is
    in a backend service or handler. This boost connects files with shared
    domain tokens while requiring either a task keyword or a high-scoring
    entrypoint seed, so generic services do not all float upward.
    """
    w = weights or _DEFAULT_WEIGHTS
    positive_scores = [score for fi, score, _ in scored if score > 0 and not fi.ignored and not fi.binary]
    if not positive_scores:
        return scored
    threshold = sorted(positive_scores, reverse=True)[max(0, min(4, len(positive_scores) - 1))]

    keyword_tokens = set(_keyword_token_weights(keywords))
    seed_domains: set[str] = set()
    for fi, score, _reasons in scored:
        if score >= threshold and _has_role(fi.path, _ENTRYPOINT_ROLE_TOKENS):
            seed_domains |= _domain_tokens(fi.path)

    related_terms = (keyword_tokens | seed_domains) - _PATH_NOISE_TOKENS
    if not related_terms:
        return scored

    result: list[tuple[FileInfo, float, list[str]]] = []
    for fi, score, reasons in scored:
        if not fi.ignored and not fi.binary and _has_role(fi.path, _IMPLEMENTATION_ROLE_TOKENS):
            if _domain_tokens(fi.path) & related_terms and "cross-layer related implementation" not in reasons:
                score += w.cross_layer_related
                reasons = reasons + ["cross-layer related implementation"]
        result.append((fi, score, reasons))
    return result


def boost_api_endpoint_pairs(
    scored: list[tuple[FileInfo, float, list[str]]],
    keywords: set[str] | dict[str, float] | KeywordPlan,
    weights: ScoringWeights | None = None,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Boost sibling API endpoints once one endpoint in the family is a strong task match."""
    w = weights or _DEFAULT_WEIGHTS
    keyword_tokens = set(_keyword_token_weights(keywords)) - _PATH_NOISE_TOKENS
    api_rows: list[tuple[FileInfo, float, list[str], tuple[str, ...]]] = []
    for fi, score, reasons in scored:
        terms = _api_route_terms(fi.path)
        if terms and not fi.ignored and not fi.binary:
            api_rows.append((fi, score, reasons, terms))
    if len(api_rows) < 2:
        return scored

    seeds: dict[str, tuple[str, float]] = {}
    for fi, score, reasons, terms in api_rows:
        if not terms:
            continue
        family = terms[0]
        direct_owner = any(reason.startswith("API route owner match:") for reason in reasons)
        endpoint_matches_task = bool(set(terms) & keyword_tokens)
        if score < 90 and not direct_owner:
            continue
        if not (direct_owner or endpoint_matches_task):
            continue
        current = seeds.get(family)
        if current is None or score > current[1]:
            seeds[family] = (fi.path, score)
    if not seeds:
        return scored

    result: list[tuple[FileInfo, float, list[str]]] = []
    for fi, score, reasons in scored:
        terms = _api_route_terms(fi.path)
        if terms:
            family = terms[0]
            seed = seeds.get(family)
            if seed and seed[0] != fi.path and not any(reason.startswith("API endpoint pair") for reason in reasons):
                seed_path, _seed_score = seed
                amount = min(85.0, w.cross_layer_related + 15.0)
                if set(terms) & keyword_tokens:
                    amount += 25.0
                score += amount
                reasons = reasons + [f"API endpoint pair with {_api_route_label(seed_path)}"]
        result.append((fi, score, reasons))
    return result


def boost_frontend_api_consumers(
    scored: list[tuple[FileInfo, float, list[str]]],
    summaries: dict[str, Any] | None,
    keywords: set[str] | dict[str, float] | KeywordPlan,
    weights: ScoringWeights | None = None,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Boost API route files consumed by scored frontend/client files."""
    if not summaries:
        return scored
    w = weights or _DEFAULT_WEIGHTS
    keyword_tokens = set(_keyword_token_weights(keywords)) - _PATH_NOISE_TOKENS
    endpoint_by_api_path: dict[str, str] = {}
    for fi, _score, _reasons in scored:
        api_path = _api_route_path(fi.path, summaries.get(fi.path))
        if api_path:
            endpoint_by_api_path[api_path] = fi.path
    if not endpoint_by_api_path:
        return scored

    boosts: dict[str, tuple[float, str, str]] = {}
    for consumer, consumer_score, _consumer_reasons in scored:
        summary_data = summaries.get(consumer.path)
        consumed_paths = _api_paths_from_summary(summary_data)
        if not consumed_paths or consumer_score <= 0:
            continue
        if not _looks_like_frontend_consumer(consumer.path, summary_data):
            continue
        for api_path in consumed_paths:
            endpoint_path = endpoint_by_api_path.get(api_path)
            if not endpoint_path or endpoint_path == consumer.path:
                continue
            amount = min(150.0, 90.0 + (w.cross_layer_related * 0.6))
            if _api_path_terms(api_path) & keyword_tokens:
                amount += 45.0
            current = boosts.get(endpoint_path)
            if current is None or amount > current[0]:
                boosts[endpoint_path] = (amount, api_path, consumer.path)

    if not boosts:
        return scored
    result: list[tuple[FileInfo, float, list[str]]] = []
    for fi, score, reasons in scored:
        boost = boosts.get(fi.path)
        if boost:
            amount, api_path, consumer_path = boost
            score += amount
            reasons = reasons + [f"API producer for frontend call {api_path} from {consumer_path}"]
        result.append((fi, score, reasons))
    return result


def boost_paired_tests(
    scored: list[tuple[FileInfo, float, list[str]]],
    weights: ScoringWeights | None = None,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Boost test files that pair with high-scoring source files.

    Only applies to test files not already boosted by changed_paths.
    Threshold: source must score above the median non-test score.
    """
    w = weights or _DEFAULT_WEIGHTS
    non_test_scores = [
        score for fi, score, _ in scored
        if not fi.ignored and not fi.binary and not _is_test_file(fi.path) and score > 0
    ]
    if not non_test_scores:
        return scored
    threshold = sorted(non_test_scores)[len(non_test_scores) // 2]  # median

    source_scores = {
        fi.path: score for fi, score, _ in scored
        if not _is_test_file(fi.path) and not _is_config_file(fi.path) and score >= threshold
    }

    result = []
    for fi, score, reasons in scored:
        if _is_test_file(fi.path):
            already_boosted = any("test for" in r for r in reasons)
            if not already_boosted:
                for src_path, src_score in source_scores.items():
                    if _test_matches_source(fi.path, src_path):
                        score += w.related_test
                        reasons = reasons + [f"test for high-scoring {src_path}"]
                        break
        result.append((fi, score, reasons))
    return result


def boost_recall_neighbors(
    scored: list[tuple[FileInfo, float, list[str]]],
    dep_graph: DependencyGraph,
    changed_paths: set[str],
    weights: ScoringWeights | None = None,
    *,
    seed_limit: int = 8,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Boost import/reverse-import/test neighbors of strong seed files.

    This raises recall for files that do not match task words directly but sit
    next to a changed or high-scoring file in the dependency graph.
    """
    if not scored:
        return scored
    w = weights or _DEFAULT_WEIGHTS
    path_set = {fi.path for fi, _score, _reasons in scored}
    seed_candidates = [
        (fi.path, score)
        for fi, score, _reasons in scored
        if fi.path in changed_paths or score >= 120
    ]
    seed_paths = [
        path for path, _score in sorted(
            seed_candidates,
            key=lambda item: (item[0] not in changed_paths, -item[1], item[0]),
        )[:seed_limit]
    ]
    if not seed_paths:
        return scored

    boosts: dict[str, tuple[float, str]] = {}
    for seed in seed_paths:
        node = dep_graph.get(seed)
        neighbors = [*node.imports, *node.imported_by, *node.tests]
        for neighbor in neighbors:
            if neighbor == seed or neighbor not in path_set:
                continue
            amount = w.recall_neighbor + (8 if seed in changed_paths else 0)
            current = boosts.get(neighbor)
            if current is None or amount > current[0]:
                boosts[neighbor] = (amount, seed)

    result: list[tuple[FileInfo, float, list[str]]] = []
    for fi, score, reasons in scored:
        boost = boosts.get(fi.path)
        if boost and not fi.ignored and not fi.binary:
            amount, seed = boost
            score += amount
            reasons = reasons + [f"recall neighbor of {seed}"]
        result.append((fi, score, reasons))
    return result


def boost_second_pass_expansion(
    scored: list[tuple[FileInfo, float, list[str]]],
    dep_graph: DependencyGraph,
    keywords: set[str] | dict[str, float] | KeywordPlan,
    weights: ScoringWeights | None = None,
    *,
    seed_limit: int = 10,
    max_boosts: int = 32,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Boost guarded two-hop neighbours around strong first-pass seeds.

    This is deliberately conservative: it only boosts files that are close to a
    strong seed and share task/domain signal, are paired tests, or are config
    files. That raises recall for adjacent implementation files without turning
    broad task wording into repo-wide expansion.
    """
    if not scored:
        return scored
    w = weights or _DEFAULT_WEIGHTS
    path_map = {fi.path: (fi, score, reasons) for fi, score, reasons in scored}
    keyword_tokens = set(_keyword_token_weights(keywords)) - _PATH_NOISE_TOKENS

    seed_paths = [
        fi.path
        for fi, score, reasons in sorted(scored, key=lambda row: row[1], reverse=True)
        if score >= 100
        or any(
            reason.startswith((
                "modified",
                "staged",
                "workspace match",
                "cross-layer related",
                "recall neighbor",
                "historically co-changed",
            ))
            for reason in reasons
        )
    ][:seed_limit]
    if not seed_paths:
        return scored

    boosts: dict[str, tuple[float, str, str]] = {}

    def neighbours(path: str) -> set[str]:
        node = dep_graph.get(path)
        return {p for p in (*node.imports, *node.imported_by, *node.tests) if p in path_map and p != path}

    for seed in seed_paths:
        seed_domains = _domain_tokens(seed) | keyword_tokens
        first_hop = neighbours(seed)
        second_hop = {candidate for hop in first_hop for candidate in neighbours(hop)}
        for candidate in sorted(second_hop - {seed} - first_hop):
            fi, _score, _reasons = path_map[candidate]
            if fi.ignored or fi.binary:
                continue
            candidate_domains = _domain_tokens(candidate)
            is_test_pair = _is_test_file(candidate) and (
                any(_test_matches_source(candidate, hop) for hop in first_hop | {seed})
                or any(_test_matches_source(candidate, hop) for hop in seed_domains)
            )
            has_domain_signal = bool(candidate_domains & seed_domains)
            has_config_signal = _is_config_file(candidate) and bool(seed_domains)
            if not (is_test_pair or has_domain_signal or has_config_signal):
                continue
            amount = w.recall_neighbor * 0.5
            label = "second-pass related test" if is_test_pair else "second-pass recall neighbor"
            if has_domain_signal:
                amount += 4
            current = boosts.get(candidate)
            if current is None or amount > current[0]:
                boosts[candidate] = (amount, seed, label)

    if not boosts:
        return scored
    keep = {
        path: value
        for path, value in sorted(boosts.items(), key=lambda item: item[1][0], reverse=True)[:max_boosts]
    }

    result: list[tuple[FileInfo, float, list[str]]] = []
    for fi, score, reasons in scored:
        boost = keep.get(fi.path)
        if boost:
            amount, seed, label = boost
            score += amount
            reasons = reasons + [f"{label} of {seed}"]
        result.append((fi, score, reasons))
    return result


def boost_monorepo_workspaces(
    scored: list[tuple[FileInfo, float, list[str]]],
    *,
    workspace_roots: list[str],
    workspace_dependency_edges: dict[str, set[str]] | None = None,
    changed_paths: set[str],
    task: str,
    weights: ScoringWeights | None = None,
) -> list[tuple[FileInfo, float, list[str]]]:
    """Boost files in the changed/task-named workspace and workspace deps."""
    if not workspace_roots:
        return scored
    w = weights or _DEFAULT_WEIGHTS
    workspace_dependency_edges = workspace_dependency_edges or {}
    active_workspaces = {
        workspace
        for path in changed_paths
        if (workspace := workspace_for_path(path, workspace_roots))
    }
    task_tokens = _tokens_for_match(task)
    for workspace in workspace_roots:
        if workspace_tokens(workspace) & task_tokens:
            active_workspaces.add(workspace)
    if not active_workspaces:
        return scored
    dependency_workspaces = {
        dep
        for workspace in active_workspaces
        for dep in workspace_dependency_edges.get(workspace, set())
    }
    dependent_workspaces = {
        workspace
        for workspace, deps in workspace_dependency_edges.items()
        if active_workspaces & deps
    }

    result: list[tuple[FileInfo, float, list[str]]] = []
    for fi, score, reasons in scored:
        workspace = workspace_for_path(fi.path, workspace_roots)
        if workspace in active_workspaces and not fi.ignored and not fi.binary:
            score += w.workspace_match
            reasons = reasons + [f"workspace match {workspace}"]
        elif workspace in dependency_workspaces and not fi.ignored and not fi.binary:
            score += w.recall_neighbor
            reasons = reasons + [f"workspace dependency {workspace}"]
        elif workspace in dependent_workspaces and not fi.ignored and not fi.binary:
            score += w.recall_neighbor * 0.5
            reasons = reasons + [f"workspace dependent {workspace}"]
        result.append((fi, score, reasons))
    return result


def _is_test_file(path: str) -> bool:
    p = Path(path)
    return (
        p.stem.startswith("test_")
        or p.stem.endswith("_test")
        or p.stem.endswith(".test")
        or p.stem.endswith(".spec")
        or "tests" in p.parts
        or "__tests__" in p.parts
    )


def _test_matches_source(test_path: str, src_path: str) -> bool:
    src_stem = Path(src_path).stem
    test_stem = Path(test_path).stem
    return src_stem in test_stem or test_stem.replace("test_", "") == src_stem


def _is_explicit_test_task_keywords(keywords: set[str] | dict[str, float] | KeywordPlan) -> bool:
    plan = _keyword_plan(keywords)
    if plan is not None and plan.task_kind == "test":
        return True
    tokens = set(_keyword_token_weights(keywords))
    return bool(tokens & {"test", "tests", "spec", "specs"})


def _is_config_file(path: str) -> bool:
    p = Path(path)
    name = p.name.lower()
    return (
        p.suffix.lower() in CONFIG_EXTENSIONS
        or p.stem.lower() in CONFIG_NAMES
        or _looks_like_config_filename(name)
    )


def _looks_like_config_filename(name: str) -> bool:
    tokens = [token for token in re.split(r"[^a-z0-9]+", name.lower()) if token]
    return "config" in tokens or "conf" in tokens


def _has_large_file_support(reasons: list[str]) -> bool:
    return any(
        reason.startswith((
            "matched call:",
            "matched define:",
            "multi-token",
            "keyword phrase match:",
            "direct dependency",
            "reverse dependency",
            "historically co-changed",
        ))
        for reason in reasons
    )


def _has_secret_content(fi: FileInfo) -> bool:
    from agentpack.core.redactor import redact_secrets

    text = fi.content
    if text is None and fi.abs_path.exists():
        try:
            text = fi.abs_path.read_text(errors="replace")
        except OSError:
            return False
    if not text:
        return False
    _redacted, warnings = redact_secrets(text, fi.path)
    return bool(warnings)


_KNOWLEDGE_NAMES = {
    "decisions", "adr", "architecture", "contributing", "design",
    "technical", "tradeoffs", "rfc", "proposal",
}
_KNOWLEDGE_DIRS = {"adr", "adrs", "decisions", "rfcs", "proposals", "design"}


def _is_knowledge_file(path: str) -> bool:
    p = Path(path)
    stem_lower = p.stem.lower()
    # Match ADR-NNN.md patterns and known doc names
    if stem_lower in _KNOWLEDGE_NAMES:
        return p.suffix.lower() == ".md"
    if re.match(r"adr[-_]?\d+", stem_lower):
        return True
    # Any .md file in a known docs dir
    if any(part.lower() in _KNOWLEDGE_DIRS for part in p.parts):
        return p.suffix.lower() == ".md"
    return False


def _is_release_task_keywords(keywords: set[str] | dict[str, float] | KeywordPlan) -> bool:
    release_terms = {
        "release", "version", "prerelease", "bump", "publish", "tag", "pypi", "npm",
        "metadata", "license",
    }
    return bool(set(_keyword_token_weights(keywords)) & release_terms)


_RELEASE_TASK_TERMS = {
    "release", "version", "prerelease", "bump", "publish", "tag", "pypi", "npm",
    "metadata", "license",
}


_BUILD_METADATA_TASK_TERMS = {
    "build",
    "building",
    "boot",
    "dependency",
    "dependencies",
    "gradle",
    "java",
    "maven",
    "runtime",
    "starter",
}


def _is_build_metadata_task_keywords(keywords: set[str] | dict[str, float] | KeywordPlan) -> bool:
    return bool(set(_keyword_token_weights(keywords)) & _BUILD_METADATA_TASK_TERMS)


def _is_build_metadata_file(path: str) -> bool:
    p = Path(path)
    if len(p.parts) != 1:
        return False
    return p.name.lower() in {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
    }


def _build_metadata_priority_bonus(path: str) -> float:
    name = Path(path).name.lower()
    if name in {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}:
        return 90.0
    return 35.0


def _is_release_metadata_file(path: str) -> bool:
    p = Path(path)
    parts = {part.lower() for part in p.parts}
    if parts & {"doc", "docs", "example", "examples", "fixture", "fixtures", "test", "tests", "__tests__"}:
        return False
    name = p.name.lower()
    if name in {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "package.json",
        "package-lock.json",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "gradle.properties",
    }:
        return True
    if name in {"__init__.py", "__about__.py", "_version.py", "version.py"}:
        return True
    return "version" in name and p.suffix.lower() in {".py", ".ts", ".js", ".java", ".toml", ".json", ".xml"}


def _release_metadata_priority_bonus(path: str) -> float:
    p = Path(path)
    name = p.name.lower()
    if name in {"pyproject.toml", "package.json", "cargo.toml", "pom.xml"}:
        return 70.0
    if name in {"__init__.py", "__about__.py", "_version.py", "version.py"}:
        return 70.0
    return 0.0


def _has_only_release_term_signal(matched_terms: set[str], reasons: list[str]) -> bool:
    if not matched_terms or not matched_terms <= _RELEASE_TASK_TERMS:
        return False
    return not any(
        reason.startswith((
            "direct dependency",
            "reverse dependency",
            "recall neighbor",
            "historically co-changed",
            "has related tests",
            "test for",
            "workspace match",
            "keyword phrase match",
            "matched entrypoint",
            "matched external system",
            "matched define",
            "multi-token",
        ))
        for reason in reasons
    )
