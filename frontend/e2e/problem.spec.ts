import { expect, test } from '@playwright/test'
import {
  expectEditorContains,
  startLeetCodeSession,
  switchLanguage,
  typeSolution,
} from './helpers'

/**
 * The full solve loop against a recorded question (FREETCODER_FAKE_LLM=1).
 *
 * The editor assertions here are the ones no unit test can make: that Monaco
 * really mounted, that the language client connected over the WebSocket, and
 * that pyright's diagnostics reach the screen.
 */

test.describe('problem view', () => {
  test('renders the statement with badges, examples and constraints', async ({ page }) => {
    await startLeetCodeSession(page)

    await expect(page.getByText('easy', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: /Topics/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Hint/ })).toBeVisible()
    await expect(page.getByText('Example 1:')).toBeVisible()
    await expect(page.getByText('Constraints:')).toBeVisible()
  })

  test('hint is collapsed until asked for', async ({ page }) => {
    await startLeetCodeSession(page)
    await expect(page.getByText(/dictionary answers that/)).toBeHidden()
    await page.getByRole('button', { name: /Hint/ }).click()
    await expect(page.getByText(/dictionary answers that/)).toBeVisible()
  })

  test('solution tab is locked until submit', async ({ page }) => {
    await startLeetCodeSession(page)
    await expect(page.getByRole('button', { name: /Solution/ })).toBeDisabled()
  })

  test('monaco mounts and shows the scaffold', async ({ page }) => {
    await startLeetCodeSession(page)
    const editor = page.locator('.monaco-editor').first()
    await expect(editor).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('two_sum').first()).toBeVisible()
  })

  test('pyright reports a type error in the editor', async ({ page }) => {
    await startLeetCodeSession(page)
    // Reference a name that does not exist -- pyright must object.
    await typeSolution(page, 'def two_sum(nums, target): return undefined_name_xyz')

    // A squiggle is the visible proof the language server is connected.
    const squiggle = page.locator(
      '.monaco-editor .squiggly-error, .monaco-editor .squiggly-warning',
    )
    await expect(squiggle.first()).toBeVisible({ timeout: 60_000 })
  })

  test('cursor position is tracked in the status bar', async ({ page }) => {
    await startLeetCodeSession(page)
    await expect(page.getByText(/Ln \d+, Col \d+/)).toBeVisible()
  })

  test('run passes the visible cases', async ({ page }) => {
    await startLeetCodeSession(page)
    await typeSolution(
      page,
      'def two_sum(nums, target): return next(([i, j] ' +
      'for i in range(len(nums)) for j in range(i + 1, len(nums)) ' +
      'if nums[i] + nums[j] == target), [])',
    )

    await page.getByRole('button', { name: '▶ Run' }).click()
    await expect(page.getByText('Accepted')).toBeVisible({ timeout: 60_000 })
  })

  test('a wrong submission shows a counterexample with named arguments',
    async ({ page }) => {
      await startLeetCodeSession(page)
      await typeSolution(page, 'def two_sum(nums, target): return [0, 0]')

      await page.getByRole('button', { name: 'Submit' }).click()
      await expect(page.getByText('Wrong Answer')).toBeVisible({ timeout: 90_000 })
      await expect(page.getByText('First failing case')).toBeVisible()
      // Arguments are shown individually, not as one blob.
      await expect(page.getByText('nums =').first()).toBeVisible()
      await expect(page.getByText('target =').first()).toBeVisible()
    })
})

test.describe('regressions from user testing', () => {
  test('print() output is shown, not a 500', async ({ page }) => {
    // A submission containing print() used to crash the server: the harness
    // shared stdout with the candidate's own output.
    await startLeetCodeSession(page)
    await typeSolution(page, 'def two_sum(nums, target): print(nums); return [0, 1]')

    await page.getByRole('button', { name: '\u25b6 Run' }).click()
    // Two Stdout boxes render (selected case, and the first-failure block);
    // the pane auto-selects the failing Case 2, whose input is [3, 2, 4].
    await expect(page.getByText('Stdout').first()).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('[3, 2, 4]').first()).toBeVisible()
  })

  test('printing a JSON object does not misgrade the attempt', async ({ page }) => {
    await startLeetCodeSession(page)
    await typeSolution(
      page,
      'def two_sum(nums, target): print(\'{"ok": true, "value": [9, 9]}\'); ' +
      'return next(([i, j] for i in range(len(nums)) ' +
      'for j in range(i + 1, len(nums)) if nums[i] + nums[j] == target), [])',
    )
    await page.getByRole('button', { name: '\u25b6 Run' }).click()
    await expect(page.getByText('Accepted')).toBeVisible({ timeout: 60_000 })
  })

  test('hover tooltips stay inside the viewport', async ({ page }) => {
    // The signature-help popup used to render inside the narrow editor pane and
    // run off the right of the screen.
    await startLeetCodeSession(page)
    await typeSolution(page, 'def two_sum(nums, target): print(nums)')

    // Put the caret inside print(...) to summon signature help.
    await page.keyboard.press('ArrowLeft')
    await page.waitForTimeout(1500)

    const viewport = page.viewportSize()!
    const widgets = page.locator(
      '.monaco-editor .parameter-hints-widget, .monaco-editor .monaco-hover, ' +
      '.parameter-hints-widget, .monaco-hover',
    )
    const count = await widgets.count()
    for (let i = 0; i < count; i++) {
      const box = await widgets.nth(i).boundingBox()
      if (!box || box.width === 0) continue
      expect(box.x).toBeGreaterThanOrEqual(-1)
      expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1)
    }
  })
})

