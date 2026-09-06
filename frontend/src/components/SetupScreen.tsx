import { useEffect, useState } from 'react'
import { api } from '../api'

/** First run: point the app at an OpenAI-compatible endpoint. */
export function SetupScreen({ onReady }: { onReady: () => void }) {
  const [baseUrl, setBaseUrl] = useState('https://openrouter.ai/api/v1')
  const [model, setModel] = useState('anthropic/claude-sonnet-4.5')
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // Already configured by environment variable? Skip straight through.
    api.getSetup().then((s) => {
      setBaseUrl(s.base_url)
      setModel(s.model)
      if (s.configured) onReady()
    })
  }, [onReady])

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const state = await api.saveSetup({ base_url: baseUrl, model, api_key: apiKey })
      if (!state.configured) throw new Error('Still not configured — is the key set?')
      onReady()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-lg">
        <h1 className="text-2xl font-semibold">freetcoder</h1>
        <p className="mt-1 text-[var(--color-muted)]">
          A local, LLM-generated coding assessment environment.
        </p>

        <div className="mt-8 space-y-4">
          <Field label="LLM endpoint" hint="Any OpenAI-compatible API">
            <input className={inputClass} value={baseUrl}
                   onChange={(e) => setBaseUrl(e.target.value)} />
          </Field>
          <Field label="Model">
            <input className={inputClass} value={model}
                   onChange={(e) => setModel(e.target.value)} />
          </Field>
          <Field label="API key" hint="Held in memory only; never written to disk">
            <input className={inputClass} type="password" value={apiKey}
                   placeholder="sk-or-..."
                   onChange={(e) => setApiKey(e.target.value)} />
          </Field>

          {error && <p className="text-sm text-[var(--color-fail)]">{error}</p>}

          <button onClick={save} disabled={busy || !apiKey}
                  className="w-full rounded bg-[var(--color-accent)] px-4 py-2 font-medium
                             text-white disabled:opacity-40">
            {busy ? 'Checking…' : 'Continue'}
          </button>
          <p className="text-xs text-[var(--color-muted)]">
            Running a local model? Point the endpoint at
            {' '}<code>http://host.docker.internal:11434/v1</code> for Ollama and use any
            non-empty key.
          </p>
        </div>
      </div>
    </div>
  )
}

const inputClass =
  'w-full rounded border border-[var(--color-edge)] bg-[var(--color-panel)] px-3 py-2 ' +
  'text-[var(--color-ink)] outline-none focus:border-[var(--color-accent)]'

function Field({ label, hint, children }:
  { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      {hint && <span className="ml-2 text-xs text-[var(--color-muted)]">{hint}</span>}
      <div className="mt-1">{children}</div>
    </label>
  )
}
