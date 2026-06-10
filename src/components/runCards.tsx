import { useState } from "react";
import { Paperclip, Send } from "lucide-react";
import type { AgentRun, AgentRunAction, AgentRunQuestion, FileRecord } from "../types";
import { providerErrorSummary } from "./shared";

export function formatRunStatus(status: AgentRun["status"]) {
  return status.replaceAll("_", " ");
}

function recordLike(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

export function FollowUpQuestionCards({
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

export function PlanningQuestionCard({
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

export function RunSetupDetails({ run }: { run: AgentRun }) {
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

export function AgentActivity({ run, compact = false }: { run: AgentRun; compact?: boolean }) {
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
