import type { AgentRun, AgentRunEvent, AgentRunQuestion, AgentRunWorkspaceItem, ContextProfile, FileRecord, Message, ModelInfo, Session, Settings, UsageSummary } from "./types";

const API = import.meta.env.VITE_API_BASE ?? "/api";
export const API_UNAVAILABLE_MESSAGE = "FileChat API is not running. Start `npm run dev:all`.";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, {
      ...init,
      headers: init?.body instanceof FormData ? init.headers : {
        "Content-Type": "application/json",
        ...init?.headers
      }
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
  health: () => request<{ status: string }>("/health"),
  settings: () => request<Settings>("/settings"),
  contextProfile: () => request<ContextProfile>("/context/profile"),
  patchContextProfile: (body: Partial<ContextProfile>) =>
    request<ContextProfile>("/context/profile", { method: "PATCH", body: JSON.stringify(body) }),
  patchSettings: (body: Partial<Settings> & { openrouter_api_key?: string }) =>
    request<Settings>("/settings", { method: "PATCH", body: JSON.stringify(body) }),
  verifyOpenRouter: () => request<Settings>("/settings/openrouter/verify", { method: "POST" }),
  models: (kind: "chat" | "embedding") => request<ModelInfo[]>(`/models?kind=${kind}`),
  modelRecommendations: (task: string) => request<Record<string, unknown>>(`/models/recommendations?task=${encodeURIComponent(task)}`),
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
