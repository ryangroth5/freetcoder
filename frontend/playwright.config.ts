import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  // Serial: submissions execute real code in the shared sandbox, and the VM is
  // memory-constrained.
  workers: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    headless: true,
  },
})
