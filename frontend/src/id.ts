/** A random id that works outside a secure context.
 *
 * `crypto.randomUUID` is only defined on secure origins, so it is present on
 * https and on localhost but *absent* when the container is reached at
 * http://<host>:8080 -- which is how anyone but the person running Docker sees
 * it. Calling it there throws and kills the click handler, so the button does
 * nothing. `crypto.getRandomValues` has no such restriction.
 *
 * These ids only tag a progress run, so uniqueness is all that is required; the
 * Math.random fallback exists for very old browsers and is never security
 * relevant.
 */
export function randomId(): string {
  const c: Crypto | undefined = globalThis.crypto
  if (typeof c?.randomUUID === 'function') return c.randomUUID()
  if (typeof c?.getRandomValues === 'function') {
    const bytes = c.getRandomValues(new Uint8Array(16))
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 18)}`
}
