import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeSanitize from 'rehype-sanitize'
import mermaid from 'mermaid'

mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  // Statements are LLM-generated, so the renderer runs locked down.
  securityLevel: 'strict',
})

/** Renders one ```mermaid fence, falling back to the source on a syntax error. */
function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    const id = `m${Math.random().toString(36).slice(2)}`
    mermaid
      .render(id, code)
      .then(({ svg }) => {
        if (!cancelled && ref.current) ref.current.innerHTML = svg
      })
      .catch(() => !cancelled && setFailed(true))
    return () => { cancelled = true }
  }, [code])

  if (failed) {
    return <pre className="overflow-x-auto rounded bg-[var(--color-panel)] p-3">{code}</pre>
  }
  return <div ref={ref} className="my-4 flex justify-center overflow-x-auto" />
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-invert max-w-none leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{
          code({ className, children, ...props }) {
            const language = /language-(\w+)/.exec(className ?? '')?.[1]
            const text = String(children).replace(/\n$/, '')
            if (language === 'mermaid') return <MermaidBlock code={text} />
            if (!language) {
              return (
                <code className="rounded bg-[var(--color-panel)] px-1.5 py-0.5
                                 font-mono text-[0.9em]" {...props}>
                  {children}
                </code>
              )
            }
            return (
              <pre className="my-3 overflow-x-auto rounded bg-[var(--color-panel)] p-3">
                <code className="font-mono text-[13px]">{text}</code>
              </pre>
            )
          },
          h1: (p) => <h1 className="mb-3 mt-5 text-lg font-semibold" {...p} />,
          h2: (p) => <h2 className="mb-2 mt-5 text-base font-semibold" {...p} />,
          p: (p) => <p className="my-3" {...p} />,
          ul: (p) => <ul className="my-3 list-disc space-y-1 pl-6" {...p} />,
          ol: (p) => <ol className="my-3 list-decimal space-y-1 pl-6" {...p} />,
          table: (p) => (
            <div className="my-3 overflow-x-auto">
              <table className="border-collapse text-sm" {...p} />
            </div>
          ),
          th: (p) => <th className="border border-[var(--color-edge)] px-2 py-1" {...p} />,
          td: (p) => <td className="border border-[var(--color-edge)] px-2 py-1" {...p} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
