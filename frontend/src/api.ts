/** Typed wrapper over the backend. Small enough not to need a query library. */

export type Difficulty = 'easy' | 'medium' | 'hard'
export type Language = 'python' | 'javascript' | 'typescript' | 'go'

export type Verdict =
  | 'ok' | 'wrong_answer' | 'timeout' | 'memory_exceeded'
  | 'runtime_error' | 'compile_error' | 'internal_error'

export interface StylePreset {
  id: string
  label: string
  difficulty?: Difficulty[]
  topics?: string[]
  question_count?: number
}

export interface StyleInfo {
  id: string
  label: string
  description: string
  difficulty_locked: boolean
  summary: string
  presets: StylePreset[]
}

export interface TestCase {
  args: Record<string, unknown>
  expected: unknown
  explanation?: string | null
}

export interface Signature {
  language: Language
  function_name: string
  scaffold: string
}

export interface Question {
  index: number
  title: string
  difficulty: Difficulty
  topics: string[]
  statement_md: string
  constraints_md: string
  hint_md: string | null
  complexity_target: string | null
  language: Language
  /** "generated" or "imported". */
  source: string
  /** What the model assumed while adapting supplied text. Empty otherwise. */
  import_notes: string
  /** Languages this format offers, in preset order. */
  languages: Language[]
  signatures: Signature[]
  scaffold: string
  function_name: string
  visible_tests: TestCase[]
  hidden_test_count: number
  remaining_seconds: number | null
}

export interface CaseResult {
  index: number
  passed: boolean
  hidden: boolean
  ms: number
  over_budget: boolean
  args: Record<string, unknown> | null
  expected: unknown
  /** Whatever the submission printed while this case ran. */
  stdout: string
  /** False when the case has no expected value: shown, never marked right or wrong. */
  judged: boolean
  /** What the code returned. Null for hidden cases, which stay hidden. */
  actual: unknown
}

/** A case as edited in the Testcase tab. */
export interface CaseInput {
  args: Record<string, unknown>
  expected?: unknown
  assert_expected: boolean
}

export interface RunReport {
  verdict: Verdict
  passed: number
  total: number
  stderr: string
  cases: CaseResult[]
  first_failure: {
    index: number
    args: Record<string, unknown>
    expected: unknown
    actual: unknown
    error: string
    stdout: string
  } | null
  score?: {
    correctness: number
    performance: number
    speed: number
    total: number
    solved: boolean
    passed_cases: number
    total_cases: number
  }
  reference_solution?: string
  /** Total wall time across the graded cases. */
  total_ms?: number
  /** submission time / reference time, measured back to back on this machine. */
  ratio?: number | null
  percentile?: number | null
  samples?: number
  enough_samples?: boolean
}

export interface LibraryQuestion {
  id: string
  title: string
  style: string
  difficulty: Difficulty
  topics: string[]
  languages: Language[]
  author: string
  votes: number
  created_at: number
  submissions: number
}

export interface SessionInfo {
  id: string
  summary: string
  question_count: number
  current_index: number
  remaining_seconds: number | null
  finished: boolean
  config: {
    session: { allow_skip: boolean; allow_revisit: boolean; timing: string }
    environment: { languages: Language[] }
    generation: {
      give_hints: boolean
      style: string
      preset_id: string
      topics: string[]
      freeform: string
    }
  }
}

export interface SessionResults {
  score: number
  scale: string
  fraction: number
  solved: number
  question_count: number
  per_question: { total: number; solved: boolean }[]
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    // FastAPI puts the human-readable reason in `detail`.
    const body = await resp.json().catch(() => ({}))
    throw new ApiError(resp.status, body.detail ?? resp.statusText)
  }
  return resp.json() as Promise<T>
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

export interface SetupState {
  configured: boolean
  base_url: string
  model: string
  has_key: boolean
}

export interface PickerSelection {
  style: string
  preset?: string | null
  topics?: string[]
  /** Steers the topic within a generated question. */
  freeform?: string
  difficulty?: Difficulty | null
  /** Prose describing a question to adapt. Distinct from `freeform`. */
  import_text?: string
}

export const api = {
  health: () => request<{ ok: boolean; configured: boolean }>('/health'),
  getSetup: () => request<SetupState>('/setup'),
  saveSetup: (payload: { base_url?: string; api_key?: string; model?: string }) =>
    post<SetupState>('/setup', payload),

  formats: () => request<StyleInfo[]>('/formats'),
  topics: () => request<{ topics: string[]; unsupported: Record<string, string> }>('/topics'),
  preview: (sel: PickerSelection) =>
    post<{ summary: string; warnings: string[] }>('/formats/preview', sel),

  createSession: (sel: PickerSelection) => post<SessionInfo>('/sessions', sel),
  getSession: (id: string) => request<SessionInfo>(`/sessions/${id}`),
  getQuestion: (id: string, index: number) =>
    request<Question>(`/sessions/${id}/questions/${index}`),

  run: (id: string, index: number, source: string, language: Language,
        cases?: CaseInput[]) =>
    post<RunReport>(`/sessions/${id}/questions/${index}/run`, {
      source,
      language,
      cases,
    }),
  submit: (id: string, index: number, source: string, language: Language) =>
    post<RunReport>(`/sessions/${id}/questions/${index}/submit`, { source, language }),
  skip: (id: string, index: number) =>
    post<{ skipped: boolean; reference_solution: string }>(
      `/sessions/${id}/questions/${index}/skip`,
    ),
  results: (id: string) => request<SessionResults>(`/sessions/${id}/results`),

  libraryStatus: () =>
    request<{ configured: boolean; reachable: boolean }>('/library/status'),
  libraryQuestions: (filters: Record<string, string> = {}) => {
    const query = new URLSearchParams(
      Object.entries(filters).filter(([, v]) => v),
    ).toString()
    return request<{ questions: LibraryQuestion[]; total: number }>(
      `/library/questions${query ? `?${query}` : ''}`,
    )
  },
  publish: (id: string, index: number, allowImport = false) =>
    post<{ id: string; title: string }>(
      `/sessions/${id}/questions/${index}/publish`
      + (allowImport ? '?allow_import=true' : ''),
    ),
  sessionFromLibrary: (qid: string) =>
    post<SessionInfo>(`/sessions/from-library/${qid}`),
}
