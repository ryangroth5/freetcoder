import { useEffect, useState } from 'react'
import { api } from '../api'
import { GenerationProgress } from './GenerationProgress'
import type {
  Difficulty,
  LibraryQuestion,
  PickerSelection,
  SessionInfo,
  StyleInfo,
} from '../api'

const OTHER = '__other__'

/**
 * Three tiers of buttons: style, then a preset within that style, then a
 * concentration. Each tier ends in "Other…", whose free text narrows the tier
 * above it without replacing the format's structural rules.
 */
export function FormatPicker({ onStart }: { onStart: (s: SessionInfo) => void }) {
  const [styles, setStyles] = useState<StyleInfo[]>([])
  const [topics, setTopics] = useState<string[]>([])
  const [unsupported, setUnsupported] = useState<Record<string, string>>({})

  const [styleId, setStyleId] = useState<string | null>(null)
  const [presetId, setPresetId] = useState<string | null>(null)
  const [chosenTopics, setChosenTopics] = useState<string[]>([])
  const [freeform, setFreeform] = useState('')
  const [showFreeform, setShowFreeform] = useState(false)
  const [difficulty, setDifficulty] = useState<Difficulty | null>(null)

  const [summary, setSummary] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [source, setSource] = useState<'generate' | 'library' | 'own'>('generate')
  // Explicit rather than inferred: guessing intent from how much text someone
  // typed would be wrong often enough to be annoying.
  const [ownMode, setOwnMode] = useState<'describe' | 'paste'>('describe')
  const [ownText, setOwnText] = useState('')

  useEffect(() => {
    api.formats().then(setStyles)
    api.topics().then((t) => {
      setTopics(t.topics)
      setUnsupported(t.unsupported)
    })
  }, [])

  const style = styles.find((s) => s.id === styleId) ?? null

  const selection: PickerSelection = {
    style: styleId ?? '',
    preset: presetId,
    topics: chosenTopics,
    freeform: source === 'own' && ownMode === 'describe' ? ownText : freeform,
    import_text: source === 'own' && ownMode === 'paste' ? ownText : '',
    difficulty,
  }

  // Preview the resolved config so nothing is hidden behind a button.
  useEffect(() => {
    if (!styleId) return
    api.preview(selection).then(
      (p) => {
        setSummary(p.summary)
        setWarnings(p.warnings)
        setError(null)
      },
      (err) => setError((err as Error).message),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [styleId, presetId, chosenTopics.join(','), freeform, difficulty])

  async function start() {
    // Minted here so the progress panel can watch the request while it runs.
    const id = crypto.randomUUID()
    setRunId(id)
    setBusy(true)
    setError(null)
    try {
      onStart(await api.createSession({ ...selection, progress_id: id }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
      // The log stays on screen after a failure: the attempt history is the
      // most useful thing on the page at that moment.
    }
  }

  return (
    <div className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-semibold">What would you like to practise?</h1>

      <div className="mt-5 flex gap-2">
        <Choice selected={source === 'generate'} onClick={() => setSource('generate')}>
          Generate a new question
        </Choice>
        <Choice selected={source === 'library'} onClick={() => setSource('library')}>
          From the library
        </Choice>
        <Choice selected={source === 'own'} onClick={() => setSource('own')}>
          Bring your own
        </Choice>
      </div>

      {source === 'library' && <LibraryBrowser onStart={onStart} />}

      {source === 'own' && (
        <section className="mt-6">
          <div className="mb-3 flex gap-2">
            <Choice small selected={ownMode === 'describe'}
                    onClick={() => setOwnMode('describe')}>
              Describe a question
            </Choice>
            <Choice small selected={ownMode === 'paste'}
                    onClick={() => setOwnMode('paste')}>
              Paste a question to adapt
            </Choice>
          </div>

          <textarea
            aria-label="Your question"
            value={ownText}
            onChange={(e) => setOwnText(e.target.value)}
            rows={ownMode === 'paste' ? 12 : 4}
            placeholder={ownMode === 'describe'
              ? 'e.g. make a question about counting dogs in a kennel log'
              : 'Paste the question here — statement, examples, constraints, '
                + 'however much of it you have.'}
            className="w-full rounded border border-[var(--color-edge)]
                       bg-[var(--color-panel)] px-3 py-2 font-mono text-[13px]
                       text-[var(--color-ink)] outline-none
                       focus:border-[var(--color-accent)]"
          />

          <p className="mt-2 text-xs text-[var(--color-muted)]">
            {ownMode === 'describe'
              ? 'A topic or premise. The question itself is written for you.'
              : 'Your text is a starting point: it will be tightened where the '
                + 'prose leaves things open, and you will be told what was '
                + 'assumed. It is validated by execution like any other '
                + 'question, so it is provably solvable or you get a reason why '
                + 'not.'}
          </p>
        </section>
      )}

      {(source === 'generate' || source === 'own') && (
        <>
      <Tier n={source === 'own' ? 2 : 1} title="Assessment style">
        <div className="flex flex-wrap gap-2">
          {styles.map((s) => (
            <Choice key={s.id} selected={styleId === s.id}
                    onClick={() => { setStyleId(s.id); setPresetId(null); setDifficulty(null) }}>
              {s.label}
            </Choice>
          ))}
        </div>
        {style && (
          <p className="mt-2 text-sm text-[var(--color-muted)]">{style.description}</p>
        )}
      </Tier>

      {style && (
        <Tier n={2} title={`${style.label} presets`}>
          <div className="flex flex-wrap gap-2">
            {style.presets.map((p) => (
              <Choice key={p.id} selected={presetId === p.id}
                      onClick={() => setPresetId(presetId === p.id ? null : p.id)}>
                {p.label}
              </Choice>
            ))}
          </div>

          {style.difficulty_locked ? (
            <p className="mt-3 text-xs text-[var(--color-muted)]">
              This format defines its own difficulty curve, so difficulty is fixed —
              choosing it would stop this simulating a real {style.label}.
            </p>
          ) : (
            <div className="mt-3 flex items-center gap-2">
              <span className="text-sm text-[var(--color-muted)]">Difficulty:</span>
              {(['easy', 'medium', 'hard'] as Difficulty[]).map((d) => (
                <Choice key={d} small selected={difficulty === d}
                        onClick={() => setDifficulty(difficulty === d ? null : d)}>
                  {d}
                </Choice>
              ))}
            </div>
          )}
        </Tier>
      )}

      {style && (
        <Tier n={3} title="Concentration" optional>
          <div className="flex flex-wrap gap-2">
            {topics.map((t) => {
              const blocked = t in unsupported
              return (
                <Choice key={t} small disabled={blocked} title={unsupported[t]}
                        selected={chosenTopics.includes(t)}
                        onClick={() =>
                          setChosenTopics((prev) =>
                            prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
                          )}>
                  {t}{blocked && ' ⚠'}
                </Choice>
              )
            })}
            <Choice small selected={showFreeform} onClick={() => setShowFreeform(!showFreeform)}>
              Other…
            </Choice>
          </div>

          {showFreeform && (
            <div className="mt-3">
              <input
                className="w-full rounded border border-[var(--color-edge)]
                           bg-[var(--color-panel)] px-3 py-2 outline-none
                           focus:border-[var(--color-accent)]"
                placeholder="e.g. SQL window functions, graph algorithms only"
                value={freeform}
                onChange={(e) => setFreeform(e.target.value)}
              />
              <p className="mt-1 text-xs text-[var(--color-muted)]">
                This narrows the topic within {style.label}; it does not change the
                format's structure.
              </p>
            </div>
          )}
        </Tier>
      )}

      {summary && (
        <div className="mt-8 rounded border border-[var(--color-edge)]
                        bg-[var(--color-panel)] p-4">
          <div className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            You will get
          </div>
          <div className="mt-1 font-medium">{summary}</div>
          {warnings.map((w) => (
            <p key={w} className="mt-2 text-sm text-[var(--color-warn)]">⚠ {w}</p>
          ))}
        </div>
      )}

      {error && <p className="mt-4 text-sm text-[var(--color-fail)]">{error}</p>}

      <button
        onClick={start}
        disabled={!styleId || busy || (source === 'own' && !ownText.trim())}
        className="mt-6 rounded bg-[var(--color-accent)] px-6 py-2 font-medium text-white
                   disabled:opacity-40"
      >
        {busy ? 'Generating and validating…' : 'Start'}
      </button>
      {runId && (busy || error) && (
        <GenerationProgress runId={runId} onCancelled={() => setBusy(false)} />
      )}
        </>
      )}
    </div>
  )
}

/** Saved questions, from the shared library service. */
function LibraryBrowser({ onStart }: { onStart: (s: SessionInfo) => void }) {
  const [questions, setQuestions] = useState<LibraryQuestion[]>([])
  const [status, setStatus] = useState<{ configured: boolean; reachable: boolean }>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.libraryStatus().then(setStatus).catch(() => undefined)
    api.libraryQuestions().then((r) => setQuestions(r.questions)).catch(() => undefined)
  }, [])

  async function start(qid: string) {
    setBusy(true)
    setError(null)
    try {
      onStart(await api.sessionFromLibrary(qid))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (status && !status.configured) {
    return (
      <p className="mt-6 text-sm text-[var(--color-muted)]">
        No question library is configured. Set <code>FREETCODER_LIBRARY_URL</code> to
        share and reuse questions.
      </p>
    )
  }

  if (status && !status.reachable) {
    return (
      <p className="mt-6 text-sm text-[var(--color-warn)]">
        The question library is unreachable. Generating new questions still works.
      </p>
    )
  }

  return (
    <div className="mt-6">
      {error && <p className="mb-3 text-sm text-[var(--color-fail)]">{error}</p>}
      {questions.length === 0 ? (
        <p className="text-sm text-[var(--color-muted)]">
          Nothing saved yet. Solve a question you like and press ☆ Save.
        </p>
      ) : (
        <ul className="space-y-2">
          {questions.map((q) => (
            <li key={q.id}>
              <button
                onClick={() => start(q.id)}
                disabled={busy}
                className="flex w-full items-center gap-3 rounded border
                           border-[var(--color-edge)] bg-[var(--color-panel)] px-4 py-2
                           text-left hover:border-[var(--color-muted)]
                           disabled:opacity-40"
              >
                <span className="font-medium">{q.title}</span>
                <span className="text-xs capitalize text-[var(--color-muted)]">
                  {q.difficulty} · {q.style}
                </span>
                <span className="ml-auto text-xs text-[var(--color-muted)]">
                  {q.submissions} submission{q.submissions === 1 ? '' : 's'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Tier({ n, title, optional, children }: {
  n: number; title: string; optional?: boolean; children: React.ReactNode
}) {
  return (
    <section className="mt-8">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide
                     text-[var(--color-muted)]">
        {n}. {title}{optional && ' (optional)'}
      </h2>
      {children}
    </section>
  )
}

function Choice({ selected, small, disabled, title, onClick, children }: {
  selected?: boolean; small?: boolean; disabled?: boolean; title?: string
  onClick?: () => void; children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={[
        'rounded border transition-colors',
        small ? 'px-2.5 py-1 text-xs' : 'px-4 py-2 text-sm',
        selected
          ? 'border-[var(--color-accent)] bg-[var(--color-accent)] text-white'
          : 'border-[var(--color-edge)] bg-[var(--color-panel)] hover:border-[var(--color-muted)]',
        disabled ? 'cursor-not-allowed opacity-40' : '',
      ].join(' ')}
    >
      {children}
    </button>
  )
}

export { OTHER }
