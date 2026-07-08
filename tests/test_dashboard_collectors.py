from __future__ import annotations

import json

from agentpack.core.config import LoopConfig
from agentpack.core.loop_protocol import LoopCommandResult, initialize_loop, save_loop_state
from agentpack.core.mcp_runtime import McpRuntimeCheck
from agentpack.dashboard.collectors import build_project_dashboard_snapshot
from agentpack.dashboard.models import (
    ContextHealth,
    DashboardSnapshot,
    McpHealth,
    McpRegistration,
    ProjectInfo,
    SelectedFileRow,
    SkillRow,
    SkillSection,
    TaskInfo,
    TaskMapFileRow,
)
from agentpack.learning.models import LearningSession


def test_dashboard_snapshot_is_json_safe() -> None:
    snapshot = DashboardSnapshot(
        generated_at="2026-06-10T10:30:00Z",
        project=ProjectInfo(name="repo", path="/tmp/repo", branch="main", git_sha="abc123"),
        task=TaskInfo(text="fix auth", state="in_progress"),
        context=ContextHealth(status="fresh", mode="balanced", packed_tokens=1200, raw_tokens=40000),
        selected_files=[
            SelectedFileRow(
                path="src/auth.py",
                include_mode="full",
                score=120.0,
                tokens=450,
                reasons=["task keyword match"],
            )
        ],
        task_map=[
            TaskMapFileRow(
                path="src/auth.py",
                kind="selected",
                risk_level="medium",
                why_selected=["modified"],
                tests_to_run=["tests/test_auth.py"],
                may_break=["reverse dependents: src/api.py"],
                retrieve_ref="src__auth.py:abc123",
            )
        ],
        mcp_health=McpHealth(
            status="healthy",
            runtime_status="stdio_waiting",
            runtime_ok=True,
            runtime_detail="agentpack mcp started and waited for MCP stdio",
            registered=True,
            registrations=[
                McpRegistration(
                    scope="Codex",
                    path="/Users/example/.codex/config.toml",
                    status="present",
                    detail="agentpack server registered.",
                )
            ],
            expected_tools=["readiness", "get_context"],
        ),
        skills=SkillSection(
            task_specific=[
                SkillRow(
                    name="pytest-debugging",
                    path="skills/pytest-debugging/SKILL.md",
                    confidence=0.86,
                    score=93.0,
                    side_effect_level="command",
                    status="used_helpful",
                    reasons=["test task match"],
                )
            ]
        ),
    )

    payload = snapshot.model_dump(mode="json")

    assert payload["schema_version"] == 1
    assert payload["project"]["name"] == "repo"
    assert payload["selected_files"][0]["path"] == "src/auth.py"
    assert payload["task_map"][0]["risk_level"] == "medium"
    assert payload["mcp_health"]["status"] == "healthy"
    assert payload["mcp_health"]["registrations"][0]["scope"] == "Codex"
    assert payload["skills"]["task_specific"][0]["status"] == "used_helpful"


