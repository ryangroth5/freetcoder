import { expect, test } from '@playwright/test'
import { gotoPicker, startLeetCodeSession } from './helpers'

/** Stub the settings API so tests never write to the shared backend. */
const STATE = {
  values: {
    llm_base_url: 'https://openrouter.ai/api/v1',
    llm_model: 'test-model',
    llm_timeout_s: 120,
    llm_max_retries: 3,
    library_url: '',
    check_statement_sufficiency: true,
    generation_attempts: 4,
    repair_rounds: 3,
    tool_call_budget: 6,
    tutor_tool_budget: 4,
    tutor_message_cap: 60,
  },
  sources: { llm_base_url: 'environment', generation_attempts: 'default' },
  persistent: true,
  db_path: '/data/freetcoder.db',
  has_key: true,
  key_hint: 'wxyz',
  configured: true,
}

async function stubSettings(page: import('@playwright/test').Page, over = {}) {
  const state = { ...STATE, ...over }
  await page.route('**/api/settings', (route) =>
    route.fulfill({ json: state }))
}

test.describe('settings', () => {
  test('the suite no longer reconfigures the backend', async ({ page, request }) => {
    // The regression guard for the bug that started this: a browser run used
    // to POST a key to the real server and leave it configured for good.
    await gotoPicker(page)
    const state = await (await request.get('/api/setup')).json()
    expect(state.configured, 'gotoPicker must not write to the backend')
      .toBe(false)
  })

  test('reachable from the picker, and Done goes back', async ({ page }) => {
    await stubSettings(page)
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Settings' }).click()

    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'This browser' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'This server' })).toBeVisible()

    await page.getByRole('button', { name: 'Done' }).click()
    await expect(page.getByRole('button', { name: 'LeetCode' })).toBeVisible()
  })

  test('returns to the problem you came from', async ({ page }) => {
    await stubSettings(page)
    await startLeetCodeSession(page)
    await page.getByRole('button', { name: 'Settings' }).click()
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()

    await page.getByRole('button', { name: 'Done' }).click()
    // Back on the same question, session intact.
    await expect(page.getByRole('heading', { name: /Two Sum/ })).toBeVisible()
  })

  test('the key is shown as a hint, never as a value', async ({ page }) => {
    await stubSettings(page)
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Settings' }).click()
    await expect(page.getByText(/ending wxyz/)).toBeVisible()
    await expect(page.getByText(/FREETCODER_LLM_API_KEY/)).toBeVisible()
  })

  test('an in-memory database says so rather than pretending', async ({ page }) => {
    await stubSettings(page, { persistent: false, db_path: '' })
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Settings' }).click()
    await expect(page.getByText(/held in memory only/i)).toBeVisible()
    await expect(page.getByText(/lost when the server restarts/i)).toBeVisible()
  })

  test('theme survives a reload', async ({ page }) => {
    await stubSettings(page)
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Settings' }).click()
    await page.getByLabel('Theme').selectOption('dark')
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  })
})
