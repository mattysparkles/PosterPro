import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react({ include: /\.[jt]sx?$/ })],
  // Next page modules are `.js` files containing JSX. Tell OXC to parse those
  // first-party route modules as JSX for real-page interaction tests.
  oxc: { lang: "jsx", jsx: { runtime: "automatic" }, include: /frontend\/pages\/.*\.js$/ },
  test: { environment: "jsdom", setupFiles: ["./test/setup.js"], globals: true, include: ["test/**/*.test.{js,jsx,ts,tsx}"] },
});
