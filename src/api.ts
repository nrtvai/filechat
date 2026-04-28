import type { AgentRun, AgentRunEvent, AgentRunQuestion, AgentRunWorkspaceItem, AuditEvent, ContextProfile, CurrentUser, FileRecord, MembershipRole, Message, MetaIssue, ModelInfo, Session, Settings, UsageSummary, WikiEdge, WikiNode } from "./types";

const API = import.meta.env.VITE_API_BASE ?? "/api";
const TEST_ROLE_KEY = "filechat:test-role";
export const API_UNAVAILABLE_MESSAGE = "FileChat API is not running. Start `npm run dev:all`.";

function roleOverride(): MembershipRole | null {
  if (typeof window === "undefined") return null;
  const storage = window.localStorage;
  if (!storage || typeof storage.getItem !== "function") return null;
  const value = storage.getItem(TEST_ROLE_KEY);
  return value === "owner" || value === "admin" || value === "member" ? value : null;
}

function requestHeaders(init?: RequestInit) {
  const headers = init?.body instanceof FormData ? new Headers(init.headers) : new Headers({
    "Content-Type": "application/json",
    ...init?.headers
  });
  const role = roleOverride();
  if (role) headers.set("X-FileChat-Test-Role", role);
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, {
      ...init,
      headers: requestHeaders(init)
    });
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error(API_UNAVAILABLE_MESSAGE);
    }
    throw err;
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail || response.statusText);
  }
  return response.json() as Promise<T>;
}

