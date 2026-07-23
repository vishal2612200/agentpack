from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentpack.core.models import Citation


CompetencyId = Literal[
    "product_reasoning",
    "implementation",
    "quality",
    "systems",
    "production",
    "security",
    "collaboration",
]
LearnerRole = Literal["frontend", "backend", "mobile", "platform", "general"]
TargetLevel = Literal["unspecified", "junior", "mid", "senior", "staff"]
CompetencyStatus = Literal["mastered", "developing", "needs_practice", "unassessed"]


class LearnerProfile(BaseModel):
    schema_version: Literal[1] = 1
    role: LearnerRole = "general"
    target_level: TargetLevel = "unspecified"
    updated_at: str = ""


class RubricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=500)
    rating: Literal["missing", "partial", "met"]
    evidence: str = Field(default="", max_length=1000)


class VerificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=2000)
    exit_code: int
    summary: str = Field(default="", max_length=2000)
    executed_at: str = ""


class LearningProof(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["reasoning", "artifact"]
    answer: str = Field(min_length=1, max_length=50_000)
    rubric_results: list[RubricResult] = Field(default_factory=list, max_length=50)
    artifact_paths: list[str] = Field(default_factory=list, max_length=50)
    verification_evidence: list[VerificationEvidence] = Field(default_factory=list, max_length=50)
    self_assessment: Literal["", "mastered", "developing", "needs-practice"] = ""
    evaluator: str = Field(default="host-agent", max_length=120)
    evaluated_at: str = ""


class CompetencySummary(BaseModel):
    competency_id: CompetencyId
    name: str
    status: CompetencyStatus = "unassessed"
    passing_proofs: int = 0
    verified_artifacts: int = 0
    latest_evidence: str = ""
    latest_score: int | None = None
    role_emphasis: bool = False


class LearningOptions(BaseModel):
    scope: str = "task"
    since: str | None = None
    today: bool = False
    json_output: bool = False


class LearningSourceFile(BaseModel):
    path: str
    change_kind: str
    why: str
    concepts: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class LearningCard(BaseModel):
    title: str
    body: str
    files: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class LearningTopic(BaseModel):
    title: str
    why: str
    prompt: str
    files: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    questions: list["LearningQuestion"] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class QuizQuestion(BaseModel):
    question: str
    answer: str


class LearningQuestion(BaseModel):
    mode: str
    question: str
    expected_points: list[str] = Field(default_factory=list)
    evidence_files: list[str] = Field(default_factory=list)
    difficulty: str = "medium"


class LearningProjectRef(BaseModel):
    project_id: str
    name: str
    root: str


class LearningEvidence(BaseModel):
    kind: str
    task_id: str = ""
    task: str = ""
    path: str = ""
    summary: str = ""
    observed_at: str = ""
    status: str = ""
    stale: bool = False


class LearningRecommendationTopic(BaseModel):
    topic_id: str
    lane: str
    project: LearningProjectRef
    title: str
    why_now: str
    score: int
    score_reasons: dict[str, int] = Field(default_factory=dict)
    concepts: list[str] = Field(default_factory=list)
    evidence: list[LearningEvidence] = Field(default_factory=list)
    exercise: str = ""
    completion_check: str = ""
    default_mode: str = "study"
    prompt: str = ""
    questions: list[LearningQuestion] = Field(default_factory=list)
    mastery_status: str = "unassessed"
    competency_id: CompetencyId = "implementation"
    competency_status: CompetencyStatus = "unassessed"
    target_level: TargetLevel = "unspecified"
    proof_requirement: Literal["reasoning", "artifact"] = "reasoning"
    required_artifact: str = ""
    start_command: str = ""


class LearningMasterySummary(BaseModel):
    mastered: int = 0
    developing: int = 0
    needs_practice: int = 0
    unassessed: int = 0


class LearningRecommendationSet(BaseModel):
    schema_version: int = 2
    recommendation_id: str
    scope: str = "local"
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    topics: list[LearningRecommendationTopic] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    mastery_summary: LearningMasterySummary = Field(default_factory=LearningMasterySummary)
    profile: LearnerProfile = Field(default_factory=LearnerProfile)
    competencies: list[CompetencySummary] = Field(default_factory=list)


class LearningSession(BaseModel):
    session_id: str = ""
    mutation_id: str = ""
    topic_id: str = ""
    recommendation_id: str = ""
    project_id: str = ""
    project_name: str = ""
    project_root: str = ""
    task_id: str = ""
    task: str
    request: str = ""
    mode: str = "study"
    topic: str = ""
    question: str = ""
    expected_points: list[str] = Field(default_factory=list)
    evidence_files: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    competency_id: CompetencyId | None = None
    target_level: TargetLevel = "unspecified"
    proof_requirement: Literal["reasoning", "artifact"] = "reasoning"
    required_artifact: str = ""
    answer: str = ""
    score: int | None = None
    self_assessment: str = ""
    note: str = ""
    mastery_status: str = "unassessed"
    proof: LearningProof | None = None
    proof_hash: str = ""
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    legacy_evidence: bool = False
    status: str = "queued"
    source: str = "agentpack learn"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""


class AgentLesson(BaseModel):
    rule: str
    evidence_files: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: int = 70
    status: str = "generated"
    last_seen: str = ""
    citations: list[Citation] = Field(default_factory=list)


class SkillEvidence(BaseModel):
    skill: str
    task: str
    evidence_files: list[str] = Field(default_factory=list)
    confidence: int = 0
    citations: list[Citation] = Field(default_factory=list)


class SkillProgress(BaseModel):
    skill: str
    task_count: int = 0
    last_task: str = ""
    evidence: list[SkillEvidence] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    confidence: int = 0
    confidence_kind: Literal["exposure"] = "exposure"
    first_seen: str = ""
    last_seen: str = ""
    source_paths: list[str] = Field(default_factory=list)
    related_tests: list[str] = Field(default_factory=list)
    accepted_corrections: list[str] = Field(default_factory=list)
    suppressed: bool = False


class FeedbackSignal(BaseModel):
    feedback: str
    target: str = ""
    note: str = ""
    task: str = ""
    scope: str = ""
    concepts: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class FeedbackSummary(BaseModel):
    helpful_concepts: set[str] = Field(default_factory=set)
    not_helpful_concepts: set[str] = Field(default_factory=set)
    suppressed_skills: set[str] = Field(default_factory=set)
    suppressed_lesson_terms: set[str] = Field(default_factory=set)
    skill_renames: dict[str, str] = Field(default_factory=dict)
    skill_merges: dict[str, str] = Field(default_factory=dict)
    accepted_notes: list[str] = Field(default_factory=list)


class LearningReport(BaseModel):
    task: str
    scope: str
    learning_request: str = ""
    coach_mode: str = "study"
    since: str | None = None
    issue_references: list[str] = Field(default_factory=list)
    issue_reference_details: list[dict] = Field(default_factory=list)
    source_files: list[LearningSourceFile] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    claim_citations: dict[str, list[Citation]] = Field(default_factory=dict)
    learning_topics: list[LearningTopic] = Field(default_factory=list)
    learning_cards: list[LearningCard] = Field(default_factory=list)
    quiz: list[QuizQuestion] = Field(default_factory=list)
    agent_lessons: list[AgentLesson] = Field(default_factory=list)
    skill_evidence: list[SkillEvidence] = Field(default_factory=list)
    next_practice: str = ""
    quality_findings: list[str] = Field(default_factory=list)
    citation_coverage: float = 0.0
    invalid_citations: list[str] = Field(default_factory=list)
    uncited_claims: list[str] = Field(default_factory=list)
    selected_hits: list[str] = Field(default_factory=list)
    selected_misses: list[str] = Field(default_factory=list)
    recommendations: LearningRecommendationSet | None = None
