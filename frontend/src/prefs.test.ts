import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_LANGUAGE_KEY, readDefaultLanguage } from './prefs'

describe('readDefaultLanguage', () => {
  beforeEach(() => localStorage.clear())

  it('falls back to python when nothing is stored', () => {
    expect(readDefaultLanguage()).toBe('python')
  })

  it('returns a stored language', () => {
    localStorage.setItem(DEFAULT_LANGUAGE_KEY, 'typescript')
    expect(readDefaultLanguage()).toBe('typescript')
  })

  it('ignores a value that is not a language we support', () => {
    localStorage.setItem(DEFAULT_LANGUAGE_KEY, 'malbolge')
    expect(readDefaultLanguage()).toBe('python')
  })

  it('survives storage being unavailable', () => {
    // Private browsing and blocked site data both throw on access.
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    expect(readDefaultLanguage()).toBe('python')
    vi.restoreAllMocks()
  })
})
