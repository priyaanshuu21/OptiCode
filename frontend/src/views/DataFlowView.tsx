import { useCallback, useEffect, useMemo, useState, type JSX } from 'react'
import { ReactFlowProvider } from '@xyflow/react'

import { cfgOf, dataflowOf, stageSkipped } from '@/api/client'
import type { AnalysisName, FunctionCfg, FunctionFacts, SolverTrace } from '@/api/types'
import { readableOrder } from '@/lib/layout'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'
import { BlockGraph } from '@/components/BlockGraph'
import { BlockInspector } from '@/components/BlockInspector'

/**
 * The three data-flow analyses, and the worklist that computed them.
 *
 * Two things are on screen, and they answer two different questions.
 *
 * The **tables** answer "what does the analysis know here?" — a block's value at its entry and
 * its exit, for liveness, reaching definitions, or constants. This is the part a person reads
 * when they want to know why the optimizer did what it did.
 *
 * The **stepper** answers "how did it get there?" — replaying the recorded block updates one at
 * a time, with the block that changed most recently highlighted. A fixed point that is merely
 * asserted is unfalsifiable; a fixed point you can watch converge is a claim you can check. The
 * order is a function of the CFG alone, so the same program always replays the same way.
 *
 * The stepper is capped by the backend, and says so when it is: a trace shorter than the
 * iteration count is a truncated trace, and presenting it as complete would be a lie about how
 * much of the work is shown.
 */

const ANALYSES: { id: AnalysisName; label: string; hint: string }[] = [
  { id: 'liveness', label: 'Liveness', hint: 'which names are still needed after this block' },
  { id: 'reaching', label: 'Reaching defs', hint: 'which assignments can reach this block' },
  {
    id: 'constant_propagation',
    label: 'Constants',
    hint: 'which names the solver proved to a single value',
  },
]

function renderValue(value: string[] | Record<string, string> | undefined): string {
  if (value === undefined) {
    return '—'
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : '∅'
  }
  const entries = Object.entries(value)
  if (entries.length === 0) {
    return '∅'
  }
  return entries.map(([name, known]) => `${name}=${known}`).join(', ')
}

/**
 * Which two maps answer "in" and "out" for an analysis.
 *
 * Liveness and reaching defs are indexed under their own names, not under a generic pair, so
 * hard-coding `live_in` for all three would quietly show liveness's answer under a "reaching
 * defs" heading — a table that is confident and wrong, which is worse than an empty one.
 */
function blockSetsFor(
  facts: FunctionFacts,
  analysis: AnalysisName,
): { entry: Record<string, string[]> | undefined; exit: Record<string, string[]> | undefined } {
  if (analysis === 'reaching') {
    return { entry: facts.reaching_in, exit: facts.reaching_out }
  }
  return { entry: facts.live_in, exit: facts.live_out }
}

