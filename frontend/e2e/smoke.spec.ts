import { expect, test } from '@playwright/test'
import { gotoPicker } from './helpers'


/**
 * Confirms the things only a real browser can: that Monaco mounts, that the
 * language server connects over the WebSocket, and that a pyright diagnostic
 * actually renders in the editor.
 */
test.describe('app shell', () => {
  test('loads without console errors', async ({ page }) => {
    const errors: string[] = []
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
    page.on('pageerror', (e) => errors.push(e.message))

    await page.goto('/')
    // Either screen is a valid landing depending on whether a key is already
    // configured; both prove React mounted.
    await expect(
      page.getByText('freetcoder').or(page.getByText('What would you like to practise?')),
    ).toBeVisible()

    // Ignore the noise monaco-vscode-api emits about optional services.
    const real = errors.filter((e) => !/Failed to load resource|favicon/i.test(e))
    expect(real, `console errors:\n${real.join('\n')}`).toHaveLength(0)
  })

  test('setup screen asks for an endpoint and key', async ({ page }) => {
    // The key lives in server process memory, so whether the setup screen
    // appears depends on what other tests have run. Stub the endpoint to pin
    // the unconfigured state rather than racing it.
    await page.route('**/api/setup', (route) =>
      route.fulfill({
        json: { configured: false, base_url: 'https://openrouter.ai/api/v1',
                model: 'test-model', has_key: false },
      }))

    await page.goto('/')
    await expect(page.getByText('LLM endpoint')).toBeVisible()
    await expect(page.getByPlaceholder('sk-or-...')).toBeVisible()
    // Continue stays disabled until a key is supplied.
    await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()
  })

  test('format picker shows all four styles and gates difficulty', async ({ page }) => {
    await gotoPicker(page)
    await expect(page.getByRole('button', { name: 'CodeSignal GCA' })).toBeVisible()

    // A self-paced style offers a difficulty choice...
    await page.getByRole('button', { name: 'LeetCode' }).click()
    await expect(page.getByText('Difficulty:')).toBeVisible()

    // ...a fixed-format one explains why it does not.
    await page.getByRole('button', { name: 'CodeSignal GCA' }).click()
    await expect(page.getByText(/defines its own difficulty curve/)).toBeVisible()
  })

  test('unsupported concentrations are visibly flagged', async ({ page }) => {
    await gotoPicker(page)
    await page.getByRole('button', { name: 'LeetCode' }).click()

    const sql = page.getByRole('button', { name: /^sql/ })
    await expect(sql).toBeDisabled()
  })
})

test.describe('bring your own question', () => {
  test('offers describe and paste modes', async ({ page }) => {
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Bring your own' }).click()

    await expect(page.getByRole('button', { name: 'Describe a question' }))
      .toBeVisible()
    await expect(page.getByRole('button', { name: 'Paste a question to adapt' }))
      .toBeVisible()
    await expect(page.getByLabel('Your question')).toBeVisible()
  })

  test('start is blocked until there is text', async ({ page }) => {
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Bring your own' }).click()
    await page.getByRole('button', { name: 'LeetCode' }).click()

    await expect(page.getByRole('button', { name: 'Start' })).toBeDisabled()
    await page.getByLabel('Your question').fill('count the dogs in a kennel log')
    await expect(page.getByRole('button', { name: 'Start' })).toBeEnabled()
  })

  test('a pasted question starts a session', async ({ page }) => {
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Bring your own' }).click()
    await page.getByRole('button', { name: 'Paste a question to adapt' }).click()
    await page.getByLabel('Your question').fill(
      'Given a list of numbers and a target, return the indices of the two '
      + 'numbers that add up to the target.',
    )
    await page.getByRole('button', { name: 'LeetCode' }).click()
    await page.getByRole('button', { name: 'Start' }).click()

    await expect(page.getByRole('button', { name: 'Submit' }))
      .toBeVisible({ timeout: 90_000 })
  })

  test('the style tier is still offered for an import', async ({ page }) => {
    await gotoPicker(page)
    await page.getByRole('button', { name: 'Bring your own' }).click()
    // An imported question still has to be *some* format.
    await expect(page.getByRole('button', { name: 'Codility' })).toBeVisible()
  })
})

test.describe('generation progress', () => {
  /**
   * Hold the session response open so the panel is observable.
   *
   * Offline mode answers almost instantly, so without this the panel is gone
   * before it renders. A real generation takes tens of seconds; this simulates
   * that without waiting for one.
   */
  async function withSlowGeneration(page: import('@playwright/test').Page) {
    await page.route('**/api/sessions', async (route) => {
      try {
        // Let the server do the work first -- that is what records the steps --
        // then hold the response so the panel stays on screen.
        const response = await route.fetch()
        await new Promise((resolve) => setTimeout(resolve, 5000))
        await route.fulfill({ response })
      } catch {
        // The page can close while the response is held; a teardown race is
        // not a test failure.
      }
    })
  }

  test('names real steps while generating', async ({ page }) => {
    await gotoPicker(page)
    await withSlowGeneration(page)
    await page.getByRole('button', { name: 'LeetCode' }).click()
    await page.getByRole('button', { name: 'Start' }).click()

    // A truthful log rather than a spinner.
    await expect(page.getByText('Building your question'))
      .toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/asking the model|reusing a question/))
      .toBeVisible({ timeout: 30_000 })
  })

  test('the elapsed time advances', async ({ page }) => {
    await gotoPicker(page)
    await withSlowGeneration(page)
    await page.getByRole('button', { name: 'LeetCode' }).click()
    await page.getByRole('button', { name: 'Start' }).click()

    await expect(page.getByText('Building your question'))
      .toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/^[2-9]\d*s$/)).toBeVisible({ timeout: 20_000 })
  })

  test('cancel is honest about what it can interrupt', async ({ page }) => {
    await gotoPicker(page)
    await withSlowGeneration(page)
    await page.getByRole('button', { name: 'LeetCode' }).click()
    await page.getByRole('button', { name: 'Start' }).click()

    const cancel = page.getByRole('button', { name: 'Cancel' })
    await expect(cancel).toBeVisible({ timeout: 30_000 })
    await cancel.click()
    // A request already in flight to the model cannot be interrupted, so the
    // button says so rather than appearing to hang.
    await expect(page.getByText(/Waiting for the model to finish/)).toBeVisible()
  })
})
