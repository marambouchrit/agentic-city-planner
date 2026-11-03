import { defineConfig, devices } from "@playwright/test";

// The real API with real services, and the production website. It uses real quotas: about 70 Transitous
// requests and 20 to 40 Gemini calls. Run from frontend: npx playwright test --config e2e-live/live.config.ts
export default defineConfig({
  testDir: ".",
  workers: 1,
  timeout: 30 * 60_000,
  outputDir: "../test-results/live",
  use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:3000", video: "on" },
  webServer: [
    {
      command: ".venv\\Scripts\\python -m uvicorn city_planner.api:app --port 8000",
      cwd: "../..",
      url: "http://localhost:8000/health",
      env: { FRONTEND_ORIGIN: "http://localhost:3000", LANGGRAPH_STRICT_MSGPACK: "true", PYTHONIOENCODING: "utf-8" },
      reuseExistingServer: false,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run build && npm run start -- -p 3000",
      cwd: "..",
      url: "http://localhost:3000",
      env: { NEXT_PUBLIC_API_URL: "http://localhost:8000" },
      reuseExistingServer: false,
      timeout: 300_000,
    },
  ],
});
