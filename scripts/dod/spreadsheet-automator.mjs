#!/usr/bin/env node
import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { constants } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(scriptDir, "..", "..");

export function getSpreadsheetAutomatorDodChecks(root = repoRoot) {
  return [
    {
      name: "separate Spreadsheet Workflow Automator surface",
      run: () => assertSeparateSurface(root),
    },
    {
      name: "workflowValidation unit tests",
      command: "npm",
      args: ["run", "test", "--", "src/spreadsheetAutomator/workflowValidation.test.ts"],
      cwd: root,
    },
    {
      name: "excel_workflow pytest",
      command: "uv",
      args: ["run", "pytest", "backend/tests/test_excel_workflow.py", "backend/tests/test_excel_workflow_html_app.py"],
      cwd: root,
    },
    {
      name: "Spreadsheet Workflow Automator e2e",
      command: "npm",
      args: ["run", "test:e2e", "--", "tests/e2e/spreadsheet-automator.spec.ts", "--reporter=list"],
      cwd: root,
    },
  ];
}

async function assertSeparateSurface(root) {
  const requiredFiles = [
    "src/spreadsheetAutomator/SpreadsheetWorkflowAutomatorApp.tsx",
    "src/spreadsheetAutomator/main.tsx",
    "docs/spreadsheet-workflow-automator.md",
    "tests/e2e/spreadsheet-automator.spec.ts",
  ];
  const missingFiles = [];

  for (const relativePath of requiredFiles) {
    try {
      await access(join(root, relativePath), constants.R_OK);
    } catch {
      missingFiles.push(relativePath);
    }
  }

  const packageJson = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
  const scripts = packageJson.scripts ?? {};
  const missingScripts = ["dev:spreadsheet-automator", "build:spreadsheet-automator"].filter(
    (name) => typeof scripts[name] !== "string",
  );

  if (missingFiles.length > 0 || missingScripts.length > 0) {
    const details = [
      missingFiles.length > 0 ? `missing files: ${missingFiles.join(", ")}` : "",
      missingScripts.length > 0 ? `missing package scripts: ${missingScripts.join(", ")}` : "",
    ].filter(Boolean);
    throw new Error(details.join("; "));
  }
}

async function runCommand(check) {
  return new Promise((resolve) => {
    const child = spawn(check.command, check.args, {
      cwd: check.cwd,
      env: process.env,
      stdio: "inherit",
    });
    child.on("close", (code) => resolve(code ?? 1));
    child.on("error", (error) => {
      console.error(error.message);
      resolve(1);
    });
  });
}

async function main() {
  let failed = false;

  for (const check of getSpreadsheetAutomatorDodChecks()) {
    process.stdout.write(`\n→ ${check.name}\n`);
    try {
      const exitCode = check.run ? await check.run() : await runCommand(check);
      if (exitCode) {
        failed = true;
        process.stdout.write(`✗ ${check.name}\n`);
      } else {
        process.stdout.write(`✓ ${check.name}\n`);
      }
    } catch (error) {
      failed = true;
      process.stdout.write(`✗ ${check.name}: ${error instanceof Error ? error.message : String(error)}\n`);
    }
  }

  if (failed) {
    process.exitCode = 1;
    return;
  }

  process.stdout.write("\nSTATUS: DONE — Spreadsheet Workflow Automator DoD verified\n");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  await main();
}
