import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const HERE = import.meta.dirname;
import { defineConfig, type Plugin } from "vite";

const OUT_DIR = resolve(HERE, "../ckanext/c4w/public/c4w/dashboard");

/** Escribe BUILD.json junto al bundle: el helper `c4w_dashboard_asset` lo lee
 *  para el cache-busting (`?v=<builtAt>`), así un deploy nuevo nunca sirve el
 *  JS viejo desde la caché del navegador. */
function buildStamp(): Plugin {
  return {
    name: "c4w-build-stamp",
    closeBundle() {
      const pkg = JSON.parse(readFileSync(resolve(HERE, "package.json"), "utf8")) as {
        version: string;
      };
      mkdirSync(OUT_DIR, { recursive: true });
      writeFileSync(
        resolve(OUT_DIR, "BUILD.json"),
        JSON.stringify({ version: pkg.version, builtAt: new Date().toISOString() }, null, 2) + "\n",
      );
    },
  };
}

export default defineConfig({
  // CKAN sirve ckanext/c4w/public/ en la raíz del sitio, así que el bundle
  // vive en /c4w/dashboard/ tanto en dev como en producción.
  base: "/c4w/dashboard/",
  plugins: [buildStamp()],
  publicDir: "public",
  optimizeDeps: {
    exclude: ["maplibre-gl"],
  },
  build: {
    outDir: OUT_DIR,
    emptyOutDir: true,
    // public/ sólo contiene los datos de desarrollo (gitignored): no se copia.
    copyPublicDir: false,
    target: "es2020",
    cssCodeSplit: false,
    sourcemap: false,
    // MapLibre + Chart.js pesan ~1,1 MB minificados; es el tamaño esperado.
    chunkSizeWarningLimit: 1400,
    rollupOptions: {
      input: resolve(HERE, "src/main.ts"),
      output: {
        entryFileNames: "c4w-dashboard.js",
        chunkFileNames: "c4w-dashboard-[name].js",
        assetFileNames: "c4w-dashboard[extname]",
      },
    },
  },
  worker: {
    format: "es",
    rollupOptions: {
      output: {
        entryFileNames: "c4w-dashboard-worker.js",
        chunkFileNames: "c4w-dashboard-worker-[name].js",
        assetFileNames: "c4w-dashboard-worker[extname]",
      },
    },
  },
});
