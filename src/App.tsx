import { ChangeEvent, FormEvent, KeyboardEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { FileText, KeyRound, Library, Loader2, MessageSquarePlus, PanelLeft, Paperclip, Search, Send, Settings as SettingsIcon, ShieldCheck, X } from "lucide-react";
import { api } from "./api";
import { ArtifactRenderer } from "./artifacts";
import type { AgentRun, AgentRunAction, AgentRunQuestion, Artifact, Citation, ContextProfile, CurrentUser, Edition, FileRecord, MembershipRole, Message, ModelInfo, Session, Settings, UsageSummary } from "./types";

const acceptedTypes = ".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.csv,.txt,.md,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.gif";
const appVersion = import.meta.env.VITE_APP_VERSION ?? "0.0.0";
type RightTab = "files" | "citations" | "artifacts" | "runs" | "settings" | "admin";
const emptyUsageSummary: UsageSummary = {
  chat_prompt_tokens: 0,
  chat_completion_tokens: 0,
  embedding_tokens: 0,
  chat_prompt_cost: 0,
  chat_completion_cost: 0,
  embedding_cost: 0,
  total_tokens: 0,
  total_cost: 0
};
const defaultContextProfile: ContextProfile = {
  artifact_policy: "chart+draft",
  citation_display: "minimized",
  drafting_policy: "model_polished_evidence",
  title_style: "localized_subject_first"
};

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatCost(value?: number) {
  const cost = value ?? 0;
  if (cost === 0) return "$0.00";
  if (cost < 0.01) return `$${cost.toFixed(5)}`;
  return `$${cost.toFixed(2)}`;
}

function formatTokens(value?: number) {
  return `${Math.round(value ?? 0).toLocaleString()} tok`;
}

function handleFileInputChange(event: ChangeEvent<HTMLInputElement>, upload: (files: File[]) => void | Promise<void>) {
  const input = event.currentTarget;
  const selectedFiles = Array.from(input.files ?? []);
  try {
    if (selectedFiles.length > 0) void upload(selectedFiles);
  } finally {
    input.value = "";
  }
}

function statusLabel(file: FileRecord) {
  if (file.status === "failed") return `Failed · ${fileErrorSummary(file)}`;
  if (file.status === "ready") return `Ready · ${file.chunk_count} chunks`;
  return `${file.status} · ${Math.round(file.progress * 100)}%`;
}

function fileErrorSummary(file: FileRecord) {
  const error = file.error || "Indexing failed";
  return providerErrorSummary(error, "Indexing failed");
}

function providerErrorSummary(error: string, fallback = "Provider issue") {
  if (error.toLowerCase().includes("openrouter authentication failed") || error.includes("401 Unauthorized")) {
    return "OpenRouter key needs attention";
  }
  if (error.toLowerCase().includes("api key")) return "OpenRouter API key needs attention";
  if (error.toLowerCase().includes("openrouter")) return "OpenRouter provider issue";
  if (!error.trim()) return fallback;
  return error;
}

function contextStatus(file: FileRecord) {
  if (file.status === "ready") return "ready to cite";
  if (file.status === "failed") return "failed";
  return `${Math.round(file.progress * 100)}%`;
}

function localTestModeUser(): CurrentUser {
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

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [contextProfile, setContextProfile] = useState<ContextProfile>(defaultContextProfile);
  const [usageSummary, setUsageSummary] = useState<UsageSummary>(emptyUsageSummary);
  const [composer, setComposer] = useState("");
  const [railOpen, setRailOpen] = useState(true);
  const [railMode, setRailMode] = useState<"sessions" | "files">("sessions");
  const [rightOpen, setRightOpen] = useState(false);
  const [rightTab, setRightTab] = useState<RightTab>("citations");
  const [busy, setBusy] = useState(false);
  const [activeLoading, setActiveLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [highlightCitationId, setHighlightCitationId] = useState<string | null>(null);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  const optimisticMessageSeq = useRef(0);

  const activeSession = sessions.find((session) => session.id === activeSessionId) ?? null;
  const readyFiles = files.filter((file) => file.status === "ready");
  const canAskActiveSession = !activeLoading && readyFiles.length > 0;
  const activeCitations = messages.flatMap((message) => message.citations);
  const activeArtifacts = messages.flatMap((message) => message.artifacts);
  const hasWorkingFiles = files.some((file) => ["queued", "reading", "indexing"].includes(file.status));
  const hasWorkingRuns = runs.some((run) => ["queued", "running"].includes(run.status));
  const effectiveBusy = busy || hasWorkingRuns;

  const refreshSessions = useCallback(async () => {
    const next = await api.sessions();
    setSessions(next);
    return next;
  }, []);

  const setActiveSessionError = useCallback((sessionId: string, message: string) => {
    if (activeSessionIdRef.current === sessionId) setError(message);
  }, []);

  const refreshActive = useCallback(async (sessionId: string) => {
    try {
      const [nextFiles, nextMessages, nextUsage, nextRuns] = await Promise.all([
        api.files(sessionId),
        api.messages(sessionId),
        api.usage(sessionId),
        api.runs(sessionId).catch(() => [] as AgentRun[]),
      ]);
      if (activeSessionIdRef.current !== sessionId) return;
      setFiles(nextFiles);
      setMessages(nextMessages);
      setUsageSummary({ ...emptyUsageSummary, ...nextUsage });
      setRuns(Array.isArray(nextRuns) ? nextRuns : []);
    } finally {
      if (activeSessionIdRef.current === sessionId) setActiveLoading(false);
    }
  }, []);

  const refreshIdentity = useCallback(async () => {
    const [nextUser, nextSettings] = await Promise.all([api.me(), api.settings()]);
    setCurrentUser(nextUser);
    setSettings(nextSettings);
    setError(null);
    setRightTab((tab) => (!nextUser.capabilities.use_admin_console && tab === "admin" ? "settings" : tab));
    return nextUser;
  }, []);

  useEffect(() => {
    let mounted = true;
    refreshIdentity()
      .catch(async (err: Error) => {
        if (!mounted) return;
        setCurrentUser(localTestModeUser());
        setError(err.message);
        try {
          const nextSettings = await api.settings();
          if (mounted) setSettings(nextSettings);
        } catch {
          // Keep the primary identity error visible; the retry loop below will reconcile when the API recovers.
        }
      });
    Promise.all([api.contextProfile(), api.createSession()])
      .then(async ([nextProfile, created]) => {
        if (!mounted) return;
        setContextProfile({ ...defaultContextProfile, ...nextProfile });
        activeSessionIdRef.current = created.id;
        setActiveLoading(true);
        setActiveSessionId(created.id);
        setFiles([]);
        setMessages([]);
        setRuns([]);
        setUsageSummary(emptyUsageSummary);
        const nextSessions = await api.sessions();
        if (mounted) setSessions(nextSessions);
      })
      .catch((err: Error) => setError(err.message));
    return () => { mounted = false; };
  }, [refreshIdentity]);

  useEffect(() => {
    if (currentUser?.auth_mode !== "local_mode_switcher_fallback") return;
    const handle = setInterval(() => {
      refreshIdentity().catch(() => undefined);
    }, 5000);
    return () => clearInterval(handle);
  }, [currentUser?.auth_mode, refreshIdentity]);

  useEffect(() => {
    if (!activeSessionId) return;
    refreshActive(activeSessionId).catch((err: Error) => setActiveSessionError(activeSessionId, err.message));
  }, [activeSessionId, refreshActive, setActiveSessionError]);

  useEffect(() => {
    if (!activeSessionId || (!hasWorkingFiles && !hasWorkingRuns)) return;
    const handle = setInterval(() => {
      refreshActive(activeSessionId).catch((err: Error) => setActiveSessionError(activeSessionId, err.message));
      refreshSessions().catch(() => undefined);
    }, 1400);
    return () => clearInterval(handle);
  }, [activeSessionId, hasWorkingFiles, hasWorkingRuns, refreshActive, refreshSessions, setActiveSessionError]);

  const upsertRun = (run: AgentRun) => {
    setRuns((current) => {
      const rest = current.filter((item) => item.id !== run.id);
      return [run, ...rest].sort((a, b) => b.created_at.localeCompare(a.created_at));
    });
  };

  const upload = async (uploadFiles: File[]) => {
    if (!activeSessionId || uploadFiles.length === 0) return;
    const sessionId = activeSessionId;
    setError(null);
    setBusy(true);
    try {
      await api.uploadFiles(sessionId, uploadFiles);
      await refreshActive(sessionId);
      await refreshSessions();
      setRailMode("files");
    } catch (err) {
      setActiveSessionError(sessionId, err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  };

  const ask = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!activeSessionId || activeLoading || !composer.trim() || readyFiles.length === 0) return;
    const sessionId = activeSessionId;
    const question = composer.trim();
    setComposer("");
    setBusy(true);
    setError(null);
    try {
      const userShadow: Message = {
        id: `pending-user-${Date.now()}-${optimisticMessageSeq.current++}`,
        session_id: sessionId,
        role: "user",
        content: question,
        unavailable_file_ids: [],
        created_at: new Date().toISOString(),
        citations: [],
        artifacts: []
      };
      setMessages((current) => [...current, userShadow]);
      const run = await api.startRun(sessionId, question);
      upsertRun(run);
      await refreshActive(sessionId);
      await refreshSessions();
      setRightTab("runs");
    } catch (err) {
      setActiveSessionError(sessionId, err instanceof Error ? err.message : "Question failed");
      await refreshActive(sessionId).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const createSession = async () => {
    const created = await api.createSession();
    activeSessionIdRef.current = created.id;
    setActiveLoading(true);
    setActiveSessionId(created.id);
    setFiles([]);
    setMessages([]);
    setRuns([]);
    setUsageSummary(emptyUsageSummary);
    await refreshSessions();
  };

  const selectSession = (sessionId: string) => {
    activeSessionIdRef.current = sessionId;
    setActiveLoading(true);
    setActiveSessionId(sessionId);
    setFiles([]);
    setMessages([]);
    setRuns([]);
    setUsageSummary(emptyUsageSummary);
  };

  const detachFile = async (fileId: string) => {
    if (!activeSessionId) return;
    const sessionId = activeSessionId;
    setError(null);
    try {
      await api.detachFile(sessionId, fileId);
      await refreshActive(sessionId);
      await refreshSessions();
    } catch (err) {
      setActiveSessionError(sessionId, err instanceof Error ? err.message : "Could not remove file from context");
    }
  };

  const retryFile = async (fileId: string) => {
    if (!activeSessionId) return;
    const sessionId = activeSessionId;
    setError(null);
    try {
      await api.retryFile(sessionId, fileId);
      await refreshActive(sessionId);
      await refreshSessions();
    } catch (err) {
      setActiveSessionError(sessionId, err instanceof Error ? err.message : "Could not retry indexing");
    }
  };

  const retryFailedFiles = async () => {
    const failedFiles = files.filter((file) => file.status === "failed");
    if (failedFiles.length === 0) return;
    setBusy(true);
    try {
      await Promise.all(failedFiles.map((file) => retryFile(file.id)));
    } finally {
      setBusy(false);
    }
  };

  const approveRun = async (runId: string) => {
    if (!activeSessionId) return;
    const sessionId = activeSessionId;
    setError(null);
    try {
      const run = await api.approveRun(sessionId, runId);
      upsertRun(run);
      await refreshActive(sessionId);
    } catch (err) {
      setActiveSessionError(sessionId, err instanceof Error ? err.message : "Could not approve agent plan");
    }
  };

  const retryRun = async (runId: string, mode: "repair" | "rerun" = "rerun") => {
    if (!activeSessionId) return;
    const sessionId = activeSessionId;
    setError(null);
    try {
      const run = await api.retryRun(sessionId, runId, mode);
      upsertRun(run);
      await refreshActive(sessionId);
      setRightTab("runs");
    } catch (err) {
      setActiveSessionError(sessionId, err instanceof Error ? err.message : "Could not retry agent run");
    }
  };

  const answerRunQuestion = async (
    runId: string,
    questionId: string,
    selectedOption: string | null,
    freeText = "",
    attachedFileIds: string[] = [],
    answer: Record<string, unknown> = {}
  ) => {
    if (!activeSessionId) return;
    const sessionId = activeSessionId;
    setError(null);
    try {
      const run = await api.answerRunQuestion(sessionId, runId, questionId, selectedOption, freeText, attachedFileIds, answer);
      upsertRun(run);
      await refreshActive(sessionId);
      setRightTab("runs");
    } catch (err) {
      setActiveSessionError(sessionId, err instanceof Error ? err.message : "Could not answer planning question");
    }
  };

  const openSettings = () => {
    setRightOpen(true);
    setRightTab(currentUser?.capabilities.use_admin_console ? "admin" : "settings");
  };

  const onCitationClick = (citation: Citation) => {
    setRightOpen(true);
    setRightTab("citations");
    setHighlightCitationId(citation.id);
    window.setTimeout(() => {
      const target = document.getElementById(`citation-${citation.id}`);
      if (target && typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }, 60);
  };

  const onArtifactSelect = (artifact: Artifact) => {
    setSelectedArtifactId(artifact.id);
    setRightOpen(true);
    setRightTab("artifacts");
  };

  const updateSettings = async (patch: Record<string, unknown>) => {
    try {
      await api.health();
      const next = await api.patchSettings(patch);
      setSettings(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Settings update failed");
      throw err;
    }
  };

  const updateAdminSettings = async (patch: Record<string, unknown>) => {
    try {
      await api.health();
      const next = await api.patchAdminSettings(patch);
      setSettings(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Admin settings update failed");
      throw err;
    }
  };

  const clearOpenRouterKey = async () => {
    try {
      await api.health();
      const next = await api.clearOpenRouterKey();
      setSettings(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear OpenRouter key");
      throw err;
    }
  };

  const setTestMode = async (edition: Edition, role: MembershipRole) => {
    api.setTestMode(edition, role);
    if (currentUser?.auth_mode === "local_mode_switcher_fallback") {
      setCurrentUser(localTestModeUser());
    }
    try {
      await refreshIdentity();
    } catch (err) {
      setCurrentUser(localTestModeUser());
      setError(err instanceof Error ? err.message : "Could not switch local test mode");
    }
  };

  const updateContextProfile = async (patch: Partial<ContextProfile>) => {
    const next = await api.patchContextProfile(patch);
    setContextProfile(next);
  };

  const screenState = useMemo(() => {
    if (activeLoading) return "loading";
    if (messages.length > 0) return "answered";
    if (files.length === 0) return "empty";
    if (readyFiles.length === files.length) return "ready";
    return "processing";
  }, [activeLoading, files, messages.length, readyFiles.length]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="icon-btn" onClick={() => setRailOpen((open) => !open)} aria-label="Toggle sidebar"><PanelLeft size={16} /></button>
        <div className="brand">
          <span>FileChat</span>
          <span className="mono subtle">local · v{appVersion}</span>
        </div>
        <div className="topbar-spacer" />
        {currentUser && <EditionControls user={currentUser} setTestMode={setTestMode} />}
        <div className="provider-pill"><span className={settings?.openrouter_key_configured ? "dot ready" : "dot warn"} /> OpenRouter · {settings?.chat_model ?? "loading"}</div>
        <div className="mono caps subtle">grounded · strict</div>
      </header>

      <div className="workspace">
        <LeftRail
          open={railOpen}
          mode={railMode}
          setMode={setRailMode}
          sessions={sessions}
          activeSessionId={activeSessionId}
          setActiveSessionId={selectSession}
          files={files}
          createSession={createSession}
        />
        <main className="center-pane">
          {error && <div className="error-banner">{error}</div>}
          {screenState === "empty" && (
            <EmptyState
              upload={upload}
              busy={effectiveBusy || !activeSessionId}
              composer={composer}
              setComposer={setComposer}
              ask={ask}
              canAsk={canAskActiveSession}
              onDetachFile={detachFile}
            />
          )}
          {screenState !== "empty" && screenState !== "answered" && (
            <ProcessingView
              files={files}
              upload={upload}
              busy={effectiveBusy}
              activeLoading={screenState === "loading"}
              composer={composer}
              setComposer={setComposer}
              ask={ask}
              canAsk={canAskActiveSession}
              onDetachFile={detachFile}
              onRetryFailedFiles={retryFailedFiles}
              openSettings={openSettings}
            />
          )}
          {screenState === "answered" && (
            <Transcript
              messages={messages}
              runs={runs}
              files={files}
              upload={upload}
              usageSummary={usageSummary}
              composer={composer}
              setComposer={setComposer}
              ask={ask}
              canAsk={canAskActiveSession && !effectiveBusy}
              busy={effectiveBusy}
              onCitationClick={onCitationClick}
              onArtifactSelect={onArtifactSelect}
              onDetachFile={detachFile}
              onAnswerRunQuestion={answerRunQuestion}
              contextProfile={contextProfile}
            />
          )}
        </main>
        <RightPanel
          open={rightOpen}
          setOpen={setRightOpen}
          tab={rightTab}
          setTab={setRightTab}
          files={files}
          citations={activeCitations}
          artifacts={activeArtifacts}
          runs={runs}
          selectedArtifactId={selectedArtifactId}
          selectArtifactId={setSelectedArtifactId}
          usageSummary={usageSummary}
          settings={settings}
          contextProfile={contextProfile}
          updateSettings={updateSettings}
          updateAdminSettings={updateAdminSettings}
          updateContextProfile={updateContextProfile}
          clearOpenRouterKey={clearOpenRouterKey}
          approveRun={approveRun}
          retryRun={retryRun}
          answerRunQuestion={answerRunQuestion}
          highlightCitationId={highlightCitationId}
          activeSession={activeSession}
          currentUser={currentUser}
        />
      </div>
    </div>
  );
}

function modeValue(user: CurrentUser) {
  if (!user.enterprise_enabled) return "community";
  return `enterprise:${user.role}`;
}

function parseMode(value: string): { edition: Edition; role: MembershipRole } {
  if (value === "community") return { edition: "community", role: "owner" };
  const role = value.split(":")[1];
  return {
    edition: "enterprise",
    role: role === "owner" || role === "admin" || role === "member" ? role : "admin",
  };
}

function EditionControls({ user, setTestMode }: { user: CurrentUser; setTestMode: (edition: Edition, role: MembershipRole) => Promise<void> }) {
  const canSwitchMode = Boolean(user.capabilities.switch_test_mode);
  return (
    <div className="edition-controls">
      <div className={`edition-pill ${user.enterprise_enabled ? "enterprise" : "community"}`}>
        <ShieldCheck size={13} />
        <span>{user.enterprise_enabled ? "Enterprise" : "Community"}</span>
        <small>{user.role}</small>
      </div>
      {canSwitchMode && (
        <label className="role-switch">
          <span className="mono caps">test mode</span>
          <select
            aria-label="Test mode"
            value={modeValue(user)}
            onChange={(event) => {
              const next = parseMode(event.target.value);
              void setTestMode(next.edition, next.role);
            }}
          >
            <option value="community">Community</option>
            <option value="enterprise:owner">Enterprise owner</option>
            <option value="enterprise:admin">Enterprise admin</option>
            <option value="enterprise:member">Enterprise member</option>
          </select>
        </label>
      )}
    </div>
  );
}

function LeftRail(props: {
  open: boolean;
  mode: "sessions" | "files";
  setMode: (mode: "sessions" | "files") => void;
  sessions: Session[];
  activeSessionId: string | null;
  setActiveSessionId: (id: string) => void;
  files: FileRecord[];
  createSession: () => void;
}) {
  const [sessionSearch, setSessionSearch] = useState("");
  const normalizedSessionSearch = sessionSearch.trim().toLowerCase();
  const visibleSessions = normalizedSessionSearch
    ? props.sessions.filter((session) => session.title.toLowerCase().includes(normalizedSessionSearch))
    : props.sessions;

  if (!props.open) {
    return <aside className="rail rail-collapsed"><Library size={16} /><FileText size={16} /></aside>;
  }
  return (
    <aside className="rail">
      <button className="primary-action" onClick={props.createSession}><MessageSquarePlus size={15} /> New session</button>
      <div className="search-box"><Search size={13} /><input placeholder="Search sessions" value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} /></div>
      <div className="seg">
        <button className={props.mode === "sessions" ? "on" : ""} onClick={() => props.setMode("sessions")}>Sessions</button>
        <button className={props.mode === "files" ? "on" : ""} onClick={() => props.setMode("files")}>Files {props.files.length}</button>
      </div>
      <div className="rail-list">
        {props.mode === "sessions" ? visibleSessions.map((session) => (
          <button key={session.id} className={`session-row ${session.id === props.activeSessionId ? "active" : ""}`} onClick={() => props.setActiveSessionId(session.id)}>
            <span>{session.title}</span>
            <small>{session.file_count} files</small>
          </button>
        )) : props.files.map((file) => <FileMini key={file.id} file={file} />)}
      </div>
    </aside>
  );
}

function FileMini({ file }: { file: FileRecord }) {
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

function EmptyState(props: {
  upload: (files: File[]) => void;
  busy: boolean;
  composer: string;
  setComposer: (value: string) => void;
  ask: (event?: FormEvent) => void;
  canAsk: boolean;
  onDetachFile: (fileId: string) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <section
      className="empty-state"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        props.upload(Array.from(event.dataTransfer.files));
      }}
    >
      <div className="empty-copy">
        <div className="mono caps accent">New session · no files yet</div>
        <h1>Attach your files.<br /><em>Ask anything grounded in them.</em></h1>
      </div>
      <button className="attach-plate" disabled={props.busy} onClick={() => inputRef.current?.click()}>
        <Paperclip size={18} />
        <span>{props.busy ? "Attaching" : "Attach files"}</span>
      </button>
      <input ref={inputRef} className="hidden" type="file" multiple accept={acceptedTypes} disabled={props.busy} onChange={(event) => handleFileInputChange(event, props.upload)} />
      <Composer
        value={props.composer}
        setValue={props.setComposer}
        ask={props.ask}
        disabled={!props.canAsk || props.busy}
        files={[]}
        busy={props.busy}
        onDetachFile={props.onDetachFile}
      />
    </section>
  );
}

function ProcessingView(props: {
  files: FileRecord[];
  upload: (files: File[]) => void;
  busy: boolean;
  activeLoading?: boolean;
  composer: string;
  setComposer: (value: string) => void;
  ask: (event?: FormEvent) => void;
  canAsk: boolean;
  onDetachFile: (fileId: string) => Promise<void>;
  onRetryFailedFiles: () => Promise<void>;
  openSettings: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const ready = props.files.filter((file) => file.status === "ready").length;
  const failed = props.files.filter((file) => file.status === "failed");
  const shouldShowFailureCallout = props.files.length > 0 && ready === 0 && failed.length > 0;
  const activeLoading = Boolean(props.activeLoading);
  return (
    <section className="processing-view" aria-busy={activeLoading || undefined} aria-label={activeLoading ? "Loading selected session" : undefined}>
      <div className="mono caps accent">{activeLoading ? "Loading session" : ready === props.files.length ? "Files ready" : "Processing files"}</div>
      <h2>{activeLoading ? "Loading files and messages..." : `${ready} of ${props.files.length} files ready`}</h2>
      {activeLoading && <p className="subtle">Hang tight while FileChat loads the selected session context.</p>}
      {!activeLoading && shouldShowFailureCallout && (
        <div className="file-failure-callout">
          <div>
            <strong>File indexing failed</strong>
            <p>{failed.some((file) => fileErrorSummary(file).includes("OpenRouter")) ? "OpenRouter rejected the API key while creating embeddings. Update the key, then retry indexing." : "Fix the file issue, then retry indexing."}</p>
          </div>
          <div className="callout-actions">
            <button className="secondary-action" type="button" onClick={props.openSettings}>Open Settings</button>
            <button className="primary-action" type="button" onClick={() => void props.onRetryFailedFiles()} disabled={props.busy}>Retry indexing</button>
          </div>
        </div>
      )}
      {!activeLoading && (
        <>
          <div className="file-table">
            {props.files.map((file) => <FileRow key={file.id} file={file} />)}
          </div>
          <button className="secondary-action" onClick={() => inputRef.current?.click()}><Paperclip size={15} /> Add files</button>
          <input ref={inputRef} className="hidden" type="file" multiple accept={acceptedTypes} onChange={(event) => handleFileInputChange(event, props.upload)} />
        </>
      )}
      <Composer value={props.composer} setValue={props.setComposer} ask={props.ask} disabled={!props.canAsk || props.busy || activeLoading} files={activeLoading ? [] : props.files} onDetachFile={props.onDetachFile} busy={props.busy || activeLoading} />
    </section>
  );
}

function FileRow({ file }: { file: FileRecord }) {
  return (
    <div className={`file-row ${file.status === "failed" ? "failed" : ""}`}>
      <div className="filemark">{file.type}</div>
      <div className="file-row-main">
        <strong>{file.name}</strong>
        {file.status !== "ready" && file.status !== "failed" && <div className="cap"><span style={{ transform: `scaleX(${file.progress})` }} /></div>}
      </div>
      <span className="mono">{formatBytes(file.size)}</span>
      <span title={file.error ?? undefined}>
        <StatusDot status={file.status} /> {statusLabel(file)}
        {!!file.indexing_total_cost && <small className="mono"> · {formatCost(file.indexing_total_cost)}</small>}
      </span>
    </div>
  );
}

function Transcript(props: {
  messages: Message[];
  runs: AgentRun[];
  files: FileRecord[];
  upload: (files: File[]) => void;
  usageSummary: UsageSummary;
  composer: string;
  setComposer: (value: string) => void;
  ask: (event?: FormEvent) => void;
  canAsk: boolean;
  busy: boolean;
  onCitationClick: (citation: Citation) => void;
  onArtifactSelect: (artifact: Artifact) => void;
  onDetachFile: (fileId: string) => Promise<void>;
  onAnswerRunQuestion: (runId: string, questionId: string, selectedOption: string | null, freeText?: string, attachedFileIds?: string[], answer?: Record<string, unknown>) => Promise<void>;
  contextProfile: ContextProfile;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const target = bottomRef.current;
    if (!target) return;
    if (typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "end", behavior });
      return;
    }
    const scroller = scrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, []);

  const onScroll = () => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    const distanceFromBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    followRef.current = distanceFromBottom < 96;
  };

  useLayoutEffect(() => {
    if (followRef.current) scrollToBottom("auto");
  }, [props.messages.length, props.busy, props.runs.length, scrollToBottom]);

  const activeRun = props.runs.find((run) => ["queued", "running", "awaiting_approval", "awaiting_user_input"].includes(run.status));
  const waitingRun = props.runs.find((run) => run.status === "awaiting_user_input" && run.current_question);

  return (
    <section className="transcript">
      <div ref={scrollRef} className="turns" onScroll={onScroll}>
        {props.messages.map((message) => {
          const messageRun = message.role === "assistant" ? props.runs.find((run) => run.assistant_message_id === message.id) : undefined;
          return (
            <article key={message.id} className={`turn ${message.role}`}>
              <div className="turn-label mono caps">{message.role === "user" ? "You" : "FileChat"}</div>
              <div className="bubble">
                {message.role === "assistant" && !message.content.trim() ? <NoAnswerNotice unavailableFileIds={message.unavailable_file_ids} files={props.files} grounding={message.grounding} /> : <RenderedMessage content={message.content} />}
                <MessageCost message={message} />
                {message.role === "assistant" && visibleArtifacts(message, props.contextProfile).length > 0 && (
                  <div className="artifact-list">
                    {visibleArtifacts(message, props.contextProfile).map((artifact) => (
                      <ArtifactRenderer
                        key={artifact.id}
                        artifact={artifact}
                        citations={message.citations}
                        onCitationClick={props.onCitationClick}
                        onSelectArtifact={props.onArtifactSelect}
                      />
                    ))}
                  </div>
                )}
                {messageRun && messageRun.follow_up_questions.length > 0 && (
                  <FollowUpQuestionCards
                    run={messageRun}
                    questions={messageRun.follow_up_questions}
                    files={props.files}
                    onAnswer={props.onAnswerRunQuestion}
                  />
                )}
                {messageRun && <AgentActivity run={messageRun} compact />}
                {message.role === "assistant" && message.citations.length > 0 && (
                  <GroundedSourceNotice citations={message.citations} />
                )}
                {message.role === "assistant" && message.citations.length > 0 && (
                  <SourcesDisclosure citations={message.citations} onCitationClick={props.onCitationClick} minimized={props.contextProfile.citation_display === "minimized"} />
                )}
                {message.role === "assistant" && message.citations.length > 0 && (message.unavailable_file_ids?.length ?? 0) > 0 && (
                  <UnavailableSourceNotice unavailableFileIds={message.unavailable_file_ids} files={props.files} />
                )}
                {message.role === "assistant" && message.content.trim() && message.citations.length === 0 && message.grounding?.status !== "not_applicable" && (message.grounding?.status === "no_citations" || props.files.length === 0 || props.files.some((file) => file.status === "ready" || file.status === "failed") || (message.unavailable_file_ids?.length ?? 0) > 0) && (
                  <NoCitationNotice unavailableFileIds={message.unavailable_file_ids} files={props.files} grounding={message.grounding} />
                )}
              </div>
            </article>
          );
        })}
        {props.busy && (
          <article className="turn assistant pending" aria-live="polite">
            <div className="turn-label mono caps">FileChat</div>
            <div className="bubble pending-bubble">
              <Loader2 className="spin" size={15} />
              <span>Reading the sources...</span>
            </div>
            {activeRun && <AgentActivity run={activeRun} compact />}
          </article>
        )}
        {waitingRun?.current_question && (
          <article className="turn assistant planning-question-turn" aria-live="polite">
            <div className="turn-label mono caps">FileChat</div>
            <div className="bubble">
              <PlanningQuestionCard run={waitingRun} question={waitingRun.current_question} onAnswer={props.onAnswerRunQuestion} />
            </div>
          </article>
        )}
        <div ref={bottomRef} className="scroll-sentinel" />
      </div>
      <div className="composer-dock">
        <SessionCostSummary usage={props.usageSummary} />
        <Composer value={props.composer} setValue={props.setComposer} ask={props.ask} disabled={!props.canAsk} files={props.files} upload={props.upload} busy={props.busy} onDetachFile={props.onDetachFile} />
      </div>
    </section>
  );
}

function visibleArtifacts(message: Message, profile: ContextProfile) {
  if (profile.artifact_policy === "all") return message.artifacts;
  return message.artifacts.filter((artifact) => (artifact.display_mode ?? "primary") === "primary");
}

function SourceContextDetails({ unavailableFileIds = [], files = [], includeUngroundedWarning = false }: { unavailableFileIds?: string[]; files?: FileRecord[]; includeUngroundedWarning?: boolean }) {
  const unavailableLabels = unavailableFileIds.map((fileId) => files.find((file) => file.id === fileId)?.name ?? fileId);
  const readyLabels = files.filter((file) => file.status === "ready").map((file) => file.name);
  const failedLabels = files
    .filter((file) => file.status === "failed")
    .map((file) => file.error ? `${file.name} (${fileErrorSummary(file)})` : file.name);
  const hasNoLocalSourceContext = files.length === 0 && unavailableLabels.length === 0;
  return (
    <>
      {includeUngroundedWarning && readyLabels.length > 0 && <small>Treat this answer as ungrounded until a cited snippet supports it.</small>}
      {includeUngroundedWarning && hasNoLocalSourceContext && <small>Attach local documents before relying on FileChat for grounded document answers.</small>}
      {readyLabels.length > 0 && <small>Available source context: {readyLabels.join(", ")}</small>}
      {failedLabels.length > 0 && <small>Failed source context: {failedLabels.join(", ")}</small>}
      {unavailableLabels.length > 0 && <small>Unavailable sources: {unavailableLabels.join(", ")}</small>}
    </>
  );
}

function NoCitationNotice({ unavailableFileIds = [], files = [], grounding }: { unavailableFileIds?: string[]; files?: FileRecord[]; grounding?: Message["grounding"] }) {
  const hasNoLocalSourceContext = files.length === 0 && unavailableFileIds.length === 0;
  return (
    <div className="source-strip no-citations" role="status" aria-live="polite" aria-label="No citations attached">
      <strong>{grounding?.notice?.trim() || (hasNoLocalSourceContext ? "No local sources were available for this answer." : "No citations attached to this answer.")}</strong>
      <small>{grounding?.detail?.trim() || "FileChat is being explicit that this response has no retrieved source snippets."}</small>
      <SourceContextDetails unavailableFileIds={unavailableFileIds} files={files} includeUngroundedWarning />
    </div>
  );
}

function UnavailableSourceNotice({ unavailableFileIds = [], files = [] }: { unavailableFileIds?: string[]; files?: FileRecord[] }) {
  const unavailableLabels = unavailableFileIds.map((fileId) => files.find((file) => file.id === fileId)?.name ?? fileId);
  if (unavailableLabels.length === 0) return null;
  return (
    <div className="source-strip no-citations" role="status" aria-live="polite" aria-label="Some sources unavailable">
      <strong>Some sources were unavailable for this answer.</strong>
      <small>Unavailable sources: {unavailableLabels.join(", ")}</small>
    </div>
  );
}

function NoAnswerNotice({ unavailableFileIds = [], files = [], grounding }: { unavailableFileIds?: string[]; files?: FileRecord[]; grounding?: Message["grounding"] }) {
  const notice = grounding?.notice?.trim() || "No answer was generated.";
  const detail = grounding?.detail?.trim() || "FileChat did not return answer text for this turn. Re-ask or check sources before relying on it.";
  return (
    <div className="source-strip no-citations no-answer" role="status" aria-live="polite" aria-label="No answer generated">
      <strong>{notice}</strong>
      <small>{detail}</small>
      <SourceContextDetails unavailableFileIds={unavailableFileIds} files={files} />
    </div>
  );
}

function sourceSummaryLabel(citations: Citation[]) {
  const labels = Array.from(new Set(citations.map(citationSourceLabel)));
  if (labels.length === 0) return `Sources · ${citations.length}`;
  const visibleLabels = labels.slice(0, 2).join(", ");
  const remaining = labels.length - 2;
  return `Sources · ${citations.length} · ${visibleLabels}${remaining > 0 ? ` +${remaining} more` : ""}`;
}

function citationSourceLabel(citation: Citation) {
  return citation.source_label.trim() || `Source ${citation.ordinal}`;
}

function citationLocationLabel(citation: Citation) {
  return citation.location.trim() || "local snippet";
}

function groundedSourceLabel(citations: Citation[]) {
  const labels = Array.from(new Set(citations.map(citationSourceLabel)));
  const sourceCount = labels.length || citations.length;
  const snippetWord = citations.length === 1 ? "snippet" : "snippets";
  const sourceWord = sourceCount === 1 ? "source" : "sources";
  const visibleLabels = labels.slice(0, 2).join(", ");
  const remaining = labels.length - 2;
  const sourceSuffix = visibleLabels ? `: ${visibleLabels}${remaining > 0 ? ` +${remaining} more` : ""}` : "";
  return `Grounded in ${citations.length} local source ${snippetWord}${sourceCount !== citations.length ? ` across ${sourceCount} ${sourceWord}` : ""}${sourceSuffix}`;
}

function GroundedSourceNotice({ citations }: { citations: Citation[] }) {
  return (
    <div className="source-strip grounded-source" role="status" aria-label="Grounded answer">
      <strong>{groundedSourceLabel(citations)}</strong>
      <small>Each cited snippet is from the attached local documents.</small>
    </div>
  );
}

function SourcesDisclosure({ citations, onCitationClick, minimized }: { citations: Citation[]; onCitationClick: (citation: Citation) => void; minimized: boolean }) {
  if (!minimized) {
    return (
      <div className="source-strip">
        {citations.map((citation) => (
          <CitationSourceButton key={citation.id} citation={citation} onCitationClick={onCitationClick} showExcerpt />
        ))}
      </div>
    );
  }
  return (
    <details className="source-strip compact">
      <summary>{sourceSummaryLabel(citations)}</summary>
      <div>
        {citations.map((citation) => (
          <CitationSourceButton key={citation.id} citation={citation} onCitationClick={onCitationClick} />
        ))}
      </div>
    </details>
  );
}

function CitationSourceButton({ citation, onCitationClick, showExcerpt = false }: { citation: Citation; onCitationClick: (citation: Citation) => void; showExcerpt?: boolean }) {
  return (
    <button className="source-citation-button" type="button" onClick={() => onCitationClick(citation)}>
      <span>{citation.ordinal}</span>
      <strong>{citationSourceLabel(citation)}</strong>
      <small>{citationLocationLabel(citation)}</small>
      {showExcerpt && citation.excerpt.trim() && <em>{citation.excerpt}</em>}
    </button>
  );
}

function MessageCost({ message }: { message: Message }) {
  if (!message.total_tokens && !message.total_cost) return null;
  const label = message.role === "user"
    ? `Input ${formatTokens(message.prompt_tokens)} · ${formatCost(message.prompt_cost)}`
    : `Output ${formatTokens(message.completion_tokens || message.total_tokens)} · ${formatCost(message.completion_cost || message.total_cost)}`;
  return <div className="message-cost mono">{label}</div>;
}

function RenderedMessage({ content }: { content: string }) {
  const blocks = parseMessageBlocks(content);
  return (
    <>
      {blocks.map((block, index) => {
        if (block.type === "code") {
          return <pre className="message-code" key={index}><code>{block.content}</code></pre>;
        }
        if (block.type === "list") {
          return <ul className="message-list" key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>;
        }
        return <p key={index}>{block.content}</p>;
      })}
    </>
  );
}

type MessageBlock =
  | { type: "paragraph"; content: string }
  | { type: "code"; content: string }
  | { type: "list"; items: string[] };

function parseMessageBlocks(content: string): MessageBlock[] {
  const lines = content.split("\n");
  const blocks: MessageBlock[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  let code: string[] | null = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ type: "paragraph", content: paragraph.join(" ") });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push({ type: "list", items: list });
      list = [];
    }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (code) {
        blocks.push({ type: "code", content: code.join("\n") });
        code = null;
      } else {
        flushParagraph();
        flushList();
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }
    const listMatch = line.match(/^\s*[-*•]\s+(.+)/);
    if (listMatch) {
      flushParagraph();
      list.push(listMatch[1]);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushList();
  if (code) blocks.push({ type: "code", content: code.join("\n") });
  return blocks.length ? blocks : [{ type: "paragraph", content }];
}

function SessionCostSummary({ usage }: { usage: UsageSummary }) {
  return (
    <div className="session-cost-strip" aria-label="Session token cost summary">
      <span><strong>{formatCost(usage.total_cost)}</strong> total</span>
      <span>input {formatCost(usage.chat_prompt_cost)}</span>
      <span>output {formatCost(usage.chat_completion_cost)}</span>
      <span>embeddings {formatCost(usage.embedding_cost)}</span>
      <span>{formatTokens(usage.total_tokens)}</span>
    </div>
  );
}

function Composer(props: {
  value: string;
  setValue: (value: string) => void;
  ask: (event?: FormEvent) => void;
  disabled: boolean;
  files: FileRecord[];
  upload?: (files: File[]) => void;
  busy?: boolean;
  onDetachFile?: (fileId: string) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const ready = props.files.filter((file) => file.status === "ready").length;
  const processing = props.files.filter((file) => ["queued", "reading", "indexing"].includes(file.status)).length;
  const failed = props.files.filter((file) => file.status === "failed").length;
  const helper = ready > 0
    ? [
        `${ready} ready source${ready === 1 ? "" : "s"}`,
        processing > 0 ? `${processing} processing` : null,
        failed > 0 ? `${failed} failed` : null,
        "Cmd/Ctrl+Enter to send",
      ].filter(Boolean).join(" · ")
    : failed > 0
      ? "No ready sources · fix failed files before sending"
      : props.files.length === 0
        ? "Attach a file to ask grounded questions · drafts stay local until a source is ready"
        : "No ready sources yet · you can draft while files process";
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Some older WebKit IME paths report keyCode 229 instead of reliably setting isComposing.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (!props.disabled && props.value.trim()) props.ask();
    }
  };
  return (
    <form className="composer" onSubmit={props.ask}>
      {props.files.length > 0 && (
        <div className="context-strip" aria-label="Files in chat context">
          {props.files.map((file) => (
            <div className={`context-chip ${file.status === "failed" ? "failed" : ""}`} key={file.id} title={file.error ? `${file.name}: ${file.error}` : file.name}>
              <span className="context-type mono">{file.type}</span>
              <span className="context-name">{file.name}</span>
              <span className={`context-status mono ${file.status}`}>{contextStatus(file)}</span>
              {props.onDetachFile && (
                <button type="button" aria-label={`Remove ${file.name} from context`} onClick={() => void props.onDetachFile?.(file.id)}>
                  <X size={12} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <textarea
        aria-label="Ask a question about the selected files"
        value={props.value}
        onChange={(event) => props.setValue(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={ready > 0 ? "Ask a question about these files" : "You can draft a prompt while waiting for a ready file"}
      />
      <div className="composer-bar">
        <span className="mono">{helper}</span>
        {props.upload && (
          <>
            <button className="icon-btn" type="button" aria-label="Attach files to chat" disabled={props.busy} onClick={() => inputRef.current?.click()}>
              <Paperclip size={15} />
            </button>
            <input ref={inputRef} className="hidden" type="file" aria-label="Attach files to chat" multiple accept={acceptedTypes} disabled={props.busy} onChange={(event) => handleFileInputChange(event, props.upload!)} />
          </>
        )}
        <button className="send-btn" disabled={props.disabled || !props.value.trim()} type="submit">
          {props.busy ? <Loader2 className="spin" size={15} /> : <Send size={15} />} Ask
        </button>
      </div>
    </form>
  );
}

function RightPanel(props: {
  open: boolean;
  setOpen: (open: boolean) => void;
  tab: RightTab;
  setTab: (tab: RightTab) => void;
  files: FileRecord[];
  citations: Citation[];
  artifacts: Artifact[];
  runs: AgentRun[];
  selectedArtifactId: string | null;
  selectArtifactId: (artifactId: string) => void;
  usageSummary: UsageSummary;
  settings: Settings | null;
  contextProfile: ContextProfile;
  updateSettings: (patch: Record<string, unknown>) => Promise<void>;
  updateAdminSettings: (patch: Record<string, unknown>) => Promise<void>;
  updateContextProfile: (patch: Partial<ContextProfile>) => Promise<void>;
  clearOpenRouterKey: () => Promise<void>;
  approveRun: (runId: string) => Promise<void>;
  retryRun: (runId: string, mode?: "repair" | "rerun") => Promise<void>;
  answerRunQuestion: (runId: string, questionId: string, selectedOption: string | null, freeText?: string, attachedFileIds?: string[], answer?: Record<string, unknown>) => Promise<void>;
  highlightCitationId: string | null;
  activeSession: Session | null;
  currentUser: CurrentUser | null;
}) {
  if (!props.open) {
    return (
      <aside className="right-closed">
        <button
          aria-expanded="false"
          onClick={() => {
            props.setTab("citations");
            props.setOpen(true);
          }}
        >
          Sources
        </button>
      </aside>
    );
  }
  return (
    <aside className="right-panel">
      <div className="tabs">
        {(["files", "citations", "artifacts", "runs", "settings"] as const).map((tab) => (
          <button key={tab} className={props.tab === tab ? "on" : ""} onClick={() => props.setTab(tab)}>{tab}</button>
        ))}
        {props.currentUser?.capabilities.use_admin_console && (
          <button className={props.tab === "admin" ? "on" : ""} onClick={() => props.setTab("admin")}>admin</button>
        )}
        <button className="icon-btn" onClick={() => props.setOpen(false)} aria-label="Close right panel"><X size={13} /></button>
      </div>
      {props.tab === "files" && <FilesTab files={props.files} activeSession={props.activeSession} usageSummary={props.usageSummary} />}
      {props.tab === "citations" && <CitationsTab citations={props.citations} highlightCitationId={props.highlightCitationId} />}
      {props.tab === "artifacts" && <ArtifactsTab artifacts={props.artifacts} selectedArtifactId={props.selectedArtifactId} selectArtifactId={props.selectArtifactId} citations={props.citations} />}
      {props.tab === "runs" && <RunsTab runs={props.runs} approveRun={props.approveRun} retryRun={props.retryRun} answerRunQuestion={props.answerRunQuestion} />}
      {props.tab === "settings" && (
        <SettingsTab
          settings={props.settings}
          contextProfile={props.contextProfile}
          updateSettings={props.updateSettings}
          updateContextProfile={props.updateContextProfile}
          clearOpenRouterKey={props.clearOpenRouterKey}
          canManageProviderKeys={!props.currentUser?.enterprise_enabled}
          heading="Settings"
          lockedReason={props.currentUser?.enterprise_enabled ? "Provider keys and models are managed in the Enterprise admin console." : undefined}
        />
      )}
      {props.tab === "admin" && props.currentUser?.capabilities.use_admin_console && (
        <SettingsTab
          settings={props.settings}
          contextProfile={props.contextProfile}
          updateSettings={props.updateAdminSettings}
          updateContextProfile={props.updateContextProfile}
          clearOpenRouterKey={props.clearOpenRouterKey}
          canManageProviderKeys={!!props.currentUser.capabilities.manage_provider_keys}
          heading="Admin console"
        />
      )}
    </aside>
  );
}

function FilesTab({ files, activeSession, usageSummary }: { files: FileRecord[]; activeSession: Session | null; usageSummary: UsageSummary }) {
  return (
    <div className="panel-body">
      <div className="panel-kicker mono caps">{activeSession?.title ?? "Session index"}</div>
      <div className="stats">
        <Stat label="Files" value={String(files.length)} />
        <Stat label="Ready" value={String(files.filter((file) => file.status === "ready").length)} />
        <Stat label="Chunks" value={String(files.reduce((sum, file) => sum + file.chunk_count, 0))} />
      </div>
      <div className="usage-card">
        <strong>{formatCost(usageSummary.total_cost)}</strong>
        <span className="mono caps">Session total</span>
        <small>Chat input {formatCost(usageSummary.chat_prompt_cost)} · output {formatCost(usageSummary.chat_completion_cost)} · embeddings {formatCost(usageSummary.embedding_cost)}</small>
      </div>
      {files.map((file) => <FileMini key={file.id} file={file} />)}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div><strong>{value}</strong><span className="mono caps">{label}</span></div>;
}

function CitationsTab({ citations, highlightCitationId }: { citations: Citation[]; highlightCitationId: string | null }) {
  if (citations.length === 0) {
    return (
      <div className="panel-empty">
        <strong>No citations yet.</strong>
        <p>Cited source snippets will appear here after grounded answers; if no local source supports an answer, FileChat will say so.</p>
      </div>
    );
  }
  return (
    <div className="panel-body">
      <div className="panel-kicker mono caps">Sources · this session</div>
      {citations.map((citation) => (
        <article id={`citation-${citation.id}`} key={citation.id} className={`citation-card ${citation.id === highlightCitationId ? "highlight" : ""}`}>
          <div><span>{citation.ordinal}</span><strong>{citationSourceLabel(citation)}</strong></div>
          <small className="mono">{citationLocationLabel(citation)} · score {citation.score.toFixed(2)}</small>
          <p>{citation.excerpt}</p>
        </article>
      ))}
    </div>
  );
}

function ArtifactsTab({
  artifacts,
  selectedArtifactId,
  selectArtifactId,
  citations
}: {
  artifacts: Artifact[];
  selectedArtifactId: string | null;
  selectArtifactId: (artifactId: string) => void;
  citations: Citation[];
}) {
  if (artifacts.length === 0) {
    return <div className="panel-empty">No artifacts yet.</div>;
  }
  const selected = artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? artifacts.find((artifact) => artifact.display_mode === "primary") ?? artifacts[0];
  const sourceCitations = selected.source_chunk_ids
    .map((chunkId) => citations.find((citation) => citation.chunk_id === chunkId))
    .filter((citation): citation is Citation => Boolean(citation));
  return (
    <div className="panel-body">
      <div className="panel-kicker mono caps">Artifacts · this session</div>
      <div className="artifact-picker" aria-label="Artifact list">
        {artifacts.map((artifact) => (
          <button key={artifact.id} className={artifact.id === selected.id ? "on" : ""} type="button" onClick={() => selectArtifactId(artifact.id)}>
            <span className="mono caps">{artifact.kind.replace("_", " ")}</span>
            <strong>{artifact.title}</strong>
            <small>{artifact.display_mode === "supporting" ? "Supporting" : "Primary"}</small>
          </button>
        ))}
      </div>
      <article className="artifact-detail-card">
        <span className="mono caps">{selected.kind.replace("_", " ")}</span>
        <h3>{selected.title}</h3>
        {selected.caption && <p>{selected.caption}</p>}
        <small className="mono">{selected.source_chunk_ids.length} source chunk{selected.source_chunk_ids.length === 1 ? "" : "s"}</small>
        {selected.kind === "file_draft" && (
          <div className="artifact-export-row">
            <a className="artifact-inline-action" href={api.exportArtifactUrl(selected.session_id, selected.id, "md")}>Markdown</a>
            <a className="artifact-inline-action" href={api.exportArtifactUrl(selected.session_id, selected.id, "json")}>JSON</a>
            <a className="artifact-inline-action" href={api.exportArtifactUrl(selected.session_id, selected.id, "notion")}>Notion</a>
          </div>
        )}
      </article>
      {sourceCitations.map((citation) => (
        <article id={`artifact-source-${citation.id}`} key={citation.id} className="citation-card">
          <div><span>{citation.ordinal}</span><strong>{citationSourceLabel(citation)}</strong></div>
          <small className="mono">{citationLocationLabel(citation)}</small>
          <p>{citation.excerpt}</p>
        </article>
      ))}
    </div>
  );
}

function RunsTab({
  runs,
  approveRun,
  retryRun,
  answerRunQuestion
}: {
  runs: AgentRun[];
  approveRun: (runId: string) => Promise<void>;
  retryRun: (runId: string, mode?: "repair" | "rerun") => Promise<void>;
  answerRunQuestion: (runId: string, questionId: string, selectedOption: string | null, freeText?: string, attachedFileIds?: string[], answer?: Record<string, unknown>) => Promise<void>;
}) {
  if (runs.length === 0) {
    return <div className="panel-empty">No agent runs yet.</div>;
  }
  return (
    <div className="panel-body">
      <div className="panel-kicker mono caps">Agent activity</div>
      {runs.map((run) => (
        <article className={`run-card ${run.status}`} key={run.id}>
          <div className="run-card-header">
            <div>
              <div className="run-card-meta">
                <span className="mono caps">{run.kind}</span>
                <span className="mono caps">{formatRunStatus(run.status)}</span>
              </div>
              <strong>{run.question}</strong>
            </div>
          </div>
          {run.status === "awaiting_user_input" && run.current_question && (
            <PlanningQuestionCard run={run} question={run.current_question} onAnswer={answerRunQuestion} compact />
          )}
          <RunSetupDetails run={run} />
          <AgentActivity run={run} />
          <div className="run-actions">
            {run.status === "awaiting_approval" && <button className="artifact-inline-action" type="button" onClick={() => void approveRun(run.id)}>Approve plan</button>}
            {run.status !== "queued" && run.status !== "running" && <button className="artifact-inline-action" type="button" onClick={() => void retryRun(run.id, "rerun")}>Retry run</button>}
            {run.repair_attempts.length > 0 && <button className="artifact-inline-action" type="button" onClick={() => void retryRun(run.id, "repair")}>Retry artifact</button>}
          </div>
        </article>
      ))}
    </div>
  );
}

function formatRunStatus(status: AgentRun["status"]) {
  return status.replaceAll("_", " ");
}

function recordLike(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function FollowUpQuestionCards({
  run,
  questions,
  files,
  onAnswer
}: {
  run: AgentRun;
  questions: AgentRunQuestion[];
  files: FileRecord[];
  onAnswer: (runId: string, questionId: string, selectedOption: string | null, freeText?: string, attachedFileIds?: string[], answer?: Record<string, unknown>) => Promise<void>;
}) {
  const readyFiles = files.filter((item) => item.status === "ready");
  return (
    <div className="follow-up-question-list" aria-label="Follow-up questions">
      {questions.map((question) => (
        <FollowUpQuestionCard key={question.id} run={run} question={question} readyFiles={readyFiles} onAnswer={onAnswer} />
      ))}
    </div>
  );
}

function FollowUpQuestionCard({
  run,
  question,
  readyFiles,
  onAnswer
}: {
  run: AgentRun;
  question: AgentRunQuestion;
  readyFiles: FileRecord[];
  onAnswer: (runId: string, questionId: string, selectedOption: string | null, freeText?: string, attachedFileIds?: string[], answer?: Record<string, unknown>) => Promise<void>;
}) {
  const card = question.card;
  const defaultOption = question.default_option || question.options[0]?.id || "";
  const multiSelect = Boolean(card.allow_multi_select);
  const [selectedOption, setSelectedOption] = useState(defaultOption);
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [freeText, setFreeText] = useState("");
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const prompt = card.prompt || question.question;
  const referenceReady = !card.allow_file_reference || selectedFileIds.length > 0;
  const canSubmit = referenceReady && (
    multiSelect
      ? selectedOptions.length > 0
      : Boolean(selectedOption || freeText.trim() || selectedFileIds.length > 0)
  );
  const toggleFile = (fileId: string) => {
    setSelectedFileIds((items) => items.includes(fileId) ? items.filter((item) => item !== fileId) : [...items, fileId]);
  };
  const toggleOption = (optionId: string) => {
    setSelectedOptions((items) => items.includes(optionId) ? items.filter((item) => item !== optionId) : [...items, optionId]);
  };
  const submitLabel = card.submit_label || "Start follow-up";

  return (
    <div className="follow-up-question-card">
      <div className="follow-up-question-head">
        <span className="mono caps">{card.group || question.phase}</span>
        <strong>{card.title || "Question to answer next"}</strong>
      </div>
      <p>{prompt}</p>
      {question.options.length > 0 && multiSelect && (
        <div className="follow-up-option-checks">
          {question.options.map((option) => (
            <label key={option.id}>
              <input
                type="checkbox"
                checked={selectedOptions.includes(option.id)}
                onChange={() => toggleOption(option.id)}
              />
              <span>{option.label}</span>
              {option.description && <small>{option.description}</small>}
            </label>
          ))}
        </div>
      )}
      {question.options.length > 0 && !multiSelect && (
        <div className="follow-up-option-row">
          {question.options.map((option) => (
            <button
              key={option.id}
              type="button"
              aria-pressed={selectedOption === option.id}
              className={selectedOption === option.id ? "selected" : ""}
              onClick={() => setSelectedOption(option.id)}
            >
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      )}
      {card.allow_free_text && (
        <textarea
          aria-label="Follow-up note"
          value={freeText}
          onChange={(event) => setFreeText(event.target.value)}
          placeholder="Add context"
        />
      )}
      {card.allow_file_reference && (
        <div className="follow-up-file-picker">
          <div className="follow-up-file-head">
            <Paperclip size={14} />
            <span>Attach reference</span>
          </div>
          {readyFiles.length > 0 ? (
            <div className="follow-up-file-options">
              {readyFiles.map((fileItem) => (
                <label key={fileItem.id}>
                  <input
                    type="checkbox"
                    checked={selectedFileIds.includes(fileItem.id)}
                    onChange={() => toggleFile(fileItem.id)}
                  />
                  <span>{fileItem.name}</span>
                  <small className="mono">{fileItem.type}</small>
                </label>
              ))}
            </div>
          ) : (
            <small className="follow-up-empty">No ready reference files.</small>
          )}
        </div>
      )}
      <button
        type="button"
        className="follow-up-submit"
        disabled={!canSubmit}
        onClick={() => void onAnswer(
          run.id,
          question.id,
          multiSelect ? null : selectedOption,
          freeText,
          selectedFileIds,
          multiSelect ? { selected_options: selectedOptions } : {}
        )}
      >
        <Send size={14} />
        <span>{submitLabel}</span>
      </button>
    </div>
  );
}

function PlanningQuestionCard({
  run,
  question,
  onAnswer,
  compact = false
}: {
  run: AgentRun;
  question: AgentRunQuestion;
  onAnswer: (runId: string, questionId: string, selectedOption: string | null, freeText?: string, attachedFileIds?: string[], answer?: Record<string, unknown>) => Promise<void>;
  compact?: boolean;
}) {
  const [freeText, setFreeText] = useState("");
  return (
    <div className={`planning-question-card ${compact ? "compact" : ""}`} aria-label="Planning question">
      <div className="planning-question-head">
        <span className="mono caps">Planning needs a choice</span>
        <strong>{question.kind === "interview_offer" ? "Interview or automatic?" : question.kind === "artifact_choice" ? "Choose an artifact" : "One more planning question"}</strong>
      </div>
      <p>{question.question}</p>
      {question.kind === "clarification" && (
        <textarea
          aria-label="Optional planning note"
          value={freeText}
          onChange={(event) => setFreeText(event.target.value)}
          placeholder="Optional note for FileChat"
        />
      )}
      <div className="planning-options">
        {question.options.map((option) => (
          <button key={option.id} type="button" onClick={() => void onAnswer(run.id, question.id, option.id, freeText)}>
            <span>{option.label}</span>
            {option.description && <small>{option.description}</small>}
          </button>
        ))}
      </div>
    </div>
  );
}

function RunSetupDetails({ run }: { run: AgentRun }) {
  const plannerContract = recordLike(run.task_contract?.planner_contract);
  const executableContract = recordLike(run.task_contract?.executable_contract);
  const contractAdjustments = Array.isArray(run.task_contract?.contract_adjustments) ? run.task_contract.contract_adjustments : [];
  const planEntries = Object.entries(run.execution_plan ?? {}).filter(([, value]) => {
    if (Array.isArray(value)) return value.length > 0;
    return value !== undefined && value !== null && value !== "";
  });
  const hasDetails = planEntries.length > 0
    || Object.keys(run.task_contract ?? {}).length > 0
    || Object.keys(run.provider_status ?? {}).length > 0
    || Object.keys(run.review_scores ?? {}).length > 0
    || Object.keys(run.model_assignments ?? {}).length > 0
    || run.agent_actions.length > 0
    || run.tool_calls.length > 0
    || run.repair_attempts.length > 0
    || run.quality_warnings.length > 0;
  if (!hasDetails) return null;
  return (
    <details className="run-setup-details">
      <summary>Agent setup and technical details</summary>
      {Object.keys(plannerContract).length > 0 && (
        <div className="run-contract-block">
          <strong>Planner intent</strong>
          <pre>{JSON.stringify(plannerContract, null, 2)}</pre>
        </div>
      )}
      {Object.keys(executableContract).length > 0 && (
        <div className="run-contract-block">
          <strong>Executable bundle</strong>
          <pre>{JSON.stringify(executableContract, null, 2)}</pre>
        </div>
      )}
      {contractAdjustments.length > 0 && (
        <div className="run-contract-block">
          <strong>Contract adjustments</strong>
          <pre>{JSON.stringify(contractAdjustments, null, 2)}</pre>
        </div>
      )}
      <pre>{JSON.stringify({
        execution_plan: run.execution_plan,
        task_contract: run.task_contract,
        provider_status: run.provider_status,
        agent_actions: run.agent_actions,
        review_scores: run.review_scores,
        revision_required: run.revision_required,
        model_assignments: run.model_assignments,
        tool_calls: run.tool_calls,
        artifact_versions: run.artifact_versions,
        repair_attempts: run.repair_attempts,
        quality_warnings: run.quality_warnings,
      }, null, 2)}</pre>
    </details>
  );
}

const actionGroups: Record<string, string> = {
  verify_provider: "setup",
  classify_request: "setup",
  plan_task: "setup",
  ask_user: "setup",
  load_sources: "source work",
  rank_sources: "source work",
  profile_table: "source work",
  build_evidence: "reasoning",
  reason: "reasoning",
  write: "artifact work",
  repair: "artifact work",
  validate: "validation",
  persist_response: "publishing",
  publish_notion: "publishing",
};

function AgentActivity({ run, compact = false }: { run: AgentRun; compact?: boolean }) {
  const grouped = run.actions.reduce<Array<{ group: string; actions: AgentRunAction[] }>>((items, action) => {
    const group = actionGroups[action.kind] ?? "activity";
    const current = items[items.length - 1];
    if (!current || current.group !== group) items.push({ group, actions: [action] });
    else current.actions.push(action);
    return items;
  }, []);
  return (
    <div className={`agent-activity ${compact ? "compact" : ""}`} aria-label="Agent activity">
      {grouped.map((group) => (
        <div className="agent-activity-group" key={`${group.group}-${group.actions[0]?.id}`}>
          {!compact && <div className="agent-activity-group-label mono caps">{group.group}</div>}
          {group.actions.map((action) => (
            <AgentActionRow key={action.id} action={action} compact={compact} />
          ))}
        </div>
      ))}
      {run.error && <div className="activity-error">{providerErrorSummary(run.error)}</div>}
    </div>
  );
}

function AgentActionRow({ action, compact }: { action: AgentRunAction; compact: boolean }) {
  const details = {
    input: action.input_json,
    output: action.output_json,
    validation: action.validation_json,
  };
  const detailEntries = Object.entries(details).filter(([, value]) => {
    if (!value || typeof value !== "object") return false;
    if (Array.isArray(value)) return value.length > 0;
    return Object.keys(value as Record<string, unknown>).length > 0;
  });
  const output = action.output_json ?? {};
  const vectorStatus = typeof output.vector_search_status === "string" ? output.vector_search_status : "";
  const vectorError = typeof output.vector_search_error === "string" ? output.vector_search_error : "";
  const degradedVector = vectorStatus.startsWith("unavailable");
  const summary = action.output_summary || action.input_summary;
  return (
    <div className={`agent-action ${action.status}`}>
      <div className="activity-dot" />
      <div className="activity-main">
        <div className="activity-line">
          <span className="mono caps">{action.kind.replace(/_/g, " ")}</span>
          <em>{action.status}</em>
        </div>
        {!compact && <strong>{action.label}</strong>}
        {!compact && summary && <p>{summary}</p>}
        {!compact && degradedVector && <p className="activity-warning">{providerErrorSummary(vectorError || vectorStatus)}</p>}
        {!compact && action.error_summary && <p className="activity-error">{providerErrorSummary(action.error_summary)}</p>}
        {!compact && detailEntries.length > 0 && (
          <details>
            <summary>Details</summary>
            <pre>{JSON.stringify(details, null, 2)}</pre>
          </details>
        )}
      </div>
    </div>
  );
}
function SettingsTab({
  settings,
  contextProfile,
  updateSettings,
  updateContextProfile,
  clearOpenRouterKey,
  canManageProviderKeys,
  heading,
  lockedReason
}: {
  settings: Settings | null;
  contextProfile: ContextProfile;
  updateSettings: (patch: Record<string, unknown>) => Promise<void>;
  updateContextProfile: (patch: Partial<ContextProfile>) => Promise<void>;
  clearOpenRouterKey: () => Promise<void>;
  canManageProviderKeys: boolean;
  heading: string;
  lockedReason?: string;
}) {
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [chatModels, setChatModels] = useState<ModelInfo[]>([]);
  const [embeddingModels, setEmbeddingModels] = useState<ModelInfo[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [clearingKey, setClearingKey] = useState(false);
  const chooserRef = useRef<HTMLDivElement>(null);

  const loadModels = useCallback(async (force = false) => {
    if (!canManageProviderKeys) return;
    if (!force && !settings?.openrouter_key_configured) return;
    setLoadingModels(true);
    setModelError(null);
    try {
      const [nextChatModels, nextEmbeddingModels] = await Promise.all([api.models("chat"), api.models("embedding")]);
      setChatModels(nextChatModels);
      setEmbeddingModels(nextEmbeddingModels);
    } catch (err) {
      setModelError(err instanceof Error ? err.message : "Could not load OpenRouter models");
    } finally {
      setLoadingModels(false);
    }
  }, [canManageProviderKeys, settings?.openrouter_key_configured]);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  const saveKey = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    try {
      await updateSettings({ openrouter_api_key: apiKey.trim() });
      await api.verifyOpenRouter();
      await updateSettings({});
      setApiKey("");
      await loadModels(true);
      window.setTimeout(() => {
        if (typeof chooserRef.current?.scrollIntoView === "function") {
          chooserRef.current.scrollIntoView({ block: "start", behavior: "smooth" });
        }
      }, 50);
    } finally {
      setSaving(false);
    }
  };
  const verifyKey = async () => {
    setVerifying(true);
    setModelError(null);
    try {
      await api.verifyOpenRouter();
      await updateSettings({});
      await loadModels(true);
    } catch (err) {
      setModelError(err instanceof Error ? err.message : "OpenRouter verification failed");
    } finally {
      setVerifying(false);
    }
  };
  const clearKey = async () => {
    setClearingKey(true);
    setModelError(null);
    try {
      await clearOpenRouterKey();
      setApiKey("");
    } catch (err) {
      setModelError(err instanceof Error ? err.message : "Could not clear OpenRouter key");
    } finally {
      setClearingKey(false);
    }
  };
  const providerStatus = settings?.openrouter_provider_status ?? "missing";
  const providerReady = providerStatus === "verified";
  const canClearSavedKey = Boolean(settings?.openrouter_key_configured && settings.openrouter_key_source === "local");
  return (
    <div className="panel-body settings-panel">
      <div className="panel-kicker mono caps">{heading}</div>
      <div className="settings-status">
        <KeyRound size={16} />
        <div>
          <strong>{providerReady ? "OpenRouter verified" : settings?.openrouter_key_configured ? `OpenRouter ${providerStatus}` : "OpenRouter key missing"}</strong>
          <small>{lockedReason ?? (providerReady ? "Model-backed runs can start." : settings?.openrouter_key_configured ? "Verify the key before running model-backed workflows." : "Add a key before indexing or asking questions.")}</small>
          <small>Source: {settings?.openrouter_key_source ?? "loading"}</small>
          <small>Scope: {settings?.settings_scope ?? "single_user"}</small>
          {settings?.openrouter_provider_message && <small>{settings.openrouter_provider_message}</small>}
        </div>
      </div>
      <div className="enterprise-config-note">
        <ShieldCheck size={16} />
        <div>
          <strong>{settings?.edition === "enterprise" ? "Enterprise configuration active" : "Community configuration active"}</strong>
          <small>
            {settings?.edition === "enterprise"
              ? "Enterprise role gates are enabled from environment configuration."
              : "Enable local enterprise testing with FILECHAT_EDITION=enterprise and FILECHAT_AUTH_TEST_MODE=true."}
          </small>
          <small>Use FILECHAT_TRUSTED_AUTH_HEADERS=true only behind a trusted auth adapter that strips inbound role headers.</small>
        </div>
      </div>
      {canManageProviderKeys ? (
        <>
          <label>API key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="sk-or-..." /></label>
          <button className="primary-action" onClick={saveKey} disabled={saving || !apiKey.trim()}><KeyRound size={15} /> Save key</button>
          {settings?.openrouter_key_configured && <button className="secondary-action" onClick={verifyKey} disabled={verifying}>{verifying ? <Loader2 size={15} className="spin" /> : <KeyRound size={15} />} Verify key</button>}
          {canClearSavedKey && <button className="secondary-action" onClick={clearKey} disabled={clearingKey}>{clearingKey ? <Loader2 size={15} className="spin" /> : <X size={15} />} Clear saved key</button>}
          {settings?.openrouter_key_source === "env" && <small>Environment keys are managed outside FileChat.</small>}
          <div ref={chooserRef} className="model-chooser">
            <div className="settings-status"><SettingsIcon size={16} /><div><strong>OpenRouter models</strong><small>{loadingModels ? "Loading live model metadata..." : "Choose model profiles for orchestration, analysis, writing, repair, and embeddings."}</small>{modelError && <small className="settings-error">{modelError}</small>}</div></div>
            <label>Routing mode
              <select value={settings?.model_routing_mode ?? "auto"} onChange={(event) => void updateSettings({ model_routing_mode: event.target.value })}>
                <option value="auto">Auto</option>
                <option value="balanced">Balanced</option>
                <option value="deep">Deep</option>
                <option value="manual">Manual</option>
              </select>
            </label>
            <label>Reasoning effort
              <select value={settings?.reasoning_effort ?? "medium"} onChange={(event) => void updateSettings({ reasoning_effort: event.target.value })}>
                <option value="none">None</option>
                <option value="minimal">Minimal</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="xhigh">X-high</option>
              </select>
            </label>
            <ModelSelector kind="chat" label="Chat model" value={settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(chat_model) => updateSettings({ chat_model })} />
            <ModelSelector kind="chat" label="Orchestrator model" value={settings?.orchestrator_model ?? settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(orchestrator_model) => updateSettings({ orchestrator_model })} />
            <ModelSelector kind="chat" label="Analysis model" value={settings?.analysis_model ?? settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(analysis_model) => updateSettings({ analysis_model })} />
            <ModelSelector kind="chat" label="Writing model" value={settings?.writing_model ?? settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(writing_model) => updateSettings({ writing_model })} />
            <ModelSelector kind="chat" label="Repair model" value={settings?.repair_model ?? settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(repair_model) => updateSettings({ repair_model })} />
            <ModelSelector kind="embedding" label="Embedding model" value={settings?.embedding_model ?? ""} models={embeddingModels} loading={loadingModels} onSelect={(embedding_model) => updateSettings({ embedding_model })} />
          </div>
        </>
      ) : (
        <div className="settings-status locked">
          <ShieldCheck size={16} />
          <div>
            <strong>Managed by admins</strong>
            <small>{settings?.chat_model ?? "No chat model selected"}</small>
            <small>{settings?.embedding_model ?? "No embedding model selected"}</small>
          </div>
        </div>
      )}
      <section className="preferences-panel">
        <div className="settings-status">
          <SettingsIcon size={16} />
          <div>
            <strong>Preferences</strong>
            <small>These defaults shape prompt context, artifact display, citations, and draft style.</small>
          </div>
        </div>
        <label>Artifacts in chat
          <select value={contextProfile.artifact_policy} onChange={(event) => void updateContextProfile({ artifact_policy: event.target.value as ContextProfile["artifact_policy"] })}>
            <option value="chart+draft">Chart + draft</option>
            <option value="all">All artifacts</option>
            <option value="ask_each_run">Ask each run</option>
          </select>
        </label>
        <label>Citations
          <select value={contextProfile.citation_display} onChange={(event) => void updateContextProfile({ citation_display: event.target.value as ContextProfile["citation_display"] })}>
            <option value="minimized">Minimized</option>
            <option value="full">Full</option>
          </select>
        </label>
        <label>Drafting
          <select value={contextProfile.drafting_policy} onChange={(event) => void updateContextProfile({ drafting_policy: event.target.value as ContextProfile["drafting_policy"] })}>
            <option value="model_polished_evidence">Model-polished evidence</option>
            <option value="deterministic_template">Deterministic template</option>
            <option value="ask_user_style">Ask user style</option>
          </select>
        </label>
        <label>Titles
          <select value={contextProfile.title_style} onChange={(event) => void updateContextProfile({ title_style: event.target.value as ContextProfile["title_style"] })}>
            <option value="localized_subject_first">Localized subject-first</option>
            <option value="generic">Generic</option>
          </select>
        </label>
      </section>
      {canManageProviderKeys && (
        <>
          <label>OCR model<input defaultValue={settings?.ocr_model ?? ""} onBlur={(event) => updateSettings({ ocr_model: event.target.value })} /></label>
          <label>Retrieval depth<input type="number" min={1} max={24} defaultValue={settings?.retrieval_depth ?? 8} onBlur={(event) => updateSettings({ retrieval_depth: Number(event.target.value) })} /></label>
          <label className="model-check">
            <input type="checkbox" checked={Boolean(settings?.high_cost_confirmation)} onChange={(event) => void updateSettings({ high_cost_confirmation: event.target.checked })} />
            Confirm high-cost or deep runs
          </label>
          <label className="model-check">
            <input type="checkbox" checked={Boolean(settings?.web_search_enabled)} onChange={(event) => void updateSettings({ web_search_enabled: event.target.checked })} />
            Optional web search
          </label>
          <label>Search engine
            <select value={settings?.web_search_engine ?? "auto"} onChange={(event) => void updateSettings({ web_search_engine: event.target.value })}>
              <option value="auto">Auto</option>
              <option value="native">Native</option>
              <option value="exa">Exa</option>
              <option value="parallel">Parallel</option>
              <option value="firecrawl">Firecrawl</option>
            </select>
          </label>
        </>
      )}
      <div className="settings-status"><SettingsIcon size={16} /><div><strong>Strict grounding</strong><small>Answers refuse when the sources do not support them.</small></div></div>
    </div>
  );
}

function ModelSelector(props: {
  kind: "chat" | "embedding";
  label: string;
  value: string;
  models: ModelInfo[];
  loading: boolean;
  onSelect: (modelId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [priceFilter, setPriceFilter] = useState<"all" | "free" | "paid">("all");
  const [minContext, setMinContext] = useState("");
  const [structuredOnly, setStructuredOnly] = useState(false);
  const [reasoningOnly, setReasoningOnly] = useState(false);
  const [sort, setSort] = useState<"name" | "newest" | "context" | "input" | "output">("name");

  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const minContextValue = Number(minContext) || 0;
    return props.models
      .filter((model) => {
        const haystack = `${model.id} ${model.name}`.toLowerCase();
        const isFree = (model.pricing.prompt ?? 0) === 0 && (model.pricing.completion ?? 0) === 0;
        const supportsStructuredOutput = model.supported_parameters.includes("response_format") || model.supported_parameters.includes("structured_outputs");
        const supportsReasoning = model.supported_parameters.includes("reasoning") || model.supported_parameters.includes("include_reasoning");
        if (normalizedQuery && !haystack.includes(normalizedQuery)) return false;
        if (priceFilter === "free" && !isFree) return false;
        if (priceFilter === "paid" && isFree) return false;
        if (minContextValue && (model.context_length ?? 0) < minContextValue) return false;
        if (structuredOnly && !supportsStructuredOutput) return false;
        if (reasoningOnly && !supportsReasoning) return false;
        return true;
      })
      .sort((a, b) => {
        if (sort === "newest") return (b.created ?? 0) - (a.created ?? 0);
        if (sort === "context") return (b.context_length ?? 0) - (a.context_length ?? 0);
        if (sort === "input") return (a.pricing.prompt ?? 0) - (b.pricing.prompt ?? 0);
        if (sort === "output") return (a.pricing.completion ?? 0) - (b.pricing.completion ?? 0);
        return a.name.localeCompare(b.name);
      });
  }, [minContext, priceFilter, props.models, query, reasoningOnly, sort, structuredOnly]);

  const selected = props.models.find((model) => model.id === props.value);

  return (
    <section className="model-selector">
      <label>{props.label}
        <select value={props.value} disabled={props.loading || filtered.length === 0} onChange={(event) => void props.onSelect(event.target.value)}>
          {props.value && !filtered.some((model) => model.id === props.value) && <option value={props.value}>{selected?.name ?? props.value}</option>}
          {filtered.map((model) => (
            <option key={model.id} value={model.id}>{model.name || model.id}</option>
          ))}
        </select>
      </label>
      <div className="model-meta mono">
        <span>{props.value || "No model selected"}</span>
        {selected && <span>{selected.context_length?.toLocaleString() ?? "unknown"} ctx · in {formatModelPrice(selected.pricing.prompt)} · out {formatModelPrice(selected.pricing.completion)}</span>}
      </div>
      <div className="model-controls">
        <label>Search<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="provider, id, name" /></label>
        <label>Price
          <select value={priceFilter} onChange={(event) => setPriceFilter(event.target.value as "all" | "free" | "paid")}>
            <option value="all">All</option>
            <option value="free">Free</option>
            <option value="paid">Paid</option>
          </select>
        </label>
        <label>Min context<input type="number" min={0} value={minContext} onChange={(event) => setMinContext(event.target.value)} placeholder="0" /></label>
        <label>Sort
          <select value={sort} onChange={(event) => setSort(event.target.value as "name" | "newest" | "context" | "input" | "output")}>
            <option value="name">Name</option>
            <option value="newest">Newest</option>
            <option value="context">Context</option>
            <option value="input">Input price</option>
            <option value="output">Output price</option>
          </select>
        </label>
      </div>
      {props.kind === "chat" && (
        <div className="model-check-row">
          <label className="model-check">
            <input type="checkbox" checked={structuredOnly} onChange={(event) => setStructuredOnly(event.target.checked)} />
            Structured output
          </label>
          <label className="model-check">
            <input type="checkbox" checked={reasoningOnly} onChange={(event) => setReasoningOnly(event.target.checked)} />
            Reasoning
          </label>
        </div>
      )}
      <div className="model-count mono">{props.loading ? "Loading models..." : `${filtered.length} of ${props.models.length} models`}</div>
    </section>
  );
}

function formatModelPrice(value?: number) {
  const perMillion = (value ?? 0) * 1_000_000;
  if (perMillion === 0) return "$0/M";
  if (perMillion < 0.01) return `$${perMillion.toFixed(4)}/M`;
  return `$${perMillion.toFixed(2)}/M`;
}

function StatusDot({ status }: { status: string }) {
  return <span className={`dot ${status === "ready" ? "ready" : status === "failed" ? "err" : "work"}`} />;
}
