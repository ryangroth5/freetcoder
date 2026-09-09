/// <reference types="vitest" />
import { createReadStream, existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { defineConfig } from 'vitest/config'
import type { Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'


/** Serves the default-extension resources during `vite dev`.
 *
 * Those packages register their files with
 * `new URL('./resources/x.json', import.meta.url)`. Once prebundled -- which
 * they must be, or the module graph splits and no grammar ever registers --
 * import.meta.url points at node_modules/.vite/deps, so every grammar, theme
 * and language-configuration 404s and nothing is coloured.
 *
 * Requests all land on one flat path, so we serve them back out of the real
 * packages. Dev only: a production build resolves these correctly on its own.
 *
 * `package.json` and `package.nls.json` exist in every package, so a basename
 * lookup can pick the wrong one. Those hold display labels only -- never a
 * grammar or a theme -- so the worst case is an untranslated label.
 */
function extensionResources(): Plugin {
  const require = createRequire(import.meta.url)
  const PACKAGES = [
    '@codingame/monaco-vscode-theme-defaults-default-extension',
    '@codingame/monaco-vscode-python-default-extension',
    '@codingame/monaco-vscode-typescript-basics-default-extension',
    '@codingame/monaco-vscode-javascript-default-extension',
  ]
  const PREFIX = '/node_modules/.vite/deps/resources/'

  const dirs = PACKAGES.flatMap((name) => {
    try {
      return [join(dirname(require.resolve(`${name}/package.json`)), 'resources')]
    } catch {
      return []   // not installed; the others still work
    }
  })

  return {
    name: 'freetcoder:extension-resources',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url?.split('?')[0] ?? ''
        if (!url.startsWith(PREFIX)) return next()
        const name = decodeURIComponent(url.slice(PREFIX.length))
        if (name.includes('/') || name.includes('..')) return next()
        const found = dirs.map((d) => join(d, name)).find(existsSync)
        if (found === undefined) return next()
        res.setHeader(
          'Content-Type',
          name.endsWith('.svg') ? 'image/svg+xml' : 'application/json',
        )
        createReadStream(found).pipe(res)
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), extensionResources()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // The backend owns /api and /lsp; the dev server only serves the SPA.
    proxy: {
      '/api': { target: 'http://dev:8080', changeOrigin: true },
      '/lsp': { target: 'ws://dev:8080', ws: true },
    },
  },
  optimizeDeps: {
    // Do NOT exclude the default-extension packages.
    //
    // They were excluded to dodge an esbuild OOM, but that OOM was esbuild
    // being SIGKILLed under memory pressure during prebundling generally --
    // these four hold ~520KB of grammars between them, far too little to be
    // the cause. The exclusion split the module graph: excluded packages got
    // raw source while monaco-vscode-api was prebundled, so there were TWO
    // instances of its lifecycle module. The barrier the extensions awaited
    // was never the one startup() opened, every whenReady() hung, and no
    // grammar was ever registered. That is why nothing was ever coloured.
    entries: ['src/main.tsx'],
    // vscode-textmate ships a UMD bundle whose header assigns
    // `exports.vscodetextmate`. Reached indirectly it is left unconverted and
    // throws "Cannot set properties of undefined (setting 'vscodetextmate')";
    // naming it here makes esbuild do the CommonJS interop properly.
    include: ['vscode-textmate', 'vscode-oniguruma'],
  },
  // monaco-vscode-api code-splits its workers, which rollup refuses to emit as
  // IIFE (Vite's default). ES workers are required, not a preference.
  worker: { format: 'es' },
  // It ships large ESM chunks; keep the warning quiet rather than splitting a
  // vendor bundle we always need in full.
  build: {
    chunkSizeWarningLimit: 4000,
    sourcemap: false,
    // Rollup's default parallelism holds many large chunks in memory at once.
    // Docker Desktop's VM defaults to ~6 GB, which is not enough for it.
    rollupOptions: { maxParallelFileOps: 2 },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Only src/. Without this vitest also collects e2e/, whose Playwright
    // `test()` calls are a different runner and fail at import.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
