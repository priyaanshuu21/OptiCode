import { describe, expect, it } from 'vitest'

import { countAstNodes, formatLines, formatSpan, maxAstDepth, spanJoin, spanToRange, walkAst } from '@/lib/span'
import { classify, formatOperand, instructionSummary } from '@/lib/ir'
import { ENTRY_ID, EXIT_ID, layoutGraph, readableOrder } from '@/lib/layout'
import {
  astChildren,
  astDepth,
  astLabel,
  astNodes,
  attributeRows,
  classifyNode,
} from '@/lib/ast'
import { layoutAst } from '@/components/AstGraph'
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

/** A node with a generated id, for the tests that care about structure rather than identity. */
let sequence = 0
function astNode(
  kind: string,
  attrs: Record<string, unknown> = {},
  children: AstNode[] = [],
): AstNode {
  sequence += 1
  return {
    id: `s${sequence}`,
    kind,
    span: SPAN,
    semantic_type: null,
    attrs,
    children: children.map((node) => ({ role: 'statement', node })),
  }
}

describe('AST labels', () => {
  it('names a node after what it declares rather than after its kind or id', () => {
    expect(astLabel(astNode('FunctionDecl', { name: 'main' }))).toBe('Function main')
    expect(astLabel(astNode('VarDecl', { name: 'total' }))).toBe('Var total')
    expect(astLabel(astNode('Assign', { target: 'total' }))).toBe('Assign (total)')
  })

  it('shows a bare operator, an identifier and a literal as themselves', () => {
    // These are the leaves. Their whole content is the word, so anything wrapped around it is
    // padding — `BinaryExpr (+)` says the same thing as `+` in twice the space.
    expect(astLabel(astNode('BinaryExpr', { op: '<' }))).toBe('<')
    expect(astLabel(astNode('UnaryExpr', { op: '!' }))).toBe('!')
    expect(astLabel(astNode('Identifier', { name: 'total' }))).toBe('total')
    expect(astLabel(astNode('IntLiteral', { value: 0, lexeme: '0' }))).toBe('0')
  })

  it('keeps a literal the way the source spelled it', () => {
    // The parsed value of `1.50` is 1.5. Printing the value would quietly rewrite the program
    // being displayed, which is the sort of small lie that makes a compiler view untrustworthy.
    expect(astLabel(astNode('FloatLiteral', { value: 1.5, lexeme: '1.50' }))).toBe('1.50')
  })

  it('renders a kind it has never seen as that kind, not as nothing', () => {
    expect(astLabel(astNode('GotoStmt', { target: 'end' }))).toBe('GotoStmt')
  })

  it('shows no id and no role anywhere in the tree', () => {
    const ast = loadFixture('loops').response.ast as AstNode
    const labels = astNodes(ast).map(astLabel)
    for (const label of labels) {
      expect(label, 'a node id leaked into a label').not.toMatch(/^n\d+$/)
      for (const role of ['condition', 'statement', 'init', 'update', 'body', 'value', 'left', 'right']) {
        expect(label.startsWith(role), `${role} leaked into ${label}`).toBe(false)
      }
    }
  })
})

describe('AST shape as drawn', () => {
  it('splices Block nodes out, including a Block nested in a Block', () => {
    // The parser synthesises blocks: a loop body that is a bare declaration becomes one, and a
    // `for` init with two declarators becomes one inside the `For`. Splicing only the outer would
    // leave a second box that says nothing.
    const body = astNode('Block', {}, [astNode('VarDecl', { name: 'i' })])
    const nested = astNode('Block', {}, [astNode('VarDecl', { name: 'j' }), body])
    const fn = astNode('FunctionDecl', { name: 'main' }, [nested, astNode('Return')])

    expect(astChildren(fn).map(astLabel)).toEqual(['Var j', 'Var i', 'Return'])
    expect(astChildren(fn).some((node) => node.kind === 'Block')).toBe(false)
  })

  it('counts and measures the tree it draws, not the tree it was sent', () => {
    const ast = loadFixture('loops').response.ast as AstNode
    const shown = astNodes(ast)

    // Fewer than the payload's node count, because the blocks are gone; deeper than one, because
    // the program has loops.
    expect(shown.length).toBeLessThan(countAstNodes(ast))
    expect(astDepth(ast)).toBeGreaterThan(2)
    expect(astDepth(ast)).toBeLessThanOrEqual(maxAstDepth(ast))
    expect(shown[0]?.kind).toBe('Program')
  })

  it('drops the lexeme from the inspector, which is the label again', () => {
    const literal = astNode('IntLiteral', { value: 0, lexeme: '0' })
    expect(attributeRows(literal)).toEqual([{ key: 'value', value: '0' }])
  })

  it('keeps every other attribute, including an array one', () => {
    const scanf = astNode('Scanf', { format: '%d %d', arguments: ['a', 'b'] })
    expect(attributeRows(scanf)).toEqual([
      { key: 'format', value: '%d %d' },
      { key: 'arguments', value: '["a","b"]' },
    ])
  })

  it('classifies every node so the graph can colour it', () => {
    const ast = loadFixture('mixed_types').response.ast as AstNode
    for (const node of astNodes(ast)) {
      expect(classifyNode(node), node.kind).toBeTypeOf('string')
    }
  })
})

