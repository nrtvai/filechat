import type { Citation, FileRecord, Message } from "../types";
import { citationLocationLabel, citationSourceLabel, fileErrorSummary } from "./shared";

function uniqueLabels(labels: string[]) {
  return Array.from(new Set(labels));
}

function unavailableSourceLabels(unavailableFileIds: string[], files: FileRecord[]) {
  return uniqueLabels(unavailableFileIds.map((fileId) => files.find((file) => file.id === fileId)?.name ?? fileId));
}

function SourceContextDetails({ unavailableFileIds = [], files = [], includeUngroundedWarning = false }: { unavailableFileIds?: string[]; files?: FileRecord[]; includeUngroundedWarning?: boolean }) {
  const unavailableLabels = unavailableSourceLabels(unavailableFileIds, files);
  const readyLabels = files.filter((file) => file.status === "ready").map((file) => file.name);
  const processingLabels = files
    .filter((file) => ["queued", "reading", "indexing"].includes(file.status))
    .map((file) => `${file.name} (${file.status} ${Math.round(file.progress * 100)}%)`);
  const failedLabels = files
    .filter((file) => file.status === "failed")
    .map((file) => file.error ? `${file.name} (${fileErrorSummary(file)})` : file.name);
  const hasNoLocalSourceContext = files.length === 0 && unavailableLabels.length === 0;
  return (
    <>
      {includeUngroundedWarning && readyLabels.length > 0 && <small>Treat this answer as ungrounded until a cited snippet supports it.</small>}
      {includeUngroundedWarning && hasNoLocalSourceContext && <small>Attach local documents before relying on FileChat for grounded document answers.</small>}
      {readyLabels.length > 0 && <small>Available source context: {readyLabels.join(", ")}</small>}
      {processingLabels.length > 0 && <small>Processing source context: {processingLabels.join(", ")}</small>}
      {failedLabels.length > 0 && <small>Failed source context: {failedLabels.join(", ")}</small>}
      {unavailableLabels.length > 0 && <small>Unavailable sources: {unavailableLabels.join(", ")}</small>}
    </>
  );
}

export function NoCitationNotice({ unavailableFileIds = [], files = [], grounding }: { unavailableFileIds?: string[]; files?: FileRecord[]; grounding?: Message["grounding"] }) {
  const hasReadyLocalSources = files.some((file) => file.status === "ready");
  const hasNoLocalSourceContext = files.length === 0 && unavailableFileIds.length === 0;
  const hasOnlyUnavailableSources = !hasReadyLocalSources && unavailableFileIds.length > 0;
  const hasOnlyFailedLocalSources = !hasReadyLocalSources && files.length > 0 && files.every((file) => file.status === "failed");
  const fallbackNotice = hasNoLocalSourceContext
    ? "No local sources were available for this answer."
    : hasOnlyUnavailableSources || hasOnlyFailedLocalSources
      ? "No available local sources supported this answer."
      : "No citations attached to this answer.";
  return (
    <div className="source-strip no-citations" role="status" aria-live="polite" aria-label="No citations attached">
      <strong>{grounding?.notice?.trim() || fallbackNotice}</strong>
      <small>{grounding?.detail?.trim() || "FileChat is being explicit that this response has no retrieved source snippets."}</small>
      <SourceContextDetails unavailableFileIds={unavailableFileIds} files={files} includeUngroundedWarning />
    </div>
  );
}

export function UnavailableSourceNotice({ unavailableFileIds = [], files = [] }: { unavailableFileIds?: string[]; files?: FileRecord[] }) {
  const unavailableLabels = unavailableSourceLabels(unavailableFileIds, files);
  if (unavailableLabels.length === 0) return null;
  return (
    <div className="source-strip no-citations" role="status" aria-live="polite" aria-label="Some sources unavailable">
      <strong>Some sources were unavailable for this answer.</strong>
      <small>Unavailable sources: {unavailableLabels.join(", ")}</small>
    </div>
  );
}

function citationReadinessLabels(citations: Citation[], files: FileRecord[]) {
  const readyFileIds = new Set(files.filter((file) => file.status === "ready").map((file) => file.id));
  return Array.from(new Set(
    citations
      .filter((citation) => !readyFileIds.has(citation.file_id))
      .map((citation) => files.find((file) => file.id === citation.file_id)?.name ?? citationSourceLabel(citation))
  ));
}

export function CitationReadinessNotice({ citations, files }: { citations: Citation[]; files: FileRecord[] }) {
  const notReadyLabels = citationReadinessLabels(citations, files);
  if (notReadyLabels.length === 0) return null;
  return (
    <div className="source-strip no-citations" role="status" aria-live="polite" aria-label="Cited sources not ready">
      <strong>Some cited sources are not ready in this chat.</strong>
      <small>Check or re-upload before relying on these citations: {notReadyLabels.join(", ")}</small>
    </div>
  );
}

export function NoAnswerNotice({ unavailableFileIds = [], files = [], grounding }: { unavailableFileIds?: string[]; files?: FileRecord[]; grounding?: Message["grounding"] }) {
  const hasNoLocalSourceContext = files.length === 0 && unavailableFileIds.length === 0;
  const notice = grounding?.notice?.trim() || (hasNoLocalSourceContext ? "No sourced answer was generated." : "No answer was generated.");
  const detail = grounding?.detail?.trim() || (hasNoLocalSourceContext
    ? "FileChat did not return answer text or local source evidence for this turn."
    : "FileChat did not return answer text for this turn. Re-ask or check sources before relying on it.");
  return (
    <div className="source-strip no-citations no-answer" role="status" aria-live="polite" aria-label="No answer generated">
      <strong>{notice}</strong>
      <small>{detail}</small>
      <SourceContextDetails unavailableFileIds={unavailableFileIds} files={files} includeUngroundedWarning />
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

export function GroundedSourceNotice({ citations, files }: { citations: Citation[]; files: FileRecord[] }) {
  if (citationReadinessLabels(citations, files).length > 0) return null;
  return (
    <div className="source-strip grounded-source" role="status" aria-label="Grounded answer">
      <strong>{groundedSourceLabel(citations)}</strong>
      <small>Each cited snippet is from the attached local documents.</small>
    </div>
  );
}

export function SourcesDisclosure({ citations, onCitationClick, minimized }: { citations: Citation[]; onCitationClick: (citation: Citation) => void; minimized: boolean }) {
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
          <CitationSourceButton key={citation.id} citation={citation} onCitationClick={onCitationClick} showExcerpt />
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
