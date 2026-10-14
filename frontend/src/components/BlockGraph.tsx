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

function handleClass(): string {
  return '!h-1.5 !w-1.5 !border-0 !bg-edge-strong'
}

function BlockNode({ data, selected }: NodeProps<Node<BlockNodeData>>): JSX.Element {
  if (data.synthetic) {
    const isEntry = data.role === 'entry'
    return (
      <div
        className={`flex w-[104px] items-center justify-center rounded-full border px-3 py-1 font-mono text-2xs font-semibold uppercase tracking-wider shadow-lg ${
          isEntry
            ? 'border-severity-success/60 text-severity-success'
            : 'border-violet-400/50 text-violet-300'
        }`}
      >
        {isEntry ? (
          <Handle type="source" position={Position.Bottom} className={handleClass()} />
        ) : (
          <Handle type="target" position={Position.Top} className={handleClass()} />
        )}
        {data.label}
      </div>
    )
  }
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
      className={`rounded-md border bg-surface-raised px-2 py-1.5 font-mono text-2xs shadow-lg transition-colors ${
        selected ? 'border-stage-block' : tone
      }`}
      style={{ width: 260 }}
    >
      <Handle type="target" position={Position.Top} className={handleClass()} />
      <div className="flex items-center gap-1.5 border-b border-edge/70 pb-1">
        <span className="font-semibold text-stage-block">{block.id}</span>
        {!block.reachable ? <span className="text-2xs text-severity-warning">unreachable</span> : null}
        {block.labels.length > 0 ? (
          <span className="truncate text-2xs text-slate-400">{block.labels.join(', ')}</span>
        ) : null}
        <span className="ml-auto text-slate-400">{block.instructions.length}</span>
      </div>
      <ul className="mt-1 space-y-0.5">
        {preview.map((line, index) => (
          <li key={index} className="break-words text-slate-300">
            {line}
          </li>
        ))}
        {preview.length === 0 ? <li className="text-slate-400">empty block</li> : null}
        {hidden > 0 ? <li className="text-slate-400">+{hidden} more</li> : null}
      </ul>
      {badge ? (
        <p className="mt-1 break-words border-t border-edge/70 pt-1 text-2xs text-sky-300">{badge}</p>
      ) : null}
      <Handle type="source" position={Position.Bottom} className={handleClass()} />
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
          // A synthetic terminal is not a block, so it has no live set to show.
          badge: node.data.synthetic ? null : (badgeFor?.(node.data.block) ?? null),
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
      onNodeClick={(_event, node) => {
        // `ENTRY` and `EXIT` are the function's boundaries, not blocks in it; selecting one
        // would show an inspector for an empty placeholder.
        if (!(node.data as BlockNodeData).synthetic) {
          onSelect(node.id)
        }
      }}
      // Readable by default rather than shrunk to fit: `fitView` zooms a large function out until
      // the instruction text inside the blocks is too small to read, which defeats the point of
      // showing the instructions. The graph opens at 1:1 from the top-left and the reader pans,
      // zooms, or presses the controls' fit-view button to take in the whole shape at once.
      defaultViewport={{ x: 24, y: 24, zoom: 1 }}
      minZoom={0.2}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      className="bg-surface-sunken"
    >
      <Background color="#26334f" gap={18} size={1} />
      {/* No colour overrides here: `.react-flow__controls` in `index.css` themes the zoom
          buttons for every graph, and the library's light default cannot be overridden by a
          class on the container anyway. */}
      <Controls showInteractive={false} />
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
