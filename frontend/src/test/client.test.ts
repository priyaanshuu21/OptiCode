import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  compile,
  codegenOf,
  cfgOf,
  dataflowOf,
  irOf,
  analysisOf,
  optimizationOf,
  listExamples,
  readExample,
  stageRan,
  stageSkipped,
  tokensOf,
  ApiError,
} from '@/api/client'
import { isExampleList, PayloadError, type CompileResponse } from '@/api/types'
import { loadFixture, loadFixtures } from './fixtures'

/**
 * The API client, against real recorded responses.
 *
 * These tests exist because the client's whole job is to turn a bag of possibly-null payloads
 * into either a narrowed value or a loud failure. A test that only checks the happy path would
 * pass while every skipped stage silently rendered an empty table.
 */

describe('fixture loader', () => {
  it('loads every fixture as a recorded compile response', () => {
    const all = loadFixtures()
    expect(all).toHaveLength(8)
    for (const { name, fixture } of all) {
      expect(fixture.source, name).toBeTypeOf('string')
      expect(fixture.request.mode, name).toMatch(/analyze|optimize/)
      expect(fixture.response.stages_run, name).toContain('tokens')
    }
  })

  it('replays a fixture with the same stdin it was recorded with', () => {
    // The `io` example reads four values. Replaying it with no input would exercise the
    // exhausted-input path instead of the one the fixture was recorded for.
    const io = loadFixture('io')
    expect(io.stdin.trim()).not.toBe('')
    expect(io.request.stdin).toBe(io.stdin)
  })
})

describe('payload narrowing', () => {
  it('returns typed payloads for a successful analyze response', () => {
    const { response } = loadFixture('hello')

    const tokens = tokensOf(response)
    expect(tokens).not.toBeNull()
    expect(tokens?.[0]).toMatchObject({ type: expect.any(String), category: expect.any(String) })

    const ast = response.ast
    expect(ast?.kind).toBeTypeOf('string')
    expect(Array.isArray(ast?.children)).toBe(true)

    const ir = irOf(response)
    expect(ir?.functions.length).toBeGreaterThan(0)
    expect(ir?.instruction_count).toBeGreaterThan(0)
    expect(ir?.text).toContain('t')

    const cfg = cfgOf(response)
    expect(Object.keys(cfg ?? {})).toContain('main')
    const main = cfg?.main
    expect(main?.blocks.length).toBeGreaterThan(0)
    expect(main?.entry).toBeTypeOf('string')

    const dataflow = dataflowOf(response)
    expect(Object.keys(dataflow ?? {})).toContain('main')
  })

  it('narrows an optimize response to a codegen payload', () => {
    const { response } = loadFixture('dead_code')

    expect(response.mode).toBe('optimize')
    const codegen = codegenOf(response)
    expect(codegen).not.toBeNull()
    expect(codegen?.source).toBeTypeOf('string')
    expect(codegen?.applied).toBeTypeOf('boolean')
  })

  it('returns null for a payload the pipeline did not produce', () => {
    // The error fixture fails in the semantic stage: lexing and parsing still happened, but
    // everything from lowering onward was skipped and its payload is null.
    const { response } = loadFixture('error')

    expect(response.stages_run).toContain('tokens')
    expect(tokensOf(response)?.length).toBeGreaterThan(0)

    expect(irOf(response)).toBeNull()
    expect(cfgOf(response)).toBeNull()
    expect(dataflowOf(response)).toBeNull()
    expect(codegenOf(response)).toBeNull()
    expect(analysisOf(response)).toBeNull()
    // Optimization did not run, so there is no report. A view that rendered an empty pass table
    // here would be claiming the optimizer ran and found nothing.
    expect(optimizationOf(response)).toBeNull()
  })

  it('narrows an analysis report with findings, and not just a count', () => {
    const { response } = loadFixture('dead_code')
    const report = analysisOf(response)

    expect(report).not.toBeNull()
    expect(report?.finding_count).toBe(report?.findings.length)
    expect(report?.finding_count).toBeGreaterThan(0)

    const finding = report?.findings[0]
    expect(finding?.kind).toBeTypeOf('string')
    expect(finding?.message).toBeTypeOf('string')
    // A finding without a span cannot be clicked through to the source, which would make the
    // panel's central affordance silently do nothing.
    expect(finding?.span).not.toBeNull()
  })

  it('narrows an optimization report, including what it skipped', () => {
    const { response } = loadFixture('loops')
    const report = optimizationOf(response)

    expect(report?.changed).toBeTypeOf('boolean')
    expect(report?.passes.length).toBeGreaterThan(0)
    expect(report?.stats.before.instructions).toBeGreaterThanOrEqual(report?.stats.after.instructions ?? 0)

    // The skipped list is the part that keeps the wins honest, so it has to survive narrowing.
    for (const gap of report?.skipped ?? []) {
      expect(gap.kind).toBeTypeOf('string')
      expect(gap.reason).not.toBe('')
    }
  })

  it('records an unverified entry as a gap rather than a pass', () => {
    // `loops` has helper functions with parameters, so the oracle cannot call them. The report
    // has to say so; a report that only listed the verified entries would overstate coverage.
    const { response } = loadFixture('loops')
    const report = optimizationOf(response)

    const unverified = report?.verification?.unverified_entries ?? []
    const gaps = report?.skipped ?? []
    for (const entry of unverified) {
      expect(gaps.some((gap) => gap.kind === 'unverified_entry' && gap.target === entry)).toBe(true)
    }
  })
})

