import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Endpoints the SPA calls — proxied to the FastAPI backend during `npm run dev`.
// In the built app (served by FastAPI itself at :8000/) these are same-origin.
const API_PATHS = [
  "/health",
  "/enquiries",
  "/enquiry",
  "/process-email",
  "/supplier-quote-requests",
  "/suppliers",
];

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      API_PATHS.map((p) => [p, { target: "http://127.0.0.1:8000", changeOrigin: true }]),
    ),
  },
});
