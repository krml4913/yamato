/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev: /api は `yamato dashboard` (既定 127.0.0.1:8765) に proxy する。
// 別の port で起動しているときは YAMATO_API=http://127.0.0.1:<port> npm run dev
const api = process.env.YAMATO_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": api } },
  build: { outDir: "../src/yamato/web/dist", emptyOutDir: true },
  test: { globals: true, environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
});
