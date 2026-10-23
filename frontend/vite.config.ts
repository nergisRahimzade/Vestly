import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The UI talks to the FastAPI backend through /api, so the browser never needs CORS and never sends a role.
const backend = process.env.VESTLY_API ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: backend, changeOrigin: true, rewrite: (path: string) => path.replace(/^\/api/, "") },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: 4173, strictPort: true, proxy },
});
