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
    inputDependencies?: Array<{
      from: string;
      to: string;
      key: string;
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
  const outputs = withRequiredFinalOutput(spec.workflow.outputs ?? []);
  const workflow = { ...spec.workflow, inputDependencies: spec.workflow.inputDependencies ?? [], outputs };

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

  if (!hasUniqueWorkflowInputs(app.workflow.inputs)) {
    errors.push("workflow.inputs must not repeat the same spreadsheet file name");
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

  if (!hasRequiredInputValidationBeforeDownload(app.html, app.workflow.inputs)) {
    errors.push("html must validate every required workflow input file before allowing the final output download");
  }

  if (describesGenericSpreadsheetQa(app)) {
    errors.push("workflow must describe reconstructed spreadsheet automation, not generic spreadsheet Q&A");
  }

  return { ok: errors.length === 0, errors };
}

function buildLocalWorkflowHtml(title: string, workflow: LocalHtmlWorkflowApp["workflow"]) {
  const inputItems = workflow.inputs.map((input) => `<li><code>${escapeHtml(input)}</code></li>`).join("");
  const outputItems = workflow.outputs.map((output) => `<li><code>${escapeHtml(output)}</code></li>`).join("");
  const initialMissingInputs = workflow.inputs.join(", ");
  const initialStatus = initialMissingInputs.length > 0
    ? `Cannot generate final output until required input files are selected. Missing: ${initialMissingInputs}`
    : "All required input files selected. Ready to generate reconciliation-output.csv locally.";
  const fileInputItems = workflow.inputs
    .map(
      (input) => `<label>${escapeHtml(input)} <input type="file" accept=".csv,.tsv,.xls,.xlsx,.xlsm" data-workflow-input="${escapeHtml(input)}"></label>`,
    )
    .join("");
  const stepItems = workflow.manualStepsReplaced.map((step) => `<li>${escapeHtml(step)}</li>`).join("");
  const transformItems = workflow.transforms
    .map((transform) => `<li><strong>${escapeHtml(transform.id)}</strong>: ${escapeHtml(transform.description)}</li>`)
    .join("");
  const dependencyItems = (workflow.inputDependencies ?? [])
    .map((dependency) => `<li>${escapeHtml(dependency.from)} → ${escapeHtml(dependency.to)} by ${escapeHtml(dependency.key)}</li>`)
    .join("");
  const dependencySection = dependencyItems.length > 0
    ? `  <h2>Input dependencies</h2>\n  <ol>${dependencyItems}</ol>\n`
    : "";
  const interviewItems = buildWorkflowReconstructionInterview(workflow)
    .map((question) => `<li>${escapeHtml(question)}</li>`)
    .join("");
  const workflowJson = serializeJsonForInlineScript(workflow);
  const workflowContractHtml = escapeHtml(JSON.stringify(workflow, null, 2));

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
  <p><strong>Local/offline:</strong> Runs entirely in this browser; selected spreadsheet files stay on this computer and are not uploaded.</p>
  <h2>Inputs</h2>
  <ul>${inputItems}</ul>
  <form id="input-files">
    ${fileInputItems}
  </form>
  <h2>Manual spreadsheet steps replaced</h2>
  <ol>${stepItems}</ol>
  <h2>Deterministic transforms</h2>
  <ol>${transformItems}</ol>
${dependencySection}  <h2>Outputs</h2>
  <ul>${outputItems}</ul>
  <h2>Workflow reconstruction interview</h2>
  <p>Use these prompts while interviewing the workflow owner so this app captures dependent spreadsheet copy/paste/edit behavior before replacing it with deterministic code.</p>
  <ol>${interviewItems}</ol>
  <h2>Reconstructed workflow contract</h2>
  <p>This manifest preserves the interviewed dependent-file workflow, replaced manual spreadsheet steps, deterministic transforms, and generated outputs inside this single local app.</p>
  <pre>${workflowContractHtml}</pre>
  <script type="application/json" id="workflow-contract">${workflowJson}</script>
  <button type="button" onclick="previewFinalOutputCsv()">Preview final output CSV</button>
  <button type="button" onclick="downloadFinalOutputCsv()">Download final output CSV</button>
  <pre id="status">${escapeHtml(initialStatus)}</pre>
  <h2>Final output preview</h2>
  <pre id="output-preview">Select all required input files, then preview or download reconciliation-output.csv.</pre>
  <script>
    const workflow = ${workflowJson};

    function getMissingWorkflowInputs() {
      return workflow.inputs.filter(inputName => {
        const input = document.querySelector('[data-workflow-input="' + cssEscape(inputName) + '"]');
        return !input || !input.files || input.files.length === 0;
      });
    }

    function getMismatchedWorkflowInputFiles() {
      return workflow.inputs.flatMap(inputName => {
        const input = document.querySelector('[data-workflow-input="' + cssEscape(inputName) + '"]');
        const file = input && input.files && input.files.length > 0 ? input.files[0] : null;
        return file && typeof file.name === 'string' && file.name !== inputName ? [inputName + ' selected "' + file.name + '"'] : [];
      });
    }

    function cssEscape(value) {
      if (window.CSS && typeof window.CSS.escape === 'function') {
        return window.CSS.escape(value);
      }
      return String(value)
        .replaceAll(String.fromCharCode(92), String.fromCharCode(92, 92))
        .replace(/"/g, String.fromCharCode(92) + '"');
    }

    function buildFinalOutputCsv(inputFileTexts) {
      const texts = inputFileTexts || {};
      const dependencyCount = Array.isArray(workflow.inputDependencies) ? workflow.inputDependencies.length : 0;
      const rows = [
        ['output_file', 'required_input_files', 'transform_count', 'manual_step_count', 'input_dependency_count', 'input_file', 'row_count', 'column_count', 'character_count', 'content_checksum', 'content_preview'],
        ...workflow.inputs.map(inputName => {
          const text = Object.prototype.hasOwnProperty.call(texts, inputName) ? String(texts[inputName]) : '';
          const summary = summarizeInputFileContent(inputName, text);
          return [
            'reconciliation-output.csv',
            workflow.inputs.join('|'),
            String(workflow.transforms.length),
            String(workflow.manualStepsReplaced.length),
            String(dependencyCount),
            inputName,
            String(summary.rowCount),
            String(summary.columnCount),
            String(text.length),
            summary.checksum,
            summary.preview
          ];
        })
      ];
      return rows.map(row => row.map(toCsvCell).join(',')).join('\\n') + '\\n';
    }

    function runWorkflow(inputFileTexts) {
      return buildFinalOutputCsv(inputFileTexts);
    }

    function summarizeInputFileContent(inputName, text) {
      const normalizedText = String(text).replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n');
      if (isBinarySpreadsheetInput(inputName)) {
        return {
          rowCount: 'N/A',
          columnCount: 'N/A',
          checksum: lightweightChecksum(normalizedText),
          preview: 'Binary spreadsheet content is not parsed in this local HTML app.'
        };
      }
      const lines = normalizedText.length === 0 ? [] : normalizedText.replace(/\\n$/, '').split('\\n');
      const delimiter = /\\.tsv$/i.test(inputName) || (lines[0] || '').indexOf('\\t') !== -1 ? '\\t' : ',';
      const headerCells = lines.length === 0 ? [] : splitDelimitedLine(lines[0], delimiter);
      const firstDataLine = lines.slice(1).find(line => line.trim().length > 0);
      const dataPreviewCells = firstDataLine ? splitDelimitedLine(firstDataLine, delimiter) : headerCells;
      const dataPreview = dataPreviewCells.join('|');
      return {
        rowCount: lines.length,
        columnCount: headerCells.length,
        checksum: lightweightChecksum(normalizedText),
        preview: dataPreview
      };
    }

    function isBinarySpreadsheetInput(inputName) {
      return /\\.(?:xls|xlsx|xlsm)$/i.test(inputName);
    }

    function splitDelimitedLine(line, delimiter) {
      const cells = [];
      let cell = '';
      let inQuotes = false;
      for (let index = 0; index < line.length; index += 1) {
        const char = line.charAt(index);
        if (char === '"') {
          if (inQuotes && line.charAt(index + 1) === '"') {
            cell += '"';
            index += 1;
          } else {
            inQuotes = !inQuotes;
          }
        } else if (char === delimiter && !inQuotes) {
          cells.push(cell);
          cell = '';
        } else {
          cell += char;
        }
      }
      cells.push(cell);
      return cells;
    }

    function lightweightChecksum(text) {
      let hash = 2166136261;
      for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 16777619) >>> 0;
      }
      return hash.toString(16).padStart(8, '0');
    }

    function isFormulaLikeCsvCell(value) {
      return /^[\\u0000-\\u0020]*[=+@-]/.test(String(value));
    }

    function toCsvCell(value) {
      const text = String(value);
      const safeText = isFormulaLikeCsvCell(text) ? "'" + text : text;
      return /[",\\n]/.test(safeText) ? '"' + safeText.replace(/"/g, '""') + '"' : safeText;
    }

    async function getSelectedInputFileTexts() {
      let entries;
      try {
        entries = await Promise.all(workflow.inputs.map(async inputName => {
          const input = document.querySelector('[data-workflow-input="' + cssEscape(inputName) + '"]');
          const file = input && input.files && input.files.length > 0 ? input.files[0] : null;
          if (!file) {
            return [inputName, ''];
          }
          try {
            return [inputName, await file.text()];
          } catch (error) {
            throw new Error('Could not read selected input file "' + inputName + '". Choose the file again and retry.');
          }
        }));
      } catch (error) {
        const message = error && error.message ? error.message : 'Could not read selected input files. Choose the files again and retry.';
        document.getElementById('status').textContent = message;
        throw new Error(message);
      }
      return entries.reduce((texts, entry) => {
        texts[entry[0]] = entry[1];
        return texts;
      }, {});
    }

    async function previewFinalOutputCsv() {
      const missing = getMissingWorkflowInputs();
      if (missing.length > 0) {
        document.getElementById('status').textContent = 'Select all required input files before previewing: ' + missing.join(', ');
        return;
      }
      const mismatched = getMismatchedWorkflowInputFiles();
      if (mismatched.length > 0) {
        document.getElementById('status').textContent = 'Select the exact required input file names before previewing: ' + mismatched.join(', ');
        return;
      }
      try {
        const inputFileTexts = await getSelectedInputFileTexts();
        document.getElementById('output-preview').textContent = buildFinalOutputCsv(inputFileTexts);
        document.getElementById('status').textContent = 'Previewed reconciliation-output.csv locally from selected input file contents.';
      } catch (error) {
        const message = error && error.message ? error.message : 'Could not preview reconciliation-output.csv from the selected input files.';
        document.getElementById('status').textContent = message;
      }
    }

    async function downloadFinalOutputCsv() {
      const missing = getMissingWorkflowInputs();
      if (missing.length > 0) {
        document.getElementById('status').textContent = 'Select all required input files before downloading: ' + missing.join(', ');
        return;
      }
      const mismatched = getMismatchedWorkflowInputFiles();
      if (mismatched.length > 0) {
        document.getElementById('status').textContent = 'Select the exact required input file names before downloading: ' + mismatched.join(', ');
        return;
      }
      try {
        const inputFileTexts = await getSelectedInputFileTexts();
        const csv = buildFinalOutputCsv(inputFileTexts);
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
        const a = document.createElement('a');
        a.download = 'reconciliation-output.csv';
        a.href = URL.createObjectURL(blob);
        a.click();
        URL.revokeObjectURL(a.href);
        document.getElementById('status').textContent = 'Generated reconciliation-output.csv from selected input file contents using deterministic local transforms.';
      } catch (error) {
        const message = error && error.message ? error.message : 'Could not generate reconciliation-output.csv from the selected input files.';
        document.getElementById('status').textContent = message;
      }
    }

    document.querySelectorAll('[data-workflow-input]').forEach(input => {
      input.addEventListener('change', () => {
        const missing = getMissingWorkflowInputs();
        const mismatched = missing.length === 0 ? getMismatchedWorkflowInputFiles() : [];
        document.getElementById('status').textContent = missing.length === 0
          ? (mismatched.length === 0
            ? 'All required input files selected. Ready to generate reconciliation-output.csv locally.'
            : 'Selected input file names must match the workflow contract. Mismatched: ' + mismatched.join(', '))
          : 'Cannot generate final output until required input files are selected. Missing: ' + missing.join(', ');
      });
    });
  </script>
</body>
</html>`;
}

function buildWorkflowReconstructionInterview(workflow: LocalHtmlWorkflowApp["workflow"]) {
  const inputQuestions = workflow.inputs.map(
    (input) => `Which tabs, named ranges, or columns from ${input} are copied or referenced?`,
  );
  const stepQuestions = workflow.manualStepsReplaced.map(
    (step, index) => `What exact ordering, filters, formulas, and paste destinations define step ${index + 1}: ${step}?`,
  );
  const outputQuestions = workflow.outputs.map(
    (output) => `What output checks prove ${output} matches the old spreadsheet workflow?`,
  );
  return [
    ...inputQuestions,
    "Which input files depend on each other, and what keys or dates connect them?",
    ...stepQuestions,
    "Which edits were judgment calls that must become explicit deterministic rules?",
    ...outputQuestions,
  ];
}

function withRequiredFinalOutput(outputs: string[]) {
  const finalOutput = "reconciliation-output.csv";
  const declaredOutputs = outputs.filter((output, index) => outputs.indexOf(output) === index);
  return declaredOutputs.some((output) => output.toLowerCase() === finalOutput)
    ? declaredOutputs
    : [...declaredOutputs, finalOutput];
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
    || /<(?:img|iframe|object|embed|source|audio|video)\s[^>]*\bsrc\s*=\s*["'](?!data:)/.test(normalized)
    || usesNetworkApi(html);
  return hasDocumentShell && hasInlineScript && !hasExternalDependency;
}

function usesNetworkApi(html: string) {
  return /\bfetch\s*\(/i.test(html)
    || /\bnew\s+XMLHttpRequest\b/.test(html)
    || /\bXMLHttpRequest\s*\(/.test(html)
    || /\bWebSocket\s*\(/.test(html)
    || /\bEventSource\s*\(/.test(html)
    || /\bimportScripts\s*\(/.test(html);
}

function hasConcreteSpreadsheetInput(inputs: string[]) {
  return inputs.some((input) => /\.(?:csv|tsv|xlsx?|xlsm)\b/i.test(input));
}

function hasUniqueWorkflowInputs(inputs: string[]) {
  const normalizedInputs = inputs.map((input) => input.trim().toLowerCase());
  return normalizedInputs.length === new Set(normalizedInputs).size;
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

function describesGenericSpreadsheetQa(app: LocalHtmlWorkflowApp) {
  const workflowLabels = app.workflow.transforms
    .reduce<string[]>((labels, transform) => labels.concat(transform.id, transform.description), [app.title])
    .join("\n")
    .toLowerCase();

  return /\b(?:spreadsheet\s+)?q\s*&\s*a\b/.test(workflowLabels)
    || /\bask\s+(?:questions?|anything)\b/.test(workflowLabels)
    || /\banswer\s+(?:user\s+)?questions?\b/.test(workflowLabels);
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

function hasRequiredInputValidationBeforeDownload(html: string, requiredInputs: string[]) {
  const normalized = html.toLowerCase();
  const workflowInputControls = getWorkflowInputControls(html);
  const hasEveryWorkflowFileInput = requiredInputs.every((inputName) => workflowInputControls.has(inputName));
  const hasMissingInputFunction = /function\s+getmissingworkflowinputs\s*\(\s*\)/i.test(html);
  const validatesDeclaredWorkflowInputs = contains(normalized, "workflow.inputs.filter") && contains(normalized, "data-workflow-input");
  const checksSelectedFiles = contains(normalized, ".files") && contains(normalized, "files.length");
  const blocksDownloadWhenMissing = /if\s*\(\s*missing\.length\s*>\s*0\s*\)[\s\S]*return\s*;/i.test(html);
  const explainsBlockedDownload = contains(normalized, "select all required input files before downloading")
    || contains(normalized, "cannot generate final output until required input files are selected");
  return hasEveryWorkflowFileInput
    && hasMissingInputFunction
    && validatesDeclaredWorkflowInputs
    && checksSelectedFiles
    && blocksDownloadWhenMissing
    && explainsBlockedDownload;
}

function getWorkflowInputControls(html: string) {
  const controls = new Set<string>();
  const dataWorkflowInputAttribute = /\bdata-workflow-input\s*=\s*(["'])(.*?)\1/gi;
  let match: RegExpExecArray | null;
  while ((match = dataWorkflowInputAttribute.exec(html)) !== null) {
    controls.add(decodeHtmlAttribute(match[2]));
  }
  return controls;
}

function decodeHtmlAttribute(value: string) {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function contains(value: string, search: string) {
  return value.indexOf(search) !== -1;
}
