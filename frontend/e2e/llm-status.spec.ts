import { expect, test } from '@playwright/test'
import { gotoPicker } from './helpers'

const BASE = {
  values: { llm_base_url: 'https://openrouter.ai/api/v1', llm_model: 'test-model' },
  sources: {},
  persistent: true,
  db_path: '/data/freetcoder.db',
  has_key: true,
  key_hint: 'wxyz',
  key_from_session: false,
  configured: true,
}

async function stub(page: import('@playwright/test').Page, over: object) {
  await page.route('**/api/settings', (route) =>
    route.fulfill({ json: { ...BASE, ...over } }))
}

test.describe('LLM status', () => {
  // A key can be present and valid while every question comes from a fixture,
  // because FREETCODER_FAKE_LLM wins in build_client. `configured` and
  // `has_key` are both true in that case, which is what made it invisible.
  test('offline is not reported as live', async ({ page }) => {
    await stub(page, {
      llm_status: 'offline',
      llm_reason: 'FREETCODER_FAKE_LLM is set, so questions come from recorded fixtures.',
    })
    await gotoPicker(page)
    await expect(page.locator('[data-llm-status="offline"]').first()).toBeVisible()
    await expect(page.getByText('offline').first()).toBeVisible()
  })

  test('live is reported as live', async ({ page }) => {
    await stub(page, { llm_status: 'live', llm_reason: 'Using test-model.' })
    await gotoPicker(page)
    await expect(page.locator('[data-llm-status="live"]').first()).toBeVisible()
    await expect(page.getByText('LLM live').first()).toBeVisible()
  })

  test('a missing key is distinguished from offline', async ({ page }) => {
    await stub(page, {
      llm_status: 'unconfigured', has_key: false, key_hint: '', configured: false,
      llm_reason: 'No API key.',
    })
    await gotoPicker(page)
    await expect(page.locator('[data-llm-status="unconfigured"]').first())
      .toBeVisible()
    await expect(page.getByText('no key').first()).toBeVisible()
  })

  test('the settings page explains the reason', async ({ page }) => {
    await stub(page, {
      llm_status: 'offline',
      llm_reason: 'FREETCODER_FAKE_LLM is set, so questions come from recorded fixtures.',
    })
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Settings' }).click()
    await expect(page.getByText(/Offline — recorded questions/)).toBeVisible()
    await expect(page.getByText(/FREETCODER_FAKE_LLM is set/)).toBeVisible()
    // The key field accepts an override rather than being read-only.
    await expect(page.getByLabel('API key')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Use this key' })).toBeDisabled()
  })
})
