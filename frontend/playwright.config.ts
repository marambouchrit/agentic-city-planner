import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8100;
const WEB_PORT = 3100;
const PYTHON = process.platform === "win32" ? ".venv\\Scripts\\python" : ".venv/bin/python";

// The browser talks to the real API code, with a scripted LLM and no network or quota. See tests/e2e_server.py.
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60_000,
  use: { baseURL: `http://localhost:${WEB_PORT}` },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: `${PYTHON} -m tests.e2e_server`,
      cwd: "..",
      url: `http://localhost:${API_PORT}/health`,
      env: { E2E_API_PORT: String(API_PORT), E2E_FRONTEND_ORIGIN: `http://localhost:${WEB_PORT}` },
      reuseExistingServer: false,
    },
    {
      command: `npm run dev -- -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      env: { NEXT_PUBLIC_API_URL: `http://localhost:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
