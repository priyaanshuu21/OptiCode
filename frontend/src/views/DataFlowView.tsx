import { useCallback, useMemo, type JSX } from 'react'
import { ReactFlowProvider } from '@xyflow/react'

import { cfgOf, dataflowOf, stageSkipped } from '@/api/client'
import type { FunctionCfg, FunctionFacts } from '@/api/types'
import { readableOrder } from '@/lib/layout'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'
import { BlockGraph } from '@/components/BlockGraph'
import { BlockInspector } from '@/components/BlockInspector'

/**
 * Liveness: which names the rest of the function still needs.
 *
 * The page answers one question, because one question is what a reader comes here with. A name
 * is *live* at a point when some path from there reads it before writing it again; a name that
 * is not live can be discarded, which is the whole reason the optimizer is allowed to delete the
 * assignment that produced it. Showing that question and no other is the point: the earlier view
 * put three analyses behind a selector and a convergence stepper, and a reader had to choose
 * before they could learn anything.
 *
 * The table states the facts the solver computed, one block per row:
 *
 * * **USE** — read in the block before anything there writes it.
 * * **DEF** — every name the block writes.
 * * **IN** / **OUT** — what is live on the way in and the way out.
 *
 * And they are related by the transfer that produced them, which is written down below the table
 * rather than left for the reader to reconstruct from the two columns.
 */

/** One liveness set as a table cell. Empty is a fact; missing can be a different one. */
function setCell(value: string[] | undefined, reachable: boolean): string {
  if (value === undefined) {
    // The solver only visits reachable blocks. Saying "None" for one it never reached would
    // claim it proved the set empty, which it did not; and a reachable block it simply has no
    // record for is a third thing again.
    return reachable ? 'Not computed' : 'Not analyzed: unreachable'
  }
  // "None" rather than an empty cell: an empty set is a result, and a blank cell reads as
  // "this column did not load".
  return value.length > 0 ? value.join(', ') : 'None'
}

