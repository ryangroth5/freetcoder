import { describe, it, expect } from 'vitest'
import { stepTimings } from './GenerationProgress'

/**
 * `Step.at` is when a step started, not how long it took. Rendering it
 * directly meant the first step read "0.0s" for the life of the run -- a
 * stopped clock next to the thing a reader most wants to know.
 */
describe('stepTimings', () => {
  const steps = [{ at: 0 }, { at: 2.5 }, { at: 4 }]

  it('gives a finished step the gap to the next one', () => {
    const got = stepTimings(steps, 9, false)
    expect(got[0].took).toBeCloseTo(2.5)
    expect(got[1].took).toBeCloseTo(1.5)
  })

  it('counts the last step against the live clock while the run is going', () => {
    expect(stepTimings(steps, 9, false)[2]).toEqual({ took: 5, running: true })
    expect(stepTimings(steps, 12, false)[2].took).toBe(8)
  })

  it('stops the last step once the run has finished', () => {
    const got = stepTimings(steps, 9, true)
    expect(got[2]).toEqual({ took: 5, running: false })
    expect(got.some(t => t.running)).toBe(false)
  })

  it('never shows a negative duration', () => {
    // A poll can return steps a moment before `elapsed` catches up.
    expect(stepTimings(steps, 3.9, false)[2].took).toBe(0)
  })

  it('handles a run with nothing reported yet', () => {
    expect(stepTimings([], 1.2, false)).toEqual([])
  })
})
