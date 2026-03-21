import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const buildTarget = process.env.VITE_BUILD_TARGET || "tauri";
const isWebBuild = buildTarget === "web";
const isCapBuild = buildTarget === "capacitor";
const isRemoteBuild = isWebBuild || isCapBuild;

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __BUILD_TARGET__: JSON.stringify(buildTarget),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@shared/providers.json": path.resolve(
        __dirname,
        "../../src/openakita/llm/registries/providers.json",
      ),
    },
  },
  base: isWebBuild ? "/web/" : isCapBuild ? "./" : undefined,
  build: isRemoteBuild
    ? {
        outDir: "dist-web",
        rollupOptions: {
          external: [
            /^@tauri-apps\//,
          ],
        },
      }
    : undefined,
  server: {
    port: 5173,
    strictPort: true,
    ...(isWebBuild
      ? {
          proxy: {
            "/api": {
              target: "http://127.0.0.1:18900",
              changeOrigin: true,
            },
            "/ws": {
              target: "ws://127.0.0.1:18900",
              ws: true,
            },
          },
        }
      : {}),
  },
  clearScreen: false,
});