function LivenessTable({ graph, facts }: { graph: FunctionCfg; facts: FunctionFacts }): JSX.Element {
  return (
    <table className="w-full border-collapse text-left font-mono text-sm">
      <caption className="sr-only">Liveness sets per basic block, computed from the CFG</caption>
      <thead className="sticky top-0 bg-surface-raised text-slate-300">
        <tr>
          <th scope="col" className="px-3 py-2 font-medium">block</th>
          <th scope="col" className="px-3 py-2 font-medium" title="read before written here">USE</th>
          <th scope="col" className="px-3 py-2 font-medium" title="written here">DEF</th>
          <th scope="col" className="px-3 py-2 font-medium" title="live on entry">IN</th>
          <th scope="col" className="px-3 py-2 font-medium" title="live on exit">OUT</th>
        </tr>
      </thead>
      <tbody>
        {graph.blocks.map((block) => (
          <tr key={block.id} className="border-t border-edge/60 hover:bg-surface-overlay/50">
            <th scope="row" className="whitespace-nowrap px-3 py-2 font-normal text-stage-block">
              {block.id}
              {block.reachable ? null : (
                <span className="ml-1 text-severity-warning" title="unreachable — not analyzed">
                  unreachable
                </span>
              )}
            </th>
            <td className="px-3 py-2 text-slate-100">{setCell(facts.use?.[block.id], block.reachable)}</td>
            <td className="px-3 py-2 text-slate-100">{setCell(facts.def?.[block.id], block.reachable)}</td>
            <td className="px-3 py-2 text-slate-100">{setCell(facts.live_in?.[block.id], block.reachable)}</td>
            <td className="px-3 py-2 text-slate-100">{setCell(facts.live_out?.[block.id], block.reachable)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** The transfer that turns USE and DEF into IN and OUT, shown so the table can be checked. */
function Equations(): JSX.Element {
  return (
    <details className="mt-1 font-mono text-2xs text-slate-500">
      <summary className="cursor-pointer select-none text-slate-400">How IN and OUT are computed</summary>
      <div className="mt-1 space-y-0.5 pl-3">
        <p>OUT[B] = ⋃ IN[S] over every successor S (OUT[EXIT] = ∅)</p>
        <p>IN[B] = USE[B] ∪ (OUT[B] \ DEF[B])</p>
        <p className="text-slate-400">
          Applied to every block repeatedly until nothing changes; a loop needs more than one pass
          because its OUT depends on its own IN.
        </p>
      </div>
    </details>
  )
}

export function DataFlowView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)
  const selectBlock = useCompilerStore((state) => state.selectBlock)
  const selected = useCompilerStore((state) => state.selectedBlock)

  const facts = response ? dataflowOf(response) : null
  const graphs = response ? cfgOf(response) : null

  const functionName = useMemo(() => {
    if (!facts) {
      return null
    }
    if (selected && facts[selected.function]) {
      return selected.function
    }
    return Object.keys(facts).sort()[0] ?? null
  }, [facts, selected])

  const functionFacts = functionName ? facts?.[functionName] : undefined
  const graph = functionName ? graphs?.[functionName] : undefined

  const focusBlock = useCallback(
    (blockId: string) => {
      if (!functionName || !graph) {
        return
      }
      selectBlock({ function: functionName, block: blockId })
      const block = graph.blocks.find((candidate) => candidate.id === blockId)
      if (block) {
        select({
          line: block.span.start_line,
          endLine: Math.max(block.span.end_line, block.span.start_line),
          origin: `block ${block.id}`,
        })
      }
    },
    [functionName, graph, select, selectBlock],
  )

  if (!response) {
    return (
      <Panel title="Data flow" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Type a program and its data-flow facts will appear here." />
      </Panel>
    )
  }

  if (!facts || !graphs) {
    return (
      <Panel title="Data flow" subtitle="The data-flow stage did not run.">
        <EmptyState
          title="No facts"
          detail="The data-flow stage was skipped, usually because the program did not lower."
          skipped={stageSkipped(response, 'dataflow')}
        />
      </Panel>
    )
  }

  if (!graph || !functionFacts) {
    return (
      <Panel title="Data flow">
        <EmptyState title="No functions" detail="There is nothing to analyse." />
      </Panel>
    )
  }

  const blockId = selected && selected.function === graph.function ? selected.block : graph.entry
  const detail = graph.blocks.find((block) => block.id === blockId) ?? graph.blocks[0]

  return (
    <Panel
      title="Data Flow: Which values are still needed?"
      subtitle="Liveness at every block boundary — a variable is live when its current value may still be used before it is replaced."
      actions={<Count label={`${graph.blocks.length} blocks`}>{graph.blocks.length} blocks</Count>}
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="shrink-0 border-b border-edge px-4 py-3">
          <p className="text-sm leading-relaxed text-slate-200">
            A variable is <span className="font-semibold text-slate-100">live</span> when its
            current value may still be used before it is replaced — somewhere on a path from here,
            the variable is read before it is written again.
          </p>
          <p className="mt-1 text-sm leading-relaxed text-slate-300">
            The last assignment to a variable that is not live computes a value nothing uses, which
            is what lets the optimizer remove it.
          </p>
          <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-300">
            <div className="flex gap-1.5">
              <dt className="font-mono font-semibold text-slate-100">USE</dt>
              <dd>read in the block before it is written here</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="font-mono font-semibold text-slate-100">DEF</dt>
              <dd>written in the block</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="font-mono font-semibold text-slate-100">IN</dt>
              <dd>live on entry</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="font-mono font-semibold text-slate-100">OUT</dt>
              <dd>live on exit</dd>
            </div>
          </dl>
          <Equations />
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="min-h-0 flex-1">
              <ReactFlowProvider>
                <BlockGraph
                  graph={graph}
                  selectedId={detail?.id ?? null}
                  onSelect={focusBlock}
                  badgeFor={(block) => {
                    const value = blockLiveOut(functionFacts, block.id)
                    return value === undefined ? null : `live out: ${value}`
                  }}
                />
              </ReactFlowProvider>
            </div>
            <div className="max-h-64 shrink-0 overflow-auto border-t border-edge">
              <LivenessTable graph={graph} facts={functionFacts} />
            </div>
          </div>
          {detail ? (
            <BlockInspector
              graph={graph}
              block={detail}
              facts={functionFacts}
              order={readableOrder(graph)}
            />
          ) : null}
        </div>
      </div>
    </Panel>
  )
}

function blockLiveOut(facts: FunctionFacts, blockId: string): string | undefined {
  const live = facts.live_out?.[blockId]
  if (!live) {
    return undefined
  }
  return live.length > 0 ? live.join(', ') : 'None'
}
