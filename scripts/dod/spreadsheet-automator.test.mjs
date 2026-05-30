import assert from "node:assert/strict";
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
});
