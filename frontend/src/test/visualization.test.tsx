import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { CfgView } from '@/views/CfgView'
import { DataFlowView } from '@/views/DataFlowView'
import { FindingsView } from '@/views/FindingsView'
import { OptimizationView } from '@/views/OptimizationView'
import { codegenOf, optimizationOf, stageSkipped } from '@/api/client'
import { useCompilerStore } from '@/store/compiler'
import { loadFixture, type FixtureName } from './fixtures'

/**
 * The visualization views, rendered against recorded compiler output.
 *
 * These are the views whose failure mode is silence. A CFG with no nodes, a data-flow table with
 * no rows, a findings panel with an empty list and an optimizer report with no passes all look
 * like a compiler that found nothing — which is a claim, not a default. So the assertions here
 * are mostly about the difference between "there is nothing here" and "this never ran", and
 * about the report stating its own coverage rather than only its wins.
 */

function seed(name: FixtureName): void {
  const { response, source } = loadFixture(name)
  useCompilerStore.setState({ response, source, status: 'ready', error: null, selection: null, selectedBlock: null })
}

function reset(): void {
  useCompilerStore.setState({
    response: null,
    status: 'idle',
    error: null,
    selection: null,
    selectedBlock: null,
    activeStage: 'cfg',
    mode: 'analyze',
  })
}

describe('CFG view', () => {
  beforeEach(reset)

  it('lays out a block per basic block and selects one', async () => {
    seed('loops')
    render(<CfgView />)

    const { cfg } = loadFixture('loops').response
    const blockCount = cfg?.main?.blocks.length ?? 0
    expect(blockCount).toBeGreaterThan(0)

    // Selecting a node has to move the source selection too, or the graph and the editor are
    // two disconnected pictures of the same program.
    const nodes = document.querySelectorAll('.react-flow__node')
    expect(nodes.length).toBe(blockCount)

    // fireEvent, not user-event: React Flow wires d3-drag mousedown handlers onto its
    // nodes, and d3 touches `document` in ways jsdom cannot provide. A plain click event
    // still reaches React Flow's onNodeClick without tripping the drag machinery.
    fireEvent.click(nodes[1] as Element)
    expect(useCompilerStore.getState().selectedBlock).not.toBeNull()
    expect(useCompilerStore.getState().selection?.line).toBeGreaterThan(0)
  })

  it('says the stage was skipped rather than showing an empty graph', () => {
    seed('error')
    render(<CfgView />)

    expect(stageSkipped(loadFixture('error').response, 'cfg')).toBe(true)
    expect(screen.getByText('No graph')).toBeInTheDocument()
    expect(screen.getByText('stage skipped')).toBeInTheDocument()
    // An empty canvas here would read as "the program has no branches", which is a different
    // claim from "this stage never ran".
    expect(document.querySelectorAll('.react-flow__node')).toHaveLength(0)
  })

  it('explains that there is nothing to draw before the first compile', () => {
    render(<CfgView />)
    expect(screen.getByText('Nothing to show yet')).toBeInTheDocument()
  })
})

