from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Literal

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]
import tomli_w
from pydantic import BaseModel, Field

from agentpack.core.modes import normalize_mode


class ProjectMilestoneConfig(BaseModel):
    id: str = ""
    title: str
    owner: str = ""
    due_date: str = ""


class ProjectOutcomeConfig(BaseModel):
    id: str = ""
    title: str
    description: str = ""
    owner: str = ""
    target_date: str = ""
    milestones: list[ProjectMilestoneConfig] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    root: str = "."
    ignore_file: str = ".agentignore"
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    display_name: str = ""
    purpose: str = ""
    audiences: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    stage: str = ""
    links: dict[str, str] = Field(default_factory=dict)
    environments: list[str] = Field(default_factory=list)
    status_stale_days: int = Field(default=14, ge=1, le=3650)
    outcomes: list[ProjectOutcomeConfig] = Field(default_factory=list)


class ContextConfig(BaseModel):
    default_budget: int = 40000
    default_mode: str = "balanced"
    max_file_tokens: int = 4000
    incremental_scan: bool = True
    full_scan_interval_seconds: int = 3600
    max_incremental_changed_files: int = 200
    min_summary_score: float = 60
    max_summary_files_lite: int = 15
    max_summary_files_balanced: int = 40
    max_summary_files_deep: int = 0
    include_tests: bool = True
    include_configs: bool = True
    include_receipts: bool = True
    broad_context: str = "auto"
    broad_context_budget_pct: int = 35
    max_module_summaries: int = 30
    max_inventory_files: int = 250
    memory_feedback: str = "auto"
    memory_boost_weight: float = 12.0


class LiteContextConfig(BaseModel):
    budget: int = 8000
    max_selected_files: int = 12
    max_omitted_files: int = 5
    max_stubs: int = 8
    summary_chars: int = 500


class SummaryConfig(BaseModel):
    provider: str = "offline"
    schema_version: int = 2


class LearningConfig(BaseModel):
    markdown_output: str = ".agentpack/learning.md"
    daily_output: str = ".agentpack/daily-summary.md"
    skill_map_output: str = ".agentpack/skills-progress.json"
    agent_lessons_output: str = ".agentpack/agent-lessons.md"
    llm_prompt_output: str = ".agentpack/learning.prompt.md"
    pr_comment_output: str = ".agentpack/pr-learning-comment.md"
    feedback_output: str = ".agentpack/learning-feedback.jsonl"
    ranking_feedback_output: str = ".agentpack/ranking-feedback.jsonl"
    episodic_cases_output: str = ".agentpack/episodic-cases.jsonl"
    task_starts_output: str = ".agentpack/task-starts.jsonl"
    procedures_output: str = ".agentpack/procedures.jsonl"
    memory_edges_output: str = ".agentpack/memory-edges.jsonl"
    dashboard_output: str = ".agentpack/learning-dashboard.html"
    team_lessons_output: str = ".agentpack/team-lessons.md"
    provider_command: str = ""
    provider_timeout_seconds: int = 60
    concept_provider_command: str = ""
    concept_provider_timeout_seconds: int = 30
    concept_provider_required: bool = False
    inject_agent_lessons: bool = True
    max_changed_files: int = 20
    max_diff_chars_per_file: int = 1200
    max_cards: int = 5
    max_quiz_questions: int = 5
    min_groundedness_score: int = 70


class LoopConfig(BaseModel):
    enabled: bool = True
    runner: str = ""
    runner_adapter: str = ""
    runner_prompt_output: str = ".agentpack/loop_runner_prompt.md"
    max_iterations: int = 10
    verification_commands: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)
    require_verification: bool = True
    require_progress_update: bool = True
    require_clean_tree: bool = True
    auto_commit: bool = False
    auto_push: bool = False
    runner_timeout_seconds: int = 600
    verification_timeout_seconds: int = 600
    max_repeated_failures: int = 3
    risk_sensitive_globs: list[str] = Field(default_factory=list)
    risk_high_file_count: int = 20


class RuntimeConfig(BaseModel):
    pack_registry_output: str = ".agentpack/pack-registry.json"
    session_events_output: str = ".agentpack/session-events.jsonl"
    observer_events_output: str = ".agentpack/observer-events.jsonl"
    observer_brief_output: str = ".agentpack/observer-brief.md"
    max_registry_records: int = 200
    max_retrieve_chars: int = 20000
    max_output_summary_items: int = 40
    max_session_events: int = 2000
    max_episodic_cases: int = 1000


class HandoffConfig(BaseModel):
    max_patch_bytes: int = Field(default=20 * 1024 * 1024, gt=0)


class HooksConfig(BaseModel):
    task_switch_detection: bool = True
    task_switch_min_terms: int = 1
    blocking_task_refresh: bool = False


class SkillsConfig(BaseModel):
    paths: list[str] = Field(default_factory=lambda: [
        "skills",
        ".claude-plugin",
        ".claude/skills",
        "~/.claude/skills",
        "~/.codex/skills",
        "~/.agents/skills",
        ".agentpack/skills",
        ".cursor/rules",
    ])
    max_selected: int = 3
    always_recommend: list[str] = Field(default_factory=lambda: ["karpathy-guidelines"])
    allow_external_side_effects: bool = False


