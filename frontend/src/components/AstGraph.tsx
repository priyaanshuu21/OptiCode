import { useEffect, useMemo, type JSX } from 'react'
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'

import type { AstNode } from '@/api/types'
import { astChildren, astLabel, classifyNode, CLASS_BORDERS } from '@/lib/ast'

/**
 * The syntax tree, drawn.
 *
 * React Flow plus dagre, the same pair the CFG uses, because a hierarchy is a graph and drawing
 * it with anything else means hand-rolling pan, zoom and hit-testing. Dagre ranks the nodes from
 * the root downwards, so `Program` lands at the top and each statement hangs under the construct
 * that owns it — which is the question the AST exists to answer.
 *
 * The node text is the node's name and nothing else. Ids (`n7`), roles (`condition`,
 * `statement`) and source coordinates are the compiler's bookkeeping; the graph has room for
 * them, but a reader does not, and each one costs a little of the word's meaning. The inspector
 * beside the graph is where the attributes live.
 *
 * Nodes carry the backend's stable id rather than a positional one, so a selection survives a
 * re-layout and a node keeps its identity when the program is recompiled underneath it.
 */

/** Everything a node box needs. A node is a label and a colour. */
export interface AstNodeData extends Record<string, unknown> {
  /** The node itself, so a box can say what kind it is without a lookup table. */
  node: AstNode
  label: string
  border: string
}

/**
 * An edge that honours the one `smoothstep` option worth setting.
 *
 * `Edge` is an intersection type rather than an interface, so it cannot be augmented; naming the
 * extra field locally is the same honest workaround `lib/layout.ts` uses for its own edges.
 */
type TreeEdge = Edge & { pathOptions?: { borderRadius?: number } }

/**
 * Box metrics, in the units dagre is given.
 *
 * Dagre needs a width and a height per node *before* it will place anything and never measures
 * the DOM itself. One constant would either overlap long labels or leave a gap around short ones,
 * so the width is estimated from the label: the boxes use the monospace face, where a character
 * is a fixed fraction of the font size, so the sum is close enough that the separation absorbs
 * the error.
 */
const CHAR_WIDTH = 7.2
const BOX_PADDING = 28
const BOX_HEIGHT = 30
const MIN_BOX_WIDTH = 64

/** Vertical gap between ranks, wide enough for an edge's elbow to read as a single line. */
const RANK_SEPARATION = 44
/** Horizontal gap between siblings. */
const NODE_SEPARATION = 20

function boxOf(label: string): { width: number; height: number } {
  return {
    width: Math.max(MIN_BOX_WIDTH, Math.round(label.length * CHAR_WIDTH) + BOX_PADDING),
    height: BOX_HEIGHT,
  }
}

function AstNodeBox({ data, selected }: NodeProps<Node<AstNodeData>>): JSX.Element {
  const { node, label, border } = data
  return (
    <div
      // A bare `+` does not say it is a `BinaryExpr`; the tooltip does, for the cases where the
      // reader has not already worked it out from position.
      title={node.kind}
      className={`rounded-md border bg-slate-900 px-3 py-1.5 font-mono text-xs text-slate-200 shadow-md transition-colors ${
        selected ? 'border-stage-ast ring-1 ring-stage-ast/40' : border
      }`}
    >
      {/* Flush with the box edges, so an edge starts at the border instead of inside it. */}
      <Handle type="target" position={Position.Top} className="!h-1 !w-1 !border-0 !bg-slate-600" />
      <span className="whitespace-nowrap">{label}</span>
      <Handle type="source" position={Position.Bottom} className="!h-1 !w-1 !border-0 !bg-slate-600" />
    </div>
  )
}

const nodeTypes = { ast: AstNodeBox }

/**
 * Lay the tree out top to bottom.
 *
 * Edges carry no arrowhead. The ranks already make the direction unambiguous, and an arrowhead
 * on every edge of a fifty-node tree is fifty arrowheads repeating the same fact. The line is
 * thin and slate for the same reason: the tree is the content, the wiring is not.
 */
