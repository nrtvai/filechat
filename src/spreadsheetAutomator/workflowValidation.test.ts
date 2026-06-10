import ordersFixture from "./__fixtures__/orders.csv?raw";
import stockFixture from "./__fixtures__/stock.tsv?raw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { generateLocalHtmlWorkflowApp, validateLocalHtmlWorkflowApp } from "./workflowValidation";

function getGeneratedWorkflowFunction<T>(html: string, functionName: string): T {
  const scriptBody = html.match(/<script>\n([\s\S]*?)\n\s\s<\/script>/)?.[1];
  expect(scriptBody).toBeTruthy();
  return new Function(`${scriptBody}\nreturn ${functionName};`)() as T;
}

function runGeneratedWorkflowCsv(html: string, inputFileTexts: Record<string, string> = {}) {
  const buildFinalOutputCsv = getGeneratedWorkflowFunction<(inputFileTexts?: Record<string, string>) => string>(
    html,
    "buildFinalOutputCsv",
  );
  return buildFinalOutputCsv(inputFileTexts);
}

describe("generateLocalHtmlWorkflowApp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns a complete self-contained local HTML app with a deterministic final CSV download contract", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      workflow: {
        inputs: ["sales_orders.csv", "warehouse_stock_units.csv"],
        manualStepsReplaced: ["copy weekly sales rows into the reorder sheet", "edit reorder quantity column by SKU"],
        transforms: [
          { id: "join-sales-stock", description: "Join sales to stock by SKU", deterministic: true },
          { id: "calculate-reorder", description: "Calculate reorder quantity from sales and on-hand units", deterministic: true },
        ],
      },
    });

    expect(app.title).toBe("Weekly inventory reorder builder");
    expect(app.workflow.outputs).toEqual(["reconciliation-output.csv"]);
    expect(app.runInstructions.mac).toContain("index.html");
    expect(app.runInstructions.windows).toContain("Windows");
    expect(app.html).toContain("<!doctype html>");
    expect(app.html).toContain("sales_orders.csv");
    expect(app.html).toContain("warehouse_stock_units.csv");
    expect(app.html.match(/type="file"/g)).toHaveLength(2);
    expect(app.html.match(/accept="\.csv,\.tsv,\.xls,\.xlsx,\.xlsm"/g)).toHaveLength(2);
    expect(app.html).toContain('data-workflow-input="sales_orders.csv"');
    expect(app.html).toContain('data-workflow-input="warehouse_stock_units.csv"');
    expect(app.html).toContain("function getMissingWorkflowInputs()");
    expect(app.html).toContain(
      "<pre id=\"status\">Cannot generate final output until required input files are selected. Missing: sales_orders.csv, warehouse_stock_units.csv</pre>",
    );
    expect(app.html).not.toContain("<pre id=\"status\">Ready to generate reconciliation-output.csv locally.</pre>");
    expect(app.html).toContain("Select all required input files before downloading: ");
    expect(app.html).toContain("Cannot generate final output until required input files are selected.");
    expect(app.html).toContain("Download final output CSV");
    expect(app.html).toContain("Runs entirely in this browser; selected spreadsheet files stay on this computer and are not uploaded.");
    expect(app.html).toContain("new Blob");
    expect(app.html).toContain("text/csv");
    expect(app.html).toContain("a.download = 'reconciliation-output.csv'");
    expect(app.html).toContain("function buildFinalOutputCsv(inputFileTexts)");
    expect(app.html).toContain("'input_file', 'row_count', 'column_count', 'character_count', 'content_checksum'");
    expect(app.html).toContain('<script type="application/json" id="workflow-contract">');
    expect(app.html).toContain('<h2>Reconstructed workflow contract</h2>');
    expect(app.html).toContain('"manualStepsReplaced":["copy weekly sales rows into the reorder sheet","edit reorder quantity column by SKU"]');
    expect(app.html).toContain("await file.text()");
    expect(app.html).toContain("String(workflow.manualStepsReplaced.length)");
    expect(app.html).not.toMatch(/<script\s[^>]*\bsrc\s*=/i);

    expect(validateLocalHtmlWorkflowApp(app)).toEqual({ ok: true, errors: [] });
  });

  it("includes a targeted reconstruction interview checklist for dependent spreadsheet workflows", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      workflow: {
        inputs: ["sales_orders.csv", "warehouse_stock_units.csv"],
        manualStepsReplaced: ["copy weekly sales rows into the reorder sheet", "edit reorder quantity column by SKU"],
        transforms: [
          { id: "join-sales-stock", description: "Join sales to stock by SKU", deterministic: true },
          { id: "calculate-reorder", description: "Calculate reorder quantity from sales and on-hand units", deterministic: true },
        ],
      },
    });

    expect(app.html).toContain("<h2>Workflow reconstruction interview</h2>");
    expect(app.html).toContain("Which tabs, named ranges, or columns from sales_orders.csv are copied or referenced?");
    expect(app.html).toContain("Which tabs, named ranges, or columns from warehouse_stock_units.csv are copied or referenced?");
    expect(app.html).toContain("What exact ordering, filters, formulas, and paste destinations define step 1: copy weekly sales rows into the reorder sheet?");
    expect(app.html).toContain("What output checks prove reconciliation-output.csv matches the old spreadsheet workflow?");
    expect(app.html).not.toContain("Ask questions about a spreadsheet");
  });

  it("preserves additional declared outputs while adding the deterministic reconciliation CSV", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Weekly close workbook builder",
      workflow: {
        inputs: ["ledger.csv", "bank_export.csv"],
        manualStepsReplaced: ["copy bank rows into the ledger reconciliation sheet"],
        transforms: [{ id: "match-ledger-bank", description: "Match ledger and bank rows by transaction id", deterministic: true }],
        outputs: ["exception-report.csv", "reconciliation-output.csv"],
      },
    });

    expect(app.workflow.outputs).toEqual(["exception-report.csv", "reconciliation-output.csv"]);
    expect(app.html).toContain("exception-report.csv");
    expect(validateLocalHtmlWorkflowApp(app)).toEqual({ ok: true, errors: [] });
  });

  it("escapes workflow metadata before embedding it in the generated inline script", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Unsafe metadata workflow",
      workflow: {
        inputs: ["orders</script><script src='https://evil.example/app.js'></script>.csv"],
        manualStepsReplaced: ["copy rows from CSV into sheet & edit quantity columns"],
        transforms: [
          {
            id: "join-rows",
            description: "Join rows by SKU before </script><script>alert('x')</script> export",
            deterministic: true,
          },
        ],
      },
    });

    expect(validateLocalHtmlWorkflowApp(app)).toEqual({ ok: true, errors: [] });
    expect(app.html).not.toContain("</script><script");
    expect(app.html).not.toMatch(/<script\s[^>]*\bsrc\s*=/i);
    expect(app.html).toContain("\\u003C/script\\u003E");
  });

  it("neutralizes spreadsheet formulas in generated CSV workflow metadata", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Formula-safe workflow",
      workflow: {
        inputs: ["=HYPERLINK(\"https://evil.test\",\"x\").csv", "safe.csv"],
        manualStepsReplaced: ["copy rows into final workbook"],
        transforms: [{ id: "copy-safe", description: "Copy rows deterministically", deterministic: true }],
      },
    });

    const csv = runGeneratedWorkflowCsv(app.html, {
      '=HYPERLINK("https://evil.test","x").csv': "sku,amount\n=cmd|' /C calc'!A0,10",
      "safe.csv": "sku,qty\nA-1,4",
    });

    expect(csv).toContain("required_input_files");
    expect(csv).toContain("'=" + "HYPERLINK");
    expect(csv).not.toContain("\n=HYPERLINK");
    expect(csv).toContain("'=" + "cmd");
    expect(csv).not.toContain("\n=cmd");
  });

  it("neutralizes formula payloads with leading whitespace or control characters", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Formula-safe workflow",
      workflow: {
        inputs: ["orders.csv", "safe.csv"],
        manualStepsReplaced: ["copy rows into final workbook"],
        transforms: [{ id: "copy-safe", description: "Copy rows deterministically", deterministic: true }],
      },
    });

    const csv = runGeneratedWorkflowCsv(app.html, {
      "orders.csv": "sku,amount\n  =cmd|' /C calc'!A0,10",
      "safe.csv": "sku,qty\nA-1,4",
    });

    expect(csv).toContain("'  =cmd");
    expect(csv).not.toContain("\n  =cmd");
  });

  it("records declared input dependency keys in the local runtime export using fixture-backed input files", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Fixture-backed inventory workflow",
      workflow: {
        inputs: ["orders.csv", "stock.tsv"],
        manualStepsReplaced: ["copy order rows into stock spreadsheet by SKU"],
        transforms: [{ id: "join-orders-stock", description: "Join selected files by SKU", deterministic: true }],
        inputDependencies: [{ from: "orders.csv", to: "stock.tsv", key: "sku" }],
      },
    });
    const inputTexts = {
      "orders.csv": ordersFixture,
      "stock.tsv": stockFixture,
    };

    const csv = runGeneratedWorkflowCsv(app.html, inputTexts);

    expect(validateLocalHtmlWorkflowApp(app)).toEqual({ ok: true, errors: [] });
    expect(app.html).toContain("<h2>Input dependencies</h2>");
    expect(app.html).toContain("orders.csv → stock.tsv by sku");
    expect(csv.split("\n")[0]).toBe(
      "output_file,required_input_files,transform_count,manual_step_count,input_dependency_count,input_file,row_count,column_count,character_count,content_checksum,content_preview",
    );
    expect(csv).toContain("reconciliation-output.csv,orders.csv|stock.tsv,1,1,1,orders.csv,3,2,");
    expect(csv).toContain("reconciliation-output.csv,orders.csv|stock.tsv,1,1,1,stock.tsv,3,2,");
  });

  it("builds deterministic final CSV rows from selected local input file contents", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Content-based workflow",
      workflow: {
        inputs: ["orders.csv", "stock.tsv"],
        manualStepsReplaced: ["copy order rows into stock spreadsheet"],
        transforms: [{ id: "summarize-inputs", description: "Summarize selected input files", deterministic: true }],
      },
    });

    const inputTexts = {
      "orders.csv": "sku,qty\nA-1,2\nB-2,3\n",
      "stock.tsv": "sku\ton_hand\nA-1\t8\n",
    };
    const csvA = runGeneratedWorkflowCsv(app.html, inputTexts);
    const csvB = runGeneratedWorkflowCsv(app.html, {
      ...inputTexts,
      "orders.csv": "sku,qty\nA-1,200\nB-2,3\n",
    });
    const csvARepeat = runGeneratedWorkflowCsv(app.html, inputTexts);

    expect(csvA).toBe(csvARepeat);
    expect(csvA).not.toBe(csvB);
    expect(csvA.split("\n")[0]).toBe(
      "output_file,required_input_files,transform_count,manual_step_count,input_dependency_count,input_file,row_count,column_count,character_count,content_checksum,content_preview",
    );
    expect(csvA).toContain("reconciliation-output.csv,orders.csv|stock.tsv,1,1,0,orders.csv,3,2,");
    expect(csvA).toContain("reconciliation-output.csv,orders.csv|stock.tsv,1,1,0,stock.tsv,2,2,");
    expect(csvA).toContain("A-1|2");
    expect(csvA).toContain("A-1|8");
  });

  it("summarizes binary spreadsheet inputs as opaque local content instead of delimited rows", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Workbook workflow",
      workflow: {
        inputs: ["ledger.xlsx", "bank_export.xlsm", "notes.xls"],
        manualStepsReplaced: ["copy workbook rows into a reconciliation spreadsheet"],
        transforms: [{ id: "summarize-inputs", description: "Summarize selected input files", deterministic: true }],
      },
    });

    const csv = runGeneratedWorkflowCsv(app.html, {
      "ledger.xlsx": "PK\u0003\u0004fake workbook bytes",
      "bank_export.xlsm": "macro workbook bytes",
      "notes.xls": "legacy workbook bytes",
    });

    expect(validateLocalHtmlWorkflowApp(app)).toEqual({ ok: true, errors: [] });
    expect(app.html.match(/accept="\.csv,\.tsv,\.xls,\.xlsx,\.xlsm"/g)).toHaveLength(3);
    expect(csv).toContain(
      "reconciliation-output.csv,ledger.xlsx|bank_export.xlsm|notes.xls,1,1,0,ledger.xlsx,N/A,N/A,",
    );
    expect(csv).toContain("Binary spreadsheet content is not parsed in this local HTML app.");
    expect(csv).not.toContain("ledger.xlsx,1,1");
  });

  it("reports file-read failures without creating a Blob or triggering a download", async () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Read failure workflow",
      workflow: {
        inputs: ["orders.csv"],
        manualStepsReplaced: ["copy rows into final workbook"],
        transforms: [{ id: "copy-safe", description: "Copy rows deterministically", deterministic: true }],
      },
    });
    const status = { textContent: "" };
    const fileInput = {
      files: [{ text: vi.fn().mockRejectedValue(new Error("permission denied")) }],
      addEventListener: vi.fn(),
    };
    const blobSpy = vi.fn();
    const clickSpy = vi.fn();

    vi.stubGlobal("window", { CSS: { escape: (value: string) => value } });
    vi.stubGlobal("document", {
      querySelector: vi.fn((selector: string) => (selector.includes('data-workflow-input="orders.csv"') ? fileInput : null)),
      querySelectorAll: vi.fn(() => [fileInput]),
      getElementById: vi.fn(() => status),
      createElement: vi.fn(() => ({ click: clickSpy })),
    });
    vi.stubGlobal("Blob", blobSpy);
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:csv"),
      revokeObjectURL: vi.fn(),
    });
    const downloadFinalOutputCsv = getGeneratedWorkflowFunction<() => Promise<void>>(
      app.html,
      "downloadFinalOutputCsv",
    );

    await downloadFinalOutputCsv();

    expect(status.textContent).toBe(
      'Could not read selected input file "orders.csv". Choose the file again and retry.',
    );
    expect(blobSpy).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
  });

  it("previews the deterministic final CSV locally without triggering a download", async () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Preview workflow",
      workflow: {
        inputs: ["orders.csv"],
        manualStepsReplaced: ["copy rows into final workbook"],
        transforms: [{ id: "copy-safe", description: "Copy rows deterministically", deterministic: true }],
      },
    });
    const status = { textContent: "" };
    const outputPreview = { textContent: "" };
    const fileInput = {
      files: [{ name: "orders.csv", text: vi.fn().mockResolvedValue("sku,qty\nA-1,2") }],
      addEventListener: vi.fn(),
    };
    const blobSpy = vi.fn();
    const clickSpy = vi.fn();

    expect(app.html).toContain("Preview final output CSV");
    expect(app.html).toContain("function previewFinalOutputCsv()");
    vi.stubGlobal("window", { CSS: { escape: (value: string) => value } });
    vi.stubGlobal("document", {
      querySelector: vi.fn((selector: string) => (selector.includes('data-workflow-input="orders.csv"') ? fileInput : null)),
      querySelectorAll: vi.fn(() => [fileInput]),
      getElementById: vi.fn((id: string) => (id === "output-preview" ? outputPreview : status)),
      createElement: vi.fn(() => ({ click: clickSpy })),
    });
    vi.stubGlobal("Blob", blobSpy);
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:csv"),
      revokeObjectURL: vi.fn(),
    });
    const previewFinalOutputCsv = getGeneratedWorkflowFunction<() => Promise<void>>(
      app.html,
      "previewFinalOutputCsv",
    );

    await previewFinalOutputCsv();

    expect(outputPreview.textContent).toContain("output_file,required_input_files");
    expect(outputPreview.textContent).toContain("reconciliation-output.csv,orders.csv,1,1,0,orders.csv,2,2,");
    expect(status.textContent).toBe("Previewed reconciliation-output.csv locally from selected input file contents.");
    expect(blobSpy).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
  });

  it("blocks the final output download when a selected local file name does not match the required spreadsheet input", async () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Wrong file guard workflow",
      workflow: {
        inputs: ["orders.csv"],
        manualStepsReplaced: ["copy rows into final workbook"],
        transforms: [{ id: "copy-safe", description: "Copy rows deterministically", deterministic: true }],
      },
    });
    const status = { textContent: "" };
    const fileInput = {
      files: [{ name: "customers.csv", text: vi.fn().mockResolvedValue("id,name\n1,Ada") }],
      addEventListener: vi.fn(),
    };
    const blobSpy = vi.fn();
    const clickSpy = vi.fn();

    expect(app.html).toContain("function getMismatchedWorkflowInputFiles()");
    expect(app.html).toContain("Select the exact required input file names before downloading: ");
    expect(validateLocalHtmlWorkflowApp(app)).toEqual({ ok: true, errors: [] });

    vi.stubGlobal("window", { CSS: { escape: (value: string) => value } });
    vi.stubGlobal("document", {
      querySelector: vi.fn((selector: string) => (selector.includes('data-workflow-input="orders.csv"') ? fileInput : null)),
      querySelectorAll: vi.fn(() => [fileInput]),
      getElementById: vi.fn(() => status),
      createElement: vi.fn(() => ({ click: clickSpy })),
    });
    vi.stubGlobal("Blob", blobSpy);
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:csv"),
      revokeObjectURL: vi.fn(),
    });
    const downloadFinalOutputCsv = getGeneratedWorkflowFunction<() => Promise<void>>(
      app.html,
      "downloadFinalOutputCsv",
    );

    await downloadFinalOutputCsv();

    expect(status.textContent).toBe(
      'Select the exact required input file names before downloading: orders.csv selected "customers.csv"',
    );
    expect(fileInput.files[0].text).not.toHaveBeenCalled();
    expect(blobSpy).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
  });
});

