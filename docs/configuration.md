# Configuration

Configuration is intentionally file-based and local. Most projects can start with defaults, then tune ignore rules and scoring weights when ranking needs calibration.

## Configuration

`.agentpack/config.toml`:

```toml
[project]
root = "."
ignore_file = ".agentignore"

[context]
default_budget = 40000
default_mode = "balanced"  # lite | balanced | deep
max_file_tokens = 4000
min_summary_score = 60
max_summary_files_lite = 15
max_summary_files_balanced = 40
max_summary_files_deep = 0
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

[hooks]
task_switch_detection = true
task_switch_min_terms = 1
blocking_task_refresh = false

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
dashboard_output = ".agentpack/learning-dashboard.html"
team_lessons_output = ".agentpack/team-lessons.md"
provider_command = ""
provider_timeout_seconds = 60
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

[agents.claude]
output = ".agentpack/context.claude.md"
patch_claude_md = true

[agents.generic]
output = ".agentpack/context.md"
```

`broad_context = "auto"` keeps normal coding tasks compact, but adds curated
repo-wide inventory and module summaries when the task asks for review, sharing,
audit, or repository overview. Existing repos do not need to add these keys;
AgentPack uses model defaults when keys are absent.

`memory_feedback = "auto"` lets prior ranking feedback and episodic eval
outcomes provide small, receipt-backed ranking boosts. Episodic boosts are
ignored when the remembered file is missing or its recorded hash no longer
matches the current checkout. Set it to `"off"` for baseline comparisons or if
local memory becomes noisy.

`max_session_events` and `max_episodic_cases` are retention limits used by
`agentpack memory --prune`. Existing repos receive these defaults at runtime
even if their checked-in config file does not yet include the keys.

Loop automation is local and opt-in per runner:

```toml
[loop]
enabled = true
runner = ""
runner_adapter = ""
runner_prompt_output = ".agentpack/loop_runner_prompt.md"
max_iterations = 10
verification_commands = []
acceptance_checks = []
require_verification = true
require_progress_update = true
require_clean_tree = true
runner_timeout_seconds = 600
verification_timeout_seconds = 600
max_repeated_failures = 3
risk_sensitive_globs = []
risk_high_file_count = 20
```

`runner` is intentionally empty by default. Set it to a local command such as
`claude < .agentpack/context.claude.md`, then set `verification_commands` to the
smallest commands that prove the task. During `agentpack work --run`, AgentPack
captures loop phases, runner JSON contracts, dirty-diff snapshots, verification
results, risk reviews, rollback patches, acceptance summaries, handoff notes,
and failure diagnoses under `.agentpack/`. `runner_adapter` may be `claude`,
`codex`, or `cursor`; the adapter only resolves a local command when the
matching executable is present. `acceptance_checks` asks the runner to report
semantic pass/fail evidence in its final JSON. `risk_sensitive_globs` and
`risk_high_file_count` tune the high-risk finish gate for a repo.

Hook defaults stay lightweight. Prompt-submit hooks do nothing until
`.agentpack/task.md` contains a real task, and `blocking_task_refresh = false`
keeps refresh work off the prompt path unless you explicitly opt in.

---

## Configurable scoring weights

```toml
# .agentpack/config.toml
[scoring]
modified                  = 100
staged                    = 90
filename_keyword          = 80
symbol_keyword            = 70
content_keyword_per_hit   = 10
content_keyword_max       = 60
direct_dep                = 50
reverse_dep               = 40
related_test              = 35
knowledge_file            = 30   # DECISIONS.md, ADR-*.md, ARCHITECTURE.md, docs/adr/ etc.
config_file               = 25
recently_modified         = 20
churn_high                = 15   # top 10% by commit frequency
large_unrelated_penalty   = -50
ignored_penalty           = -100
```

---

## Learning

