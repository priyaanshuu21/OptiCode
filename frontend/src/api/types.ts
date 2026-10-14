import type { components, paths } from './schema'

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

/** A branch condition the constant-propagation solver was able to decide statically. */
export interface BranchFact {
  instruction_id: string
  operand: string
  value: Operand
  false_target: string
  true_target: string
  statically_decidable: boolean
  /** Which arm always runs: `"true"`, `"false"`, or `null` when both are possible. */
  always_taken: 'true' | 'false' | null
}

/** The def-use index, both directions. */
export interface DefUseChains {
  defs_of: Record<string, string[]>
  uses_of: Record<string, string[]>
}

/** One block update the solver performed, as the stepper replays it. */
export interface SolverStep {
  /** Monotonic count of block updates so far, 1-based. */
  iteration: number
  block: string
  /**
   * The block's entry value *after* this update.
   *
   * A sorted name list for the set-valued analyses (liveness, reaching defs) and a name→value
   * map for constant propagation, whose values are lattice elements already rendered as text.
   */
  in: string[] | Record<string, string>
  /** The block's exit value *after* this update. Same two forms as `in`. */
  out: string[] | Record<string, string>
  changed: boolean
}

/**
 * One analysis's recorded convergence.
 *
 * The stepper walks `steps` in order. The order is the order the worklist ran in, which is a
 * function of the CFG alone — so the same program produces the same steps every run, and a
 * screenshot of the stepper is a claim somebody can check.
 */
export interface SolverTrace {
  /** `forward` for the analyses that follow control, `backward` for liveness. */
  direction: 'forward' | 'backward' | string
  /** Total block updates, which may exceed `steps.length` when the trace was cut short. */
  iterations: number
  steps: SolverStep[]
  /** True when `steps` stops short of `iterations`; the count above is still the true one. */
  trace_truncated: boolean
}

/** The name an analysis is known by in the payload and in the UI. */
export type AnalysisName = 'liveness' | 'reaching' | 'constant_propagation'

/**
 * One variable's abstract value on a function's exit edge.
 *
 * `top` is "nothing is known", `const` is "proved to this value", and `nac` is "not a constant
 * after all" — which is the one that is easy to misread as `top`, and means the analysis learned
 * something negative rather than nothing at all.
 */
export interface AbstractValue {
  kind: 'top' | 'const' | 'nac'
  /** Present only for `const`. */
  value?: number
  /** The MiniC type, so `5` and `5.0` never meet into a value true of neither. */
  type?: string
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
  /**
   * Block id → `USE[B]`: names read in the block before anything there writes them.
   *
   * The local half of the liveness transfer. `def` below is spelled without the trailing
   * keyword-free alias because the backend serializes the set as `"def"`, which is a reserved
   * word in JavaScript only as a statement — the property access `facts.def` is fine.
   */
  use?: Record<string, string[]>
  /** Block id → `DEF[B]`: every name the block writes, read first or not. */
  def?: Record<string, string[]>
  reaching_in?: Record<string, string[]>
  reaching_out?: Record<string, string[]>
  /** Block updates each analysis needed. Parallel to the keys of `traces`. */
  iterations?: Record<string, number>
  /** Per-analysis convergence, for the iteration stepper. */
  traces?: Partial<Record<AnalysisName, SolverTrace>>
  branches?: BranchFact[]
  chains?: DefUseChains
  exit_values?: Record<string, AbstractValue>
  uninitialized_reads?: { name: string; span: Span; instruction_id: string }[]
}

/** A piece of supporting evidence for a finding, with the range it points at. */
export interface FindingEvidence {
  span: Span
  note: string
}

/**
 * What a finding is about. The kind is the stable API key; the message is prose and will change.
 *
 * The trailing `(string & {})` keeps the editor's autocomplete for the known kinds while still
 * accepting a kind this build has never heard of — a new backend kind should render as a finding
 * with a raw label, not crash the panel.
 */
export type FindingKind =
  | 'unreachable_code'
  | 'conditionally_unreachable'
  | 'dead_assignment'
  | 'unused_call_result'
  | 'unused_read_result'
  | 'use_before_initialization'
  | 'missing_return'
  | 'constant_condition'
  | 'constant_division_by_zero'
  | (string & {})

/** What an optimizer would do with the flagged code, if one ran. */
export type FindingAction = 'will_be_removed' | 'kept' | 'informational'

/**
 * One dead-code finding.
 *
 * Every field is here because the panel shows all of them: a dead assignment is only credible
 * if you can see the write, the read that never happens, and the live set in between.
 */
