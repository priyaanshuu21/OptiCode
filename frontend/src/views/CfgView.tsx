import { useCallback, useEffect, type JSX } from 'react'
import { ReactFlowProvider } from '@xyflow/react'

import { cfgOf, stageSkipped } from '@/api/client'
import type { FunctionCfg } from '@/api/types'
import { readableOrder } from '@/lib/layout'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'
import { BlockGraph, EdgeLegend } from '@/components/BlockGraph'
import { BlockInspector } from '@/components/BlockInspector'

/**
 * The control-flow graph.
 *
 * This is the one view that needs a diagram rather than a list. A CFG's whole point is its
 * shape — which blocks feed which, where the loop is, where the exit is — and a table of
 * predecessors and successors destroys exactly that. So React Flow draws it and dagre places the
 * nodes.
 *
 * Three things the earlier version of this view got wrong, all of them about telling apart
 * things that are not the same:
 *
 * * **Entry and exit are named by the graph, not inferred.** A block whose only way out is a
 *   `return` has no successors, exactly like the synthetic exit block, so "no successors" is not
 *   a test for "exit".
 * * **The two arms of a branch are different colours.** "Which way does this go" is the first
 *   question asked of a CFG, and the answer should not require reading a label.
 * * **Unreachable blocks stay visible and say so.** They are still in the program, and a graph
 *   that silently omits them cannot explain a finding about them.
 */

/** Which function's graph is on screen, defaulting to the store's selection or the first one. */
function useChosenFunction(graphs: Record<string, FunctionCfg>): string | null {
  const selected = useCompilerStore((state) => state.selectedBlock)
  const names = Object.keys(graphs).sort()
  if (selected && graphs[selected.function]) {
    return selected.function
  }
  return names[0] ?? null
}

export function CfgView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)
  const selectBlock = useCompilerStore((state) => state.selectBlock)
  const selected = useCompilerStore((state) => state.selectedBlock)

  const graphs = response ? cfgOf(response) : null

  if (!response) {
    return (
      <Panel title="CFG" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Type a program and its control-flow graph will appear here." />
      </Panel>
    )
  }

  if (!graphs) {
    return (
      <Panel title="CFG" subtitle="The CFG stage did not run.">
        <EmptyState
          title="No graph"
          detail="The CFG stage was skipped, usually because lowering failed. See Diagnostics."
          skipped={stageSkipped(response, 'cfg')}
        />
      </Panel>
    )
  }

  return (
    <GraphBody
      graphs={graphs}
      selected={selected}
      onSelect={(functionName, blockId) => {
        selectBlock({ function: functionName, block: blockId })
        const block = graphs[functionName]?.blocks.find((candidate) => candidate.id === blockId)
        if (block) {
          select({
            line: block.span.start_line,
            endLine: Math.max(block.span.end_line, block.span.start_line),
            origin: `block ${block.id}`,
          })
        }
      }}
    />
  )
}

function GraphBody({
  graphs,
  selected,
  onSelect,
}: {
  graphs: Record<string, FunctionCfg>
  selected: { function: string; block: string } | null
  onSelect: (functionName: string, blockId: string) => void
}): JSX.Element {
  const names = Object.keys(graphs).sort()
  const functionName = useChosenFunction(graphs)
  const graph = functionName ? graphs[functionName] : undefined

  // Changing function must not leave a block id from the previous graph selected: `B1` means
  // something different in every function, and the inspector would show the wrong one.
  useEffect(() => {
    if (functionName && (!selected || selected.function !== functionName)) {
      onSelect(functionName, graphs[functionName]?.entry ?? graphs[functionName]?.blocks[0]?.id ?? '')
    }
    // Intentionally keyed on the function only: re-selecting on every `selected` change would
    // undo the click that caused it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [functionName])

  const handleSelect = useCallback(
    (blockId: string) => {
      if (functionName) {
        onSelect(functionName, blockId)
      }
    },
    [functionName, onSelect],
  )

  if (!graph) {
    return (
      <Panel title="CFG">
        <EmptyState title="No functions" detail="The program has no functions to draw." />
      </Panel>
    )
  }

  const blockId = selected && selected.function === graph.function ? selected.block : graph.entry
  const detail = graph.blocks.find((block) => block.id === blockId) ?? graph.blocks[0]

  return (
    <Panel
      title="CFG"
      subtitle="Basic blocks and the edges between them. Amber edges point backwards, which is to say loops."
      actions={
        <div className="flex items-center gap-3">
          {names.length > 1 ? (
            <select
              value={functionName ?? ''}
              onChange={(event) => onSelect(event.target.value, graphs[event.target.value]?.entry ?? '')}
              aria-label="Function"
              className="rounded border border-edge bg-surface-overlay px-2 py-1 font-mono text-xs text-slate-200"
            >
              {names.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : null}
          <Count label={`${graph.block_count} blocks`}>{graph.block_count} blocks</Count>
          <Count label={`${graph.edge_count} edges`}>{graph.edge_count} edges</Count>
          <span
            className={`font-mono text-2xs ${graph.has_loops ? 'text-severity-warning' : 'text-slate-500'}`}
          >
            {graph.has_loops ? `loops: ${graph.back_edges.length}` : 'no loops'}
          </span>
        </div>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex min-h-0 flex-1">
          <div className="min-h-0 flex-1">
            <ReactFlowProvider>
              <BlockGraph
                graph={graph}
                selectedId={detail?.id ?? null}
                onSelect={handleSelect}
              />
            </ReactFlowProvider>
          </div>
          {detail ? (
            <BlockInspector
              graph={graph}
              block={detail}
              facts={undefined}
              order={readableOrder(graph)}
            />
          ) : null}
        </div>
        <div className="shrink-0 border-t border-edge px-4 py-2">
          <EdgeLegend />
        </div>
      </div>
    </Panel>
  )
}
