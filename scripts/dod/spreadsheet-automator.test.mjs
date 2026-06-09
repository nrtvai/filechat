import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { describe, it } from "node:test";

import { getSpreadsheetAutomatorDodChecks } from "./spreadsheet-automator.mjs";

describe("Spreadsheet Workflow Automator DoD script", () => {
  it("defines the product-completion checks required by the milestone ledger", () => {
    const checks = getSpreadsheetAutomatorDodChecks();
    const names = checks.map((check) => check.name);

    assert.deepEqual(names, [
      "separate Spreadsheet Workflow Automator surface",
      "workflowValidation unit tests",
      "excel_workflow pytest",
      "Spreadsheet Workflow Automator e2e",
    ]);
  });

  it("pins explicit docs and package wording for the separate product", async () => {
    const [packageJson, docs] = await Promise.all([
      readFile(new URL("../../package.json", import.meta.url), "utf8").then(JSON.parse),
      readFile(new URL("../../docs/spreadsheet-workflow-automator.md", import.meta.url), "utf8"),
    ]);

    assert.equal(packageJson.scripts["dev:spreadsheet-automator"], "vite --host 127.0.0.1 --port 5174");
    assert.equal(packageJson.scripts["build:spreadsheet-automator"], "tsc -b && vite build");
    assert.match(docs, /^# Spreadsheet Workflow Automator/m);
    assert.match(docs, /\/workflows/);
    assert.match(docs, /\/api\/workflows\/interview/);
    assert.match(docs, /\/api\/workflows\/generate/);
    assert.doesNotMatch(docs.toLowerCase(), /filechat spreadsheet mode/);
  });
});
