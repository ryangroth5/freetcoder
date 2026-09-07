import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ChatMessage, Language } from '../api'
import { useStore } from '../store'

/**
 * A tutor that can see what you see.
 *
 * It has the statement, the verified clarifications, your current code and your
 * last run -- and it can ask the intended solution what it returns for a given
 * input. It cannot read that solution, and it cannot see the hidden cases.
 */
export function TutorPane({ language }: { language: Language }) {
  const { session, index, currentSource } = useStore()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [available, setAvailable] = useState<boolean | null>(null)
  const [reason, setReason] = useState('')
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [partial, setPartial] = useState('')
  const [tools, setTools] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!session) return
    api.chatState(session.id, index).then((state) => {
      setAvailable(state.available)
      setReason(state.reason)
      setMessages(state.messages)
    }).catch(() => setAvailable(false))
  }, [session?.id, index]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages, partial, tools])

  async function send() {
    const text = draft.trim()
    if (!text || !session || streaming) return

    setDraft('')
    setError(null)
    setTools([])
    setPartial('')
    setMessages((prior) => [...prior, { role: 'user', content: text }])
    setStreaming(true)

    let reply = ''
    try {
      for await (const event of api.chat(
        session.id, index, text, currentSource(), language,
      )) {
        if (event.type === 'token') {
          reply += event.text
          setPartial(reply)
        } else if (event.type === 'tool') {
          setTools((prior) => [...prior, event.result])
        } else if (event.type === 'error') {
          setError(event.message)
        }
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setStreaming(false)
      setPartial('')
      // Keep whatever arrived: a truncated answer still beats a blank bubble.
      if (reply) {
        setMessages((prior) => [...prior, { role: 'assistant', content: reply }])
      }
    }
  }

  if (available === false) {
    return (
      <div className="p-4 text-sm text-[var(--color-muted)]">
        <p className="mb-2 font-medium text-[var(--color-ink)]">Tutor locked</p>
        <p>{reason || 'The tutor is not available for this question.'}</p>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && !streaming && (
          <p className="text-xs text-[var(--color-muted)]">
            Ask about the problem, why a case is failing, or what the question
            means. It can check what the intended solution does for a given
            input, but it cannot show you the solution or the hidden cases.
          </p>
        )}

        {messages.map((message, i) => <Bubble key={i} message={message} />)}

        {tools.map((result, i) => (
          <p key={`tool-${i}`} className="text-xs text-[var(--color-muted)]">
            <span className="mr-1">🔎</span>{result}
          </p>
        ))}

        {partial && (
          <Bubble message={{ role: 'assistant', content: partial }} pending />
        )}
        {streaming && !partial && (
          <p className="text-xs text-[var(--color-muted)]">Thinking…</p>
        )}
        {error && (
          <p className="text-xs text-[var(--color-fail)]">{error}</p>
        )}
        <div ref={bottom} />
      </div>

      <div className="flex gap-2 border-t border-[var(--color-edge)] p-3">
        <input
          aria-label="Ask the tutor"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
          placeholder="Ask about this problem…"
          disabled={streaming}
          className="flex-1 rounded border border-[var(--color-edge)]
                     bg-[var(--color-panel)] px-3 py-1.5 text-sm outline-none
                     focus:border-[var(--color-accent)] disabled:opacity-50"
        />
        <button
          onClick={send}
          disabled={streaming || !draft.trim()}
          className="rounded bg-[var(--color-accent)] px-3 py-1 text-sm text-white
                     disabled:opacity-40"
        >
          Ask
        </button>
      </div>
    </div>
  )
}

function Bubble({ message, pending }: { message: ChatMessage; pending?: boolean }) {
  const mine = message.role === 'user'
  return (
    <div data-role={message.role} className={mine ? 'text-right' : ''}>
      <div
        className={[
          'inline-block max-w-[90%] whitespace-pre-wrap rounded px-3 py-2 text-left text-sm',
          mine
            ? 'bg-[var(--color-accent)] text-white'
            : 'bg-[var(--color-panel)] text-[var(--color-ink)]',
          pending ? 'opacity-90' : '',
        ].join(' ')}
      >
        {message.content}
        {pending && <span className="ml-1 animate-pulse">▍</span>}
      </div>
    </div>
  )
}
