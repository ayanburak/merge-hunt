/**
 * file: vite.config.ts
 * description: Build and dev server settings for the frontend.
 */

// strictPort stays on because the backend will allow exactly this origin once CORS is added.
// If the port is taken vite should stop, sliding to the next port would only give a confusing CORS error.

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
