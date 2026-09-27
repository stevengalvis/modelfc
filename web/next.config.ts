import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  agentRules: false,
  // Demo and integration responses are shared with the Python backend.
  turbopack: { root: path.resolve(__dirname, "..") },
};

export default nextConfig;