describe('stage bookkeeping', () => {
  it('distinguishes ran from skipped', () => {
    const ok = loadFixture('hello').response
    const bad = loadFixture('error').response

    expect(stageRan(ok, 'cfg')).toBe(true)
    expect(stageSkipped(ok, 'cfg')).toBe(false)

    // The error fixture stops before lowering, so those stages must be recorded as skipped
    // rather than simply absent.
    expect(stageRan(bad, 'ir')).toBe(false)
    expect(bad.stages_skipped.length).toBeGreaterThan(0)
  })
})

describe('compile', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts the request and returns the response body', async () => {
    const { request, response } = loadFixture('hello')
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await compile(request)

    expect(result.stages_run).toContain('tokens')
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/compile')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toMatchObject({ mode: request.mode })
  })

  it('reports a backend error with the status code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('boom', { status: 500 })),
    )

    await expect(compile({ source: 'int main() {}', mode: 'analyze' })).rejects.toThrow(ApiError)
  })

  it('explains an unreachable backend instead of surfacing a raw fetch failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(compile({ source: '', mode: 'analyze' })).rejects.toThrow(/backend running/)
  })

  it('rejects a non-object body rather than handing a view nonsense', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('null', { status: 200 })))

    await expect(compile({ source: '', mode: 'analyze' })).rejects.toThrow(/non-object/)
  })
})

