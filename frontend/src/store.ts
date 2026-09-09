/** Session state. Zustand: the shape is small and mostly flat. */

import { create } from 'zustand'
import { api } from './api'
import { readDefaultLanguage } from './prefs'
import type { CaseInput, Language, Question, RunReport, SessionInfo } from './api'
import { randomId } from './id'

/**
 * A case as held in the editor: every field is raw text so a half-typed value
 * is representable. Parsing happens on the way out, not on every keystroke.
 */
export interface EditableCase {
  id: string
  args: Record<string, string>
  expected: string
  /** Blank expected means "just show me the output", not "expect null". */
  assertExpected: boolean
  /** True when the expected value came from the intended solution, not you. */
  computed?: boolean
}

let caseSeq = 0
const nextId = () => `c${++caseSeq}`

export function caseFromTest(
  args: Record<string, unknown>, expected?: unknown, assertExpected = true,
): EditableCase {
  return {
    id: nextId(),
    args: Object.fromEntries(
      Object.entries(args).map(([k, v]) => [k, JSON.stringify(v)]),
    ),
    expected: assertExpected ? JSON.stringify(expected) : '',
    assertExpected,
  }
}

/** Which fields do not parse as JSON. Empty means the case is runnable. */
export function caseErrors(c: EditableCase): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const [key, raw] of Object.entries(c.args)) {
    if (raw.trim() === '') { errors[key] = 'required'; continue }
    try { JSON.parse(raw) } catch { errors[key] = 'not valid JSON' }
  }
  if (c.assertExpected && c.expected.trim() !== '') {
    try { JSON.parse(c.expected) } catch { errors.expected = 'not valid JSON' }
  }
  return errors
}

function toInput(c: EditableCase): CaseInput {
  const args = Object.fromEntries(
    Object.entries(c.args).map(([k, raw]) => [k, JSON.parse(raw)]),
  )
  const filled = c.assertExpected && c.expected.trim() !== ''
  return {
    args,
    expected: filled ? JSON.parse(c.expected) : undefined,
    assert_expected: filled,
  }
}

export type Screen = 'setup' | 'picker' | 'problem' | 'results' | 'settings'

interface State {
  screen: Screen
  returnTo: Screen
  session: SessionInfo | null
  question: Question | null
  index: number
  language: Language
  source: string
  /** Per-language buffers, so switching language never discards work. */
  sources: Record<string, string>
  cases: EditableCase[]
  report: RunReport | null
  referenceSolution: string | null
  busy: boolean
  error: string | null
  /** Seconds left, ticked locally between server reads. */
  remaining: number | null
  startedAt: number

  /** Reads the editor's live text. Registered by EditorPane once it loads. */
  readEditor: (() => string) | null
  /** Progress run for a generation in flight, if any. */
  runId: string | null

  go: (screen: Screen) => void
  /** Opens settings remembering where to come back to. */
  openSettings: () => void
  closeSettings: () => void
  setSource: (source: string) => void
  setLanguage: (language: Language) => void
  setReadEditor: (read: (() => string) | null) => void
  updateCase: (id: string, patch: Partial<EditableCase>) => void
  addCase: () => void
  duplicateCase: (id: string) => void
  removeCase: (id: string) => void
  resetCases: () => void
  casesAreValid: () => boolean
  computeExpected: (id: string) => Promise<void>
  startSession: (session: SessionInfo) => Promise<void>
  loadQuestion: (index: number) => Promise<void>
  run: () => Promise<void>
  submit: () => Promise<void>
  skip: () => Promise<void>
  next: () => Promise<void>
  nextQuestion: () => Promise<void>
  tick: () => void
  currentSource: () => string
  clearError: () => void
  publish: (allowImport?: boolean) => Promise<void>
  notice: string | null
  clearNotice: () => void
}

