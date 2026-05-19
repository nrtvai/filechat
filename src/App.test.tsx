import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { api, API_UNAVAILABLE_MESSAGE } from "./api";
import type { AgentRun, AgentRunAction, AgentRunQuestion, Artifact, Citation, CurrentUser, FileRecord, Message, Session, Settings } from "./types";

const settings: Settings = {
  openrouter_key_configured: true,
  openrouter_key_source: "env",
  edition: "community",
  settings_scope: "single_user",
  openrouter_provider_status: "verified",
  openrouter_provider_message: "OpenRouter key verified.",
  openrouter_verified_at: "",
  chat_model: "openai/gpt-4o-mini",
  orchestrator_model: "openai/gpt-5.4-mini",
  analysis_model: "openai/gpt-5.4-mini",
  writing_model: "openai/gpt-4o-mini",
  repair_model: "openai/gpt-4o-mini",
  embedding_model: "openai/text-embedding-3-small",
  ocr_model: "openai/gpt-4o-mini",
  retrieval_depth: 8,
  strict_grounding: true,
  web_search_enabled: false,
  web_search_engine: "auto",
  reasoning_effort: "medium",
  model_routing_mode: "auto",
  high_cost_confirmation: true
};

const currentUser: CurrentUser = {
  id: "usr_single",
  display_name: "Local user",
  email: "local@filechat.dev",
  role: "owner",
  organization_id: "org_single",
  edition: "community",
  enterprise_enabled: false,
  auth_test_mode: false,
  auth_mode: "single_user",
  capabilities: {
    use_sessions: true,
    manage_settings: true,
    manage_provider_keys: true,
    export_logs: true,
    use_admin_console: false
  }
};

function session(id: string, title = "New reading session", file_count = 0): Session {
  return { id, title, created_at: "", updated_at: "", file_count };
}

function file(id: string, name: string, status: FileRecord["status"] = "ready", error: string | null = null): FileRecord {
  return {
    id,
    hash: `${id}-hash`,
    name,
    type: "TXT",
    size: 128,
    status,
    progress: status === "ready" || status === "failed" ? 1 : 0.4,
    page_count: 1,
    chunk_count: status === "ready" ? 1 : 0,
    error
  };
}

function message(id: string, session_id: string, role: Message["role"], content: string): Message {
  return {
    id,
    session_id,
    role,
    content,
    unavailable_file_ids: [],
    created_at: "",
    citations: [],
    artifacts: []
  };
}

function citation(id: string, message_id = "msg_answer"): Citation {
  return {
    id,
    message_id,
    file_id: "fil_report",
    chunk_id: "chk_1",
    source_label: "report.txt",
    location: "chunk 1",
    excerpt: "Source excerpt",
    score: 0.99,
    ordinal: 1
  };
}

function artifact(id: string, kind: Artifact["kind"], spec: Artifact["spec"]): Artifact {
  return {
    id,
    session_id: "ses_new",
    message_id: "msg_answer",
    kind,
    title: kind === "mermaid" ? "Flowchart" : "Artifact",
    caption: "Grounded artifact",
    display_mode: "primary",
    source_chunk_ids: ["chk_1"],
    spec,
    created_at: ""
  };
}

function action(id: string, run_id: string, kind: AgentRunAction["kind"], status: AgentRunAction["status"], ordinal: number, output_summary?: string, output_json: Record<string, unknown> = {}): AgentRunAction {
  return {
    id,
    run_id,
    ordinal,
    kind,
    label: kind.replace(/_/g, " "),
    status,
    input_summary: status === "running" ? `${kind} ${status}` : "",
    output_summary: output_summary ?? `${kind} ${status}`,
    input_json: {},
    output_json,
    validation_json: {},
    created_at: "",
    updated_at: ""
  };
}

function run(id: string, status: AgentRun["status"], question = "Summarize", assistant_message_id: string | null = null): AgentRun {
  const kinds: AgentRunAction["kind"][] = ["verify_provider", "classify_request", "plan_task", "load_sources", "write", "validate", "persist_response"];
  return {
    id,
    session_id: "ses_new",
    user_message_id: "msg_user",
    assistant_message_id,
    kind: question.toLowerCase().includes("chart") ? "create" : "ask",
    status,
    question,
    execution_plan: {},
    task_contract: {},
    provider_status: {},
    agent_actions: [],
    review_scores: {},
    revision_required: false,
    model_assignments: {},
    tool_calls: [],
    artifact_versions: [],
    repair_attempts: [],
    quality_warnings: [],
    follow_up_questions: [],
    parent_run_id: null,
    trigger_question_id: null,
    actions: kinds.map((kind, index) => action(`act_${kind}`, id, kind, status === "completed" ? "completed" : index === 3 ? "running" : index < 3 ? "completed" : "running", index + 1)),
    created_at: "",
    updated_at: ""
  };
}

