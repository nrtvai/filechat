#!/usr/bin/env node
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

const checks = [
  {
    name: "chat-first source preflight",
    run: async () => {
      const app = await readFile(resolve(repoRoot, "src/App.tsx"), "utf8");
      const required = [
        "Attach your files",
        "Ask a question about the selected files",
        "rightOpen, setRightOpen] = useState(false)",
        "Agent activity",
      ];
      const missing = required.filter((item) => !app.includes(item));
      if (missing.length > 0) {
        throw new Error(`Missing chat-first/debug-gating markers: ${missing.join(", ")}`);
      }
    },
  },
  {
    name: "frontend App tests",
    command: "npm",
    args: ["test", "--", "--run", "src/App.test.tsx"],
  },
  {
    name: "backend grounded Filechat smoke",
    command: "uv",
    args: ["run", "pytest", "backend/tests/test_api.py::test_filechat_original_smoke_returns_grounded_answer_with_provenance"],
  },
  {
    name: "clean default browser e2e",
    command: "npm",
    args: ["run", "test:e2e", "--", "--grep", "empty workbench renders|chat-first cold start", "--reporter=list"],
  },
];

function runCommand(check) {
  return new Promise((resolveCheck, rejectCheck) => {
    const child = spawn(check.command, check.args, {
      cwd: repoRoot,
      env: { ...process.env, FORCE_COLOR: "0" },
      stdio: "inherit",
    });
    child.on("error", rejectCheck);
    child.on("exit", (code, signal) => {
      if (code === 0) {
        resolveCheck();
        return;
      }
      rejectCheck(new Error(`${check.command} ${check.args.join(" ")} exited ${code ?? signal}`));
    });
  });
}

for (const check of checks) {
  process.stdout.write(`\n[Filechat Original DoD] ${check.name}\n`);
  try {
    if (check.run) {
      await check.run();
    } else {
      await runCommand(check);
    }
  } catch (error) {
    console.error(`\n[Filechat Original DoD] FAILED: ${check.name}`);
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}

console.log("\n[Filechat Original DoD] PASS");
