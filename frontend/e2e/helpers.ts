import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'

/**
 * Reach the format picker from a cold load.
 *
 * Stubs GET /api/setup rather than filling in a key. The helper used to type
 * 'test-key' and click Continue, which POSTs to the real backend and mutates
 * its in-memory settings -- so every browser run silently reconfigured the
 * developer's server, and it stayed that way until the container was recreated.
 * A test suite must not write to the thing it is testing.
 *
 * Stubbing also removes the race this helper used to work around: the setup
 * screen could be visible when we looked and gone a tick later, because
 * whether it appeared depended on what had already run.
 *
 * Session creation still works unconfigured: it only fails when the FakeLLM is
 * *exhausted*, and FREETCODER_FAKE_LLM=1 builds one that cycles.
 */
export async function gotoPicker(page: Page): Promise<void> {
  await page.route('**/api/setup', (route) =>
    route.fulfill({
      json: {
        configured: true,
        base_url: 'https://openrouter.ai/api/v1',
        model: 'test-model',
        has_key: true,
      },
    }))
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'LeetCode' }))
    .toBeVisible({ timeout: 30_000 })
}

export async function startLeetCodeSession(page: Page): Promise<void> {
  await gotoPicker(page)
  await page.getByRole('button', { name: 'LeetCode' }).click()
  await page.getByRole('button', { name: 'Start' }).click()
  await expect(page.getByRole('heading', { name: /Two Sum/ }))
    .toBeVisible({ timeout: 90_000 })
}

/**
 * Replace the editor contents.
 *
 * Monaco's textarea sits under the rendered view-lines, which intercept pointer
 * events, so click the view-lines. Code is passed as a single line because
 * Monaco auto-indents typed newlines and silently corrupts Python.
 */
export async function typeSolution(page: Page, oneLiner: string): Promise<void> {
  await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 60_000 })
  await page.locator('.monaco-editor .view-lines').first().click()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.press('Delete')
  await page.keyboard.type(oneLiner)

  // Confirm the text actually landed before the caller clicks Run. Under load
  // the keystrokes can still be settling, and Run would then execute the
  // previous buffer -- the source of intermittent failures.
  const probe = oneLiner.slice(0, 30)
  await expect.poll(() => editorText(page), { timeout: 30_000 }).toContain(probe)
}

/** The whole editor's visible text. Monaco splits tokens across spans, so
 *  getByText() on source fragments is unreliable; read the buffer instead. */
export async function editorText(page: Page): Promise<string> {
  const raw = await page.locator('.monaco-editor .view-lines').first().innerText()
  // Monaco renders spaces as U+00A0, so a plain substring check fails on text
  // that looks identical. Normalise before comparing.
  return raw.replace(/\u00a0/g, ' ')
}

export async function expectEditorContains(page: Page, needle: string): Promise<void> {
  await expect.poll(() => editorText(page), { timeout: 60_000 })
    .toContain(needle)
}

/** A token unique to each language's scaffold, used to wait for the swap. */
// What each language's scaffold must contain for the editor to have finished
// swapping. Deliberately the *function*, not an export convention: a generated
// scaffold is written by the model and the JavaScript harness accepts a
// script-style answer with no exports at all, so asserting on `module.exports`
// was asserting the shape of one fixture rather than that the swap happened.
const SCAFFOLD_MARKER: Record<string, string> = {
  python: 'def two_sum',
  javascript: 'function two_sum(nums, target) {',
  typescript: 'function two_sum(nums: number[], target: number)',
}

/**
 * Switch language and wait for the editor to actually show it.
 *
 * Swapping models is asynchronous; typing immediately after selectOption raced
 * it and edited the outgoing buffer.
 */
export async function switchLanguage(page: Page, language: string): Promise<void> {
  await page.getByLabel('Language').selectOption(language)
  await expectEditorContains(page, SCAFFOLD_MARKER[language])
}
