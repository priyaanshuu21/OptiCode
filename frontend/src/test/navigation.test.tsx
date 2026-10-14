import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '@/App'
import { clampRatio, MainWorkspace } from '@/components/MainWorkspace'
import { useCompilerStore } from '@/store/compiler'

/**
 * The shell: the main workspace and the stage screens hanging off it.
 *
 * These tests are about what *survives* navigation, not about what any one screen draws. Choosing
 * a stage replaces the workspace in place, and Back has to land on the same workspace it left —
 * same source, same selection, same editor instance. A shell that remounted the editor to show the
 * tokens would pass every view test in this suite while throwing away the reader's cursor, scroll
 * and undo history on every visit.
 */

// Monaco cannot run under jsdom. The marker stands in for it so the shell around it is the real
// thing: the menu, the stage screens, the Back button, the context that threads them.
vi.mock('@/components/SourceEditor', () => ({
  SourceEditor: () => <div data-testid="source-editor" />,
}))

function reset(): void {
  useCompilerStore.setState({
    source: 'int main() { return 0; }',
    compiledSource: null,
    response: null,
    status: 'idle',
    error: null,
    activeStage: null,
    selection: null,
    selectedBlock: null,
    examples: [],
    examplesError: null,
    optimizationPreview: null,
    previewStatus: 'idle',
    previewError: null,
  })
}

describe('workspace navigation', () => {
  beforeEach(() => {
    reset()
    // No backend in these tests. Every caller in the shell already degrades gracefully, which is
    // the state the app opens in when the compiler is not running.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('no backend')
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('opens a stage screen and returns to the workspace with state intact', async () => {
    useCompilerStore.setState({ selection: { line: 1, endLine: 1, origin: 'test' } })
    render(<App />)

    // The workspace is what opens: editor and menu, side by side.
    const editor = screen.getByTestId('source-editor')
    expect(editor).toBeInTheDocument()
    expect(screen.getByTestId('main-workspace')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Data Flow/ }))
    expect(useCompilerStore.getState().activeStage).toBe('dataflow')

    // The stage screen owns a Back control, and the workspace is hidden rather than gone.
    const back = screen.getByRole('button', { name: 'Back' })
    expect(back).toBeInTheDocument()
    expect(useCompilerStore.getState().source).toBe('int main() { return 0; }')

    await userEvent.click(back)
    expect(useCompilerStore.getState().activeStage).toBeNull()

    // Back lands on the same workspace: same editor node, same source, same selection.
    expect(screen.getByTestId('source-editor')).toBe(editor)
    expect(screen.getByRole('button', { name: /Data Flow/ })).toBeInTheDocument()
    expect(useCompilerStore.getState().selection).toEqual({ line: 1, endLine: 1, origin: 'test' })
  })

  it('offers every stage from the menu and reaches each one', async () => {
    render(<App />)

    for (const label of ['Tokens', 'AST', 'TAC', 'CFG', 'Optimization', 'Diagnostics']) {
      await userEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}\\b`) }))
      expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: 'Back' }))
      expect(useCompilerStore.getState().activeStage).toBeNull()
    }
  })

  it('renders the draggable split between the editor and the menu', () => {
    render(<MainWorkspace />)
    expect(
      screen.getByRole('separator', { name: /Resize the source and stage menu/ }),
    ).toBeInTheDocument()
  })
})

describe('clampRatio', () => {
  it('keeps both panes at or above their minimum width', () => {
    // A 1000px window: the editor may not go below 320/1000 = 0.32, and the menu reserves
    // 280/1000 = 0.28, so the split may not exceed 0.72.
    expect(clampRatio(0.1, 1000)).toBeCloseTo(0.32)
    expect(clampRatio(0.9, 1000)).toBeCloseTo(0.72)
    expect(clampRatio(0.58, 1000)).toBeCloseTo(0.58)
  })

  it('falls back to an even split when the viewport cannot fit both minimums', () => {
    // 500px cannot hold a 320px editor and a 280px menu, so no clamp is meaningful.
    expect(clampRatio(0.9, 500)).toBe(0.5)
  })

  it('uses the default for a zero or invalid width', () => {
    expect(clampRatio(0.9, 0)).toBe(0.58)
    expect(clampRatio(Number.NaN, 1000)).toBe(0.58)
  })
})
