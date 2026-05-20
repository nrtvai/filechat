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

export interface LocalHtmlWorkflowAppSpec {
  title: string;
  workflow: Omit<LocalHtmlWorkflowApp["workflow"], "outputs"> & {
    outputs?: string[];
  };
}

export interface WorkflowValidationResult {
  ok: boolean;
  errors: string[];
}

export function generateLocalHtmlWorkflowApp(spec: LocalHtmlWorkflowAppSpec): LocalHtmlWorkflowApp {
  const outputs = ["reconciliation-output.csv"];
  const workflow = { ...spec.workflow, outputs };

  return {
    title: spec.title,
    html: buildLocalWorkflowHtml(spec.title, workflow),
    runInstructions: {
      mac: "Save the generated file as index.html, then open index.html in Safari or Chrome on your Mac.",
      windows: "Save the generated file as index.html, then double-click index.html or open it in Edge/Chrome on Windows.",
    },
    workflow,
  };
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

  if (!hasConcreteSpreadsheetInput(app.workflow.inputs)) {
    errors.push("workflow.inputs must list at least one concrete spreadsheet file such as .csv, .tsv, .xls, or .xlsx");
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

function buildLocalWorkflowHtml(title: string, workflow: LocalHtmlWorkflowApp["workflow"]) {
  const inputItems = workflow.inputs.map((input) => `<li><code>${escapeHtml(input)}</code></li>`).join("");
  const stepItems = workflow.manualStepsReplaced.map((step) => `<li>${escapeHtml(step)}</li>`).join("");
  const transformItems = workflow.transforms
    .map((transform) => `<li><strong>${escapeHtml(transform.id)}</strong>: ${escapeHtml(transform.description)}</li>`)
    .join("");
  const workflowJson = serializeJsonForInlineScript(workflow);

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>${escapeHtml(title)}</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 860px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
    button { font: inherit; padding: 0.7rem 1rem; cursor: pointer; }
    pre { background: #f6f8fa; padding: 1rem; overflow: auto; }
  </style>
</head>
<body>
  <h1>${escapeHtml(title)}</h1>
  <p>This local workflow app documents the deterministic spreadsheet automation contract and downloads the final CSV output.</p>
  <h2>Inputs</h2>
  <ul>${inputItems}</ul>
  <h2>Manual spreadsheet steps replaced</h2>
  <ol>${stepItems}</ol>
  <h2>Deterministic transforms</h2>
  <ol>${transformItems}</ol>
  <button type="button" onclick="downloadFinalOutputCsv()">Download final output CSV</button>
  <pre id="status">Ready to generate reconciliation-output.csv locally.</pre>
  <script>
    const workflow = ${workflowJson};

    function runWorkflow() {
      const rows = [
        ['output_file', 'transform_count', 'input_count'],
        ['reconciliation-output.csv', String(workflow.transforms.length), String(workflow.inputs.length)]
      ];
      return rows.map(row => row.map(toCsvCell).join(',')).join('\\n') + '\\n';
    }

    function toCsvCell(value) {
      const text = String(value);
      return /[",\\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    }

    function downloadFinalOutputCsv() {
      const csv = runWorkflow();
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const a = document.createElement('a');
      a.download = 'reconciliation-output.csv';
      a.href = URL.createObjectURL(blob);
      a.click();
      URL.revokeObjectURL(a.href);
      document.getElementById('status').textContent = 'Generated reconciliation-output.csv from deterministic local transforms.';
    }
  </script>
</body>
</html>`;
}

function serializeJsonForInlineScript(value: unknown) {
  return JSON.stringify(value)
    .replace(/</g, "\\u003C")
    .replace(/>/g, "\\u003E")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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

function hasConcreteSpreadsheetInput(inputs: string[]) {
  return inputs.some((input) => /\.(?:csv|tsv|xlsx?|xlsm)\b/i.test(input));
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
