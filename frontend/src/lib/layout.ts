/**
 * Turning a CFG into React Flow nodes and edges.
 *
 * React Flow positions every node absolutely and expects the caller to have decided the layout.
 * Dagre does that: it ranks blocks top to bottom, orders them within a rank, and returns a
 * coordinate per node. The alternative — hand-placing blocks — means every view of every
 * program has to be re-laid out by hand, which is exactly the kind of code that looks fine on
 * the three-block example and falls apart on the twenty-block one.
 *
 * Three things dagre does not do that this module adds:
 *
 * * **Node heights follow the content.** Dagre needs a height per node before it will place
 *   anything, and a block with eight instructions needs a taller box than one with two. Passing
 *   a single constant would either clip or leave a gap.
 * * **Every block keeps its id.** React Flow needs stable ids so that selection survives a
 *   re-layout, and because the CFG and data-flow views select a block by id to show its facts.
 * * **Entry and exit are drawn.** The CFG names its entry block and its synthetic exit, so the
 *   graph can say "execution starts here" and "these returns leave the function" without asking
 *   a reader to infer either from the absence of an arrow.
 *
 * Edges are coloured by what they *are* rather than by where they point, and the palette lives
 * here so the graph and its legend cannot disagree. The two branch arms get opposite hues
 * because "which way does this go" is the single most common question asked of a CFG, and a
 * reader should get the answer from colour before reading the label. Back edges are amber and
 * animated, because a back edge is a cycle and a cycle in a CFG is a loop.
 */

import dagre from 'dagre'
import { MarkerType, type Edge, type Node, type SmoothStepPathOptions } from '@xyflow/react'

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
const TERMINAL_WIDTH = 104
const TERMINAL_HEIGHT = 36
const LAYOUT_SEPARATION = 56

/**
 * The two ids the synthetic entry and exit nodes are drawn under.
 *
 * Deliberately the words rather than `B1`/`B2`: a block id is a number the reader can follow
 * into the inspector, and nothing should make `ENTRY` look like one. They also cannot collide
 * with a block id, which is always `B` followed by digits.
 */
export const ENTRY_ID = 'ENTRY'
export const EXIT_ID = 'EXIT'

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
  /** Every instruction in the block, so the node shows the block and not a summary of it. */
  preview: string[]
  /**
   * Instructions not shown, kept for a node whose list is capped.
   *
   * Always zero today — the node shows the whole block — but the renderer still reads it, so a
   * future cap does not mean a second copy of this file.
   */
  hidden: number
  /**
   * Which of the function's terminal nodes this is, or `body` for a real block.
   *
   * Taken from the graph rather than inferred from having no predecessors, because "no
   * predecessors" is also true of a block whose only way out is a return — and a block that
   * returns is emphatically not the exit node of the function.
   */
  role: 'entry' | 'exit' | 'body'
  /** True for the synthetic `ENTRY`/`EXIT` nodes, which draw as small terminals. */
  synthetic: boolean
  /** What the node header shows: `ENTRY`, `EXIT`, or the block id. */
  label: string
}

/** A block with no code, standing in for the synthetic entry and exit terminals. */
function placeholderBlock(id: string, reachable: boolean): Block {
  return {
    id,
    labels: [],
    leader_reason: '',
    instructions: [],
    instruction_ids: [],
    successors: [],
    predecessors: [],
    reachable,
    span: { start_line: 0, start_col: 0, end_line: 0, end_col: 0 },
  }
}