describe('data-flow view', () => {
  beforeEach(reset)

  it('shows a table of entry and exit values for every block', () => {
    seed('loops')
    render(<DataFlowView />)

    const { cfg, dataflow } = loadFixture('loops').response
    const main = cfg?.main
    expect(main).toBeDefined()

    const table = screen.getByRole('table')
    // A header row plus one row per block: the table has to cover the whole function, because a
    // partial table invites the reader to assume the missing blocks were uninteresting.
    expect(within(table).getAllByRole('row')).toHaveLength((main?.blocks.length ?? 0) + 1)
    expect(dataflow?.main?.live_in).toBeDefined()
  })

  it('switches analyses and shows the matching values', async () => {
    seed('dead_code')
    render(<DataFlowView />)

    // The selected function defaults to the first alphabetically, which is `dead_in_branch`.
    const before = screen.getByRole('table').textContent
    await userEvent.click(screen.getByRole('button', { name: 'Reaching defs' }))
    const after = screen.getByRole('table').textContent

    // Liveness and reaching defs answer different questions; showing one under the other's
    // heading would be confidently wrong rather than merely empty.
    expect(before).not.toBe(after)
  })

  it('steps through the solver trace of a function that needed several passes', async () => {
    // `main` in `loops` converges in a single block update, which would make the stepper look
    // like it does nothing. `sum_while` needed eight, so the replay has something to show.
    seed('loops')
    useCompilerStore.setState({ selectedBlock: { function: 'sum_while', block: 'B1' } })
    render(<DataFlowView />)

    const trace = loadFixture('loops').response.dataflow?.sum_while?.traces?.liveness
    expect((trace?.steps.length ?? 0)).toBeGreaterThan(1)

    // It starts at the first update, not at a random one.
    expect(screen.getByText(`step 1 of ${trace?.steps.length}`)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Next iteration' }))
    expect(screen.getByText(`step 2 of ${trace?.steps.length}`)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Last iteration' }))
    expect(
      screen.getByText(`step ${trace?.steps.length} of ${trace?.steps.length}`),
    ).toBeInTheDocument()
  })

  it('labels a truncated trace instead of implying the replay is complete', () => {
    seed('loops')
    useCompilerStore.setState({ selectedBlock: { function: 'sum_while', block: 'B1' } })
    render(<DataFlowView />)

    // The recorded trace is not truncated, so the honest thing is to say nothing about the cap.
    // A view that always printed "showing the first N" would be asserting a limit that is not
    // there, and a reader would distrust the steps it *is* showing.
    const trace = loadFixture('loops').response.dataflow?.sum_while?.traces?.liveness
    expect(trace?.trace_truncated).toBe(false)
    expect(screen.queryByText(/showing the first/)).not.toBeInTheDocument()
  })

  it('says when a trace was cut short, rather than presenting a partial replay as complete', () => {
    const fixture = loadFixture('loops')
    const facts = fixture.response.dataflow as unknown as Record<string, Record<string, unknown>>
    const original = facts.sum_while?.traces as unknown as Record<string, { steps: unknown[]; trace_truncated: boolean }>
    const liveness = original.liveness
    expect(liveness?.steps.length).toBeGreaterThan(2)
    facts.sum_while = {
      ...facts.sum_while,
      traces: {
        ...original,
        // Halve the recorded steps and mark it truncated, which is exactly what the backend
        // does when a long-running function hits the cap.
        liveness: { ...liveness, steps: liveness!.steps.slice(0, 2), trace_truncated: true },
      },
    }
    useCompilerStore.setState({
      response: { ...fixture.response, dataflow: facts as unknown as typeof fixture.response.dataflow },
      source: fixture.source,
      status: 'ready',
      selectedBlock: { function: 'sum_while', block: 'B1' },
    })
    render(<DataFlowView />)

    expect(screen.getByText(/showing the first 2/)).toBeInTheDocument()
    // The true iteration count is still shown, because the cap limits what is *replayed*, not
    // what the solver did.
    expect(screen.getByText(/^8 block updates/)).toBeInTheDocument()
  })

  it('says the stage was skipped rather than showing an empty table', () => {
    seed('error')
    render(<DataFlowView />)

    expect(screen.getByText('No facts')).toBeInTheDocument()
    expect(screen.getByText('stage skipped')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

describe('findings view', () => {
  beforeEach(reset)

  it('groups findings by kind and expands one group', async () => {
    seed('dead_code')
    render(<FindingsView />)

    const report = loadFixture('dead_code').response.analysis
    expect(report?.finding_count).toBeGreaterThan(0)

    // Collapsed by default: a program with hundreds of dead assignments should not bury the
    // other stages under a wall of text.
    const toggle = screen.getByRole('button', { name: /Dead assignment/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    await userEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  it('shows the evidence behind a claim, not just the claim', async () => {
    seed('dead_code')
    render(<FindingsView />)
    await userEvent.click(screen.getByRole('button', { name: /Dead assignment/i }))

    const finding = loadFixture('dead_code').response.analysis?.findings[0]
    expect(finding?.explanation).toBeTruthy()
    expect(finding?.evidence.length).toBeGreaterThan(0)
    // A finding with evidence is checkable; a bare verdict is just an assertion. The message
    // repeats across findings in this fixture, so the first match is the one under test.
    expect(screen.getAllByText(finding!.message).length).toBeGreaterThan(0)
    expect(screen.getByText(finding!.evidence[0]!.note)).toBeInTheDocument()
  })

  it('moves the source selection when a finding is clicked', async () => {
    seed('dead_code')
    render(<FindingsView />)
    await userEvent.click(screen.getByRole('button', { name: /Dead assignment/i }))

    const finding = loadFixture('dead_code').response.analysis?.findings[0]
    await userEvent.click(screen.getAllByText(finding!.message)[0] as Element)

    expect(useCompilerStore.getState().selection?.line).toBe(finding?.span?.start_line)
  })

  it('does not claim a clean bill of health when the analysis stage was skipped', () => {
    seed('error')
    render(<FindingsView />)

    // The error fixture never reached analysis. "No dead code" would be a claim about a
    // program the compiler never finished looking at.
    expect(screen.getByText('No findings')).toBeInTheDocument()
    expect(screen.getByText('stage skipped')).toBeInTheDocument()
  })
})

describe('optimizer view', () => {
  beforeEach(reset)

  it('reports the passes, the size change, and what it skipped', () => {
    seed('loops')
    render(<OptimizationView />)

    const report = optimizationOf(loadFixture('loops').response)
    expect(report?.passes.length).toBeGreaterThan(0)

    for (const pass of report?.passes ?? []) {
      expect(screen.getAllByText(pass.name).length).toBeGreaterThan(0)
    }
    // The wins alone are misleading, so the gaps are on the same screen as the wins.
    expect(screen.getByText(/^Skipped \(\d+\)$/)).toBeInTheDocument()
  })

  it('names each entry point the oracle could not call, rather than only listing wins', () => {
    seed('folded')
    render(<OptimizationView />)

    const report = optimizationOf(loadFixture('folded').response)
    // `folded` optimizes cleanly and verifies everything reachable, but `mixed_with_variable`
    // takes parameters, so the oracle has nothing to call it with. Reporting only the wins
    // would overstate the coverage.
    expect(report?.skipped.length).toBeGreaterThan(0)
    const gap = report?.skipped.find((item) => item.kind === 'unverified_entry')
    expect(gap?.target).toBe('mixed_with_variable')
    expect(screen.getAllByText('mixed_with_variable').length).toBeGreaterThan(0)
  })

  it('says so explicitly when nothing was skipped', () => {
    // No recorded optimize fixture has an empty gap list, so the honest way to test the empty
    // branch is to hand the view a report with none. Staying silent there would let a reader
    // assume the section is broken.
    const fixture = loadFixture('folded')
    const report = optimizationOf(fixture.response)
    useCompilerStore.setState({
      response: {
        ...fixture.response,
        optimization: { ...report!, skipped: [], verification: { ...report!.verification!, unverified_entries: [] } },
      },
      source: fixture.source,
      status: 'ready',
    })
    render(<OptimizationView />)

    expect(screen.getByText(/nothing skipped/)).toBeInTheDocument()
  })

  it('distinguishes a skipped oracle from a failed one', () => {
    // An analyze-mode response has no optimizer payload at all. The panel must say the stage
    // did not run, not render an empty report that reads as "the optimizer found nothing".
    const fixture = loadFixture('hello')
    expect(fixture.response.optimization).toBeNull()
    seed('hello')
    render(<OptimizationView />)

    expect(screen.getByText('No report')).toBeInTheDocument()
    expect(screen.getByText(/only runs in optimize mode/)).toBeInTheDocument()
  })

  it('shows a skipped oracle as skipped, not as a verification failure', () => {
    // A program with no argument-free entry point cannot be verified at all. `skipped: true`
    // with `verified: false` is that case, and rendering it as a failure would be wrong.
    const fixture = loadFixture('folded')
    const report = optimizationOf(fixture.response)
    useCompilerStore.setState({
      response: {
        ...fixture.response,
        optimization: {
          ...report!,
          verification: {
            ...report!.verification!,
            verified: false,
            skipped: true,
            cases_checked: 0,
            cases: [],
            unverified_entries: ['main'],
          },
        },
      },
      source: fixture.source,
      status: 'ready',
    })
    render(<OptimizationView />)

    // The panel must not claim runs matched when none ran, and must not report a disagreement
    // that did not happen. It should instead say the oracle never ran.
    expect(screen.queryByText(/a run disagreed/)).not.toBeInTheDocument()
    expect(screen.getByText(/0 runs matched/)).toBeInTheDocument()
    expect(screen.getByText(/the oracle did not run/)).toBeInTheDocument()
  })

  it('names the entry that disagreed when verification fails', () => {
    const fixture = loadFixture('folded')
    const report = optimizationOf(fixture.response)
    useCompilerStore.setState({
      response: {
        ...fixture.response,
        optimization: {
          ...report!,
          verification: {
            ...report!.verification!,
            verified: false,
            cases_checked: 1,
            first_difference: {
              entry: 'folded_add',
              stdin: '',
              matched: false,
              inconclusive: false,
              original: '14',
              optimized: '15',
              difference: 'return 15 != 14',
            },
          },
        },
      },
      source: fixture.source,
      status: 'ready',
    })
    render(<OptimizationView />)

    expect(screen.getByText(/a run disagreed/)).toBeInTheDocument()
    expect(screen.getByText(/folded_add: return 15 != 14/)).toBeInTheDocument()
  })

  it('lists each rewrite with its before and after', async () => {
    seed('folded')
    render(<OptimizationView />)
    await userEvent.click(screen.getByRole('button', { name: 'events' }))

    const report = optimizationOf(loadFixture('folded').response)
    expect(report?.events.length).toBeGreaterThan(0)
    for (const event of (report?.events ?? []).slice(0, 3)) {
      expect(screen.getAllByText(event.before).length).toBeGreaterThan(0)
      expect(screen.getAllByText(event.after).length).toBeGreaterThan(0)
    }
  })

  it('diffs the unoptimized TAC against the optimized one', async () => {
    seed('folded')
    render(<OptimizationView />)
    await userEvent.click(screen.getByRole('button', { name: 'diff' }))

    const { response } = loadFixture('folded')
    const report = optimizationOf(response)
    // The before column is the response's own top-level `ir`, the after is the optimizer's.
    // A diff between two renderings produced by different code would eventually disagree.
    expect(report?.ir.text).not.toBe(response.ir?.text)
    expect(screen.getByText('as written')).toBeInTheDocument()
    expect(screen.getByText('after the optimizer')).toBeInTheDocument()
  })

  it('offers the apply button while codegen still has a rewrite to hand over', async () => {
    // The counterpart to the settled-state test below: without it, a view that always showed the
    // fully-optimized card would pass everything above while being wrong on every real program.
    seed('folded')
    render(<OptimizationView />)
    await userEvent.click(screen.getByRole('button', { name: 'diff' }))

    expect(screen.getByRole('button', { name: 'Apply to Editor' })).toBeInTheDocument()
    expect(screen.queryByText('Code is Fully Optimized')).not.toBeInTheDocument()
  })

  it('reports the source as fully optimized once codegen has nothing left to change', async () => {
    // Every recorded fixture is a first pass, so all of them have a rewrite to offer. The settled
    // state is the same program one compile later — which is the point of applying — and no
    // fixture captures it, so it is built here from `folded` by flipping codegen's own verdict
    // and moving the editor to the source it would have rewritten.
    const fixture = loadFixture('folded')
    const codegen = codegenOf(fixture.response)!
    useCompilerStore.setState({
      response: { ...fixture.response, codegen: { ...codegen, applied: false } },
      source: codegen.source,
      status: 'ready',
    })
    render(<OptimizationView />)
    await userEvent.click(screen.getByRole('button', { name: 'diff' }))

    expect(screen.getByText('Code is Fully Optimized')).toBeInTheDocument()
    expect(
      screen.getByText(
        'The active source code already contains all applied optimizations. No further passes needed.',
      ),
    ).toBeInTheDocument()

    // The diff is the TAC before/after, and it can still be non-empty here: the optimizer folds
    // temporaries the source has no way to express. Showing it under a "no further passes needed"
    // banner would read as outstanding work that the banner just denied.
    expect(screen.queryByText('as written')).not.toBeInTheDocument()
    expect(screen.queryByText('after the optimizer')).not.toBeInTheDocument()
    // Nothing left to hand over, so nothing to offer.
    expect(screen.queryByRole('button', { name: 'Apply to Editor' })).not.toBeInTheDocument()
  })
})
