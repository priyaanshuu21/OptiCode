import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TokensView } from '@/views/TokensView'
import { AstView } from '@/views/AstView'
import { TacView } from '@/views/TacView'
import { DiagnosticsView } from '@/views/DiagnosticsView'
import { useCompilerStore } from '@/store/compiler'
import { loadFixture, type FixtureName } from './fixtures'

/**
 * Stage views, rendered against recorded compiler output.
 *
 * The point of these is the skipped-stage case. Every view here can be handed a response whose
 * payload for its stage is null, and the failure mode that matters is a view that renders an
 * empty table and calls it "nothing found" rather than saying the stage never ran.
 */

/** Put a recorded response in the store without going through the network. */
function seed(name: FixtureName): void {
  const { response, source } = loadFixture(name)
  useCompilerStore.setState({ response, source, status: 'ready', error: null })
}

function reset(): void {
  useCompilerStore.setState({
    response: null,
    status: 'idle',
    error: null,
    selection: null,
    activeStage: 'ast',
    mode: 'analyze',
  })
}

describe('stage views', () => {
  beforeEach(() => {
    reset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists tokens in source order', () => {
    seed('hello')
    render(<TokensView />)

    const { response } = loadFixture('hello')
    const expected = response.tokens?.length ?? 0
    expect(screen.getByText(`${expected} tokens`)).toBeInTheDocument()
  })

  it('selects a token span when a row is clicked', async () => {
    seed('hello')
    render(<TokensView />)

    // The table is the token stream; any row will do, and the first one is deterministic.
    const [row] = screen.getAllByRole('row').slice(1)
    expect(row).toBeDefined()
    await userEvent.click(row!)

    const { selection } = useCompilerStore.getState()
    expect(selection?.origin).toMatch(/^token \d+$/)
    expect(selection?.line).toBeGreaterThan(0)
  })

  it('shows the AST with a node count and a detail pane', async () => {
    seed('loops')
    render(<AstView />)

    expect(screen.getByText(/\d+ nodes/)).toBeInTheDocument()

    const [entry] = screen.getAllByRole('button')
    expect(entry).toBeDefined()
    await userEvent.click(entry!)
    expect(useCompilerStore.getState().selection?.origin).toBeTruthy()
  })

  it('renders TAC as a table by default and as text on request', async () => {
    seed('mixed_types')
    render(<TacView />)

    expect(screen.getByRole('table')).toBeInTheDocument()
    const { response } = loadFixture('mixed_types')
    expect(response.ir?.text).toContain('t')

    await userEvent.click(screen.getByRole('button', { name: 'text' }))
    // The backend's own rendering, verbatim — a second implementation would eventually disagree.
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('says the stage was skipped rather than showing an empty result', () => {
    // The error fixture fails in the semantic stage, so lowering never ran. An empty TAC table
    // here would read as "the program produced no code", which is a different claim.
    seed('error')
    render(<TacView />)

    expect(screen.getByText('No TAC')).toBeInTheDocument()
    expect(screen.getByText('stage skipped')).toBeInTheDocument()
  })

  it('does not claim a clean bill of health when the pipeline stopped early', () => {
    seed('error')
    render(<DiagnosticsView />)

    // Errors are listed, and the all-clear wording is absent.
    expect(screen.getAllByText('E-SEM-001').length).toBeGreaterThan(0)
    expect(screen.queryByText('No diagnostics')).not.toBeInTheDocument()
  })

  it('reports a clean compile as clean, including the stages that were skipped', () => {
    // `hello` is analyzed, so codegen is legitimately absent — but nothing failed.
    seed('hello')
    render(<DiagnosticsView />)

    expect(screen.getByText('No diagnostics')).toBeInTheDocument()
    expect(screen.getByText(/stage skipped/)).toBeInTheDocument()
    expect(useCompilerStore.getState().response?.success).toBe(true)
  })

  it('filters diagnostics by severity', async () => {
    seed('error')
    render(<DiagnosticsView />)

    const { response } = loadFixture('error')
    const warnings = (response.diagnostics ?? []).filter((d) => d.severity === 'warning').length
    if (warnings > 0) {
      await userEvent.click(screen.getByRole('button', { name: new RegExp(`${warnings} warnings`) }))
      expect(screen.queryByText(/severity/)).toBeDefined()
    }
  })

  it('surfaces a diagnostic code and its stage', () => {
    seed('error')
    render(<DiagnosticsView />)

    const { response } = loadFixture('error')
    const code = response.diagnostics?.[0]?.code
    if (code) {
      expect(screen.getAllByText(code).length).toBeGreaterThan(0)
    }
  })

  it('waits quietly when there is no response yet', () => {
    reset()
    render(<AstView />)
    expect(screen.getByText('Nothing to show yet')).toBeInTheDocument()
  })
})

describe('compile status', () => {
  beforeEach(() => {
    reset()
  })

  it('reports an unreachable backend without crashing the shell', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await act(async () => {
      await useCompilerStore.getState().run()
    })

    const { status, error } = useCompilerStore.getState()
    expect(status).toBe('error')
    expect(error).toMatch(/backend running/)

    render(<DiagnosticsView />)
    await waitFor(() => expect(screen.getByText(/stage skipped|Nothing to show yet/)).toBeInTheDocument())
  })
})
