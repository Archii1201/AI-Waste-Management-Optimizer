import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The /api proxy is development-only (`npm run dev` / `npm run preview`).
// Production builds call VITE_API_BASE_URL or same-origin /api/v1 — no proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
