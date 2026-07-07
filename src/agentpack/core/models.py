from pathlib import Path
from typing import Any
from typing import Literal
from pydantic import BaseModel, Field

from agentpack.core.modes import PackMode

SUMMARY_SCHEMA_VERSION = 2


class ScanResult(BaseModel):
    packable: list["FileInfo"]
    ignored: list["FileInfo"]
    binary: list["FileInfo"]
    scan_mode: Literal["full", "incremental"] = "full"
    rehashed_count: int = 0
    reused_count: int = 0
    full_scan_reason: str | None = None

    @property
    def all_files(self) -> list["FileInfo"]:
        return self.packable + self.ignored + self.binary


class FileInfo(BaseModel):
    path: str
    abs_path: Path
    language: str | None = None
    size_bytes: int
    estimated_tokens: int
    hash: str | None = None
    ignored: bool = False
    binary: bool = False
    too_large: bool = False
    content: str | None = None  # cached at scan time; avoids re-reads in scoring/selection

    model_config = {"arbitrary_types_allowed": True}


class Symbol(BaseModel):
    name: str
    kind: Literal["class", "function", "method", "variable"]
    start_line: int
    end_line: int
    signature: str | None = None
    summary: str | None = None
    body: str | None = None  # source text captured at extraction time; no re-read needed
    node_id: str = ""
    signature_hash: str = ""
    source_hash: str = ""


class Citation(BaseModel):
    path: str
    start_line: int | None = None
    end_line: int | None = None
    source_hash: str | None = None
    kind: Literal["code", "symbol", "summary", "receipt", "external"]
    claim_id: str | None = None
    note: str = ""
    support_text: str = ""
    url: str = ""
    retrieved_at: str = ""
    content_hash: str = ""


class FileSummary(BaseModel):
    path: str
    hash: str
    language: str | None = None
    provider: str = "offline"
    schema_version: int = SUMMARY_SCHEMA_VERSION
    summary: str
    imports: list[str] = Field(default_factory=list)
    symbols: list[Symbol] = Field(default_factory=list)
    domain: str | None = None
    role: str | None = None
    entrypoints: list[str] = Field(default_factory=list)
    defines: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    reads_env: list[str] = Field(default_factory=list)
    reads_files: list[str] = Field(default_factory=list)
    writes_files: list[str] = Field(default_factory=list)
    external_systems: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    failure_hints: list[str] = Field(default_factory=list)
    ranking_keywords: list[str] = Field(default_factory=list)
    related_hints: list[str] = Field(default_factory=list)
    public_api: list[str] = Field(default_factory=list)
    naming_signals: list[str] = Field(default_factory=list)
    naming_keywords: list[str] = Field(default_factory=list)
    error_paths: list[str] = Field(default_factory=list)
    test_hints: list[str] = Field(default_factory=list)


class SelectedFile(BaseModel):
    path: str
    language: str | None = None
    score: float
    include_mode: Literal["full", "diff", "symbols", "skeleton", "summary"]
    reasons: list[str]
    content: str | None = None
    summary: str | None = None
    symbols: list[Symbol] = []
    redaction_warnings: list[str] = []
    source_hash: str | None = None
    citations: list[Citation] = Field(default_factory=list)


class Receipt(BaseModel):
    path: str
    action: Literal["included", "excluded", "summarized"]
    reason: str
    citations: list[Citation] = Field(default_factory=list)


class OmittedRelevantFile(BaseModel):
    path: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    estimated_tokens: int
    suggested_mode: Literal["full", "diff", "symbols", "skeleton", "summary"]
    omission_reason: str = "budget exhausted"
    risk: Literal["high", "medium", "low"] = "low"


class ModuleSummary(BaseModel):
    path: str
    files: int
    tokens: int
    languages: list[str] = Field(default_factory=list)
    key_files: list[str] = Field(default_factory=list)
    summary: str = ""
    citations: list[Citation] = Field(default_factory=list)


class BroadContext(BaseModel):
    intent: str
    inventory_files: int
    module_summaries: list[ModuleSummary] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    configs: list[str] = Field(default_factory=list)
    docs: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    inventory: list[str] = Field(default_factory=list)
    semantic_clusters: list[str] = Field(default_factory=list)
    omitted_by_budget: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class ContextPack(BaseModel):
    task: str
    agent: str
    mode: PackMode
    task_class: str = "general"
    budget: int
    token_estimate: int
    raw_repo_tokens: int
    after_ignore_tokens: int
    estimated_savings_percent: float
    repo_map: str = ""
    broad_context: BroadContext | None = None
    delta_summary: str = ""
    agent_lessons: str = ""
    changed_files: list[str]
    selected_files: list[SelectedFile]
    receipts: list[Receipt]
    citations: list[Citation] = Field(default_factory=list)
    omitted_relevant_files: list[OmittedRelevantFile] = Field(default_factory=list)
    pack_handoff_omitted_relevant_files: list[OmittedRelevantFile] = Field(default_factory=list)
    redaction_warnings: list[str] = []
    stale: bool = False
    freshness: dict[str, Any] = Field(default_factory=dict)
    freshness_warnings: list[str] = Field(default_factory=list)
    execution_state: dict[str, Any] = Field(default_factory=dict)
    concurrent_context: dict[str, Any] = Field(default_factory=dict)
    task_map: dict[str, Any] = Field(default_factory=dict)
    agent_lessons: str = ""


class DependencyNode(BaseModel):
    path: str
    imports: list[str] = []
    imported_by: list[str] = []
    tests: list[str] = []


class DependencyGraph(BaseModel):
    nodes: dict[str, DependencyNode] = {}

    def get(self, path: str) -> DependencyNode:
        return self.nodes.get(path, DependencyNode(path=path))

    def __getitem__(self, path: str) -> DependencyNode:
        return self.nodes[path]

    def __setitem__(self, path: str, node: DependencyNode) -> None:
        self.nodes[path] = node

    def __contains__(self, path: object) -> bool:
        return path in self.nodes

    def __iter__(self):  # type: ignore[override]
        return iter(self.nodes)

    def __len__(self) -> int:
        return len(self.nodes)

    def items(self):  # type: ignore[override]
        return self.nodes.items()
