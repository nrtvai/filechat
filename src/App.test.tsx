import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { API_UNAVAILABLE_MESSAGE } from "./api";
import type { AgentRun, AgentRunQuestion, AgentRunStep, Artifact, Citation, CurrentUser, FileRecord, Message, Session, Settings } from "./types";

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

function step(id: string, run_id: string, phase: AgentRunStep["phase"], status: AgentRunStep["status"], ordinal: number): AgentRunStep {
  return {
    id,
    run_id,
    phase,
    ordinal,
    status,
    summary: `${phase} ${status}`,
    detail: {},
    created_at: "",
    updated_at: ""
  };
}

function run(id: string, status: AgentRun["status"], question = "Summarize", assistant_message_id: string | null = null): AgentRun {
  const phases: AgentRunStep["phase"][] = ["plan", "search", "analysis", "writing", "review", "implement"];
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
    prompt_context: {},
    provider_status: {},
    agent_actions: [],
    review_scores: {},
    revision_required: false,
    model_assignments: {},
    tool_calls: [],
    artifact_versions: [],
    repair_attempts: [],
    quality_warnings: [],
    steps: phases.map((phase, index) => step(`step_${phase}`, id, phase, status === "completed" ? "completed" : index === 1 ? "running" : index === 0 ? "completed" : "pending", index + 1)),
    created_at: "",
    updated_at: ""
  };
}

function planningQuestion(run_id: string, kind: AgentRunQuestion["kind"] = "choice"): AgentRunQuestion {
  return {
    id: "ques_1",
    run_id,
    phase: "plan",
    kind,
    question: "어떤 의사결정에 바로 쓸 수 있는 분석 자료가 필요하신가요?",
    options: [
      { id: "leadership_report", label: "리더 공유용", description: "핵심 인사이트와 실행 제안을 우선합니다." },
      { id: "team_workshop", label: "팀 워크숍용", description: "토론 질문과 병목 유형을 우선합니다." }
    ],
    default_option: "leadership_report",
    status: "pending",
    created_at: "",
    updated_at: ""
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
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

  it("shows a clear API offline message when the dev server is not reachable", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText(API_UNAVAILABLE_MESSAGE)).toBeInTheDocument();
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
    fireEvent.change(input, { target: { value: "Make a chart about the survey result" } });

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
    fireEvent.change(input, { target: { value: "Summarize this file" } });
    const sendButton = screen.getAllByRole("button", { name: /ask/i }).find((button) => !button.hasAttribute("disabled"));
    expect(sendButton).toBeDefined();
    fireEvent.click(sendButton!);

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
    fireEvent.change(input, { target: { value: "First offline ask" } });
    fireEvent.click(screen.getAllByRole("button", { name: /ask/i }).find((button) => !button.hasAttribute("disabled"))!);

    expect(await screen.findByText(API_UNAVAILABLE_MESSAGE)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("First offline ask")).toBeInTheDocument());

    const transcriptInput = screen.getByLabelText("Ask a question about the selected files");
    fireEvent.change(transcriptInput, { target: { value: "Second offline ask" } });
    fireEvent.click(screen.getAllByRole("button", { name: /ask/i }).find((button) => !button.hasAttribute("disabled"))!);

    await waitFor(() => expect(screen.getByText("Second offline ask")).toBeInTheDocument());
    expect(consoleError.mock.calls.some((call) => String(call[0]).includes("Encountered two children with the same key"))).toBe(false);
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
    fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
    expect(await screen.findByText("Artifacts · this session")).toBeInTheDocument();
    expect(screen.getByText("1 source chunk")).toBeInTheDocument();
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
    expect(screen.getByRole("link", { name: "Markdown" })).toHaveAttribute("href", "/api/sessions/ses_new/artifacts/art_draft/export?format=md");
    expect(screen.getByRole("link", { name: "JSON" })).toHaveAttribute("href", "/api/sessions/ses_new/artifacts/art_draft/export?format=json");
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

  it("surfaces persisted phase timelines in the runs panel", async () => {
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
    expect(screen.getAllByText("plan").length).toBeGreaterThan(0);
    expect(screen.getAllByText("implement").length).toBeGreaterThan(0);
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
    fireEvent.click(screen.getByRole("button", { name: /리더 공유용/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/sessions/ses_new/runs/run_wait/questions/ques_1/answer",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ selected_option: "leadership_report", free_text: "" }) })
      );
    });
  });

  it("shows failed phase errors in the runs panel", async () => {
    const failedRun = run("run_failed", "failed", "Make a chart");
    failedRun.error = "Selected chat model did not return structured output.";
    failedRun.steps = failedRun.steps.map((item) => item.phase === "writing" ? { ...item, status: "failed", error: "Selected chat model did not return structured output." } : item);
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
    degradedRun.steps = degradedRun.steps.map((item) => item.phase === "search" ? {
      ...item,
      status: "completed",
      summary: "Loaded ready source files; vector search unavailable",
      detail: {
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
