import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Markdown } from './Markdown'

/**
 * The tutor writes markdown whether or not anyone asked it to — backticks
 * around identifiers, a numbered list of steps, the occasional fenced snippet.
 * The chat bubble rendered it as plain text, so it arrived as literal asterisks
 * and stray backticks.
 *
 * This covers the renderer the bubble now uses; what the bubble itself decides
 * (markdown for the tutor, verbatim for the candidate) is asserted below.
 */
describe('tutor replies render as markdown', () => {
  it('turns backticked identifiers into code, not literal backticks', () => {
    render(<Markdown>{'Try `nums[i]` first.'}</Markdown>)
    expect(screen.getByText('nums[i]').tagName).toBe('CODE')
    expect(screen.queryByText(/`nums/)).toBeNull()
  })

  it('renders a numbered list as a list', () => {
    const { container } = render(
      <Markdown>{'1. read the failing case\n2. check the bounds\n'}</Markdown>,
    )
    expect(container.querySelectorAll('ol li')).toHaveLength(2)
  })

  it('renders a fenced snippet as a code block', () => {
    const { container } = render(
      <Markdown>{'```python\nfor i in range(n):\n    pass\n```'}</Markdown>,
    )
    expect(container.querySelector('pre code')?.textContent)
      .toContain('for i in range(n):')
  })

  it('survives a half-streamed fence', () => {
    // Replies stream in, so the renderer sees unterminated markdown between
    // chunks and must not throw.
    expect(() => render(<Markdown>{'here:\n```python\nfor i in ra'}</Markdown>))
      .not.toThrow()
  })

  it('does not execute embedded html', () => {
    const { container } = render(
      <Markdown>{'<img src=x onerror="alert(1)">'}</Markdown>,
    )
    expect(container.querySelector('img')).toBeNull()
  })
})
