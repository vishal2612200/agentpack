from __future__ import annotations

import json
import warnings
from pathlib import Path

from agentpack.analysis.repo_map import build_repo_map
from agentpack.analysis.ranking import extract_keyword_weights, score_files
from agentpack.core.cache import _cache_key, load_summary, save_summary
from agentpack.core.models import DependencyGraph, FileInfo, FileSummary
from agentpack.summaries.offline import summarize


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _fi(tmp_path: Path, rel: str, content: str = "", language: str = "python") -> FileInfo:
    path = _write(tmp_path, rel, content or "def placeholder():\n    pass\n")
    return FileInfo(
        path=rel,
        abs_path=path,
        language=language,
        size_bytes=path.stat().st_size,
        estimated_tokens=80,
        hash="h1",
        content=path.read_text(encoding="utf-8"),
    )


def test_python_role_inference_session_token_management(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "auth/session.py",
        "from jose import jwt\n\nclass SessionManager:\n    def issue_token(self):\n        return jwt.encode({})\n",
    )

    summary = summarize("auth/session.py", src, "python", "h1")

    assert summary.domain == "auth"
    assert summary.role == "session/token management"
    assert "SessionManager" in summary.defines
    assert "jwt.encode" in summary.calls
    assert "token" in summary.ranking_keywords


def test_fastapi_stripe_webhook_entrypoint_and_external_system(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "payments/stripe/webhook.py",
        "import stripe\nfrom fastapi import APIRouter\n\nrouter = APIRouter()\n\n"
        "@router.post('/webhooks/stripe')\n"
        "def handle_webhook():\n"
        "    return stripe.Webhook.construct_event()\n",
    )

    summary = summarize("payments/stripe/webhook.py", src, "python", "h1")

    assert summary.domain == "payments"
    assert summary.role == "Stripe webhook handling"
    assert "POST /webhooks/stripe" in summary.entrypoints
    assert "Stripe" in summary.external_systems


def test_django_url_and_management_command_detection(tmp_path: Path) -> None:
    urls = _write(
        tmp_path,
        "api/urls.py",
        "from django.urls import path\nfrom .views import user_list\n"
        "urlpatterns = [path('users/', user_list)]\n",
    )
    command = _write(
        tmp_path,
        "app/management/commands/send_digest.py",
        "from django.core.management.base import BaseCommand\n\nclass Command(BaseCommand):\n    pass\n",
    )

    url_summary = summarize("api/urls.py", urls, "python", "h1")
    command_summary = summarize("app/management/commands/send_digest.py", command, "python", "h2")

    assert any(ep.startswith("Django URL: users/") for ep in url_summary.entrypoints)
    assert "Django management command: send_digest" in command_summary.entrypoints


def test_celery_task_and_typer_click_command_detection(tmp_path: Path) -> None:
    task = _write(
        tmp_path,
        "tasks/email.py",
        "from celery import shared_task\n\n@shared_task\n"
        "def send_email():\n    return None\n",
    )
    cli = _write(
        tmp_path,
        "cli.py",
        "import typer\napp = typer.Typer()\n\n@app.command('pack')\n"
        "def pack_cmd():\n    pass\n",
    )

    task_summary = summarize("tasks/email.py", task, "python", "h1")
    cli_summary = summarize("cli.py", cli, "python", "h2")

    assert "Celery task: send_email" in task_summary.entrypoints
    assert task_summary.role == "background email task"
    assert "CLI command: pack" in cli_summary.entrypoints


