import { useCallback, useMemo, useState, type JSX } from 'react'

import { astOf, stageSkipped } from '@/api/client'
import type { AstNode } from '@/api/types'
import { astChildren, astDepth, astLabel, astNodes, attributeRows } from '@/lib/ast'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'
import { AstGraph } from '@/components/AstGraph'

/**
 * The abstract syntax tree.
 *
 * A diagram rather than a list. An indented outline of a real function is fifty to a few hundred
 * rows of monospace, and what a reader wants from it — *what is inside this `if`*, *where does
 * this loop end* — is a question about shape. Shape is what a tree drawn root-downwards answers
 * at a glance and an outline answers only by scrolling.
 *
 * React Flow and dagre do the drawing and the placing, the same pair the CFG uses; see
 * `AstGraph.tsx` for why. `Block` nodes never reach the screen: a statement list says nothing,
 * and a box for one is a box the reader clicks through to reach what they came for.
 *
 * Nothing on screen is an id, a role, or a coordinate. Those are the compiler's bookkeeping, and
 * the inspector beside the graph is where the attributes live. Clicking a node is still the
 * bridge to the source: it selects the node's span in the editor, so the tree and the text stay
 * the same picture.
 */

/** The node the inspector is describing, or null when nothing is selected or it is stale. */
function NodeInspector({ node }: { node: AstNode }): JSX.Element {
  const rows = attributeRows(node)
  const children = astChildren(node)

  return (
    <div className="space-y-3">
      <p className="font-mono text-xs font-semibold text-stage-ast">{astLabel(node)}</p>
      {node.semantic_type ? (
        <div>
          <p className="text-2xs uppercase tracking-wider text-slate-500">Type</p>
          <p className="font-mono text-xs text-emerald-300">{node.semantic_type}</p>
        </div>
      ) : null}
      {rows.length > 0 ? (
        <div>
          <p className="text-2xs uppercase tracking-wider text-slate-500">Attributes</p>
          <dl className="mt-1 space-y-0.5">
            {rows.map((row) => (
              <div key={row.key} className="flex gap-2 font-mono text-2xs">
                <dt className="shrink-0 text-slate-500">{row.key}</dt>
                <dd className="min-w-0 break-all text-slate-200">{row.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {children.length > 0 ? (
        <div>
          <p className="text-2xs uppercase tracking-wider text-slate-500">
            Children ({children.length})
          </p>
          {/* The same children the graph hangs below this node, labelled the same way. A list
              that disagreed with the picture under it would be a second thing to keep in sync,
              and the only entry it would add is the `Block` the graph deliberately hides. */}
          <ul className="mt-1 space-y-0.5">
            {children.map((child) => (
              <li key={child.id} className="font-mono text-2xs text-slate-300">
                {astLabel(child)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

export function AstView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const ast = response ? astOf(response) : null
  // Both counted over the tree as drawn, so the header agrees with what is on the canvas, and
  // measured once rather than on every render of the badges.
  const nodes = useMemo(() => (ast ? astNodes(ast) : []), [ast])
  const depth = useMemo(() => (ast ? astDepth(ast) : 0), [ast])
  const selected = useMemo(
    () => nodes.find((node) => node.id === selectedId) ?? null,
    [nodes, selectedId],
  )

  const handleSelect = useCallback(
    (nodeId: string) => {
      const node = nodes.find((candidate) => candidate.id === nodeId)
      if (!node) {
        return
      }
      setSelectedId(node.id)
      // Selecting a node is why the editor highlights a span. `endLine` is clamped because a
      // one-line node still has to be a valid range for Monaco.
      select({
        line: node.span.start_line,
        endLine: Math.max(node.span.end_line, node.span.start_line),
        origin: `AST node ${astLabel(node)}`,
      })
    },
    [nodes, select],
  )

  if (!response) {
    return (
      <Panel title="AST" subtitle="Waiting for the first compile.">
        <EmptyState
          title="Nothing to show yet"
          detail="Type a program and its syntax tree will appear here."
        />
      </Panel>
    )
  }

  if (!ast) {
    return (
      <Panel title="AST" subtitle="The parser did not produce a tree.">
        <EmptyState
          title="No tree"
          detail="The parser was skipped or failed, so there is nothing to draw. Errors from the parser will be in Diagnostics."
          skipped={stageSkipped(response, 'ast')}
        />
      </Panel>
    )
  }

  return (
    <Panel
      title="AST"
      subtitle="Abstract Syntax Tree (AST)"
      actions={
        <div className="flex items-center gap-2">
          <Count label={`${nodes.length} nodes`}>{nodes.length} nodes</Count>
          <Count label={`depth ${depth}`}>depth {depth}</Count>
        </div>
      }
    >
      <div className="flex h-full min-h-0">
        <div className="min-h-0 flex-1">
          <AstGraph tree={ast} selectedId={selectedId} onSelect={handleSelect} />
        </div>
        <aside className="w-72 shrink-0 overflow-auto border-l border-edge bg-surface-sunken p-3">
          {selected ? (
            <NodeInspector node={selected} />
          ) : (
            <EmptyState
              title="No node selected"
              detail="Select a node in the graph to see its attributes and children."
            />
          )}
        </aside>
      </div>
    </Panel>
  )
}
