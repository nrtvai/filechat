import { readFile } from "node:fs/promises";
import { test } from "node:test";
import assert from "node:assert/strict";

const scriptPath = new URL("./filechat-original.mjs", import.meta.url);

test("Filechat Original DoD script runs the required focused checks", async () => {
  const source = await readFile(scriptPath, "utf8");

  assert.match(source, /npm["'],\s*args:\s*\["test",\s*"--",\s*"--run",\s*"src\/App\.test\.tsx"\]/);
  assert.match(source, /uv["'],\s*args:\s*\["run",\s*"pytest",\s*"backend\/tests\/test_api\.py::test_filechat_original_smoke_returns_grounded_answer_with_provenance"/);
  assert.match(source, /npm["'],\s*args:\s*\["run",\s*"test:e2e",\s*"--",\s*"--grep",\s*"empty workbench renders|chat-first cold start"/);
  assert.match(source, /Attach your files/);
  assert.match(source, /Agent activity/);
});
