import { useState } from 'react'
import type { ProgressCall } from '../api'
import { readInspectorOpen, writeInspectorOpen } from '../prefs'

/**
 * What each model call is doing, for when generation is slow or wrong.
 *
 * Closed by default: most people never need it. Open, it answers the questions
 * the progress log cannot — did the model start answering, how fast, which
 * upstream actually served it, and what exactly was asked and returned.
 */
export function CallInspector({ calls }: { calls: ProgressCall[] }) {
  const [open, setOpen] = useState(readInspectorOpen)

  function toggle() {
    setOpen(!open)
    writeInspectorOpen(!open)
  }

  return (
    <div className="mt-3 border-t border-[var(--color-edge)] pt-2">
      <button
        onClick={toggle}
        aria-expanded={open}
        className="text-xs text-[var(--color-muted)] hover:text-[var(--color-ink)]"
      >
        {open ? '▾' : '▸'} Inspect model calls ({calls.length})
      </button>
      {open && (
        calls.length === 0
          ? <p className="mt-2 text-xs text-[var(--color-muted)]">No calls yet.</p>
          : <ol className="mt-2 space-y-1">
              {calls.map((call, i) => <CallRow key={i} call={call} />)}
            </ol>
      )}
    </div>
  )
}

const OUTCOME_TONE: Record<ProgressCall['outcome'], string> = {
  running: 'text-[var(--color-muted)]',
  ok: 'text-[var(--color-pass)]',
  fell_back: 'text-[var(--color-warn)]',
  stalled: 'text-[var(--color-warn)]',
  timeout: 'text-[var(--color-fail)]',
  error: 'text-[var(--color-fail)]',
}

function CallRow({ call }: { call: ProgressCall }) {
  const [expanded, setExpanded] = useState(false)
  const route = call.served_by ? `${call.model} → ${call.served_by}` : call.model
  // A running call with no first token is the stall this panel exists to show.
  const ttft = call.ttft_s === null
    ? (call.in_flight ? `waiting ${call.seconds.toFixed(0)}s` : '—')
    : `${call.ttft_s.toFixed(1)}s`

  return (
    <li className="text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="flex w-full flex-wrap items-baseline gap-x-3 text-left font-mono"
      >
        <span className={OUTCOME_TONE[call.outcome]}>{call.outcome}</span>
        <span>{call.stage}</span>
        <span className="text-[var(--color-muted)]">{route}</span>
        <span className="ml-auto tabular-nums text-[var(--color-muted)]">
          ttft {ttft} · {call.tokens_streamed} tok · {call.tokens_per_s} tok/s ·
          gap {call.longest_gap_s.toFixed(1)}s · {call.seconds.toFixed(1)}s
        </span>
      </button>
      {call.error && <div className="text-[var(--color-fail)]">{call.error}</div>}
      {expanded && (
        <div className="mt-1 space-y-1">
          <Block label={`Prompt (${call.prompt_tokens} tokens)`} text={call.prompt} />
          <Block
            label={`Reply (${call.completion_tokens} tokens)`}
            text={call.reply || (call.in_flight ? '…nothing yet' : '(empty)')}
          />
        </div>
      )}
    </li>
  )
}

function Block({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <div className="text-[var(--color-muted)]">{label}</div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded
                      bg-[var(--color-surface)] p-2 text-[11px]">{text}</pre>
    </div>
  )
}
