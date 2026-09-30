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
 *   re-layout, and because the CFG and data-flow views select a block by id to show its facts.
 *
 * Edges are coloured by what they *are* rather than by where they point, and the palette lives
 * here so the graph and its legend cannot disagree. The two branch arms get opposite hues
 * because "which way does this go" is the single most common question asked of a CFG, and a
 * reader should get the answer from colour before reading the label. Back edges are amber and
 * animated, because a back edge is a cycle and a cycle in a CFG is a loop.
 */

import dagre from 'dagre'
import type { Edge, Node, SmoothStepPathOptions } from '@xyflow/react'

import type { Block, Edge as CfgEdge, FunctionCfg } from '@/api/types'
import { instructionSummary } from '@/lib/ir'

/**
 * An edge, plus the one field a `smoothstep` edge honours that the library's `Edge` type omits.
 *
 * React Flow has a `SmoothStepEdge` type internally, but it is not exported — only the union
 * `BuiltInEdge` is, which cannot be narrowed back down — and `Edge` itself is an intersection
 * type rather than an interface, so augmenting it is not possible either. Intersecting with the
 * library's own `SmoothStepPathOptions` is the honest way to say what these edges really are.
 */
export type FlowEdge = Edge & { pathOptions?: SmoothStepPathOptions }

const NODE_WIDTH = 260
const NODE_HEIGHT = 120
const LAYOUT_SEPARATION = 56

/** React Flow's built-in smooth step looks wrong on the straight runs dagre produces. */
const EDGE_KIND = 'smoothstep' as const

/** One colour per edge kind. A branch's two arms must be tellable apart at a glance. */
export const EDGE_COLOUR = {
  fallthrough: '#38bdf8',
  true: '#34d399',
  false: '#fb7185',
  jump: '#a78bfa',
  return: '#64748b',
  back: '#f59e0b',
} as const satisfies Record<string, string>

export type EdgeKind = keyof typeof EDGE_COLOUR

/** What the legend shows, in the order a reader meets them in a function. */
export const EDGE_LEGEND: { kind: EdgeKind; label: string; colour: string }[] = [
  { kind: 'fallthrough', label: 'falls through', colour: EDGE_COLOUR.fallthrough },
  { kind: 'true', label: 'if → then', colour: EDGE_COLOUR.true },
  { kind: 'false', label: 'if → else', colour: EDGE_COLOUR.false },
  { kind: 'jump', label: 'goto', colour: EDGE_COLOUR.jump },
  { kind: 'return', label: 'returns', colour: EDGE_COLOUR.return },
  { kind: 'back', label: 'loop', colour: EDGE_COLOUR.back },
]

/** One block plus the display data a node needs. */
export interface BlockNodeData extends Record<string, unknown> {
  block: Block
  /** Lines of TAC shown in the node body. */
  preview: string[]
  /** How many instructions the block holds beyond the preview. */
  hidden: number
  /**
   * Whether this is the graph's entry or exit block.
   *
   * Taken from the graph rather than inferred from having no predecessors, because "no
   * predecessors" is also true of a block whose only way out is a return — and a block that
   * returns is emphatically not the exit node of the function.
   */
  role: 'entry' | 'exit' | 'body'
}

function previewOf(block: Block, limit = 4): { preview: string[]; hidden: number } {
  // The backend's own `text` field is used elsewhere; here the summary helper is, so a block
  // node and the TAC table describe the same instruction the same way.
  const lines = block.instructions.slice(0, limit).map(instructionSummary)
  return { preview: lines, hidden: Math.max(0, block.instructions.length - limit) }
}

/** Lay out one function's CFG. Returns nodes and edges ready for React Flow. */
export function layoutGraph(graph: FunctionCfg): {
  nodes: Node<BlockNodeData>[]
  edges: FlowEdge[]
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
      data: { block, preview, hidden, role: roleOf(block.id, graph) },
      draggable: true,
      selectable: true,
    }
  })

  const edges: FlowEdge[] = graph.edges
    .filter((edge) => graph.blocks.some((block) => block.id === edge.dst))
    .map((edge, index) => toFlowEdge(edge, graph, index))

  return { nodes, edges }
}

/** Entry, exit, or neither — decided by the graph's own labels, not by the neighbours. */
export function roleOf(blockId: string, graph: FunctionCfg): 'entry' | 'exit' | 'body' {
  if (blockId === graph.entry) {
    return 'entry'
  }
  if (blockId === graph.exit) {
    return 'exit'
  }
  return 'body'
}

function toFlowEdge(edge: CfgEdge, graph: FunctionCfg, index: number): FlowEdge {
  const isLoop = edge.is_back_edge
  // A back edge is labelled by its own kind, not by "loop": the loop is a property of the
  // graph, and the reader still wants to know whether it closes a `while` or a `goto`.
  const colour: string = isLoop
    ? EDGE_COLOUR.back
    : (EDGE_COLOUR[edge.kind as EdgeKind] ?? EDGE_COLOUR.fallthrough)
  return {
    id: `${edge.src}->${edge.dst}:${edge.kind}`,
    source: edge.src,
    target: edge.dst,
    type: EDGE_KIND,
    label: isLoop ? `${edge.kind} · loop` : edge.kind,
    animated: isLoop,
    className: isLoop ? 'edge-loop' : edge.kind === 'return' ? 'edge-return' : undefined,
    style: {
      stroke: colour,
      strokeWidth: isLoop ? 2 : 1.5,
      // A return does not go anywhere a reader can follow, so it is drawn as an ending rather
      // than as a path to a destination.
      strokeDasharray: edge.kind === 'return' ? '4 3' : undefined,
    },
    labelStyle: { fill: '#94a3b8', fontSize: 10 },
    labelBgStyle: { fill: '#121a30' },
    // Two back edges between the same pair of blocks would otherwise draw on top of each
    // other; fanning them out by index is what makes nested loops readable.
    pathOptions: { borderRadius: 14, offset: isLoop ? 18 + index * 8 : 0 },
    data: { kind: edge.kind, isBackEdge: isLoop, hasLoops: graph.has_loops },
  }
}

/** Block ids in reverse post-order, which is the order a person reads a function in. */
export function readableOrder(graph: FunctionCfg): string[] {
  return graph.rpo.length > 0 ? graph.rpo : graph.blocks.map((block) => block.id)
}
