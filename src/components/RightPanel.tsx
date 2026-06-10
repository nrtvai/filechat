import { X } from "lucide-react";
import { api } from "../api";
import type { AgentRun, Artifact, Citation, ContextProfile, CurrentUser, FileRecord, Session, Settings, UsageSummary } from "../types";
import { AgentActivity, formatRunStatus, PlanningQuestionCard, RunSetupDetails } from "./runCards";
import { SettingsTab } from "./SettingsTab";
import { citationLocationLabel, citationSourceLabel, FileMini, formatCost, RightTab } from "./shared";

export function RightPanel(props: {
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
        {(["files", "citations", "artifacts", "runs"] as const).map((tab) => (
          <button key={tab} className={props.tab === tab ? "on" : ""} onClick={() => props.setTab(tab)}>{tab}</button>
        ))}
        {props.tab !== "citations" && (
          <button className={props.tab === "settings" ? "on" : ""} onClick={() => props.setTab("settings")}>settings</button>
        )}
        {props.currentUser?.capabilities.use_admin_console && props.tab !== "citations" && (
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
