import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development, /api goes to the Python backend (python -m custom_ui.server)
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": process.env.BACKEND_URL || "http://localhost:8001" } },
});
