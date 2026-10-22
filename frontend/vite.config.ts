import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Vite dev server proxies /api to the FastAPI process, so the
// same-origin assumption of production (one container, one URL) holds locally too.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.VITE_API_URL ?? "http://localhost:8000", changeOrigin: true } },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 5000, // plotly.js is large; it is split into its own chunk below
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes("plotly.js")) return "plotly";
        },
      },
    },
  },
});
