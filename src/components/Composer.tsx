import { FormEvent, KeyboardEvent, useRef } from "react";
import { Loader2, Paperclip, Send, X } from "lucide-react";
import type { FileRecord, UsageSummary } from "../types";
import { acceptedTypes, contextStatus, formatCost, formatTokens, handleFileInputChange } from "./shared";

export function SessionCostSummary({ usage }: { usage: UsageSummary }) {
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

export function Composer(props: {
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
  const hasNonReadyContext = processing > 0 || failed > 0;
  const helper = ready > 0
    ? [
        `${ready} ready source${ready === 1 ? "" : "s"}`,
        processing > 0 ? `${processing} processing` : null,
        failed > 0 ? `${failed} failed` : null,
        hasNonReadyContext ? "answers use ready sources only" : null,
        "Cmd/Ctrl+Enter to send",
      ].filter(Boolean).join(" · ")
    : failed > 0
      ? "No ready sources · fix failed files before sending"
      : props.files.length === 0
        ? "Attach a file to ask grounded questions · drafts stay local until a source is ready"
        : "No ready sources yet · you can draft while files process";
  const askDisabledReason = ready === 0
    ? "Attach a ready local source before asking; your draft will stay local."
    : props.busy
      ? "FileChat is still reading the sources for the current request."
      : undefined;
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
        <button className="send-btn" disabled={props.disabled || !props.value.trim()} title={(props.disabled || !props.value.trim()) ? askDisabledReason : undefined} type="submit">
          {props.busy ? <Loader2 className="spin" size={15} /> : <Send size={15} />} Ask
        </button>
      </div>
    </form>
  );
}
