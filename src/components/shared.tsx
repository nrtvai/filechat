import { ChangeEvent } from "react";
import { api } from "../api";
import type { Citation, ContextProfile, CurrentUser, FileRecord, UsageSummary } from "../types";

export const acceptedTypes = ".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.csv,.txt,.md,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.gif";
export type RightTab = "files" | "citations" | "artifacts" | "runs" | "settings" | "admin";
export const emptyUsageSummary: UsageSummary = {
  chat_prompt_tokens: 0,
  chat_completion_tokens: 0,
  embedding_tokens: 0,
  chat_prompt_cost: 0,
  chat_completion_cost: 0,
  embedding_cost: 0,
  total_tokens: 0,
  total_cost: 0
};
export const defaultContextProfile: ContextProfile = {
  artifact_policy: "chart+draft",
  citation_display: "minimized",
  drafting_policy: "model_polished_evidence",
  title_style: "localized_subject_first"
};

export function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatCost(value?: number) {
  const cost = value ?? 0;
  if (cost === 0) return "$0.00";
  if (cost < 0.01) return `$${cost.toFixed(5)}`;
  return `$${cost.toFixed(2)}`;
}

export function formatTokens(value?: number) {
  return `${Math.round(value ?? 0).toLocaleString()} tok`;
}

export function formatModelPrice(value?: number) {
  const perMillion = (value ?? 0) * 1_000_000;
  if (perMillion === 0) return "$0/M";
  if (perMillion < 0.01) return `$${perMillion.toFixed(4)}/M`;
  return `$${perMillion.toFixed(2)}/M`;
}

export function handleFileInputChange(event: ChangeEvent<HTMLInputElement>, upload: (files: File[]) => void | Promise<void>) {
  const input = event.currentTarget;
  const selectedFiles = Array.from(input.files ?? []);
  try {
    if (selectedFiles.length > 0) void upload(selectedFiles);
  } finally {
    input.value = "";
  }
}

export function statusLabel(file: FileRecord) {
  if (file.status === "failed") return `Failed · ${fileErrorSummary(file)}`;
  if (file.status === "ready") return `Ready · ${file.chunk_count} chunks`;
  return `${file.status} · ${Math.round(file.progress * 100)}%`;
}

export function fileErrorSummary(file: FileRecord) {
  const error = file.error || "Indexing failed";
  return providerErrorSummary(error, "Indexing failed");
}

export function providerErrorSummary(error: string, fallback = "Provider issue") {
  if (error.toLowerCase().includes("openrouter authentication failed") || error.includes("401 Unauthorized")) {
    return "OpenRouter key needs attention";
  }
  if (error.toLowerCase().includes("api key")) return "OpenRouter API key needs attention";
  if (error.toLowerCase().includes("openrouter")) return "OpenRouter provider issue";
  if (!error.trim()) return fallback;
  return error;
}

export function contextStatus(file: FileRecord) {
  if (file.status === "ready") return "ready to cite";
  if (file.status === "failed") return "failed";
  return `${Math.round(file.progress * 100)}%`;
}

export function localTestModeUser(): CurrentUser {
  const mode = api.effectiveTestMode();
  const enterprise = mode.edition === "enterprise";
  return {
    id: enterprise ? `usr_test_${mode.role}` : "usr_single",
    display_name: enterprise ? `Test ${mode.role}` : "Local user",
    email: enterprise ? `${mode.role}@filechat.test` : "local@filechat.dev",
    role: enterprise ? mode.role : "owner",
    organization_id: "org_single",
    edition: mode.edition,
    enterprise_enabled: enterprise,
    auth_test_mode: true,
    auth_mode: "local_mode_switcher_fallback",
    capabilities: {
      use_sessions: true,
      manage_settings: !enterprise || mode.role !== "member",
      manage_provider_keys: !enterprise || mode.role !== "member",
      export_logs: !enterprise || mode.role === "owner",
      use_admin_console: enterprise && mode.role !== "member",
      switch_test_mode: true,
    },
  };
}

export function citationSourceLabel(citation: Citation) {
  return citation.source_label.trim() || `Source ${citation.ordinal}`;
}

export function citationLocationLabel(citation: Citation) {
  return citation.location.trim() || "local snippet";
}

export function StatusDot({ status }: { status: string }) {
  return <span className={`dot ${status === "ready" ? "ready" : status === "failed" ? "err" : "work"}`} />;
}

export function FileMini({ file }: { file: FileRecord }) {
  return (
    <div className="file-mini">
      <div className="filemark">{file.type}</div>
      <div>
        <strong>{file.name}</strong>
        <small><StatusDot status={file.status} /> {statusLabel(file)}</small>
        {!!file.indexing_total_cost && <small className="mono">indexing {formatTokens(file.indexing_prompt_tokens)} · {formatCost(file.indexing_total_cost)}</small>}
      </div>
    </div>
  );
}
