import { describe, expect, it } from "vitest";
import { generateLocalHtmlWorkflowApp, validateLocalHtmlWorkflowApp } from "./workflowValidation";

describe("generateLocalHtmlWorkflowApp", () => {
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
    expect(app.html).toContain("Download final output CSV");
    expect(app.html).toContain("new Blob");
    expect(app.html).toContain("text/csv");
    expect(app.html).toContain("a.download = 'reconciliation-output.csv'");
    expect(app.html).not.toMatch(/<script\s[^>]*\bsrc\s*=/i);

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
});

describe("validateLocalHtmlWorkflowApp", () => {
  it("accepts a deterministic local HTML workflow app with Mac and Windows run instructions", () => {
    const result = validateLocalHtmlWorkflowApp({
      title: "Weekly inventory reorder builder",
      html: "<!doctype html><html><body><button>Download final output CSV</button><script>function runWorkflow(input){ return input; } function downloadFinalOutputCsv(){ const blob = new Blob(['a,b'], { type: 'text/csv' }); const a = document.createElement('a'); a.download = 'reconciliation-output.csv'; a.href = URL.createObjectURL(blob); a.click(); }</script></body></html>",
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
    ]);
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
