from __future__ import annotations

from agentpack.dashboard.models import (
    BenchmarkSummary,
    ContextHealth,
    DashboardSnapshot,
    LearningArtifact,
    LearningWeakSpot,
    LoopSummary,
    McpHealth,
    McpRegistration,
    ObserverInsightRow,
    ObserverSummary,
    ProjectInfo,
    SelectedFileRow,
    SkillDomainSummary,
    SkillInventoryRow,
    SkillInventorySourceSummary,
    SkillRow,
    SkillSection,
    SkillsInventorySummary,
    SuggestedAction,
    TaskInfo,
    TaskMapFileRow,
)
from agentpack.dashboard.renderers import render_dashboard_html


def test_render_dashboard_html_contains_core_sections() -> None:
    html = render_dashboard_html(
        DashboardSnapshot(
            generated_at="2026-06-10T10:30:00Z",
            project=ProjectInfo(name="repo", path="/tmp/repo", branch="main", git_sha="abc123"),
            task=TaskInfo(text="fix auth", state="in_progress"),
            context=ContextHealth(status="fresh", mode="balanced", packed_tokens=1200, raw_tokens=40000),
            selected_files=[SelectedFileRow(path="src/auth.py", include_mode="full", score=120)],
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
            skills=SkillSection(
                task_specific=[SkillRow(name="auth-review", confidence=0.8, status="used_helpful")]
            ),
            learning=[LearningArtifact(label="Learning notes", path=".agentpack/learning.md", exists=True)],
            learning_weak_spots=[
                LearningWeakSpot(
                    concept="caching",
                    count=2,
                    mode="quiz",
                    latest_task="Fix cache ttl bug",
                    latest_question="How should TTL invalidation behave?",
                    evidence_files=["src/cache.py"],
                )
            ],
            observer=ObserverSummary(
                events=1,
                event_types={"task_memory": 1},
                insights=[
                    ObserverInsightRow(
                        kind="counterfactual",
                        title="Prior route context missed changed files",
                        detail="A prior task changed src/cache.py without selecting it.",
                        action="Inspect src/cache.py as a hypothesis.",
                        confidence=0.62,
                        related_files=["src/cache.py"],
                        evidence=["Fix cache ttl bug"],
                    )
                ],
            ),
            benchmarks=BenchmarkSummary(averages={"selection_recall": 0.8, "skill_recall_at_3": 0.9}),
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
                remediation=["Call agentpack_readiness() from the agent host to prove live exposure."],
            ),
            loop=LoopSummary(
                exists=True,
                status="ready_to_finish",
                task="fix auth",
                iteration=1,
                max_iterations=10,
                last_verification_status="passed",
                next_action="agentpack finish --since main",
            ),
            suggested_actions=[SuggestedAction(label="Refresh context", command="agentpack pack --task auto")],
        )
    )

    assert "AgentPack Dashboard" in html
    assert "fix auth" in html
    assert "src/auth.py" in html
    assert "Task Map" in html
    assert 'href="#task-map"' in html
    assert "tests/test_auth.py" in html
    assert "retrieve_context(block_id=&quot;src__auth.py:abc123&quot;)" in html
    assert "Integrations" in html
    assert 'href="#integrations"' in html
    assert "stdio_waiting" in html
    assert "agentpack_readiness()" in html
    assert "auth-review" in html
    assert "selection_recall" in html
    assert "Guarded Loop" in html
    assert "agentpack finish --since main" in html
    assert "agentpack pack --task auto" in html
    assert 'class="topbar"' in html
    assert 'class="quality-strip"' in html
    assert "Dashboard quality summary" in html
    assert "Skill Recall" in html
    assert "0.900" in html
    assert 'class="section-header"' in html
    assert 'href="#inventory"' in html
    assert 'href="#observer"' in html
    assert "Observer" in html
    assert "Prior route context missed changed files" in html
    assert "Observer signals are hypotheses" in html
    assert 'class="table-wrap"' in html
    assert 'class="learning-list"' in html
    assert "weak spot" in html
    assert "caching" in html
    assert "How should TTL invalidation behave?" in html
    assert 'class="benchmark-grid"' in html
    assert 'class="empty-state">No recent benchmark misses.' in html
    assert "top: 54px" not in html


def test_render_dashboard_html_escapes_dynamic_content() -> None:
    html = render_dashboard_html(
        DashboardSnapshot(
            project=ProjectInfo(name="<repo>", path="/tmp/repo"),
            task=TaskInfo(text="<script>alert(1)</script>"),
        )
    )

    assert "&lt;repo&gt;" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_render_dashboard_html_uses_no_remote_assets() -> None:
    html = render_dashboard_html(DashboardSnapshot(project=ProjectInfo(name="repo", path="/tmp/repo")))

    assert "https://" not in html
    assert "http://" not in html
    assert "<script" not in html.lower()


def test_render_dashboard_html_contains_skills_inventory_without_bodies() -> None:
    description = (
        "Use for pytest failures across service, repository, and API tests where fixture setup, "
        "mock behavior, and assertion output need careful debugging without hiding useful context."
    )
    html = render_dashboard_html(
        DashboardSnapshot(
            project=ProjectInfo(name="repo", path="/tmp/repo"),
            skills_inventory=SkillsInventorySummary(
                available=True,
                total_skills=1,
                total_rules=0,
                domains=[SkillDomainSummary(name="testing", count=1)],
                sources=[
                    SkillInventorySourceSummary(
                        configured_path=".agentpack/skills",
                        resolved_path="/tmp/repo/.agentpack/skills",
                        exists=True,
                        file_count=1,
                    )
                ],
                rows=[
                    SkillInventoryRow(
                        name="pytest-debugging",
                        path=".agentpack/skills/pytest-debugging/SKILL.md",
                        source=".agentpack/skills",
                        domains=["testing"],
                        languages=["python"],
                        frameworks=["pytest"],
                        side_effect_level="command",
                        metadata_quality="explicit",
                        metadata=[
                            "domain source: explicit domains",
                            "domain confidence: 1.00",
                            f"description: {description}",
                            "task: testing",
                            "language: python",
                            "framework: pytest",
                            "triggers: pytest, fixtures, assertions",
                        ],
                        domain_confidence=1.0,
                        domain_source="explicit domains",
                    )
                ],
            ),
        )
    )

    assert "Skills Inventory" in html
    assert 'class="inventory-list"' in html
    assert 'class="inventory-card"' in html
    assert 'class="inventory-description"' in html
    assert 'class="trigger-chip"' in html
    assert "pytest-debugging" in html
    assert "testing" in html
    assert "Domain Source" in html
    assert "explicit domains" in html
    assert "Domain Confidence" in html
    assert "1.00" in html
    assert "Task" in html
    assert "Framework" in html
    assert "pytest" in html
    assert description in html
    assert description[:120] + "..." not in html
    assert ".agentpack/skills" in html
