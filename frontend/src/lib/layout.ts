/**
 * Turning a CFG into React Flow nodes and edges.
 *
 * React Flow positions every node absolutely and expects the caller to have decided the layout.
 * Dagre does that: it ranks blocks top to bottom, orders them within a rank, and returns a
 * coordinate per node. The alternative — hand-placing blocks — means every view of every
 * program has to be re-laid out by hand, which is exactly the kind of code that looks fine on
 * the three-block example and falls apart on the twenty-block one.
 *
 * Two things dagre does not do that this module adds:
 *
 * * **Node heights follow the content.** Dagre needs a height per node before it will place
 *   anything, and a block with five instructions needs a taller box than one with two. Passing
 *   a single constant would either clip or leave a gap.
 * * **Every block keeps its id.** React Flow needs stable ids so that selection survives a
 *   re-layout, and because the Blocks view selects a block by id to show its instructions.
 *
 * Back edges are drawn animated and in amber, because a back edge is a cycle, and a cycle in a
 * CFG is a loop. `smoothstep` routing on its own draws it as a line straight back up the graph,
 * which reads as an error rather than as control flow.
 */

import dagre from 'dagre'
import type { Edge, Node } from '@xyflow/react'

import type { Block, Edge as CfgEdge, FunctionCfg } from '@/api/types'

const NODE_WIDTH = 260
const NODE_HEIGHT = 120
const LAYOUT_SEPARATION = 56

/** React Flow's built-in smooth step looks wrong on the straight runs dagre produces. */
const EDGE_KIND = 'smoothstep' as const

/** One block plus the display data a node needs. */
export interface BlockNodeData extends Record<string, unknown> {
  block: Block
  /** Lines of TAC shown in the node body. */
  preview: string[]
  /** How many instructions the block holds beyond the preview. */
  hidden: number
}

function previewOf(block: Block, limit = 4): { preview: string[]; hidden: number } {
  const lines = block.instructions.slice(0, limit).map((instruction) => {
    const target = instruction.dest
    const destination = target?.name ? `${target.name} = ` : ''
    const operands = instruction.operands
      .map((operand) => (operand.kind === 'const' ? String(operand.value) : (operand.name ?? '?')))
      .join(', ')
    if (instruction.opcode === 'goto') {
      return `goto ${instruction.target ?? '?'}`
    }
    if (instruction.opcode === 'if_false') {
      return `if_false ${operands} → ${instruction.target ?? '?'}`
    }
    if (instruction.opcode === 'label') {
      return `${instruction.target ?? ''}:`
    }
    return `${destination}${instruction.opcode} ${operands}`.trim()
  })
  return { preview: lines, hidden: Math.max(0, block.instructions.length - limit) }
}

/** Lay out one function's CFG. Returns nodes and edges ready for React Flow. */
export function layoutGraph(graph: FunctionCfg): {
  nodes: Node<BlockNodeData>[]
  edges: Edge[]
} {
  const dagreGraph = new dagre.graphlib.Graph()
  dagreGraph.setDefaultEdgeLabel(() => ({}))
  dagreGraph.setGraph({
    rankdir: 'TB',
    nodesep: LAYOUT_SEPARATION,
    ranksep: LAYOUT_SEPARATION * 1.4,
    // Left-to-right room for back edges, so routing them does not need extra margins.
    marginx: 48,
  })

  for (const block of graph.blocks) {
    const { preview, hidden } = previewOf(block)
    // Height grows with the preview, or a five-instruction block would clip.
    const height = Math.max(NODE_HEIGHT, 56 + preview.length * 18 + (hidden > 0 ? 20 : 0))
    dagreGraph.setNode(block.id, { width: NODE_WIDTH, height })
  }
  for (const edge of graph.edges) {
    if (dagreGraph.hasNode(edge.src) && dagreGraph.hasNode(edge.dst)) {
      dagreGraph.setEdge(edge.src, edge.dst)
    }
  }
  dagre.layout(dagreGraph)

  const nodes: Node<BlockNodeData>[] = graph.blocks.map((block) => {
    const positioned = dagreGraph.node(block.id) ?? { x: 0, y: 0 }
    const { preview, hidden } = previewOf(block)
    return {
      id: block.id,
      type: 'block',
      position: { x: positioned.x - NODE_WIDTH / 2, y: positioned.y - positioned.height / 2 },
      data: { block, preview, hidden },
      draggable: true,
      selectable: true,
    }
  })

  const edges: Edge[] = graph.edges
    .filter((edge) => graph.blocks.some((block) => block.id === edge.dst))
    .map((edge) => toFlowEdge(edge, graph))

  return { nodes, edges }
}

function toFlowEdge(edge: CfgEdge, graph: FunctionCfg): Edge {
  const isLoop = edge.is_back_edge
  return {
    id: `${edge.src}->${edge.dst}:${edge.kind}`,
    source: edge.src,
    target: edge.dst,
    type: EDGE_KIND,
    label: edge.kind,
    animated: isLoop,
    className: isLoop ? 'edge-loop' : edge.kind === 'return' ? 'edge-return' : undefined,
    style: isLoop
      ? { stroke: '#f59e0b', strokeWidth: 2 }
      : edge.kind === 'return'
        ? { stroke: '#64748b', strokeDasharray: '4 3' }
        : { stroke: '#38bdf8' },
    labelStyle: { fill: '#94a3b8', fontSize: 10 },
    labelBgStyle: { fill: '#121a30' },
    data: { kind: edge.kind, isBackEdge: isLoop, hasLoops: graph.has_loops },
  }
}

/** Block ids in reverse post-order, which is the order a person reads a function in. */
export function readableOrder(graph: FunctionCfg): string[] {
  return graph.rpo.length > 0 ? graph.rpo : graph.blocks.map((block) => block.id)
}
