import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component and page tests. The pure helper tests in lib/*.test.mjs stay plain
// node scripts (see scripts/verify.sh); this runner is for anything that needs
// JSX, hooks or a DOM.
const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Mirrors the "@/*" path in tsconfig.json.
    alias: [{ find: /^@\//, replacement: `${root}/` }]
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["**/node_modules/**", "**/.next/**"],
    css: false,
    clearMocks: true
  }
});
