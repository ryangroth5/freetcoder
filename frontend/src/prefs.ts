/** Browser-local preferences.
 *
 * Deliberately not on the server: these are personal, and two people sharing
 * one container should not overwrite each other's editor default. The theme
 * follows the same rule in theme.ts.
 */
import type { Language } from './api'

export const DEFAULT_LANGUAGE_KEY = 'freetcoder.defaultLanguage'

const LANGUAGES = ['python', 'javascript', 'typescript', 'go']

export function readDefaultLanguage(): Language {
  try {
    const stored = localStorage.getItem(DEFAULT_LANGUAGE_KEY)
    if (stored && LANGUAGES.includes(stored)) return stored as Language
  } catch {
    // Private browsing and blocked site data both throw; a default is fine.
  }
  return 'python'
}

export const INSPECTOR_OPEN_KEY = 'freetcoder.inspectorOpen'

/** Whether the generation inspector was left open. Closed unless chosen. */
export function readInspectorOpen(): boolean {
  try {
    return localStorage.getItem(INSPECTOR_OPEN_KEY) === '1'
  } catch {
    return false
  }
}

export function writeInspectorOpen(open: boolean): void {
  try {
    localStorage.setItem(INSPECTOR_OPEN_KEY, open ? '1' : '0')
  } catch {
    // Not being able to remember it is harmless.
  }
}