export function layoutAst(root: AstNode, selectedId: string | null): {
  nodes: Node<AstNodeData>[]
  edges: TreeEdge[]
} {
  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: 'TB', nodesep: NODE_SEPARATION, ranksep: RANK_SEPARATION, marginx: 24 })

  const boxes = new Map<string, AstNode>()
  const links: { parent: string; child: string }[] = []

  const visit = (node: AstNode): void => {
    boxes.set(node.id, node)
    graph.setNode(node.id, boxOf(astLabel(node)))
    for (const child of astChildren(node)) {
      links.push({ parent: node.id, child: child.id })
      visit(child)
    }
  }
  visit(root)

  for (const link of links) {
    graph.setEdge(link.parent, link.child)
  }
  dagre.layout(graph)

  const nodes: Node<AstNodeData>[] = []
  for (const [id, node] of boxes) {
    const placed = graph.node(id) as { x: number; y: number } | undefined
    const box = boxOf(astLabel(node))
    nodes.push({
      id,
      type: 'ast',
      // Dagre reports a node's centre; React Flow wants its top-left corner.
      position: { x: (placed?.x ?? 0) - box.width / 2, y: (placed?.y ?? 0) - box.height / 2 },
      data: { node, label: astLabel(node), border: CLASS_BORDERS[classifyNode(node)] },
      selected: id === selectedId,
      draggable: true,
      selectable: true,
    })
  }

  const edges: TreeEdge[] = links.map((link) => ({
    id: `${link.parent}->${link.child}`,
    source: link.parent,
    target: link.child,
    type: 'smoothstep',
    style: { stroke: '#334155', strokeWidth: 1.25 },
    pathOptions: { borderRadius: 10 },
  }))

  return { nodes, edges }
}

/**
 * Refit the viewport once the boxes have been measured, and again whenever the tree changes.
 *
 * The `fitView` prop on `<ReactFlow>` fires exactly once, which is right for a graph that does
 * not change and wrong for this one: recompiling replaces every node, and a viewport still
 * framing the old tree then frames empty space. `useNodesInitialized` is what makes the fit
 * *accurate* rather than merely attempted — dagre's coordinates are in and React Flow has
 * measured every box, so the bounds it fits are the real ones.
 */
function useRefitOnChange(tree: AstNode): void {
  const { fitView } = useReactFlow()
  const initialized = useNodesInitialized()

  useEffect(() => {
    if (initialized) {
      fitView({ padding: 0.15, duration: 200 })
    }
  }, [initialized, fitView, tree])
}

/** Must be a child of a `ReactFlowProvider`: both hooks below read the flow store from context. */
function AstCanvas({
  tree,
  selectedId,
  onSelect,
}: {
  tree: AstNode
  selectedId: string | null
  onSelect: (nodeId: string) => void
}): JSX.Element {
  const { nodes, edges } = useMemo(() => layoutAst(tree, selectedId), [tree, selectedId])
  useRefitOnChange(tree)

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_event, node) => onSelect(node.id)}
      minZoom={0.1}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      className="bg-surface-sunken"
    >
      <Background color="#26334f" gap={18} size={1} />
      <Controls showInteractive={false} />
    </ReactFlow>
  )
}

/**
 * The whole graph, provider included.
 *
 * The provider is here rather than in the view so a caller cannot get this wrong by forgetting
 * to wrap: `<ReactFlow>` supplies context to its own children, but not to the component that
 * renders it, which is exactly where the refit hook lives.
 */
export function AstGraph({
  tree,
  selectedId,
  onSelect,
}: {
  tree: AstNode
  selectedId: string | null
  onSelect: (nodeId: string) => void
}): JSX.Element {
  return (
    <ReactFlowProvider>
      <AstCanvas tree={tree} selectedId={selectedId} onSelect={onSelect} />
    </ReactFlowProvider>
  )
}
