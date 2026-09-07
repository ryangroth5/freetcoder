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

let monacoThemesDefined = false

function defineMonacoThemes(): void {
  if (monacoThemesDefined) return
  monacoThemesDefined = true

  // Colours mirror the CSS variables in index.css so the editor and the panels
  // read as one surface rather than two.
  monaco.editor.defineTheme('freetcoder-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'comment', foreground: '6a9955', fontStyle: 'italic' },
      { token: 'keyword', foreground: 'c586c0' },
      { token: 'constant', foreground: '569cd6' },
      { token: 'string', foreground: 'ce9178' },
      { token: 'string.escape', foreground: 'd7ba7d' },
      { token: 'number', foreground: 'b5cea8' },
      { token: 'number.hex', foreground: 'b5cea8' },
      { token: 'type', foreground: '4ec9b0' },
      { token: 'type.identifier', foreground: '4ec9b0' },
      { token: 'entity.name.function', foreground: 'dcdcaa' },
      { token: 'operator', foreground: 'd4d4d4' },
    ],
    colors: { 'editor.background': '#1e1e1e' },
  })

  monaco.editor.defineTheme('freetcoder-light', {
    base: 'vs',
    inherit: true,
    rules: [
      { token: 'comment', foreground: '008000', fontStyle: 'italic' },
      { token: 'keyword', foreground: 'af00db' },
      { token: 'constant', foreground: '0000ff' },
      { token: 'string', foreground: 'a31515' },
      { token: 'string.escape', foreground: 'b06500' },
      { token: 'number', foreground: '098658' },
      { token: 'number.hex', foreground: '098658' },
      { token: 'type', foreground: '267f99' },
      { token: 'type.identifier', foreground: '267f99' },
      { token: 'entity.name.function', foreground: '795e26' },
      { token: 'operator', foreground: '000000' },
    ],
    colors: { 'editor.background': '#ffffff' },
  })
}

//: Monaco's services do not exist until the editor wrapper has started. Calling
//: defineTheme or setTheme before that leaves monaco-vscode-api in a state where
//: the editor never renders at all -- silently, with no error. So the panels are
//: themed immediately and the editor is themed once it announces itself.
let monacoReady = false

function applyMonacoTheme(resolved: 'light' | 'dark'): void {
  if (!monacoReady) return
  defineMonacoThemes()
  // The VSCode theme extensions own token colours; switching by name keeps
  // TextMate highlighting intact, which defineTheme would discard.
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