export const useStore = create<State>((set, get) => ({
  screen: 'setup',
  returnTo: 'picker',
  session: null,
  question: null,
  index: 0,
  language: readDefaultLanguage(),
  source: '',
  sources: {},
  cases: [],
  report: null,
  referenceSolution: null,
  busy: false,
  error: null,
  remaining: null,
  startedAt: Date.now(),
  readEditor: null,
  runId: null,
  notice: null,

  go: (screen) => set({ screen }),

  // Settings is a detour, not a destination: leaving must land you back where
  // you were, with the session untouched (it lives in this store, so it is).
  openSettings: () => set((st) => ({ screen: 'settings', returnTo: st.screen })),
  closeSettings: () => set((st) => ({ screen: st.returnTo })),
  setReadEditor: (readEditor) => set({ readEditor }),

  updateCase: (id, patch) =>
    set((s) => ({
      cases: s.cases.map((c) => (c.id === id ? { ...c, ...patch } : c)),
    })),

  addCase: () =>
    set((s) => {
      // Shape the new case from an existing one so the parameter names are
      // right; there is no other source for them on the client.
      const template = s.cases[0] ?? s.question?.visible_tests[0]
      const args = template && 'args' in template
        ? Object.fromEntries(Object.keys(template.args).map((k) => [k, '']))
        : {}
      return { cases: [...s.cases, { id: nextId(), args, expected: '',
                                     assertExpected: false }] }
    }),

  duplicateCase: (id) =>
    set((s) => {
      const found = s.cases.find((c) => c.id === id)
      if (!found) return {}
      const copy = { ...found, id: nextId(), args: { ...found.args } }
      const at = s.cases.findIndex((c) => c.id === id)
      return { cases: [...s.cases.slice(0, at + 1), copy, ...s.cases.slice(at + 1)] }
    }),

  removeCase: (id) => set((s) => ({ cases: s.cases.filter((c) => c.id !== id) })),

  resetCases: () =>
    set((s) => ({
      cases: (s.question?.visible_tests ?? []).map((t) =>
        caseFromTest(t.args, t.expected)),
    })),

  casesAreValid: () =>
    get().cases.every((c) => Object.keys(caseErrors(c)).length === 0),

  /** Fill in one case's expected value from the intended solution. */
  computeExpected: async (id) => {
    const { session, index, language, cases } = get()
    const target = cases.find((c) => c.id === id)
    if (!session || !target) return

    // Only the arguments need to parse; the expected field is what we are
    // about to replace.
    let args: Record<string, unknown>
    try {
      args = Object.fromEntries(
        Object.entries(target.args).map(([k, raw]) => [k, JSON.parse(raw)]),
      )
    } catch {
      set({ error: 'Fix the arguments before computing an expected value.' })
      return
    }

    set({ busy: true, error: null })
    try {
      const { expected } = await api.computeExpected(
        session.id, index, args, language,
      )
      get().updateCase(id, {
        expected: JSON.stringify(expected),
        assertExpected: true,
        computed: true,
      })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ busy: false })
    }
  },
  clearError: () => set({ error: null }),
  clearNotice: () => set({ notice: null }),

  publish: async (allowImport = false) => {
    const { session, index } = get()
    if (!session) return
    set({ busy: true, error: null, notice: null })
    try {
      const saved = await api.publish(session.id, index, allowImport)
      set({ notice: `Saved "${saved.title}" to the library.` })
    } catch (err) {
      // Publishing is a bonus, never a blocker: report and carry on.
      set({ error: (err as Error).message })
    } finally {
      set({ busy: false })
    }
  },
  setSource: (source) =>
    set((s) => ({ source, sources: { ...s.sources, [s.language]: source } })),

  setLanguage: (language) => {
    const { question, sources, language: previous, source } = get()
    if (!question || language === previous) return
    const scaffold =
      question.signatures.find((s) => s.language === language)?.scaffold ?? ''
    set({
      language,
      // Keep the outgoing buffer, and restore this language's if we have one.
      sources: { ...sources, [previous]: source },
      source: sources[language] ?? scaffold,
      report: null,
    })
  },

  startSession: async (session) => {
    set({ session, index: 0, screen: 'problem', remaining: session.remaining_seconds })
    await get().loadQuestion(0)
  },

  loadQuestion: async (index) => {
    const { session } = get()
    if (!session) return
    set({ busy: true, error: null })
    try {
      const question = await api.getQuestion(session.id, index)
      const { language } = get()
      // Keep the chosen language across questions when the new one offers it.
      const next = question.languages.includes(language)
        ? language
        : (question.languages[0] ?? question.language)
      const scaffold =
        question.signatures.find((s) => s.language === next)?.scaffold
        ?? question.scaffold
      set({
        question,
        index,
        language: next,
        // Scaffold, not the previous question's code.
        source: scaffold,
        sources: { [next]: scaffold },
        cases: question.visible_tests.map((t) => caseFromTest(t.args, t.expected)),
        report: null,
        referenceSolution: null,
        remaining: question.remaining_seconds,
        startedAt: Date.now(),
      })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ busy: false })
    }
  },

  run: async () => {
    const { session, index, language } = get()
    if (!session) return
    const source = get().currentSource()
    set({ busy: true, error: null })
    try {
      const cases = get().cases.map(toInput)
      set({ report: await api.run(session.id, index, source, language, cases) })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ busy: false })
    }
  },

  submit: async () => {
    const { session, index, language } = get()
    if (!session) return
    const source = get().currentSource()
    set({ busy: true, error: null })
    try {
      const report = await api.submit(session.id, index, source, language)
      set({ report, referenceSolution: report.reference_solution ?? null })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ busy: false })
    }
  },

  skip: async () => {
    const { session, index } = get()
    if (!session) return
    const result = await api.skip(session.id, index)
    set({ referenceSolution: result.reference_solution })
    await get().next()
  },

  next: async () => {
    const { session, index } = get()
    if (!session) return
    if (index + 1 >= session.question_count) {
      set({ screen: 'results' })
      return
    }
    await get().loadQuestion(index + 1)
  },

  /**
   * Move on to another question.
   *
   * In a multi-question session that means the next one. In a single-question
   * one it means a *new* question in the same format -- previously this landed
   * on the results screen, so there was no way to keep practising without going
   * back to the picker.
   */
  nextQuestion: async () => {
    const { session, index } = get()
    if (!session) return

    if (index + 1 < session.question_count) {
      await get().loadQuestion(index + 1)
      return
    }

    const id = randomId()
    set({ busy: true, error: null, notice: null, runId: id })
    try {
      // Carry the format, preset and concentration over so "next" means
      // "another one like this", not "start again".
      const gen = session.config.generation
      const fresh = await api.createSession({
        style: gen.style,
        preset: gen.preset_id || null,
        topics: gen.topics,
        freeform: gen.freeform,
        progress_id: id,
      })
      await get().startSession(fresh)
      set({ runId: null })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ busy: false })
    }
  },

  /** What is actually on screen beats what the store last recorded.
   *  A keystroke still settling would otherwise run the previous buffer --
   *  the editor showed the right code while the scaffold was executed. */
  currentSource: () => {
    const { readEditor, source } = get()
    const live = readEditor?.()
    return live !== undefined && live !== '' ? live : source
  },

  tick: () => {
    const { remaining } = get()
    if (remaining === null) return
    const next = Math.max(0, remaining - 1)
    set({ remaining: next })
    // At zero the attempt is submitted as-is, then the session advances --
    // matching how a real timed assessment behaves.
    if (next === 0) void get().submit().then(() => get().next())
  },
}))
