from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_community_files_exist() -> None:
    for rel in (
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "LICENSE",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/docs.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/contributor-labels.json",
        ".github/contributor-issues.json",
        ".github/repository-topics.json",
        ".github/DISCUSSION_TEMPLATE/roadmap.yml",
        ".github/DISCUSSION_TEMPLATE/ideas.yml",
        ".github/DISCUSSION_TEMPLATE/help-wanted.yml",
        "docs/github-community-setup.md",
        "tools/github_contributor_setup.py",
    ):
        assert (ROOT / rel).exists(), rel


def test_issue_templates_include_required_fields() -> None:
    for path in (ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if path.name != "config.yml":
            assert "name:" in text, path
            assert "description:" in text, path
            assert "body:" in text, path
            assert "validations:" in text, path


def test_contributing_documents_route_json_alias() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert 'agentpack route --task "<task>" --json' in text


def test_license_file_is_mit() -> None:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert text.startswith("MIT License")
    assert "Permission is hereby granted, free of charge" in text
    assert "THE SOFTWARE IS PROVIDED \"AS IS\"" in text


def test_contributor_label_manifest_matches_docs() -> None:
    labels = json.loads((ROOT / ".github" / "contributor-labels.json").read_text(encoding="utf-8"))
    names = {label["name"] for label in labels}
    expected = {
        "good first issue",
        "help wanted",
        "first-timers-only",
        "docs",
        "documentation",
        "benchmark",
        "cli",
        "python",
        "testing",
    }
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert expected <= names
    for name in expected:
        assert f"`{name}`" in contributing


def test_repository_topics_include_discovery_targets() -> None:
    topics = set(json.loads((ROOT / ".github" / "repository-topics.json").read_text(encoding="utf-8")))
    setup = (ROOT / "docs" / "github-community-setup.md").read_text(encoding="utf-8")

    assert {"good-first-issue", "help-wanted", "first-timers-only", "developer-tools", "cli", "python"} <= topics
    assert "Good First Issue" in setup
    assert "First Contributions" in setup
    assert "at least three open issues" in setup
    assert "at least ten contributors" in setup
    assert "Good First Issue submission status: submitted on 2026-07-06" in setup
    assert "Your response has been recorded" in setup


def test_contributor_issue_manifest_has_first_issue_and_pin_queue() -> None:
    issues = json.loads((ROOT / ".github" / "contributor-issues.json").read_text(encoding="utf-8"))
    titles = [issue["title"] for issue in issues]
    pinned = [issue for issue in issues if issue.get("pinned")]

    assert 10 <= len(issues) <= 15
    assert len(titles) == len(set(titles))
    assert 3 <= len(pinned) <= 5
    assert all(issue["body"].count("## Acceptance criteria") == 1 for issue in issues)
    assert any("good first issue" in issue["labels"] for issue in issues)
    assert any("first-timers-only" in issue["labels"] for issue in issues)
    assert any("benchmark" in issue["labels"] for issue in issues)
    assert any("cli" in issue["labels"] for issue in issues)
    assert any("python" in issue["labels"] for issue in issues)
    assert any("testing" in issue["labels"] for issue in issues)


def test_discussion_templates_cover_requested_categories() -> None:
    names = {path.stem for path in (ROOT / ".github" / "DISCUSSION_TEMPLATE").glob("*.yml")}

    assert {"roadmap", "ideas", "help-wanted"} <= names
