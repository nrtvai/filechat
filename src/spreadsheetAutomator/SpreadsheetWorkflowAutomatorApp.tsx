import { FormEvent, useEffect, useMemo, useState } from "react";
import { Download, FileSpreadsheet, Loader2 } from "lucide-react";
import { api, WorkflowFileText } from "../api";

type ResultState = {
  questions: string[];
  downloadUrl: string;
  filename: string;
  error: string;
};

const emptyResult: ResultState = {
  questions: [],
  downloadUrl: "",
  filename: "",
  error: "",
};

function parseSourceFileTexts(sourceJson: string): WorkflowFileText[] {
  if (!sourceJson.trim()) return [];
  const parsed = JSON.parse(sourceJson) as unknown;
  if (!Array.isArray(parsed)) throw new Error("Source file summaries JSON must be an array.");
  const fileTexts = parsed.map((item, index) => {
    if (!item || typeof item !== "object") throw new Error(`Source summary ${index + 1} must be an object.`);
    const candidate = item as Partial<WorkflowFileText>;
    if (typeof candidate.file_name !== "string" || typeof candidate.text !== "string") {
      throw new Error(`Source summary ${index + 1} needs file_name and text.`);
    }
    return {
      file_id: candidate.file_id ?? candidate.file_name,
      file_name: candidate.file_name,
      text: candidate.text,
    };
  });
  const uniqueFileNames = new Set(fileTexts.map((item) => item.file_name.trim().toLowerCase()));
  if (uniqueFileNames.size !== fileTexts.length) {
    throw new Error("Source file summaries must use unique file_name values.");
  }
  return fileTexts;
}

export function SpreadsheetWorkflowAutomatorApp() {
  const [description, setDescription] = useState("");
  const [sourceJson, setSourceJson] = useState("");
  const [busy, setBusy] = useState<"interview" | "generate" | "">("");
  const [result, setResult] = useState<ResultState>(emptyResult);

  useEffect(() => {
    return () => {
      if (result.downloadUrl) URL.revokeObjectURL(result.downloadUrl);
    };
  }, [result.downloadUrl]);

  const sourceFileTexts = useMemo(() => {
    try {
      return parseSourceFileTexts(sourceJson);
    } catch {
      return [];
    }
  }, [sourceJson]);

  async function submitWorkflow(mode: "interview" | "generate", event: FormEvent) {
    event.preventDefault();
    setBusy(mode);
    setResult((current) => {
      if (current.downloadUrl) URL.revokeObjectURL(current.downloadUrl);
      return emptyResult;
    });
    try {
      const fileTexts = parseSourceFileTexts(sourceJson);
      const payload = { description, file_texts: fileTexts };
      const response = mode === "interview" ? await api.workflowInterview(payload) : await api.workflowGenerate(payload);
      if (response.status === "generated" && response.html) {
        const blob = new Blob([response.html], { type: response.content_type ?? "text/html" });
        setResult({
          ...emptyResult,
          downloadUrl: URL.createObjectURL(blob),
          filename: response.filename ?? "spreadsheet-workflow-automator.html",
        });
      } else {
        setResult({ ...emptyResult, questions: response.required_questions });
      }
    } catch (error) {
      setResult({ ...emptyResult, error: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy("");
    }
  }

  return (
    <main className="workflow-shell">
      <section className="workflow-header">
        <div className="workflow-brand">
          <FileSpreadsheet size={24} aria-hidden="true" />
          <h1>Spreadsheet Workflow Automator</h1>
        </div>
      </section>

      <form className="workflow-form">
        <label>
          Workflow description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.currentTarget.value)}
            rows={5}
          />
        </label>
        <label>
          Source file summaries JSON
          <textarea
            value={sourceJson}
            onChange={(event) => setSourceJson(event.currentTarget.value)}
            rows={10}
            spellCheck={false}
          />
        </label>
        <div className="workflow-actions">
          <button type="button" onClick={(event) => void submitWorkflow("interview", event)} disabled={busy !== ""}>
            {busy === "interview" ? <Loader2 size={16} className="spin" aria-hidden="true" /> : null}
            Interview
          </button>
          <button type="button" onClick={(event) => void submitWorkflow("generate", event)} disabled={busy !== ""}>
            {busy === "generate" ? <Loader2 size={16} className="spin" aria-hidden="true" /> : null}
            Generate
          </button>
        </div>
      </form>

      <section className="workflow-output" aria-live="polite">
        {result.error ? <p className="error-text">{result.error}</p> : null}
        {result.questions.length > 0 ? (
          <ol>
            {result.questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ol>
        ) : null}
        {result.downloadUrl ? (
          <a className="download-link" href={result.downloadUrl} download={result.filename}>
            <Download size={16} aria-hidden="true" />
            Download local HTML app
          </a>
        ) : null}
        {!result.error && result.questions.length === 0 && !result.downloadUrl ? (
          <p className="subtle">{sourceFileTexts.length} source summaries loaded</p>
        ) : null}
      </section>
    </main>
  );
}
