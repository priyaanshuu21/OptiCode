import { useCallback, useMemo, useState, type JSX } from 'react'
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { cfgOf, stageSkipped } from '@/api/client'
import type { Block, FunctionCfg } from '@/api/types'
import { layoutGraph, type BlockNodeData } from '@/lib/layout'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'

/**
 * The control-flow graph, laid out with dagre.
 *
 * This is the one view that needs a diagram rather than a list. A CFG's whole point is its
 * shape — which blocks feed which, where the loop is, where the exit is — and a table of
 * predecessors and successors destroys exactly that. So React Flow draws it and dagre places
 * the nodes.
 *
 * Back edges are styled and animated, because a cycle in a CFG *is* a loop, and that is the
 * single most useful thing to see at a glance.
 *
 * Block selection is local state rather than store state, because it is a property of this
 * view only. What is shared is the *consequence* of a selection: clicking a block highlights
 * its source range in the editor, which is why that half goes through the store.
 */

function BlockNode({ data, selected }: NodeProps<Node<BlockNodeData>>): JSX.Element {
  const { block, preview, hidden } = data
  const isEntry = block.predecessors.length === 0
  const isExit = block.successors.length === 0

  return (
    <div
      className={`w-64 rounded-md border bg-surface-raised px-2 py-1.5 font-mono text-2xs shadow-lg transition-colors ${
        selected ? 'border-stage-block' : block.reachable ? 'border-edge-strong' : 'border-edge opacity-60'
      }`}
    >
      <Handle type="target" position={Position.Top} className="!h-1.5 !w-1.5 !bg-edge-strong !border-0" />
      <div className="flex items-center gap-1.5 border-b border-edge/70 pb-1">
        <span className="font-semibold text-stage-block">{block.id}</span>
        {isEntry ? <span className="text-2xs text-severity-success">entry</span> : null}
        {isExit ? <span className="text-2xs text-slate-500">exit</span> : null}
        {!block.reachable ? <span className="text-2xs text-severity-warning">unreachable</span> : null}
        <span className="ml-auto text-slate-600">{block.instructions.length}</span>
      </div>
      <ul className="mt-1 space-y-0.5">
        {preview.map((line, index) => (
          <li key={index} className="truncate text-slate-300">
            {line}
          </li>
        ))}
        {hidden > 0 ? <li className="text-slate-600">+{hidden} more</li> : null}
      </ul>
      <Handle type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !bg-edge-strong !border-0" />
    </div>
  )
}

const nodeTypes = { block: BlockNode }

function GraphCanvas({
  graph,
  selectedId,
  onSelect,
}: {
  graph: FunctionCfg
  selectedId: string | null
  onSelect: (blockId: string) => void
}): JSX.Element {
  const { nodes, edges } = useMemo(() => layoutGraph(graph), [graph])

  return (
    <ReactFlow
      nodes={nodes.map((node) => ({ ...node, selected: node.id === selectedId }))}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_event, node) => onSelect(node.id)}
      fitView
      fitViewOptions={{ padding: 0.2 }}
      minZoom={0.2}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      className="bg-surface-sunken"
    >
      <Background color="#243050" gap={18} size={1} />
      <Controls showInteractive={false} className="!bg-surface-raised !border-edge" />
      <MiniMap
        pannable
        zoomable
        className="!bg-surface-raised !border-edge"
        nodeColor={() => '#34d399'}
        maskColor="rgba(8, 12, 24, 0.7)"
      />
    </ReactFlow>
  )
}

