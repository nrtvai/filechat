from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


FileStatus = Literal["queued", "reading", "indexing", "ready", "failed"]
MembershipRole = Literal["owner", "admin", "member"]
Edition = Literal["community", "enterprise"]
ArtifactKind = Literal["mermaid", "chart", "table", "decision_cards", "comparison", "summary_panel", "file_draft"]
ArtifactDisplayMode = Literal["primary", "supporting"]
AgentPhase = Literal["plan", "search", "analysis", "writing", "review", "implement"]
AgentRunStatus = Literal[
    "queued",
    "awaiting_approval",
    "awaiting_user_input",
    "needs_setup",
    "needs_revision",
    "running",
    "completed",
    "completed_with_warning",
    "failed",
]
AgentStepStatus = Literal["pending", "running", "completed", "skipped", "failed"]
ModelRoutingMode = Literal["auto", "balanced", "deep", "manual"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
AgentQuestionKind = Literal["interview_offer", "clarification", "choice", "missing_context", "approval"]
AgentQuestionStatus = Literal["pending", "answered", "cancelled"]


class SessionOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    file_count: int = 0
    latest_message_preview: str | None = None


class FileRecord(BaseModel):
    id: str
    hash: str
    name: str
    type: str
    size: int
    status: FileStatus
    progress: float
    page_count: int
    chunk_count: int
    error: str | None = None
    indexing_prompt_tokens: int = 0
    indexing_total_cost: float = 0.0


class CitationOut(BaseModel):
    id: str
    message_id: str
    file_id: str
    chunk_id: str
    source_label: str
    location: str
    excerpt: str
    score: float
    ordinal: int


class ArtifactOut(BaseModel):
    id: str
    session_id: str
    message_id: str
    kind: ArtifactKind
    title: str
    caption: str = ""
    display_mode: ArtifactDisplayMode = "primary"
    source_chunk_ids: list[str] = Field(default_factory=list)
    spec: dict[str, Any]
    created_at: str


class MessageOut(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    unavailable_file_ids: list[str] = Field(default_factory=list)
    created_at: str
    citations: list[CitationOut] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cost: float = 0.0
    completion_cost: float = 0.0
    total_cost: float = 0.0


class AgentRunStepOut(BaseModel):
    id: str
    run_id: str
    phase: AgentPhase
    ordinal: int
    status: AgentStepStatus
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
    updated_at: str


class AgentQuestionOptionOut(BaseModel):
    id: str
    label: str
    description: str = ""


class AgentRunQuestionOut(BaseModel):
    id: str
    run_id: str
    phase: AgentPhase
    kind: AgentQuestionKind
    question: str
    options: list[AgentQuestionOptionOut] = Field(default_factory=list)
    default_option: str | None = None
    answer: dict[str, Any] | None = None
    status: AgentQuestionStatus
    created_at: str
    updated_at: str
    answered_at: str | None = None


class AgentRunEventOut(BaseModel):
    id: str
    run_id: str
    seq: int
    type: str
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AgentRunWorkspaceItemOut(BaseModel):
    id: str
    run_id: str
    path: str
    kind: str
    content: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class AgentRunOut(BaseModel):
    id: str
    session_id: str
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    kind: Literal["ask", "create"] = "ask"
    status: AgentRunStatus
    question: str
    error: str | None = None
    execution_plan: dict[str, Any] = Field(default_factory=dict)
    task_contract: dict[str, Any] = Field(default_factory=dict)
    prompt_context: dict[str, Any] = Field(default_factory=dict)
    provider_status: dict[str, Any] = Field(default_factory=dict)
    agent_actions: list[dict[str, Any]] = Field(default_factory=list)
    review_scores: dict[str, Any] = Field(default_factory=dict)
    revision_required: bool = False
    model_assignments: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    artifact_versions: list[dict[str, Any]] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    current_question: AgentRunQuestionOut | None = None
    steps: list[AgentRunStepOut] = Field(default_factory=list)
    created_at: str
    updated_at: str
    completed_at: str | None = None


class SettingsOut(BaseModel):
    openrouter_key_configured: bool
    openrouter_key_source: Literal["env", "local", "missing"]
    edition: Edition = "community"
    settings_scope: Literal["single_user", "organization"] = "single_user"
    openrouter_provider_status: Literal["missing", "unverified", "verified", "invalid"]
    openrouter_provider_message: str = ""
    openrouter_verified_at: str | None = None
    chat_model: str
    orchestrator_model: str
    analysis_model: str
    writing_model: str
    repair_model: str
    embedding_model: str
    ocr_model: str
    retrieval_depth: int
    strict_grounding: bool
    web_search_enabled: bool = False
    web_search_engine: Literal["auto", "native", "exa", "parallel", "firecrawl"] = "auto"
    reasoning_effort: ReasoningEffort = "medium"
    model_routing_mode: ModelRoutingMode = "auto"
    high_cost_confirmation: bool = True


class SettingsPatch(BaseModel):
    openrouter_api_key: str | None = None
    chat_model: str | None = None
    orchestrator_model: str | None = None
    analysis_model: str | None = None
    writing_model: str | None = None
    repair_model: str | None = None
    embedding_model: str | None = None
    ocr_model: str | None = None
    retrieval_depth: int | None = Field(default=None, ge=1, le=24)
    strict_grounding: bool | None = None
    web_search_enabled: bool | None = None
    web_search_engine: Literal["auto", "native", "exa", "parallel", "firecrawl"] | None = None
    reasoning_effort: ReasoningEffort | None = None
    model_routing_mode: ModelRoutingMode | None = None
    high_cost_confirmation: bool | None = None


class ContextProfileOut(BaseModel):
    artifact_policy: Literal["chart+draft", "all", "ask_each_run"] = "chart+draft"
    citation_display: Literal["minimized", "full"] = "minimized"
    drafting_policy: Literal["model_polished_evidence", "deterministic_template", "ask_user_style"] = "model_polished_evidence"
    title_style: Literal["localized_subject_first", "generic"] = "localized_subject_first"


class ContextProfilePatch(BaseModel):
    artifact_policy: Literal["chart+draft", "all", "ask_each_run"] | None = None
    citation_display: Literal["minimized", "full"] | None = None
    drafting_policy: Literal["model_polished_evidence", "deterministic_template", "ask_user_style"] | None = None
    title_style: Literal["localized_subject_first", "generic"] | None = None


class CreateSession(BaseModel):
    title: str | None = None


class AskRequest(BaseModel):
    content: str = Field(min_length=1)


class CurrentUserOut(BaseModel):
    id: str
    display_name: str
    email: str | None = None
    role: MembershipRole
    organization_id: str
    edition: Edition
    enterprise_enabled: bool
    auth_test_mode: bool
    auth_mode: str
    capabilities: dict[str, bool] = Field(default_factory=dict)


class AuditEventOut(BaseModel):
    id: str
    organization_id: str
    actor_user_id: str
    actor_role: MembershipRole
    action: str
    target_type: str
    target_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class MetaIssueCreate(BaseModel):
    source: Literal["runtime", "complaint", "provider", "bot", "api"] = "complaint"
    severity: Literal["info", "warning", "error", "critical"] = "error"
    title: str = Field(min_length=1)
    body: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetaIssueUpdate(BaseModel):
    status: Literal["open", "triaged", "resolved", "ignored"]


class MetaIssueOut(BaseModel):
    id: str
    organization_id: str
    created_by: str | None = None
    source: str
    severity: str
    status: str
    title: str
    body: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str
    external_url: str | None = None
    created_at: str
    updated_at: str


class RetryRunRequest(BaseModel):
    mode: Literal["repair", "rerun"] = "rerun"


class AnswerRunQuestionRequest(BaseModel):
    selected_option: str | None = None
    free_text: str | None = None
    answer: dict[str, Any] = Field(default_factory=dict)


class UsageSummary(BaseModel):
    chat_prompt_tokens: int = 0
    chat_completion_tokens: int = 0
    embedding_tokens: int = 0
    chat_prompt_cost: float = 0.0
    chat_completion_cost: float = 0.0
    embedding_cost: float = 0.0
    total_tokens: int = 0
    total_cost: float = 0.0


class ModelPricing(BaseModel):
    prompt: float = 0.0
    completion: float = 0.0
    request: float = 0.0
    image: float = 0.0


class ModelInfo(BaseModel):
    id: str
    name: str
    context_length: int | None = None
    pricing: ModelPricing = Field(default_factory=ModelPricing)
    created: int | None = None
    architecture: dict[str, Any] = Field(default_factory=dict)
    supported_parameters: list[str] = Field(default_factory=list)
