import { FormEvent, useCallback, useLayoutEffect, useRef } from "react";
import { Loader2 } from "lucide-react";
import { ArtifactRenderer } from "../artifacts";
import type { AgentRun, Artifact, Citation, ContextProfile, FileRecord, Message, UsageSummary } from "../types";
import { Composer, SessionCostSummary } from "./Composer";
import { CitationReadinessNotice, GroundedSourceNotice, NoAnswerNotice, NoCitationNotice, SourcesDisclosure, UnavailableSourceNotice } from "./messageNotices";
import { MessageCost, RenderedMessage } from "./RenderedMessage";
import { AgentActivity, FollowUpQuestionCards, PlanningQuestionCard } from "./runCards";

export function Transcript(props: {
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
  const hasNonReadySources = props.files.some((file) => file.status !== "ready");
  const pendingSourceLabel = hasNonReadySources ? "Reading ready sources only..." : "Reading the sources...";

  return (
    <section className="transcript">
      <div ref={scrollRef} className="turns" onScroll={onScroll}>
        {props.messages.map((message) => {
          const messageRun = message.role === "assistant" ? props.runs.find((run) => run.assistant_message_id === message.id) : undefined;
          const hasAnswerText = message.content.trim().length > 0;
          return (
            <article key={message.id} className={`turn ${message.role}`}>
              <div className="turn-label mono caps">{message.role === "user" ? "You" : "FileChat"}</div>
              <div className="bubble">
                {message.role === "assistant" && !hasAnswerText ? <NoAnswerNotice unavailableFileIds={message.unavailable_file_ids} files={props.files} grounding={message.grounding} /> : <RenderedMessage content={message.content} />}
                <MessageCost message={message} />
                {message.role === "assistant" && hasAnswerText && visibleArtifacts(message, props.contextProfile).length > 0 && (
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
                {message.role === "assistant" && hasAnswerText && message.citations.length > 0 && (
                  <GroundedSourceNotice citations={message.citations} files={props.files} />
                )}
                {message.role === "assistant" && message.citations.length > 0 && (
                  <SourcesDisclosure citations={message.citations} onCitationClick={props.onCitationClick} minimized={props.contextProfile.citation_display === "minimized"} />
                )}
                {message.role === "assistant" && hasAnswerText && message.citations.length > 0 && (
                  <CitationReadinessNotice citations={message.citations} files={props.files} />
                )}
                {message.role === "assistant" && hasAnswerText && message.citations.length > 0 && (message.unavailable_file_ids?.length ?? 0) > 0 && (
                  <UnavailableSourceNotice unavailableFileIds={message.unavailable_file_ids} files={props.files} />
                )}
                {message.role === "assistant" && hasAnswerText && message.citations.length === 0 && message.grounding?.status !== "not_applicable" && (message.grounding?.status === "no_citations" || props.files.length === 0 || props.files.some((file) => file.status === "ready" || file.status === "failed" || ["queued", "reading", "indexing"].includes(file.status)) || (message.unavailable_file_ids?.length ?? 0) > 0) && (
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
              <span>{pendingSourceLabel}</span>
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