test.describe('multi-language', () => {
  test('offers every language the format supports', async ({ page }) => {
    await startLeetCodeSession(page)
    const select = page.getByLabel('Language')
    await expect(select).toBeVisible()
    await expect(select.locator('option')).toHaveText(
      ['python', 'javascript', 'typescript'])
  })

  test('switching language swaps the scaffold and preserves work',
    async ({ page }) => {
      await startLeetCodeSession(page)
      await typeSolution(page, 'def two_sum(nums, target): return [7, 7]')

      // The JavaScript scaffold, not the Python edit.
      await switchLanguage(page, 'javascript')

      // Going back restores what was typed, rather than resetting it.
      await page.getByLabel('Language').selectOption('python')
      await expectEditorContains(page, 'return [7, 7]')
    })

  test('solves the question in javascript', async ({ page }) => {
    await startLeetCodeSession(page)
    await switchLanguage(page, 'javascript')
    await typeSolution(
      page,
      'function two_sum(nums, target) { for (let i = 0; i < nums.length; i++) ' +
      '{ for (let j = i + 1; j < nums.length; j++) ' +
      '{ if (nums[i] + nums[j] === target) return [i, j]; } } return []; } ' +
      'module.exports = { two_sum };',
    )
    await page.getByRole('button', { name: '\u25b6 Run' }).click()
    await expect(page.getByText('Accepted')).toBeVisible({ timeout: 90_000 })
  })

  test('console.log output is captured, not lost', async ({ page }) => {
    // The print bug, in the language where the naive harness would recur.
    await startLeetCodeSession(page)
    await switchLanguage(page, 'javascript')
    await typeSolution(
      page,
      'function two_sum(nums, target) { console.log(JSON.stringify(nums)); ' +
      'return [0, 1]; } module.exports = { two_sum };',
    )
    await page.getByRole('button', { name: '\u25b6 Run' }).click()
    await expect(page.getByText('Stdout').first()).toBeVisible({ timeout: 90_000 })
  })

  test('a typescript type error is reported as a compile error', async ({ page }) => {
    await startLeetCodeSession(page)
    await switchLanguage(page, 'typescript')
    await typeSolution(
      page,
      'export function two_sum(nums: number[], target: number): number[] ' +
      '{ return "nope"; }',
    )
    await page.getByRole('button', { name: '\u25b6 Run' }).click()
    await expect(page.getByText('Compile Error')).toBeVisible({ timeout: 90_000 })
    await expect(page.getByText(/not assignable/)).toBeVisible()
  })
})

