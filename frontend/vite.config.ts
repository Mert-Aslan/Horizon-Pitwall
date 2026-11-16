/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";

// Build identity: one id per build, compiled into the bundle (__UI_BUILD_ID__) and written
// to dist/build-manifest.json, which the backend reports in hello/build. A page running an
// older bundle than the one the server now serves can therefore tell the user to reload.
const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")) as { version: string };
const buildId = `${pkg.version}-${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}-${randomBytes(3).toString("hex")}`;

function buildManifest(): Plugin {
  return {
    name: "pitwall-build-manifest",
    apply: "build",
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "build-manifest.json",
        source: JSON.stringify({ ui_version: pkg.version, ui_build_id: buildId, built_at_utc: new Date().toISOString() }, null, 1),
      });
    },
  };
}

// Dev server proxies the API to a locally running backend (python -m backend).
const backend = process.env.PITWALL_BACKEND ?? "http://127.0.0.1:8080";

export default defineConfig(({ mode }) => ({
  plugins: [react(), buildManifest()],
  define: { __UI_BUILD_ID__: JSON.stringify(mode === "test" ? "test" : buildId) },
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
