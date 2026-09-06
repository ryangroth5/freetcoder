import { useEffect, useState } from 'react'
import type { Question, RunReport } from '../api'
import { caseErrors, useStore } from '../store'
import type { EditableCase } from '../store'

const VERDICT_LABEL: Record<string, string> = {
  ok: 'Accepted',
  wrong_answer: 'Wrong Answer',
  timeout: 'Time Limit Exceeded',
  memory_exceeded: 'Memory Limit Exceeded',
  runtime_error: 'Runtime Error',
  compile_error: 'Compile Error',
  internal_error: 'Internal Error',
}

/** Testcase / Test Result pane, with the per-case tab strip. */
export function ResultsPane({ question, report, busy }: {
  question: Question
  report: RunReport | null
  busy: boolean
}) {
  const [tab, setTab] = useState<'testcase' | 'result'>('testcase')
  const [selected, setSelected] = useState(0)

  useEffect(() => {
    if (!report) return
    setTab('result')
    // Jump straight to what went wrong -- that is what you want to see.
    const firstFail = report.cases?.findIndex((c) => !c.passed) ?? -1
    setSelected(firstFail >= 0 ? firstFail : 0)
  }, [report])

  const cases = report?.cases ?? []
  const current = cases[selected]
  const accepted = report?.verdict === 'ok'

  return (
    <div className="flex h-full flex-col bg-[var(--color-surface)]">
      <div className="flex gap-1 border-b border-[var(--color-edge)] px-3">
        <Tab active={tab === 'testcase'} onClick={() => setTab('testcase')}>Testcase</Tab>
        <Tab active={tab === 'result'} onClick={() => setTab('result')}>Test Result</Tab>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {tab === 'testcase' && <CaseEditor question={question} />}

        {tab === 'result' && (
          busy ? <div className="text-[var(--color-muted)]">Running…</div>
          : !report ? <div className="text-[var(--color-muted)]">Run your code to see results.</div>
          : (
            <>
              <div className="flex flex-wrap items-baseline gap-3">
                <span className={`text-lg font-semibold ${
                  accepted ? 'text-[var(--color-pass)]' : 'text-[var(--color-fail)]'}`}>
                  {VERDICT_LABEL[report.verdict] ?? report.verdict}
                </span>
                <span className="text-sm text-[var(--color-muted)]">
                  {report.passed}/{report.total} cases
                </span>
                {report.score && (
                  <span className="text-sm text-[var(--color-muted)]">
                    score {Math.round(report.score.total * 100)}%
                  </span>
                )}
                {report.ratio != null && (
                  <span className="text-sm text-[var(--color-muted)]"
                        title="Your time divided by the reference solution's, both
                               measured on this machine just now. Below 1.00 beats it.">
                    {report.ratio.toFixed(2)}× the reference
                  </span>
                )}
                {report.enough_samples && report.percentile != null && (
                  <span className="text-sm text-[var(--color-pass)]">
                    faster than {report.percentile}% of {report.samples} submissions
                  </span>
                )}
              </div>

              {report.stderr && !cases.length && (
                <pre className="mt-3 overflow-x-auto rounded bg-[var(--color-panel)] p-3
                                font-mono text-xs text-[var(--color-fail)]">
                  {report.stderr}
                </pre>
              )}

              {cases.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {cases.map((c, i) => (
                    <button key={i} onClick={() => setSelected(i)}
                      className={[
                        'rounded px-2.5 py-1 text-xs',
                        selected === i ? 'bg-[var(--color-panel)] ring-1 ring-[var(--color-edge)]'
                                       : 'hover:bg-[var(--color-panel)]',
                        !c.judged ? 'text-[var(--color-muted)]'
                          : c.passed ? 'text-[var(--color-pass)]'
                          : 'text-[var(--color-fail)]',
                      ].join(' ')}>
                      {!c.judged ? '·' : c.passed ? '✓' : '✕'} Case {i + 1}
                      {c.over_budget && ' 🐢'}
                    </button>
                  ))}
                </div>
              )}

              {current && (
                <div className="mt-4 space-y-3">
                  {current.hidden && !report.first_failure ? (
                    <p className="text-sm text-[var(--color-muted)]">
                      Hidden case — inputs stay hidden.
                    </p>
                  ) : null}

                  {/* Each argument gets its own labelled box, not one blob. */}
                  {current.args &&
                    Object.entries(current.args).map(([k, v]) => (
                      <Labelled key={k} label={k}>{JSON.stringify(v)}</Labelled>
                    ))}
                  {current.judged &&
                    current.expected !== undefined && current.expected !== null && (
                    <Labelled label="Expected">{JSON.stringify(current.expected)}</Labelled>
                  )}
                  {!current.hidden && (
                    <Labelled label={current.judged ? 'Your output' : 'Output'}>
                      {JSON.stringify(current.actual)}
                    </Labelled>
                  )}
                  {!current.judged && (
                    <p className="text-xs text-[var(--color-muted)]">
                      No expected value, so this case is not marked right or wrong.
                    </p>
                  )}
                  {current.stdout && (
                    <Labelled label="Stdout">
                      <pre className="whitespace-pre-wrap">{current.stdout}</pre>
                    </Labelled>
                  )}
                  <div className="text-xs text-[var(--color-muted)]">
                    Runtime: {current.ms.toFixed(1)} ms
                    {current.over_budget && (
                      <span className="ml-2 text-[var(--color-warn)]">
                        over the performance budget — correct, but too slow
                      </span>
                    )}
                  </div>
                </div>
              )}

              {report.first_failure && (
                <div className="mt-4 rounded border border-[var(--color-fail)]/40
                                bg-[var(--color-fail)]/5 p-3">
                  <div className="mb-2 text-xs font-semibold uppercase
                                  tracking-wide text-[var(--color-fail)]">
                    First failing case
                  </div>
                  {Object.entries(report.first_failure.args).map(([k, v]) => (
                    <Labelled key={k} label={k}>{JSON.stringify(v)}</Labelled>
                  ))}
                  <Labelled label="Expected">
                    {JSON.stringify(report.first_failure.expected)}
                  </Labelled>
                  <Labelled label="Your output">
                    {report.first_failure.error
                      ? <span className="text-[var(--color-fail)]">
                          {report.first_failure.error.split('\n').slice(-2).join(' ')}
                        </span>
                      : JSON.stringify(report.first_failure.actual)}
                  </Labelled>
                  {report.first_failure.stdout && (
                    <Labelled label="Stdout">
                      <pre className="whitespace-pre-wrap">
                        {report.first_failure.stdout}
                      </pre>
                    </Labelled>
                  )}
                </div>
              )}
            </>
          )
        )}
      </div>
    </div>
  )
}

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="mb-1 text-xs text-[var(--color-muted)]">{label} =</div>
      <div className="overflow-x-auto rounded bg-[var(--color-panel)] px-3 py-2
                      font-mono text-[13px]">
        {children}
      </div>
    </div>
  )
}

