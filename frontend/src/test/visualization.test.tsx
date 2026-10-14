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
  useCompilerStore.setState({
    response,
    source,
    // The recorded response describes exactly this source; saying so is what lets the
    // optimization screen treat it as current rather than as stale.
    compiledSource: source,
    status: 'ready',
    error: null,
    selection: null,
    selectedBlock: null,
  })
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
    activeStage: 'cfg',
    mode: 'analyze',
    optimizationPreview: null,
    previewStatus: 'idle',
    previewError: null,
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
    // One node per block, plus the synthetic ENTRY and EXIT terminals the graph draws.
    const nodes = document.querySelectorAll('.react-flow__node')
    expect(nodes.length).toBe(blockCount + 2)

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

  it('shows one USE/DEF/IN/OUT row for every block', () => {
    seed('loops')
    render(<DataFlowView />)

    const { cfg, dataflow } = loadFixture('loops').response
    const main = cfg?.main
    expect(main).toBeDefined()

    const table = screen.getByRole('table')
    // A header row plus one row per block: the table has to cover the whole function, because a
    // partial table invites the reader to assume the missing blocks were uninteresting.
    expect(within(table).getAllByRole('row')).toHaveLength((main?.blocks.length ?? 0) + 1)
    for (const column of ['USE', 'DEF', 'IN', 'OUT']) {
      expect(within(table).getByRole('columnheader', { name: column })).toBeInTheDocument()
    }
    expect(dataflow?.main?.live_in).toBeDefined()
  })

  it('fills each row from the recorded liveness sets, marking an empty set as None', () => {
    seed('loops')
    useCompilerStore.setState({ selectedBlock: { function: 'sum_while', block: 'B2' } })
    render(<DataFlowView />)

    const { cfg, dataflow } = loadFixture('loops').response
    const graph = cfg?.sum_while
    const facts = dataflow?.sum_while
    expect(graph).toBeDefined()
    expect(facts).toBeDefined()

    const table = screen.getByRole('table')
    for (const block of graph!.blocks) {
      const row = within(table).getByRole('row', { name: new RegExp(`\\b${block.id}\\b`) })
      const sets = [
        facts!.use?.[block.id],
        facts!.def?.[block.id],
        facts!.live_in?.[block.id],
        facts!.live_out?.[block.id],
      ]
      for (const value of sets) {
        // An empty set is a result and reads as "None"; the row must not leave it blank.
        const text =
          value === undefined
            ? 'Not analyzed: unreachable'
            : value.length > 0
              ? value.join(', ')
              : 'None'
        expect(row.textContent, `${block.id}: ${text}`).toContain(text)
      }
    }
  })

  it('marks an unreachable block as not analyzed rather than as an empty set', () => {
    // `always_early` returns before B2, so the solver never visits B2 and B3. Recording that as
    // "None" would claim the analysis proved their sets empty, which it did not.
    seed('unreachable')
    render(<DataFlowView />)

    const { cfg, dataflow } = loadFixture('unreachable').response
    const graph = cfg?.always_early
    expect(graph?.blocks.some((block) => !block.reachable)).toBe(true)
    expect(dataflow?.always_early?.live_in?.B2).toBeUndefined()

    const table = screen.getByRole('table')
    const row = within(table).getByRole('row', { name: /\bB2\b/ })
    expect(row.textContent).toContain('Not analyzed: unreachable')
  })

  it('shows only liveness — no analysis selector and no convergence stepper', () => {
    seed('loops')
    render(<DataFlowView />)

    // The page answers one question now. The other analyses' controls must be gone, not merely
    // hidden behind a default, or the page still promises a choice it does not offer.
    expect(screen.queryByRole('button', { name: 'Reaching defs' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Next iteration' })).not.toBeInTheDocument()
    // ...but the transfer that produced the table is still stated, so the numbers are checkable.
    expect(screen.getByText(/How IN and OUT are computed/)).toBeInTheDocument()
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

  it('refuses to apply a proposal the editor has moved past', () => {
    seed('folded')
    // A proposal built from some earlier text: it no longer matches the editor, so applying it
    // would overwrite newer code with something the reader never saw.
    useCompilerStore.setState({
      optimizationPreview: {
        originalSource: 'int main() { return 1; }',
        optimizedSource: 'int main() { return 0; }',
        changed: true,
        reason: '',
      },
    })
    render(<OptimizationView />)

    expect(screen.getByText(/code changed since this preview/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply to Editor' })).toBeDisabled()
  })

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
      compiledSource: codegen.source,
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
    // Nothing left to hand over, so nothing to apply: the control stays visible but disabled, so
    // the reader can see the action exists and why it is unavailable rather than hunting for it.
    expect(screen.getByText(/No optimization changes available/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply to Editor' })).toBeDisabled()
  })
})
