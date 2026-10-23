import { defineConfig } from "@playwright/test";

// The browser walk runs against a real backend on a fresh company world (scripted model) and the built UI.
// It uses the Edge already on the machine, so no browser download is needed; set VESTLY_E2E_CHANNEL=chrome to switch.
const apiPort = process.env.VESTLY_E2E_API_PORT ?? "8765";

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  expect: { timeout: 60_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4173",
    channel: process.env.VESTLY_E2E_CHANNEL ?? "msedge",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "node e2e/backend.mjs",
      url: `http://127.0.0.1:${apiPort}/health`,
      timeout: 180_000,
      reuseExistingServer: false,
      stdout: "pipe",
    },
    {
      command: "npx vite build && npx vite preview --host 127.0.0.1",
      url: "http://127.0.0.1:4173",
      timeout: 120_000,
      reuseExistingServer: false,
      env: { VESTLY_API: `http://127.0.0.1:${apiPort}` },
    },
  ],
});
