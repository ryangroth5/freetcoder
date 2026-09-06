import { useState } from 'react'
import type { Question } from '../api'
import { Markdown } from './Markdown'

type Tab = 'description' | 'solution'

const DIFFICULTY_STYLE: Record<string, string> = {
  easy: 'text-[var(--color-pass)] bg-[var(--color-pass)]/10',
  medium: 'text-[var(--color-warn)] bg-[var(--color-warn)]/10',
  hard: 'text-[var(--color-fail)] bg-[var(--color-fail)]/10',
}

export function StatementPane({ question, referenceSolution }: {
  question: Question
  referenceSolution: string | null
}) {
  const [tab, setTab] = useState<Tab>('description')
  const [showHint, setShowHint] = useState(false)
  const [showTopics, setShowTopics] = useState(false)

  return (
    <div className="flex h-full flex-col bg-[var(--color-surface)]">
      <div className="flex gap-1 border-b border-[var(--color-edge)] px-3">
        <TabButton active={tab === 'description'} onClick={() => setTab('description')}>
          Description
        </TabButton>
        <TabButton
          active={tab === 'solution'}
          disabled={!referenceSolution}
          // Locked until submit or skip: seeing it earlier defeats the exercise.
          title={referenceSolution ? undefined : 'Unlocks after you submit or skip'}
          onClick={() => referenceSolution && setTab('solution')}
        >
          Solution {referenceSolution ? '' : '🔒'}
        </TabButton>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {tab === 'description' ? (
          <>
            <h1 className="text-xl font-semibold">
              {question.index + 1}. {question.title}
            </h1>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium capitalize
                                ${DIFFICULTY_STYLE[question.difficulty]}`}>
                {question.difficulty}
              </span>
              {question.topics.length > 0 && (
                <Chip onClick={() => setShowTopics(!showTopics)}>
                  Topics {showTopics ? '▾' : '▸'}
                </Chip>
              )}
              {question.hint_md && (
                <Chip onClick={() => setShowHint(!showHint)}>
                  💡 Hint {showHint ? '▾' : '▸'}
                </Chip>
              )}
            </div>

            {showTopics && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {question.topics.map((t) => (
                  <span key={t} className="rounded bg-[var(--color-panel)] px-2 py-0.5 text-xs">
                    {t}
                  </span>
                ))}
              </div>
            )}
            {showHint && question.hint_md && (
              <div className="mt-2 rounded border border-[var(--color-edge)]
                              bg-[var(--color-panel)] p-3 text-sm">
                <Markdown>{question.hint_md}</Markdown>
              </div>
            )}

            <div className="mt-4">
              <Markdown>{question.statement_md}</Markdown>
            </div>

            {question.visible_tests.map((test, i) => (
              <div key={i} className="mt-4">
                <div className="font-semibold">Example {i + 1}:</div>
                <div className="mt-1 border-l-2 border-[var(--color-edge)] py-1 pl-4
                                font-mono text-[13px]">
                  <div><span className="font-semibold">Input: </span>
                    {Object.entries(test.args)
                      .map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join(', ')}
                  </div>
                  <div><span className="font-semibold">Output: </span>
                    {JSON.stringify(test.expected)}
                  </div>
                  {test.explanation && (
                    <div className="mt-1 font-sans text-[var(--color-muted)]">
                      <span className="font-semibold">Explanation: </span>{test.explanation}
                    </div>
                  )}
                </div>
              </div>
            ))}

            <div className="mt-5">
              <div className="font-semibold">Constraints:</div>
              <Markdown>{question.constraints_md}</Markdown>
            </div>

            {question.complexity_target && (
              <div className="mt-4 rounded border border-[var(--color-warn)]/40
                              bg-[var(--color-warn)]/10 p-3 text-sm">
                <span className="font-semibold">Expected complexity: </span>
                <code>{question.complexity_target}</code>
                <div className="mt-1 text-xs text-[var(--color-muted)]">
                  Performance is graded — a correct but slower solution loses marks.
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            <h2 className="text-lg font-semibold">Reference solution</h2>
            <pre className="mt-3 overflow-x-auto rounded bg-[var(--color-panel)] p-3
                            font-mono text-[13px]">
              {referenceSolution}
            </pre>
          </>
        )}
      </div>
    </div>
  )
}

function TabButton({ active, disabled, title, onClick, children }: {
  active: boolean; disabled?: boolean; title?: string
  onClick: () => void; children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={[
        'border-b-2 px-3 py-2 text-sm transition-colors',
        active ? 'border-[var(--color-accent)] text-white'
               : 'border-transparent text-[var(--color-muted)] hover:text-[var(--color-ink)]',
        disabled ? 'cursor-not-allowed opacity-50' : '',
      ].join(' ')}
    >
      {children}
    </button>
  )
}

function Chip({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
            className="rounded-full border border-[var(--color-edge)] px-2.5 py-0.5
                       text-xs hover:border-[var(--color-muted)]">
      {children}
    </button>
  )
}
