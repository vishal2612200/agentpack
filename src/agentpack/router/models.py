from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SideEffectLevel = Literal["none", "file_write", "command", "external"]


class SkillArtifact(BaseModel):
    name: str
    source: str
    path: str
    description: str = ""
    domains: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    anti_triggers: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    side_effect_level: SideEffectLevel = "none"
    applies_to_paths: list[str] = Field(default_factory=list)
    anti_paths: list[str] = Field(default_factory=list)
    priority: int = 50
    confidence_threshold: float = 0.45
    raw_text: str = ""
    aliases: list[str] = Field(default_factory=list)


class RuleArtifact(BaseModel):
    name: str
    source: str
    path: str
    scope_paths: list[str] = Field(default_factory=list)
    priority: int = 50
    description: str = ""
    raw_text: str = ""


class SelectedSkill(BaseModel):
    skill: SkillArtifact
    score: float
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class AppliedRule(BaseModel):
    rule: RuleArtifact
    reasons: list[str] = Field(default_factory=list)


class CommandSuggestion(BaseModel):
    command: str
    reason: str
    source: str
    side_effect_level: Literal["command"] = "command"


class SkillInventory(BaseModel):
    version: int = 1
    skills: list[SkillArtifact] = Field(default_factory=list)
    rules: list[RuleArtifact] = Field(default_factory=list)


class RouteResult(BaseModel):
    task: str
    recommended_interaction_mode: str = "agent"
    mode_reason: str = ""
    current_agent: str = "generic"
    reviewer_agent: str = "codex"
    task_mode: str = "broad_feature"
    task_mode_confidence: float = 0.0
    task_mode_signals: list[str] = Field(default_factory=list)
    selected_files: list[dict] = Field(default_factory=list)
    selection_explanations: list[dict] = Field(default_factory=list)
    omitted_files: list[dict] = Field(default_factory=list)
    selected_skills: list[SelectedSkill] = Field(default_factory=list)
    baseline_skills: list[SelectedSkill] = Field(default_factory=list)
    applied_rules: list[AppliedRule] = Field(default_factory=list)
    suggested_commands: list[CommandSuggestion] = Field(default_factory=list)
    evidence_checklist: list[str] = Field(default_factory=list)
    routing_notes: list[str] = Field(default_factory=list)
    observer_notes: list[dict] = Field(default_factory=list)
    prompt_quality_warnings: list[str] = Field(default_factory=list)
    recommended_prompt_template: list[str] = Field(default_factory=list)
    safety_warnings: list[str] = Field(default_factory=list)
    agent_prompt: str = ""


class RouteExplanation(RouteResult):
    skill_scores: list[SelectedSkill] = Field(default_factory=list)
