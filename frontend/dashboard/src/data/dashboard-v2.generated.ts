/* Generated from docs/schemas/dashboard-v2.schema.json. Do not edit. */

export type Source = "declared" | "observed" | "inferred";
export type Confidence = number;
export type UpdatedAt = string;
export type Kind = string;
export type Ref = string;
export type Summary = string;
export type Path = string;
export type OccurredAt = string;
export type WorkspaceId = string;
export type Evidence = ProjectEvidence[];
export type WorkspaceId1 = string;
export type Warnings = string[];
export type SchemaVersion = number;
export type ProjectId = string;
export type GeneratedAt = string;
export type SelectedWorkspace = string;
export type Source1 = "declared" | "observed" | "inferred";
export type Confidence1 = number;
export type UpdatedAt1 = string;
export type Evidence1 = ProjectEvidence[];
export type WorkspaceId2 = string;
export type Warnings1 = string[];
export type ProjectId1 = string;
export type ConfigRevision = string;
export type DisplayName = string;
export type Purpose = string;
export type Audiences = string[];
export type Owners = string[];
export type Stage = string;
export type Environments = string[];
export type StatusStaleDays = number;
export type Source2 = "declared" | "observed" | "inferred";
export type Confidence2 = number;
export type UpdatedAt2 = string;
export type Evidence2 = ProjectEvidence[];
export type WorkspaceId3 = string;
export type Warnings2 = string[];
export type Path1 = string;
export type Branch = string;
export type GitSha = string;
export type IsCurrent = boolean;
export type ReadOnly = boolean;
export type Workspaces = ProjectWorkspace[];
export type Source3 = "declared" | "observed" | "inferred";
export type Confidence3 = number;
export type UpdatedAt3 = string;
export type Evidence3 = ProjectEvidence[];
export type WorkspaceId4 = string;
export type Warnings3 = string[];
export type OutcomeCount = number;
export type ActiveOutcomes = number;
export type MilestoneCount = number;
export type CompletedMilestones = number;
export type MilestoneCompletionPct = number | null;
export type OpenRisks = number;
export type PendingDecisions = number;
export type ConfirmedInitiatives = number;
export type RecentChanges = number;
export type EvidenceCoverage = number | null;
export type Source4 = "declared" | "observed" | "inferred";
export type Confidence4 = number;
export type UpdatedAt4 = string;
export type Evidence4 = ProjectEvidence[];
export type WorkspaceId5 = string;
export type Warnings4 = string[];
export type OutcomeId = string;
export type Title = string;
export type Description = string;
export type Owner = string;
export type TargetDate = string;
export type Status = "planned" | "on_track" | "at_risk" | "achieved" | "paused";
export type ProgressPct = number | null;
export type Source5 = "declared" | "observed" | "inferred";
export type Confidence5 = number;
export type UpdatedAt5 = string;
export type Evidence5 = ProjectEvidence[];
export type WorkspaceId6 = string;
export type Warnings5 = string[];
export type MilestoneId = string;
export type OutcomeId1 = string;
export type Title1 = string;
export type Owner1 = string;
export type DueDate = string;
export type Status1 = "planned" | "in_progress" | "blocked" | "done";
export type Milestones = ProjectMilestoneState[];
export type Outcomes = ProjectOutcomeState[];
export type Source6 = "declared" | "observed" | "inferred";
export type Confidence6 = number;
export type UpdatedAt6 = string;
export type Evidence6 = ProjectEvidence[];
export type WorkspaceId7 = string;
export type Warnings6 = string[];
export type InitiativeId = string;
export type SuggestionId = string;
export type Title2 = string;
export type Description1 = string;
export type Owner2 = string;
export type OutcomeId2 = string;
export type Status2 = string;
export type Initiatives = ProjectInitiative[];
export type Source7 = "declared" | "observed" | "inferred";
export type Confidence7 = number;
export type UpdatedAt7 = string;
export type Evidence7 = ProjectEvidence[];
export type WorkspaceId8 = string;
export type Warnings7 = string[];
export type SuggestionId1 = string;
export type Title3 = string;
export type Rationale = string;
export type OutcomeId3 = string;
export type Score = number;
export type TaskIds = string[];
export type InitiativeSuggestions = ProjectInitiativeSuggestion[];
export type Source8 = "declared" | "observed" | "inferred";
export type Confidence8 = number;
export type UpdatedAt8 = string;
export type Evidence8 = ProjectEvidence[];
export type WorkspaceId9 = string;
export type Warnings8 = string[];
export type RiskId = string;
export type Title4 = string;
export type Description2 = string;
export type Owner3 = string;
export type Severity = "low" | "medium" | "high" | "critical";
export type Status3 = "open" | "mitigating" | "accepted" | "resolved";
export type Mitigation = string;
export type Risks = ProjectRisk[];
export type Source9 = "declared" | "observed" | "inferred";
export type Confidence9 = number;
export type UpdatedAt9 = string;
export type Evidence9 = ProjectEvidence[];
export type WorkspaceId10 = string;
export type Warnings9 = string[];
export type DecisionId = string;
export type Title5 = string;
export type Context = string;
export type Decision = string;
export type Owner4 = string;
export type Status4 = "proposed" | "accepted" | "rejected" | "superseded";
export type Decisions = ProjectDecision[];
export type Source10 = "declared" | "observed" | "inferred";
export type Confidence10 = number;
export type UpdatedAt10 = string;
export type Evidence10 = ProjectEvidence[];
export type WorkspaceId11 = string;
export type Warnings10 = string[];
export type Source11 = "declared" | "observed" | "inferred";
export type Confidence11 = number;
export type UpdatedAt11 = string;
export type Evidence11 = ProjectEvidence[];
export type WorkspaceId12 = string;
export type Warnings11 = string[];
export type Dimension = "delivery" | "validation" | "architecture" | "release" | "context" | "knowledge";
export type Status5 = "healthy" | "attention" | "blocked" | "stale" | "unknown";
export type Summary1 = string;
export type Dimensions = ProjectHealthDimension[];
export type Source12 = "declared" | "observed" | "inferred";
export type Confidence12 = number;
export type UpdatedAt12 = string;
export type Evidence12 = ProjectEvidence[];
export type WorkspaceId13 = string;
export type Warnings12 = string[];
export type EventId = string;
export type Kind1 = string;
export type Title6 = string;
export type Summary2 = string;
export type EntityId = string;
export type Actor = string;
export type GitSha1 = string;
export type Branch1 = string;
export type Tags = string[];
export type RecentChanges1 = ProjectTimelineEvent[];
export type Partial = boolean;
export type ReadOnly1 = boolean;
export type Source13 = "declared" | "observed" | "inferred";
export type Confidence13 = number;
export type UpdatedAt13 = string;
export type Evidence13 = ProjectEvidence[];
export type WorkspaceId14 = string;
export type Warnings13 = string[];
export type Source14 = "declared" | "observed" | "inferred";
export type Confidence14 = number;
export type UpdatedAt14 = string;
export type Evidence14 = ProjectEvidence[];
export type WorkspaceId15 = string;
export type Warnings14 = string[];
export type Mode = "summary" | "engineering";
export type Markdown = string;
export type ProjectId2 = string;
export type Id = string;
export type Title7 = string;
export type Owner5 = string;
export type DueDate1 = string;
export type Id1 = string;
export type Title8 = string;
export type Description3 = string;
export type Owner6 = string;
export type TargetDate1 = string;
/**
 * @maxItems 100
 */
