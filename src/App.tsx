import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PanelLeft } from "lucide-react";
import { api } from "./api";
import { EditionControls } from "./components/EditionControls";
import { LeftRail } from "./components/LeftRail";
import { RightPanel } from "./components/RightPanel";
import { EmptyState, ProcessingView } from "./components/SessionViews";
import { Transcript } from "./components/Transcript";
import { defaultContextProfile, emptyUsageSummary, localTestModeUser, RightTab } from "./components/shared";
import type { AgentRun, Artifact, Citation, ContextProfile, CurrentUser, Edition, FileRecord, MembershipRole, Message, Session, Settings, UsageSummary } from "./types";

const appVersion = import.meta.env.VITE_APP_VERSION ?? "0.0.0";

export function App() {
  return <FileChatApp />;
}

function FileChatApp() {
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
