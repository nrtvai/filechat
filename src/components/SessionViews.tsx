import { FormEvent, useRef } from "react";
import { Paperclip } from "lucide-react";
import type { FileRecord } from "../types";
import { Composer } from "./Composer";
import { acceptedTypes, fileErrorSummary, formatBytes, formatCost, handleFileInputChange, statusLabel, StatusDot } from "./shared";

export function EmptyState(props: {
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

export function ProcessingView(props: {
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

export function FileRow({ file }: { file: FileRecord }) {
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
