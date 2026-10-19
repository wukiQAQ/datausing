import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 产物由 server 以 /app 前缀提供，因此 base 固定为 /app/
export default defineConfig({
  base: "/app/",
  plugins: [vue()],
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8090" },
  },
});
