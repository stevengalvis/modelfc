import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["lib/api/*.integration.ts"],
    testTimeout: 15000,
  },
});
