import { useMemo, useState, type JSX } from 'react'

import { astOf, stageSkipped } from '@/api/client'
import type { AstNode } from '@/api/types'
import { countAstNodes, formatSpan, maxAstDepth } from '@/lib/span'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'

/**
 * The parse tree.
 *
 * An indented outline rather than a node-link diagram. That is a deliberate choice: an AST for
 * a real function is fifty to a few hundred nodes, and a graph of that size is a hairball. The
 * indented form answers the questions a reader actually has — what is the shape, what is inside
 * this `if`, what type did the analyzer give this expression — and it scrolls.
 *
 * The detail panel is what a graph would have given for free: clicking a node shows its full
 * attribute list and selects its span in the editor.
 */

/** One flattened row with its tree-branch connector prefix. */
interface AstEntry {
  node: AstNode
  depth: number
  role: string
  prefix: string
  isLast: boolean
}

/**
 * Flatten the tree depth-first, computing a `├── `/`└── `/`│   ` connector prefix per row.
 *
 * `parentLasts` tracks, for each ancestor level that occupies a guide column, whether that
 * ancestor was the last child of its own parent. A last-child ancestor renders blank space
 * (`    `) because its subtree is finished; any other ancestor renders a vertical guide
 * (`│   `) because more siblings follow below it.
 */
function buildAstEntries(root: AstNode): AstEntry[] {
  const out: AstEntry[] = []
  const walk = (
    node: AstNode,
    depth: number,
    role: string,
    isLast: boolean,
    parentLasts: boolean[],
  ): void => {
    let prefix = ''
    if (depth > 0) {
      for (const ancestorLast of parentLasts) {
        prefix += ancestorLast ? '    ' : '│   '
      }
      prefix += isLast ? '└── ' : '├── '
    }
    out.push({ node, depth, role, prefix, isLast })
    const children = node.children ?? []
    children.forEach((child, index) => {
      const childLast = index === children.length - 1
      // The root has no connector column of its own, so it contributes nothing to the guide.
      const childParents = depth === 0 ? [] : [...parentLasts, isLast]
      walk(child.node, depth + 1, child.role, childLast, childParents)
    })
  }
  walk(root, 0, 'root', true, [])
  return out
}

/** Attribute keys rendered as `key: value` when there is no dedicated row for them. */
function attributeRows(node: AstNode): { key: string; value: string }[] {
  const rows: { key: string; value: string }[] = []
  for (const [key, value] of Object.entries(node.attrs)) {
    if (value === null || value === undefined) {
      continue
    }
    if (typeof value === 'object') {
      rows.push({ key, value: JSON.stringify(value) })
      continue
    }
    rows.push({ key, value: String(value) })
  }
  return rows
}

export function AstView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const ast = response ? astOf(response) : null

  const entries = useMemo(
    () => (ast ? buildAstEntries(ast) : []),
    [ast],
  )
  const selected = useMemo(
    () => entries.find((entry) => entry.node.id === selectedId)?.node ?? null,
    [entries, selectedId],
  )

  if (!response) {
    return (
      <Panel title="AST" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Type a program and the parse tree will appear here." />
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
      subtitle="The parse tree, indented by depth. Select a node to see its attributes and highlight its span."
      actions={
        <div className="flex items-center gap-2">
          <Count label={`${countAstNodes(ast)} nodes`}>{countAstNodes(ast)} nodes</Count>
          <Count label={`depth ${maxAstDepth(ast)}`}>depth {maxAstDepth(ast)}</Count>
        </div>
      }
    >
      <div className="flex h-full min-h-0">
        <div className="min-h-0 flex-1 overflow-auto py-1">
          {entries.map(({ node, depth, role, prefix }) => {
            const isSelected = node.id === selectedId
            return (
              <button
                key={node.id}
                type="button"
                onClick={() => {
                  setSelectedId(node.id)
                  select({
                    line: node.span.start_line,
                    endLine: node.span.end_line,
                    origin: `AST node ${node.kind}`,
                  })
                }}
                className={`flex w-full items-baseline gap-2 px-3 py-0.5 text-left font-mono text-xs hover:bg-surface-overlay ${
                  isSelected ? 'bg-surface-overlay text-stage-ast' : 'text-slate-300'
                }`}
                style={{ paddingLeft: `${depth * 14 + 12}px` }}
              >
                {prefix ? (
                  <span aria-hidden="true" className="shrink-0 whitespace-pre text-slate-600">
                    {prefix}
                  </span>
                ) : null}
                <span className="text-slate-600">{role}</span>
                <span className="text-slate-200">{node.kind}</span>
                <span className="text-slate-500">{node.id}</span>
                {node.semantic_type ? (
                  <span className="rounded bg-surface-overlay px-1 text-2xs text-emerald-300">
                    {node.semantic_type}
                  </span>
                ) : null}
                <span className="ml-auto shrink-0 text-2xs text-slate-600">
                  {node.span.start_line}:{node.span.start_col}
                </span>
              </button>
            )
          })}
        </div>

        <aside className="w-72 shrink-0 overflow-auto border-l border-edge bg-surface-sunken p-3">
          {selected ? (
            <div className="space-y-3">
              <div>
                <p className="font-mono text-xs font-semibold text-stage-ast">{selected.kind}</p>
                <p className="font-mono text-2xs text-slate-500">{selected.id}</p>
              </div>
              {selected.semantic_type ? (
                <div>
                  <p className="text-2xs uppercase tracking-wider text-slate-500">Type</p>
                  <p className="font-mono text-xs text-emerald-300">{selected.semantic_type}</p>
                </div>
              ) : null}
              {attributeRows(selected).length > 0 ? (
                <div>
                  <p className="text-2xs uppercase tracking-wider text-slate-500">Attributes</p>
                  <dl className="mt-1 space-y-0.5">
                    {attributeRows(selected).map((row) => (
                      <div key={row.key} className="flex gap-2 font-mono text-2xs">
                        <dt className="shrink-0 text-slate-500">{row.key}</dt>
                        <dd className="min-w-0 break-all text-slate-200">{row.value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ) : null}
              <div>
                <p className="text-2xs uppercase tracking-wider text-slate-500">Span</p>
                <p className="font-mono text-2xs text-slate-300">{formatSpan(selected.span)}</p>
              </div>
              {selected.children.length > 0 ? (
                <div>
                  <p className="text-2xs uppercase tracking-wider text-slate-500">
                    Children ({selected.children.length})
                  </p>
                  <ul className="mt-1 space-y-0.5">
                    {selected.children.map((child, index) => (
                      <li key={`${child.node.id}-${index}`} className="font-mono text-2xs text-slate-400">
                        <span className="text-slate-600">{child.role}</span> {child.node.kind}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : (
            <EmptyState
              title="No node selected"
              detail="Select a node in the tree to see its type, attributes, and source span."
            />
          )}
        </aside>
      </div>
    </Panel>
  )
}
