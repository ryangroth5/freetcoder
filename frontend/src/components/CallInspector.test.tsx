import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { CallInspector } from './CallInspector'
import type { ProgressCall } from '../api'

function call(overrides: Partial<ProgressCall> = {}): ProgressCall {
  return {
    stage: 'module', model: 'deepseek/deepseek-v4.1-flash', served_by: 'DeepSeek',
    mode: 'text', seconds: 12.5, ttft_s: 0.9, tokens_streamed: 420,
    tokens_per_s: 36.2, longest_gap_s: 1.1, outcome: 'ok', error: '',
    in_flight: false, prompt: 'THE PROMPT', reply: 'THE REPLY',
    prompt_tokens: 900, completion_tokens: 410, ...overrides,
  }
}

describe('CallInspector', () => {
  beforeEach(() => {
    cleanup()
    localStorage.clear()
  })

  it('is closed by default', () => {
    render(<CallInspector calls={[call()]} />)
    expect(screen.getByRole('button', { name: /Inspect model calls \(1\)/ }))
      .toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('THE PROMPT')).toBeNull()
  })

  it('opens, and remembers that it was opened', () => {
    const first = render(<CallInspector calls={[call()]} />)
    fireEvent.click(screen.getByRole('button', { name: /Inspect model calls/ }))
    expect(screen.getByText(/deepseek-v4.1-flash → DeepSeek/)).toBeInTheDocument()
    first.unmount()

    render(<CallInspector calls={[call()]} />)
    expect(screen.getByRole('button', { name: /Inspect model calls/ }))
      .toHaveAttribute('aria-expanded', 'true')
  })

  it('shows the prompt and reply when a call is expanded', () => {
    localStorage.setItem('freetcoder.inspectorOpen', '1')
    render(<CallInspector calls={[call()]} />)
    fireEvent.click(screen.getByText(/deepseek-v4.1-flash → DeepSeek/))
    expect(screen.getByText('THE PROMPT')).toBeInTheDocument()
    expect(screen.getByText('THE REPLY')).toBeInTheDocument()
  })

  it('makes a stall visible as waiting with no first token', () => {
    localStorage.setItem('freetcoder.inspectorOpen', '1')
    render(<CallInspector calls={[call({
      outcome: 'running', in_flight: true, ttft_s: null, seconds: 27,
      tokens_streamed: 0, reply: '',
    })]} />)
    expect(screen.getByText(/ttft waiting 27s/)).toBeInTheDocument()
  })
})