export type Milestones1 = ProjectMilestoneInput[];
export type DisplayName1 = string | null;
export type Purpose1 = string | null;
export type Audiences1 =
  | []
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string]
  | [string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | null;
export type Owners1 =
  | []
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string]
  | [string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | null;
export type Stage1 = string | null;
export type Links1 = {
  [k: string]: string;
} | null;
export type Environments1 =
  | []
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string]
  | [string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | null;
export type StatusStaleDays1 = number | null;
export type Outcomes1 = ProjectOutcomeInput[] | null;
export type MutationId = string;
export type ExpectedRevision = string;
export type Kind2 = string;
export type Ref1 = string;
export type Summary3 = string;
export type Path2 = string;
export type EventType =
  | "project_outcome_status"
  | "project_milestone_status"
  | "project_risk_upsert"
  | "project_decision_recorded"
  | "project_initiative_confirmed"
  | "project_initiative_dismissed";
export type MutationId1 = string;
export type EntityId1 = string;
export type Status6 = string;
export type Title9 = string;
export type Description4 = string;
export type Owner7 = string;
export type Severity1 = string;
export type Mitigation1 = string;
export type Context1 = string;
export type Decision1 = string;
export type OutcomeId4 = string;
/**
 * @maxItems 20
 */
export type Evidence15 =
  | []
  | [ProjectEventEvidenceInput]
  | [ProjectEventEvidenceInput, ProjectEventEvidenceInput]
  | [ProjectEventEvidenceInput, ProjectEventEvidenceInput, ProjectEventEvidenceInput]
  | [ProjectEventEvidenceInput, ProjectEventEvidenceInput, ProjectEventEvidenceInput, ProjectEventEvidenceInput]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ]
  | [
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput,
      ProjectEventEvidenceInput
    ];

