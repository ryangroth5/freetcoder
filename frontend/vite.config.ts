/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
    // These carry large TextMate grammars. Prebundling them is what OOM'd
    // esbuild on this VM ("The service is no longer running: write EPIPE",
    // then a blank page); excluding them skips that step entirely.
    exclude: [
      '@codingame/monaco-vscode-python-default-extension',
      '@codingame/monaco-vscode-javascript-default-extension',
      '@codingame/monaco-vscode-typescript-basics-default-extension',
      '@codingame/monaco-vscode-theme-defaults-default-extension',
    ],
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
