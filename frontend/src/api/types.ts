import type { paths } from './schema'

/**
 * The backend's response shapes, narrowed to what this app actually reads.
 *
 * Most of these payloads are typed as `unknown` by the OpenAPI generator, and rightly so: the
 * backend declares them `Any` because they are whatever stage happened to produce. That is not
 * an excuse for the frontend to guess, though — a view that reads `body.cfg.main.blocks[0].id`
 * out of an `unknown` is a view that breaks silently when the shape moves.
 *
 * So the structural types live here, written by hand next to the code that consumes them, and
 * `assertPayload` below is the single place where an `unknown` becomes one of them. Everything
 * downstream is then typechecked against the real shape.
 */

/** A source position, as the backend reports it. */
export interface Span {
  start_line: number
  start_col: number
  end_line: number
  end_col: number
}

/** Severity as the backend's enum spells it. */
export type Severity = 'error' | 'warning' | 'info' | 'hint'

/** One compiler message. `stage` names the pipeline stage that raised it. */
export interface Diagnostic {
  severity: Severity
  code: string
  message: string
  span: Span | null
  stage: string
  related: { message: string; span: Span }[] | null
}

/** One lexical token. */
export interface Token {
  type: string
  category: string
  lexeme: string
  value: string | number | boolean | null
  span: Span
}

/** An AST node, serialized. `attrs` holds whatever fields that node kind has. */
export interface AstNode {
  id: string
  kind: string
  span: Span
  semantic_type: string | null
  attrs: Record<string, unknown>
  children: { role: string; node: AstNode }[]
}

/** A TAC operand: a constant, a variable, or a temporary. */
export interface Operand {
  kind: 'const' | 'var' | 'temp'
  type: string | null
  name?: string
  value?: number | string | null
}

/** One three-address instruction. */
export interface Instruction {
  id: string
  opcode: string
  span: Span
  ast_node_id: string
  synthetic: boolean
  type: string | null
  dest: Operand | null
  operands: Operand[]
  operand_origin_ids: string[]
  callee?: string
  format?: string
  target?: string | null
  uses?: { kind: string; name: string }[]
  defs?: { kind: string; name: string }[]
  has_side_effects?: boolean
  may_trap?: boolean
}

/** One lowered function. */
export interface IrFunction {
  name: string
  return_type: string
  span: Span
  node_id: string
  params: { kind: string; type: string; name: string }[]
  instructions: Instruction[]
  instruction_count: number
  declared_locals: string[]
  labels: string[]
  temp_count: number
}

/** The whole TAC program, including the backend's own text rendering of it. */
export interface IrProgram {
  functions: IrFunction[]
  function_count: number
  instruction_count: number
  types: string[]
  /** The TAC as formatted text. Used by the "TAC" view's text mode. */
  text: string
}

/** A basic block within one function's CFG. */
export interface Block {
  id: string
  labels: string[]
  leader_reason: string
  instructions: Instruction[]
  instruction_ids: string[]
  successors: string[]
  predecessors: string[]
  reachable: boolean
  span: Span
}

/**
 * A control-flow edge.
 *
 * `kind` is what the edge *is*, not where it points: a `fallthrough` is the implicit edge
 * between adjacent blocks, `true`/`false` are the two arms of a branch, `jump` is an explicit
 * goto, and `return` is the edge to the synthetic exit block. `is_back_edge` is the one to
 * reach for when deciding whether something is a loop — the CFG builder already computed it, and
 * re-deriving it by comparing ranks is how a graph ends up disagreeing with itself.
 */
export interface Edge {
  src: string
  dst: string
  kind: 'fallthrough' | 'true' | 'false' | 'jump' | 'return' | string
  is_back_edge: boolean
}

/** One function's control-flow graph. */
export interface FunctionCfg {
  function: string
  return_type: string
  entry: string
  exit: string
  blocks: Block[]
  edges: Edge[]
  /** The edges that close a cycle. One per loop. */
  back_edges: Edge[]
  /** Block ids in reverse post-order: the order a person reads the function in. */
  rpo: string[]
  block_count: number
  edge_count: number
  has_loops: boolean
}

/** Liveness, reaching definitions, and constant propagation for one instruction. */
export interface InstructionFacts {
  liveness?: {
    live_in: string[]
    live_out: string[]
    dest_live: boolean
  }
  reaching?: {
    reaching: string[]
    defines: string | null
  }
  constants?: {
    result: Operand | null
    operands: Operand[]
  }
}

