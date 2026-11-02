import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  reporter: [["list"]],
  use: { baseURL: process.env.SITE_URL ?? "http://localhost:4173/careo/", trace: "off" },
  webServer: process.env.SITE_URL
    ? undefined
    : { command: "npm run preview", url: "http://localhost:4173/careo/", reuseExistingServer: true },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
