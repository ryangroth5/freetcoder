/**
 * Light/dark theming for both the panels and the editor.
 *
 * One source of truth: the panels read CSS variables and Monaco reads a
 * registered theme, and both are switched from here so they can never disagree.
 */

import * as monaco from 'monaco-editor'
import { create } from 'zustand'

export type ThemeChoice = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'freetcoder.theme'

function systemPrefersDark(): boolean {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? true
}

export function resolveTheme(choice: ThemeChoice): 'light' | 'dark' {
  if (choice === 'system') return systemPrefersDark() ? 'dark' : 'light'
  return choice
}

//: Monaco's services do not exist until the editor wrapper has started. Calling
//: defineTheme or setTheme before that leaves monaco-vscode-api in a state where
//: the editor never renders at all -- silently, with no error. So the panels are
//: themed immediately and the editor is themed once it announces itself.
let monacoReady = false

function applyMonacoTheme(resolved: 'light' | 'dark'): void {
  if (!monacoReady) return
  // Switch by name. The VS Code theme extensions own the token colours, and
  // there used to be a defineTheme call here that both discarded them and
  // threw -- `defineTheme` does not exist in the wrapper's 'extended' mode, so
  // it raised before setTheme ran and EditorPane swallowed it. The result was
  // a light/dark toggle that never re-themed the editor at all.
  monaco.editor.setTheme(
    resolved === 'dark' ? 'Default Dark Modern' : 'Default Light Modern',
  )
}

/** Called by EditorPane once the wrapper has initialised Monaco's services. */
export function monacoDidLoad(): void {
  monacoReady = true
  applyMonacoTheme(useTheme.getState().resolved)
}

function apply(choice: ThemeChoice): void {
  const resolved = resolveTheme(choice)
  document.documentElement.setAttribute('data-theme', resolved)
  applyMonacoTheme(resolved)
}

function stored(): ThemeChoice {
  const raw = localStorage.getItem(STORAGE_KEY)
  return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'system'
}

interface ThemeState {
  choice: ThemeChoice
  resolved: 'light' | 'dark'
  setChoice: (choice: ThemeChoice) => void
  toggle: () => void
}

export const useTheme = create<ThemeState>((set, get) => ({
  choice: stored(),
  resolved: resolveTheme(stored()),

  setChoice: (choice) => {
    localStorage.setItem(STORAGE_KEY, choice)
    apply(choice)
    set({ choice, resolved: resolveTheme(choice) })
  },

  // Toggling from 'system' commits to the opposite of what is showing, which
  // is what someone reaching for the control actually wants.
  toggle: () => get().setChoice(get().resolved === 'dark' ? 'light' : 'dark'),
}))

/** Applies the stored choice, and follows the OS while the choice is 'system'. */
export function initTheme(): void {
  apply(useTheme.getState().choice)
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.(
    'change',
    () => {
      const { choice, setChoice } = useTheme.getState()
      if (choice === 'system') setChoice('system')
    },
  )
}
