import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ProgressRun, ProgressStep } from '../api'

const POLL_MS = 900
const TICK_MS = 100

/**
 * How long each step took, and which one is still running.
 *
 * `at` is when a step *started*, so a step's duration is the gap to the next
 * one. The last step has no next one because it has not finished, which is
 * exactly the number a reader watching a slow generation wants.
 */
export function stepTimings(
  steps: { at: number }[],
  elapsed: number,
  finished: boolean,
): { took: number; running: boolean }[] {
  return steps.map((step, i) => {
    const last = i === steps.length - 1
    const until = last ? elapsed : steps[i + 1].at
    return { took: Math.max(0, until - step.at), running: last && !finished }
  })
}

/**
 * What generation is actually doing.
 *
 * A truthful log rather than a progress bar: attempts and repair rounds are
 * unbounded, so any percentage would be invented, and a bar stalling at 80% is
 * worse than none. Elapsed time plus a list of real steps is both more useful
 * and more honest.
 *
 * It also doubles as diagnosis. A failed generation used to collapse to one
 * apologetic sentence; the log stays on screen with the attempt history intact.
 */
export function GenerationProgress({ runId, onCancelled }: {
  runId: string
  onCancelled?: () => void
}) {
  const [run, setRun] = useState<ProgressRun | null>(null)
  const [cancelling, setCancelling] = useState(false)

  // Elapsed is counted from the server's own reading, re-anchored on every
  // poll. Counting from mount instead meant a reload mid-generation restarted
  // the clock at zero, and drift accumulated over a run measured in minutes.
  const anchor = useRef({ elapsed: 0, at: Date.now() })
  const [, tick] = useState(0)

  const finished = run?.finished ?? false

  useEffect(() => {
    let live = true

    const read = async () => {
      try {
        const next = await api.progress(runId)
        if (!live) return
        anchor.current = { elapsed: next.elapsed, at: Date.now() }
        setRun(next)
      } catch {
        // A 404 just means the run has not been registered yet.
      }
    }

    // Read once immediately: waiting a full interval means a fast generation
    // finishes before anything is ever shown, so the panel flashes empty.
    void read()
    if (finished) return () => { live = false }

    const poll = window.setInterval(read, POLL_MS)
    // Fast enough to read as a running stopwatch rather than a stuck number.
    const clock = window.setInterval(() => live && tick(n => n + 1), TICK_MS)
    return () => {
      live = false
      window.clearInterval(poll)
      window.clearInterval(clock)
    }
  }, [runId, finished])

  async function cancel() {
    setCancelling(true)
    await api.cancelProgress(runId).catch(() => undefined)
    onCancelled?.()
  }

  const steps = run?.steps ?? []
  const elapsed = finished
    ? (run?.elapsed ?? 0)
    : anchor.current.elapsed + (Date.now() - anchor.current.at) / 1000
  const timings = stepTimings(steps, elapsed, finished)

  return (
    <div className="mt-4 rounded border border-[var(--color-edge)]
                    bg-[var(--color-panel)] p-4">
      <div className="mb-3 flex items-center gap-3">
        <span className="text-sm font-medium">
          {cancelling ? 'Cancelling…' : 'Building your question'}
        </span>
        <span className="font-mono text-xs tabular-nums text-[var(--color-muted)]">
          {elapsed.toFixed(1)}s
        </span>
        <button
          onClick={cancel}
          disabled={cancelling || finished}
          className="ml-auto rounded border border-[var(--color-edge)] px-2 py-0.5
                     text-xs text-[var(--color-muted)]
                     hover:text-[var(--color-ink)] disabled:opacity-40"
        >
          Cancel
        </button>
      </div>

      {cancelling && (
        // Honest about the limit: a request already in flight to the model
        // cannot be interrupted, so the button must not look broken.
        <p className="mb-3 text-xs text-[var(--color-muted)]">
          Waiting for the model to finish responding before stopping.
        </p>
      )}

      {steps.length === 0 ? (
        <p className="text-xs text-[var(--color-muted)]">Starting…</p>
      ) : (
        <ol className="space-y-1">
          {steps.map((step, i) => (
            <StepRow
              key={i}
              step={step}
              latest={i === steps.length - 1}
              {...timings[i]}
            />
          ))}
        </ol>
      )}

      <p className="mt-3 text-xs text-[var(--color-muted)]">
        Every question is executed against its own tests before you see it.
      </p>
    </div>
  )
}

const ICON: Record<ProgressStep['kind'], string> = {
  info: '·',
  ok: '✓',
  warn: '!',
  fail: '✕',
}

const TONE: Record<ProgressStep['kind'], string> = {
  info: 'text-[var(--color-muted)]',
  ok: 'text-[var(--color-pass)]',
  warn: 'text-[var(--color-warn)]',
  fail: 'text-[var(--color-fail)]',
}

function StepRow({ step, latest, took, running }: {
  step: ProgressStep
  latest: boolean
  took: number
  running: boolean
}) {
  return (
    <li className="flex gap-2 text-xs">
      <span className={`w-3 shrink-0 ${TONE[step.kind]}`}>
        {running ? '·' : ICON[step.kind]}
      </span>
      <span className={latest ? 'text-[var(--color-ink)]' : 'text-[var(--color-muted)]'}>
        {step.message}
      </span>
      <span
        className="ml-auto shrink-0 font-mono tabular-nums
                   text-[var(--color-muted)]"
        title={`started at ${step.at.toFixed(1)}s`}
      >
        {took.toFixed(1)}s{running ? '…' : ''}
      </span>
    </li>
  )
}
