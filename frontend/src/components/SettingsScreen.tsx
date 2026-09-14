import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AppSettings, Language } from '../api'
import { useStore } from '../store'
import { useTheme } from '../theme'
import type { ThemeChoice } from '../theme'
import { DEFAULT_LANGUAGE_KEY, readDefaultLanguage } from '../prefs'

const LANGUAGES: Language[] = ['python', 'javascript', 'typescript']

/** Numeric and boolean server settings, with the bounds the API enforces. */
const SERVER_FIELDS: {
  name: string
  label: string
  hint?: string
  kind: 'text' | 'number' | 'bool' | 'choice'
  min?: number
  max?: number
  options?: { value: string; label: string }[]
}[] = [
  { name: 'llm_base_url', label: 'LLM endpoint', kind: 'text',
    hint: 'Any OpenAI-compatible API' },
  { name: 'llm_model', label: 'Model', kind: 'text' },
  { name: 'llm_timeout_s', label: 'Request timeout (seconds)', kind: 'number',
    min: 1, max: 600 },
  { name: 'llm_max_retries', label: 'Retries', kind: 'number', min: 0, max: 10 },
  { name: 'library_url', label: 'Question library URL', kind: 'text',
    hint: 'Empty disables publishing and browsing' },
  { name: 'check_statement_sufficiency', kind: 'bool',
    label: 'Check questions are solvable from their statement',
    hint: 'A second model solves from the prose alone. Catches unfair questions; roughly doubles generation cost.' },
  { name: 'generation_strategy', label: 'How questions are written',
    kind: 'choice',
    options: [
      { value: 'module', label: 'As a Python module (recommended)' },
      { value: 'monolithic', label: 'As a JSON payload (legacy)' },
    ],
    hint: 'A module is linted, type-checked and executed before you see it. The JSON path asks for code inside a data format, which models escape badly.' },
  { name: 'generation_attempts', label: 'Generation attempts', kind: 'number',
    min: 1, max: 10, hint: 'Regenerations before giving up on a question' },
  { name: 'repair_rounds', label: 'Repair rounds', kind: 'number',
    min: 0, max: 10,
    hint: 'Patch-and-re-gate rounds before regenerating. JSON payload strategy only.' },
  { name: 'tool_call_budget', label: 'Repair tool budget', kind: 'number',
    min: 0, max: 32, hint: 'JSON payload strategy only' },
  { name: 'tutor_tool_budget', label: 'Tutor tool budget', kind: 'number',
    min: 0, max: 32 },
  { name: 'tutor_message_cap', label: 'Tutor messages per session',
    kind: 'number', min: 1, max: 500 },
]

