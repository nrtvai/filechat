import { test, expect } from "@playwright/test";

test("empty workbench renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("FileChat")).toBeVisible();
  await expect(page.getByText("Attach files")).toBeVisible();
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

  await expect(page.getByText("Survey chart")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Open source for Yes" })).toBeVisible();
  await expect(page.locator(".native-chart").getByText("10")).toBeVisible();

  await page.getByRole("button", { name: "runs" }).click();
  await expect(page.getByText("Agent activity")).toBeVisible();
  await expect(page.locator(".right-panel").getByText("implement")).toBeVisible();
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
  await transcriptQuestion.getByRole("button", { name: /리더 공유용/ }).click();

  await expect(page.locator(".artifact-file_draft").getByRole("heading", { name: /분석 초안/ })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".turn.assistant .artifact-table")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Open source for 예" })).toBeVisible();
  await expect(page.locator(".native-chart").getByText("10")).toBeVisible();

  await page.getByRole("button", { name: "artifacts" }).click();
  await expect(page.getByLabel("Artifact list").getByText(/원자료 미리보기/)).toBeVisible();

  await page.getByRole("button", { name: "runs" }).click();
  await expect(page.getByText("Agent activity")).toBeVisible();
  await expect(page.locator(".right-panel .run-card.failed")).toHaveCount(0);
});