class AgentConfig(BaseModel):
    output: str
    patch_claude_md: bool = False


class AgenticConfig(BaseModel):
    llm_structured_format: str = "auto"
    enforce_llm_toon: bool = True
    toon_fallback_when_larger: bool = True


class AgentsConfig(BaseModel):
    claude: AgentConfig = Field(
        default_factory=lambda: AgentConfig(
            output=".agentpack/context.claude.md",
            patch_claude_md=True,
        )
    )
    generic: AgentConfig = Field(
        default_factory=lambda: AgentConfig(output=".agentpack/context.md")
    )


class ScoringWeights(BaseModel):
    """Configurable scoring weights. All values are additive points."""
    modified: float = 100
    staged: float = 90
    filename_keyword: float = 80
    symbol_keyword: float = 70
    content_keyword_per_hit: float = 10
    content_keyword_max: float = 60
    direct_dep: float = 50
    reverse_dep: float = 40
    related_test: float = 35
    config_file: float = 25
    knowledge_file: float = 30
    implementation_role: float = 35
    cross_layer_related: float = 30
    co_changed: float = 28
    recall_neighbor: float = 24
    workspace_match: float = 32
    weak_filename_match_penalty: float = -45
    recently_modified: float = 20
    churn_high: float = 15   # file appears in top 10% by churn
    large_unrelated_penalty: float = -50
    ignored_penalty: float = -100


class ArchitectureSelectorConfig(BaseModel):
    entity_types: list[str] = Field(default_factory=list)
    path_globs: list[str] = Field(default_factory=list)
    qualified_names: list[str] = Field(default_factory=list)
    qualified_name_contains: list[str] = Field(default_factory=list)


class ArchitectureInvariantConfig(BaseModel):
    id: str
    kind: Literal["forbid_edge", "require_test", "require_consumer_update"] = "forbid_edge"
    enforcement: Literal["block", "warn"] = "warn"
    description: str = ""
    owner: str = ""
    enabled: bool = True
    edge_types: list[str] = Field(default_factory=lambda: ["imports"])
    min_confidence: Literal["structured", "best_effort", "file_level", "unavailable"] = "best_effort"
    source: ArchitectureSelectorConfig = Field(default_factory=ArchitectureSelectorConfig)
    target: ArchitectureSelectorConfig = Field(default_factory=ArchitectureSelectorConfig)


class ArchitectureConfig(BaseModel):
    cache_dir: str = ".agentpack/architecture"
    enabled: bool = True
    policy_mode: Literal["off", "warn", "enforce"] = "warn"
    baseline_path: str = ".agentpack/architecture-baseline.json"
    max_growth_pct: float = Field(default=25.0, ge=0.0, le=10000.0)
    max_quality_regression_pct: float = Field(default=5.0, ge=0.0, le=100.0)
    max_build_time_multiplier: float = Field(default=2.0, ge=1.0, le=100.0)
    invariant: list[ArchitectureInvariantConfig] = Field(default_factory=list)


