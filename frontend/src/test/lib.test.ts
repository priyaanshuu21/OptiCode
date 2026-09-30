import { describe, expect, it } from 'vitest'

import { countAstNodes, formatLines, formatSpan, maxAstDepth, spanJoin, spanToRange, walkAst } from '@/lib/span'
import { classify, formatOperand, instructionSummary } from '@/lib/ir'
import { layoutGraph, readableOrder } from '@/lib/layout'
import type { AstNode, FunctionCfg, Instruction, Span } from '@/api/types'
import { loadFixture } from './fixtures'

/**
 * The pure parts of the UI: span maths, TAC rendering, and graph layout.
 *
 * These are the parts where a mistake is invisible on the one program somebody happened to look
 * at. Column arithmetic and graph coordinates are exactly that, so they are tested against real
 * recorded output rather than invented inputs.
 */

const SPAN: Span = { start_line: 3, start_col: 5, end_line: 3, end_col: 9 }

describe('span maths', () => {
  it('formats a single-line span the way a compiler diagnostic does', () => {
    expect(formatSpan(SPAN)).toBe('3:5-3:9')
    expect(formatLines(SPAN)).toBe('L3')
  })

  it('formats a missing span as an em dash rather than "null"', () => {
    expect(formatSpan(null)).toBe('—')
  })

  it('passes an end-exclusive span straight through to a Monaco range', () => {
    // Both conventions are 1-based and end-exclusive, so the correct conversion adds nothing.
    // Adding a column here would over-highlight by one character on every single span, which
    // is the kind of bug that looks fine until someone reads a highlighted `if`.
    expect(spanToRange(SPAN)).toEqual({
      startLineNumber: 3,
      startColumn: 5,
      endLineNumber: 3,
      endColumn: 9,
    })
  })

  it('agrees with the backend that a span is end-exclusive', () => {
    // The strongest available check on the convention: re-slice real recorded tokens with it.
    for (const name of ['hello', 'loops', 'mixed_types'] as const) {
      const { response, source } = loadFixture(name)
      const lines = source.split('\n')
      for (const token of response.tokens ?? []) {
        if (token.span.start_line !== token.span.end_line) {
          continue
        }
        const line = lines[token.span.start_line - 1] ?? ''
        expect(
          line.slice(token.span.start_col - 1, token.span.end_col - 1),
          `${name}: ${JSON.stringify(token.lexeme)}`,
        ).toBe(token.lexeme)
      }
    }
  })

  it('finds the span covering a set of spans', () => {
    const joined = spanJoin([
      { start_line: 10, start_col: 1, end_line: 12, end_col: 3 },
      { start_line: 8, start_col: 6, end_line: 9, end_col: 2 },
    ])
    expect(joined).toEqual({ start_line: 8, start_col: 1, end_line: 12, end_col: 3 })
  })

  it('returns null for an empty set rather than throwing', () => {
    expect(spanJoin([])).toBeNull()
  })
})

describe('AST walking', () => {
  it('visits every node of a real tree and reports depth', () => {
    const { response } = loadFixture('hello')
    const ast = response.ast as AstNode

    const entries = Array.from(walkAst(ast))

    expect(entries.length).toBe(countAstNodes(ast))
    expect(entries[0]?.role).toBe('root')
    // Depth never exceeds the count, and a nested node is deeper than its parent.
    expect(maxAstDepth(ast)).toBeGreaterThan(2)
    expect(maxAstDepth(ast)).toBeLessThan(entries.length)
  })

  it('preserves sibling order', () => {
    const node: AstNode = {
      id: 'root',
      kind: 'Module',
      span: SPAN,
      semantic_type: null,
      attrs: {},
      children: [
        { role: 'first', node: leaf('a') },
        { role: 'second', node: leaf('b') },
      ],
    }

    expect(Array.from(walkAst(node)).map((entry) => entry.node.id)).toEqual(['root', 'a', 'b'])
  })
})

function leaf(id: string): AstNode {
  return { id, kind: 'Leaf', span: SPAN, semantic_type: null, attrs: {}, children: [] }
}