```toml
[learning]
markdown_output = ".agentpack/learning.md"
daily_output = ".agentpack/daily-summary.md"
skill_map_output = ".agentpack/skills-progress.json"
agent_lessons_output = ".agentpack/agent-lessons.md"
llm_prompt_output = ".agentpack/learning.prompt.md"
pr_comment_output = ".agentpack/pr-learning-comment.md"
feedback_output = ".agentpack/learning-feedback.jsonl"
dashboard_output = ".agentpack/learning-dashboard.html"
team_lessons_output = ".agentpack/team-lessons.md"
provider_command = ""
provider_timeout_seconds = 60
inject_agent_lessons = true
max_changed_files = 20
max_diff_chars_per_file = 1200
max_cards = 5
max_quiz_questions = 5
min_groundedness_score = 70
```

These settings control local learning output size, destinations, future-agent
context injection, and the quality warning threshold. The quality score includes
claim-level citation coverage: generated summaries, decisions, risks, tests,
lessons, cards, topics, and skill evidence should cite source files with line
anchors where available. Learning artifacts are local by default: no hosted
service is called, diffs are bounded, and secret redaction runs before diff text
is used. `provider_command` is opt-in and runs a local JSON-in/JSON-out command with the bounded report payload on
stdin.
Feedback-aware skill memory and practice drills are stored locally in
`skill_map_output` and `feedback_output`; shared team learning should export
only selected lessons or taxonomy files, not personal skill history. Use
`dashboard_output` for the local IDE/browser review surface and
`team_lessons_output` for shareable lessons that are safe to discuss in review
without exposing the developer's personal skill map.

---

## .agentignore

Works like `.gitignore`. Default rules exclude:

- `node_modules/`, `.venv/`, `__pycache__/`
- `dist/`, `build/`, `.next/`, `coverage/`
- `*.lock`, `*.log`, `*.min.js`, `*.map`
- `.env`, `.env.*`, `*.pem`, `*.key`
- `*.csv`, `*.jsonl`, `*.parquet`

Use automation before hand-tuning ignore rules:

```bash
agentpack ignore suggest
agentpack ignore apply          # dry-run
agentpack ignore apply --yes    # write reviewed suggestions
agentpack diagnose-selection
```

---

## Git integration

```
.agentignore              ✓ commit
.agentpack/config.toml    ✓ commit
.agentpack/cache/         ✓ commit if --share-cache (recommended for teams)
.agentpack/.gitignore     ✗ gitignored
.agentpack/snapshots/     ✗ gitignored
.agentpack/context.*      ✗ gitignored
.agentpack/task.md        ✗ gitignored (local current task)
.agentpack/learning-sessions.jsonl ✗ gitignored (local coach queue)
.agentpack/learning.md    ✗ gitignored (local learning notes)
.agentpack/daily-summary.md ✗ gitignored (local daily rollup)
.agentpack/skills-progress.json ✗ gitignored (local skill evidence)
.agentpack/agent-lessons.md ✗ gitignored (future-agent lessons)
.agentpack/learning.prompt.md ✗ gitignored (optional LLM prompt)
.agentpack/pr-learning-comment.md ✗ gitignored (optional PR summary)
.agentpack/learning-dashboard.html ✗ gitignored (optional local dashboard)
.agentpack/team-lessons.md ✗ gitignored (optional shared lesson export)
.agentpack/learning-feedback.jsonl ✗ gitignored (local feedback)
.agent/skills/agentpack/  ✗ gitignored (generated Antigravity context)
```

---

## File scoring

| Signal | Points |
|--------|-------:|
| Modified file | +100 |
| Staged file | +90 |
| Filename/path keyword match | +80 |
| Symbol keyword match | +70 |
| Content keyword match | +60 |
| Direct dependency of changed file | +50 |
| Reverse dependency | +40 |
| Has related tests | +35 |
| Knowledge/architecture doc (DECISIONS.md, ADR-*.md, ARCHITECTURE.md, docs/adr/, docs/decisions/, docs/rfcs/) | +30 |
| Config file | +25 |
| Recently modified | +20 |
| High churn (top 10% by commit frequency) | +15 |
| Large unrelated file | −50 |
| Ignored/binary | −100 |

Keyword scoring uses weighted concept synonym expansion — literal task terms are strongest, normalized variants are slightly weaker, and broad concept synonyms are weaker again. "rate limiting" still expands to `throttle`, `leaky`, `bucket`, `quota`, but broad expansions no longer dominate literal task terms. Matching is token-based, so `task` does not accidentally match every `tasks.py`.

---
