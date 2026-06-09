import { expect, test } from "@playwright/test";

function workflowSummary(workbook: string, worksheet: string, rawCsv: string) {
  const [headers, ...rows] = rawCsv.trim().split("\n");
  const previewRows = rows.map((row) => `| ${row.split(",").join(" | ")} |`).join("\n");
  const headerCells = headers.split(",").join(" | ");
  const separator = headers.split(",").map(() => "---").join(" | ");

  return [
    "# Excel Mode Spreadsheet Summary",
    "",
    `Workbook: ${workbook}`,
    "Mode: Excel / spreadsheet analysis lane",
    "",
    `## Worksheet: ${worksheet}`,
    `Rows: ${rows.length}`,
    `Columns: ${headers.split(",").length}`,
    `Headers: ${headers.split(",").join(", ")}`,
    "",
    "Preview (source rows 2-4):",
    `| ${headerCells} |`,
    `| ${separator} |`,
    previewRows,
    "",
    "## Raw Data (CSV)",
    "```csv",
    rawCsv.trim(),
    "```",
    "",
  ].join("\n");
}

test("vague workflow request returns interview questions without a generated app", async ({ page }) => {
  await page.goto("/workflows");

  await expect(page.getByRole("heading", { name: "Spreadsheet Workflow Automator" })).toBeVisible();
  await expect(page.getByText("FileChat", { exact: true })).toHaveCount(0);

  await page.getByLabel("Workflow description").fill("automate my spreadsheets");
  await page.getByRole("button", { name: "Interview" }).click();

  await expect(page.getByText("Which source spreadsheet files are required for this recurring workflow?")).toBeVisible();
  await expect(page.getByRole("link", { name: "Download local HTML app" })).toHaveCount(0);
});

test("specified workflow produces a downloadable local HTML app", async ({ page }) => {
  await page.goto("/workflows");

  await page.getByLabel("Workflow description").fill("turn my weekly spreadsheet copy/paste/edit reconciliation into a local HTML app");
  await page.getByLabel("Source file summaries JSON").fill(JSON.stringify([
    {
      file_id: "forecast",
      file_name: "forecast.csv",
      text: workflowSummary("forecast.csv", "forecast", "SKU,Qty\nA1,10\nB2,20\n"),
    },
    {
      file_id: "actuals",
      file_name: "actuals.csv",
      text: workflowSummary("actuals.csv", "actuals", "SKU,Qty\nA1,12\nC3,30\n"),
    },
  ]));
  await page.getByRole("button", { name: "Generate" }).click();

  const download = page.getByRole("link", { name: "Download local HTML app" });
  await expect(download).toBeVisible();
  await expect(download).toHaveAttribute("download", "spreadsheet-workflow-automator.html");
  await expect(download).toHaveAttribute("href", /^blob:/);
});

test("duplicate source file names are rejected before generation", async ({ page }) => {
  await page.goto("/workflows");

  await page.getByLabel("Workflow description").fill("turn my weekly spreadsheet copy/paste/edit reconciliation into a local HTML app");
  await page.getByLabel("Source file summaries JSON").fill(JSON.stringify([
    {
      file_id: "forecast",
      file_name: "forecast.csv",
      text: workflowSummary("forecast.csv", "forecast", "SKU,Qty\nA1,10\n"),
    },
    {
      file_id: "forecast-copy",
      file_name: "forecast.csv",
      text: workflowSummary("forecast.csv", "forecast", "SKU,Qty\nA1,12\n"),
    },
  ]));
  await page.getByRole("button", { name: "Generate" }).click();

  await expect(page.getByText("Source file summaries must use unique file_name values.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Download local HTML app" })).toHaveCount(0);
});
