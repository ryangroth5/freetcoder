import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'

/**
 * Reach the format picker from a cold load.
 *
 * The API key lives in server process memory, so whether the setup screen
 * appears depends on what has already run. Worse, it can be visible at the
 * moment we check and gone a tick later, once GET /api/setup resolves and the
 * app skips ahead — so a plain isVisible() check races and fill() throws.
 * Attempt the fill, tolerate it failing, and assert only on the destination.
 */
export async function gotoPicker(page: Page): Promise<void> {
  await page.goto('/')

  const key = page.getByPlaceholder('sk-or-...')
  const leetcode = page.getByRole('button', { name: 'LeetCode' })

  await Promise.race([
    key.waitFor({ state: 'visible', timeout: 30_000 }).catch(() => undefined),
    leetcode.waitFor({ state: 'visible', timeout: 30_000 }).catch(() => undefined),
  ])

  if (!(await leetcode.isVisible().catch(() => false))) {
    await key.fill('test-key', { timeout: 5_000 }).catch(() => undefined)
    await page.getByRole('button', { name: 'Continue' })
      .click({ timeout: 5_000 })
      .catch(() => undefined)
  }

  await expect(leetcode).toBeVisible({ timeout: 30_000 })
}

/** Start a LeetCode session and wait for the generated question to render. */
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
const SCAFFOLD_MARKER: Record<string, string> = {
  python: 'def two_sum',
  javascript: 'module.exports',
  typescript: 'export function',
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