export const api = {
  testRole: roleOverride,
  setTestRole: (role: MembershipRole) => {
    if (typeof window !== "undefined" && typeof window.localStorage?.setItem === "function") {
      window.localStorage.setItem(TEST_ROLE_KEY, role);
    }
  },
  health: () => request<{ status: string }>("/health"),
  me: () => request<CurrentUser>("/me"),
  settings: () => request<Settings>("/settings"),
  contextProfile: () => request<ContextProfile>("/context/profile"),
  patchContextProfile: (body: Partial<ContextProfile>) =>
    request<ContextProfile>("/context/profile", { method: "PATCH", body: JSON.stringify(body) }),
  patchSettings: (body: Partial<Settings> & { openrouter_api_key?: string }) =>
    request<Settings>("/settings", { method: "PATCH", body: JSON.stringify(body) }),
  adminSettings: () => request<Settings>("/admin/settings"),
  patchAdminSettings: (body: Partial<Settings> & { openrouter_api_key?: string }) =>
    request<Settings>("/admin/settings", { method: "PATCH", body: JSON.stringify(body) }),
  clearOpenRouterKey: () => request<Settings>("/admin/settings/openrouter-key", { method: "DELETE" }),
  auditEvents: () => request<AuditEvent[]>("/admin/audit-events"),
  metaIssues: () => request<MetaIssue[]>("/admin/meta-issues"),
  createMetaIssue: (body: Pick<MetaIssue, "source" | "severity" | "title"> & { body?: string; metadata?: Record<string, unknown> }) =>
    request<MetaIssue>("/meta-issues", { method: "POST", body: JSON.stringify(body) }),
  updateMetaIssue: (issueId: string, status: MetaIssue["status"]) =>
    request<MetaIssue>(`/admin/meta-issues/${issueId}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  verifyOpenRouter: () => request<Settings>("/settings/openrouter/verify", { method: "POST" }),
  models: (kind: "chat" | "embedding") => request<ModelInfo[]>(`/models?kind=${kind}`),
  modelRecommendations: (task: string) => request<Record<string, unknown>>(`/models/recommendations?task=${encodeURIComponent(task)}`),
  wikiNodes: (query = "") => request<WikiNode[]>(`/wiki/nodes${query}`),
  createWikiNode: (body: Pick<WikiNode, "scope" | "type" | "title"> & { summary?: string; properties?: Record<string, unknown>; source_refs?: Record<string, unknown>[] }) =>
    request<WikiNode>("/wiki/nodes", { method: "POST", body: JSON.stringify(body) }),
  updateWikiNode: (nodeId: string, body: Partial<Pick<WikiNode, "type" | "title" | "summary" | "properties" | "source_refs">>) =>
    request<WikiNode>(`/wiki/nodes/${nodeId}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteWikiNode: (nodeId: string) => request<{ ok: boolean }>(`/wiki/nodes/${nodeId}`, { method: "DELETE" }),
  wikiEdges: () => request<WikiEdge[]>("/wiki/edges"),
  createWikiEdge: (body: Pick<WikiEdge, "source_node_id" | "target_node_id" | "relation_type"> & { weight?: number; confidence?: number; properties?: Record<string, unknown> }) =>
    request<WikiEdge>("/wiki/edges", { method: "POST", body: JSON.stringify(body) }),
  updateWikiEdge: (edgeId: string, body: Partial<Pick<WikiEdge, "relation_type" | "weight" | "confidence" | "properties">>) =>
    request<WikiEdge>(`/wiki/edges/${edgeId}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteWikiEdge: (edgeId: string) => request<{ ok: boolean }>(`/wiki/edges/${edgeId}`, { method: "DELETE" }),
  sessions: () => request<Session[]>("/sessions"),
  createSession: (title?: string) =>
    request<Session>("/sessions", { method: "POST", body: JSON.stringify({ title }) }),
  files: (sessionId: string) => request<FileRecord[]>(`/sessions/${sessionId}/files`),
  uploadFiles: (sessionId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("uploads", file));
    return request<FileRecord[]>(`/sessions/${sessionId}/files`, { method: "POST", body: form });
  },
  detachFile: (sessionId: string, fileId: string) =>
    request<{ ok: boolean }>(`/sessions/${sessionId}/files/${fileId}`, { method: "DELETE" }),
  retryFile: (sessionId: string, fileId: string) =>
    request<FileRecord>(`/sessions/${sessionId}/files/${fileId}/retry`, { method: "POST" }),
  messages: (sessionId: string) => request<Message[]>(`/sessions/${sessionId}/messages`),
  runs: (sessionId: string) => request<AgentRun[]>(`/sessions/${sessionId}/runs`),
  run: (sessionId: string, runId: string) => request<AgentRun>(`/sessions/${sessionId}/runs/${runId}`),
  usage: (sessionId: string) => request<UsageSummary>(`/sessions/${sessionId}/usage`),
  ask: (sessionId: string, content: string) =>
    request<Message>(`/sessions/${sessionId}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  startRun: (sessionId: string, content: string) =>
    request<AgentRun>(`/sessions/${sessionId}/runs`, { method: "POST", body: JSON.stringify({ content }) }),
  approveRun: (sessionId: string, runId: string) =>
    request<AgentRun>(`/sessions/${sessionId}/runs/${runId}/approve-plan`, { method: "POST" }),
  retryRun: (sessionId: string, runId: string, mode: "repair" | "rerun" = "rerun") =>
    request<AgentRun>(`/sessions/${sessionId}/runs/${runId}/retry`, { method: "POST", body: JSON.stringify({ mode }) }),
  resumeRun: (sessionId: string, runId: string) =>
    request<AgentRun>(`/sessions/${sessionId}/runs/${runId}/resume`, { method: "POST" }),
  runContract: (sessionId: string, runId: string) =>
    request<Record<string, unknown>>(`/sessions/${sessionId}/runs/${runId}/contract`),
  currentRunQuestion: (sessionId: string, runId: string) =>
    request<AgentRunQuestion | null>(`/sessions/${sessionId}/runs/${runId}/questions/current`),
  answerRunQuestion: (sessionId: string, runId: string, questionId: string, selectedOption: string, freeText = "") =>
    request<AgentRun>(`/sessions/${sessionId}/runs/${runId}/questions/${questionId}/answer`, {
      method: "POST",
      body: JSON.stringify({ selected_option: selectedOption, free_text: freeText })
    }),
  runEvents: (sessionId: string, runId: string, afterSeq = 0) =>
    request<AgentRunEvent[]>(`/sessions/${sessionId}/runs/${runId}/events?after_seq=${afterSeq}`),
  runWorkspace: (sessionId: string, runId: string) =>
    request<AgentRunWorkspaceItem[]>(`/sessions/${sessionId}/runs/${runId}/workspace`),
  refreshSessionContext: (sessionId: string) =>
    request<Record<string, unknown>>(`/sessions/${sessionId}/context/refresh`, { method: "POST" }),
  exportArtifactUrl: (sessionId: string, artifactId: string, format: "md" | "json") =>
    `${API}/sessions/${sessionId}/artifacts/${artifactId}/export?format=${format}`
};
