import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  webServer: {
    command: "../scripts/run-browser-test-stack.sh",
    url: "http://127.0.0.1:5174/health/ready",
    timeout: 120_000,
    reuseExistingServer: false,
  },
  use: {
    baseURL: process.env.FINANCE_FRONTEND_URL ?? "http://127.0.0.1:5174",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : undefined,
  },
});
