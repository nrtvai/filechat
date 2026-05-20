export interface LocalHtmlWorkflowApp {
  title: string;
  html: string;
  runInstructions: {
    mac: string;
    windows: string;
  };
  workflow: {
    inputs: string[];
    manualStepsReplaced: string[];
    transforms: Array<{
      id: string;
      description: string;
      deterministic: boolean;
    }>;
    outputs: string[];
  };
}

export interface WorkflowValidationResult {
  ok: boolean;
  errors: string[];
}

export function validateLocalHtmlWorkflowApp(app: LocalHtmlWorkflowApp): WorkflowValidationResult {
  const errors: string[] = [];

  if (!isCompleteLocalHtmlApp(app.html)) {
    errors.push("html must be a complete self-contained local HTML document with <!doctype html>, <html>, and inline <script> tags");
  }

  if (!hasLocalRunInstruction(app.runInstructions.mac, ["mac", "macos"])) {
    errors.push("runInstructions.mac must explain how to open/run the local HTML app on macOS");
  }

  if (!hasLocalRunInstruction(app.runInstructions.windows, ["windows"])) {
    errors.push("runInstructions.windows must explain how to open/run the local HTML app on Windows");
  }

  if (!hasConcreteSpreadsheetStep(app.workflow.manualStepsReplaced)) {
    errors.push("workflow.manualStepsReplaced must list at least one copy/paste/edit step being automated");
  }

  if (app.workflow.transforms.length === 0 || app.workflow.transforms.some((transform) => !transform.deterministic)) {
    errors.push("workflow.transforms must all be deterministic coded transforms");
  }

  if (app.workflow.outputs.length === 0) {
    errors.push("workflow.outputs must list at least one generated spreadsheet/workflow output");
  }

  if (!app.workflow.outputs.some((output) => output.toLowerCase() === "reconciliation-output.csv")) {
    errors.push("workflow.outputs must include the deterministic reconciliation-output.csv final output");
  }

  if (!hasLocalFinalOutputDownload(app.html)) {
    errors.push("html must include a local final output CSV download action implemented with a browser Blob and deterministic filename");
  }

  return { ok: errors.length === 0, errors };
}

function isCompleteLocalHtmlApp(html: string) {
  const normalized = html.toLowerCase();
  const hasDocumentShell = contains(normalized, "<!doctype html") && contains(normalized, "<html");
  const hasInlineScript = /<script(?:\s[^>]*)?>[\s\S]*<\/script>/.test(normalized);
  const hasExternalDependency = /<script\s[^>]*\bsrc\s*=/.test(normalized)
    || /<link\s[^>]*\bhref\s*=/.test(normalized)
    || /<(?:img|iframe|object|embed|source|audio|video)\s[^>]*\bsrc\s*=\s*["'](?!data:)/.test(normalized);
  return hasDocumentShell && hasInlineScript && !hasExternalDependency;
}

function hasConcreteSpreadsheetStep(steps: string[]) {
  const actionWords = ["copy", "paste", "edit", "update", "fill", "merge", "join", "calculate"];
  const spreadsheetWords = ["cell", "cells", "row", "rows", "column", "columns", "sheet", "workbook", "spreadsheet", "csv", "xlsx"];
  return steps.some((step) => {
    const normalized = step.toLowerCase();
    return actionWords.some((word) => contains(normalized, word))
      && spreadsheetWords.some((word) => contains(normalized, word));
  });
}

function hasLocalRunInstruction(instruction: string, platformWords: string[]) {
  const normalized = instruction.toLowerCase();
  const namesHtmlFile = contains(normalized, "index.html") || contains(normalized, ".html");
  const opensLocally = contains(normalized, "open") || contains(normalized, "double-click") || contains(normalized, "double click");
  const namesPlatform = platformWords.some((word) => contains(normalized, word));
  return namesHtmlFile && opensLocally && namesPlatform;
}

function hasLocalFinalOutputDownload(html: string) {
  const normalized = html.toLowerCase();
  const hasVisibleDownloadAction = contains(normalized, "download final output csv");
  const hasBlobCsv = /new\s+blob\s*\(/i.test(html) && contains(normalized, "text/csv");
  const createsLocalObjectUrl = contains(normalized, "url.createobjecturl");
  const setsDownloadFilename = /\.download\s*=\s*["']reconciliation-output\.csv["']/.test(html)
    || /download\s*=\s*["']reconciliation-output\.csv["']/.test(normalized);
  return hasVisibleDownloadAction && hasBlobCsv && createsLocalObjectUrl && setsDownloadFilename;
}

function contains(value: string, search: string) {
  return value.indexOf(search) !== -1;
}
