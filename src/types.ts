export type FileStatus = "queued" | "reading" | "indexing" | "ready" | "failed";
export type Edition = "community" | "enterprise";
export type MembershipRole = "owner" | "admin" | "member";

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  file_count: number;
  latest_message_preview?: string | null;
}

export interface FileRecord {
  id: string;
  hash: string;
  name: string;
  type: string;
  size: number;
  status: FileStatus;
  progress: number;
  page_count: number;
  chunk_count: number;
  error?: string | null;
  indexing_prompt_tokens?: number;
  indexing_total_cost?: number;
}

export interface Citation {
  id: string;
  message_id: string;
  file_id: string;
  chunk_id: string;
  source_label: string;
  location: string;
  excerpt: string;
  score: number;
  ordinal: number;
}

export type ArtifactKind = "mermaid" | "chart" | "table" | "decision_cards" | "comparison" | "summary_panel" | "file_draft";
export type ArtifactDisplayMode = "primary" | "supporting";
export type AgentPhase = "plan" | "search" | "analysis" | "writing" | "review" | "implement";
export type AgentRunStatus = "queued" | "awaiting_approval" | "awaiting_user_input" | "needs_setup" | "needs_revision" | "running" | "completed" | "completed_with_warning" | "failed";
export type AgentStepStatus = "pending" | "running" | "completed" | "skipped" | "failed";
export type ModelRoutingMode = "auto" | "balanced" | "deep" | "manual";
export type ReasoningEffort = "none" | "minimal" | "low" | "medium" | "high" | "xhigh";
export type AgentQuestionKind = "interview_offer" | "clarification" | "choice" | "missing_context" | "approval";
export type AgentQuestionStatus = "pending" | "answered" | "cancelled";

export interface JsonRenderElement {
  type: string;
  props: Record<string, unknown>;
  children?: string[];
  visible?: unknown;
}

export interface JsonRenderSpec {
  root: string;
  elements: Record<string, JsonRenderElement>;
}

export interface Artifact {
  id: string;
  session_id: string;
  message_id: string;
  kind: ArtifactKind;
  title: string;
  caption: string;
  display_mode: ArtifactDisplayMode;
  source_chunk_ids: string[];
  spec: Record<string, unknown>;
  created_at: string;
}

export interface AgentRunStep {
  id: string;
  run_id: string;
  phase: AgentPhase;
  ordinal: number;
  status: AgentStepStatus;
  summary: string;
  detail: Record<string, unknown>;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentQuestionOption {
  id: string;
  label: string;
  description: string;
}

export interface AgentRunQuestion {
  id: string;
  run_id: string;
  phase: AgentPhase;
  kind: AgentQuestionKind;
  question: string;
  options: AgentQuestionOption[];
  default_option?: string | null;
  answer?: Record<string, unknown> | null;
  status: AgentQuestionStatus;
  created_at: string;
  updated_at: string;
  answered_at?: string | null;
}

export interface AgentRunEvent {
  id: string;
  run_id: string;
  seq: number;
  type: string;
  summary: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface AgentRunWorkspaceItem {
  id: string;
  run_id: string;
  path: string;
  kind: string;
  content: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentRun {
  id: string;
  session_id: string;
  user_message_id?: string | null;
  assistant_message_id?: string | null;
  kind: "ask" | "create";
  status: AgentRunStatus;
  question: string;
  error?: string | null;
  execution_plan: Record<string, unknown>;
  task_contract: Record<string, unknown>;
  prompt_context: Record<string, unknown>;
  provider_status: Record<string, unknown>;
  agent_actions: Record<string, unknown>[];
  review_scores: Record<string, unknown>;
  revision_required: boolean;
  model_assignments: Record<string, unknown>;
  tool_calls: Record<string, unknown>[];
  artifact_versions: Record<string, unknown>[];
  repair_attempts: Record<string, unknown>[];
  quality_warnings: string[];
  current_question?: AgentRunQuestion | null;
  steps: AgentRunStep[];
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  unavailable_file_ids: string[];
  created_at: string;
  citations: Citation[];
  artifacts: Artifact[];
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  prompt_cost?: number;
  completion_cost?: number;
  total_cost?: number;
}

export interface Settings {
  openrouter_key_configured: boolean;
  openrouter_key_source: "env" | "local" | "missing";
  edition: Edition;
  settings_scope: "single_user" | "organization";
  openrouter_provider_status: "missing" | "unverified" | "verified" | "invalid";
  openrouter_provider_message: string;
  openrouter_verified_at?: string | null;
  chat_model: string;
  orchestrator_model: string;
  analysis_model: string;
  writing_model: string;
  repair_model: string;
  embedding_model: string;
  ocr_model: string;
  retrieval_depth: number;
  strict_grounding: boolean;
  web_search_enabled: boolean;
  web_search_engine: "auto" | "native" | "exa" | "parallel" | "firecrawl";
  reasoning_effort: ReasoningEffort;
  model_routing_mode: ModelRoutingMode;
  high_cost_confirmation: boolean;
}

export interface CurrentUser {
  id: string;
  display_name: string;
  email?: string | null;
  role: MembershipRole;
  organization_id: string;
  edition: Edition;
  enterprise_enabled: boolean;
  auth_test_mode: boolean;
  auth_mode: string;
  capabilities: {
    use_sessions?: boolean;
    manage_settings?: boolean;
    manage_provider_keys?: boolean;
    export_logs?: boolean;
    use_admin_console?: boolean;
  };
}

export interface AuditEvent {
  id: string;
  organization_id: string;
  actor_user_id: string;
  actor_role: MembershipRole;
  action: string;
  target_type: string;
  target_id?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface MetaIssue {
  id: string;
  organization_id: string;
  created_by?: string | null;
  source: "runtime" | "complaint" | "provider" | "bot" | "api";
  severity: "info" | "warning" | "error" | "critical";
  status: "open" | "triaged" | "resolved" | "ignored";
  title: string;
  body: string;
  metadata: Record<string, unknown>;
  fingerprint: string;
  external_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WikiNode {
  id: string;
  organization_id: string;
  owner_user_id?: string | null;
  scope: "organization" | "user";
  type: string;
  title: string;
  summary: string;
  properties: Record<string, unknown>;
  source_refs: Record<string, unknown>[];
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface WikiEdge {
  id: string;
  organization_id: string;
  source_node_id: string;
  target_node_id: string;
  relation_type: string;
  weight: number;
  confidence: number;
  properties: Record<string, unknown>;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ContextProfile {
  artifact_policy: "chart+draft" | "all" | "ask_each_run";
  citation_display: "minimized" | "full";
  drafting_policy: "model_polished_evidence" | "deterministic_template" | "ask_user_style";
  title_style: "localized_subject_first" | "generic";
}

export interface UsageSummary {
  chat_prompt_tokens: number;
  chat_completion_tokens: number;
  embedding_tokens: number;
  chat_prompt_cost: number;
  chat_completion_cost: number;
  embedding_cost: number;
  total_tokens: number;
  total_cost: number;
}

export interface ModelPricing {
  prompt: number;
  completion: number;
  request: number;
  image: number;
}

export interface ModelInfo {
  id: string;
  name: string;
  context_length?: number | null;
  pricing: ModelPricing;
  created?: number | null;
  architecture: {
    input_modalities?: string[];
    output_modalities?: string[];
    modality?: string;
    tokenizer?: string;
    instruct_type?: string;
  };
  supported_parameters: string[];
}
