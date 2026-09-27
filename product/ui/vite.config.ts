import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { svelteTesting } from "@testing-library/svelte/vite";
export default defineConfig({
  plugins: [svelte(), svelteTesting({ autoCleanup: false })],
  server: { host: "127.0.0.1" },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
});
