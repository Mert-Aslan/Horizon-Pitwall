/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the API to a locally running backend (python -m backend).
const backend = process.env.PITWALL_BACKEND ?? "http://127.0.0.1:8080";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // build:profile -> React profiling build so <Profiler> reports commit times.
  resolve: mode === "profile" ? { alias: { "react-dom/client": "react-dom/profiling" } } : {},
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api/v1/stream": { target: backend.replace(/^http/, "ws"), ws: true },
      "/api": { target: backend },
    },
  },
  build: { outDir: "dist", sourcemap: false, assetsInlineLimit: 0 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
}));
