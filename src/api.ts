import type { AgentRun, AgentRunEvent, AgentRunQuestion, AgentRunWorkspaceItem, AuditEvent, BotIngestionResult, ContextProfile, CurrentUser, Edition, FileRecord, MembershipRole, Message, MetaIssue, ModelInfo, Session, Settings, UsageSummary, WikiEdge, WikiNode } from "./types";

const API = import.meta.env.VITE_API_BASE ?? "/api";
const TEST_ROLE_KEY = "filechat:test-role";
const TEST_EDITION_KEY = "filechat:test-edition";
export const API_UNAVAILABLE_MESSAGE = "FileChat API is not running. Start `npm run dev:all`.";

export interface TestModeOverride {
  edition: Edition;
  role: MembershipRole;
}

let memoryTestMode: TestModeOverride | null = null;

function localStorageGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  const storage = window.localStorage;
  if (!storage || typeof storage.getItem !== "function") return null;
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function localStorageSet(key: string, value: string) {
  if (typeof window === "undefined") return;
  const storage = window.localStorage;
  if (!storage || typeof storage.setItem !== "function") return;
  try {
    storage.setItem(key, value);
  } catch {
    // The in-memory override remains active when browser storage is unavailable.
  }
}

function localStorageRemove(key: string) {
  if (typeof window === "undefined") return;
  const storage = window.localStorage;
  if (!storage || typeof storage.removeItem !== "function") return;
  try {
    storage.removeItem(key);
  } catch {
    // Nothing to clear from browser storage; clearTestMode resets memory state.
  }
}

function roleOverride(): MembershipRole | null {
  const value = localStorageGet(TEST_ROLE_KEY) ?? memoryTestMode?.role ?? null;
  return value === "owner" || value === "admin" || value === "member" ? value : null;
}

function editionOverride(): Edition | null {
  const value = localStorageGet(TEST_EDITION_KEY) ?? memoryTestMode?.edition ?? null;
  return value === "community" || value === "enterprise" ? value : null;
}

function testModeOverride(): TestModeOverride | null {
  const edition = editionOverride();
  if (!edition) return null;
  return { edition, role: roleOverride() ?? "admin" };
}

function effectiveTestModeOverride(): TestModeOverride {
  return testModeOverride() ?? { edition: "community", role: "owner" };
}

function requestHeaders(init?: RequestInit) {
  const headers = init?.body instanceof FormData ? new Headers(init.headers) : new Headers({
    "Content-Type": "application/json",
    ...init?.headers
  });
  const role = roleOverride();
  const edition = editionOverride();
  if (edition) headers.set("X-FileChat-Test-Edition", edition);
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
    const detail = String(payload.detail || response.statusText || "Request failed");
    if (response.status >= 500) {
      throw new Error(`FileChat API returned ${response.status} for ${path}. ${detail}. Check API logs or restart \`npm run dev:all\`.`);
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  testRole: roleOverride,
  testMode: testModeOverride,
  effectiveTestMode: effectiveTestModeOverride,
  setTestMode: (edition: Edition, role: MembershipRole = "admin") => {
    memoryTestMode = { edition, role };
    localStorageSet(TEST_EDITION_KEY, edition);
    localStorageSet(TEST_ROLE_KEY, role);
  },
  setTestRole: (role: MembershipRole) => {
    memoryTestMode = { edition: editionOverride() ?? "enterprise", role };
    localStorageSet(TEST_ROLE_KEY, role);
  },
  clearTestMode: () => {
    memoryTestMode = null;
    localStorageRemove(TEST_EDITION_KEY);
    localStorageRemove(TEST_ROLE_KEY);
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
  ingestSlackEvent: (body: unknown) =>
    request<BotIngestionResult>("/integrations/slack/events", { method: "POST", body: JSON.stringify(body) }),
  ingestTelegramWebhook: (body: unknown) =>
    request<BotIngestionResult>("/integrations/telegram/webhook", { method: "POST", body: JSON.stringify(body) }),
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
  answerRunQuestion: (sessionId: string, runId: string, questionId: string, selectedOption: string | null, freeText = "", attachedFileIds: string[] = [], answer: Record<string, unknown> = {}) => {
    const body: Record<string, unknown> = {};
    if (selectedOption) body.selected_option = selectedOption;
    body.free_text = freeText;
    if (attachedFileIds.length > 0) body.attached_file_ids = attachedFileIds;
    if (Object.keys(answer).length > 0) body.answer = answer;
    return request<AgentRun>(`/sessions/${sessionId}/runs/${runId}/questions/${questionId}/answer`, {
      method: "POST",
      body: JSON.stringify(body)
    });
  },
  runEvents: (sessionId: string, runId: string, afterSeq = 0) =>
    request<AgentRunEvent[]>(`/sessions/${sessionId}/runs/${runId}/events?after_seq=${afterSeq}`),
  runWorkspace: (sessionId: string, runId: string) =>
    request<AgentRunWorkspaceItem[]>(`/sessions/${sessionId}/runs/${runId}/workspace`),
  refreshSessionContext: (sessionId: string) =>
    request<Record<string, unknown>>(`/sessions/${sessionId}/context/refresh`, { method: "POST" }),
  exportArtifactUrl: (sessionId: string, artifactId: string, format: "md" | "json" | "notion" | "csv" | "pdf" | "od") =>
    `${API}/sessions/${sessionId}/artifacts/${artifactId}/export?format=${format}`
};
