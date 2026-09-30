import type { Span } from '@/api/types'

/**
 * Source positions, and the conversions every view needs to turn one into a highlight.
 *
 * Spans are 1-based and **end-exclusive**, which is the backend's convention
 * (`docs/ARCHITECTURE.md` §4.1): the token `int` at columns 1-4 covers characters 1, 2, 3.
 *
 * That happens to match Monaco's own convention, which is also 1-based and end-exclusive, so
 * the conversion is the identity — and that is worth stating, because the two conventions
 * usually disagree. If the backend ever switches to an inclusive end, this is the one function
 * that has to change, and the test that pins it is the one that should fail first.
 */

/** The least span covering both inputs. Used to merge child nodes into a parent selection. */
export function spanJoin(spans: Span[]): Span | null {
  if (spans.length === 0) {
    return null
  }
  return spans.reduce((outer, inner) => ({
    start_line: Math.min(outer.start_line, inner.start_line),
    start_col: Math.min(outer.start_col, inner.start_col),
    end_line: Math.max(outer.end_line, inner.end_line),
    end_col: Math.max(outer.end_col, inner.end_col),
  }))
}

/** `1:5-1:9`, the form a compiler diagnostic uses. */
export function formatSpan(span: Span | null | undefined): string {
  if (!span) {
    return '—'
  }
  return `${span.start_line}:${span.start_col}-${span.end_line}:${span.end_col}`
}

/** `L12` for a single-line span, `L12-18` for a range. */
export function formatLines(span: Span): string {
  return span.start_line === span.end_line
    ? `L${span.start_line}`
    : `L${span.start_line}-${span.end_line}`
}

/** A Monaco range for a span. The conventions agree, so this is a straight remap. */
export function spanToRange(span: Span): { startLineNumber: number; startColumn: number; endLineNumber: number; endColumn: number } {
  return {
    startLineNumber: span.start_line,
    startColumn: span.start_col,
    endLineNumber: span.end_line,
    endColumn: span.end_col,
  }
}

/** Depth-first walk of an AST, yielding each node with its depth and role in its parent. */
export function* walkAst<T extends { children: { role: string; node: T }[] }>(
  root: T,
  depth = 0,
): Generator<{ node: T; depth: number; role: string }> {
  const stack: { node: T; depth: number; role: string }[] = [{ node: root, depth, role: 'root' }]
  while (stack.length > 0) {
    const entry = stack.pop()
    if (!entry) {
      break
    }
    yield entry
    const children = entry.node.children ?? []
    for (let index = children.length - 1; index >= 0; index -= 1) {
      const child = children[index]
      if (child) {
        stack.push({ node: child.node as T, depth: entry.depth + 1, role: child.role })
      }
    }
  }
}

/** Total node count, for a summary line. */
export function countAstNodes<T extends { children: { role: string; node: T }[] }>(root: T): number {
  let total = 0
  for (const _entry of walkAst(root)) {
    total += 1
  }
  return total
}

/** The deepest nesting level, which is the tree's width on screen. */
export function maxAstDepth<T extends { children: { role: string; node: T }[] }>(root: T): number {
  let deepest = 0
  for (const entry of walkAst(root)) {
    deepest = Math.max(deepest, entry.depth)
  }
  return deepest
}
