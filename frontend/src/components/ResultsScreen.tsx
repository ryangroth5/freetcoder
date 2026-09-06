import { useEffect, useState } from 'react'
import { api } from '../api'
import type { SessionResults } from '../api'
import { useStore } from '../store'

export function ResultsScreen() {
  const session = useStore((s) => s.session)
  const go = useStore((s) => s.go)
  const [results, setResults] = useState<SessionResults | null>(null)

  useEffect(() => {
    if (session) api.results(session.id).then(setResults)
  }, [session])

  if (!results) return <div className="p-8 text-[var(--color-muted)]">Scoring…</div>

  return (
    <div className="mx-auto max-w-2xl p-10">
      <h1 className="text-2xl font-semibold">Session complete</h1>

      <div className="mt-6 rounded border border-[var(--color-edge)]
                      bg-[var(--color-panel)] p-6 text-center">
        <div className="text-5xl font-semibold tabular-nums">{results.score}</div>
        <div className="mt-1 text-sm text-[var(--color-muted)]">
          {results.scale === 'percent' ? 'percent' : `on the ${results.scale} scale`}
        </div>
        <div className="mt-3 text-sm">
          Solved {results.solved} of {results.question_count}
        </div>
      </div>

      <div className="mt-6 space-y-2">
        {results.per_question.map((q, i) => (
          <div key={i} className="flex items-center gap-3 rounded border
                                  border-[var(--color-edge)] px-4 py-2">
            <span className="text-sm">Question {i + 1}</span>
            <span className={`ml-auto text-sm ${
              q.solved ? 'text-[var(--color-pass)]' : 'text-[var(--color-muted)]'}`}>
              {Math.round(q.total * 100)}%
            </span>
          </div>
        ))}
      </div>

      <button onClick={() => go('picker')}
              className="mt-8 rounded bg-[var(--color-accent)] px-6 py-2 font-medium text-white">
        Practise something else
      </button>
    </div>
  )
}
