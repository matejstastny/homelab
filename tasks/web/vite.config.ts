import { fileURLToPath } from "node:url";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const port = Number(process.env.RPI_WEB_PORT) || undefined;
const api = process.env.RPI_API_URL;

export default defineConfig({
    plugins: [react(), tailwind()],
    resolve: {
        alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) }
    },
    build: {
        outDir: "../server/dist",
        emptyOutDir: true,
        assetsDir: "_astro"
    },
    server: {
        ...(port ? { port } : {}),
        ...(api ? { proxy: { "/api": api } } : {})
    }
});