class Config(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    context_lite: LiteContextConfig = Field(default_factory=LiteContextConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    handoff: HandoffConfig = Field(default_factory=HandoffConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    agentic: AgenticConfig = Field(default_factory=AgenticConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    scoring: ScoringWeights = Field(default_factory=ScoringWeights)
    architecture: ArchitectureConfig = Field(default_factory=ArchitectureConfig)


DEFAULT_CONFIG = Config()

CONFIG_TEMPLATE = """\
[project]
display_name = ""
purpose = ""
audiences = []
owners = []
stage = ""
links = {}
environments = []
status_stale_days = 14
# Restrict packing to these glob patterns (empty = all files).
# Example: include_globs = ["app/**", "packages/core/**"]
include_globs = []
# Always exclude these patterns on top of .agentignore.
# Example: exclude_globs = ["migrations/**", "generated/**", "snapshots/**"]
exclude_globs = []

[context]
default_budget = 40000   # token budget per pack
default_mode = "balanced"  # lite | balanced | deep
max_file_tokens = 4000   # files larger than this are summarised, not inlined
incremental_scan = true  # reuse previous snapshot and re-hash only dirty paths when safe
full_scan_interval_seconds = 3600  # periodic correctness backstop
max_incremental_changed_files = 200  # fall back to full scan above this many dirty paths
min_summary_score = 60   # unchanged summary files below this score are excluded
max_summary_files_lite = 15      # 0 = no cap
max_summary_files_balanced = 40  # 0 = no cap
max_summary_files_deep = 0       # deep mode stays uncapped
include_tests = true
include_configs = true
include_receipts = true
broad_context = "auto"       # auto | off | on
broad_context_budget_pct = 35
max_module_summaries = 30
max_inventory_files = 250
memory_feedback = "auto"     # auto | off
memory_boost_weight = 12.0

[context_lite]
budget = 8000
max_selected_files = 12
max_omitted_files = 5
max_stubs = 8
summary_chars = 500

[summary]
provider = "offline"
schema_version = 2

[learning]
markdown_output = ".agentpack/learning.md"
daily_output = ".agentpack/daily-summary.md"
skill_map_output = ".agentpack/skills-progress.json"
agent_lessons_output = ".agentpack/agent-lessons.md"
llm_prompt_output = ".agentpack/learning.prompt.md"
pr_comment_output = ".agentpack/pr-learning-comment.md"
feedback_output = ".agentpack/learning-feedback.jsonl"
ranking_feedback_output = ".agentpack/ranking-feedback.jsonl"
episodic_cases_output = ".agentpack/episodic-cases.jsonl"
task_starts_output = ".agentpack/task-starts.jsonl"
procedures_output = ".agentpack/procedures.jsonl"
memory_edges_output = ".agentpack/memory-edges.jsonl"
dashboard_output = ".agentpack/learning-dashboard.html"
team_lessons_output = ".agentpack/team-lessons.md"
provider_command = ""
provider_timeout_seconds = 60
concept_provider_command = ""
concept_provider_timeout_seconds = 30
concept_provider_required = false
inject_agent_lessons = true
max_changed_files = 20
max_diff_chars_per_file = 1200
max_cards = 5
max_quiz_questions = 5
min_groundedness_score = 70

[runtime]
pack_registry_output = ".agentpack/pack-registry.json"
session_events_output = ".agentpack/session-events.jsonl"
observer_events_output = ".agentpack/observer-events.jsonl"
observer_brief_output = ".agentpack/observer-brief.md"
max_registry_records = 200
max_retrieve_chars = 20000
max_output_summary_items = 40
max_session_events = 2000
max_episodic_cases = 1000

[handoff]
# Maximum uncompressed Git patch size. Raise deliberately for large binary work.
max_patch_bytes = 20971520

[loop]
enabled = true
runner = ""
max_iterations = 10
verification_commands = []
require_verification = true
require_progress_update = true
require_clean_tree = true
auto_commit = false
auto_push = false
runner_timeout_seconds = 600
verification_timeout_seconds = 600
max_repeated_failures = 3

[hooks]
# Prompt hooks only stay active when .agentpack/task.md has a real task.
# They can still detect a clearly different coding prompt and update task.md.
task_switch_detection = true
task_switch_min_terms = 1
# Opt in if you want prompt-submit hooks to block for a refresh when context is stale.
blocking_task_refresh = false

[skills]
# Skill/rule sources used by `agentpack route` and MCP `route_task`.
paths = ["skills", ".claude-plugin", ".claude/skills", "~/.claude/skills", "~/.codex/skills", "~/.agents/skills", ".agentpack/skills", ".cursor/rules"]
max_selected = 3
always_recommend = ["karpathy-guidelines"]
allow_external_side_effects = false

[agentic]
llm_structured_format = "auto"
enforce_llm_toon = true
toon_fallback_when_larger = true

[scoring]
# Scoring weights — higher wins budget allocation.
# Tune these to make agentpack favour your team's file layout.
modified              = 100
staged                = 90
filename_keyword      = 80
symbol_keyword        = 70
content_keyword_per_hit = 10
content_keyword_max   = 60
direct_dep            = 50
reverse_dep           = 40
related_test          = 35
config_file           = 25
knowledge_file        = 30
implementation_role   = 35
cross_layer_related   = 30
co_changed            = 28
recall_neighbor       = 24
workspace_match       = 32
weak_filename_match_penalty = -45
recently_modified     = 20
churn_high            = 15
large_unrelated_penalty = -50
ignored_penalty       = -100

[architecture]
cache_dir = ".agentpack/architecture"
enabled = false
policy_mode = "warn"  # off | warn | enforce
baseline_path = ".agentpack/architecture-baseline.json"
max_growth_pct = 25.0
max_quality_regression_pct = 5.0
max_build_time_multiplier = 2.0

# Example:
# [[architecture.invariant]]
# id = "no-public-internal-imports"
# description = "Public modules must not import internal implementation modules."
# owner = "platform"
# kind = "forbid_edge"
# enforcement = "warn"
# edge_types = ["imports"]
# min_confidence = "best_effort"
# source = { path_globs = ["src/public/**"] }
# target = { path_globs = ["src/internal/**"] }
"""


def config_path(root: Path) -> Path:
    return root / ".agentpack" / "config.toml"


def load_config(root: Path) -> Config:
    path = config_path(root)
    if not path.exists():
        return DEFAULT_CONFIG
    try:
        with path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        cfg = Config.model_validate(data)
        cfg.context.default_mode = normalize_mode(cfg.context.default_mode)
        return cfg
    except Exception:
        import warnings
        warnings.warn(
            f"Failed to parse {path} — using defaults. Fix or delete the file.",
            stacklevel=2,
        )
        return DEFAULT_CONFIG


def save_config(cfg: Config, root: Path) -> None:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump()
    with path.open("wb") as f:
        tomli_w.dump(data, f)
