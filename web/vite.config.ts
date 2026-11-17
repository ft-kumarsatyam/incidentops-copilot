/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API = process.env.VITE_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // Served by FastAPI from the Python package; see `make web-build`.
    outDir: "../src/incidentops/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/incidents": { target: API, changeOrigin: true },
      "/health": API,
      "/ready": API,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
