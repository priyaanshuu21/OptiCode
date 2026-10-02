import { useMemo, useState, type JSX } from 'react'

import { astOf, stageSkipped } from '@/api/client'
import type { AstNode } from '@/api/types'
import { countAstNodes, formatSpan, maxAstDepth } from '@/lib/span'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'

/**
 * The abstract syntax tree.
 *
 * An indented outline rather than a node-link diagram. That is a deliberate choice: an AST for
 * a real function is fifty to a few hundred nodes, and a graph of that size is a hairball. The
 * indented form answers the questions a reader actually have — what is the shape, what is inside
 * this `if`, what type did the analyzer give this expression — and it scrolls.
 *
 * The rows are deliberately terse. `Function main`, `Assign (total)`, `+` say what a node is. The
 * role it plays in its parent — `condition`, `statement`, `init`, `value` — is bookkeeping for the
 * compiler, and a column of it in front of every row is a column nobody reads. `Block` nodes are
 * grouping and nothing else, so their statements are spliced into the enclosing construct instead
 * of each getting a row of its own.
 *
 * The branch lines are borders and pseudo-elements rather than `├── `/`└── ` glyphs. A text
 * connector has to be assembled per row out of spacer and guide characters, which only lines up
 * while every row is exactly the same height; one taller row turns the tree into a staircase.
 * Borders and pseudo-elements are laid out by the same box model as the text they point at, so
 * they cannot drift.
 *
 * The detail panel is what a graph would have given for free: clicking a node shows its full
 * attribute list and selects its span in the editor.
 */

/**
 * A string attribute of a node, or null when it is absent or is not a string.
 *
 * Written defensively because `attrs` is whatever the backend put there. A node kind that grows
 * an attribute, or loses one, should change this panel's labels at worst — never crash it.
 */
function stringAttr(node: AstNode, key: string): string | null {
  const value = node.attrs[key]
  return typeof value === 'string' ? value : null
}

/**
 * The row label: the node kind, plus the one attribute that says *which* one it is.
 *
 * A kind this build has never heard of falls through to its raw name. That is the honest
 * rendering — a row reading `VarDecl` is a lookup away from being understood, whereas a row
 * rendering nothing looks like a bug.
 */
function nodeLabel(node: AstNode): string {
  switch (node.kind) {
    case 'FunctionDecl':
      return `Function ${stringAttr(node, 'name') ?? '?'}`
    case 'VarDecl':
      return `Var ${stringAttr(node, 'name') ?? '?'}`
    case 'Param':
      return `Param ${stringAttr(node, 'name') ?? '?'}`
    case 'Assign':
      return `Assign (${stringAttr(node, 'target') ?? '?'})`
    case 'BinaryExpr':
    case 'UnaryExpr':
      return stringAttr(node, 'op') ?? node.kind
    case 'Identifier':
      return stringAttr(node, 'name') ?? node.kind
    case 'CallExpr':
      return `Call ${stringAttr(node, 'callee') ?? '?'}`
    case 'IntLiteral':
    case 'FloatLiteral':
    case 'StringLiteral': {
      // The lexeme is the literal as the source wrote it, so `1.50` stays `1.50` instead of
      // collapsing to `1.5` the way the parsed value would.
      const lexeme = stringAttr(node, 'lexeme')
      if (lexeme !== null) {
        return lexeme
      }
      const value = node.attrs.value
      return value === null || value === undefined ? node.kind : String(value)
    }
    default:
      return node.kind
  }
}

/**
 * A node's children as they are drawn, with `Block` spliced away.
 *
 * A `Block` is a statement list and carries no information of its own, so a row for it is a row
 * the reader clicks through to reach what they already came for. The splice is recursive because
 * the parser synthesises blocks — a loop body that is a bare declaration, or a `for` init with
 * two declarators — and a synthesised block can sit inside a written one.
 */
function visibleChildren(node: AstNode): AstNode[] {
  const out: AstNode[] = []
  for (const child of node.children ?? []) {
    if (child.node.kind === 'Block') {
      out.push(...visibleChildren(child.node))
    } else {
      out.push(child.node)
    }
  }
  return out
}

