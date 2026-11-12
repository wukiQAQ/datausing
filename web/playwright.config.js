import { defineConfig } from "@playwright/test";

// 运行前启动隔离数据目录的 FastAPI(8090) 与 Vite(5173)，避免写入真实业务库。
export default defineConfig({
  testDir: "./test/e2e",
  outputDir: "./.qa/results",
  workers: 1,
  timeout: 30000,
  reporter: "list",
  use: {
    baseURL: process.env.FLOWMASK_TEST_URL || "http://127.0.0.1:5173/app/",
    viewport: { width: 1440, height: 1080 },
    headless: true,
    channel: process.env.FLOWMASK_BROWSER_CHANNEL || "msedge",
    screenshot: "only-on-failure",
  },
});
