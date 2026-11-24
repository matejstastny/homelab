// @ts-check
import { defineConfig } from "astro/config";

const port = Number(process.env.RPI_WEB_PORT) || undefined;
const api = process.env.RPI_API_URL;

export default defineConfig({
    srcDir: ".",
    outDir: "../server/dist",
    build: { format: "directory", assets: "_astro" },
    devToolbar: { enabled: false },
    ...(port ? { server: { port } } : {}),
    ...(api ? { vite: { server: { proxy: { "/api": api, "/dl": api } } } } : {})
});
