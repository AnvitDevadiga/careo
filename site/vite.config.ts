import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// GitHub Pages serves the project site at https://anvitdevadiga.github.io/careo/
export default defineConfig({
  base: "/careo/",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
