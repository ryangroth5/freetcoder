import { useEffect } from 'react'
import { useStore } from '../store'

function format(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function Timer() {
  const remaining = useStore((s) => s.remaining)
  const tick = useStore((s) => s.tick)

  useEffect(() => {
    if (remaining === null) return
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [remaining === null, tick])

  if (remaining === null) return <span className="text-xs text-[var(--color-muted)]">untimed</span>

  const low = remaining < 300
  return (
    <span
      className={`font-mono text-sm tabular-nums ${
        low ? 'text-[var(--color-fail)]' : 'text-[var(--color-ink)]'}`}
      title={low ? 'Under 5 minutes — the attempt auto-submits at zero' : undefined}
    >
      ⏱ {format(remaining)}
    </span>
  )
}