describe('TAC rendering', () => {
  const program = loadFixture('loops').response.ir

  it('renders a constant operand inline', () => {
    expect(
      formatOperand({ kind: 'const', type: 'int', value: 0 }),
    ).toBe('0')
  })

  it('renders a variable operand by name', () => {
    expect(formatOperand({ kind: 'var', type: 'int', name: 'i' })).toBe('i')
  })

  it('classifies opcodes so rows can be coloured', () => {
    const instructions = program?.functions[0]?.instructions ?? []
    const opcodes = new Set(instructions.map((instruction) => instruction.opcode))
    for (const instruction of instructions) {
      expect(classify(instruction)).toBeTypeOf('string')
    }
    // A loop's TAC must contain control flow, otherwise the CFG has nothing to draw.
    expect(opcodes.size).toBeGreaterThan(2)
  })

  it('summarizes a jump as a jump', () => {
    const instruction: Instruction = {
      id: 't1',
      opcode: 'goto',
      span: SPAN,
      ast_node_id: 'n1',
      synthetic: false,
      type: null,
      dest: null,
      operands: [],
      operand_origin_ids: [],
      target: 'BB1',
    }
    expect(instructionSummary(instruction)).toBe('goto BB1')
  })
})

describe('graph layout', () => {
  // `loops.json` has a loop-free `main` plus two looped functions. Laying out `main` would
  // exercise the ranker without ever producing a back edge, so the loop assertions below use
  // `sum_for` deliberately.
  const graphs = loadFixture('loops').response.cfg as Record<string, FunctionCfg>
  const graph = graphs.sum_for as FunctionCfg

  it('produces one node per block, keyed by block id', () => {
    const { nodes } = layoutGraph(graph)
    expect(nodes).toHaveLength(graph.blocks.length)
    expect(new Set(nodes.map((node) => node.id))).toEqual(new Set(graph.blocks.map((block) => block.id)))
  })

  it('places every node at a finite coordinate', () => {
    const { nodes } = layoutGraph(graph)
    for (const node of nodes) {
      expect(Number.isFinite(node.position.x), node.id).toBe(true)
      expect(Number.isFinite(node.position.y), node.id).toBe(true)
    }
  })

  it('ranks blocks top to bottom, so an edge points downwards', () => {
    const { nodes } = layoutGraph(graph)
    const byId = new Map(nodes.map((node) => [node.id, node]))
    for (const edge of graph.edges) {
      if (edge.is_back_edge) {
        continue
      }
      const from = byId.get(edge.src)
      const to = byId.get(edge.dst)
      if (from && to) {
        expect(to.position.y, `${edge.src} -> ${edge.dst}`).toBeGreaterThan(from.position.y)
      }
    }
  })

  it('marks a back edge so a loop is visible', () => {
    const { edges } = layoutGraph(graph)

    expect(graph.has_loops).toBe(true)
    expect(graph.back_edges.length).toBeGreaterThan(0)
    // A back edge is a cycle, which is to say a loop. It has to read as one.
    for (const back of graph.back_edges) {
      const drawn = edges.find((edge) => edge.source === back.src && edge.target === back.dst)
      expect(drawn, `${back.src} -> ${back.dst}`).toBeDefined()
      expect(drawn?.animated).toBe(true)
    }
  })

  it('ranks the loop body below its header despite the back edge', () => {
    // `sum_for` is `for (i = 1; i <= n; i++) total += i`: B3 jumps back to B2. The back edge
    // must not drag the body above the header.
    const byId = new Map(layoutGraph(graph).nodes.map((node) => [node.id, node]))
    const header = byId.get('B2')
    const body = byId.get('B3')
    expect(header).toBeDefined()
    expect(body).toBeDefined()
    expect(body!.position.y).toBeGreaterThan(header!.position.y)
  })

  it('gives each node a preview and an overflow count', () => {
    const { nodes } = layoutGraph(graph)
    const biggest = graph.blocks.reduce((max, block) =>
      block.instructions.length > max.instructions.length ? block : max,
    )
    const node = nodes.find((candidate) => candidate.id === biggest.id)
    expect(node?.data.preview.length).toBe(4)
    expect(node?.data.hidden).toBe(biggest.instructions.length - 4)
  })

  it('reports a readable order for a graph with no rpo', () => {
    const stub: FunctionCfg = { ...graph, rpo: [] }
    expect(readableOrder(stub)).toEqual(stub.blocks.map((block) => block.id))
  })
})