function planningQuestion(run_id: string, kind: AgentRunQuestion["kind"] = "interview_offer"): AgentRunQuestion {
  const isInterviewOffer = kind === "interview_offer";
  return {
    id: "ques_1",
    run_id,
    action_kind: "ask_user",
    kind,
    question: isInterviewOffer
      ? "Do you want a short interview for a better result, or should FileChat handle it automatically?"
      : "어떤 의사결정에 바로 쓸 수 있는 분석 자료가 필요하신가요?",
    options: isInterviewOffer
      ? [
          { id: "automatic", label: "Handle automatically", description: "Infer the best grounded deliverable from the attached files." },
          { id: "interview", label: "Interview me", description: "Ask a few focused questions before producing the result." }
        ]
      : [
          { id: "leadership_report", label: "리더 공유용", description: "핵심 인사이트와 실행 제안을 우선합니다." },
          { id: "team_workshop", label: "팀 워크숍용", description: "토론 질문과 병목 유형을 우선합니다." }
        ],
    default_option: isInterviewOffer ? "automatic" : "leadership_report",
    blocking: true,
    phase: "ask_user",
    card: {
      title: "Planning needs a choice",
      prompt: isInterviewOffer ? "Interview or automatic?" : "One more planning question",
      group: "business",
      options: [],
      allow_free_text: kind === "clarification",
      allow_file_reference: false,
      allow_multi_select: false,
      submit_label: "Start follow-up"
    },
    parent_message_id: null,
    parent_artifact_id: null,
    status: "pending",
    answer_file_ids: [],
    created_at: "",
    updated_at: ""
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

async function enabledAskButton() {
  let button: HTMLElement | undefined;
  await waitFor(() => {
    button = screen.getAllByRole("button", { name: /ask/i }).find((candidate) => !candidate.hasAttribute("disabled"));
    expect(button).toBeDefined();
  });
  return button!;
}

function fileListOf(files: File[]): FileList {
  const entries = files.reduce<Record<number, File>>((acc, file, index) => {
    acc[index] = file;
    return acc;
  }, {});
  return {
    ...entries,
    length: files.length,
    item: (index: number) => files[index] ?? null,
    [Symbol.iterator]: files[Symbol.iterator].bind(files),
  } as FileList;
}

function chooseFiles(input: HTMLInputElement, files: File[], value = files[0] ? `C:\\fakepath\\${files[0].name}` : "C:\\fakepath\\cleared.txt") {
  Object.defineProperty(input, "files", { configurable: true, value: fileListOf(files) });
  Object.defineProperty(input, "value", { configurable: true, writable: true, value });
  fireEvent.change(input);
}

function chooseFile(input: HTMLInputElement, selectedFile: File) {
  chooseFiles(input, [selectedFile]);
}

function uploadPostCount(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([input, init]) => String(input).endsWith("/api/sessions/ses_new/files") && init?.method === "POST").length;
}

function runPostCount(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([input, init]) => String(input).endsWith("/api/sessions/ses_new/runs") && init?.method === "POST").length;
}

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    api.clearTestMode();
  });

  it("creates a fresh blank session on initial load instead of reopening old sessions", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_old", "Old conversation", 1), session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_old/files")) return Response.json([file("fil_old", "old.txt")]);
      if (url.endsWith("/api/sessions/ses_old/messages")) return Response.json([message("msg_old", "ses_old", "assistant", "old answer")]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Attach files")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith("/api/sessions", expect.objectContaining({ method: "POST", body: JSON.stringify({}) }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/sessions/ses_new/files", expect.anything()));
    expect(fetchMock).not.toHaveBeenCalledWith("/api/sessions/ses_old/files", expect.anything());
  });

  it("resets the cold-start Attach files input so the same file can be selected again", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files") && init?.method === "POST") return Response.json([file("fil_repeat", "repeat.txt", "queued")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Attach files")).toBeInTheDocument();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    const selectedFile = new File(["repeat"], "repeat.txt", { type: "text/plain" });

    chooseFile(input, selectedFile);

    await waitFor(() => expect(uploadPostCount(fetchMock)).toBe(1));
    expect(input.value).toBe("");

    chooseFile(input, selectedFile);

    await waitFor(() => expect(uploadPostCount(fetchMock)).toBe(2));
    expect(input.value).toBe("");
  });

  it("resets the processing Add files input so the same file can be selected again", async () => {
    const readyFile = file("fil_ready", "ready.txt");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new", "New reading session", 1));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files") && init?.method === "POST") return Response.json([readyFile, file("fil_repeat", "repeat.txt", "queued")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([readyFile]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Files ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add files/i })).toBeInTheDocument();
    expect(screen.queryByText("Attach files")).not.toBeInTheDocument();
    const fileInputs = document.querySelectorAll('input[type="file"]');
    expect(fileInputs).toHaveLength(1);
    const input = fileInputs[0] as HTMLInputElement;
    const selectedFile = new File(["repeat"], "repeat.txt", { type: "text/plain" });

    chooseFile(input, selectedFile);

    await waitFor(() => expect(uploadPostCount(fetchMock)).toBe(1));
    expect(input.value).toBe("");

    chooseFile(input, selectedFile);

    await waitFor(() => expect(uploadPostCount(fetchMock)).toBe(2));
    expect(input.value).toBe("");
  });

  it("resets a file input without uploading when the selected file list is empty", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Attach files")).toBeInTheDocument();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeTruthy();

    chooseFiles(input, [], "C:\\fakepath\\cancelled.txt");

    expect(uploadPostCount(fetchMock)).toBe(0);
    expect(input.value).toBe("");
  });

  it("keeps the chat composer visible on cold start without ready files and does not submit drafts", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Attach files")).toBeInTheDocument();
    const input = await screen.findByLabelText("Ask a question about the selected files");
    expect(input).toBeVisible();
    expect(screen.getByText("No ready sources yet · you can draft while files process")).toBeVisible();

    fireEvent.change(input, { target: { value: "I can draft before attaching" } });
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    expect(input).toHaveValue("I can draft before attaching");
    expect(fetchMock).not.toHaveBeenCalledWith("/api/sessions/ses_new/runs", expect.objectContaining({ method: "POST" }));
  });

  it("warns that an answer with ready files but no citations is ungrounded", async () => {
    const answer = message("msg_answer", "ses_new", "assistant", "This answer has no cited snippets.");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new", "New reading session", 1));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("No citations attached to this answer.")).toBeInTheDocument();
    expect(screen.getByText("Treat this answer as ungrounded until a cited snippet supports it.")).toBeVisible();
    expect(screen.getByText("Available source context: report.txt")).toBeVisible();
  });

  it("names failed source context when an uncited answer has skipped files", async () => {
    const answer = message("msg_answer", "ses_new", "assistant", "This answer could not cite every attached source.");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new", "New reading session", 2));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 2)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([
        file("fil_report", "report.txt"),
        file("fil_notes", "notes.pdf", "failed", "OCR failed")
      ]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("No citations attached to this answer.")).toBeInTheDocument();
    expect(screen.getByText("Available source context: report.txt")).toBeVisible();
    expect(screen.getByText("Failed source context: notes.pdf (OCR failed)")).toBeVisible();
  });

  it("ignores the composer shortcut during IME composition", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new", "New reading session", 1));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs") && init?.method === "POST") return Response.json(run("run_1", "completed", "한글 질문"));
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const input = await screen.findByLabelText("Ask a question about the selected files");
    await screen.findByText(/1 ready source/);
    fireEvent.change(input, { target: { value: "한글 질문" } });

    const composingShortcut = new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true, cancelable: true });
    Object.defineProperty(composingShortcut, "isComposing", { value: true });
    fireEvent(input, composingShortcut);

    expect(runPostCount(fetchMock)).toBe(0);
    expect(input).toHaveValue("한글 질문");

    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true, keyCode: 229 });

    expect(runPostCount(fetchMock)).toBe(0);
    expect(input).toHaveValue("한글 질문");

    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    await waitFor(() => expect(runPostCount(fetchMock)).toBe(1));
  });

  it("does not submit to a newly selected session while its files and messages are still loading", async () => {
    const slowFiles = deferred<Response>();
    const slowMessages = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_fresh", "Fresh session", 1));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_fresh", "Fresh session", 1), session("ses_slow", "Slow session", 1)]);
      if (url.endsWith("/api/sessions/ses_fresh/files")) return Response.json([file("fil_fresh", "fresh.txt")]);
      if (url.endsWith("/api/sessions/ses_fresh/messages")) return Response.json([message("msg_fresh", "ses_fresh", "assistant", "Fresh answer")]);
      if (url.endsWith("/api/sessions/ses_fresh/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_fresh/runs")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_slow/files")) return slowFiles.promise;
      if (url.endsWith("/api/sessions/ses_slow/messages")) return slowMessages.promise;
      if (url.endsWith("/api/sessions/ses_slow/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_slow/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Fresh answer")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Slow session/i }));

    expect(await screen.findByText("Loading session")).toBeInTheDocument();
    expect(screen.getByText("Loading files and messages...")).toBeInTheDocument();
    expect(screen.queryByText("Files ready")).not.toBeInTheDocument();
    expect(screen.queryByText("0 of 0 files ready")).not.toBeInTheDocument();

    const input = await screen.findByLabelText("Ask a question about the selected files");
    fireEvent.change(input, { target: { value: "Do not submit while stale sources are visible" } });
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    for (const button of screen.getAllByRole("button", { name: /ask/i })) {
      fireEvent.click(button);
    }

    expect(fetchMock).not.toHaveBeenCalledWith("/api/sessions/ses_slow/runs", expect.objectContaining({ method: "POST" }));

    await act(async () => {
      slowFiles.resolve(Response.json([file("fil_slow", "slow.txt")]));
      slowMessages.resolve(Response.json([]));
    });
  });

  it("shows a clear API offline message when the dev server is not reachable", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText(API_UNAVAILABLE_MESSAGE)).toBeInTheDocument();
  });

  it("renders local mode controls from stored test mode when /api/me fails", async () => {
    api.setTestMode("enterprise", "member");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json({ detail: "Internal server error" }, { status: 500 });
      if (url.endsWith("/api/settings")) return Response.json({ ...settings, edition: "enterprise", settings_scope: "organization" });
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const mode = await screen.findByLabelText("Test mode");
    expect(mode).toHaveValue("enterprise:member");
    expect(screen.getByRole("option", { name: "Community" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Enterprise owner" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Enterprise admin" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Enterprise member" })).toBeInTheDocument();
    expect(await screen.findByText(/FileChat API returned 500 for \/me/)).toBeInTheDocument();
  });

  it("shows file context chips and detaches a file from the active session", async () => {
    const report = file("fil_report", "report.txt");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files") && init?.method === "DELETE") return Response.json({ ok: true });
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([report]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([message("msg_1", "ses_new", "assistant", "A cited answer")]);
      if (url.endsWith("/api/sessions/ses_new/files/fil_report") && init?.method === "DELETE") return Response.json({ ok: true });
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const remove = await screen.findByRole("button", { name: "Remove report.txt from context" });
    expect(screen.getByText("report.txt")).toBeInTheDocument();
    expect(screen.getByText("ready to cite")).toBeInTheDocument();
    fireEvent.click(remove);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/sessions/ses_new/files/fil_report", expect.objectContaining({ method: "DELETE" }));
    });
  });

  it("shows agent setup preview for survey chart requests without blocking send", async () => {
    const survey = { ...file("fil_survey", "survey.csv"), type: "CSV" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([survey]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([message("msg_1", "ses_new", "assistant", "Ready")]);
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const input = await screen.findByLabelText("Ask a question about the selected files");
    await act(async () => {
      fireEvent.change(input, { target: { value: "Make a chart about the survey result" } });
    });

    expect(await screen.findByLabelText("Agent Setup preview")).toBeInTheDocument();
    expect(screen.getByText(/CSV parser/)).toBeInTheDocument();
  });

  it("renders a pending assistant turn while a question is generating", async () => {
    const askRequest = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/runs") && init?.method === "POST") return askRequest.promise;
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const input = await screen.findByLabelText("Ask a question about the selected files");
    await screen.findByText(/1 ready source/);
    fireEvent.change(input, { target: { value: "Summarize this file" } });
    const sendButton = await enabledAskButton();
    fireEvent.click(sendButton);

    expect(await screen.findByText("Reading the sources...")).toBeInTheDocument();

    await act(async () => {
      askRequest.resolve(Response.json(run("run_1", "completed", "Summarize this file", "msg_answer")));
    });
  });

  it("ignores stale session loads after switching sessions", async () => {
    const newFiles = deferred<Response>();
    const newMessages = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_old", "Old conversation", 1), session("ses_new", "Fresh session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return newFiles.promise;
      if (url.endsWith("/api/sessions/ses_new/messages")) return newMessages.promise;
      if (url.endsWith("/api/sessions/ses_old/files")) return Response.json([file("fil_old", "old.txt")]);
      if (url.endsWith("/api/sessions/ses_old/messages")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Old conversation/i }));
    await act(async () => {
      newFiles.resolve(Response.json([file("fil_new", "new.txt")]));
      newMessages.resolve(Response.json([]));
    });

    await waitFor(() => expect(screen.getAllByText("old.txt").length).toBeGreaterThan(0));
    expect(screen.queryAllByText("new.txt")).toHaveLength(0);
  });

  it("ignores stale session load failures after switching sessions", async () => {
    const newFiles = deferred<Response>();
    const newMessages = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/context/profile")) return Response.json({});
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new", "Fresh session", 1));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_old", "Old conversation", 1), session("ses_new", "Fresh session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return newFiles.promise;
      if (url.endsWith("/api/sessions/ses_new/messages")) return newMessages.promise;
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_old/files")) return Response.json([file("fil_old", "old.txt")]);
      if (url.endsWith("/api/sessions/ses_old/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_old/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_old/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Old conversation/i }));
    await waitFor(() => expect(screen.getAllByText("old.txt").length).toBeGreaterThan(0));

    await act(async () => {
      newMessages.resolve(Response.json([]));
      newFiles.reject(new Error("Stale new-session load failed"));
    });

    await waitFor(() => expect(screen.queryByText("Stale new-session load failed")).not.toBeInTheDocument());
    expect(screen.getAllByText("old.txt").length).toBeGreaterThan(0);
  });

  it("shows failed files clearly without exposing raw provider errors in the layout", async () => {
    const rawError = "Client error '401 Unauthorized' for url 'https://openrouter.ai/api/v1/embeddings'";
    const failed = file("fil_failed", "bad-key.pdf", "failed", rawError);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files/fil_failed/retry") && init?.method === "POST") return Response.json(file("fil_failed", "bad-key.pdf", "queued"));
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([failed]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("File indexing failed")).toBeInTheDocument();
    expect(screen.getAllByText(/OpenRouter key needs attention/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Client error '401 Unauthorized'/)).not.toBeInTheDocument();
    expect(document.querySelector("[title*='Client error']")).toBeTruthy();
    expect(screen.queryByText("100%")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry indexing" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/sessions/ses_new/files/fil_failed/retry", expect.objectContaining({ method: "POST" }));
    });
  });

  it("allows drafting when no files are ready but does not submit", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_failed", "bad-key.pdf", "failed", "OpenRouter authentication failed")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const input = await screen.findByLabelText("Ask a question about the selected files");
    fireEvent.change(input, { target: { value: "I can still draft this" } });
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });

    expect(input).toHaveValue("I can still draft this");
    expect(fetchMock).not.toHaveBeenCalledWith("/api/sessions/ses_new/runs", expect.objectContaining({ method: "POST" }));
  });

  it("submits ready prompts with Cmd+Enter", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/runs") && init?.method === "POST") return Response.json(run("run_1", "completed", "Summarize", "msg_answer"));
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const input = await screen.findByLabelText("Ask a question about the selected files");
    await screen.findByText(/1 ready source/);
    fireEvent.change(input, { target: { value: "Summarize" } });
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/sessions/ses_new/runs",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ content: "Summarize" }) })
      );
    });
  });

  it("loads live model dropdowns after saving an API key and filters results", async () => {
    const missingSettings = {
      ...settings,
      openrouter_key_configured: false,
      openrouter_key_source: "missing" as const,
      openrouter_provider_status: "missing" as const,
      openrouter_provider_message: "OpenRouter API key is missing.",
      openrouter_verified_at: null
    };
    const chatModels = [
      {
        id: "openai/gpt-free",
        name: "GPT Free",
        context_length: 8192,
        pricing: { prompt: 0, completion: 0, request: 0, image: 0 },
        created: 1,
        architecture: { input_modalities: ["text"], output_modalities: ["text"] },
        supported_parameters: ["response_format"]
      },
      {
        id: "anthropic/claude-paid",
        name: "Claude Paid",
        context_length: 200000,
        pricing: { prompt: 0.000003, completion: 0.000015, request: 0, image: 0 },
        created: 2,
        architecture: { input_modalities: ["text"], output_modalities: ["text"] },
        supported_parameters: []
      }
    ];
    const embeddingModels = [
      {
        id: "openai/text-embedding-test",
        name: "Embedding Test",
        context_length: 8192,
        pricing: { prompt: 0.00000002, completion: 0, request: 0, image: 0 },
        created: 3,
        architecture: { input_modalities: ["text"], output_modalities: ["embeddings"] },
        supported_parameters: []
      }
    ];
    let sawHealthCheck = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/health")) {
        sawHealthCheck = true;
        return Response.json({ status: "ok" });
      }
      if (url.endsWith("/api/settings") && init?.method === "PATCH") return Response.json(settings);
      if (url.endsWith("/api/settings/openrouter/verify") && init?.method === "POST") return Response.json(settings);
      if (url.endsWith("/api/settings")) return Response.json(missingSettings);
      if (url.includes("/api/models?kind=chat")) return Response.json(chatModels);
      if (url.includes("/api/models?kind=embedding")) return Response.json(embeddingModels);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "settings" }));
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-test" } });
    fireEvent.click(screen.getByRole("button", { name: /Save key/i }));

    await waitFor(() => expect(sawHealthCheck).toBe(true));
    expect(await screen.findByLabelText("Chat model")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText(/2 of 2 models/).length).toBeGreaterThan(0));
    fireEvent.change(screen.getAllByLabelText("Search")[0], { target: { value: "claude" } });
    expect(screen.getByText(/1 of 2 models/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Chat model"), { target: { value: "anthropic/claude-paid" } });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/settings", expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ chat_model: "anthropic/claude-paid" })
      }));
    });
  });

  it("hides provider key management from enterprise members", async () => {
    const enterpriseSettings: Settings = {
      ...settings,
      edition: "enterprise",
      settings_scope: "organization"
    };
    const enterpriseMember: CurrentUser = {
      ...currentUser,
      role: "member",
      edition: "enterprise",
      enterprise_enabled: true,
      auth_test_mode: true,
      auth_mode: "test_impersonation",
      capabilities: {
        use_sessions: true,
        manage_settings: false,
        manage_provider_keys: false,
        export_logs: false,
        use_admin_console: false
      }
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(enterpriseMember);
      if (url.endsWith("/api/settings")) return Response.json(enterpriseSettings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "settings" }));
    expect(screen.getByText("Managed by admins")).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "admin" })).not.toBeInTheDocument();
  });

  it("shows how enterprise configuration is enabled from environment settings", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.includes("/api/models?kind=chat")) return Response.json([]);
      if (url.includes("/api/models?kind=embedding")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "settings" }));
    expect(screen.getByText("Community configuration active")).toBeInTheDocument();
    expect(screen.getByText(/FILECHAT_EDITION=enterprise/)).toBeInTheDocument();
    expect(screen.getByText(/FILECHAT_TRUSTED_AUTH_HEADERS=true/)).toBeInTheDocument();
  });

  it("toggles local test mode between community and enterprise roles", async () => {
    const switcherUser: CurrentUser = {
      ...currentUser,
      auth_test_mode: true,
      auth_mode: "local_mode_switcher",
      capabilities: {
        ...currentUser.capabilities,
        switch_test_mode: true
      }
    };
    const enterpriseAdmin: CurrentUser = {
      ...switcherUser,
      role: "admin",
      edition: "enterprise",
      enterprise_enabled: true,
      capabilities: {
        ...switcherUser.capabilities,
        use_admin_console: true
      }
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const headers = new Headers(init?.headers);
      if (url.endsWith("/api/me")) {
        return Response.json(headers.get("X-FileChat-Test-Edition") === "enterprise" ? enterpriseAdmin : switcherUser);
      }
      if (url.endsWith("/api/settings")) {
        return Response.json(headers.get("X-FileChat-Test-Edition") === "enterprise" ? { ...settings, edition: "enterprise", settings_scope: "organization" } : settings);
      }
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const mode = await screen.findByLabelText("Test mode");
    fireEvent.change(mode, { target: { value: "enterprise:admin" } });

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url, init]) => (
        String(url).endsWith("/api/me")
        && new Headers((init as RequestInit | undefined)?.headers).get("X-FileChat-Test-Edition") === "enterprise"
      ))).toBe(true);
    });
    expect(await screen.findByText("Enterprise")).toBeInTheDocument();
  });

  it("renders all local mode choices when /api/me allows switching", async () => {
    const switcherUser: CurrentUser = {
      ...currentUser,
      auth_test_mode: true,
      auth_mode: "local_mode_switcher",
      capabilities: {
        ...currentUser.capabilities,
        switch_test_mode: true
      }
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(switcherUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("option", { name: "Community" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Enterprise owner" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Enterprise admin" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Enterprise member" })).toBeInTheDocument();
  });

  it("puts enterprise provider key management in the admin console", async () => {
    const enterpriseSettings: Settings = {
      ...settings,
      edition: "enterprise",
      settings_scope: "organization"
    };
    const enterpriseAdmin: CurrentUser = {
      ...currentUser,
      role: "admin",
      edition: "enterprise",
      enterprise_enabled: true,
      auth_test_mode: true,
      auth_mode: "test_impersonation",
      capabilities: {
        use_sessions: true,
        manage_settings: true,
        manage_provider_keys: true,
        export_logs: false,
        use_admin_console: true
      }
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(enterpriseAdmin);
      if (url.endsWith("/api/health")) return Response.json({ status: "ok" });
      if (url.endsWith("/api/settings")) return Response.json(enterpriseSettings);
      if (url.endsWith("/api/admin/settings") && init?.method === "PATCH") return Response.json(enterpriseSettings);
      if (url.endsWith("/api/settings/openrouter/verify") && init?.method === "POST") return Response.json(enterpriseSettings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.includes("/api/models?kind=chat")) return Response.json([]);
      if (url.includes("/api/models?kind=embedding")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "admin" }));
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-admin" } });
    fireEvent.click(screen.getByRole("button", { name: /Save key/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/admin/settings", expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ openrouter_api_key: "sk-or-admin" })
      }));
    });
  });

  it("lets enterprise admins clear saved local provider keys", async () => {
    const localSettings: Settings = {
      ...settings,
      edition: "enterprise",
      settings_scope: "organization",
      openrouter_key_source: "local"
    };
    const clearedSettings: Settings = {
      ...localSettings,
      openrouter_key_configured: false,
      openrouter_key_source: "missing",
      openrouter_provider_status: "missing",
      openrouter_provider_message: "OpenRouter API key is missing."
    };
    const enterpriseAdmin: CurrentUser = {
      ...currentUser,
      role: "admin",
      edition: "enterprise",
      enterprise_enabled: true,
      auth_test_mode: true,
      auth_mode: "test_impersonation",
      capabilities: {
        use_sessions: true,
        manage_settings: true,
        manage_provider_keys: true,
        export_logs: false,
        use_admin_console: true
      }
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(enterpriseAdmin);
      if (url.endsWith("/api/health")) return Response.json({ status: "ok" });
      if (url.endsWith("/api/admin/settings/openrouter-key") && init?.method === "DELETE") return Response.json(clearedSettings);
      if (url.endsWith("/api/settings")) return Response.json(localSettings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new")]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.includes("/api/models?kind=chat")) return Response.json([]);
      if (url.includes("/api/models?kind=embedding")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "admin" }));
    fireEvent.click(await screen.findByRole("button", { name: /Clear saved key/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/admin/settings/openrouter-key", expect.objectContaining({
        method: "DELETE"
      }));
    });
  });

  it("keeps optimistic user messages uniquely keyed across repeated API outages", async () => {
    let messageReads = 0;
    const consoleError = vi.spyOn(globalThis.console, "error").mockImplementation(() => undefined);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/runs") && init?.method === "POST") {
        throw new TypeError("Failed to fetch");
      }
      if (url.endsWith("/api/sessions/ses_new/messages")) {
        messageReads += 1;
        if (messageReads === 1) return Response.json([]);
        throw new TypeError("Failed to fetch");
      }
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const input = await screen.findByLabelText("Ask a question about the selected files");
    await screen.findByText(/1 ready source/);
    fireEvent.change(input, { target: { value: "First offline ask" } });
    fireEvent.click(await enabledAskButton());

    expect(await screen.findByText(API_UNAVAILABLE_MESSAGE)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("First offline ask")).toBeInTheDocument());

    const transcriptInput = screen.getByLabelText("Ask a question about the selected files");
    fireEvent.change(transcriptInput, { target: { value: "Second offline ask" } });
    fireEvent.click(await enabledAskButton());

    await waitFor(() => expect(screen.getByText("Second offline ask")).toBeInTheDocument());
    expect(consoleError.mock.calls.some((call) => String(call[0]).includes("Encountered two children with the same key"))).toBe(false);
  });

  it("labels source-less assistant answers so grounded chat does not imply hidden citations", async () => {
    const answer = message("msg_answer", "ses_new", "assistant", "I could not find that in the attached file.");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new", "New reading session", 1));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("I could not find that in the attached file.")).toBeInTheDocument();
    expect(screen.getByText("No citations attached to this answer.")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "No citations attached" })).toHaveTextContent("no retrieved source snippets");
    expect(screen.getByText("FileChat is being explicit that this response has no retrieved source snippets."))
      .toBeInTheDocument();
  });

  it("names unavailable files on source-less assistant answers", async () => {
    const missing = file("fil_missing", "archived-notes.pdf", "failed", "File was detached before retrieval");
    const answer = {
      ...message("msg_answer", "ses_new", "assistant", "I could only answer from available context."),
      unavailable_file_ids: ["fil_missing", "fil_deleted"]
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new", "New reading session", 1));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([missing]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("I could only answer from available context.")).toBeInTheDocument();
    expect(screen.getByText("No citations attached to this answer.")).toBeInTheDocument();
    expect(screen.getByText("Unavailable sources: archived-notes.pdf, fil_deleted")).toBeInTheDocument();
  });

  it("renders per-message costs and the session cost total", async () => {
    const costedUser: Message = {
      ...message("msg_user", "ses_new", "user", "Summarize"),
      prompt_tokens: 107,
      total_tokens: 107,
      prompt_cost: 0.0011,
      total_cost: 0.0011
    };
    const costedAssistant: Message = {
      ...message("msg_answer", "ses_new", "assistant", "Answer"),
      completion_tokens: 25,
      total_tokens: 25,
      completion_cost: 0.002,
      total_cost: 0.002
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([{ ...file("fil_report", "report.txt"), indexing_prompt_tokens: 11, indexing_total_cost: 0.00022 }]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([costedUser, costedAssistant]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({
        chat_prompt_tokens: 100,
        chat_completion_tokens: 25,
        embedding_tokens: 18,
        chat_prompt_cost: 0.001,
        chat_completion_cost: 0.002,
        embedding_cost: 0.00032,
        total_tokens: 143,
        total_cost: 0.00332
      });
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Input 107 tok · $0.00110")).toBeInTheDocument();
    expect(screen.getByText("Output 25 tok · $0.00200")).toBeInTheDocument();
    expect(screen.getByLabelText("Session token cost summary")).toHaveTextContent("$0.00332 total");
  });

  it("renders json-render artifacts and opens artifact details", async () => {
    const cited = citation("cit_1");
    const tableArtifact = artifact("art_table", "table", {
      root: "card",
      elements: {
        card: { type: "ArtifactCard", props: { title: "Pilot Plan", caption: "Grounded artifact" }, children: ["table", "source"] },
        table: { type: "DataTable", props: { columns: ["Step", "Owner"], rows: [["Pilot", "Operations"]] }, children: [] },
        source: { type: "SourceButton", props: { label: "Open source", chunkId: "chk_1" }, children: [] }
      }
    });
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Here is the table."), citations: [cited], artifacts: [tableArtifact] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Pilot Plan")).toBeInTheDocument();
    expect(screen.getByText("Operations")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PDF" })).toHaveAttribute("href", expect.stringContaining("format=pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
    expect(await screen.findByText("Artifacts · this session")).toBeInTheDocument();
    expect(screen.getByText("1 source chunk")).toBeInTheDocument();
  });

  it("renders decision cards with timeline JSON-render components", async () => {
    const cited = citation("cit_1");
    const optionsArtifact = artifact("art_options", "decision_cards", {
      root: "card",
      elements: {
        card: { type: "ArtifactCard", props: { title: "Available Charts And Docs" }, children: ["timeline", "source"] },
        timeline: {
          type: "Timeline",
          props: {
            items: [
              { date: "4월", label: "Proposal review", description: "Review proposal and scope.", status: "planned", sourceChunkId: "chk_1" },
              { date: "5월", label: "Training", description: "Run foundational education.", status: "planned", sourceChunkId: "chk_1" }
            ]
          },
          children: []
        },
        source: { type: "SourceButton", props: { label: "Open source", chunkId: "chk_1" }, children: [] }
      }
    });
    const answer = { ...message("msg_answer", "ses_new", "assistant", "I mapped the options."), citations: [cited], artifacts: [optionsArtifact] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect((await screen.findAllByText("Available Charts And Docs")).length).toBeGreaterThan(0);
    expect(screen.getByText("Proposal review")).toBeInTheDocument();
    expect(screen.getByText("4월")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Copy request/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Open source" })[0]);
    expect(await screen.findByText("Source excerpt")).toBeInTheDocument();
  });

  it("renders bullet glyph assistant responses as lists", async () => {
    const answer = message("msg_answer", "ses_new", "assistant", "Options:\n\n• Roadmap chart\n• Executive summary");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("list")).toBeInTheDocument();
    expect(screen.getByText("Roadmap chart")).toBeInTheDocument();
    expect(screen.getByText("Executive summary")).toBeInTheDocument();
  });

  it("renders typed chart artifacts without crashing on data-shaped specs", async () => {
    const cited = citation("cit_1");
    const chartArtifact = artifact("art_chart", "chart", {
      data: [
        { label: "Yes", value: 10, source_id: 1 },
        { label: "No", value: 4, source_id: 1 }
      ],
      x_label: "Answer",
      y_label: "Count"
    });
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Here is the chart."), citations: [cited], artifacts: [chartArtifact] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([run("run_chart", "completed", "Make a chart", "msg_answer")]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Here is the chart.")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open source for Yes" }));
    expect(await screen.findByText("Source excerpt")).toBeInTheDocument();
  });

  it("renders line and pie chart artifacts with distinct native SVGs", async () => {
    const lineArtifact = {
      ...artifact("art_line", "chart", {
      chart_type: "line",
      values: [
        { label: "2026-01", value: 100, source_id: 1 },
        { label: "2026-02", value: 125, source_id: 1 },
        { label: "2026-03", value: 160, source_id: 1 }
      ],
      x_label: "Month",
      y_label: "Revenue"
      }),
      title: "Line chart"
    };
    const pieArtifact = {
      ...artifact("art_pie", "chart", {
        chart_type: "pie",
        values: [
          { label: "North", value: 60, source_id: 1 },
          { label: "South", value: 40, source_id: 1 }
        ],
        x_label: "Region",
        y_label: "Share"
      }),
      title: "Pie chart"
    };
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Here are charts."), citations: [citation("cit_1")], artifacts: [lineArtifact, pieArtifact] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Here are charts.")).toBeInTheDocument();
    expect(document.querySelector("svg.chart-line-svg")).toBeTruthy();
    expect(document.querySelector("svg.chart-pie-svg")).toBeTruthy();
    expect(document.querySelector(".chart-bar-row")).toBeFalsy();
    expect(screen.getByText("North")).toBeInTheDocument();
  });

  it("renders an insight narrative panel under chart artifacts", async () => {
    const chartArtifact = {
      ...artifact("art_chart", "chart", {
        chart_type: "line",
        values: [
          { label: "2026-05", value: 100, source_id: 1 },
          { label: "2026-06", value: 125, source_id: 1 }
        ],
        x_label: "forecast_month",
        y_label: "forecast_units",
        insight_narrative: {
          headline: "Forecast units are rising",
          meaning: "The x-axis is forecast_month and the measure is aggregated forecast_units.",
          evidence: ["forecast_month orders the trend.", "SKU is an identifier/dimension, not a measure."],
          so_what: "Treat this as a demand planning signal.",
          recommended_actions: ["Inspect region/SKU mix and validate stockout/allocation assumptions before action."],
          follow_up_questions: [
            {
              id: "q_mix",
              group: "data",
              question: "Which region/SKU combinations explain the change?",
              options: [{ id: "inspect", label: "Inspect mix" }],
              default_option: "inspect",
              requires_reference: true
            }
          ],
          caveats: ["Aggregated duplicate periods across regions and SKUs."],
          confidence: "high",
          source_columns: ["forecast_month", "forecast_units"]
        }
      }),
      title: "Forecast trend"
    };
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Here is the best chart."), citations: [citation("cit_1")], artifacts: [chartArtifact] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "regional_demand_forecast.csv")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([]);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Forecast units are rising")).toBeInTheDocument();
    expect(screen.getByText("Meaning")).toBeInTheDocument();
    expect(screen.getByText("Treat this as a demand planning signal.")).toBeInTheDocument();
    expect(screen.getByText("SKU is an identifier/dimension, not a measure.")).toBeInTheDocument();
    expect(screen.getByText("Inspect region/SKU mix and validate stockout/allocation assumptions before action.")).toBeInTheDocument();
    expect(screen.getByText("Which region/SKU combinations explain the change?")).toBeInTheDocument();
    expect(screen.getByText(/Confidence: high/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Notion" })).toHaveAttribute("href", "/api/sessions/ses_new/artifacts/art_chart/export?format=notion");
    expect(screen.getByRole("link", { name: "CSV" })).toHaveAttribute("href", "/api/sessions/ses_new/artifacts/art_chart/export?format=csv");
  });

  it("answers follow-up cards with selected options, notes, and ready reference files without disabling the composer", async () => {
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Here is the best chart."), artifacts: [artifact("art_chart", "chart", { values: [{ label: "May", value: 100 }] })] };
    const followUp: AgentRunQuestion = {
      ...planningQuestion("run_parent", "choice"),
      id: "ques_follow",
      blocking: false,
      phase: "follow_up",
      question: "Which region/SKU combinations explain the change?",
      options: [
        { id: "inspect_mix", label: "Inspect mix", description: "" },
        { id: "compare_segments", label: "Compare segments", description: "" }
      ],
      default_option: "inspect_mix",
      card: {
        title: "Question to answer next",
        prompt: "Which region/SKU combinations explain the change?",
        group: "data",
        options: [],
        allow_free_text: true,
        allow_file_reference: true,
        allow_multi_select: false,
        submit_label: "Start follow-up"
      },
      parent_message_id: "msg_answer",
      parent_artifact_id: "art_chart"
    };
    const parentRun = { ...run("run_parent", "completed", "best chart for this file", "msg_answer"), follow_up_questions: [followUp] };
    const childRun = { ...run("run_child", "queued", "Follow up on the completed chart insight."), parent_run_id: "run_parent", trigger_question_id: "ques_follow" };
    let runsPayload: AgentRun[] = [parentRun];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_forecast", "regional_demand_forecast.csv")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs/run_parent/questions/ques_follow/answer") && init?.method === "POST") {
        runsPayload = [{ ...parentRun, follow_up_questions: [] }, childRun];
        return Response.json(childRun);
      }
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json(runsPayload);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const followUpCards = await screen.findByLabelText("Follow-up questions");
    expect(followUpCards).toBeInTheDocument();
    expect(screen.getByLabelText("Ask a question about the selected files")).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Compare segments" }));
    fireEvent.change(screen.getByLabelText("Follow-up note"), { target: { value: "Focus on West region." } });
    fireEvent.click(within(followUpCards).getByLabelText(/regional_demand_forecast\.csv/));
    fireEvent.click(screen.getByRole("button", { name: /Start follow-up/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/sessions/ses_new/runs/run_parent/questions/ques_follow/answer",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            selected_option: "compare_segments",
            free_text: "Focus on West region.",
            attached_file_ids: ["fil_forecast"]
          })
        })
      );
    });
  });

  it("answers Available Charts And Docs with multi-select option ids", async () => {
    const answer = {
      ...message("msg_answer", "ses_new", "assistant", "I mapped the options."),
      artifacts: [artifact("art_options", "decision_cards", {
        root: "card",
        elements: {
          card: { type: "ArtifactCard", props: { title: "Available Charts And Docs" }, children: ["option_1", "option_2"] },
          option_1: { type: "TextBlock", props: { text: "Revenue line chart" }, children: [] },
          option_2: { type: "TextBlock", props: { text: "Executive summary" }, children: [] }
        },
        decision_options: [
          { id: "chart_revenue", label: "Revenue line chart", description: "Trend revenue by month.", artifact_kind: "chart", chart_type: "line", produce_payload: { instruction: "server-owned" } },
          { id: "summary_exec", label: "Executive summary", description: "Summarize grounded findings.", artifact_kind: "summary_panel", produce_payload: { instruction: "server-owned" } }
        ]
      })]
    };
    const followUp = {
      ...planningQuestion("run_parent", "artifact_choice"),
      id: "ques_artifacts",
      kind: "artifact_choice",
      blocking: false,
      phase: "artifact_choice",
      question: "Select one or more artifacts to produce.",
      options: [
        { id: "chart_revenue", label: "Revenue line chart", description: "Trend revenue by month." },
        { id: "summary_exec", label: "Executive summary", description: "Summarize grounded findings." }
      ],
      default_option: "",
      card: {
        title: "Available Charts And Docs",
        prompt: "Select one or more artifacts to produce.",
        group: "business",
        options: [],
        allow_free_text: false,
        allow_file_reference: false,
        allow_multi_select: true,
        submit_label: "Produce selected"
      },
      parent_message_id: "msg_answer",
      parent_artifact_id: "art_options"
    } as AgentRunQuestion;
    const parentRun = { ...run("run_parent", "completed", "what charts and docs can you make with this?", "msg_answer"), follow_up_questions: [followUp] };
    const childRun = { ...run("run_child", "queued", "Produce selected artifacts from the current session sources."), parent_run_id: "run_parent", trigger_question_id: "ques_artifacts" };
    let runsPayload: AgentRun[] = [parentRun];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "monthly_revenue.csv")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      if (url.endsWith("/api/sessions/ses_new/runs/run_parent/questions/ques_artifacts/answer") && init?.method === "POST") {
        runsPayload = [{ ...parentRun, follow_up_questions: [] }, childRun];
        return Response.json(childRun);
      }
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json(runsPayload);
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect((await screen.findAllByText("Available Charts And Docs")).length).toBeGreaterThan(0);
    const produce = screen.getByRole("button", { name: /Produce selected/i });
    expect(produce).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /Revenue line chart/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Executive summary/i }));
    expect(produce).not.toBeDisabled();
    fireEvent.click(produce);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/sessions/ses_new/runs/run_parent/questions/ques_artifacts/answer",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            free_text: "",
            answer: { selected_options: ["chart_revenue", "summary_exec"] }
          })
        })
      );
    });
    expect(await screen.findByText("Produce selected artifacts from the current session sources.")).toBeInTheDocument();
  });

  it("keeps supporting artifacts out of the transcript but available in the artifacts panel", async () => {
    const chartArtifact = { ...artifact("art_chart", "chart", { values: [{ label: "Theme", value: 3, source_id: 1 }] }), title: "Survey themes" };
    const tableArtifact = {
      ...artifact("art_table", "table", {
        root: "card",
        elements: {
          card: { type: "ArtifactCard", props: { title: "Survey data preview" }, children: ["table"] },
          table: { type: "DataTable", props: { columns: ["A"], rows: [["raw"]] }, children: [] }
        }
      }),
      title: "Survey data preview",
      display_mode: "supporting" as const
    };
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Here is the chart."), citations: [citation("cit_1")], artifacts: [chartArtifact, tableArtifact] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Survey themes")).toBeInTheDocument();
    expect(screen.queryByText("Survey data preview")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "artifacts" }));
    expect(await screen.findByLabelText("Artifact list")).toHaveTextContent("Survey data preview");
  });

  it("shows a friendly chart render fallback for malformed chart artifacts", async () => {
    const brokenChart = artifact("art_chart", "chart", { data: "bad" });
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Here is the chart."), citations: [citation("cit_1")], artifacts: [brokenChart] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText(/could not be rendered/)).toBeInTheDocument();
  });

  it("renders file draft export controls", async () => {
    const draft = artifact("art_draft", "file_draft", {
      filename: "memo.md",
      format: "markdown",
      content: "# Memo\n\nGrounded draft.",
      open_design: { material_type: "docs_page" }
    });
    const answer = { ...message("msg_answer", "ses_new", "assistant", "I drafted a file."), citations: [citation("cit_1")], artifacts: [draft] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("memo.md")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Markdown" })).toHaveAttribute("href", "/api/sessions/ses_new/artifacts/art_draft/export?format=md");
    expect(screen.getByRole("link", { name: "JSON" })).toHaveAttribute("href", "/api/sessions/ses_new/artifacts/art_draft/export?format=json");
    expect(screen.getByRole("link", { name: "Open Design ZIP" })).toHaveAttribute("href", "/api/sessions/ses_new/artifacts/art_draft/export?format=od");
  });

  it("hides Open Design ZIP export for normal file drafts", async () => {
    const draft = artifact("art_draft", "file_draft", {
      filename: "memo.md",
      format: "markdown",
      content: "# Memo\n\nGrounded draft."
    });
    const answer = { ...message("msg_answer", "ses_new", "assistant", "I drafted a file."), citations: [citation("cit_1")], artifacts: [draft] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("memo.md")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Design ZIP" })).not.toBeInTheDocument();
  });

  it("minimizes transcript sources by default", async () => {
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Answer"), citations: [citation("cit_1")] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Sources · 1")).toBeInTheDocument();
  });

  it("surfaces persisted agent activity in the runs panel", async () => {
    const completedRun = run("run_done", "completed", "Summarize", "msg_answer");
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Answer"), citations: [citation("cit_1")] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([completedRun]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "runs" }));
    expect(await screen.findByText("Agent activity")).toBeInTheDocument();
    expect(screen.getAllByText("plan task").length).toBeGreaterThan(0);
    expect(screen.getAllByText("persist response").length).toBeGreaterThan(0);
  });

  it("shows planner intent and executable bundle for completed runs with warnings", async () => {
    const warnedRun = run("run_warned", "completed_with_warning", "각 사 워크샵 설계 자료 제작", "msg_answer");
    warnedRun.kind = "create";
    warnedRun.task_contract = {
      planner_contract: {
        required_outputs: ["summary_panel", "chart", "file_draft"],
        deliverable: "워크샵 설계 자료"
      },
      executable_contract: {
        primary_outputs: ["file_draft", "chart"],
        supporting_outputs: ["summary_panel"]
      },
      contract_adjustments: [
        "Downgraded summary_panel to a supporting artifact because the survey path guarantees a draft + chart bundle."
      ]
    };
    warnedRun.review_scores = {
      passed: true,
      outcome: "completed_with_warning",
      warnings: ["Missing supporting artifact: summary_panel."]
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([message("msg_answer", "ses_new", "assistant", "Answer")]);
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([warnedRun]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "runs" }));
    expect(await screen.findByText("completed with warning")).toBeInTheDocument();
    expect(screen.getByText("Planner intent")).toBeInTheDocument();
    expect(screen.getByText("Executable bundle")).toBeInTheDocument();
    expect(screen.getByText("Contract adjustments")).toBeInTheDocument();
  });

  it("renders in-pipeline planning questions and resumes with the selected answer", async () => {
    const waitingRun = run("run_wait", "awaiting_user_input", "분석 자료 제작");
    waitingRun.current_question = planningQuestion(waitingRun.id);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_survey", "survey.csv")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([message("msg_user", "ses_new", "user", "분석 자료 제작")]);
      if (url.endsWith("/api/sessions/ses_new/runs/run_wait/questions/ques_1/answer") && init?.method === "POST") {
        return Response.json({ ...waitingRun, status: "queued", current_question: null });
      }
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([waitingRun]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByLabelText("Planning question")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Handle automatically/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/sessions/ses_new/runs/run_wait/questions/ques_1/answer",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ selected_option: "automatic", free_text: "" }) })
      );
    });
  });

  it("shows artifact recommendation choices and resumes with the selected option", async () => {
    const waitingRun = run("run_wait", "awaiting_user_input", "best graph for this file");
    waitingRun.current_question = {
      ...planningQuestion(waitingRun.id, "choice"),
      kind: "artifact_choice",
      question: "Choose an artifact to create from this file.",
      options: [
        { id: "chart_line_month_revenue", label: "Line chart", description: "Month gives an ordered x-axis and Revenue is numeric." },
        { id: "table_preview", label: "Comparison table", description: "SKU and inventory columns form business records." }
      ],
      default_option: "chart_line_month_revenue"
    } as AgentRunQuestion;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_revenue", "monthly_revenue.csv")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([message("msg_user", "ses_new", "user", "best graph for this file")]);
      if (url.endsWith("/api/sessions/ses_new/runs/run_wait/questions/ques_1/answer") && init?.method === "POST") {
        return Response.json({ ...waitingRun, status: "queued", current_question: null });
      }
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([waitingRun]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Choose an artifact to create from this file.")).toBeInTheDocument();
    expect(screen.getByText("Month gives an ordered x-axis and Revenue is numeric.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Line chart/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/sessions/ses_new/runs/run_wait/questions/ques_1/answer",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ selected_option: "chart_line_month_revenue", free_text: "" }) })
      );
    });
  });

  it("shows failed action errors in the runs panel", async () => {
    const failedRun = run("run_failed", "failed", "Make a chart");
    failedRun.error = "Selected chat model did not return structured output.";
    failedRun.actions = failedRun.actions.map((item) => item.kind === "write" ? { ...item, status: "failed", error_summary: "Selected chat model did not return structured output." } : item);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([failedRun]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "runs" }));
    expect(await screen.findAllByText("Selected chat model did not return structured output.")).not.toHaveLength(0);
  });

  it("shows degraded vector search as a warning while the run completes", async () => {
    const degradedRun = run("run_degraded", "completed", "분석 자료 제작", "msg_answer");
    degradedRun.kind = "create";
    degradedRun.actions = degradedRun.actions.map((item) => item.kind === "load_sources" ? {
      ...item,
      status: "completed",
      output_summary: "Loaded ready source files; vector search unavailable",
      output_json: {
        vector_search_status: "unavailable_auth",
        vector_search_error: "Client error '401 Unauthorized' for url 'https://openrouter.ai/api/v1/embeddings'"
      }
    } : item);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([]);
      if (url.endsWith("/api/sessions/ses_new/runs")) return Response.json([degradedRun]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "runs" }));
    expect(await screen.findByText("Loaded ready source files; vector search unavailable")).toBeInTheDocument();
    expect(screen.getByText("OpenRouter key needs attention")).toBeInTheDocument();
    expect(screen.queryByText("failed")).not.toBeInTheDocument();
  });

  it("falls back to code for invalid mermaid artifacts", async () => {
    const brokenArtifact = artifact("art_mermaid", "mermaid", { diagram: "not a valid mermaid diagram" });
    const answer = { ...message("msg_answer", "ses_new", "assistant", "Here is the flowchart."), citations: [citation("cit_1")], artifacts: [brokenArtifact] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return Response.json(currentUser);
      if (url.endsWith("/api/settings")) return Response.json(settings);
      if (url.endsWith("/api/sessions") && init?.method === "POST") return Response.json(session("ses_new"));
      if (url.endsWith("/api/sessions")) return Response.json([session("ses_new", "New reading session", 1)]);
      if (url.endsWith("/api/sessions/ses_new/files")) return Response.json([file("fil_report", "report.txt")]);
      if (url.endsWith("/api/sessions/ses_new/messages")) return Response.json([answer]);
      if (url.endsWith("/api/sessions/ses_new/usage")) return Response.json({});
      return Response.json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Flowchart")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("not a valid mermaid diagram")).toBeInTheDocument());
  });
});