/** Instructions of the selected block, beside the graph. */
function BlockDetail({ block, order }: { block: Block; order: string[] }): JSX.Element {
  return (
    <aside className="w-80 shrink-0 overflow-auto border-l border-edge bg-surface-sunken p-3">
      <div className="space-y-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-sm font-semibold text-stage-block">{block.id}</span>
          <span className="text-2xs text-slate-500">
            {block.instructions.length} instruction{block.instructions.length === 1 ? '' : 's'}
          </span>
        </div>
        <p className="text-2xs leading-relaxed text-slate-500">
          Leader: {block.leader_reason || 'first block'}
        </p>
        <div className="grid grid-cols-2 gap-2 text-2xs">
          <div>
            <p className="uppercase tracking-wider text-slate-500">Predecessors</p>
            <p className="font-mono text-slate-300">{block.predecessors.join(', ') || '—'}</p>
          </div>
          <div>
            <p className="uppercase tracking-wider text-slate-500">Successors</p>
            <p className="font-mono text-slate-300">{block.successors.join(', ') || '—'}</p>
          </div>
        </div>
        {block.labels.length > 0 ? (
          <div>
            <p className="text-2xs uppercase tracking-wider text-slate-500">Labels</p>
            <p className="font-mono text-2xs text-slate-300">{block.labels.join(', ')}</p>
          </div>
        ) : null}
        <div>
          <p className="text-2xs uppercase tracking-wider text-slate-500">Instructions</p>
          {block.instructions.length === 0 ? (
            <p className="font-mono text-2xs text-slate-600">empty block</p>
          ) : (
            <ol className="mt-1 space-y-1">
              {block.instructions.map((instruction) => (
                <li key={instruction.id} className="font-mono text-2xs leading-relaxed text-slate-300">
                  <span className="text-slate-600">{instruction.id}</span> {instruction.opcode}{' '}
                  <span className="text-slate-400">
                    {instruction.operands.map((operand) => operand.name ?? String(operand.value)).join(', ')}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
        <div>
          <p className="text-2xs uppercase tracking-wider text-slate-500">Function order</p>
          <p className="mt-0.5 font-mono text-2xs leading-relaxed text-slate-400">{order.join(' → ')}</p>
        </div>
      </div>
    </aside>
  )
}

export function BlocksView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)
  const [functionName, setFunctionName] = useState<string | null>(null)
  const [selectedBlock, setSelectedBlock] = useState<string | null>(null)

  const handleSelect = useCallback(
    (graph: FunctionCfg, blockId: string) => {
      setSelectedBlock(blockId)
      const block = graph.blocks.find((candidate) => candidate.id === blockId)
      if (block) {
        select({
          line: block.span.start_line,
          endLine: Math.max(block.span.end_line, block.span.start_line),
          origin: `block ${block.id}`,
        })
      }
    },
    [select],
  )

  if (!response) {
    return (
      <Panel title="Blocks" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Type a program and its control-flow graph will appear here." />
      </Panel>
    )
  }

  const graphs = cfgOf(response)

  if (!graphs) {
    return (
      <Panel title="Blocks" subtitle="The CFG stage did not run.">
        <EmptyState
          title="No graph"
          detail="The CFG stage was skipped, usually because lowering failed. See Diagnostics."
          skipped={stageSkipped(response, 'cfg')}
        />
      </Panel>
    )
  }

  const names = Object.keys(graphs).sort()
  const selectedName = functionName && graphs[functionName] ? functionName : names[0]
  const graph = selectedName ? graphs[selectedName] : undefined

  if (!graph) {
    return (
      <Panel title="Blocks">
        <EmptyState title="No functions" detail="The program has no functions to draw." />
      </Panel>
    )
  }

  const detail =
    graph.blocks.find((block) => block.id === selectedBlock) ??
    graph.blocks.find((block) => block.id === graph.entry) ??
    graph.blocks[0]
  const order = graph.rpo.length > 0 ? graph.rpo : graph.blocks.map((block) => block.id)

  return (
    <Panel
      title="Blocks"
      subtitle="Basic blocks and the edges between them. Animated edges are back edges, which is to say loops."
      actions={
        <div className="flex items-center gap-3">
          {names.length > 1 ? (
            <select
              value={selectedName}
              onChange={(event) => {
                setFunctionName(event.target.value)
                setSelectedBlock(null)
              }}
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
      <div className="flex h-full min-h-0">
        <div className="min-h-0 flex-1">
          <ReactFlowProvider>
            <GraphCanvas
              graph={graph}
              selectedId={detail?.id ?? null}
              onSelect={(blockId) => handleSelect(graph, blockId)}
            />
          </ReactFlowProvider>
        </div>
        {detail ? <BlockDetail block={detail} order={order} /> : null}
      </div>
    </Panel>
  )
}