export interface Finding {
  id: string
  kind: FindingKind
  severity: Severity
  action?: FindingAction
  function: string
  block: string
  instruction_ids: string[]
  ast_node_id: string | null
  /**
   * Set when a coarser stage already said this in a diagnostic, so the panel can say so rather
   * than presenting the same fact twice as if it were new.
   */
  duplicate_of?: string
  span: Span | null
  message: string
  explanation: string
  evidence: FindingEvidence[]
}

/** The analysis stage's report. */
export interface AnalysisReport {
  findings: Finding[]
  finding_count: number
  /** How many of each kind, for the summary line. */
  summary: Record<string, number>
  /** `function:block` ids that no path from the entry reaches. */
  unreachable_blocks: string[]
  dead_instruction_ids: string[]
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

/** One pass's outcome: how often it ran, how often it fired, and what it took out. */
export interface OptimizationPass {
  name: string
  description: string
  iterations: number
  applications: number
  removed: number
  changed: boolean
}

/** One rewrite, with the text before and after so the diff can be shown inline. */
export interface OptimizationEvent {
  pass: string
  function: string
  kind: string
  detail: string
  before: string
  after: string
  instruction_ids: string[]
  span: Span | null
}

/**
 * Something the optimizer did not do, with the reason.
 *
 * The report also lists what it *did*, and a list of wins alone invites the wrong conclusion:
 * "12 instructions removed" reads as "everything removable went", which is only true if nothing
 * was skipped. So the gaps come with the wins, and the panel shows them.
 */
export interface SkippedGap {
  kind:
    | 'pass_found_nothing'
    | 'unverified_entry'
    | 'inconclusive_case'
    | 'truncated_events'
    | string
  /** What the gap is about: a pass name, a function name, or `verification`. */
  target: string
  reason: string
}

/** One run of the interpreter, as the equivalence oracle recorded it. */
export interface VerificationCase {
  entry: string
  stdin: string
  matched: boolean
  inconclusive: boolean
  original: string
  optimized: string
  /** A one-line description of the disagreement. Omitted when the runs matched. */
  difference?: string
}

/**
 * What the oracle concluded, and how much it was able to conclude.
 *
 * `skipped` and `verified: false` mean different things, and conflating them is the mistake
 * worth avoiding: *skipped* is "the oracle never ran", *not verified* is "it ran and something
 * disagreed". Only the second is a failure.
 */
export interface VerificationReport {
  verified: boolean
  /** True when the oracle did not run at all, which is not the same as passing. */
  skipped: boolean
  cases_checked: number
  cases_inconclusive: number
  cases_truncated: boolean
  /** The first disagreement found. Omitted when every case matched. */
  first_difference?: VerificationCase
  cases: VerificationCase[]
  unverified_entries: string[]
}

/** Instruction counts before and after. */
export interface OptimizationStats {
  before: { functions: number; instructions: number; temps: number; labels: number }
  after: { functions: number; instructions: number; temps: number; labels: number }
}

/** The whole optimization report, including the TAC it produced. */
export interface OptimizationPayload {
  changed: boolean
  verified: boolean
  rounds: number
  removed_instructions: number
  passes: OptimizationPass[]
  events: OptimizationEvent[]
  events_truncated: boolean
  skipped: SkippedGap[]
  stats: OptimizationStats
  verification: VerificationReport | null
  /** The optimized TAC. The unoptimized one is the response's top-level `ir`. */
  ir: IrProgram
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
  optimization?: OptimizationPayload | null
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

/** One entry in the examples menu. No source: the menu must not download every program. */
export type ExampleSummary = components['schemas']['ExampleSummaryModel']

/** One example's metadata plus the program text, as the detail endpoint returns it. */
export type ExampleDetail = components['schemas']['ExampleDetailModel']

/** The whole examples library. */
export interface ExampleListResponse {
  examples: ExampleSummary[]
  count: number
  /** How many of them are *meant* to fail, which is what makes the count honest. */
  invalid_count: number
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

export const isAnalysis = (value: unknown): value is AnalysisReport =>
  isObject(value) && Array.isArray(value.findings)

export const isOptimization = (value: unknown): value is OptimizationPayload =>
  isObject(value) &&
  typeof value.changed === 'boolean' &&
  Array.isArray(value.passes) &&
  Array.isArray(value.events) &&
  isIrProgram(value.ir)

export const isExampleList = (value: unknown): value is ExampleListResponse =>
  isObject(value) &&
  Array.isArray(value.examples) &&
  value.examples.every((item) => isObject(item) && typeof item.name === 'string')

export const isExampleDetail = (value: unknown): value is ExampleDetail =>
  isObject(value) && typeof value.name === 'string' && typeof value.source === 'string'
