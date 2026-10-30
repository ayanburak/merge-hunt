/**
 * file: playwright.config.ts
 * description: Browser tests of the frontend, run with npm run test:e2e.
 */

// The backend is faked inside each test, so only the dev server is started here. One already
// running is reused, starting a second one would stop on the taken port.
//
// NOTE: @playwright/test stays pinned to the build that uses chromium-1247, the browser already
// on this machine. A newer version needs npx playwright install chromium first.

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: "http://localhost:5173" },
  webServer: { command: "npm run dev", url: "http://localhost:5173", reuseExistingServer: true },
});
