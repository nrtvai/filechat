import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import packageJson from "./package.json";

const appVersion = packageJson.version;

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react()],
    define: {
      "import.meta.env.VITE_APP_VERSION": JSON.stringify(appVersion),
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": env.VITE_DEV_API_TARGET || "http://127.0.0.1:8000"
      }
    }
  };
});
