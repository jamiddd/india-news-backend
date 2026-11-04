import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Dev server data modes (see the "Data" note in src/api.js):
//   npm run dev                      -> pages call /api/v1/*, proxied to API_PROXY_TARGET
//                                       (default http://127.0.0.1:8000, a local backend)
//   VITE_USE_SAMPLE=1 npm run dev    -> fully offline: pages read /api/sample, which this
//                                       plugin serves from prototype/data/sample.json
const SAMPLE = fileURLToPath(new URL("../prototype/data/sample.json", import.meta.url));

function sampleData() {
  return {
    name: "oin-sample-data",
    // Added directly (not via a returned hook) so it runs before Vite's /api proxy.
    configureServer(server) {
      server.middlewares.use("/api/sample", async (req, res) => {
        try {
          const body = await readFile(SAMPLE);
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.setHeader("Cache-Control", "no-store");
          res.end(body);
        } catch (e) {
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify({ detail: "Couldn't read " + SAMPLE + ": " + e.message }));
        }
      });
    },
  };
}

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    // Production assets live under /app-assets/ (app/web_spa.py). The dev server serves the
    // app from / so the router's clean URLs (/story/12, /timelines, ...) resolve.
    base: command === "build" ? "/app-assets/" : "/",
    plugins: [react(), sampleData()],
    build: { outDir: "dist" },
    server: { proxy: { "/api": env.API_PROXY_TARGET || "http://127.0.0.1:8000" } },
    test: { environment: "node", include: ["test/**/*.test.js"] },
  };
});