def test_python_summary_invalid_escape_does_not_warn(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "regex.py",
        'PATTERN = "' + "\\(" + '"\n\ndef compile_pattern():\n    return PATTERN\n',
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", SyntaxWarning)
        summary = summarize("regex.py", src, "python", "h1")

    assert "compile_pattern" in summary.defines
    assert not [warning for warning in captured if issubclass(warning.category, SyntaxWarning)]


def test_js_role_inference_react_component_and_next_routes(tmp_path: Path) -> None:
    component = _write(
        tmp_path,
        "components/LoginForm.tsx",
        "export default function LoginForm() { return <form /> }\n",
    )
    route = _write(
        tmp_path,
        "src/app/api/auth/route.ts",
        "export async function POST(req: Request) { return fetch('/session') }\n",
    )
    page = _write(tmp_path, "src/app/dashboard/page.tsx", "export default function Page() { return <main /> }\n")

    component_summary = summarize("components/LoginForm.tsx", component, "typescript", "h1")
    route_summary = summarize("src/app/api/auth/route.ts", route, "typescript", "h2")
    page_summary = summarize("src/app/dashboard/page.tsx", page, "typescript", "h3")

    assert component_summary.domain == "frontend/auth"
    assert component_summary.role == "login form UI component"
    assert "React component: LoginForm" in component_summary.entrypoints
    assert "POST /api/auth" in route_summary.entrypoints
    assert "HTTP" in route_summary.external_systems
    assert "React page: src/app/dashboard/page.tsx" in page_summary.entrypoints


def test_js_role_inference_extracts_frontend_api_literals(tmp_path: Path) -> None:
    client = _write(
        tmp_path,
        "src/app/signals/signals-client.tsx",
        "export function SignalsClient() {\n"
        "  fetch('/api/signals/stats')\n"
        "  fetch(`/api/signals/history?limit=${limit}`)\n"
        "  return null\n"
        "}\n",
    )

    summary = summarize("src/app/signals/signals-client.tsx", client, "typescript", "h1")

    assert "API call: /api/signals/stats" in summary.calls
    assert "API call: /api/signals/history" in summary.calls


def test_go_summary_extracts_imports_symbols_and_public_api(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "cmd/server/main.go",
        "package main\n\n"
        "import (\n"
        "    \"net/http\"\n"
        "    \"github.com/gin-gonic/gin\"\n"
        ")\n\n"
        "type Server struct {}\n\n"
        "func NewServer() *Server { return &Server{} }\n\n"
        "func (s *Server) Start() {\n"
        "    http.ListenAndServe(\":8080\", nil)\n"
        "}\n",
    )

    summary = summarize("cmd/server/main.go", src, "go", "h1")

    assert summary.language == "go"
    assert "net/http" in summary.imports
    assert "github.com/gin-gonic/gin" in summary.imports
    assert {"Server", "NewServer", "Server.Start"} <= {symbol.name for symbol in summary.symbols}
    assert "NewServer" in summary.defines
    assert "Server.Start" in summary.defines
    assert "NewServer" in summary.public_api


def test_rust_summary_extracts_imports_symbols_and_public_api(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "src/server.rs",
        "use std::collections::HashMap;\n"
        "use tokio::net::TcpListener;\n\n"
        "pub struct Server {\n"
        "    port: u16,\n"
        "}\n\n"
        "impl Server {\n"
        "    pub fn new(port: u16) -> Self {\n"
        "        Server { port }\n"
        "    }\n"
        "}\n\n"
        "pub fn build_server(port: u16) -> Server {\n"
        "    Server::new(port)\n"
        "}\n",
    )

    summary = summarize("src/server.rs", src, "rust", "h1")

    assert summary.language == "rust"
    assert summary.provider == "offline"
    assert "std" in summary.imports
    assert "tokio" in summary.imports
    assert {"Server", "Server.new", "build_server"} <= {symbol.name for symbol in summary.symbols}
    assert "build_server" in summary.defines
    assert "Server.new" in summary.defines
    assert "build_server" in summary.public_api
    assert summary.summary.splitlines()[0] == "Language: Rust"


def test_env_file_external_system_and_side_effect_detection(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "src/cache.py",
        "import os\nimport redis\nfrom pathlib import Path\n\n"
        "TTL = os.getenv('OTP_TTL_SECONDS')\n"
        "TOKEN = os.environ['AUTH_TOKEN']\n"
        "def cache_it(path):\n"
        "    data = Path(path).read_text()\n"
        "    Path('out.txt').write_text(data)\n"
        "    return redis.Redis().get('otp')\n",
    )

    summary = summarize("src/cache.py", src, "python", "h1")

    assert {"OTP_TTL_SECONDS", "AUTH_TOKEN"} <= set(summary.reads_env)
    assert "dynamic path" in summary.reads_files
    assert "out.txt" in summary.writes_files
    assert "Redis" in summary.external_systems
    assert "possible Redis read/write" in summary.side_effects


def test_summary_cache_schema_version_invalidates_old_default(tmp_path: Path) -> None:
    old = FileSummary(
        path="src/foo.py",
        hash="abc",
        language="python",
        provider="offline",
        schema_version=1,
        summary="old summary",
    )
    save_summary(tmp_path, old)

    assert load_summary(tmp_path, "src/foo.py", "abc") is None
    assert load_summary(tmp_path, "src/foo.py", "abc", schema_version=1) is not None


def test_summary_includes_naming_signals_and_keywords(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "auth/otp.py",
        "def verify_otp(code):\n"
        "    return code\n"
        "\n"
        "def handle():\n"
        "    return None\n",
    )

    summary = summarize("auth/otp.py", src, "python", "h1")

    assert any("verify_otp" in item for item in summary.naming_signals)
    assert any("handle" in item for item in summary.naming_signals)
    assert "verify" in summary.naming_keywords
    assert "otp" in summary.naming_keywords


def test_old_summary_json_loads_with_safe_defaults(tmp_path: Path) -> None:
    cache_dir = tmp_path / ".agentpack" / "cache"
    cache_dir.mkdir(parents=True)
    key = _cache_key("src/foo.py", "abc", "offline", 1)
    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "path": "src/foo.py",
                "hash": "abc",
                "language": "python",
                "provider": "offline",
                "schema_version": 1,
                "summary": "old",
                "imports": [],
                "symbols": [],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_summary(tmp_path, "src/foo.py", "abc", schema_version=1)

    assert loaded is not None
    assert loaded.entrypoints == []
    assert loaded.reads_env == []
    assert loaded.schema_version == 1


def test_ranking_boosts_from_structured_summary_fields(tmp_path: Path) -> None:
    fi = _fi(tmp_path, "api/otp/routes.py")
    summaries = {
        fi.path: {
            "symbols": [],
            "entrypoints": ["POST /v1/otp/verify"],
            "role": "OTP verification API route",
            "domain": "otp/auth",
            "ranking_keywords": ["otp", "verify", "auth"],
            "defines": ["verify_otp"],
            "external_systems": ["Redis"],
            "side_effects": ["possible Redis read/write"],
            "reads_env": ["OTP_TTL_SECONDS"],
        }
    }

    otp_score = score_files(
        [fi],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("fix OTP verify 422"),
        summaries=summaries,
    )[0]
    redis_score = score_files(
        [fi],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("debug Redis cache issue"),
        summaries=summaries,
    )[0]
    env_score = score_files(
        [fi],
        changed_paths=set(),
        staged_paths=set(),
        recently_modified=[],
        dep_graph={},
        keywords=extract_keyword_weights("environment variable missing"),
        summaries=summaries,
    )[0]

    assert any(reason == "matched entrypoint: POST /v1/otp/verify" for reason in otp_score[2])
    assert any(reason == "matched external system: Redis" for reason in redis_score[2])
    assert any(reason == "matched env read: OTP_TTL_SECONDS" for reason in env_score[2])
    assert otp_score[1] > 0


def test_repo_map_includes_domain_role_and_entrypoint(tmp_path: Path) -> None:
    fi = _fi(tmp_path, "auth/routes.py")
    repo_map = build_repo_map(
        files=[fi],
        scored=[(fi, 100.0, ["matched entrypoint: POST /v1/otp/verify"])],
        summaries={
            fi.path: {
                "domain": "otp/auth",
                "role": "OTP verification API route",
                "entrypoints": ["POST /v1/otp/verify"],
            }
        },
        dep_graph=DependencyGraph(),
        changed_paths=set(),
        budget_tokens=200,
    )

    assert "otp/auth / OTP verification API route" in repo_map
    assert "auth/routes.py: OTP verification API route; POST /v1/otp/verify" in repo_map
