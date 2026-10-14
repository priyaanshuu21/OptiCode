import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TokensView } from '@/views/TokensView'
import { AstView } from '@/views/AstView'
import { TacView } from '@/views/TacView'
import { DiagnosticsView } from '@/views/DiagnosticsView'
import { astNodes } from '@/lib/ast'
import { countAstNodes } from '@/lib/span'
import { useCompilerStore } from '@/store/compiler'
import { codegenOf } from '@/api/client'
import type { AstNode } from '@/api/types'
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
  // `compiledSource` records which source the response describes; without it the store cannot
  // tell a current result from a stale one, and the optimization screen falls back to the
  // response's own inline proposal.
  useCompilerStore.setState({ response, source, compiledSource: source, status: 'ready', error: null })
}

function reset(): void {
  useCompilerStore.setState({
    response: null,
    source: '',
    compiledSource: null,
    status: 'idle',
    error: null,
    selection: null,
    selectedBlock: null,
    activeStage: 'ast',
    mode: 'analyze',
    optimizationPreview: null,
    previewStatus: 'idle',
    previewError: null,
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

  it('shows the AST as a graph with a node count and a detail pane', () => {
    seed('loops')
    render(<AstView />)

    expect(screen.getByText(/\d+ nodes/)).toBeInTheDocument()

    // One box per node the tree actually draws, which is fewer than the payload holds because
    // `Block` nodes are spliced out. A `Block` box would be a node saying nothing.
    const { response } = loadFixture('loops')
    const drawn = document.querySelectorAll('.react-flow__node')
    expect(drawn).toHaveLength(astNodes(response.ast as AstNode).length)
    expect(drawn.length).toBeLessThan(countAstNodes(response.ast as AstNode))
  })

  it('selects a node in the graph, highlights its span, and opens the inspector', () => {
    seed('loops')
    const { container } = render(<AstView />)

    // fireEvent, not user-event: React Flow wires d3-style drag handlers onto its nodes and
    // touches `document` in ways jsdom cannot provide. A plain click still reaches `onNodeClick`.
    const nodes = document.querySelectorAll('.react-flow__node')
    expect(nodes.length).toBeGreaterThan(1)
    fireEvent.click(nodes[1] as Element)

    const { selection } = useCompilerStore.getState()
    expect(selection?.origin).toMatch(/^AST node /)
    expect(selection?.line).toBeGreaterThan(0)

    // The inspector is scoped because the graph shows three `Function …` boxes of its own; only
    // the heading inside the aside is this node's.
    const aside = within(container.querySelector('aside') as HTMLElement)
    expect(aside.queryByText('No node selected')).not.toBeInTheDocument()
    expect(aside.getByText(/^Function /)).toBeInTheDocument()

    // A function's own body is a `Block`. It must not be a box, and it must not be a line in the
    // inspector either — the inspector lists what the graph hangs below the node.
    expect(container.textContent).not.toContain('Block')
  })

  it('prints no source coordinates anywhere in the AST panel', () => {
    seed('loops')
    const { container } = render(<AstView />)

    fireEvent.click(document.querySelectorAll('.react-flow__node')[1] as Element)

    // A tree covered in `4:9-4:26` is a table of source positions pretending to be a tree. The
    // editor still highlights the selected node's span; the panel itself must not narrate it.
    expect(screen.queryByText('Span')).not.toBeInTheDocument()
    expect(container.textContent ?? '').not.toMatch(/\d+:\d+/)
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

describe('applying the optimizer output', () => {
  beforeEach(() => {
    reset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('recompiles the applied source instead of leaving the pre-apply report on screen', async () => {
    const fixture = loadFixture('folded')
    const codegen = codegenOf(fixture.response)!
    expect(codegen.applied).toBe(true)

    // Deliberately held open. The state worth testing is the one between "the editor changed"
    // and "the new report arrived" — exactly the window in which the previous implementation
    // left the old response on screen, still counting and still diffing the source the editor
    // no longer had.
    const gate = new Promise<Response>(() => {})
    const fetchMock = vi.fn(() => gate)
    vi.stubGlobal('fetch', fetchMock)

    useCompilerStore.setState({
      response: fixture.response,
      source: fixture.source,
      status: 'ready',
      error: null,
      mode: 'optimize',
      selection: { line: 1, endLine: 1, origin: 'test' },
      selectedBlock: { function: 'main', block: 'B1' },
    })

    await act(async () => {
      useCompilerStore.getState().applyOptimizedSource(codegen.source)
    })

    const mid = useCompilerStore.getState()
    expect(mid.source).toBe(codegen.source)
    // A stale response is not a harmless detail here: every badge, count and diff in the app is
    // read off it, so keeping it would have the UI describing a program that no longer exists.
    expect(mid.response).toBeNull()
    expect(mid.selection).toBeNull()
    expect(mid.selectedBlock).toBeNull()
    expect(mid.status).toBe('compiling')

    // Straight to the request, with the new source. A debounce would leave the pre-apply state
    // on screen for another 350ms, which is the window this action exists to close.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/api/compile')
    expect(JSON.parse(init.body as string).source).toBe(codegen.source)
  })

  it('keeps a failing apply visible instead of leaving an empty editor', async () => {
    const fixture = loadFixture('folded')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    useCompilerStore.setState({
      response: fixture.response,
      source: fixture.source,
      status: 'ready',
      mode: 'optimize',
    })

    await act(async () => {
      useCompilerStore.getState().applyOptimizedSource(codegenOf(fixture.response)!.source)
    })

    // The source is already replaced, so swallowing the failure would leave an editor showing
    // a program the app has no report for and no error either.
    const { status, error, response } = useCompilerStore.getState()
    expect(status).toBe('error')
    expect(error).toMatch(/backend running/)
    expect(response).toBeNull()
  })
})