function Tab({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button onClick={onClick}
      className={`border-b-2 px-3 py-2 text-sm ${
        active ? 'border-[var(--color-accent)] text-white'
               : 'border-transparent text-[var(--color-muted)] hover:text-[var(--color-ink)]'}`}>
      {children}
    </button>
  )
}

/** The Testcase tab: an unlimited, editable case list. */
function CaseEditor({ question }: { question: Question }) {
  const { cases, updateCase, addCase, duplicateCase, removeCase, resetCases } =
    useStore()

  return (
    <div className="space-y-3">
      {cases.map((c, i) => (
        <CaseRow
          key={c.id}
          index={i}
          value={c}
          onChange={(patch) => updateCase(c.id, patch)}
          onDuplicate={() => duplicateCase(c.id)}
          onRemove={cases.length > 1 ? () => removeCase(c.id) : undefined}
        />
      ))}

      <div className="flex items-center gap-3">
        <button onClick={addCase}
                className="rounded border border-[var(--color-edge)] px-3 py-1 text-sm
                           hover:border-[var(--color-muted)]">
          + Add case
        </button>
        <button onClick={resetCases}
                className="text-xs text-[var(--color-muted)] underline
                           hover:text-[var(--color-ink)]">
          Reset to the original examples
        </button>
      </div>

      <p className="text-xs text-[var(--color-muted)]">
        These run on <strong>Run</strong> only and never affect your score.
        Submit uses the question's own examples plus {question.hidden_test_count}
        {' '}hidden cases.
      </p>
    </div>
  )
}

function CaseRow({ index, value, onChange, onDuplicate, onRemove }: {
  index: number
  value: EditableCase
  onChange: (patch: Partial<EditableCase>) => void
  onDuplicate: () => void
  onRemove?: () => void
}) {
  const errors = caseErrors(value)

  return (
    <div className="rounded border border-[var(--color-edge)] p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-[var(--color-muted)]">
        <span>Case {index + 1}</span>
        <button onClick={onDuplicate} className="ml-auto hover:text-[var(--color-ink)]"
                title="Duplicate">⧉</button>
        {onRemove && (
          <button onClick={onRemove} className="hover:text-[var(--color-fail)]"
                  title="Delete">✕</button>
        )}
      </div>

      {Object.entries(value.args).map(([key, raw]) => (
        <Field key={key} label={key} error={errors[key]}>
          <input
            aria-label={`Case ${index + 1} ${key}`}
            value={raw}
            onChange={(e) => onChange({ args: { ...value.args, [key]: e.target.value } })}
            className={fieldClass(errors[key])}
          />
        </Field>
      ))}

      <label className="mb-1 mt-2 flex items-center gap-2 text-xs
                        text-[var(--color-muted)]">
        <input
          type="checkbox"
          checked={value.assertExpected}
          onChange={(e) => onChange({ assertExpected: e.target.checked })}
        />
        Check against an expected value
      </label>

      {value.assertExpected && (
        <Field label="expected" error={errors.expected}>
          <input
            aria-label={`Case ${index + 1} expected`}
            value={value.expected}
            onChange={(e) => onChange({ expected: e.target.value })}
            className={fieldClass(errors.expected)}
          />
        </Field>
      )}
    </div>
  )
}

function fieldClass(error?: string): string {
  return [
    'w-full rounded bg-[var(--color-panel)] px-3 py-1.5 font-mono text-[13px]',
    'text-[var(--color-ink)] outline-none border',
    error ? 'border-[var(--color-fail)]' : 'border-transparent focus:border-[var(--color-accent)]',
  ].join(' ')
}

function Field({ label, error, children }: {
  label: string; error?: string; children: React.ReactNode
}) {
  return (
    <div className="mb-2">
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-xs text-[var(--color-muted)]">{label} =</span>
        {error && <span className="text-xs text-[var(--color-fail)]">{error}</span>}
      </div>
      {children}
    </div>
  )
}