describe("validateLocalHtmlWorkflowApp", () => {
  function validateHtmlWithWorkflowScript(script: string) {
    return validateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      html: `<!doctype html><html><body><input type='file' data-workflow-input='sales_orders.csv'><button>Download final output CSV</button><pre id='status'></pre><script>const workflow = { inputs: ['sales_orders.csv'] }; function getMissingWorkflowInputs(){ return workflow.inputs.filter(inputName => { const input = document.querySelector('[data-workflow-input="' + inputName + '"]'); return !input || !input.files || input.files.length === 0; }); } ${script} function downloadFinalOutputCsv(){ const missing = getMissingWorkflowInputs(); if (missing.length > 0) { document.getElementById('status').textContent = 'Select all required input files before downloading: ' + missing.join(', '); return; } const blob = new Blob(['a,b'], { type: 'text/csv' }); const a = document.createElement('a'); a.download = 'reconciliation-output.csv'; a.href = URL.createObjectURL(blob); a.click(); }</script></body></html>`,
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["sales_orders.csv"],
        manualStepsReplaced: ["copy weekly sales into the reorder sheet"],
        transforms: [{ id: "calculate", description: "Calculate reorder output", deterministic: true }],
        outputs: ["reconciliation-output.csv"],
      },
    });
  }

  it("accepts a deterministic local HTML workflow app with Mac and Windows run instructions", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      html: "<!doctype html><html><body><input type='file' data-workflow-input='sales_orders.csv'><input type='file' data-workflow-input='warehouse_stock_units.csv'><button>Download final output CSV</button><pre id='status'></pre><script>const workflow = { inputs: ['sales_orders.csv', 'warehouse_stock_units.csv'] }; function getMissingWorkflowInputs(){ return workflow.inputs.filter(inputName => { const input = document.querySelector('[data-workflow-input=\"' + inputName + '\"]'); return !input || !input.files || input.files.length === 0; }); } function runWorkflow(input){ return input; } function downloadFinalOutputCsv(){ const missing = getMissingWorkflowInputs(); if (missing.length > 0) { document.getElementById('status').textContent = 'Select all required input files before downloading: ' + missing.join(', '); return; } const blob = new Blob(['a,b'], { type: 'text/csv' }); const a = document.createElement('a'); a.download = 'reconciliation-output.csv'; a.href = URL.createObjectURL(blob); a.click(); }</script></body></html>",
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["sales_orders.csv", "warehouse_stock_units.csv"],
        manualStepsReplaced: ["copy weekly sales into the reorder sheet", "edit reorder quantities by SKU"],
        transforms: [
          { id: "join-sales-stock", description: "Join sales to stock by SKU", deterministic: true },
        ],
        outputs: ["reconciliation-output.csv"],
      },
    });

    expect(result.ok).toBe(true);
    expect(result.errors).toEqual([]);
  });

  it("rejects generic spreadsheet Q&A artifacts that do not ship a runnable deterministic local app", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Ask questions about a spreadsheet",
      html: "<div>Upload a spreadsheet and ask anything.</div>",
      runInstructions: { mac: "", windows: "" },
      workflow: {
        inputs: ["workbook.xlsx"],
        manualStepsReplaced: [],
        transforms: [{ id: "answer", description: "Answer user questions", deterministic: false }],
        outputs: [],
      },
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toEqual([
      "html must be a complete self-contained local HTML document with <!doctype html>, <html>, and inline <script> tags",
      "runInstructions.mac must explain how to open/run the local HTML app on macOS",
      "runInstructions.windows must explain how to open/run the local HTML app on Windows",
      "workflow.manualStepsReplaced must list at least one copy/paste/edit step being automated",
      "workflow.transforms must all be deterministic coded transforms",
      "workflow.outputs must list at least one generated spreadsheet/workflow output",
      "workflow.outputs must include the deterministic reconciliation-output.csv final output",
      "html must include a local final output CSV download action implemented with a browser Blob and deterministic filename",
      "html must validate every required workflow input file before allowing the final output download",
      "workflow must describe reconstructed spreadsheet automation, not generic spreadsheet Q&A",
    ]);
  });

  it("rejects generic spreadsheet Q&A labels even when the artifact has file inputs and deterministic output plumbing", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Spreadsheet Q&A assistant",
      workflow: {
        inputs: ["orders.csv"],
        manualStepsReplaced: ["copy rows into the final spreadsheet"],
        transforms: [{ id: "answer-questions", description: "Answer questions about uploaded spreadsheet rows", deterministic: true }],
      },
    });

    expect(validateLocalHtmlWorkflowApp(app)).toEqual({
      ok: false,
      errors: ["workflow must describe reconstructed spreadsheet automation, not generic spreadsheet Q&A"],
    });
  });

  it("rejects local HTML apps that only display a report without a final output download", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Weekly reconciliation builder",
      html: "<!doctype html><html><body><button>Run local reconciliation</button><pre id='report'></pre><script>function runWorkflow(input){ return input; }</script></body></html>",
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["forecast.csv", "actuals.csv"],
        manualStepsReplaced: ["copy rows from forecast.csv and paste matched rows into actuals.csv"],
        transforms: [{ id: "reconcile", description: "Reconcile rows by SKU", deterministic: true }],
        outputs: ["reconciliation-output.csv"],
      },
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain("html must include a local final output CSV download action implemented with a browser Blob and deterministic filename");
  });

  it("rejects local HTML apps that allow final output download without validating required input files", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Weekly reconciliation builder",
      html: "<!doctype html><html><body><form><input type='file' data-workflow-input='forecast.csv'><input type='file' data-workflow-input='actuals.csv'></form><button>Download final output CSV</button><pre id='status'></pre><script>function runWorkflow(input){ return input; } function downloadFinalOutputCsv(){ const blob = new Blob(['a,b'], { type: 'text/csv' }); const a = document.createElement('a'); a.download = 'reconciliation-output.csv'; a.href = URL.createObjectURL(blob); a.click(); }</script></body></html>",
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["forecast.csv", "actuals.csv"],
        manualStepsReplaced: ["copy rows from forecast.csv and paste matched rows into actuals.csv"],
        transforms: [{ id: "reconcile", description: "Reconcile rows by SKU", deterministic: true }],
        outputs: ["reconciliation-output.csv"],
      },
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain("html must validate every required workflow input file before allowing the final output download");
  });

  it("rejects local HTML apps that omit a declared workflow input from file controls and validation", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Weekly reconciliation builder",
      html: "<!doctype html><html><body><form><input type='file' data-workflow-input='forecast.csv'></form><button>Download final output CSV</button><pre id='status'></pre><script>const workflow = { inputs: ['forecast.csv', 'actuals.csv'] }; function getMissingWorkflowInputs(){ const input = document.querySelector('[data-workflow-input=\"forecast.csv\"]'); return input.files && input.files.length > 0 ? [] : ['forecast.csv']; } function runWorkflow(input){ return input; } function downloadFinalOutputCsv(){ const missing = getMissingWorkflowInputs(); if (missing.length > 0) { document.getElementById('status').textContent = 'Select all required input files before downloading: ' + missing.join(', '); return; } const blob = new Blob(['a,b'], { type: 'text/csv' }); const a = document.createElement('a'); a.download = 'reconciliation-output.csv'; a.href = URL.createObjectURL(blob); a.click(); }</script></body></html>",
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["forecast.csv", "actuals.csv"],
        manualStepsReplaced: ["copy rows from forecast.csv and paste matched rows into actuals.csv"],
        transforms: [{ id: "reconcile", description: "Reconcile rows by SKU", deterministic: true }],
        outputs: ["reconciliation-output.csv"],
      },
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain("html must validate every required workflow input file before allowing the final output download");
  });

  it("rejects local HTML apps whose workflow output contract omits the deterministic final CSV", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Weekly reconciliation builder",
      html: "<!doctype html><html><body><button>Download final output CSV</button><script>function downloadFinalOutputCsv(){ const blob = new Blob(['a,b'], { type: 'text/csv' }); const a = document.createElement('a'); a.download = 'reconciliation-output.csv'; a.href = URL.createObjectURL(blob); a.click(); }</script></body></html>",
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["forecast.csv", "actuals.csv"],
        manualStepsReplaced: ["copy rows from forecast.csv and paste matched rows into actuals.csv"],
        transforms: [{ id: "reconcile", description: "Reconcile rows by SKU", deterministic: true }],
        outputs: ["reorder_plan.csv"],
      },
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain("workflow.outputs must include the deterministic reconciliation-output.csv final output");
  });

  it("rejects generic browser instructions and vague manual steps", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      html: "<!doctype html><html><body><button>Download final output CSV</button><script>function runWorkflow(input){ return input; } function downloadFinalOutputCsv(){ const blob = new Blob(['a,b'], { type: 'text/csv' }); const a = document.createElement('a'); a.download = 'reconciliation-output.csv'; a.href = URL.createObjectURL(blob); a.click(); }</script></body></html>",
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Open index.html in Chrome.",
      },
      workflow: {
        inputs: ["sales_orders.csv"],
        manualStepsReplaced: ["review the workbook"],
        transforms: [{ id: "calculate", description: "Calculate reorder output", deterministic: true }],
        outputs: ["reconciliation-output.csv"],
      },
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain("runInstructions.windows must explain how to open/run the local HTML app on Windows");
    expect(result.errors).toContain("workflow.manualStepsReplaced must list at least one copy/paste/edit step being automated");
  });

  it("rejects workflows without concrete spreadsheet file inputs", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      html: "<!doctype html><html><body><button>Download final output CSV</button><script>function runWorkflow(input){ return input; } function downloadFinalOutputCsv(){ const blob = new Blob(['a,b'], { type: 'text/csv' }); const a = document.createElement('a'); a.download = 'reconciliation-output.csv'; a.href = URL.createObjectURL(blob); a.click(); }</script></body></html>",
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["weekly inventory dashboard"],
        manualStepsReplaced: ["copy weekly sales rows into the reorder sheet"],
        transforms: [{ id: "calculate", description: "Calculate reorder output", deterministic: true }],
        outputs: ["reconciliation-output.csv"],
      },
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain("workflow.inputs must list at least one concrete spreadsheet file such as .csv, .tsv, .xls, or .xlsx");
  });

  it("rejects duplicate workflow input file names because each upload slot must map to one source file", () => {
    const app = generateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      workflow: {
        inputs: ["sales_orders.csv", "sales_orders.csv", "warehouse_stock_units.csv"],
        manualStepsReplaced: ["copy weekly sales rows into the reorder sheet"],
        transforms: [{ id: "calculate", description: "Calculate reorder output", deterministic: true }],
      },
    });

    expect(validateLocalHtmlWorkflowApp(app)).toEqual({
      ok: false,
      errors: ["workflow.inputs must not repeat the same spreadsheet file name"],
    });
  });

  it.each([
    ["fetch", "async function runWorkflow(input){ return fetch('https://api.example.test/transform', { method: 'POST', body: input }); }"],
    ["XMLHttpRequest", "function runWorkflow(){ const request = new XMLHttpRequest(); request.open('GET', 'https://api.example.test/transform'); return request; }"],
    ["XMLHttpRequest without parentheses", "function runWorkflow(){ const request = new XMLHttpRequest; request.open('GET', 'https://api.example.test/transform'); return request; }"],
    ["WebSocket", "function runWorkflow(){ return new WebSocket('wss://api.example.test/stream'); }"],
    ["EventSource", "function runWorkflow(){ return new EventSource('https://api.example.test/events'); }"],
    ["importScripts", "function runWorkflow(){ importScripts('https://cdn.example.test/worker-helper.js'); return 'done'; }"],
  ])("rejects the %s network API so the generated app remains fully local and offline", (_apiName, script) => {
    const result = validateHtmlWithWorkflowScript(script);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain("html must be a complete self-contained local HTML document with <!doctype html>, <html>, and inline <script> tags");
  });

  it("rejects external or linked asset dependencies so the generated app remains local", () => {
    const externalScriptResult = validateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      html: '<!doctype html><html><body><script src="https://cdn.example.test/app.js"></script></body></html>',
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["sales_orders.csv"],
        manualStepsReplaced: ["copy weekly sales into the reorder sheet"],
        transforms: [{ id: "calculate", description: "Calculate reorder output", deterministic: true }],
        outputs: ["reconciliation-output.csv"],
      },
    });
    const linkedCssResult = validateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      html: '<!doctype html><html><head><link rel="stylesheet" href="styles.css"></head><body><script>function runWorkflow(input){ return input; }</script></body></html>',
      runInstructions: {
        mac: "Open index.html in Safari or Chrome on your Mac.",
        windows: "Double-click index.html or open it in Edge/Chrome on Windows.",
      },
      workflow: {
        inputs: ["sales_orders.csv"],
        manualStepsReplaced: ["copy weekly sales into the reorder sheet"],
        transforms: [{ id: "calculate", description: "Calculate reorder output", deterministic: true }],
        outputs: ["reconciliation-output.csv"],
      },
    });

    expect(externalScriptResult.ok).toBe(false);
    expect(externalScriptResult.errors).toContain("html must be a complete self-contained local HTML document with <!doctype html>, <html>, and inline <script> tags");
    expect(linkedCssResult.ok).toBe(false);
    expect(linkedCssResult.errors).toContain("html must be a complete self-contained local HTML document with <!doctype html>, <html>, and inline <script> tags");
  });
});
