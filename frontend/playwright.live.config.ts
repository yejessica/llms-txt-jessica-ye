import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "live.spec.ts",
  outputDir: "test-results/live",
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 60_000 },
  use: {
    ...devices["Desktop Chrome"],
    channel: "chrome",
    baseURL: "http://127.0.0.1:3101",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "../backend/.venv/bin/python tests/live-backend.py",
      url: "http://127.0.0.1:8100/api/health",
      reuseExistingServer: false,
      timeout: 30_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
    },
    {
      command: "npm run start -- --hostname 127.0.0.1 --port 3101",
      env: { BACKEND_URL: "http://127.0.0.1:8100" },
      url: "http://127.0.0.1:3101",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