test.describe('editable test cases', () => {
  test('shows the provided examples as editable fields', async ({ page }) => {
    await startLeetCodeSession(page)
    await page.getByRole('button', { name: 'Testcase' }).click()
    await expect(page.getByLabel('Case 1 nums')).toHaveValue('[2,7,11,15]')
    await expect(page.getByLabel('Case 1 target')).toHaveValue('9')
  })

  test('editing an example changes what actually runs', async ({ page }) => {
    await startLeetCodeSession(page)
    await typeSolution(page, 'def two_sum(nums, target): return [0, 1]')
    await page.getByRole('button', { name: 'Testcase' }).click()

    // Leave one case so the reported failure is unambiguously the edited one.
    await page.getByTitle('Delete').last().click()
    await page.getByLabel('Case 1 nums').fill('[5, 5]')
    await page.getByLabel('Case 1 target').fill('10')
    await page.getByLabel('Case 1 expected').fill('[9, 9]')

    await page.getByRole('button', { name: '▶ Run' }).click()
    await expect(page.getByText('First failing case')).toBeVisible({ timeout: 60_000 })
    // The edited input reached the runner, not the original example.
    await expect(page.getByText('[5,5]').first()).toBeVisible()
    await expect(page.getByText('[2,7,11,15]')).toHaveCount(1)  // statement only
  })

  test('a case with no expected value shows output without a verdict',
    async ({ page }) => {
      await startLeetCodeSession(page)
      await typeSolution(page, 'def two_sum(nums, target): return [0, 1]')
      await page.getByRole('button', { name: 'Testcase' }).click()

      // Remove the second example so only the unjudged case remains judged-free.
      await page.getByTitle('Delete').last().click()
      await page.getByLabel('Check against an expected value').uncheck()

      await page.getByRole('button', { name: '▶ Run' }).click()
      await expect(page.getByText(/not marked right or wrong/))
        .toBeVisible({ timeout: 60_000 })
    })

  test('cases can be added and removed', async ({ page }) => {
    await startLeetCodeSession(page)
    await page.getByRole('button', { name: 'Testcase' }).click()
    await expect(page.getByLabel(/^Case \d+ nums$/)).toHaveCount(2)

    await page.getByRole('button', { name: '+ Add case' }).click()
    await page.getByRole('button', { name: '+ Add case' }).click()
    await expect(page.getByLabel(/^Case \d+ nums$/)).toHaveCount(4)

    await page.getByTitle('Delete').last().click()
    await expect(page.getByLabel(/^Case \d+ nums$/)).toHaveCount(3)
  })

  test('an unparseable field blocks Run and is flagged', async ({ page }) => {
    await startLeetCodeSession(page)
    await page.getByRole('button', { name: 'Testcase' }).click()
    await page.getByLabel('Case 1 nums').fill('[1, 2')

    await expect(page.getByText('not valid JSON').first()).toBeVisible()
    await expect(page.getByRole('button', { name: '▶ Run' })).toBeDisabled()
  })

  test('resetting restores the original examples', async ({ page }) => {
    await startLeetCodeSession(page)
    await page.getByRole('button', { name: 'Testcase' }).click()
    await page.getByLabel('Case 1 nums').fill('[9, 9]')
    await page.getByRole('button', { name: /Reset to the original examples/ }).click()
    await expect(page.getByLabel('Case 1 nums')).toHaveValue('[2,7,11,15]')
  })
})

test.describe('question library', () => {
  test('a solved question can be saved and reopened', async ({ page }) => {
    await startLeetCodeSession(page)
    await page.getByRole('button', { name: '☆ Save' }).click()
    await expect(page.getByText(/Saved ".*" to the library/))
      .toBeVisible({ timeout: 60_000 })

    // It is now reachable without generating anything.
    await page.goto('/')
    await page.getByRole('button', { name: 'From the library' }).click()
    const saved = page.getByRole('button', { name: /Two Sum/ })
    await expect(saved.first()).toBeVisible({ timeout: 30_000 })

    await saved.first().click()
    await expect(page.getByRole('heading', { name: /Two Sum/ }))
      .toBeVisible({ timeout: 60_000 })
  })

  test('submitting reports a ratio against the reference', async ({ page }) => {
    await startLeetCodeSession(page)
    await typeSolution(
      page,
      'def two_sum(nums, target): return next(([i, j] ' +
      'for i in range(len(nums)) for j in range(i + 1, len(nums)) ' +
      'if nums[i] + nums[j] == target), [])',
    )
    await page.getByRole('button', { name: 'Submit' }).click()
    await expect(page.getByText(/× the reference/)).toBeVisible({ timeout: 90_000 })
  })
})

test.describe('fixes from use', () => {
  test('next question produces another question, not the results screen',
    async ({ page }) => {
      // In a single-question format the nav was hidden entirely and next()
      // went to results, so there was no way to keep practising.
      await startLeetCodeSession(page)
      const next = page.getByTitle(/another question|Next question/)
      await expect(next).toBeVisible()
      await expect(next).toBeEnabled()

      await next.click()
      await expect(page.getByRole('heading', { name: /Two Sum/ }))
        .toBeVisible({ timeout: 90_000 })
      // Still a problem view, not the results screen.
      await expect(page.getByRole('button', { name: 'Submit' })).toBeVisible()
    })

  test('the theme toggle changes the panels and persists', async ({ page }) => {
    await startLeetCodeSession(page)
    const before = await page.locator('html').getAttribute('data-theme')

    await page.getByLabel('Toggle theme').click()
    const after = await page.locator('html').getAttribute('data-theme')
    expect(after).not.toBe(before)

    const bg = await page.locator('body')
      .evaluate((el) => getComputedStyle(el).backgroundColor)
    expect(bg).toBe(after === 'dark' ? 'rgb(30, 30, 30)' : 'rgb(255, 255, 255)')

    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-theme', after!)
  })
})