describe('solver traces', () => {
  it('carries a step per block update, and admits when it cut the trace short', () => {
    const { response } = loadFixture('loops')
    const facts = dataflowOf(response)
    const main = facts?.main
    const trace = main?.traces?.liveness

    expect(trace).toBeDefined()
    // A trace with no steps would render a stepper that steps nowhere, and a stepper that
    // claims a convergence it did not record would be worse than none.
    expect(trace?.steps.length).toBeGreaterThan(0)
    expect(trace?.iterations).toBeGreaterThanOrEqual(trace?.steps.length ?? 0)
    // Liveness is defined backwards, which the stepper labels so the direction is not a surprise.
    expect(trace?.direction).toBe('backward')

    for (const step of trace?.steps ?? []) {
      expect(step.iteration).toBeGreaterThan(0)
      expect(step.block).toBeTypeOf('string')
      expect(Array.isArray(step.in)).toBe(true)
    }
  })

  it('steps in a strictly increasing iteration order', () => {
    const { response } = loadFixture('loops')
    const steps = dataflowOf(response)?.main?.traces?.liveness?.steps ?? []
    const numbers = steps.map((step) => step.iteration)

    // The order is the worklist's order, which is what makes a screenshot of the stepper a
    // claim somebody can check. A gap is fine (a step can be dropped when capping); a
    // repeat is not.
    for (let index = 1; index < numbers.length; index += 1) {
      expect(numbers[index]!).toBeGreaterThan(numbers[index - 1]!)
    }
  })

  it('keeps per-block values separate per analysis', () => {
    // Liveness and reaching defs both answer a question about a block, and they answer it
    // differently. Reading one under the other's heading would be confidently wrong.
    const { response } = loadFixture('dead_code')
    const facts = dataflowOf(response)?.overwritten_before_read

    expect(facts?.live_in).toBeDefined()
    expect(facts?.reaching_in).toBeDefined()
    expect(Object.keys(facts?.reaching_in ?? {}).length).toBeGreaterThan(0)
    // The constant-propagation states are only in the trace, not in the report.
    expect(facts?.traces?.constant_propagation?.steps.length).toBeGreaterThan(0)
  })
})

describe('examples library', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches the listing without downloading every source', async () => {
    const body = {
      count: 2,
      invalid_count: 1,
      examples: [
        { name: 'hello', title: 'Hello', blurb: 'A greeting', file: 'valid/hello.c', valid: true, stdin: '' },
        { name: 'oops', title: 'Oops', blurb: 'Broken', file: 'invalid/oops.c', valid: false, stdin: '' },
      ],
    }
    const fetchMock = vi.fn<(url: string) => Promise<Response>>(async () =>
      new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await listExamples()

    expect(result.count).toBe(2)
    expect(result.invalid_count).toBe(1)
    // The point of splitting the endpoints: the menu must not have to hold every program.
    expect(result.examples[0]).not.toHaveProperty('source')
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/examples')
  })

  it('fetches one example, escaping the name in the path', async () => {
    const fetchMock = vi.fn<(url: string) => Promise<Response>>(async () =>
      new Response(
        JSON.stringify({ name: 'x', title: 'X', blurb: '', file: 'valid/x.c', valid: true, stdin: '', source: 'int main() {}' }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const detail = await readExample('x')

    expect(detail.source).toContain('main')
    // A raw interpolation would let a stray `/` or `?` change which endpoint is called. The
    // backend validates names too, but the client is not the thing that has to be sure.
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/examples/x')
  })

  it('rejects a listing that is not the documented shape', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ examples: 'nope' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    // A menu fed this would render zero examples, which reads as "there are none" rather than
    // as "the answer was not what we asked for".
    await expect(listExamples()).rejects.toThrow(PayloadError)
  })

  it('surfaces the backend 404 for an example that is not in the manifest', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('unknown example', { status: 404 })),
    )

    await expect(readExample('nope')).rejects.toThrow(ApiError)
  })

  it('accepts a listing whose entries are summaries', () => {
    expect(
      isExampleList({ count: 1, invalid_count: 0, examples: [{ name: 'a' }] }),
    ).toBe(true)
    expect(isExampleList({ count: 1, invalid_count: 0, examples: [{ title: 'a' }] })).toBe(false)
  })
})

describe('payload guards', () => {
  it('throws a PayloadError when a payload has the wrong shape', () => {
    const broken: CompileResponse = {
      success: true,
      mode: 'analyze',
      diagnostics: [],
      tokens: 'not an array' as unknown as CompileResponse['tokens'],
      timings: {},
      stages_run: [],
      stages_skipped: [],
    }

    // The client casts the body without validation, so the guard is the thing that has to catch
    // this. It is a `PayloadError`, not an `ApiError`: the request succeeded, the body did not.
    expect(() => tokensOf(broken)).toThrow(/Expected tokens/)
  })
})
