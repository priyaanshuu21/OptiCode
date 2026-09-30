import {
  assertPayload,
  isAstNode,
  isCfg,
  isCodegen,
  isDataflow,
  isIrProgram,
  type AstNode,
  type CodegenResultPayload,
  type CompileRequest,
  type CompileResponse,
  type Diagnostic,
  type FunctionCfg,
  type FunctionFacts,
  type HealthResponse,
  type IrProgram,
  type Token,
} from './types'

/** Where the backend lives. Same-origin in dev via the Vite proxy; override for a real deploy. */
const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''

export class ApiError extends Error {
  readonly status: number
  readonly diagnostics: Diagnostic[]

  constructor(message: string, status: number, diagnostics: Diagnostic[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.diagnostics = diagnostics
  }
}

async function post(path: string, body: unknown): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    // A fetch that never completes is the common case here: the backend is not running. The
    // browser's own message ("Failed to fetch") says nothing about what to do about it.
    throw new ApiError(
      `Could not reach the compiler API at ${BASE_URL || 'the current origin'}. Is the backend running?`,
      0,
    )
  }
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new ApiError(`The compiler API returned ${response.status}. ${text}`.trim(), response.status)
  }
  return response.json() as Promise<unknown>
}

/** Compile a program and return the whole response, without narrowing anything yet. */
export async function compileSource(request: CompileRequest): Promise<unknown> {
  return post('/api/compile', request)
}

/**
 * Compile and return a typed response.
 *
 * The payload members stay `unknown` until a view asks for them, because a view for a stage
 * that did not run must not be handed the wrong type. Each getter narrows with the guard for
 * the payload it wants and throws for the rest.
 */
export async function compile(
  request: CompileRequest,
): Promise<CompileResponse> {
  const body = await compileSource(request)
  if (typeof body !== 'object' || body === null) {
    throw new ApiError('The compiler API returned a non-object response.', 0)
  }
  return body as CompileResponse
}

export async function health(): Promise<HealthResponse> {
  let response: Response
  try {
    response = await fetch(`${BASE_URL}/api/health`)
  } catch {
    throw new ApiError('Could not reach the compiler API health endpoint.', 0)
  }
  if (!response.ok) {
    throw new ApiError(`Health check returned ${response.status}.`, response.status)
  }
  return (await response.json()) as HealthResponse
}

// Narrowing accessors. Each returns null when the stage did not run, which is different from
// it running and producing nothing, and a view that confuses the two shows an empty table for
// a program it never actually compiled.

export function tokensOf(response: CompileResponse): Token[] | null {
  const value = response.tokens
  return value == null ? null : assertPayload(value, 'tokens', (c): c is Token[] => Array.isArray(c))
}

export function astOf(response: CompileResponse): AstNode | null {
  const value = response.ast
  return value == null ? null : assertPayload(value, 'an AST', isAstNode)
}

export function irOf(response: CompileResponse): IrProgram | null {
  const value = response.ir
  return value == null ? null : assertPayload(value, 'a TAC program', isIrProgram)
}

export function cfgOf(response: CompileResponse): Record<string, FunctionCfg> | null {
  const value = response.cfg
  return value == null ? null : assertPayload(value, 'a CFG', isCfg)
}

export function dataflowOf(response: CompileResponse): Record<string, FunctionFacts> | null {
  const value = response.dataflow
  return value == null ? null : assertPayload(value, 'data-flow facts', isDataflow)
}

export function codegenOf(response: CompileResponse): CodegenResultPayload | null {
  const value = response.codegen
  return value == null ? null : assertPayload(value, 'a codegen result', isCodegen)
}

/** Whether a stage ran, as opposed to being skipped. */
export function stageRan(response: CompileResponse, stage: string): boolean {
  return response.stages_run.includes(stage)
}

/** Whether a stage was skipped, with the reason the pipeline records. */
export function stageSkipped(response: CompileResponse, stage: string): boolean {
  return response.stages_skipped.includes(stage)
}