function previewOf(block: Block): { preview: string[]; hidden: number } {
  // The backend's own `text` field is used elsewhere; here the summary helper is, so a block
  // node and the TAC table describe the same instruction the same way. No slice: the node is
  // the block, and a node that hides half a block cannot answer "what is in B7".
  return { preview: block.instructions.map(instructionSummary), hidden: 0 }
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
    const { preview } = previewOf(block)
    // Height grows with the preview, or an eight-instruction block would clip.
    const height = Math.max(NODE_HEIGHT, 40 + preview.length * 18)
    dagreGraph.setNode(block.id, { width: NODE_WIDTH, height })
  }
  // The terminals are dagre nodes too, so the entry ranks above B1 and the exit below every
  // returning block without any manual nudging.
  dagreGraph.setNode(ENTRY_ID, { width: TERMINAL_WIDTH, height: TERMINAL_HEIGHT })
  dagreGraph.setNode(EXIT_ID, { width: TERMINAL_WIDTH, height: TERMINAL_HEIGHT })

  if (graph.entry && dagreGraph.hasNode(graph.entry)) {
    dagreGraph.setEdge(ENTRY_ID, graph.entry)
  }
  for (const edge of graph.edges) {
    // `edge.dst` is a block id or the graph's exit id; both are nodes by now, so a return edge
    // is laid out rather than dropped, which is what put the exit nowhere before.
    if (dagreGraph.hasNode(edge.src) && dagreGraph.hasNode(edge.dst)) {
      // Parallel edges share a pair; dagre only gives a position, so a plain pair is enough.
      dagreGraph.setEdge(edge.src, edge.dst)
    }
  }
  dagre.layout(dagreGraph)

  const positioned = (id: string, width: number, height: number): { x: number; y: number } => {
    const node = dagreGraph.node(id) as { x?: number; y?: number; width?: number; height?: number } | undefined
    const w = node?.width ?? width
    const h = node?.height ?? height
    return { x: (node?.x ?? 0) - w / 2, y: (node?.y ?? 0) - h / 2 }
  }

  const nodes: Node<BlockNodeData>[] = [
    {
      id: ENTRY_ID,
      type: 'block',
      position: positioned(ENTRY_ID, TERMINAL_WIDTH, TERMINAL_HEIGHT),
      data: {
        block: placeholderBlock(ENTRY_ID, true),
        preview: [],
        hidden: 0,
        role: 'entry',
        synthetic: true,
        label: ENTRY_ID,
      },
      draggable: true,
      selectable: false,
    },
    ...graph.blocks.map((block): Node<BlockNodeData> => {
      const { preview, hidden } = previewOf(block)
      return {
        id: block.id,
        type: 'block',
        position: positioned(block.id, NODE_WIDTH, NODE_HEIGHT),
        data: { block, preview, hidden, role: 'body', synthetic: false, label: block.id },
        draggable: true,
        selectable: true,
      }
    }),
    {
      id: EXIT_ID,
      type: 'block',
      position: positioned(EXIT_ID, TERMINAL_WIDTH, TERMINAL_HEIGHT),
      data: {
        block: placeholderBlock(EXIT_ID, true),
        preview: [],
        hidden: 0,
        role: 'exit',
        synthetic: true,
        label: EXIT_ID,
      },
      draggable: true,
      selectable: false,
    },
  ]

  // Count how many edges share a source/target pair, so two arms of a branch that both fall on
  // the same block are fanned apart instead of drawn on top of each other.
  const pairCounts = new Map<string, number>()
  for (const edge of graph.edges) {
    const key = `${edge.src}->${edge.dst}`
    pairCounts.set(key, (pairCounts.get(key) ?? 0) + 1)
  }
  const pairSeen = new Map<string, number>()

  const edges: FlowEdge[] = []
  if (graph.entry && dagreGraph.hasNode(graph.entry)) {
    edges.push(terminalEdge(`${ENTRY_ID}->${graph.entry}:entry`, ENTRY_ID, graph.entry))
  }
  for (const edge of graph.edges) {
    const key = `${edge.src}->${edge.dst}`
    const total = pairCounts.get(key) ?? 1
    const seen = pairSeen.get(key) ?? 0
    pairSeen.set(key, seen + 1)
    edges.push(toFlowEdge(edge, graph, seen, total))
  }

  return { nodes, edges }
}

/**
 * The synthetic `ENTRY → entry block` edge.
 *
 * A distinct, muted colour rather than one of the CFG kinds: it is not control flow the program
 * wrote, it is the graph saying where the function begins.
 */
function terminalEdge(id: string, source: string, target: string): FlowEdge {
  const colour = EDGE_COLOUR.fallthrough
  return {
    id,
    source,
    target,
    type: EDGE_KIND,
    label: 'entry',
    style: { stroke: colour, strokeWidth: 1.5, strokeDasharray: '2 2' },
    markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
    labelStyle: { fill: '#94a3b8', fontSize: 10 },
    labelBgStyle: { fill: '#111c2e' },
    pathOptions: { borderRadius: 14 },
    data: { kind: 'entry', isBackEdge: false, hasLoops: false },
  }
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

function toFlowEdge(
  edge: CfgEdge,
  graph: FunctionCfg,
  indexInPair: number,
  pairCount: number,
): FlowEdge {
  const isLoop = edge.is_back_edge
  // A back edge is labelled by its own kind, not by "loop": the loop is a property of the
  // graph, and the reader still wants to know whether it closes a `while` or a `goto`.
  const colour: string = isLoop
    ? EDGE_COLOUR.back
    : (EDGE_COLOUR[edge.kind as EdgeKind] ?? EDGE_COLOUR.fallthrough)
  // Parallel edges are fanned apart; a lone edge sits straight.
  const parallelOffset = pairCount > 1 ? (indexInPair - (pairCount - 1) / 2) * 26 : 0
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
    labelBgStyle: { fill: '#111c2e' },
    markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
    // Two back edges between the same pair of blocks would otherwise draw on top of each
    // other; so would the two arms of a branch that land on the same block. Fanning them out
    // by index is what makes both readable.
    pathOptions: {
      borderRadius: 14,
      offset: isLoop ? 18 + indexInPair * 8 : parallelOffset,
    },
    data: { kind: edge.kind, isBackEdge: isLoop, hasLoops: graph.has_loops },
  }
}

/** Block ids in reverse post-order, which is the order a person reads a function in. */
export function readableOrder(graph: FunctionCfg): string[] {
  return graph.rpo.length > 0 ? graph.rpo : graph.blocks.map((block) => block.id)
}