function SetTable({ graph, facts, analysis }: { graph: FunctionCfg; facts: FunctionFacts; analysis: AnalysisName }): JSX.Element {
  const { entry, exit } = blockSetsFor(facts, analysis)
  return (
    <table className="w-full border-collapse text-left font-mono text-2xs">
      <caption className="sr-only">Block entry and exit values</caption>
      <thead>
        <tr className="text-slate-500">
          <th scope="col" className="px-3 py-1 font-normal">block</th>
          <th scope="col" className="px-3 py-1 font-normal">in</th>
          <th scope="col" className="px-3 py-1 font-normal">out</th>
        </tr>
      </thead>
      <tbody>
        {graph.blocks.map((block) => (
          <tr key={block.id} className="border-t border-edge/60">
            <th scope="row" className="px-3 py-1 font-normal text-stage-block">
              {block.id}
              {block.reachable ? null : <span className="ml-1 text-severity-warning">?</span>}
            </th>
            <td className="px-3 py-1 text-slate-300">{renderValue(entry?.[block.id])}</td>
            <td className="px-3 py-1 text-slate-300">{renderValue(exit?.[block.id])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ConstantTable({ graph, facts }: { graph: FunctionCfg; facts: FunctionFacts }): JSX.Element {
  return (
    <table className="w-full border-collapse text-left font-mono text-2xs">
      <caption className="sr-only">Proven constant values per block</caption>
      <thead>
        <tr className="text-slate-500">
          <th scope="col" className="px-3 py-1 font-normal">block</th>
          <th scope="col" className="px-3 py-1 font-normal">in</th>
          <th scope="col" className="px-3 py-1 font-normal">out</th>
        </tr>
      </thead>
      <tbody>
        {graph.blocks.map((block) => (
          <tr key={block.id} className="border-t border-edge/60">
            <th scope="row" className="px-3 py-1 font-normal text-stage-block">
              {block.id}
            </th>
            <td className="px-3 py-1 text-slate-300">
              {renderValue(statesOf(facts, 'in', block.id))}
            </td>
            <td className="px-3 py-1 text-slate-300">
              {renderValue(statesOf(facts, 'out', block.id))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * The per-block constant states, which the report does not carry.
 *
 * Liveness and reaching defs expose their block values as top-level fields, but constant
 * propagation's states are not in the report at all — only the step trace has them. Reading them
 * from the trace is honest about where they came from, and the last update recorded for a block
 * is its final value, which is exactly what this table claims to show. A block the trace never
 * updated shows `—` rather than an empty set, because "no update" is not the same as "no values".
 */
function statesOf(
  facts: FunctionFacts,
  edge: 'in' | 'out',
  blockId: string,
): Record<string, string> | undefined {
  const trace = facts.traces?.constant_propagation
  if (!trace) {
    return undefined
  }
  for (let index = trace.steps.length - 1; index >= 0; index -= 1) {
    const step = trace.steps[index]
    if (step?.block === blockId) {
      const value = edge === 'in' ? step.in : step.out
      return Array.isArray(value) ? undefined : value
    }
  }
  return undefined
}

function Stepper({
  trace,
  functionName,
  onFocusBlock,
}: {
  trace: SolverTrace
  functionName: string
  onFocusBlock: (blockId: string) => void
}): JSX.Element {
  const [index, setIndex] = useState(0)
  const last = Math.max(0, trace.steps.length - 1)

  // A new function or a new compile resets the replay: step 40 of a trace that no longer exists
  // is meaningless, and leaving it there would show another block's state.
  useEffect(() => {
    setIndex(0)
  }, [functionName, trace])

  const step = trace.steps[Math.min(index, last)]
  const changed = trace.steps.filter((entry) => entry.changed).length

  return (
    <section className="border-t border-edge px-4 py-3" aria-label="Iteration stepper">
      <div className="flex flex-wrap items-center gap-3">
        <p className="font-mono text-2xs text-slate-400">
          {trace.iterations} block update{trace.iterations === 1 ? '' : 's'}
          {trace.trace_truncated
            ? ` · showing the first ${trace.steps.length}`
            : ''}
          {` · ${changed} changed something`}
        </p>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setIndex(0)}
            disabled={index === 0}
            aria-label="First iteration"
            className="rounded border border-edge px-2 py-0.5 font-mono text-2xs text-slate-300 disabled:opacity-40"
          >
            ⇤
          </button>
          <button
            type="button"
            onClick={() => setIndex((value) => Math.max(0, value - 1))}
            disabled={index === 0}
            aria-label="Previous iteration"
            className="rounded border border-edge px-2 py-0.5 font-mono text-2xs text-slate-300 disabled:opacity-40"
          >
            ←
          </button>
          <input
            type="range"
            min={0}
            max={last}
            value={Math.min(index, last)}
            onChange={(event) => setIndex(Number(event.target.value))}
            aria-label="Iteration"
            className="w-40"
          />
          <button
            type="button"
            onClick={() => setIndex((value) => Math.min(last, value + 1))}
            disabled={index >= last}
            aria-label="Next iteration"
            className="rounded border border-edge px-2 py-0.5 font-mono text-2xs text-slate-300 disabled:opacity-40"
          >
            →
          </button>
          <button
            type="button"
            onClick={() => setIndex(last)}
            disabled={index >= last}
            aria-label="Last iteration"
            className="rounded border border-edge px-2 py-0.5 font-mono text-2xs text-slate-300 disabled:opacity-40"
          >
            ⇥
          </button>
        </div>
        <span className="font-mono text-2xs text-slate-500">
          {trace.steps.length === 0 ? 'no updates recorded' : `step ${Math.min(index, last) + 1} of ${trace.steps.length}`}
        </span>
        {trace.direction === 'backward' ? (
          <span className="font-mono text-2xs text-slate-600" title="liveness is defined backwards">
            runs backward
          </span>
        ) : null}
      </div>

      {step ? (
        <div className="mt-2 rounded border border-edge bg-surface-sunken p-2 font-mono text-2xs">
          <p className="text-slate-400">
            <span className="text-slate-600">update #{step.iteration}</span>{' '}
            <button
              type="button"
              onClick={() => onFocusBlock(step.block)}
              className="text-stage-block underline decoration-dotted underline-offset-2"
            >
              {step.block}
            </button>
            {step.changed ? (
              <span className="ml-2 text-severity-success">changed</span>
            ) : (
              <span className="ml-2 text-slate-600">no change</span>
            )}
          </p>
          <p className="mt-1 break-words text-slate-300">in: {renderValue(step.in)}</p>
          <p className="break-words text-slate-300">out: {renderValue(step.out)}</p>
        </div>
      ) : (
        <p className="mt-2 font-mono text-2xs text-slate-600">
          The solver reached its fixed point without recording an update.
        </p>
      )}
    </section>
  )
}

export function DataFlowView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)
  const selectBlock = useCompilerStore((state) => state.selectBlock)
  const selected = useCompilerStore((state) => state.selectedBlock)
  const [analysis, setAnalysis] = useState<AnalysisName>('liveness')

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
  const trace = functionFacts?.traces?.[analysis]

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
  const counts = functionFacts.iterations ?? {}

  return (
    <Panel
      title="Data flow"
      subtitle="What each analysis knows at every block boundary, and the worklist that got it there."
      actions={
        <div className="flex items-center gap-2">
          <div className="flex rounded border border-edge" role="group" aria-label="Analysis">
            {ANALYSES.map((item) => (
              <button
                key={item.id}
                type="button"
                title={item.hint}
                onClick={() => setAnalysis(item.id)}
                aria-pressed={analysis === item.id}
                className={`px-2 py-1 font-mono text-2xs transition-colors ${
                  analysis === item.id
                    ? 'bg-surface-overlay text-slate-100'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <Count label={`${counts[analysis] ?? 0} iterations for ${analysis}`}>
            {counts[analysis] ?? 0} updates
          </Count>
        </div>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="min-h-0 flex-1">
              <ReactFlowProvider>
                <BlockGraph
                  graph={graph}
                  selectedId={detail?.id ?? null}
                  onSelect={focusBlock}
                  badgeFor={(block) => {
                    const value = analysis === 'liveness' ? blockLiveOut(functionFacts, block.id) : undefined
                    return value === undefined ? null : `live out: ${value}`
                  }}
                />
              </ReactFlowProvider>
            </div>
            <div className="max-h-56 shrink-0 overflow-auto border-t border-edge">
              {analysis === 'constant_propagation' ? (
                <ConstantTable graph={graph} facts={functionFacts} />
              ) : (
                <SetTable graph={graph} facts={functionFacts} analysis={analysis} />
              )}
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
        {trace ? (
          <Stepper trace={trace} functionName={graph.function} onFocusBlock={focusBlock} />
        ) : (
          <p className="border-t border-edge px-4 py-3 font-mono text-2xs text-slate-600">
            This response carries no iteration trace for {analysis}.
          </p>
        )}
      </div>
    </Panel>
  )
}

function blockLiveOut(facts: FunctionFacts, blockId: string): string | undefined {
  const live = facts.live_out?.[blockId]
  if (!live) {
    return undefined
  }
  return live.length > 0 ? live.join(', ') : '∅'
}
