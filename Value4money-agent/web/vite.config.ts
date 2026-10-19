// The dev server, and the one place the API token exists.
//
// EventSource cannot set request headers. That is a browser fact, not a preference, so a token in
// the page cannot be attached to the event stream and a token in a query string would end up in
// logs. The proxy is the way out: Vite reads API_TOKEN from the environment and adds the header to
// every /api request it forwards, including the stream, so the browser never sees it and this
// codebase never stores it.
//
// The consequence is deliberate and worth stating: in development the frontend only works through
// this proxy. A production deployment would need the same header added at its own edge.

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const API_TARGET = "http://127.0.0.1:8000";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const token = env.API_TOKEN ?? "";

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: API_TARGET,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on("proxyReq", (proxyRequest) => {
              if (token) {
                proxyRequest.setHeader("X-API-Token", token);
              }
            });
          },
        },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
    },
  };
});