/** Every node the tree draws, in the order it draws them. The set a selection can point at. */
function drawnNodes(root: AstNode): AstNode[] {
  const out: AstNode[] = []
  const walk = (node: AstNode): void => {
    out.push(node)
    for (const child of visibleChildren(node)) {
      walk(child)
    }
  }
  walk(root)
  return out
}

/**
 * The branch a child row draws back to its parent's guide line.
 *
 * `before` is the horizontal stub. It starts one indent step to the left of the row, which is
 * exactly where the parent list's `border-l` is, so the two meet with no seam to align.
 *
 * `last:after` is the trim. A `border-l` cannot know that nothing follows the final child, so it
 * would otherwise keep drawing down past the end of the subtree into empty space; this paints a
 * one-pixel vertical sliver in the panel's own background over that stretch, beginning where the
 * last stub begins. The background is the `surface-sunken` token rather than a hex literal
 * precisely so it keeps matching the panel if the palette ever moves.
 *
 * Both offsets are 3.5 steps down because that is the vertical centre of a row's label.
 */
const BRANCH_ROW =
  "relative py-1 before:content-[''] before:absolute before:-left-5 before:top-3.5 before:w-4 before:h-px before:bg-slate-700/60 last:after:content-[''] last:after:absolute last:after:-left-5 last:after:top-3.5 last:after:bottom-0 last:after:w-px last:after:bg-surface-sunken"

/** One row, and beneath it the rows of its children. */
function AstRow({
  node,
  selectedId,
  onSelect,
  /** False for the root, which has no parent guide to branch back to. */
  branch,
}: {
  node: AstNode
  selectedId: string | null
  onSelect: (node: AstNode) => void
  branch: boolean
}): JSX.Element {
  const children = visibleChildren(node)
  const isSelected = node.id === selectedId

  return (
    <li className={branch ? BRANCH_ROW : 'relative py-1'}>
      <button
        type="button"
        onClick={() => onSelect(node)}
        title={`${node.kind} · ${formatSpan(node.span)}`}
        className={`flex w-full items-center gap-2 py-0.5 text-left font-mono text-xs hover:bg-surface-overlay ${
          isSelected ? 'bg-surface-overlay text-stage-ast' : 'text-slate-300'
        }`}
      >
        <span className="text-slate-200">{nodeLabel(node)}</span>
        {node.semantic_type ? (
          <span className="rounded bg-surface-overlay px-1 text-2xs text-emerald-300">
            {node.semantic_type}
          </span>
        ) : null}
        <span className="ml-auto shrink-0 text-2xs text-slate-600">
          {node.span.start_line}:{node.span.start_col}
        </span>
      </button>

      {children.length > 0 ? (
        <ul className="relative ml-2 border-l border-slate-700/60 pl-5">
          {children.map((child) => (
            <AstRow
              key={child.id}
              node={child}
              selectedId={selectedId}
              onSelect={onSelect}
              branch
            />
          ))}
        </ul>
      ) : null}
    </li>
  )
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

  const nodes = useMemo(() => (ast ? drawnNodes(ast) : []), [ast])
  const selected = useMemo(
    () => nodes.find((node) => node.id === selectedId) ?? null,
    [nodes, selectedId],
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
      subtitle="Abstract Syntax Tree (AST)"
      actions={
        <div className="flex items-center gap-2">
          <Count label={`${countAstNodes(ast)} nodes`}>{countAstNodes(ast)} nodes</Count>
          <Count label={`depth ${maxAstDepth(ast)}`}>depth {maxAstDepth(ast)}</Count>
        </div>
      }
    >
      <div className="flex h-full min-h-0">
        <div className="min-h-0 flex-1 overflow-auto">
          <ul className="px-3 py-1">
            <AstRow
              node={ast}
              selectedId={selectedId}
              onSelect={(node) => {
                setSelectedId(node.id)
                select({
                  line: node.span.start_line,
                  endLine: node.span.end_line,
                  origin: `AST node ${node.kind}`,
                })
              }}
              branch={false}
            />
          </ul>
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
