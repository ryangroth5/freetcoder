import { useState } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { useStore } from '../store'
import { useTheme } from '../theme'
import { EditorPane } from './EditorPane'
import { ResultsPane } from './ResultsPane'
import { StatementPane } from './StatementPane'
import { Timer } from './Timer'

export function ProblemView() {
  const {
    session, question, index, source, language, report, referenceSolution,
    busy, error, setSource, setLanguage, setReadEditor, run, submit, skip,
    loadQuestion, clearError, casesAreValid, publish, notice, clearNotice,
    nextQuestion,
  } = useStore()
  const [cursor, setCursor] = useState({ line: 1, column: 1 })
  const { resolved, toggle } = useTheme()

  if (!session || !question) {
    return <div className="p-8 text-[var(--color-muted)]">Loading question…</div>
  }

  const multi = session.question_count > 1
  const allowSkip = session.config.session.allow_skip
  const allowRevisit = session.config.session.allow_revisit

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-[var(--color-edge)]
                         bg-[var(--color-panel)] px-3 py-2">
        <span className="font-semibold">freetcoder</span>
        <span className="text-xs text-[var(--color-muted)]">{session.summary}</span>

        <div className="ml-2 flex items-center gap-1">
          {multi && (
            <>
              <IconButton disabled={!allowRevisit || index === 0}
                          onClick={() => loadQuestion(index - 1)} title="Previous">
                ‹
              </IconButton>
              <span className="text-xs text-[var(--color-muted)]">
                {index + 1} / {session.question_count}
              </span>
            </>
          )}
          <IconButton
            disabled={busy}
            onClick={() => nextQuestion()}
            title={multi && index + 1 < session.question_count
              ? 'Next question'
              : 'Generate another question in this format'}
          >
            {busy ? '…' : '›'}
          </IconButton>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Timer />
          <button
            onClick={run}
            disabled={busy || !casesAreValid()}
            title={casesAreValid() ? undefined
                                   : 'A test case field is not valid JSON'}
            className="rounded border border-[var(--color-edge)] px-3 py-1 text-sm
                       hover:border-[var(--color-muted)] disabled:opacity-40"
          >
            ▶ Run
          </button>
          <button onClick={submit} disabled={busy}
                  className="rounded bg-[var(--color-pass)] px-3 py-1 text-sm font-medium
                             text-white disabled:opacity-40">
            Submit
          </button>
          <button
            onClick={toggle}
            aria-label="Toggle theme"
            title={`Switch to ${resolved === 'dark' ? 'light' : 'dark'} mode`}
            className="rounded border border-[var(--color-edge)] px-2 py-1 text-sm
                       text-[var(--color-muted)] hover:text-[var(--color-ink)]"
          >
            {resolved === 'dark' ? '☀' : '☾'}
          </button>
          <button
            onClick={publish}
            disabled={busy}
            title="Save this question to the shared library"
            className="rounded border border-[var(--color-edge)] px-3 py-1 text-sm
                       text-[var(--color-muted)] hover:text-[var(--color-ink)]
                       disabled:opacity-40"
          >
            ☆ Save
          </button>
          {allowSkip && (
            <button onClick={skip} disabled={busy}
                    className="rounded border border-[var(--color-edge)] px-3 py-1 text-sm
                               text-[var(--color-muted)] hover:text-[var(--color-ink)]
                               disabled:opacity-40">
              Skip
            </button>
          )}
        </div>
      </header>

      {notice && (
        <div className="flex items-center gap-3 border-b border-[var(--color-pass)]/40
                        bg-[var(--color-pass)]/10 px-4 py-2 text-sm
                        text-[var(--color-pass)]">
          {notice}
          <button className="ml-auto text-xs underline" onClick={clearNotice}>
            dismiss
          </button>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-3 border-b border-[var(--color-fail)]/40
                        bg-[var(--color-fail)]/10 px-4 py-2 text-sm text-[var(--color-fail)]">
          {error}
          <button className="ml-auto text-xs underline" onClick={clearError}>dismiss</button>
        </div>
      )}

      <Group orientation="horizontal" className="flex-1">
        <Panel defaultSize={42} minSize={20}>
          <StatementPane question={question} referenceSolution={referenceSolution} />
        </Panel>
        <Handle vertical />
        <Panel defaultSize={58} minSize={25}>
          <Group orientation="vertical">
            <Panel defaultSize={62} minSize={20}>
              <div className="flex h-full flex-col">
                <div className="flex items-center gap-2 border-b border-[var(--color-edge)]
                                px-3 py-1.5 text-xs text-[var(--color-muted)]">
                  <span>{'</>'} Code</span>
                  <select
                    aria-label="Language"
                    value={language}
                    onChange={(e) => setLanguage(e.target.value as typeof language)}
                    className="rounded border border-[var(--color-edge)]
                               bg-[var(--color-surface)] px-2 py-0.5
                               text-[var(--color-ink)] outline-none"
                  >
                    {question.languages.map((lang) => (
                      <option key={lang} value={lang}>{lang}</option>
                    ))}
                  </select>
                  <button className="ml-auto hover:text-[var(--color-ink)]"
                          onClick={() => setSource(
                            question.signatures.find((s) => s.language === language)
                              ?.scaffold ?? question.scaffold,
                          )}
                          title="Reset to the starting scaffold">
                    ⟲ Reset
                  </button>
                </div>
                <div className="min-h-0 flex-1">
                  <EditorPane
                    language={language}
                    languages={question.languages}
                    value={source}
                    onChange={setSource}
                    onCursor={(line, column) => setCursor({ line, column })}
                    onReader={setReadEditor}
                    theme={resolved}
                  />
                </div>
                <div className="flex justify-end gap-4 border-t border-[var(--color-edge)]
                                px-3 py-1 text-xs text-[var(--color-muted)]">
                  <span>Ln {cursor.line}, Col {cursor.column}</span>
                </div>
              </div>
            </Panel>
            <Handle />
            <Panel defaultSize={38} minSize={15}>
              <ResultsPane question={question} report={report} busy={busy} />
            </Panel>
          </Group>
        </Panel>
      </Group>
    </div>
  )
}

function Handle({ vertical }: { vertical?: boolean }) {
  return (
    <Separator
      className={`${vertical ? 'w-1 cursor-col-resize' : 'h-1 cursor-row-resize'}
                  bg-[var(--color-edge)] transition-colors
                  hover:bg-[var(--color-accent)]`}
    />
  )
}

function IconButton({ disabled, onClick, title, children }: {
  disabled?: boolean; onClick: () => void; title: string; children: React.ReactNode
}) {
  return (
    <button onClick={onClick} disabled={disabled} title={title}
            className="rounded px-2 py-0.5 text-[var(--color-muted)]
                       hover:text-[var(--color-ink)] disabled:opacity-30">
      {children}
    </button>
  )
}