export function SettingsScreen() {
  const close = useStore((s) => s.closeSettings)
  const loadSettings = useStore((s) => s.loadSettings)
  const { choice, setChoice } = useTheme()
  const [defaultLanguage, setDefaultLanguage] =
    useState<Language>(readDefaultLanguage)
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [apiKey, setApiKey] = useState('')

  useEffect(() => {
    api.getSettings()
      .then((s) => { setSettings(s); setDraft(s.values) })
      .catch((err: Error) => setError(err.message))
  }, [])

  const dirty = settings
    ? Object.keys(draft).some((k) => draft[k] !== settings.values[k])
    : false

  async function save() {
    if (!settings) return
    setBusy(true); setError(null); setSaved(false)
    try {
      const changed = Object.fromEntries(
        Object.entries(draft).filter(([k, v]) => v !== settings.values[k]),
      )
      const next = await api.saveSettings(changed)
      setSettings(next); setDraft(next.values); setSaved(true)
      await loadSettings()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  /** Applies a key to the running server without storing it anywhere.
   *
   *  Reuses POST /api/setup, which has always been memory-only. A key entered
   *  here also overrides FREETCODER_FAKE_LLM, so a container started in
   *  offline mode can be pointed at a real provider without a restart.
   */
  async function useKey() {
    setBusy(true); setError(null)
    try {
      await api.saveSetup({ api_key: apiKey.trim() })
      setApiKey('')
      await refreshStatus()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function clearKey() {
    setBusy(true); setError(null)
    try {
      await api.saveSetup({ api_key: '' })
      await refreshStatus()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function refreshStatus() {
    const next = await api.getSettings()
    setSettings(next); setDraft(next.values)
    await loadSettings()
  }

  async function reset(field: string) {
    setBusy(true); setError(null)
    try {
      const next = await api.resetSetting(field)
      setSettings(next); setDraft(next.values)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-[var(--color-bg)] text-[var(--color-ink)]">
      <header className="flex items-center gap-4 border-b border-[var(--color-edge)] px-6 py-3">
        <h1 className="text-lg font-semibold">Settings</h1>
        <button onClick={close}
                className="ml-auto rounded border border-[var(--color-edge)] px-3 py-1
                           text-sm hover:border-[var(--color-muted)]">
          Done
        </button>
      </header>

      <div className="mx-auto max-w-2xl space-y-10 p-6">
        <Section
          title="This browser"
          note="Stored in this browser only, so it is yours alone even if the container is shared."
        >
          <Row label="Theme">
            <select
              aria-label="Theme"
              value={choice}
              onChange={(e) => setChoice(e.target.value as ThemeChoice)}
              className={selectClass}
            >
              <option value="system">Follow the system</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </Row>
          <Row label="Default language" hint="Where new sessions start">
            <select
              aria-label="Default language"
              value={defaultLanguage}
              onChange={(e) => {
                const lang = e.target.value as Language
                setDefaultLanguage(lang)
                localStorage.setItem(DEFAULT_LANGUAGE_KEY, lang)
              }}
              className={selectClass}
            >
              {LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </Row>
        </Section>

        <Section
          title="This server"
          note="Shared by everyone using this container."
        >
          {settings && !settings.persistent && (
            <p className="rounded border border-[var(--color-warn)]/40
                          bg-[var(--color-warn)]/5 p-3 text-sm text-[var(--color-warn)]">
              These are held in memory only. This deployment has no database
              file, so changes are lost when the server restarts. Saving still
              applies them for now.
            </p>
          )}

          {settings && (
            <div
              className="flex items-start gap-2 rounded border p-3 text-sm"
              style={{
                borderColor: STATUS_COLOUR[settings.llm_status] + '66',
                background: STATUS_COLOUR[settings.llm_status] + '0d',
              }}
              data-llm-status={settings.llm_status}
            >
              <span
                aria-hidden="true"
                className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full"
                style={{ background: STATUS_COLOUR[settings.llm_status] }}
              />
              <span>
                <strong>{STATUS_LABEL[settings.llm_status]}</strong>
                {' — '}{settings.llm_reason}
              </span>
            </div>
          )}

          <Row
            label="API key"
            hint={settings?.has_key
              ? settings.key_from_session
                ? `set, ending ${settings.key_hint} — entered here, lost when the server restarts`
                : `set, ending ${settings.key_hint} — from the environment`
              : 'not set. FREETCODER_LLM_API_KEY in .env is the durable place'}
          >
            <input
              type="password"
              aria-label="API key"
              placeholder="sk-or-..."
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className={inputClass}
            />
            <button
              onClick={useKey}
              disabled={busy || !apiKey.trim()}
              className="rounded border border-[var(--color-edge)] px-2 py-1
                         text-xs hover:border-[var(--color-muted)]
                         disabled:opacity-40"
            >
              Use this key
            </button>
            {settings?.key_from_session && (
              <button
                onClick={clearKey}
                disabled={busy}
                className="text-xs text-[var(--color-muted)] underline
                           hover:text-[var(--color-ink)] disabled:opacity-40"
              >
                Clear
              </button>
            )}
          </Row>

          {settings && SERVER_FIELDS.map((f) => (
            <Row
              key={f.name}
              label={f.label}
              hint={f.hint}
              source={settings.sources[f.name]}
              onReset={settings.sources[f.name] === 'saved'
                ? () => reset(f.name) : undefined}
            >
              {f.kind === 'choice' ? (
                <select
                  aria-label={f.label}
                  value={String(draft[f.name] ?? '')}
                  onChange={(e) =>
                    setDraft({ ...draft, [f.name]: e.target.value })}
                  className={inputClass}
                >
                  {f.options?.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              ) : f.kind === 'bool' ? (
                <input
                  type="checkbox"
                  aria-label={f.label}
                  checked={Boolean(draft[f.name])}
                  onChange={(e) =>
                    setDraft({ ...draft, [f.name]: e.target.checked })}
                />
              ) : (
                <input
                  type={f.kind === 'number' ? 'number' : 'text'}
                  aria-label={f.label}
                  min={f.min}
                  max={f.max}
                  value={String(draft[f.name] ?? '')}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      [f.name]: f.kind === 'number'
                        ? Number(e.target.value) : e.target.value,
                    })}
                  className={inputClass}
                />
              )}
            </Row>
          ))}

          {error && <p className="text-sm text-[var(--color-fail)]">{error}</p>}

          <div className="flex items-center gap-3">
            <button
              onClick={save}
              disabled={busy || !dirty}
              className="rounded bg-[var(--color-accent)] px-4 py-2 text-sm
                         font-medium text-white disabled:opacity-40"
            >
              {busy ? 'Saving…' : 'Save'}
            </button>
            {saved && !dirty && (
              <span className="text-sm text-[var(--color-pass)]">Saved</span>
            )}
            {settings?.db_path && (
              <span className="ml-auto font-mono text-xs text-[var(--color-muted)]">
                {settings.db_path}
              </span>
            )}
          </div>
        </Section>
      </div>
    </div>
  )
}

function Section({ title, note, children }: {
  title: string; note: string; children: React.ReactNode
}) {
  return (
    <section>
      <h2 className="text-sm font-semibold uppercase tracking-wide
                     text-[var(--color-muted)]">{title}</h2>
      <p className="mt-1 text-xs text-[var(--color-muted)]">{note}</p>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  )
}

function Row({ label, hint, source, onReset, children }: {
  label: string
  hint?: string
  source?: string
  onReset?: () => void
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <div className="w-full sm:w-64">
        <div className="text-sm">{label}</div>
        {hint && <div className="text-xs text-[var(--color-muted)]">{hint}</div>}
      </div>
      <div className="flex flex-1 items-center gap-2">
        {children}
        {source === 'environment' && (
          <span className="text-xs text-[var(--color-muted)]">from environment</span>
        )}
        {onReset && (
          <button onClick={onReset}
                  className="text-xs text-[var(--color-muted)] underline
                             hover:text-[var(--color-ink)]">
            Reset
          </button>
        )}
      </div>
    </div>
  )
}

const STATUS_COLOUR: Record<string, string> = {
  live: 'var(--color-pass)',
  offline: 'var(--color-warn)',
  unconfigured: 'var(--color-muted)',
}

const STATUS_LABEL: Record<string, string> = {
  live: 'LLM live',
  offline: 'Offline — recorded questions',
  unconfigured: 'No API key',
}

const inputClass =
  'w-full max-w-xs rounded border border-[var(--color-edge)] bg-[var(--color-panel)] ' +
  'px-3 py-1.5 text-sm text-[var(--color-ink)] outline-none ' +
  'focus:border-[var(--color-accent)]'

const selectClass = inputClass