def test_project_dashboard_missing_agentpack_has_empty_states(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(
        "agentpack.dashboard.collectors.check_mcp_runtime",
        lambda **_kwargs: McpRuntimeCheck(
            status="command_missing",
            ok=False,
            detail="agentpack command not found on PATH",
        ),
    )
    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert snapshot.project.name == tmp_path.name
    assert snapshot.context.status == "missing"
    assert snapshot.mcp_health.status == "missing"
    assert any(action.command == "agentpack init --yes" for action in snapshot.suggested_actions)


def test_project_dashboard_reads_pack_metadata_and_metrics(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agentpack.dashboard.collectors.check_mcp_runtime",
        lambda **_kwargs: McpRuntimeCheck(
            status="stdio_waiting",
            ok=True,
            detail="agentpack mcp started and waited for MCP stdio",
        ),
    )
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"agentpack": {"command": "agentpack", "args": ["mcp"]}}}),
        encoding="utf-8",
    )
    (agentpack / "task.md").write_text("fix auth token expiry\n", encoding="utf-8")
    (agentpack / "pack_metadata.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-06-10T10:30:00Z",
                "task": "fix auth token expiry",
                "mode": "balanced",
                "token_estimate": 1450,
                "raw_tokens": 40000,
                "saving_pct": 96.3,
                "selected_files_meta": [
                    {
                        "path": "src/auth/token.py",
                        "mode": "full",
                        "score": 120,
                        "tokens": 450,
                        "reasons": ["task keyword match", "related test"],
                        "symbols": [
                            {
                                "name": "refresh_token",
                                "kind": "function",
                                "start_line": 12,
                                "end_line": 24,
                                "signature": "def refresh_token(user_id: str) -> Token",
                                "summary": "Refreshes an expired auth token.",
                                "node_id": "node:refresh-token",
                                "signature_hash": "sig123",
                                "source_hash": "filehash",
                            }
                        ],
                    }
                ],
                "task_map": {
                    "files": [
                        {
                            "path": "src/auth/token.py",
                            "kind": "selected",
                            "risk_level": "high",
                            "why_selected": ["task keyword match"],
                            "tests_to_run": ["tests/test_token.py"],
                            "may_break": ["reverse dependents: src/api.py"],
                            "retrieve_ref": "src__auth__token.py:abc123",
                        }
                    ]
                },
                "freshness": {"status": "fresh"},
            }
        ),
        encoding="utf-8",
    )
    (agentpack / "metrics.jsonl").write_text(
        json.dumps({"selection_recall": 0.8, "selection_token_precision": 0.5}) + "\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert snapshot.task.text == "fix auth token expiry"
    assert snapshot.context.status == "fresh"
    assert snapshot.context.packed_tokens == 1450
    assert snapshot.context.raw_tokens == 40000
    assert snapshot.selected_files[0].path == "src/auth/token.py"
    assert snapshot.selected_files[0].symbols[0].name == "refresh_token"
    assert snapshot.selected_files[0].symbols[0].start_line == 12
    assert snapshot.task_map[0].path == "src/auth/token.py"
    assert snapshot.task_map[0].risk_level == "high"
    assert snapshot.mcp_health.status == "healthy"
    assert snapshot.mcp_health.registered is True
    assert "readiness" in snapshot.mcp_health.expected_tools
    assert snapshot.benchmarks.averages["selection_recall"] == 0.8


def test_project_dashboard_indexes_sibling_agentpack_projects(tmp_path) -> None:
    current = tmp_path / "current"
    sibling = tmp_path / "sibling"
    for project in (current, sibling):
        (project / ".agentpack").mkdir(parents=True)
    (current / ".agentpack" / "task.md").write_text("fix current task\n", encoding="utf-8")
    (current / ".agentpack" / "pack_metadata.json").write_text(
        json.dumps(
            {
                "task": "fix current task",
                "token_estimate": 100,
                "raw_tokens": 1000,
                "saving_pct": 90,
                "selected_files_meta": [{"path": "src/current.py"}],
                "freshness": {"status": "fresh"},
            }
        ),
        encoding="utf-8",
    )
    (sibling / ".agentpack" / "task.md").write_text("fix sibling task\n", encoding="utf-8")
    (sibling / ".agentpack" / "pack_metadata.json").write_text(
        json.dumps(
            {
                "task": "fix sibling task",
                "token_estimate": 200,
                "raw_tokens": 2000,
                "saving_pct": 90,
                "selected_files_meta": [{"path": "src/sibling.py"}, {"path": "tests/test_sibling.py"}],
                "freshness": {"status": "stale", "reason": "task changed"},
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(current)
    projects = {row.name: row for row in snapshot.project_index.projects}

    assert snapshot.project_index.project_count == 2
    assert snapshot.project_index.total_raw_tokens == 3000
    assert snapshot.project_index.total_packed_tokens == 300
    assert snapshot.project_index.estimated_saved_tokens == 2700
    assert projects["current"].current is True
    assert projects["current"].selected_files_count == 1
    assert projects["sibling"].context_status == "stale"
    assert projects["sibling"].selected_files_count == 2
    assert projects["sibling"].open_command == f"cd {sibling} && agentpack dashboard --open"


def test_project_dashboard_reads_task_memory_events(tmp_path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "session-events.jsonl").write_text(
        json.dumps(
            {
                "type": "task_memory",
                "task": "Fix cache ttl bug",
                "stage": "finish",
                "status": "done",
                "branch": "main",
                "git_sha": "abcdef1234567890",
                "concepts": ["caching"],
                "changed_files": ["src/cache.py"],
                "selected_files": ["src/cache.py"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert snapshot.learning_memories[0].task == "Fix cache ttl bug"
    assert snapshot.learning_memories[0].concepts == ["caching"]


def test_project_dashboard_reads_review_runs(tmp_path) -> None:
    agentpack = tmp_path / ".agentpack"
    run_dir = agentpack / "reviews" / "pr-42" / "20260707T120000-abcd1234"
    run_dir.mkdir(parents=True)
    (run_dir / "understanding.toon").write_text("@root review_understanding\n", encoding="utf-8")
    (run_dir / "preflight.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-07T12:00:00Z",
                "review_context": "review auth PR",
                "review": {
                    "run_id": "20260707T120000-abcd1234",
                    "branch_prefix": "pr-42",
                    "scaffold": "strict",
                    "target": {"number": 42, "url": "https://github.com/acme/repo/pull/42"},
                },
                "diff": {"source": "pr-target", "changed_files_count": 5},
                "paths": {
                    "run_dir": ".agentpack/reviews/pr-42/20260707T120000-abcd1234",
                    "understanding_canonical_output": ".agentpack/reviews/pr-42/20260707T120000-abcd1234/understanding.toon",
                    "findings_canonical_output": ".agentpack/reviews/pr-42/20260707T120000-abcd1234/findings.toon",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)
    review = snapshot.review_runs[0]

    assert review.run_id == "20260707T120000-abcd1234"
    assert review.target_number == 42
    assert review.changed_files_count == 5
    assert review.status == "understanding_ready"
    assert review.resume_command == "agentpack review --resume 20260707T120000-abcd1234"
    assert any(action.command == review.resume_command for action in snapshot.suggested_actions)


def test_project_dashboard_suggests_review_when_no_runs_exist(tmp_path) -> None:
    (tmp_path / ".agentpack").mkdir()

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert any(action.command == "agentpack review --pr <number>" for action in snapshot.suggested_actions)


def test_project_dashboard_reads_observer_summary(tmp_path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cache.py").write_text("CACHE = {}\n", encoding="utf-8")
    (agentpack / "task.md").write_text("Fix cache ttl bug\n", encoding="utf-8")
    (agentpack / "observer-events.jsonl").write_text(
        json.dumps(
            {
                "type": "task_memory",
                "timestamp": "2026-07-03T00:00:00Z",
                "task": "Fix cache ttl bug",
                "payload": {
                    "changed_files": ["src/cache.py"],
                    "selected_files": [],
                    "selected_misses": ["src/cache.py"],
                    "tests": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert snapshot.observer.events == 1
    assert snapshot.observer.event_types["task_memory"] == 1
    assert snapshot.observer.insights
    assert snapshot.observer.insights[0].kind in {"counterfactual", "route_prior", "test_gap"}


def test_project_dashboard_summarizes_learning_weak_spots(tmp_path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    queued = LearningSession(
        task="Fix cache ttl bug",
        request="quiz me on last task",
        mode="quiz",
        topic="Cache Correctness",
        question="How should TTL invalidation behave?",
        expected_points=["TTL expires stale entries"],
        evidence_files=["src/cache.py"],
        concepts=["caching"],
    )
    agentpack.joinpath("learning-sessions.jsonl").write_text(
        json.dumps(queued.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert snapshot.learning_weak_spots[0].concept == "caching"
    assert snapshot.learning_weak_spots[0].count == 1
    assert snapshot.learning_weak_spots[0].mode == "quiz"
    assert snapshot.learning_weak_spots[0].latest_task == "Fix cache ttl bug"
    assert snapshot.learning_weak_spots[0].evidence_files == ["src/cache.py"]


def test_project_dashboard_summarizes_learning_prep_sessions(tmp_path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    sessions = [
        LearningSession(
            task="Fix cache ttl bug",
            request="interview me on last task",
            mode="interview",
            topic="Cache Correctness",
            question="How would you explain TTL invalidation tradeoffs?",
            expected_points=["TTL expires stale entries"],
            evidence_files=["src/cache.py"],
            concepts=["caching"],
        ),
        LearningSession(
            task="Fix auth token refresh",
            request="quiz me on last task",
            mode="quiz",
            topic="Authentication",
            question="What breaks when refresh tokens expire early?",
            status="done",
            score=90,
            evidence_files=["src/auth.py"],
            concepts=["authentication"],
        ),
    ]
    agentpack.joinpath("learning-sessions.jsonl").write_text(
        "\n".join(json.dumps(session.model_dump(mode="json")) for session in sessions) + "\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert snapshot.learning_prep.queued_count == 1
    assert snapshot.learning_prep.completed_count == 1
    assert snapshot.learning_prep.top_concepts[:2] == ["caching", "authentication"]
    assert snapshot.learning_prep.sessions[0].question == "What breaks when refresh tokens expire early?"
    assert snapshot.learning_prep.interview_command == 'agentpack learn "interview me on last task"'


def test_project_dashboard_summarizes_skill_feedback(tmp_path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "skill_feedback.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"recommended_skills": ["auth-review"], "task": "fix auth"}),
                json.dumps({"used_skills": ["auth-review"], "tests_passed": True, "user_feedback": "helpful"}),
                json.dumps({"ignored_skills": ["deploy-checklist"], "user_feedback": "ignored"}),
                json.dumps({"bad_recommendations": ["deploy-checklist"], "user_feedback": "noisy"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (agentpack / "pack_metadata.json").write_text(
        json.dumps(
            {
                "selected_skills": [
                    {
                        "skill": {
                            "name": "auth-review",
                            "path": "skills/auth-review/SKILL.md",
                            "side_effect_level": "none",
                        },
                        "confidence": 0.8,
                        "score": 80,
                        "reasons": ["task keyword match"],
                    },
                    {
                        "skill": {
                            "name": "deploy-checklist",
                            "path": "skills/deploy/SKILL.md",
                            "side_effect_level": "external",
                        },
                        "confidence": 0.7,
                        "score": 70,
                        "reasons": ["task keyword match"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    statuses = {skill.name: skill.status for skill in snapshot.skills.task_specific}
    assert statuses["auth-review"] == "used_helpful"
    assert statuses["deploy-checklist"] == "bad_recommendation"


def test_project_dashboard_caps_jsonl_rows(tmp_path) -> None:
    agentpack = tmp_path / ".agentpack"
    agentpack.mkdir()
    (agentpack / "metrics.jsonl").write_text(
        "".join(json.dumps({"selection_recall": idx / 1000}) + "\n" for idx in range(700)),
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert 0.0 < snapshot.benchmarks.averages["selection_recall"] <= 1.0


def test_project_dashboard_collects_loop_state(tmp_path) -> None:
    state = initialize_loop(tmp_path, "fix auth", LoopConfig(runner="agent", verification_commands=["pytest -q"]))
    state.status = "ready_to_finish"
    state.last_verification = LoopCommandResult(command="pytest -q", returncode=0, output_excerpt="passed")
    state.failure_class = "test_assertion"
    state.risk_review.level = "medium"
    state.last_diff.files_changed = ["src/auth.py"]
    state.acceptance_file = ".agentpack/loop_acceptance.md"
    state.handoff_file = ".agentpack/loop_handoff.md"
    (tmp_path / ".agentpack" / "loop_diagnosis.md").write_text("diagnosis\n", encoding="utf-8")
    (tmp_path / ".agentpack" / "loop_metrics.jsonl").write_text(
        json.dumps({"outcome": "ready_to_finish", "iterations": 2}) + "\n",
        encoding="utf-8",
    )
    save_loop_state(tmp_path, state)

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert snapshot.loop.exists is True
    assert snapshot.loop.status == "ready_to_finish"
    assert snapshot.loop.last_verification_status == "passed"
    assert snapshot.loop.failure_class == "test_assertion"
    assert snapshot.loop.risk_level == "medium"
    assert snapshot.loop.changed_files == ["src/auth.py"]
    assert snapshot.loop.diagnosis_file == ".agentpack/loop_diagnosis.md"
    assert snapshot.loop.acceptance_file == ".agentpack/loop_acceptance.md"
    assert snapshot.loop.runs == 1
    assert snapshot.loop.ready_runs == 1
    assert snapshot.loop.avg_iterations == 2
    assert snapshot.loop.next_action == "agentpack finish --since main"


def test_project_dashboard_collects_skills_inventory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skill = tmp_path / ".agentpack" / "skills" / "pytest-debugging" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: pytest-debugging\n"
        "domains: [quality]\n"
        "task_types: [testing]\n"
        "languages: [python]\n"
        "frameworks: [pytest]\n"
        "---\n\n"
        "Use for pytest failures.\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    assert snapshot.skills_inventory.total_skills == 1
    assert snapshot.skills_inventory.total_rules == 0
    assert snapshot.skills_inventory.domains[0].name == "quality"
    assert snapshot.skills_inventory.rows[0].name == "pytest-debugging"
    assert snapshot.skills_inventory.rows[0].domains == ["quality"]
    assert snapshot.skills_inventory.rows[0].metadata_quality == "explicit"
    assert snapshot.skills_inventory.rows[0].domain_confidence == 1.0
    assert snapshot.skills_inventory.rows[0].domain_source == "explicit domains"
    assert snapshot.skills_inventory.index_refreshed is True


def test_project_dashboard_infers_skill_inventory_domains_with_bm25(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skill = tmp_path / ".agentpack" / "skills" / "academic-cv-builder" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    description = "Use this when creating academic CVs, resumes, cover letters, and job application portfolios."
    skill.write_text(
        "# Academic CV Builder\n\n"
        f"{description}\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    row = snapshot.skills_inventory.rows[0]
    assert row.name == "Academic CV Builder"
    assert row.domains == ["career"]
    assert row.metadata_quality == "inferred"
    assert "domain source: bm25" in row.metadata
    assert "inferred domains: career" in row.metadata
    assert 0.0 < row.domain_confidence < 1.0
    assert any(item.startswith("description:") for item in row.metadata)
    assert f"description: {description}" in row.metadata
    assert snapshot.skills_inventory.uncategorized_count == 0


def test_project_dashboard_filters_generic_inferred_skill_triggers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skill = tmp_path / ".agentpack" / "skills" / "graphql-architect" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: graphql-architect\n"
        "description: Use when designing GraphQL schemas, implementing Apollo Federation, or building real-time subscriptions with DataLoader.\n"
        "---\n\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    metadata = snapshot.skills_inventory.rows[0].metadata
    trigger_line = next(item for item in metadata if item.startswith("triggers:"))
    triggers = [item.strip() for item in trigger_line.removeprefix("triggers:").split(",")]
    assert "graphql-architect" not in trigger_line
    assert "graphql" in trigger_line
    assert "architect" not in trigger_line
    assert "building" not in trigger_line
    assert "designing" not in trigger_line
    assert "implementing" not in triggers
    assert "time-subscription" not in trigger_line
    assert "apollo" in trigger_line
    assert "dataloader" in trigger_line
    assert "graphql-schema" in triggers or "schema" in triggers
    assert "schemas" not in trigger_line
    assert triggers.count("schema") <= 1


def test_project_dashboard_does_not_surface_broad_modifier_triggers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skill = tmp_path / ".agentpack" / "skills" / "golang-patterns" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: golang-patterns\n"
        "description: Idiomatic Go patterns, best practices, and conventions for building robust, efficient, and maintainable Go applications.\n"
        "---\n\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    metadata = snapshot.skills_inventory.rows[0].metadata
    trigger_line = next(item for item in metadata if item.startswith("triggers:"))
    triggers = [item.strip() for item in trigger_line.removeprefix("triggers:").split(",")]
    assert "idiomatic-go" in trigger_line
    assert "go-pattern" in trigger_line
    assert "best-practice" in trigger_line
    assert "robust" not in triggers
    assert "building" not in triggers
    assert "maintainable" not in triggers
    assert "application" not in triggers


def test_project_dashboard_prefers_skill_trigger_keyphrases(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    base = tmp_path / ".agentpack" / "skills"
    career = base / "career-changer-translator" / "SKILL.md"
    review = base / "code-reviewer" / "SKILL.md"
    career.parent.mkdir(parents=True)
    review.parent.mkdir(parents=True)
    career.write_text(
        "---\n"
        "name: Career Changer Translator\n"
        "description: Translate skills from one industry to another, identify transferable skills\n"
        "---\n\n",
        encoding="utf-8",
    )
    review.write_text(
        "---\n"
        "name: code-reviewer\n"
        "description: Analyzes code diffs and files to identify bugs, security vulnerabilities (SQL injection, XSS, insecure deserialization), code smells, N+1 queries, naming issues, and architectural concerns, then produces a structured review report with prioritized, actionable feedback. Use when reviewing pull requests, conducting code quality audits, identifying refactoring opportunities, or checking for security issues. Invoke for PR reviews, code quality checks, refactoring suggestions, review code, code quality.\n"
        "---\n\n",
        encoding="utf-8",
    )

    snapshot = build_project_dashboard_snapshot(tmp_path)

    metadata_by_name = {row.name: row.metadata for row in snapshot.skills_inventory.rows}
    career_triggers = next(item for item in metadata_by_name["Career Changer Translator"] if item.startswith("triggers:"))
    review_triggers = next(item for item in metadata_by_name["code-reviewer"] if item.startswith("triggers:"))
    assert "translate-skill" in career_triggers
    assert "transferable-skill" in career_triggers
    assert "one-industry" not in career_triggers
    assert "another" not in career_triggers
    assert "code-quality-check" in review_triggers
    assert "refactoring-suggestion" in review_triggers
    assert "security-vulnerability" in review_triggers
    assert "actionable" not in review_triggers
    assert "analyzes" not in review_triggers
