import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL: "http://127.0.0.1:5174",
    launchOptions: process.env.PURCHASE_TEST_BROWSER
      ? { executablePath: process.env.PURCHASE_TEST_BROWSER }
      : {},
  },
  webServer: {
    command: "npm run dev -- --port 5174",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: false,
  },
  outputDir: "test-results",
});