export interface AgentPackDashboardV2 {
  schema_version: 2;
  detail: "home" | "full";
  snapshot: {
    project_overview?: ProjectOverview | null;
    [k: string]: unknown;
  };
  graph: ObjectEnvelope;
  map: ObjectEnvelope;
  action_history: {
    [k: string]: unknown;
  }[];
  workspace: {
    project: {
      [k: string]: unknown;
    };
    workspace: {
      [k: string]: unknown;
    } | null;
    task: {
      [k: string]: unknown;
    };
    context: {
      [k: string]: unknown;
    };
  };
  agents: {
    handoffs: Handoff[];
    sessions: Session[];
    threads: {
      [k: string]: unknown;
    }[];
    integrations: {
      [k: string]: unknown;
    }[];
    mcp_health: McpHealth;
  };
  impact: {
    schema_version: number;
    available: boolean;
    entity_count: number;
    edge_count: number;
    unresolved_count: number;
    capabilities: {
      [k: string]: string;
    };
  };
  cached_project_status?: CachedProjectStatus | null;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectOverview".
 */
export interface ProjectOverview {
  source?: Source;
  confidence?: Confidence;
  updated_at?: UpdatedAt;
  evidence?: Evidence;
  workspace_id?: WorkspaceId1;
  warnings?: Warnings;
  schema_version?: SchemaVersion;
  project_id: ProjectId;
  generated_at: GeneratedAt;
  selected_workspace?: SelectedWorkspace;
  profile: ProjectProfile;
  workspaces?: Workspaces;
  metrics?: ProjectMetrics;
  outcomes?: Outcomes;
  initiatives?: Initiatives;
  initiative_suggestions?: InitiativeSuggestions;
  risks?: Risks;
  decisions?: Decisions;
  health?: ProjectHealthSnapshot;
  focus?: ProjectFocusSnapshot | null;
  recent_changes?: RecentChanges1;
  partial?: Partial;
  read_only?: ReadOnly1;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectEvidence".
 */
export interface ProjectEvidence {
  kind: Kind;
  ref?: Ref;
  summary?: Summary;
  path?: Path;
  occurred_at?: OccurredAt;
  workspace_id?: WorkspaceId;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectProfile".
 */
export interface ProjectProfile {
  source?: Source1;
  confidence?: Confidence1;
  updated_at?: UpdatedAt1;
  evidence?: Evidence1;
  workspace_id?: WorkspaceId2;
  warnings?: Warnings1;
  project_id: ProjectId1;
  config_revision: ConfigRevision;
  display_name?: DisplayName;
  purpose?: Purpose;
  audiences?: Audiences;
  owners?: Owners;
  stage?: Stage;
  links?: Links;
  environments?: Environments;
  status_stale_days?: StatusStaleDays;
  [k: string]: unknown;
}
export interface Links {
  [k: string]: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectWorkspace".
 */
export interface ProjectWorkspace {
  source?: Source2;
  confidence?: Confidence2;
  updated_at?: UpdatedAt2;
  evidence?: Evidence2;
  workspace_id: WorkspaceId3;
  warnings?: Warnings2;
  path: Path1;
  branch?: Branch;
  git_sha?: GitSha;
  is_current?: IsCurrent;
  read_only?: ReadOnly;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectMetrics".
 */
export interface ProjectMetrics {
  source?: Source3;
  confidence?: Confidence3;
  updated_at?: UpdatedAt3;
  evidence?: Evidence3;
  workspace_id?: WorkspaceId4;
  warnings?: Warnings3;
  outcome_count?: OutcomeCount;
  active_outcomes?: ActiveOutcomes;
  milestone_count?: MilestoneCount;
  completed_milestones?: CompletedMilestones;
  milestone_completion_pct?: MilestoneCompletionPct;
  open_risks?: OpenRisks;
  pending_decisions?: PendingDecisions;
  confirmed_initiatives?: ConfirmedInitiatives;
  recent_changes?: RecentChanges;
  evidence_coverage?: EvidenceCoverage;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectOutcomeState".
 */
export interface ProjectOutcomeState {
  source?: Source4;
  confidence?: Confidence4;
  updated_at?: UpdatedAt4;
  evidence?: Evidence4;
  workspace_id?: WorkspaceId5;
  warnings?: Warnings4;
  outcome_id: OutcomeId;
  title: Title;
  description?: Description;
  owner?: Owner;
  target_date?: TargetDate;
  status?: Status;
  progress_pct?: ProgressPct;
  milestones?: Milestones;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectMilestoneState".
 */
export interface ProjectMilestoneState {
  source?: Source5;
  confidence?: Confidence5;
  updated_at?: UpdatedAt5;
  evidence?: Evidence5;
  workspace_id?: WorkspaceId6;
  warnings?: Warnings5;
  milestone_id: MilestoneId;
  outcome_id: OutcomeId1;
  title: Title1;
  owner?: Owner1;
  due_date?: DueDate;
  status?: Status1;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectInitiative".
 */
export interface ProjectInitiative {
  source?: Source6;
  confidence?: Confidence6;
  updated_at?: UpdatedAt6;
  evidence?: Evidence6;
  workspace_id?: WorkspaceId7;
  warnings?: Warnings6;
  initiative_id: InitiativeId;
  suggestion_id?: SuggestionId;
  title: Title2;
  description?: Description1;
  owner?: Owner2;
  outcome_id?: OutcomeId2;
  status?: Status2;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectInitiativeSuggestion".
 */
export interface ProjectInitiativeSuggestion {
  source?: Source7;
  confidence?: Confidence7;
  updated_at?: UpdatedAt7;
  evidence?: Evidence7;
  workspace_id?: WorkspaceId8;
  warnings?: Warnings7;
  suggestion_id: SuggestionId1;
  title: Title3;
  rationale: Rationale;
  outcome_id?: OutcomeId3;
  score?: Score;
  task_ids?: TaskIds;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectRisk".
 */
export interface ProjectRisk {
  source?: Source8;
  confidence?: Confidence8;
  updated_at?: UpdatedAt8;
  evidence?: Evidence8;
  workspace_id?: WorkspaceId9;
  warnings?: Warnings8;
  risk_id: RiskId;
  title: Title4;
  description?: Description2;
  owner?: Owner3;
  severity?: Severity;
  status?: Status3;
  mitigation?: Mitigation;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectDecision".
 */
export interface ProjectDecision {
  source?: Source9;
  confidence?: Confidence9;
  updated_at?: UpdatedAt9;
  evidence?: Evidence9;
  workspace_id?: WorkspaceId10;
  warnings?: Warnings9;
  decision_id: DecisionId;
  title: Title5;
  context?: Context;
  decision?: Decision;
  owner?: Owner4;
  status?: Status4;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectHealthSnapshot".
 */
export interface ProjectHealthSnapshot {
  source?: Source10;
  confidence?: Confidence10;
  updated_at?: UpdatedAt10;
  evidence?: Evidence10;
  workspace_id?: WorkspaceId11;
  warnings?: Warnings10;
  dimensions?: Dimensions;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectHealthDimension".
 */
export interface ProjectHealthDimension {
  source?: Source11;
  confidence?: Confidence11;
  updated_at?: UpdatedAt11;
  evidence?: Evidence11;
  workspace_id?: WorkspaceId12;
  warnings?: Warnings11;
  dimension: Dimension;
  status?: Status5;
  summary?: Summary1;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectFocusSnapshot".
 */
export interface ProjectFocusSnapshot {
  outcome_id?: string;
  milestone_id?: string;
  attention?: ProjectFocusItem[];
  next_actions?: ProjectNextAction[];
  source?: "declared" | "observed" | "inferred";
  confidence?: number;
  updated_at?: string;
  evidence?: ProjectEvidence[];
  workspace_id?: string;
  warnings?: string[];
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectFocusItem".
 */
export interface ProjectFocusItem {
  item_id: string;
  kind: "health" | "risk" | "decision" | "milestone" | "initiative";
  entity_id: string;
  title: string;
  summary?: string;
  status?: string;
  severity?: string;
  target_view: string;
  source?: "declared" | "observed" | "inferred";
  confidence?: number;
  updated_at?: string;
  evidence?: ProjectEvidence[];
  workspace_id?: string;
  warnings?: string[];
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectNextAction".
 */
export interface ProjectNextAction {
  action_id: string;
  entity_id: string;
  title: string;
  rationale?: string;
  target_view: string;
  source?: "declared" | "observed" | "inferred";
  confidence?: number;
  updated_at?: string;
  evidence?: ProjectEvidence[];
  workspace_id?: string;
  warnings?: string[];
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectTimelineEvent".
 */
export interface ProjectTimelineEvent {
  source?: Source12;
  confidence?: Confidence12;
  updated_at?: UpdatedAt12;
  evidence?: Evidence12;
  workspace_id?: WorkspaceId13;
  warnings?: Warnings12;
  event_id: EventId;
  kind: Kind1;
  title: Title6;
  summary?: Summary2;
  entity_id?: EntityId;
  actor?: Actor;
  git_sha?: GitSha1;
  branch?: Branch1;
  tags?: Tags;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "objectEnvelope".
 */
export interface ObjectEnvelope {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "handoff".
 */
export interface Handoff {
  name: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  source_provider?: string;
  source_session_id?: string;
  target_provider?: string;
  target_session_id?: string;
  task?: string;
  summary?: string;
  next_action?: string;
  claim_provider?: string;
  claim_session_id?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "session".
 */
export interface Session {
  provider: string;
  session_id: string;
  thread_id?: string;
  task?: string;
  status?: string;
  context_status?: string;
  updated_at?: string;
  worktree?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "McpHealth".
 */
export interface McpHealth {
  status?: string;
  checked_at?: string;
  source?: string;
  runtime_status?: string;
  runtime_ok?: boolean;
  runtime_detail?: string;
  registered?: boolean;
  registrations?: {
    [k: string]: unknown;
  }[];
  live_exposure?: string;
  expected_tools?: string[];
  remediation?: string[];
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "CachedProjectStatus".
 */
export interface CachedProjectStatus {
  schema_version?: 1;
  project_id: string;
  generated_at: string;
  branch?: string;
  git_sha?: string;
  profile: CachedProjectProfile;
  metrics: ProjectMetrics;
  outcomes?: ProjectOutcomeState[];
  initiatives?: ProjectInitiative[];
  risks?: ProjectRisk[];
  decisions?: ProjectDecision[];
  health: ProjectHealthSnapshot;
  focus?: ProjectFocusSnapshot | null;
  recent_changes?: ProjectTimelineEvent[];
  partial?: boolean;
  read_only?: boolean;
  warnings?: string[];
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "CachedProjectProfile".
 */
export interface CachedProjectProfile {
  display_name?: string;
  purpose?: string;
  audiences?: string[];
  owners?: string[];
  stage?: string;
  environments?: string[];
  status_stale_days?: number;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "learningRecommendationSet".
 */
export interface LearningRecommendationSet {
  schema_version: 1;
  recommendation_id: string;
  scope: "local" | "global";
  generated_at: string;
  /**
   * @maxItems 3
   */
  topics:
    | []
    | [LearningRecommendationTopic]
    | [LearningRecommendationTopic, LearningRecommendationTopic]
    | [LearningRecommendationTopic, LearningRecommendationTopic, LearningRecommendationTopic];
  warnings: string[];
  mastery_summary: LearningMasterySummary;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "learningRecommendationTopic".
 */
export interface LearningRecommendationTopic {
  topic_id: string;
  lane: "now" | "system" | "weak_spot";
  project: LearningProjectRef;
  title: string;
  why_now: string;
  score: number;
  score_reasons: {
    [k: string]: number;
  };
  concepts: string[];
  /**
   * @maxItems 5
   */
  evidence:
    | []
    | [LearningEvidence]
    | [LearningEvidence, LearningEvidence]
    | [LearningEvidence, LearningEvidence, LearningEvidence]
    | [LearningEvidence, LearningEvidence, LearningEvidence, LearningEvidence]
    | [LearningEvidence, LearningEvidence, LearningEvidence, LearningEvidence, LearningEvidence];
  exercise: string;
  completion_check: string;
  default_mode: string;
  prompt: string;
  questions: LearningQuestion[];
  mastery_status: "mastered" | "developing" | "needs_practice" | "unassessed";
  start_command: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "learningProjectRef".
 */
export interface LearningProjectRef {
  project_id: string;
  name: string;
  root: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "learningEvidence".
 */
export interface LearningEvidence {
  kind: string;
  task_id: string;
  task: string;
  path: string;
  summary: string;
  observed_at: string;
  status: string;
  stale: boolean;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "learningQuestion".
 */
export interface LearningQuestion {
  mode: string;
  question: string;
  expected_points: string[];
  evidence_files: string[];
  difficulty: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "learningMasterySummary".
 */
export interface LearningMasterySummary {
  mastered: number;
  developing: number;
  needs_practice: number;
  unassessed: number;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "impactResponse".
 */
export interface ImpactResponse {
  schema_version: 2;
  query: string;
  relationship: string;
  language: string;
  confidence: string;
  available: boolean;
  summary: {
    [k: string]: unknown;
  };
  affected_tests: {
    [k: string]: unknown;
  }[];
  entities: ImpactEntity[];
  relationships: ImpactRelationship[];
  scene: ImpactScene;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "impactEntity".
 */
export interface ImpactEntity {
  id: string;
  kind: "file" | "symbol" | "test" | "action" | "external";
  label: string;
  path: string;
  line: number;
  parent_id: string;
  confidence_tier: string;
  task_relevant: boolean;
  risk: string;
  reasons: string[];
  related_ids: string[];
  evidence: {
    [k: string]: unknown;
  }[];
  actions: string[];
  x: number;
  y: number;
  z: number;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "impactRelationship".
 */
export interface ImpactRelationship {
  id: string;
  source_id: string;
  target_id: string;
  relationship: string;
  confidence_tier: string;
  strength: number;
  task_relevant: boolean;
  evidence: {
    [k: string]: unknown;
  }[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "impactScene".
 */
export interface ImpactScene {
  available: boolean;
  unavailable_reason: string;
  entities: ImpactEntity[];
  relationships: ImpactRelationship[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionRequest".
 */
export interface ActionRequest {
  action: string;
  cwd?: string;
  agent?: string;
  thread?: string;
  task?: string;
  target?: string;
  path?: string;
  mode?: string;
  budget?: number | null;
  status?: string;
  summary?: string;
  thread_id?: string;
  older_than?: string;
  refresh?: boolean;
  guard?: boolean;
  global?: boolean;
  confirmed?: boolean;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "handoffOperationRequest".
 */
export interface HandoffOperationRequest {
  name: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "agentsResponse".
 */
export interface AgentsResponse {
  schema_version: 2;
  agents: Agents;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "agents".
 */
export interface Agents {
  handoffs: Handoff[];
  sessions: Session[];
  threads: {
    [k: string]: unknown;
  }[];
  integrations: {
    [k: string]: unknown;
  }[];
  mcp_health: McpHealth;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "agentOperationResponse".
 */
export interface AgentOperationResponse {
  schema_version: 2;
  handoff: Handoff;
  warnings: string[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionInspection".
 */
export interface ActionInspection {
  schema_version: 2;
  action: string;
  command: string;
  cwd: string;
  purpose: string;
  risk: string;
  risk_reasons: string[];
  affected_paths: string[];
  expected_effect: string;
  confirm_required: boolean;
  allowed: boolean;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionInspectionResponse".
 */
export interface ActionInspectionResponse {
  schema_version: 2;
  inspection: ActionInspection;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionRunResponse".
 */
export interface ActionRunResponse {
  schema_version: 2;
  session: {
    [k: string]: unknown;
  };
  command: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "unavailableState".
 */
export interface UnavailableState {
  kind:
    | "stale_context"
    | "tree_sitter_unavailable"
    | "mcp_unavailable"
    | "permission_denied"
    | "repository_mismatch"
    | "action_conflict"
    | "webgl_unavailable"
    | "server_error";
  title: string;
  detail: string;
  next_action: string;
  retryable: boolean;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "evidenceResponse".
 */
export interface EvidenceResponse {
  schema_version: 2;
  context: {
    [k: string]: unknown;
  };
  selected_files: {
    [k: string]: unknown;
  }[];
  task_map: {
    [k: string]: unknown;
  }[];
  observer: {
    [k: string]: unknown;
  };
  timeline: {
    [k: string]: unknown;
  }[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "actionsResponse".
 */
export interface ActionsResponse {
  schema_version: 2;
  suggested: {
    [k: string]: unknown;
  }[];
  catalog: {
    [k: string]: unknown;
  }[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "errorResponse".
 */
export interface ErrorResponse {
  schema_version: 2;
  error: string;
  kind: string;
  retryable: boolean;
  detail: string;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectDerivedRecord".
 */
export interface ProjectDerivedRecord {
  source?: Source13;
  confidence?: Confidence13;
  updated_at?: UpdatedAt13;
  evidence?: Evidence13;
  workspace_id?: WorkspaceId14;
  warnings?: Warnings13;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectStatusBrief".
 */
export interface ProjectStatusBrief {
  source?: Source14;
  confidence?: Confidence14;
  updated_at?: UpdatedAt14;
  evidence?: Evidence14;
  workspace_id?: WorkspaceId15;
  warnings?: Warnings14;
  mode: Mode;
  markdown: Markdown;
  project_id: ProjectId2;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectMilestoneInput".
 */
export interface ProjectMilestoneInput {
  id?: Id;
  title: Title7;
  owner?: Owner5;
  due_date?: DueDate1;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectOutcomeInput".
 */
export interface ProjectOutcomeInput {
  id?: Id1;
  title: Title8;
  description?: Description3;
  owner?: Owner6;
  target_date?: TargetDate1;
  milestones?: Milestones1;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectProfilePatch".
 */
export interface ProjectProfilePatch {
  display_name?: DisplayName1;
  purpose?: Purpose1;
  audiences?: Audiences1;
  owners?: Owners1;
  stage?: Stage1;
  links?: Links1;
  environments?: Environments1;
  status_stale_days?: StatusStaleDays1;
  outcomes?: Outcomes1;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectProfileUpdateRequest".
 */
export interface ProjectProfileUpdateRequest {
  mutation_id: MutationId;
  expected_revision: ExpectedRevision;
  profile: ProjectProfilePatch;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectEventEvidenceInput".
 */
export interface ProjectEventEvidenceInput {
  kind: Kind2;
  ref?: Ref1;
  summary?: Summary3;
  path?: Path2;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectEventRequest".
 */
export interface ProjectEventRequest {
  event_type: EventType;
  mutation_id: MutationId1;
  entity_id: EntityId1;
  status?: Status6;
  title?: Title9;
  description?: Description4;
  owner?: Owner7;
  severity?: Severity1;
  mitigation?: Mitigation1;
  context?: Context1;
  decision?: Decision1;
  outcome_id?: OutcomeId4;
  evidence?: Evidence15;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectTimelineResponse".
 */
export interface ProjectTimelineResponse {
  /**
   * @maxItems 200
   */
  timeline: ProjectTimelineEvent[];
  warnings?: string[];
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectProfileMutationResponse".
 */
export interface ProjectProfileMutationResponse {
  duplicate: boolean;
  profile: ProjectProfile;
  project_overview: ProjectOverview;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `AgentPackDashboardV2`'s JSON-Schema
 * via the `definition` "ProjectEventMutationResponse".
 */
export interface ProjectEventMutationResponse {
  duplicate: boolean;
  event: {
    [k: string]: unknown;
  };
  project_overview: ProjectOverview;
  [k: string]: unknown;
}
