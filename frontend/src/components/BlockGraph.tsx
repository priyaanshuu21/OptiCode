import { useMemo, type JSX } from 'react'
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { Block, FunctionCfg } from '@/api/types'
import { EDGE_COLOUR, layoutGraph, type BlockNodeData } from '@/lib/layout'

/**
 * The graph itself, shared by the CFG and the data-flow views.
 *
 * They are two readings of one graph, so they must not be two graphs. A reader who clicks a
 * block in the CFG and switches to the data-flow tab expects to still be looking at that block,
 * and the only way to guarantee that is to draw the same nodes with the same ids from the same
 * layout function. The per-block overlay a view wants — the data-flow view tints a block with
 * its live set, the CFG view does not — is passed in as a function rather than baked in.
 */

/** Extra text shown under a block's instruction list, e.g. a block's live set. */
export type BlockBadge = (block: Block) => string | null

function BlockNode({ data, selected }: NodeProps<Node<BlockNodeData>>): JSX.Element {
  const { block, preview, hidden, role, badge } = data as BlockNodeData & { badge: string | null }
  const tone =
    role === 'entry'
      ? 'border-severity-success/60'
      : role === 'exit'
        ? 'border-violet-400/50'
        : block.reachable
          ? 'border-edge-strong'
          : 'border-edge opacity-60'

  return (
    <div
      className={`w-64 rounded-md border bg-surface-raised px-2 py-1.5 font-mono text-2xs shadow-lg transition-colors ${
        selected ? 'border-stage-block' : tone
      }`}
    >
      <Handle type="target" position={Position.Top} className="!h-1.5 !w-1.5 !bg-edge-strong !border-0" />
      <div className="flex items-center gap-1.5 border-b border-edge/70 pb-1">
        <span className="font-semibold text-stage-block">{block.id}</span>
        {role === 'entry' ? <span className="text-2xs text-severity-success">entry</span> : null}
        {role === 'exit' ? <span className="text-2xs text-violet-300">exit</span> : null}
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
      {badge ? (
        <p className="mt-1 truncate border-t border-edge/70 pt-1 text-2xs text-sky-300">{badge}</p>
      ) : null}
      <Handle type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !bg-edge-strong !border-0" />
    </div>
  )
}

const nodeTypes = { block: BlockNode }

export function BlockGraph({
  graph,
  selectedId,
  onSelect,
  badgeFor,
}: {
  graph: FunctionCfg
  selectedId: string | null
  onSelect: (blockId: string) => void
  badgeFor?: BlockBadge
}): JSX.Element {
  const { nodes, edges } = useMemo(() => {
    const laid = layoutGraph(graph)
    return {
      nodes: laid.nodes.map((node) => ({
        ...node,
        selected: node.id === selectedId,
        data: {
          ...node.data,
          badge: badgeFor?.(node.data.block) ?? null,
        },
      })),
      edges: laid.edges,
    }
  }, [graph, selectedId, badgeFor])

  return (
    <ReactFlow
      nodes={nodes}
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
    </ReactFlow>
  )
}

/** The edge palette as a legend. A graph whose colours have to be guessed is a diagram. */
export function EdgeLegend(): JSX.Element {
  return (
    <ul className="flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="Edge kinds">
      {Object.entries(EDGE_COLOUR).map(([kind, colour]) => (
        <li key={kind} className="flex items-center gap-1.5 font-mono text-2xs text-slate-500">
          <span
            aria-hidden="true"
            className="inline-block h-0.5 w-4 rounded"
            style={{ backgroundColor: colour }}
          />
          {kind}
        </li>
      ))}
    </ul>
  )
}
