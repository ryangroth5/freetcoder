import { useEffect } from 'react'
import { useStore } from '../store'

const LABEL: Record<string, string> = {
  live: 'LLM live',
  offline: 'offline',
  unconfigured: 'no key',
}

/** Colours read from the palette so the dot matches the rest of the chrome. */
const COLOUR: Record<string, string> = {
  live: 'var(--color-pass)',
  offline: 'var(--color-warn)',
  unconfigured: 'var(--color-muted)',
}

/**
 * Whether the app is really talking to a provider.
 *
 * Worth the chrome: a key can be set and valid while every question still comes
 * from a fixture, because FREETCODER_FAKE_LLM wins in `build_client`. Before
 * this the only signals were `configured` and `has_key`, both true in exactly
 * that case.
 */
export function LlmStatus({ compact = false }: { compact?: boolean }) {
  const settings = useStore((s) => s.settings)
  const loadSettings = useStore((s) => s.loadSettings)

  useEffect(() => {
    if (!settings) void loadSettings()
  }, [settings, loadSettings])

  if (!settings) return null
  const status = settings.llm_status

  return (
    <span
      className="flex items-center gap-1.5 text-xs text-[var(--color-muted)]"
      title={settings.llm_reason}
      data-llm-status={status}
    >
      <span
        aria-hidden="true"
        className="inline-block h-2 w-2 shrink-0 rounded-full"
        style={{ background: COLOUR[status] ?? COLOUR.unconfigured }}
      />
      {!compact && <span>{LABEL[status] ?? status}</span>}
      <span className="sr-only">{settings.llm_reason}</span>
    </span>
  )
}