/**
 * All data-flow facts for one function.
 *
 * The per-instruction maps are what the optimizer consumes. The function-level sets are the
 * same facts indexed by block and by loop, which is what makes a question like "what does this
 * loop carry across its back edge" answerable without walking every instruction again.
 */
export interface FunctionFacts {
  name: string
  instructions: Record<string, InstructionFacts>
  live_in?: Record<string, string[]>
  live_out?: Record<string, string[]>
  reaching_in?: Record<string, string[]>
  reaching_out?: Record<string, string[]>
  /** Back-edge ids with the values that survive the iteration. */
  iterations?: Record<string, { block: string; tests: string[] }>
  branches?: Record<string, unknown>
  chains?: Record<string, unknown>
  exit_values?: Record<string, string | null>
  uninitialized_reads?: { name: string; span: Span; instruction_id: string }[]
}

/** One dead-code finding, as the analysis stage reports it. */
export interface Finding {
  kind?: string
  message: string
  severity?: Severity
  span?: Span | null
  function?: string
  node_id?: string
  [key: string]: unknown
}

/** The analysis stage's report. */
export interface AnalysisReport {
  findings: Finding[]
  summary?: Record<string, unknown>
  functions?: Record<string, unknown>
}

/** The optimizer's per-instruction result. `value` is the post-optimization constant. */
export interface CodegenChange {
  kind: string
  node_id: string
  span: Span
  function: string
  reason: string
  before: string
  after: string
  passes: string[]
  instruction_ids: string[]
}

/** The optimizer's change summary inside the codegen payload. */
export interface CodegenResultPayload {
  source: string
  annotated_source: string
  changes: { changes: CodegenChange[] }
  applied: boolean
  verified: boolean
  reason: string
  self_check: {
    ok: boolean
    reason: string
    verification?: Record<string, unknown>
  } | null
}

/** Everything a compile request can return. */
export interface CompileResponse {
  success: boolean
  mode: string
  diagnostics: Diagnostic[]
  tokens?: Token[] | null
  ast?: AstNode | null
  semantic?: Record<string, unknown> | null
  ir?: IrProgram | null
  cfg?: Record<string, FunctionCfg> | null
  dataflow?: Record<string, FunctionFacts> | null
  analysis?: AnalysisReport | null
  optimization?: Record<string, unknown> | null
  codegen?: CodegenResultPayload | null
  timings: Record<string, number>
  stages_run: string[]
  stages_skipped: string[]
}

/** The request body. */
export type CompileRequest = paths['/api/compile']['post']['requestBody']['content']['application/json']

/** The health endpoint's payload. */
export interface HealthResponse {
  status: string
  version: string
  stages: string[]
}

/** Thrown when the backend answers with something the app cannot use. */
export class PayloadError extends Error {
  readonly payload: unknown

  constructor(message: string, payload: unknown) {
    super(message)
    this.name = 'PayloadError'
    this.payload = payload
  }
}

/**
 * Narrow an `unknown` payload to the shape a view expects, or throw.
 *
 * The checks are shallow on purpose. Verifying every field of a several-hundred-instruction
 * payload on every keystroke would cost more than it is worth, and the backend is the only
 * producer of these documents — it is not untrusted input. What matters is that a view which
 * is handed the wrong *shape* says so loudly instead of rendering an empty table, because an
 * empty table reads as "the compiler found nothing", which is a claim.
 */
export function assertPayload<T>(
  value: unknown,
  expect: string,
  guard: (candidate: unknown) => candidate is T,
): T {
  if (!guard(value)) {
    throw new PayloadError(`Expected ${expect} in the compile response`, value)
  }
  return value
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export const isToken = (value: unknown): value is Token =>
  isObject(value) && typeof value.type === 'string' && typeof value.lexeme === 'string'

export const isAstNode = (value: unknown): value is AstNode =>
  isObject(value) && typeof value.kind === 'string' && Array.isArray(value.children)

export const isIrProgram = (value: unknown): value is IrProgram =>
  isObject(value) && Array.isArray(value.functions)

export const isCfg = (value: unknown): value is Record<string, FunctionCfg> =>
  isObject(value) && Object.values(value).every((graph) => isObject(graph) && Array.isArray(graph.blocks))

export const isDataflow = (value: unknown): value is Record<string, FunctionFacts> =>
  isObject(value) && Object.values(value).every((facts) => isObject(facts))

export const isCodegen = (value: unknown): value is CodegenResultPayload =>
  isObject(value) && typeof value.source === 'string' && typeof value.applied === 'boolean'
