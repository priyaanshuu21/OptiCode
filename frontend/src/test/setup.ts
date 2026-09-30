import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

/**
 * Test setup, shared by every suite.
 *
 * jsdom does not implement the handful of browser APIs that Monaco and React Flow reach for,
 * so they are stubbed here once rather than per suite. Notably `ResizeObserver`, which both
 * libraries use to size themselves; without it React Flow throws on mount and the failure has
 * nothing to do with the code under test.
 */

class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver ??= ResizeObserverStub

// React Flow measures the container it is given; jsdom always reports zero.
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 800 })
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 600 })

// Monaco's web worker and language services do not exist under jsdom. Monaco loads them by
// URL, and a failed fetch logs a stack trace that drowns out real failures.
globalThis.MonacoEnvironment = {
  getWorker: () => new Worker(new URL('./empty.worker.ts', import.meta.url), { type: 'module' }),
  getWorkerUrl: () => '',
}

// jsdom has no layout engine, so `scrollIntoView` and friends are missing.
Element.prototype.scrollIntoView ??= function scrollIntoView(): void {}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})
