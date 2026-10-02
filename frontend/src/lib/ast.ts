import type { AstNode } from '@/api/types'

/**
 * Reading an AST as a tree a person wants to look at.
 *
 * The payload is complete and unusable on its own: every node carries an id (`n7`), every child
 * carries the role it plays in its parent (`condition`, `statement`, `init`), and a statement
 * list is wrapped in a `Block` that says nothing. Print all of that and the reader has to skip
 * past three pieces of bookkeeping per row to get to the one fact the row exists to convey.
 *
 * So this module answers two questions and nothing else: *what is this node called*, and *what
 * are its children*. Both are pure functions of a node, which is what makes them testable and
 * lets the graph and the inspector agree without either of them re-deriving the rules.
 */

/**
 * A string attribute of a node, or null when it is absent or is not a string.
 *
 * Written defensively because `attrs` is whatever the backend put there. A node kind that grows
 * an attribute, or loses one, should change these labels at worst — never crash a view.
 */
function stringAttr(node: AstNode, key: string): string | null {
  const value = node.attrs[key]
  return typeof value === 'string' ? value : null
}

/**
 * The node's name, short enough to sit inside a box or a chip.
 *
 * `Function main`, `Var total`, `Assign (total)`, and for the leaves the thing itself: the
 * operator, the identifier, the literal. A kind with no interesting attribute — `If`, `Return`,
 * `While` — is just its own name, because there is nothing to add to it.
 *
 * A kind this build has never heard of falls through to its raw name. That is the honest
 * rendering: a node reading `VarDecl` is a lookup away from being understood, whereas a node
 * rendering nothing looks like a bug.
 */
export function astLabel(node: AstNode): string {
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
 * A node's children as they are shown, with `Block` spliced away.
 *
 * A `Block` is a statement list and carries no information of its own, so a row for it is a row
 * the reader clicks through to reach what they already came for. The splice is recursive because
 * the parser synthesises blocks — a loop body that is a bare declaration, or a `for` init with
 * two declarators — and a synthesised block can sit inside a written one.
 *
 * Note this is a *view* decision, not a claim about the tree. The inspector still reports the
 * real child count, so nothing here can quietly under-report what the parser built.
 */
export function astChildren(node: AstNode): AstNode[] {
  const out: AstNode[] = []
  for (const child of node.children ?? []) {
    if (child.node.kind === 'Block') {
      out.push(...astChildren(child.node))
    } else {
      out.push(child.node)
    }
  }
  return out
}

/** Every node the tree shows, in the order it shows them. The set a selection can point at. */
export function astNodes(root: AstNode): AstNode[] {
  const out: AstNode[] = []
  const walk = (node: AstNode): void => {
    out.push(node)
    for (const child of astChildren(node)) {
      walk(child)
    }
  }
  walk(root)
  return out
}

/** How deep the shown tree goes. Measured on the shown tree, so it matches what is on screen. */
export function astDepth(root: AstNode): number {
  let deepest = 0
  const walk = (node: AstNode, depth: number): void => {
    if (depth > deepest) {
      deepest = depth
    }
    for (const child of astChildren(node)) {
      walk(child, depth + 1)
    }
  }
  walk(root, 0)
  return deepest
}

/** What a node is for, which is what decides how it is coloured. */
export type AstClass = 'program' | 'declaration' | 'statement' | 'expression' | 'literal' | 'io'

/**
 * Which of those a node is.
 *
 * Drives colour and nothing else, so an unrecognised kind is not an error: it is a statement,
 * which is the least surprising place for something new to land.
 */
export function classifyNode(node: AstNode): AstClass {
  switch (node.kind) {
    case 'Program':
      return 'program'
    case 'FunctionDecl':
    case 'VarDecl':
    case 'Param':
      return 'declaration'
    case 'Printf':
    case 'Scanf':
      return 'io'
    case 'IntLiteral':
    case 'FloatLiteral':
    case 'StringLiteral':
      return 'literal'
    case 'BinaryExpr':
    case 'UnaryExpr':
    case 'CallExpr':
    case 'Identifier':
      return 'expression'
    default:
      return 'statement'
  }
}

/** Border colour per class. Kept beside `classifyNode` so the two cannot disagree. */
export const CLASS_BORDERS: Record<AstClass, string> = {
  program: 'border-stage-ast/60',
  declaration: 'border-amber-400/40',
  statement: 'border-slate-700/80',
  expression: 'border-sky-400/40',
  literal: 'border-emerald-400/40',
  io: 'border-fuchsia-400/40',
}

/** Attributes worth showing in the inspector. */
const HIDDEN_ATTRS = new Set([
  // The lexeme is the literal's source text, and the node's label is already that literal. In an
  // inspector sitting under the label it is the same word twice.
  'lexeme',
])

/** A node's attributes as `key: value`, minus the ones that only repeat the label. */
export function attributeRows(node: AstNode): { key: string; value: string }[] {
  const rows: { key: string; value: string }[] = []
  for (const [key, value] of Object.entries(node.attrs)) {
    if (HIDDEN_ATTRS.has(key) || value === null || value === undefined) {
      continue
    }
    rows.push({ key, value: typeof value === 'object' ? JSON.stringify(value) : String(value) })
  }
  return rows
}