describe('AST tree layout', () => {
  const ast = loadFixture('loops').response.ast as AstNode

  it('draws one node per shown node and one edge per parent-child link', () => {
    const { nodes, edges } = layoutAst(ast, null)
    const shown = astNodes(ast)

    expect(nodes).toHaveLength(shown.length)
    expect(new Set(nodes.map((node) => node.id))).toEqual(new Set(shown.map((node) => node.id)))
    // A tree has exactly one edge fewer than it has nodes. Two edges between the same pair would
    // mean the dagre graph and the drawn edges had drifted apart.
    expect(edges).toHaveLength(shown.length - 1)
  })

  it('puts the root at the top and every child below its parent', () => {
    const { nodes } = layoutAst(ast, null)
    const byId = new Map(nodes.map((node) => [node.id, node]))

    const root = byId.get(ast.id)
    expect(root).toBeDefined()
    for (const node of nodes) {
      expect(Number.isFinite(node.position.x), node.id).toBe(true)
      expect(Number.isFinite(node.position.y), node.id).toBe(true)
    }
    for (const child of astNodes(ast)) {
      for (const grandchild of astChildren(child)) {
        const from = byId.get(child.id)
        const to = byId.get(grandchild.id)
        if (from && to) {
          expect(to.position.y, `${child.kind} -> ${grandchild.kind}`).toBeGreaterThan(from.position.y)
        }
      }
    }
    // `Program` owns everything, so nothing may sit above it.
    for (const node of nodes) {
      expect(node.position.y).toBeGreaterThanOrEqual(root!.position.y)
    }
  })

  it('marks exactly the selected node', () => {
    const target = astNodes(ast)[3]
    expect(target).toBeDefined()
    const { nodes } = layoutAst(ast, target!.id)
    expect(nodes.filter((node) => node.selected).map((node) => node.id)).toEqual([target!.id])
  })
})

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

  it('produces one node per block plus the entry and exit terminals', () => {
    const { nodes } = layoutGraph(graph)
    const ids = nodes.map((node) => node.id)
    // The two terminals are drawn and are not blocks, so the block nodes are the rest.
    expect(ids).toContain(ENTRY_ID)
    expect(ids).toContain(EXIT_ID)
    expect(ids.filter((id) => id !== ENTRY_ID && id !== EXIT_ID)).toHaveLength(graph.blocks.length)
    expect(new Set(ids)).toEqual(new Set([ENTRY_ID, EXIT_ID, ...graph.blocks.map((block) => block.id)]))
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

  it('shows every instruction of a block, not a truncated preview', () => {
    const { nodes } = layoutGraph(graph)
    const biggest = graph.blocks.reduce((max, block) =>
      block.instructions.length > max.instructions.length ? block : max,
    )
    const node = nodes.find((candidate) => candidate.id === biggest.id)
    // The node is the block: a preview that stopped at four would hide the two instructions a
    // reader is most likely looking for.
    expect(node?.data.preview).toHaveLength(biggest.instructions.length)
    expect(node?.data.hidden).toBe(0)
  })

  it('connects the entry terminal to the entry block and keeps return edges to the exit', () => {
    const { edges } = layoutGraph(graph)
    const entryEdge = edges.find((edge) => edge.source === ENTRY_ID)
    expect(entryEdge?.target).toBe(graph.entry)

    // A return edge used to be dropped because its target was the synthetic exit; now it is a
    // drawn edge into the EXIT node, so "returns" is visible rather than implied.
    for (const edge of graph.edges.filter((candidate) => candidate.kind === 'return')) {
      const drawn = edges.find((candidate) => candidate.source === edge.src && candidate.target === EXIT_ID)
      expect(drawn, `${edge.src} -> EXIT`).toBeDefined()
    }
  })

  it('reports a readable order for a graph with no rpo', () => {
    const stub: FunctionCfg = { ...graph, rpo: [] }
    expect(readableOrder(stub)).toEqual(stub.blocks.map((block) => block.id))
  })
})
