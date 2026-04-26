import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:18081",
    trace: "on-first-retry"
  },
  webServer: [
    {
      command: "uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 18080",
      port: 18080,
      reuseExistingServer: true,
      timeout: 120_000,
      env: {
        FILECHAT_ALLOW_FAKE_OPENROUTER: "true",
        FILECHAT_DATA_DIR: ".filechat-e2e"
      }
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 18081 --strictPort",
      port: 18081,
      reuseExistingServer: true,
      timeout: 120_000,
      env: {
        VITE_DEV_API_TARGET: "http://127.0.0.1:18080"
      }
    }
  ],
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } }
  ]
});
