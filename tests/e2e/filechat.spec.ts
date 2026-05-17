import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

test("empty workbench renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("FileChat", { exact: true })).toBeVisible();
  await expect(page.getByText("Attach files")).toBeVisible();
});

test("chat-first cold start keeps draft through file attach and sends after indexing", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Attach files" })).toBeEnabled();
  await page.getByRole("button", { name: "New session" }).click();

  const composer = page.getByLabel("Ask a question about the selected files");
  await expect(composer).toBeVisible();
  await expect(composer).toBeEnabled();
  await expect(page.getByText("No ready sources yet · you can draft while files process")).toBeVisible({ timeout: 15_000 });

  const draft = "Make a chart about the survey result";
  await composer.fill(draft);
  await expect(composer).toHaveValue(draft);

  await expect(page.getByRole("button", { name: "Attach files" })).toBeEnabled();
  await page.setInputFiles("input[type='file']", {
    name: "customer_survey.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Answer,Count\nYes,10\nNo,4\nMaybe,2\n")
  });

  await expect(composer).toHaveValue(draft);
  await expect(page.getByRole("heading", { name: "1 of 1 files ready" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /Ask/ }).click();

  await expect(page.getByRole("button", { name: "Open source for Yes" })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".native-chart").getByText("10")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/\bundefined\b|\bNaN\b|\$NaN|fake citation|synthetic source confidence/i);
});

test("survey chart request renders grounded artifact and phases", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Attach files" })).toBeEnabled();
  await page.setInputFiles("input[type='file']", {
    name: "survey.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Answer,Count\nYes,10\nNo,4\nMaybe,2\n")
  });

  await expect(page.getByRole("heading", { name: "1 of 1 files ready" })).toBeVisible({ timeout: 15_000 });
  await page.getByLabel("Ask a question about the selected files").fill("Make a chart about the survey result");
  await page.getByRole("button", { name: /Ask/ }).click();

  await expect(page.locator(".artifact-chart").getByRole("heading", { name: /survey chart/i })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Open source for Yes" })).toBeVisible();
  await expect(page.locator(".native-chart").getByText("10")).toBeVisible();

  await page.getByRole("button", { name: "runs" }).click();
  await expect(page.getByText("Agent activity")).toBeVisible();
  await expect(page.locator(".right-panel").getByText("persist response").first()).toBeVisible();
});

test("broad Korean analysis request asks a planning question then builds artifacts", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Attach files" })).toBeEnabled();
  await page.setInputFiles("input[type='file']", {
    name: "Form Responses 1.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Answer,Count\n예,10\n아니오,4\n")
  });

  await expect(page.getByRole("heading", { name: "1 of 1 files ready" })).toBeVisible({ timeout: 15_000 });
  await page.getByLabel("Ask a question about the selected files").fill("분석 자료 제작");
  await page.getByRole("button", { name: /Ask/ }).click();

  const transcriptQuestion = page.getByRole("main").getByLabel("Planning question");
  await expect(transcriptQuestion).toBeVisible({ timeout: 15_000 });
  await transcriptQuestion.getByRole("button", { name: /Handle automatically/ }).click();

  await expect(page.locator(".artifact-file_draft").getByRole("heading", { name: /form responses 1 draft/i })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".turn.assistant .artifact-table")).toBeVisible();
  await expect(page.getByRole("button", { name: "Open source for 예" })).toBeVisible();
  await expect(page.locator(".native-chart").getByText("10")).toBeVisible();

  await page.getByRole("button", { name: "artifacts" }).click();
  await expect(page.getByLabel("Artifact list").getByText(/Form Responses 1/).first()).toBeVisible();

  await page.getByRole("button", { name: "runs" }).click();
  await expect(page.getByText("Agent activity")).toBeVisible();
  await expect(page.locator(".right-panel .run-card.failed")).toHaveCount(0);
});

