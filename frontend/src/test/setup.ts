/** Vitest setup.
 *
 * vite.config.ts has pointed at this file since vitest was configured, but it
 * never existed -- so `npm test` could not run at all. Creating it makes the
 * unit-test lane usable; the browser suite in e2e/ is unaffected.
 */
import '@testing-library/jest-dom/vitest'
