from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    ROOT / "README.md",
    ROOT / "docs/architecture.md",
    ROOT / "docs/commands.md",
    ROOT / "docs/configuration.md",
    ROOT / "docs/integrations.md",
    ROOT / "docs/agent-plugins.md",
    ROOT / "docs/codex-plugin.md",
    ROOT / "docs/benchmarking.md",
    ROOT / "docs/demo.md",
    ROOT / "docs/development.md",
    ROOT / "docs/limitations.md",
]


LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def test_root_readme_is_lean() -> None:
    line_count = len((ROOT / "README.md").read_text(encoding="utf-8").splitlines())

    assert line_count <= 300


def test_root_readme_presents_agentpack_as_one_product() -> None:
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    product_story = readme_text.split("## Get Started", 1)[0]

    assert "Make AI coding work easier to understand, verify, and continue." in readme_text
    assert (
        "AgentPack is the local, agent-neutral reliability layer for AI software development."
        in readme_text
    )
    assert 'href="docs/index.md">Technical docs</a>' in readme_text
    assert "## One Product Across the Workflow" in readme_text
    for implementation_detail in (
        "agentpack route",
        "agentpack pack",
        "agentpack review",
        "agentpack memory",
        "agentpack eval",
        "Anchor",
        "Judge",
        "Critic",
        "Actor",
    ):
        assert implementation_detail not in product_story


def test_split_docs_exist() -> None:
    for path in DOCS:
        assert path.exists(), f"missing {path}"


def test_local_markdown_links_have_targets() -> None:
    for path in DOCS + [ROOT / "benchmarks/README.md"]:
        text = path.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            if _is_external_or_fragment(target):
                continue
            target_path = target.split("#", 1)[0]
            resolved = (path.parent / target_path).resolve()
            assert resolved.exists(), f"{path} links to missing {target}"


def test_public_benchmark_claims_match_current_result() -> None:
    result_path = ROOT / "benchmarks/results/2026-07-06-public.md"
    metrics = _benchmark_metrics(result_path)
    expected_snippets = [
        str(result_path.relative_to(ROOT)),
        metrics["cases"],
        metrics["avg recall"],
        metrics["avg token precision"],
    ]
    for path in (
        ROOT / "README.md",
        ROOT / "benchmarks/README.md",
        ROOT / "docs/benchmarking.md",
        ROOT / "docs/limitations.md",
        ROOT / "docs/reduce-claude-code-token-usage.md",
        ROOT / "docs/benchmark-learnings.md",
    ):
        text = path.read_text(encoding="utf-8")
        for snippet in expected_snippets:
            assert snippet in text, f"{path} missing current benchmark claim: {snippet}"


def test_public_version_claims_match_package_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    init_text = (ROOT / "src/agentpack/__init__.py").read_text(encoding="utf-8")
    npm_text = (ROOT / "npm/package.json").read_text(encoding="utf-8")
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert f'__version__ = "{version}"' in init_text
    assert f'"version": "{version}"' in npm_text
    assert f"Alpha: `{version}`." in readme_text


def test_readme_keeps_advisory_source_of_truth_boundary() -> None:
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "Generated context, memory, observer state, and integration hints are advisory."
        in readme_text
    )
    assert (
        "Source files, diffs, tests, runtime evidence, and PR state remain the source of truth."
        in readme_text
    )


def test_technical_docs_explain_advisory_memory_timeline() -> None:
    commands_text = (ROOT / "docs/commands.md").read_text(encoding="utf-8")
    runtime_text = (ROOT / "docs/runtime-loop.md").read_text(encoding="utf-8")

    for snippet in (
        "agentpack memory --timeline",
        "agentpack eval --memory-ab",
        "timestamped rows",
        "confidence",
        "visible reason",
        "stale path flags",
        "without treating memory as source of truth",
    ):
        assert snippet in commands_text
    for snippet in (
        "AgentPack's memory graph is append-only and advisory",
        "source hash",
        "live source and tests outrank",
    ):
        assert snippet in runtime_text


def test_docs_visual_assets_cover_route_review_learn_and_memory() -> None:
    route_demo = ROOT / "docs" / "assets" / "agentpack-route-demo.svg"
    social = ROOT / "docs" / "assets" / "agentpack-social.svg"
    icon = ROOT / "docs" / "assets" / "icon.svg"
    gif = ROOT / "docs" / "assets" / "agentpack-demo.gif"
    mp4 = ROOT / "docs" / "assets" / "agentpack-demo.mp4"
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")

    assert route_demo.read_text(encoding="utf-8") == (ROOT / "assets" / "route-demo.svg").read_text(
        encoding="utf-8"
    )
    assert icon.read_text(encoding="utf-8") == (ROOT / "assets" / "icon.svg").read_text(encoding="utf-8")
    route_demo_text = route_demo.read_text(encoding="utf-8")
    social_text = social.read_text(encoding="utf-8")
    for expected in (
        "Skill recommendations",
        "agentpack review --check",
        "agentpack learn",
        "agentpack memory --timeline",
    ):
        assert expected in route_demo_text
        assert expected in social_text
    assert "logo: assets/icon.svg" in mkdocs
    assert "favicon: assets/icon.svg" in mkdocs
    assert "social_image: assets/agentpack-social.svg" in mkdocs
    assert 0 < gif.stat().st_size < 2_000_000
    assert 0 < mp4.stat().st_size < 500_000


def test_e2e_claims_stay_pending_without_public_ab_report() -> None:
    e2e_reports = [
        path
        for path in (ROOT / "benchmarks/results").glob("*-e2e-ab.md")
        if path.name != "e2e-ab-status.md"
    ]
    status = (ROOT / "benchmarks/results/e2e-ab-status.md").read_text(encoding="utf-8")
    assert e2e_reports or "No public AgentPack vs no-AgentPack E2E outcome report is published yet." in status

    public_docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "README.md",
            ROOT / "docs/benchmarking.md",
            ROOT / "docs/limitations.md",
            ROOT / "docs/reduce-claude-code-token-usage.md",
            ROOT / "benchmarks/README.md",
        )
    )
    forbidden_without_report = (
        "AgentPack reduces tool calls",
        "AgentPack saves token cost",
        "AgentPack improves task success",
        "proven task-success",
    )
    if not e2e_reports:
        for phrase in forbidden_without_report:
            assert phrase not in public_docs
        assert "e2e-ab-status.md" in public_docs


def test_platform_depth_limitations_stay_explicit() -> None:
    limitations = (ROOT / "docs/limitations.md").read_text(encoding="utf-8")
    native_status = (ROOT / "native-integrations/README.md").read_text(encoding="utf-8")

    assert "common Cargo path dependencies" in limitations
    assert "common Go module `require`/local `replace` edges" in limitations
    assert "Go has lightweight function, method, struct, and interface extraction" in limitations
    assert "all hosts remain `advisory`" in limitations
    assert "Only hosts with every required capability can be marked `enforced`" in native_status


def _is_external_or_fragment(target: str) -> bool:
    return (
        target.startswith("#")
        or target.startswith("http://")
        or target.startswith("https://")
        or target.startswith("mailto:")
    )


def _benchmark_metrics(path: Path) -> dict[str, str]:
    metrics: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- cases:"):
            metrics["cases"] = line.split(":", 1)[1].strip()
            continue
        match = re.match(r"^\| (avg recall|avg token precision) \| ([^|]+) \|$", line)
        if match:
            metrics[match.group(1)] = match.group(2).strip()
    missing = {"cases", "avg recall", "avg token precision"} - set(metrics)
    assert not missing, f"{path} missing metrics: {sorted(missing)}"
    return metrics