test("artifact discovery renders selectable JSON options and produces selected artifacts", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Attach files" })).toBeEnabled();
  await page.setInputFiles("input[type='file']", {
    name: "monthly_revenue.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Month,Revenue,Cost\nJan,10,6\nFeb,15,8\nMar,21,11\n")
  });

  await expect(page.getByRole("heading", { name: "1 of 1 files ready" })).toBeVisible({ timeout: 15_000 });
  await page.getByLabel("Ask a question about the selected files").fill("what charts and docs can you make with this?");
  await page.getByRole("button", { name: /Ask/ }).click();

  await expect(page.locator(".artifact-decision_cards .json-artifact-card h4")).toHaveText("Available Charts And Docs", { timeout: 15_000 });
  await expect(page.getByRole("button", { name: /Copy request/i })).toHaveCount(0);
  const followUpCard = page.locator(".follow-up-question-card", { hasText: "Available Charts And Docs" }).first();
  await expect(followUpCard).toBeVisible();
  const produce = followUpCard.getByRole("button", { name: /Produce selected/ });
  await expect(produce).toBeDisabled();
  const choices = followUpCard.getByRole("checkbox");
  expect(await choices.count()).toBeGreaterThanOrEqual(2);
  await choices.nth(0).check();
  await choices.nth(1).check();
  await expect(produce).toBeEnabled();
  await produce.click();

  await expect(page.locator(".turn.assistant .artifact-chart, .turn.assistant .artifact-table, .turn.assistant .artifact-summary_panel").nth(1)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/could not render/i)).toHaveCount(0);
});

test("roadmap chart request renders JSON timeline artifact", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Attach files" })).toBeEnabled();
  await page.setInputFiles("input[type='file']", {
    name: "roadmap.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("AI adoption roadmap: 4월 proposal review. 5월 foundational training. 6월 consulting and tool build. 7월 support.")
  });

  await expect(page.getByRole("heading", { name: "1 of 1 files ready" })).toBeVisible({ timeout: 15_000 });
  await page.getByLabel("Ask a question about the selected files").fill("Create the AI adoption roadmap chart");
  await page.getByRole("button", { name: /Ask/ }).click();

  await expect(page.getByText("AI Adoption Roadmap")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".json-timeline span.mono", { hasText: "4월" }).first()).toBeVisible();
  await expect(page.locator(".json-timeline span.mono", { hasText: "5월" }).first()).toBeVisible();
  await expect(page.getByText(/could not render/i)).toHaveCount(0);
});

test("regional forecast chart renders reviewed insight and starts an attached follow-up", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Attach files" })).toBeEnabled();
  await page.setInputFiles("input[type='file']", {
    name: "regional_demand_forecast.csv",
    mimeType: "text/csv",
    buffer: readFileSync("test_documents/correlated_business/regional_demand_forecast.csv")
  });

  await expect(page.getByRole("heading", { name: "1 of 1 files ready" })).toBeVisible({ timeout: 15_000 });
  await page.getByLabel("Ask a question about the selected files").fill("best chart for this file");
  await page.getByRole("button", { name: /Ask/ }).click();

  const insight = page.getByRole("region", { name: "Insight narrative" });
  await expect(insight.getByText("Aggregated values can hide row-level variation.").first()).toBeVisible({ timeout: 15_000 });
  await expect(insight.getByText("Inspect the largest segment before acting.").first()).toBeVisible();
  const followUpCard = page.locator(".follow-up-question-card").first();
  await expect(followUpCard.getByText("Which forecast_month segment should be reviewed next?")).toBeVisible();
  await expect(page.getByLabel("Ask a question about the selected files")).toBeEnabled();

  await followUpCard.getByRole("button", { name: "Largest segment" }).click();
  await followUpCard.getByLabel("Follow-up note").fill("Focus on West region.");
  await followUpCard.getByLabel(/regional_demand_forecast\.csv/).check();
  await followUpCard.getByRole("button", { name: /Start follow-up/ }).click();

  await page.getByRole("button", { name: "runs" }).click();
  await expect(page.locator(".right-panel .run-card-header strong", { hasText: "Follow up on the completed chart insight." }).first()).toBeVisible({ timeout: 15_000 });
});
